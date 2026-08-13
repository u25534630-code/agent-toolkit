"""Опознать поля карточки, вписав в них метки.

    python -m scripts.probe_fields --deal 8113           расставить метки
    python -m scripts.probe_fields --deal 8113 --clear   убрать метки

Названия полей этот портал по API не отдаёт — только коды вида
UF_CRM_69DDE3A9BB6DC, которых шестьдесят. Опознать нужное можно так:
вписать в каждое пустое поле метку «МЕТКА-1», «МЕТКА-2» и посмотреть
в карточке, какая метка оказалась в нужной строке.

Заполненные поля не трогаются — метки идут только в пустые.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

from app.config import get_settings
from app.integrations.bitrix import BitrixClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")

PREFIX = "МЕТКА-"


async def collect(client: BitrixClient, deal_id: int, types: set[str]) -> list[str]:
    """Пустые пользовательские поля сделки подходящего типа."""
    schema = await client._call("crm.deal.fields", {}) or {}  # noqa: SLF001
    deal = await client._call("crm.deal.get", {"id": deal_id}) or {}  # noqa: SLF001

    codes = []
    for code, info in schema.items():
        if not code.startswith("UF_"):
            continue
        if types and str(info.get("type") or "") not in types:
            continue
        value = deal.get(code)
        # Чужие значения не затираем: метки только там, где пусто
        if value in (None, "", [], False):
            codes.append(code)
    return sorted(codes)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Опознание полей карточки")
    parser.add_argument("--deal", type=int, required=True, help="номер сделки")
    parser.add_argument(
        "--type",
        default="url,string",
        help="типы полей через запятую (по умолчанию url,string)",
    )
    parser.add_argument("--clear", action="store_true", help="убрать метки")
    args = parser.parse_args()

    if not get_settings().bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — скрипту не с чем работать.")
        return

    types = {part.strip() for part in args.type.split(",") if part.strip()}
    client = BitrixClient()
    try:
        if args.clear:
            deal = await client._call("crm.deal.get", {"id": args.deal}) or {}  # noqa: SLF001
            marked = {
                code: ""
                for code, value in deal.items()
                if code.startswith("UF_") and str(value or "").startswith(PREFIX)
            }
            if not marked:
                print("Меток в этой сделке нет.")
                return
            await client.update_deal(args.deal, marked)
            print(f"Убрал меток: {len(marked)}")
            return

        codes = await collect(client, args.deal, types)
        if not codes:
            print("Пустых полей подходящего типа нет.")
            return

        fields: dict[str, Any] = {}
        print(f"\nВпишу в сделку #{args.deal} метки:\n")
        for number, code in enumerate(codes, 1):
            label = f"{PREFIX}{number}"
            fields[code] = label
            print(f"  {label:<12} -> {code}")

        await client.update_deal(args.deal, fields)
        print(
            f"\nГотово, меток: {len(fields)}.\n\n"
            f"Откройте сделку в Битриксе и посмотрите, какая метка стоит\n"
            f"в нужном поле. Скажите её номер — по нему станет ясен код.\n\n"
            f"Убрать метки: python -m scripts.probe_fields --deal {args.deal} --clear"
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
