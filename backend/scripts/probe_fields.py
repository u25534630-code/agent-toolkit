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
import sys
from typing import Any

from pathlib import Path

from app.config import get_settings
from app.integrations.bitrix import BitrixClient
from scripts.setup_bitrix import write_env

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


# Настройка -> как поле называется в карточке
WANTED: list[tuple[str, str]] = [
    ("BITRIX_UF_RESUME_URL", "Ссылка на резюме"),
    ("BITRIX_UF_VACANCY", "кандидат на должность"),
    ("BITRIX_UF_CITY", "Город"),
    ("BITRIX_UF_BRANCH", "Филиал"),
]


async def map_by_markers(client: BitrixClient, deal_id: int) -> None:
    """Спросить номера меток и записать соответствующие коды в .env.

    Человек видит в карточке «МЕТКА-56» рядом с нужным полем — назвать номер
    он может, а сопоставить его с UF_CRM_69DDE3A9BB6DC нет. Сопоставляем сами.
    """
    deal = await client._call("crm.deal.get", {"id": deal_id}) or {}  # noqa: SLF001
    by_marker = {
        str(value).strip(): code
        for code, value in deal.items()
        if code.startswith("UF_") and str(value or "").startswith(PREFIX)
    }
    if not by_marker:
        print(
            f"В сделке #{deal_id} меток нет. Сначала расставьте их: "
            "запустите probe.bat и назовите номер сделки."
        )
        return

    print(f"\nМеток в сделке #{deal_id}: {len(by_marker)}")
    print("Назовите номер метки, которая стоит в нужном поле.")
    print("Если такого поля у вас нет — просто Enter.\n")

    values: dict[str, str] = {}
    for variable, human in WANTED:
        answer = input(f"  {human}: МЕТКА-").strip()
        if not answer:
            continue
        code = by_marker.get(f"{PREFIX}{answer}")
        if not code:
            print(f"    Метки {PREFIX}{answer} в этой сделке нет, пропускаю.")
            continue
        values[variable] = code
        print(f"    {variable} = {code}")

    if not values:
        print("\nНичего не выбрано.")
        return

    if not Path(".env").exists():
        print("\nФайл .env не найден — запускать нужно из папки backend.")
        return

    write_env(values)
    print(f"\nЗаписал в настройки: {len(values)}.")
    print("Метки уберём: запустите probe.bat ещё раз и выберите очистку.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Опознание полей карточки")
    parser.add_argument("--deal", type=int, help="номер сделки")
    parser.add_argument(
        "--type",
        default="url,string",
        help="типы полей через запятую (по умолчанию url,string)",
    )
    parser.add_argument("--clear", action="store_true", help="убрать метки")
    parser.add_argument(
        "--map", action="store_true", help="назвать номера меток и записать коды"
    )
    args = parser.parse_args()

    if not get_settings().bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — скрипту не с чем работать.")
        return

    # Запущенный двойным щелчком .bat аргументов не получает — спрашиваем всё
    if not (args.clear or args.map) and sys.stdin.isatty():
        print(
            "\nЧто сделать?\n"
            "  1 — расставить метки по пустым полям\n"
            "  2 — назвать номера меток и записать коды в настройки\n"
            "  3 — убрать метки"
        )
        choice = input("Ваш выбор (1/2/3): ").strip()
        args.map = choice == "2"
        args.clear = choice == "3"

    deal_id = args.deal
    while deal_id is None:
        print(
            "\nНомер сделки виден в её адресе в Битриксе:"
            "\n  .../crm/deal/details/8113/  — здесь это 8113"
        )
        try:
            answer = input("Номер сделки: ").strip()
        except EOFError:
            print("Ввод недоступен.")
            return
        if answer.isdigit():
            deal_id = int(answer)
        else:
            print("Нужно число.")
    args.deal = deal_id

    types = {part.strip() for part in args.type.split(",") if part.strip()}
    client = BitrixClient()
    try:
        if args.map:
            await map_by_markers(client, args.deal)
            return

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
