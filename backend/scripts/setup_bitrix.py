"""Подготовка Битрикса: воронки, стадии и пользовательские поля сделок.

    python -m scripts.setup_bitrix --show-stages    воронки и коды их стадий
    python -m scripts.setup_bitrix --create-fields  создать недостающие поля

Подбор ведётся Сделками в воронке HR, поэтому смотрим стадии сделок, а не лидов.
Скрипт ничего не удаляет и не перезаписывает: существующие поля пропускаются.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import get_settings
from app.integrations.bitrix import BitrixClient

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Код поля -> (подпись в карточке, тип в Битриксе)
FIELDS: dict[str, tuple[str, str]] = {
    "UF_CRM_RESUME_URL": ("Ссылка на резюме", "string"),
    "UF_CRM_AGE": ("Возраст", "integer"),
    "UF_CRM_CITY": ("Город", "string"),
    "UF_CRM_EXPERIENCE": ("Опыт, лет", "double"),
    "UF_CRM_SALARY": ("Ожидаемая ЗП", "string"),
    "UF_CRM_REJECT_REASON": ("Причина отказа", "string"),
    "UF_CRM_VACANCY": ("Вакансия", "string"),
}

# Наш статус -> переменная в .env и как стадия называется в воронке HR
STAGE_HINTS = [
    ("BITRIX_STAGE_NEW", "Новое резюме"),
    ("BITRIX_STAGE_CALLED", "Первичный созвон"),
    ("BITRIX_STAGE_TEST_TASK", "Тестовое задание"),
    ("BITRIX_STAGE_INTERVIEW", "Собеседование"),
    ("BITRIX_STAGE_INTERN", "Стажировка"),
    ("BITRIX_STAGE_RESERVE", "Кадровый резерв"),
    ("BITRIX_STAGE_HIRED", "успешная стадия — вышел на работу"),
    ("BITRIX_STAGE_REJECTED", "провальная стадия — не подходит"),
]


async def show_stages(client: BitrixClient) -> None:
    categories = await client.list_deal_categories()

    print("\nВоронки сделок в вашем портале:\n")
    print(f"  {'ID':<6} Название")
    print(f"  {'0':<6} Общая (основная воронка)")
    for category in categories:
        print(f"  {category.get('ID'):<6} {category.get('NAME')}")

    print(
        "\nНайдите воронку HR и впишите её ID в BITRIX_DEAL_CATEGORY_ID.\n"
        "Ниже — стадии каждой воронки.\n"
    )

    for category_id, name in [("0", "Общая")] + [
        (str(c.get("ID")), c.get("NAME")) for c in categories
    ]:
        stages = await client.list_deal_stages(int(category_id))
        if not stages:
            continue
        print(f"--- Воронка {category_id}: {name} ---")
        for stage in stages:
            # Полный код вида C7:EXECUTING; в .env нужна часть после двоеточия
            full = str(stage.get("STATUS_ID"))
            short = full.split(":", 1)[1] if ":" in full else full
            print(f"  {short:<22} {stage.get('NAME'):<28} (полный код {full})")
        print()

    print("Сопоставьте стадии воронки HR с переменными .env:\n")
    for variable, meaning in STAGE_HINTS:
        print(f"  {variable:<24} — {meaning}")
    print()


async def create_fields(client: BitrixClient) -> None:
    existing = {field.get("FIELD_NAME") for field in await client.list_userfields()}

    for code, (label, field_type) in FIELDS.items():
        if code in existing:
            print(f"  уже есть: {code}")
            continue
        try:
            await client.create_userfield(code, label, field_type)
            print(f"  создано:  {code} — {label}")
        except Exception as error:
            print(f"  ошибка:   {code} — {error}")

    print("\nГотово. Коды полей уже прописаны в .env.example как значения по умолчанию.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовка Битрикса")
    parser.add_argument("--show-stages", action="store_true")
    parser.add_argument("--create-fields", action="store_true")
    args = parser.parse_args()

    if not (args.show_stages or args.create_fields):
        parser.print_help()
        return

    settings = get_settings()
    if not settings.bitrix_configured:
        print(
            "BITRIX_WEBHOOK_URL не заполнен в .env — скрипту не с чем работать.\n"
            "Битрикс подключать не обязательно: бот работает и без него, "
            "см. docs/bitrix_setup.md."
        )
        return

    print(f"Портал: {settings.bitrix_webhook_url.split('/rest/')[0]}")

    # Скрипт создаёт поля по-настоящему, даже если в .env стоит DRY_RUN
    client = BitrixClient(dry_run=False)
    try:
        if args.show_stages:
            await show_stages(client)
        if args.create_fields:
            await create_fields(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
