"""Подготовка Битрикса: пользовательские поля и просмотр кодов стадий.

    python -m scripts.setup_bitrix --show-statuses   посмотреть коды стадий
    python -m scripts.setup_bitrix --create-fields   создать недостающие поля

Скрипт ничего не удаляет и не перезаписывает: если поле уже есть, оно
пропускается.
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


async def show_statuses(client: BitrixClient) -> None:
    statuses = await client.list_lead_statuses()
    print("\nСтадии лида в вашем портале — впишите нужные в .env:\n")
    for status in statuses:
        print(f"  {status.get('STATUS_ID'):<24} {status.get('NAME')}")
    print(
        "\nСопоставьте так:\n"
        "  BITRIX_STATUS_NEW        — новый отклик\n"
        "  BITRIX_STATUS_IN_PROCESS — в работе / дозвонились\n"
        "  BITRIX_STATUS_INTERVIEW  — собеседование назначено\n"
        "  BITRIX_STATUS_INTERN     — стажировка\n"
        "  BITRIX_STATUS_HIRED      — вышел на работу\n"
        "  BITRIX_STATUS_REJECTED   — не подходит\n"
    )


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
    parser.add_argument("--show-statuses", action="store_true")
    parser.add_argument("--create-fields", action="store_true")
    args = parser.parse_args()

    if not (args.show_statuses or args.create_fields):
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
        if args.show_statuses:
            await show_statuses(client)
        if args.create_fields:
            await create_fields(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
