#!/usr/bin/env python3
"""
Обложки-мокапы лид-магнита для email и мессенджеров через openai/gpt-image-2.

Это НЕ обложка внутри PDF — её рисует HTML-генератор из логотипа и заголовка.
Здесь делается отдельная горизонтальная картинка 3:2, которую вставляют в
письмо или пост, чтобы у гайда было узнаваемое превью.

Заголовок и подзаголовок берутся из content.md (# H1 / ## H2). Палитра —
из brand.yaml. Визуальный мотив задаётся флагом --motif: без него модель
рисует нейтральную абстракцию, а не выдуманные детали не по теме гайда.

Usage:
    uv run scripts/cover.py <project>/content.md \\
        --motif "окно браузера с зелёной галочкой и облако с искрой"
    uv run scripts/cover.py <project>/content.md --approach all

Токен: REPLICATE_API_TOKEN в окружении или в .env в корне рабочего репозитория.
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
import sys
from pathlib import Path

import brand as brand_config
import replicate_image as replicate

# Канонический вариант. Остальные доступны через --approach.
DEFAULT_APPROACHES = ["book"]

# Нейтральный мотив: применяется, когда автор не задал --motif.
# Намеренно абстрактный — лучше пустая геометрия, чем выдуманные детали,
# не имеющие отношения к теме гайда.
DEFAULT_MOTIF = (
    "простые абстрактные геометрические фигуры: круги, скруглённые "
    "прямоугольники и тонкие линии"
)


def palette_line(brand: dict) -> str:
    palette = brand["palette"]
    return (
        f"Фирменная палитра: основной {palette['primary'].upper()}, "
        f"голубой акцент {palette['blue'].upper()}, "
        f"светлый фон {palette['gray'].upper()}, "
        f"чистый белый {palette['white'].upper()}."
    )


def prompts(title: str, subtitle: str, brand: dict, motif: str) -> dict[str, str]:
    """Четыре композиции обложки. Мотив и палитра подставляются снаружи."""
    colors = palette_line(brand)
    palette = brand["palette"]
    primary = palette["primary"].upper()
    blue = palette["blue"].upper()
    gray = palette["gray"].upper()
    white = palette["white"].upper()
    tail = "Все надписи строго кириллицей, никакой латиницы. Без людей, без фото."

    return {
        # A. Плоский баннер: упор на текст.
        "banner": (
            f"Горизонтальный рекламный баннер в стиле flat design на сплошном "
            f"фоне {primary}. Слева крупный белый заголовок жирным гротеском "
            f'в три строки: "{title}". Над заголовком небольшая белая скруглённая '
            f'плашка-пилюля с текстом цвета {primary}: "БЕСПЛАТНЫЙ PDF-ГАЙД". '
            f'Под заголовком тонкая строка-подзаголовок: "{subtitle}". '
            f"Справа крупная плоская иллюстрация в тонах {blue}: {motif}. "
            f"Мягкие большие круги {blue} по углам как лёгкий фоновый декор. "
            f"{colors} {tail}"
        ),
        # B. 3D-мокап книги — канонический вариант.
        "book": (
            f"Реалистичный 3D-мокап обложки книги-гайда, стоящей под лёгким углом, "
            f"с мягкой тенью, на чистом светлом фоне {gray}. "
            f"Обложка книги цвета {primary}, на ней крупный белый заголовок жирным "
            f'гротеском: "{title}", и маленькая плашка {blue} с белым текстом '
            f'"PDF-ГАЙД". Вокруг книги несколько небольших парящих карточек в белых '
            f"тонах {white} со скруглёнными углами и мягкой тенью, на карточках "
            f"простые плоские иконки: {motif}. "
            f"Не рисуй надписи со словом «курс», не рисуй кнопки play и видео-превью. "
            f"{colors} Современный, чистый, премиальный вид. {tail}"
        ),
        # C. Мокап устройств.
        "device": (
            f"Чистый горизонтальный мокап: ноутбук и смартфон, на экранах которых "
            f"показан интерфейс в цвете {primary}. На экранах ТОЛЬКО крупные простые "
            f"элементы: одна кнопка, пара широких пустых блоков-плейсхолдеров и "
            f"простая плоская иконка. Без мелкого текста и абзацев на экранах. "
            f"Устройства на чистом светлом фоне {gray} с мягкой тенью. "
            f'Слева крупный заголовок цвета {primary} жирным гротеском: "{title}". '
            f"{colors} Flat-иллюстрация, аккуратно, минимализм. "
            f"Этот единственный заголовок — строго кириллицей. Без людей, без фото."
        ),
        # D. Концептуальная плоская иллюстрация.
        "concept": (
            f"Горизонтальная плоская иллюстрация flat design на белом фоне {white}. "
            f"По центру композиция: {motif}. "
            f"Сверху по центру крупный заголовок цвета {primary} жирным гротеском "
            f'кириллицей: "{title}". {colors} Минимализм, скруглённые углы, мягкие '
            f"тени. {tail} Без 3D-реализма."
        ),
    }


def parse_title_subtitle(content_md: Path) -> tuple[str, str]:
    """Взять # H1 и первый ## H2 из content.md."""
    title, subtitle = "", ""
    for line in content_md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
        elif title and not subtitle and stripped.startswith("## "):
            subtitle = stripped[3:].strip()
            break
    # Длинный заголовок обрезаем до первой запятой: на обложке он должен
    # читаться с расстояния, а не заполнять её целиком.
    short = title.split(",")[0].strip() if len(title) > 40 else title
    return short or title, subtitle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Обложки-мокапы лид-магнита через gpt-image-2"
    )
    parser.add_argument(
        "content", nargs="?", help="Путь к content.md (из него берутся # H1 и ## H2)"
    )
    parser.add_argument(
        "--approach",
        default="default",
        help="default(book)|all|book|concept|device|banner (список через запятую)",
    )
    parser.add_argument("--title", help="Перебить заголовок с обложки")
    parser.add_argument("--subtitle", help="Перебить подзаголовок")
    parser.add_argument(
        "--motif",
        default=DEFAULT_MOTIF,
        help=(
            "Что нарисовать на иллюстрации — по теме гайда, короткой фразой. "
            "Без него будет нейтральная абстракция."
        ),
    )
    parser.add_argument("-o", "--out-dir", help="Куда сложить PNG")
    replicate.add_common_arguments(parser)
    brand_config.add_brand_argument(parser)
    args = parser.parse_args()

    title, subtitle, project_dir = "", "", None
    if args.content:
        content_md = Path(args.content).resolve()
        if not content_md.exists():
            sys.exit(f"❌ Нет файла: {content_md}")
        project_dir = content_md.parent
        title, subtitle = parse_title_subtitle(content_md)
    title = args.title or title
    subtitle = args.subtitle or subtitle
    if not title:
        sys.exit("❌ Нет заголовка: укажи content.md с # H1 или флаг --title")

    brand = brand_config.load_brand(args.brand, project_dir)
    source = brand.get("_source")
    print(f"Бренд: {brand['name']} ({source if source else 'встроенные дефолты'})")

    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    elif project_dir:
        out_dir = project_dir / "output" / "cover"
    else:
        sys.exit("❌ Укажи content.md или -o <папка>")

    all_prompts = prompts(title, subtitle, brand, args.motif)
    if args.approach == "default":
        chosen = list(DEFAULT_APPROACHES)
    elif args.approach == "all":
        chosen = list(all_prompts)
    else:
        chosen = [
            name.strip()
            for name in args.approach.split(",")
            if name.strip() in all_prompts
        ]
    if not chosen:
        sys.exit(f"❌ Нет таких подходов. Доступно: {', '.join(all_prompts)}")

    if args.motif == DEFAULT_MOTIF:
        print(
            "⚠️  --motif не задан: иллюстрация будет абстрактной. "
            "Опиши мотив по теме гайда, чтобы обложка отражала содержание."
        )

    jobs = [
        replicate.ImageJob(
            name=name,
            prompt=all_prompts[name],
            dest=out_dir / f"cover-{name}.png",
            quality=args.quality,
            aspect=args.aspect,
        )
        for name in chosen
    ]

    token = replicate.load_token(project_dir or Path.cwd())
    print(
        f"\nГенерация {len(jobs)} обложек ({replicate.MODEL}, "
        f"quality={args.quality}, aspect={args.aspect}): {', '.join(chosen)}\n"
    )
    results = replicate.generate_many(token, jobs, max_workers=len(jobs))

    ok = sum(1 for result in results if result.ok)
    print(f"\nГотово: {ok}/{len(jobs)} в {out_dir}")
    print("\nДля отчёта:")
    for result in results:
        print(f"  {result.report_line()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
