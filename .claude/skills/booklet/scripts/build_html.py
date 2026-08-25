#!/usr/bin/env python3
"""
Собрать брендированный HTML-превью лид-магнита из Markdown.

Фирменный стиль (название, сайт, логотип, палитра, колонтитулы) берётся
из brand.yaml — порядок поиска описан в scripts/brand.py.

Usage:
    uv run scripts/build_html.py lead-magnet-projects/[slug]/content.md
    uv run scripts/build_html.py content.md -o output/guide.html
    uv run scripts/build_html.py content.md --brand presets/univerus/brand.yaml
"""

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "markdown",
#     "beautifulsoup4",
#     "pyyaml",
# ]
# ///


import brand as brand_config

SCRIPT_PATH = Path(__file__).resolve()
SKILL_DIR = brand_config.SKILL_DIR
SKILL_ASSETS_DIR = SKILL_DIR / "assets"
FONTS_DIR = SKILL_ASSETS_DIR / "fonts"

# Активная бренд-конфигурация. Подменяется в configure_brand() до того,
# как отработает любая функция рендера.
BRAND = dict(brand_config.DEFAULT_BRAND)


def configure_brand(explicit=None, project_dir=None) -> dict:
    """Загрузить brand.yaml и сделать его активным для всего модуля."""
    global BRAND
    BRAND = brand_config.load_brand(explicit, project_dir)
    return BRAND


def palette(key: str) -> str:
    return BRAND["palette"][key]


def font_dir(key: str) -> Path:
    return FONTS_DIR / BRAND["fonts"][key]


def running_header(separator: str = "/") -> str:
    """Строка колонтитула: «Бренд / гайд». Без tagline — только название."""
    parts = [BRAND["name"], BRAND.get("tagline") or ""]
    return f" {separator} ".join(part for part in parts if part)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "section"


def read_project(input_file: Path) -> dict:
    project_path = input_file.parent / "project.json"
    if not project_path.exists():
        return {}
    with project_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_logo_data_uri() -> str:
    logo_path = brand_config.resolve_asset(BRAND, BRAND["logo"]["path"])
    if logo_path is None:
        return ""
    svg_text = logo_path.read_text(encoding="utf-8")
    viewbox = BRAND["logo"].get("viewbox") or ""
    if viewbox:
        svg_text = re.sub(
            r'viewBox="[^"]+"',
            f'viewBox="{viewbox}"',
            svg_text,
            count=1,
        )
    encoded = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def font_face(family: str, path: Path, weight: int) -> str:
    if not path.exists():
        return ""
    font_format = "truetype" if path.suffix.lower() == ".ttf" else "woff2"
    mime = "font/ttf" if path.suffix.lower() == ".ttf" else "font/woff2"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "@font-face {"
        f"font-family: '{family}'; "
        f"src: url('data:{mime};base64,{encoded}') format('{font_format}'); "
        f"font-weight: {weight}; "
        "font-style: normal; "
        "font-display: swap;"
        "}"
    )


def font_face_range(family: str, path: Path, weight_range: str) -> str:
    if not path.exists():
        return ""
    font_format = "truetype" if path.suffix.lower() == ".ttf" else "woff2"
    mime = "font/ttf" if path.suffix.lower() == ".ttf" else "font/woff2"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "@font-face {"
        f"font-family: '{family}'; "
        f"src: url('data:{mime};base64,{encoded}') format('{font_format}'); "
        f"font-weight: {weight_range}; "
        "font-style: normal; "
        "font-display: swap;"
        "}"
    )


def get_local_font_css() -> str:
    """Вшить шрифты в HTML как data URI, чтобы PDF собирался без внешних файлов."""
    sans = font_dir("sans_dir")
    mono = font_dir("mono_dir")
    heading = font_dir("heading_dir")

    faces = [
        font_face("Geist", sans / "Geist-Regular.woff2", 400),
        font_face("Geist", sans / "Geist-Medium.woff2", 500),
        font_face("Geist", sans / "Geist-SemiBold.woff2", 600),
        font_face("Geist", sans / "Geist-Bold.woff2", 700),
        font_face("Geist Mono", mono / "GeistMono-Regular.woff2", 400),
        font_face("Geist Mono", mono / "GeistMono-Medium.woff2", 500),
        font_face("Geist Mono", mono / "GeistMono-SemiBold.woff2", 600),
        font_face_range(
            "Montserrat",
            heading / "Montserrat-VariableFont_wght.ttf",
            "400 900",
        ),
        font_face("Montserrat", heading / "Montserrat-Medium.ttf", 500),
        font_face("Montserrat", heading / "Montserrat-SemiBold.ttf", 600),
        font_face("Montserrat", heading / "Montserrat-Bold.ttf", 700),
    ]
    css = "\n".join(face for face in faces if face)
    if not css:
        print(
            f"⚠️  Шрифты не найдены в {FONTS_DIR}. "
            "PDF соберётся системными шрифтами и будет выглядеть иначе.",
            file=sys.stderr,
        )
    return css


def first_text(soup, selector: str, fallback: str = "") -> str:
    node = soup.find(selector)
    if not node:
        return fallback
    return node.get_text(" ", strip=True)


def get_subtitle(soup, project: dict) -> str:
    h2 = soup.find("h2")
    if h2:
        return h2.get_text(" ", strip=True)
    return project.get("topic", "")


def build_toc(soup) -> list[dict]:
    seen = {}
    toc = []
    for heading in soup.find_all(["h2"]):
        text = heading.get_text(" ", strip=True)
        if not text:
            continue
        base_id = slugify(text)
        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        heading_id = base_id if count == 0 else f"{base_id}-{count + 1}"
        heading["id"] = heading_id
        toc.append(
            {
                "level": int(heading.name[1]),
                "id": heading_id,
                "title": text,
            }
        )
    return toc


def remove_cover_headings(soup):
    first_h1 = soup.find("h1")
    if first_h1:
        first_h1.decompose()
    first_h2 = soup.find("h2")
    if first_h2:
        first_h2.decompose()


def normalize_extra_h1_sections(soup):
    """Treat any remaining H1 headings as document sections."""
    for heading in soup.find_all("h1"):
        heading.name = "h2"


def add_section_classes(soup):
    section_index = 0
    for heading in soup.find_all("h2"):
        section_index += 1
        heading["class"] = [*heading.get("class", []), "section-heading"]
        platform_prefix = (BRAND["cta"].get("platform_heading_prefix") or "").lower()
        is_platform_heading = bool(platform_prefix) and heading.get_text(
            " ", strip=True
        ).lower().startswith(platform_prefix)
        if is_platform_heading:
            heading["class"] = [*heading.get("class", []), "platform-heading"]
        eyebrow = soup.new_tag("div", attrs={"class": "section-eyebrow"})
        if is_platform_heading:
            eyebrow["class"] = [*eyebrow.get("class", []), "platform-eyebrow"]
        eyebrow.string = f"Раздел / {section_index:02d}"
        heading.insert_before(eyebrow)

    for heading in soup.find_all("h3"):
        heading["class"] = [*heading.get("class", []), "card-heading"]
        link_prefix = (BRAND["cta"].get("link_heading_prefix") or "").lower()
        if link_prefix and heading.get_text(" ", strip=True).lower().startswith(link_prefix):
            heading["class"] = [*heading.get("class", []), "cta-link-heading"]

    for blockquote in soup.find_all("blockquote"):
        blockquote["class"] = [*blockquote.get("class", []), "callout"]

    for pre in soup.find_all("pre"):
        pre["class"] = [*pre.get("class", []), "prompt-box"]

    for table in soup.find_all("table"):
        table["class"] = [*table.get("class", []), "data-table"]

    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if text.startswith("Сложность клонирования") or text.startswith("Сложность:"):
            paragraph["class"] = [*paragraph.get("class", []), "difficulty-line"]
        if text.startswith("© "):
            paragraph["class"] = [*paragraph.get("class", []), "cta-copyright"]


def image_to_data_uri(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def resolve_image_path(src: str, input_file: Path, output_file: Path) -> Path | None:
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https", "data", "mailto"}:
        return None

    raw_path = unquote(parsed.path)
    candidate = Path(raw_path)
    candidates = [candidate] if candidate.is_absolute() else [
        input_file.parent / candidate,
        output_file.parent / candidate,
        Path.cwd() / candidate,
    ]

    for image_path in candidates:
        if image_path.exists() and image_path.is_file():
            return image_path

    return None


def only_child_image(paragraph) -> bool:
    meaningful_children = [
        child
        for child in paragraph.children
        if getattr(child, "name", None) or str(child).strip()
    ]
    return len(meaningful_children) == 1 and getattr(meaningful_children[0], "name", None) == "img"


def embed_and_format_images(soup, input_file: Path, output_file: Path):
    for image in soup.find_all("img"):
        src = image.get("src", "")
        local_path = resolve_image_path(src, input_file, output_file)
        source_label = ""
        if local_path:
            image["src"] = image_to_data_uri(local_path)
            source_label = local_path.stem.replace("-", " ")

        image["class"] = [*image.get("class", []), "media-image"]
        alt_text = image.get("alt", "").strip()

        parent = image.parent
        if parent and parent.name == "p" and only_child_image(parent):
            title_text = image.get("title", "").strip()
            # Рамка браузера уместна только для скриншотов сайтов: у них есть
            # адрес в title или они лежат в assets/screenshots/. Схемы и
            # инфографика получают чистую фигуру без фейковой адресной строки.
            is_screenshot = bool(title_text) or "screenshots/" in src.replace("\\", "/")

            figure_class = "media-figure" if is_screenshot else "media-figure is-plain"
            figure = soup.new_tag("figure", attrs={"class": figure_class})

            image.extract()

            if is_screenshot:
                chrome = soup.new_tag("div", attrs={"class": "media-browser-bar"})
                dots = soup.new_tag("span", attrs={"class": "media-browser-dots"})
                for dot_class in ["is-red", "is-yellow", "is-green"]:
                    dots.append(soup.new_tag("i", attrs={"class": dot_class}))
                address = soup.new_tag("span", attrs={"class": "media-browser-address"})
                address.string = title_text or source_label or "Скриншот"
                spacer = soup.new_tag("span", attrs={"class": "media-browser-spacer"})
                chrome.append(dots)
                chrome.append(address)
                chrome.append(spacer)
                figure.append(chrome)

            figure.append(image)

            if alt_text and not title_text:
                caption = soup.new_tag("figcaption", attrs={"class": "media-caption"})
                caption.string = alt_text
                figure.append(caption)

            parent.replace_with(figure)


def get_css(variant: str = "standard") -> str:
    readable = variant in {"standard", "readable"}
    # Шрифты всегда локальные: PDF должен собираться без интернета.
    font_css = get_local_font_css()
    display_weight = 800
    body_size = "16.5px" if readable else "16px"
    body_line_height = "1.64" if readable else "1.62"
    cover_h1_size = "54px"
    cover_h1_line_height = ".98"
    cover_h1_width = "148mm"
    cover_h1_margin = "72mm"
    cover_subtitle_line_height = "1.44" if readable else "1.36"
    content_h2_size = "40px"
    content_h2_line_height = "1.05"
    content_h3_family = "var(--font-display)"
    content_h3_size = "25px"
    content_h3_line_height = "1.18"
    content_h3_weight = 750
    prompt_size = "14px" if readable else "13px"
    table_size = "14px" if readable else "13px"
    readable_measure_css = """
        .content > p,
        .content > ul,
        .content > ol,
        .content > blockquote,
        .content > pre {
            max-width: 148mm;
        }

        .content > h2,
        .content > h3,
        .content > h4,
        .section-eyebrow {
            max-width: 154mm;
        }
    """ if readable else ""

    return f"""
        {font_css}

        :root {{
            --primary: {palette("primary")};
            --brand-blue: {palette("blue")};
            --bg2: {palette("gray")};
            --text: {palette("text")};
            --surface: {palette("white")};
            --alert: {palette("red")};
            --muted: #5f6b7a;
            --line: #dbe6f0;
            --font-sans: 'Geist', Arial, sans-serif;
            --font-display: 'Montserrat', 'Geist', Arial, sans-serif;
            --font-mono: 'Geist Mono', 'SF Mono', Consolas, monospace;
        }}

        * {{ box-sizing: border-box; }}

        html {{
            background: #d7e3ee;
        }}

        body {{
            margin: 0;
            color: var(--text);
            font-family: var(--font-sans);
            font-size: {body_size};
            line-height: {body_line_height};
            background: var(--bg2);
        }}

        a {{
            color: var(--primary);
            font-weight: 600;
            text-decoration: none;
            border-bottom: 1px solid rgba(55, 133, 226, .32);
        }}

        .page {{
            width: 210mm;
            min-height: 297mm;
            margin: 24px auto;
            padding: 22mm 24mm;
            background: var(--surface);
            box-shadow: 0 20px 60px rgba(35, 62, 92, .18);
            position: relative;
            overflow: hidden;
        }}

        .cover {{
            page: cover;
            color: #fff;
            background: var(--primary);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}

        .cover::before {{
            content: '';
            position: absolute;
            width: 165mm;
            height: 165mm;
            right: -42mm;
            top: -34mm;
            border-radius: 50%;
            background: rgba(255,255,255,.16);
        }}

        .cover::after {{
            content: '';
            position: absolute;
            width: 120mm;
            height: 120mm;
            left: -52mm;
            bottom: -42mm;
            border-radius: 50%;
            background: rgba(75,167,249,.38);
        }}

        .cover-inner {{
            position: relative;
            z-index: 1;
        }}

        .brand-row,
        .running-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            font-family: var(--font-mono);
            font-size: 12px;
            font-weight: 600;
            letter-spacing: .12em;
            text-transform: uppercase;
        }}

        .brand-row {{
            justify-content: flex-start;
        }}

        .brand-logo-card {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 320px;
            height: 72px;
            padding: 8px 12px;
            border-radius: 14px;
            background: #fff;
            overflow: hidden;
        }}

        .brand-logo-card img {{
            display: block;
            width: 91%;
            height: 91%;
            object-fit: contain;
            transform: translate(-15px, -16px);
        }}

        .cover h1 {{
            max-width: {cover_h1_width};
            margin: {cover_h1_margin} 0 0;
            font-family: var(--font-display);
            font-size: {cover_h1_size};
            line-height: {cover_h1_line_height};
            font-weight: {display_weight};
        }}

        .cover-subtitle {{
            max-width: 138mm;
            margin-top: 14mm;
            font-size: 21px;
            line-height: {cover_subtitle_line_height};
            font-weight: 500;
            opacity: .96;
        }}

        .toc h1,
        .content h1 {{
            margin: 0 0 20mm;
            font-family: var(--font-display);
            font-size: 48px;
            line-height: 1.03;
            font-weight: {display_weight};
        }}

        .toc-list {{
            margin-top: 18mm;
            border-top: 1px solid var(--line);
        }}

        .toc-item {{
            display: grid;
            grid-template-columns: 34mm 1fr 18mm;
            gap: 12px;
            padding: 9mm 0;
            border-bottom: 1px solid var(--line);
            color: inherit;
            break-inside: avoid;
            page-break-inside: avoid;
        }}

        .toc-item a {{
            color: inherit;
            border: 0;
        }}

        .toc-index {{
            font-family: var(--font-display);
            font-size: 32px;
            line-height: 1;
            font-weight: {display_weight};
            color: var(--primary);
        }}

        .toc-title {{
            font-size: 20px;
            line-height: 1.25;
            font-weight: 700;
        }}

        .toc-page {{
            padding-top: 5px;
            font-family: var(--font-mono);
            font-size: 10px;
            letter-spacing: .12em;
            text-transform: uppercase;
            color: var(--muted);
            text-align: right;
            border: 0;
        }}

        .toc-page::before {{
            content: 'стр. ';
        }}

        .toc-page::after {{
            content: target-counter(attr(href), page);
        }}

        .content {{
            padding-top: 18mm;
        }}

        {readable_measure_css}

        .content p {{
            margin: 0 0 14px;
            widows: 3;
            orphans: 3;
        }}

        .content h2 {{
            margin: 5mm 0 9mm;
            padding-bottom: 8mm;
            border-bottom: 3px solid var(--text);
            font-family: var(--font-display);
            font-size: {content_h2_size};
            line-height: {content_h2_line_height};
            font-weight: {display_weight};
            break-after: avoid-page;
            page-break-after: avoid;
        }}

        .section-eyebrow {{
            margin-top: 18mm;
            font-family: var(--font-mono);
            font-size: 12px;
            font-weight: 600;
            letter-spacing: .18em;
            text-transform: uppercase;
            color: var(--primary);
            break-before: page;
            page-break-before: always;
            break-after: avoid-page;
            page-break-after: avoid;
        }}

        /* Первый section-eyebrow (после обложки/оглавления) не должен ломать страницу */
        .toc + * .section-eyebrow:first-of-type,
        .section-eyebrow:first-of-type {{
            break-before: auto;
            page-break-before: auto;
        }}

        .content h3 {{
            margin: 12mm 0 5mm;
            font-family: {content_h3_family};
            font-size: {content_h3_size};
            line-height: {content_h3_line_height};
            font-weight: {content_h3_weight};
            break-after: avoid-page;
            page-break-after: avoid;
        }}

        .content h4 {{
            margin: 8mm 0 3mm;
            font-size: 18px;
            line-height: 1.28;
            font-weight: 700;
            break-after: avoid-page;
            page-break-after: avoid;
        }}

        .difficulty-line {{
            margin: 4mm 0 0;
            font-size: 15px;
            line-height: 1.32;
            break-before: avoid-page;
            page-break-before: avoid;
            break-after: avoid-page;
            page-break-after: avoid;
        }}

        .difficulty-line + .section-eyebrow {{
            margin-top: 14mm;
            padding-top: 12mm;
            border-top: 1px solid var(--line);
        }}

        .platform-eyebrow {{
            break-before: page;
            page-break-before: always;
        }}

        .platform-heading {{
            margin-top: 0;
        }}

        .cta-link-heading {{
            margin: 6mm 0 4mm;
            font-size: 30px;
            line-height: 1;
            break-before: avoid-page;
            page-break-before: avoid;
        }}

        .cta-copyright {{
            margin-top: 2mm;
            font-size: 15px;
            line-height: 1.35;
            break-before: avoid-page;
            page-break-before: avoid;
        }}

        .content ul,
        .content ol {{
            margin: 0 0 16px 0;
            padding-left: 22px;
        }}

        .content li {{
            margin: 0 0 8px;
            widows: 3;
            orphans: 3;
        }}

        .callout {{
            margin: 9mm 0;
            padding: 7mm 8mm;
            border-left: 4px solid var(--primary);
            border-radius: 14px;
            background: var(--bg2);
            break-inside: avoid-page;
            page-break-inside: avoid;
        }}

        .callout p:last-child {{
            margin-bottom: 0;
        }}

        .media-figure {{
            margin: 8mm 0 9mm;
            padding: 0;
            border: 1px solid #c9d7e3;
            border-radius: 8px;
            background: #fff;
            overflow: hidden;
            break-inside: avoid-page;
            page-break-inside: avoid;
        }}

        /* Схемы и инфографика: без рамки браузера, картинка во всю ширину. */
        .media-figure.is-plain {{
            border: 0;
            background: transparent;
        }}

        .media-figure.is-plain .media-image {{
            border: 1px solid #dbe6f0;
            border-radius: 8px;
            background: transparent;
        }}

        .media-figure.is-plain .media-caption {{
            padding: 6px 2px 0;
            text-align: center;
        }}

        .media-browser-bar {{
            display: grid;
            grid-template-columns: 58px minmax(0, 1fr) 58px;
            align-items: center;
            height: 34px;
            padding: 0 9px;
            border-bottom: 1px solid #c8d6e2;
            background: linear-gradient(#e7eef5, #d6e2ec);
            color: var(--muted);
            font-family: var(--font-mono);
        }}

        .media-browser-dots {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex: 0 0 auto;
        }}

        .media-browser-dots i {{
            display: block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }}

        .media-browser-dots .is-red {{ background: #f4675d; }}
        .media-browser-dots .is-yellow {{ background: #f2c94c; }}
        .media-browser-dots .is-green {{ background: #45c078; }}

        .media-browser-address {{
            min-width: 0;
            padding: 4px 12px;
            border: 1px solid #c2cfdb;
            border-radius: 999px;
            background: rgba(255,255,255,.88);
            color: #425466;
            font-size: 10px;
            line-height: 1.2;
            letter-spacing: .02em;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .media-browser-spacer {{
            display: block;
        }}

        .media-image {{
            display: block;
            width: 100%;
            max-height: 104mm;
            object-fit: contain;
            background: var(--bg2);
        }}

        .media-caption {{
            padding: 8px 10px 10px;
            color: var(--muted);
            font-size: 12px;
            line-height: 1.4;
        }}

        .prompt-box {{
            margin: 8mm 0;
            padding: 7mm 8mm;
            border-radius: 12px;
            background: #111827;
            color: #f8fafc;
            font-family: var(--font-mono);
            font-size: {prompt_size};
            line-height: 1.58;
            white-space: pre-wrap;
            break-inside: avoid-page;
            page-break-inside: avoid;
        }}

        code {{
            padding: 2px 6px;
            border-radius: 6px;
            background: #edf4fb;
            font-family: var(--font-mono);
            font-size: .92em;
        }}

        .prompt-box code {{
            padding: 0;
            background: transparent;
            color: inherit;
        }}

        .data-table {{
            width: 100%;
            margin: 8mm 0;
            border-collapse: collapse;
            font-size: {table_size};
            break-inside: avoid-page;
            page-break-inside: avoid;
        }}

        .data-table tr {{
            break-inside: avoid-page;
            page-break-inside: avoid;
        }}

        .data-table th {{
            padding: 10px 12px;
            background: var(--primary);
            color: #fff;
            text-align: left;
            font-weight: 700;
        }}

        .data-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--line);
            vertical-align: top;
        }}

        .data-table tr:nth-child(even) td {{
            background: var(--bg2);
        }}

        hr {{
            margin: 12mm 0;
            border: 0;
            border-top: 1px solid var(--line);
        }}

        .footer-note {{
            position: absolute;
            left: 24mm;
            right: 24mm;
            bottom: 14mm;
            display: flex;
            justify-content: space-between;
            border-top: 1px solid rgba(0,0,0,.16);
            padding-top: 8px;
            font-family: var(--font-mono);
            font-size: 10px;
            letter-spacing: .12em;
            text-transform: uppercase;
            color: var(--muted);
        }}

        @page {{
            size: A4;
            margin: 18mm 20mm 20mm;
            @top-left {{
                content: '{running_header()}';
                font-family: 'Geist Mono', monospace;
                font-size: 9px;
                letter-spacing: .12em;
                text-transform: uppercase;
                color: {palette("muted")};
            }}
            @top-right {{
                content: '';
            }}
            @bottom-left {{
                content: '{BRAND["site"]}';
                font-family: 'Geist Mono', monospace;
                font-size: 9px;
                color: {palette("muted")};
            }}
            @bottom-right {{
                content: 'стр. ' counter(page) ' из ' counter(pages);
                font-family: 'Geist Mono', monospace;
                font-size: 9px;
                color: {palette("muted")};
            }}
        }}

        @page cover {{
            size: A4;
            margin: 0;
        }}

        @page :first {{
            size: A4;
            margin: 0;
            @top-left {{ content: ''; }}
            @top-right {{ content: ''; }}
            @bottom-left {{ content: ''; }}
            @bottom-right {{ content: ''; }}
        }}

        @media print {{
            html, body {{
                background: #fff;
            }}

            .page {{
                width: auto;
                min-height: auto;
                margin: 0;
                padding: 0;
                box-shadow: none;
                break-after: page;
            }}

            .cover,
            .toc {{
                min-height: 259mm;
            }}

            .cover {{
                min-height: 297mm;
                padding: 22mm 24mm;
            }}

            .toc {{
                padding: 0;
            }}

            .content {{
                padding: 0;
            }}

            .footer-note {{
                display: none;
            }}
        }}
    """


def render_cover(title: str, subtitle: str, project: dict) -> str:
    related = project.get("related_product") or {}
    product = related.get("name") or "Лид-магнит"
    logo_data_uri = get_logo_data_uri()
    logo_html = (
        f'<img src="{logo_data_uri}" alt="{BRAND["name"]}">'
        if logo_data_uri
        else html.escape(BRAND["name"])
    )
    return f"""
    <section class="page cover">
        <div class="cover-inner">
            <div class="brand-row">
                <div class="brand-logo-card">{logo_html}</div>
            </div>
            <h1>{html.escape(title)}</h1>
            <div class="cover-subtitle">{html.escape(subtitle)}</div>
        </div>
        <div class="footer-note">
            <span>{html.escape(product)}</span>
            <span>{html.escape(BRAND["site"])}</span>
        </div>
    </section>
    """


def render_toc(toc: list[dict]) -> str:
    main_items = [item for item in toc if item["level"] == 2]
    rows = "\n".join(
        f"""
        <div class="toc-item">
            <div class="toc-index">{index:02d}</div>
            <a class="toc-title" href="#{html.escape(item["id"])}">{html.escape(item["title"])}</a>
            <a class="toc-page" href="#{html.escape(item["id"])}"></a>
        </div>
        """
        for index, item in enumerate(main_items, 1)
    )
    return f"""
    <section class="page toc">
        <h1>Содержание</h1>
        <div class="toc-list">
            {rows}
        </div>
        <div class="footer-note">
            <span>{html.escape(running_header(separator="·"))}</span>
            <span>содержание</span>
        </div>
    </section>
    """


def apply_nbsp_to_numbers(text: str) -> str:
    """Заменяет обычные пробелы на неразрывные внутри числовых групп
    и перед короткими «прилипающими» словами (₽, $, €, мес, лет, чел, тыс).

    Решает проблему, когда «10 000 ₽» переносится так, что нули или знак валюты
    уходят на следующую строку при вёрстке узкого текста.
    """
    import re

    # 1) Пробел между группами цифр («10 000», «200 000 000») → NBSP
    text = re.sub(r"(?<=\d) (?=\d{3}\b)", " ", text)

    # 2) Пробел перед валютой и короткими единицами после числа → NBSP
    text = re.sub(
        r"(\d)\s+(₽|\$|€|мес|год|года|лет|чел|тыс|млн|млрд)\b",
        lambda m: f"{m.group(1)} {m.group(2)}",
        text,
    )

    return text


def markdown_to_editorial_html(
    input_path: str,
    output_path: str | None = None,
    variant: str = "standard",
    cover_title: str | None = None,
    cover_subtitle: str | None = None,
    brand_path: str | None = None,
) -> str:
    import markdown
    from bs4 import BeautifulSoup

    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    brand = configure_brand(brand_path, input_file.resolve().parent)
    source = brand.get("_source")
    print(f"Бренд: {brand['name']} ({source if source else 'встроенные дефолты'})")

    project = read_project(input_file)

    if output_path is None:
        output_dir = input_file.parent / "output"
        output_dir.mkdir(exist_ok=True)
        slug = project.get("slug") or input_file.stem
        output_file = output_dir / f"{slug}.html"
    else:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

    markdown_text = input_file.read_text(encoding="utf-8")
    markdown_text = apply_nbsp_to_numbers(markdown_text)
    fragment = markdown.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )

    soup = BeautifulSoup(fragment, "html.parser")
    title = first_text(soup, "h1", project.get("name", "Лид-магнит"))
    subtitle = get_subtitle(soup, project)
    display_title = cover_title or title
    display_subtitle = cover_subtitle or subtitle
    remove_cover_headings(soup)
    normalize_extra_h1_sections(soup)
    toc = build_toc(soup)
    add_section_classes(soup)
    embed_and_format_images(soup, input_file, output_file)

    full_html = f"""<!doctype html>
<html lang="{html.escape(project.get("language", "ru"))}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(title)} · {BRAND["name"]}</title>
    <style>{get_css(variant)}</style>
</head>
<body>
    {render_cover(display_title, display_subtitle, project)}
    {render_toc(toc)}
    <main class="page content">
        {str(soup)}
    </main>
</body>
</html>
"""

    output_file.write_text(full_html, encoding="utf-8")
    size_kb = output_file.stat().st_size / 1024
    print(f"HTML created: {output_file}")
    print(f"Size: {size_kb:.1f} KB")
    return str(output_file)


def main():
    parser = argparse.ArgumentParser(
        description="Собрать брендированный HTML-превью лид-магнита из Markdown"
    )
    brand_config.add_brand_argument(parser)
    parser.add_argument("input", help="Input Markdown file path")
    parser.add_argument(
        "-o",
        "--output",
        help="Output HTML file path (default: output/[slug].html)",
    )
    parser.add_argument(
        "--variant",
        choices=["standard", "editorial", "readable", "univerus"],
        default="standard",
        help="Вариант вёрстки (univerus — устаревший алиас standard)",
    )
    parser.add_argument(
        "--cover-title",
        help="Override title on the cover page only",
    )
    parser.add_argument(
        "--cover-subtitle",
        help="Override subtitle on the cover page only",
    )
    args = parser.parse_args()
    variant = "standard" if args.variant == "univerus" else args.variant
    markdown_to_editorial_html(
        args.input,
        args.output,
        variant=variant,
        cover_title=args.cover_title,
        cover_subtitle=args.cover_subtitle,
        brand_path=args.brand,
    )


if __name__ == "__main__":
    main()
