"""Разбор завала: перевести старые сделки воронки в другую стадию.

    python -m scripts.cleanup_deals                 показать, что будет сделано
    python -m scripts.cleanup_deals --apply         сделать

Массовая правка живой CRM обратно не откатывается — поэтому по умолчанию
скрипт ничего не меняет, а печатает список. Переносить он начинает только
после явного --apply.

По умолчанию берёт стадию, на которую бот кладёт новых кандидатов, оставляет
всё созданное сегодня и переводит остальное в провальную стадию (отказ).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.integrations.bitrix import BitrixClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def full_stage(code: str, category_id: int) -> str:
    """Полный код стадии: из EXECUTING и воронки 1 получается C1:EXECUTING."""
    if ":" in code or not category_id:
        return code
    return f"C{category_id}:{code}"


async def find_deals(
    client: BitrixClient, category_id: int, stage_id: str, before: date
) -> list[dict[str, Any]]:
    """Сделки указанной стадии, созданные раньше указанной даты."""
    collected: list[dict[str, Any]] = []
    start = 0

    while True:
        result = await client._call(  # noqa: SLF001 — служебная выборка
            "crm.deal.list",
            {
                "filter": {
                    "CATEGORY_ID": category_id,
                    "STAGE_ID": stage_id,
                    "<DATE_CREATE": before.isoformat(),
                },
                "select": ["ID", "TITLE", "DATE_CREATE", "STAGE_ID"],
                "order": {"ID": "ASC"},
                "start": start,
            },
        )
        if not result:
            break
        collected.extend(result)
        if len(result) < 50:  # Битрикс отдаёт страницами по 50
            break
        start += 50

    return collected


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Разбор старых сделок воронки")
    parser.add_argument(
        "--stage",
        default=settings.bitrix_stage_new,
        help="код стадии, откуда убирать (по умолчанию — куда бот кладёт новых)",
    )
    parser.add_argument(
        "--to",
        default=settings.bitrix_stage_rejected,
        help="код стадии, куда переводить (по умолчанию — провальная)",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=0,
        help="сколько последних дней не трогать (0 — оставить только сегодняшние)",
    )
    parser.add_argument("--apply", action="store_true", help="выполнить перенос")
    args = parser.parse_args()

    if not settings.bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — скрипту не с чем работать.")
        return

    client = BitrixClient()
    category = settings.bitrix_deal_category_id
    before = date.today() - timedelta(days=args.keep_days)

    stage_from = full_stage(args.stage, category)
    stage_to = full_stage(args.to, category)

    try:
        deals = await find_deals(client, category, stage_from, before)

        print(f"\nВоронка {category}, стадия {stage_from}")
        print(f"Созданные раньше {before.strftime('%d.%m.%Y')} — их и переносим.\n")

        if not deals:
            print("Таких сделок нет, переносить нечего.")
            return

        for deal in deals:
            created = str(deal.get("DATE_CREATE", ""))[:10]
            print(f"  #{deal['ID']:<8} {created}  {deal.get('TITLE')}")

        print(f"\nВсего: {len(deals)}")

        if not args.apply:
            # Показать и остановиться: список выше — единственный способ
            # заметить, что под условие попало не то
            print(
                f"\nЭто предварительный показ, ничего не изменено.\n"
                f"Проверьте список. Если он верный, повторите команду с --apply:\n"
                f"    python -m scripts.cleanup_deals --apply\n"
                f"Сделки уйдут в стадию {stage_to}."
            )
            return

        print(f"\nПереношу в {stage_to}…")
        moved = 0
        for deal in deals:
            try:
                await client.update_deal(int(deal["ID"]), {"STAGE_ID": stage_to})
                moved += 1
            except Exception as error:  # noqa: BLE001
                print(f"  #{deal['ID']} не удалось: {error}")

        print(f"\nГотово. Перенесено: {moved} из {len(deals)}.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
