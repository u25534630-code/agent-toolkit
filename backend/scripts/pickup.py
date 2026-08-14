"""Забрать отклики за прошедшие дни — те, что бот раньше пропустил.

    python -m scripts.pickup            спросит глубину и покажет, сколько их
    python -m scripts.pickup --days 7   то же за неделю
    python -m scripts.pickup --days 7 --apply

Бот берёт отклики, пришедшие после прошлого удачного опроса. Всё, что
накопилось до того, как его подключили (или пока он был выключен дольше
разрешённого), для него уже не новое.

Скрипт сдвигает эту отметку назад. Сам он в Битрикс ничего не пишет: заведёт
кандидатов обычный опрос — при запуске бота и дальше по расписанию.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import state
from app.db.session import init_db
from app.integrations.hh import HHClient

logging.basicConfig(level=logging.WARNING, format="%(message)s")


async def count_responses(days: int) -> tuple[int, int]:
    """Сколько откликов всего и сколько попадёт в окно."""
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    client = HHClient()
    total = fresh = 0
    try:
        vacancies = await client.list_active_vacancies()
        if not vacancies:
            print("У работодателя нет активных вакансий — откликам взяться неоткуда.")
            return 0, 0

        print(f"\nВакансий: {len(vacancies)}. Считаю отклики…\n")
        for vacancy in vacancies:
            vacancy_id = str(vacancy.get("id"))
            # Заголовки резюме не запрашиваем: здесь нужно только количество
            items = await client._all_responses(vacancy_id, 50)  # noqa: SLF001
            picked = client._recent_enough(items, since)  # noqa: SLF001
            total += len(items)
            fresh += len(picked)
            print(
                f"  {vacancy.get('name') or vacancy_id}: "
                f"всего {len(items)}, за {days} дн. — {len(picked)}"
            )
    finally:
        await client.close()

    tz = settings.tz
    print(
        f"\nИтого откликов: {total}. За последние {days} дн.: {fresh} "
        f"(с {since.astimezone(tz).strftime('%d.%m %H:%M')})"
    )
    return total, fresh


async def main() -> None:
    parser = argparse.ArgumentParser(description="Разбор накопившихся откликов")
    parser.add_argument("--days", type=int, help="за сколько дней забрать")
    parser.add_argument("--apply", action="store_true", help="сдвинуть отметку")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.hh_configured:
        print("hh.ru не настроен — сначала авторизация работодателя.")
        return

    init_db()

    days = args.days
    while days is None:
        print(
            "\nЗа сколько дней забрать отклики?"
            "\n  3  — выходные"
            "\n  7  — неделя"
            "\n  30 — месяц накопленного"
        )
        try:
            answer = input("Дней: ").strip()
        except EOFError:
            print("Ввод недоступен, укажите --days.")
            return
        if answer.isdigit() and int(answer) > 0:
            days = int(answer)
        else:
            print("Нужно число больше нуля.")

    _, fresh = await count_responses(days)
    if not fresh:
        print("\nЗабирать нечего.")
        return

    limit = settings.hh_max_new_per_poll
    if limit:
        polls = -(-fresh // limit)  # округление вверх
        print(
            f"За один опрос заводится не больше {limit} — "
            f"на всех уйдёт опросов: {polls}."
        )
    print("Уже заведённые кандидаты повторно не создадутся: их узнают по номеру отклика.")

    if not args.apply:
        print("\nПока ничего не изменено.")
        if not sys.stdin.isatty():
            print("Повторите с --apply, чтобы забрать.")
            return
        answer = input(f"Забрать отклики за {days} дн.? (д/н): ").strip().lower()
        if answer not in ("д", "да", "y", "yes"):
            print("Ничего не трогаю.")
            return

    state.set_time(state.LAST_HH_POLL, datetime.now(timezone.utc) - timedelta(days=days))
    print(
        f"\nГотово. Бот считает, что последний раз смотрел отклики {days} дн. назад.\n"
        "Кандидаты появятся при следующем опросе: перезапустите бота "
        "(start.bat) — опрос идёт сразу при запуске."
    )


if __name__ == "__main__":
    asyncio.run(main())
