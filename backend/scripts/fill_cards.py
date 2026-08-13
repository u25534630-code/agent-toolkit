"""Дозаполнить карточки, заведённые раньше: телефон, город, ссылка на резюме.

    python -m scripts.fill_cards            показать, что будет дописано
    python -m scripts.fill_cards --apply    дописать

Кандидаты, заведённые до того, как бот научился класть данные в комментарий
сделки, остались с пустыми карточками — ссылку на резюме приходится искать
на hh.ru. Скрипт проходит по базе бота и дописывает то, что знает.

Чужой текст в комментарии не затирается: если там уже что-то написано,
наш блок добавляется снизу.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Candidate
from app.db.session import session_scope
from app.integrations.bitrix import BitrixClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")

MARK = "Телефон:"  # по этой строке узнаём свой блок в комментарии


async def main() -> None:
    parser = argparse.ArgumentParser(description="Дозаполнение карточек Битрикса")
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — скрипту не с чем работать.")
        return

    with session_scope() as session:
        candidates = list(
            session.scalars(
                select(Candidate)
                .where(Candidate.bitrix_deal_id.is_not(None))
                .order_by(Candidate.id)
            )
        )
        # Данные достаём заранее: после закрытия сессии объекты отвяжутся
        plan = [
            (c.bitrix_deal_id, c.full_name, BitrixClient._summary(c), c)  # noqa: SLF001
            for c in candidates
        ]

    plan = [row for row in plan if row[2]]
    if not plan:
        print("Нечего дописывать: карточек с данными в базе нет.")
        return

    client = BitrixClient()
    try:
        print(f"\nКарточек в базе: {len(plan)}\n")
        todo: list[tuple[int, str, str, object]] = []

        for deal_id, name, summary, candidate in plan:
            deal = await client._call("crm.deal.get", {"id": deal_id})  # noqa: SLF001
            existing = (deal or {}).get("COMMENTS") or ""
            if MARK in existing:
                print(f"  #{deal_id:<8} {name} — уже заполнено, пропускаю")
                continue
            has_resume = "Резюме:" in summary
            print(f"  #{deal_id:<8} {name} — допишу{' со ссылкой' if has_resume else ''}")
            todo.append((deal_id, existing, summary, candidate))

        if not todo:
            print("\nВсе карточки уже заполнены.")
            return

        print(f"\nК дозаписи: {len(todo)}")
        if not args.apply:
            print("\nПока ничего не изменено.")
            if not sys.stdin.isatty():
                print("Повторите с --apply, чтобы дописать.")
                return
            answer = input("Дописать? (д/н): ").strip().lower()
            if answer not in ("д", "да", "y", "yes"):
                print("Ничего не трогаю.")
                return

        done = 0
        for deal_id, existing, summary, candidate in todo:
            # Существующий текст сохраняем: там могут быть заметки коллег
            comments = f"{existing}\n\n{summary}" if existing.strip() else summary
            # Заодно заполняем поля карточки — те, что в портале есть
            fields = await client.fields_for(candidate)
            fields["COMMENTS"] = comments
            try:
                await client.update_deal(deal_id, fields)
                done += 1
            except Exception as error:  # noqa: BLE001
                print(f"  #{deal_id} не удалось: {error}")

        print(f"\nГотово. Дописано: {done} из {len(todo)}.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
