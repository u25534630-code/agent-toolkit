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


async def restore_vacancies() -> None:
    """Вернуть названия вакансий тем, у кого их нет."""
    from app.integrations.hh import HHClient

    with session_scope() as session:
        rows = [
            (c.id, c.hh_negotiation_id)
            for c in session.scalars(
                select(Candidate).where(
                    Candidate.hh_negotiation_id.is_not(None),
                    # Пустая строка — это тоже «вакансии нет»
                    (Candidate.vacancy_title.is_(None))
                    | (Candidate.vacancy_title == ""),
                )
            )
        ]

    if not rows:
        print("Вакансии есть у всех кандидатов, спрашивать нечего.\n")
        return

    print(f"Спрошу у hh.ru вакансии для {len(rows)} кандидатов…")
    client = HHClient()
    restored = 0
    try:
        for candidate_id, negotiation_id in rows:
            try:
                data = await client._get(f"/negotiations/{negotiation_id}")  # noqa: SLF001
            except Exception as error:  # noqa: BLE001
                print(f"  отклик {negotiation_id}: {str(error)[:120]}")
                continue
            name = ((data or {}).get("vacancy") or {}).get("name")
            if not name:
                continue
            with session_scope() as session:
                candidate = session.get(Candidate, candidate_id)
                if candidate:
                    candidate.vacancy_title = name
                    restored += 1
    finally:
        await client.close()

    print(f"Вакансий восстановлено: {restored}\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Дозаполнение карточек Битрикса")
    parser.add_argument("--apply", action="store_true", help="выполнить запись")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — скрипту не с чем работать.")
        return

    # Вакансию у старых кандидатов не сохранили — спрашиваем её у hh.ru
    # по номеру отклика: он в базе есть
    if settings.hh_configured:
        await restore_vacancies()

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
        todo: list[tuple[int, str | None, str, dict]] = []

        for deal_id, name, summary, candidate in plan:
            deal = await client._call("crm.deal.get", {"id": deal_id}) or {}  # noqa: SLF001
            existing = deal.get("COMMENTS") or ""

            # Комментарий и поля карточки заполняются независимо: раньше коды
            # полей были неизвестны, и у старых карточек комментарий есть,
            # а поля пустые. Один признак «уже готово» на двоих не годится.
            wanted = await client.fields_for(candidate)
            missing = {
                code: value
                for code, value in wanted.items()
                if code.startswith("UF_")
                and value
                and deal.get(code) in (None, "", [], False)
            }
            needs_comment = MARK not in existing

            if not needs_comment and not missing:
                print(f"  #{deal_id:<8} {name} — уже заполнено, пропускаю")
                continue

            what = []
            if needs_comment:
                what.append("комментарий")
            if missing:
                what.append(f"поля ({len(missing)})")
            print(f"  #{deal_id:<8} {name} — допишу: {', '.join(what)}")
            todo.append((deal_id, existing if needs_comment else None, summary, missing))

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
        for deal_id, existing, summary, missing in todo:
            fields = dict(missing)
            if existing is not None:
                # Существующий текст сохраняем: там могут быть заметки коллег
                fields["COMMENTS"] = (
                    f"{existing}\n\n{summary}" if existing.strip() else summary
                )
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
