#!/usr/bin/env python3
"""
Генерация схем и инфографики внутрь лид-магнита через openai/gpt-image-2.

Закрывает то, чего скилл раньше не умел: объясняющие картинки в теле гайда —
блок-схемы, ленты шагов, сравнения, концептуальные плакаты, эмуляции экранов
с точным русским текстом.

Маршрут из трёх шагов:

    scan      вытащить плейсхолдеры из content.md в visuals.json
    generate  сгенерировать картинки по промптам из visuals.json
    apply     заменить плейсхолдеры в content.md на markdown-картинки

Плейсхолдер в content.md:

    <!-- ДИАГРАММА: kak-rabotaet.png — путь от голосовой команды к приложению -->

Промпты пишет агент — руками, по правилам из references/visuals-image2.md.
Скрипт сам добавляет к каждому промпту фирменную палитру из brand.yaml и
общие ограничения стиля, чтобы не повторять их в каждом промпте.

Usage:
    uv run scripts/visuals.py scan     <project>/content.md
    uv run scripts/visuals.py generate <project>/content.md
    uv run scripts/visuals.py apply    <project>/content.md
"""

from __future__ import annotations

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "pyyaml",
# ]
# ///

import argparse
import json
import re
import sys
from pathlib import Path

import brand as brand_config
import replicate_image as replicate

# <!-- ДИАГРАММА: file.png — описание -->  /  <!-- ILLUSTRATION: file.png — описание -->
PLACEHOLDER_RE = re.compile(
    r"<!--\s*(?:ДИАГРАММА|ИЛЛЮСТРАЦИЯ|ILLUSTRATION|DIAGRAM)\s*:\s*"
    r"(?P<file>[\w\-.]+\.png)\s*(?:[—–-]\s*(?P<description>[^>]*?))?\s*-->",
    re.IGNORECASE,
)

VISUALS_FILE = "visuals.json"
ASSETS_SUBDIR = "assets/illustrations"

# Хвост, который добавляется к каждому промпту. Держит все картинки гайда
# в одном стиле и снимает с автора промпта необходимость помнить палитру.
STYLE_SUFFIX = (
    "Стиль: flat design, минимализм, мягкие тени, скруглённые углы, "
    "никакого 3D, никаких градиентов, читается на экране телефона. "
    "Все надписи строго на русском кириллицей, короткие. "
    "Не добавляй декоративные элементы, людей, фоновые узоры, "
    "подписи автора и водяные знаки."
)


def palette_block(brand: dict) -> str:
    """Строка палитры для промпта — из brand.yaml, а не зашитая в код."""
    palette = brand["palette"]
    return (
        f"Цвета: основной {palette['primary'].upper()}, "
        f"светлый акцент {palette['blue'].upper()}, "
        f"фон {palette['gray'].upper()}, "
        f"поверхности {palette['white'].upper()}, "
        f"предупреждения {palette['red'].upper()}, "
        f"основной текст {palette['text'].upper()}, "
        f"вторичный текст {palette['muted'].upper()}."
    )


def build_prompt(raw_prompt: str, brand: dict) -> str:
    """Собрать финальный промпт: замысел автора + палитра бренда + стиль."""
    return f"{raw_prompt.strip()}\n\n{palette_block(brand)} {STYLE_SUFFIX}"


def visuals_path(content_md: Path) -> Path:
    return content_md.parent / VISUALS_FILE


def load_visuals(content_md: Path) -> dict:
    path = visuals_path(content_md)
    if not path.exists():
        return {"visuals": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("visuals", [])
    return data


def save_visuals(content_md: Path, data: dict) -> Path:
    path = visuals_path(content_md)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def find_placeholders(content_md: Path) -> list[dict]:
    text = content_md.read_text(encoding="utf-8")
    found = []
    for match in PLACEHOLDER_RE.finditer(text):
        filename = match.group("file")
        found.append(
            {
                "name": Path(filename).stem,
                "file": filename,
                "description": (match.group("description") or "").strip(),
            }
        )
    return found


def cmd_scan(args) -> int:
    content_md = Path(args.content).resolve()
    placeholders = find_placeholders(content_md)
    if not placeholders:
        print(
            "Плейсхолдеров не найдено. Добавь в content.md строки вида\n"
            "  <!-- ДИАГРАММА: имя-файла.png — что показать на схеме -->"
        )
        return 0

    data = load_visuals(content_md)
    known = {item["file"]: item for item in data["visuals"]}
    added = 0

    for placeholder in placeholders:
        if placeholder["file"] in known:
            # Описание в content.md могло измениться — подтягиваем, промпт не трогаем.
            known[placeholder["file"]]["description"] = placeholder["description"]
            continue
        data["visuals"].append(
            {
                "name": placeholder["name"],
                "file": placeholder["file"],
                "description": placeholder["description"],
                "caption": placeholder["description"],
                "aspect": "3:2",
                "prompt": "",
            }
        )
        added += 1

    path = save_visuals(content_md, data)
    print(f"Плейсхолдеров в content.md: {len(placeholders)}, новых записей: {added}")
    print(f"Файл: {path}")
    empty = [item["file"] for item in data["visuals"] if not item.get("prompt")]
    if empty:
        print(
            "\nБез промпта (заполни по references/visuals-image2.md):\n  "
            + "\n  ".join(empty)
        )
    return 0


def cmd_generate(args) -> int:
    content_md = Path(args.content).resolve()
    project_dir = content_md.parent
    brand = brand_config.load_brand(args.brand, project_dir)
    source = brand.get("_source")
    print(f"Бренд: {brand['name']} ({source if source else 'встроенные дефолты'})")

    data = load_visuals(content_md)
    if not data["visuals"]:
        sys.exit(f"❌ Нет {VISUALS_FILE}. Сначала запусти команду scan.")

    out_dir = project_dir / ASSETS_SUBDIR
    selected = [
        item
        for item in data["visuals"]
        if not args.only or item["name"] in args.only or item["file"] in args.only
    ]
    if args.only and not selected:
        sys.exit(f"❌ Не найдено: {', '.join(args.only)}")

    jobs, skipped = [], []
    for item in selected:
        if not item.get("prompt"):
            skipped.append(f"{item['file']} — пустой prompt")
            continue
        dest = out_dir / item["file"]
        if dest.exists() and not args.force:
            skipped.append(f"{item['file']} — уже есть, перегенерация только с --force")
            continue
        jobs.append(
            replicate.ImageJob(
                name=item["name"],
                prompt=build_prompt(item["prompt"], brand),
                dest=dest,
                quality=args.quality,
                aspect=item.get("aspect") or args.aspect,
            )
        )

    for line in skipped:
        print(f"⏭  {line}")
    if not jobs:
        print("Генерировать нечего.")
        return 0

    token = replicate.load_token(project_dir)
    print(
        f"\nГенерация {len(jobs)} шт. ({replicate.MODEL}, quality={args.quality}):"
        f" {', '.join(job.name for job in jobs)}\n"
    )
    results = replicate.generate_many(token, jobs, max_workers=args.workers)

    ok = sum(1 for result in results if result.ok)
    print(f"\nГотово: {ok}/{len(jobs)} в {out_dir}")
    print("\nДля отчёта:")
    for result in results:
        print(f"  {result.report_line()}")
    if ok < len(jobs):
        print(
            "\nЧто делать с промахами — см. references/visuals-image2.md, "
            "раздел «Если картинка не получилась»."
        )
    return 0 if ok == len(jobs) else 1


def cmd_apply(args) -> int:
    content_md = Path(args.content).resolve()
    project_dir = content_md.parent
    data = load_visuals(content_md)
    captions = {item["file"]: (item.get("caption") or item.get("description") or "")
                for item in data["visuals"]}

    text = content_md.read_text(encoding="utf-8")
    replaced, missing = [], []

    def replace(match: re.Match) -> str:
        filename = match.group("file")
        image_path = project_dir / ASSETS_SUBDIR / filename
        if not image_path.exists():
            missing.append(filename)
            return match.group(0)
        caption = captions.get(filename) or (match.group("description") or "").strip()
        replaced.append(filename)
        return f"![{caption}]({ASSETS_SUBDIR}/{filename})"

    updated = PLACEHOLDER_RE.sub(replace, text)

    for filename in missing:
        print(f"⏭  {filename} — картинки нет, плейсхолдер оставлен")

    if not replaced:
        print("Нечего заменять.")
        return 0

    if args.dry_run:
        print(f"Заменил бы {len(replaced)}: {', '.join(replaced)}")
        return 0

    content_md.write_text(updated, encoding="utf-8")
    print(f"✅ Заменено плейсхолдеров: {len(replaced)}")
    for filename in replaced:
        print(f"  {filename}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Схемы и инфографика для лид-магнита через gpt-image-2"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Собрать плейсхолдеры в visuals.json")
    scan.add_argument("content", help="Путь к content.md")
    scan.set_defaults(func=cmd_scan)

    generate = subparsers.add_parser("generate", help="Сгенерировать картинки")
    generate.add_argument("content", help="Путь к content.md")
    generate.add_argument("--only", nargs="+", help="Только указанные имена или файлы")
    generate.add_argument("--force", action="store_true", help="Перезаписать существующие")
    generate.add_argument("--workers", type=int, default=4, help="Параллельных генераций")
    replicate.add_common_arguments(generate)
    brand_config.add_brand_argument(generate)
    generate.set_defaults(func=cmd_generate)

    apply_cmd = subparsers.add_parser("apply", help="Вставить картинки в content.md")
    apply_cmd.add_argument("content", help="Путь к content.md")
    apply_cmd.add_argument("--dry-run", action="store_true", help="Показать, не менять")
    apply_cmd.set_defaults(func=cmd_apply)

    args = parser.parse_args()

    content_path = Path(args.content)
    if not content_path.exists():
        sys.exit(f"❌ Нет файла: {content_path}")

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
