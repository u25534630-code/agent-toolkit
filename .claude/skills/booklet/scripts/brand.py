#!/usr/bin/env python3
"""
Загрузчик бренд-конфигурации для booklet.

Весь фирменный стиль PDF — название, сайт, логотип, палитра, колонтитулы —
живёт в brand.yaml, а не в коде генератора. Так один и тот же скилл собирает
гайды для любого бренда.

Порядок поиска конфигурации (первый найденный выигрывает):

1. путь, переданный явно (аргумент --brand);
2. brand.yaml в папке проекта или выше по дереву до папки с .git;
3. путь из переменной окружения BOOKLET_BRAND;
4. brand.yaml в корне скилла;
5. встроенные дефолты (нейтральный безымянный бренд).

Найденный файл накладывается поверх дефолтов рекурсивно: в brand.yaml
достаточно перечислить только то, что отличается.
"""

from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

# Нейтральный дефолт. Ничего фирменного: скилл, установленный «из коробки»,
# собирает корректный PDF без чужого бренда внутри.
DEFAULT_BRAND: dict = {
    # Название бренда. Идёт в <title>, в alt логотипа и как запасной вариант,
    # если логотип не найден.
    "name": "Lead Magnet",
    # Домен без протокола. Печатается в нижнем колонтитуле и на обложке.
    "site": "",
    # Слово после названия в верхнем колонтитуле: «Бренд / гайд».
    "tagline": "гайд",
    "logo": {
        # Путь к SVG. Относительный — считается сначала от папки brand.yaml,
        # затем от корня скилла. Пустая строка = печатать текстовое название.
        "path": "",
        # Кроп логотипа. Пустая строка — оставить viewBox файла как есть.
        "viewbox": "",
    },
    "palette": {
        "primary": "#3785e2",
        "blue": "#4ba7f9",
        "gray": "#f5f9fc",
        "text": "#000000",
        "white": "#ffffff",
        "red": "#f4675d",
        # Цвет колонтитулов. Живёт внутри @page, куда CSS-переменные
        # WeasyPrint не пробрасывает, поэтому подставляется в текст CSS.
        "muted": "#5f6b7a",
    },
    "cta": {
        # H2, который начинается с этой строки, выносится на отдельную
        # финальную CTA-страницу. Пусто = не выделять ни один раздел.
        "platform_heading_prefix": "",
        # H3, который начинается с этой строки, оформляется как крупная
        # ссылка-призыв. Обычно совпадает с site.
        "link_heading_prefix": "",
    },
    "fonts": {
        # Имена папок внутри assets/fonts/ скилла.
        "heading_dir": "montserrat",
        "sans_dir": "geist-sans",
        "mono_dir": "geist-mono",
    },
}

ENV_VAR = "BOOKLET_BRAND"
CONFIG_NAME = "brand.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Наложить override поверх base, не теряя незаданные ключи вложенных словарей."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def find_brand_file(explicit: str | Path | None = None,
                    project_dir: Path | None = None) -> Path | None:
    """Вернуть путь к brand.yaml по порядку приоритетов или None."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"❌ Бренд-конфигурация не найдена: {path}")
        return path

    if project_dir:
        # Идём от папки проекта вверх: один brand.yaml в корне рабочего
        # репозитория покрывает все проекты сразу. Останавливаемся на папке
        # с .git — выше искать нечего.
        current = Path(project_dir).resolve()
        for directory in [current, *current.parents]:
            candidate = directory / CONFIG_NAME
            if candidate.exists():
                return candidate
            if (directory / ".git").exists():
                break

    env_value = os.environ.get(ENV_VAR)
    if env_value:
        path = Path(env_value).expanduser()
        if not path.exists():
            raise SystemExit(f"❌ {ENV_VAR} указывает на несуществующий файл: {path}")
        return path.resolve()

    skill_config = SKILL_DIR / CONFIG_NAME
    if skill_config.exists():
        return skill_config.resolve()

    return None


def load_brand(explicit: str | Path | None = None,
               project_dir: Path | None = None) -> dict:
    """Собрать итоговую бренд-конфигурацию.

    В результат добавляются два служебных ключа:
      _source   — путь к использованному brand.yaml (или None для дефолтов);
      _base_dir — папка, от которой считаются относительные пути к ассетам.
    """
    brand_file = find_brand_file(explicit, project_dir)
    if brand_file is None:
        brand = dict(DEFAULT_BRAND)
        brand["_source"] = None
        brand["_base_dir"] = SKILL_DIR
        return brand

    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "❌ Не установлен pyyaml. Запускай скрипты через `uv run` — "
            "зависимости объявлены в заголовке каждого скрипта."
        )

    raw = yaml.safe_load(brand_file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"❌ {brand_file} должен быть YAML-словарём")

    brand = _deep_merge(DEFAULT_BRAND, raw)
    brand["_source"] = brand_file
    brand["_base_dir"] = brand_file.parent
    return brand


def resolve_asset(brand: dict, relative: str) -> Path | None:
    """Найти файл ассета: сначала рядом с brand.yaml, потом в корне скилла."""
    if not relative:
        return None
    path = Path(relative).expanduser()
    if path.is_absolute():
        return path if path.exists() else None

    for base in (brand.get("_base_dir", SKILL_DIR), SKILL_DIR):
        candidate = Path(base) / path
        if candidate.exists():
            return candidate
    return None


def add_brand_argument(parser) -> None:
    """Добавить единый флаг --brand во все скрипты скилла."""
    parser.add_argument(
        "--brand",
        help=(
            "Путь к brand.yaml. По умолчанию ищется в папке проекта, "
            f"затем в ${ENV_VAR}, затем в корне скилла."
        ),
    )
