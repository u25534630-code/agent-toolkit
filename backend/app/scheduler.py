"""Фоновые задачи: поллинг откликов, напоминания, вечерний отчёт."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot import keyboards
from app.bot.main import get_bot
from app.db.session import session_scope
from app.deps import build_context
from app.services.reports import build_daily_report

logger = logging.getLogger(__name__)


# Одна и та же жалоба каждый цикл — это не информирование, а спам: пять
# одинаковых сообщений подряд не сообщают больше, чем одно, зато приучают
# их пролистывать. Повторяем, только если ошибка изменилась или прошло много
# времени.
_last_error: tuple[str, datetime] | None = None
_ERROR_REPEAT_AFTER = timedelta(hours=6)


def _should_report(text: str) -> bool:
    global _last_error
    now = datetime.now(timezone.utc)
    if _last_error and _last_error[0] == text and now - _last_error[1] < _ERROR_REPEAT_AFTER:
        return False
    _last_error = (text, now)
    return True


async def poll_hh_responses() -> None:
    """Забрать новые отклики, завести лиды, сообщить рекрутеру."""
    context = build_context()
    if context.hh is None:
        return

    logger.info("Проверяю отклики на hh.ru…")
    try:
        incoming = await context.hh.fetch_new_responses()
    except Exception as error:
        logger.exception("Поллинг откликов hh.ru упал")
        # «Возможно, истёк токен» — догадка, которая уводит не туда. Причин
        # много: нет активных вакансий, отказ в правах, обрыв связи. Пусть
        # видно будет, что ответил сам hh.ru.
        if _should_report(str(error)[:300]):
            await _notify_owner(
                "Не смог забрать отклики с hh.ru.\n\n"
                f"<code>{str(error)[:300]}</code>\n\n"
                "Если в ответе 403 или слово token — нужна повторная "
                "авторизация. Остальное покажет check.bat.\n"
                "Повторю это сообщение не раньше чем через 6 часов."
            )
        return

    global _last_error
    _last_error = None

    if not incoming:
        # Молчание неотличимо от поломки: человек смотрит в окно и не знает,
        # то ли откликов нет, то ли опрос не дошёл
        logger.info(
            "Новых откликов нет (беру не старше %d дн.)",
            context.settings.hh_skip_older_than_days,
        )
        return

    created = []
    known = 0
    failed = 0
    limit = context.settings.hh_max_new_per_poll
    with session_scope() as session:
        for item in incoming:
            # Ограничение считаем по заведённым, а не по прочитанным: дубли
            # места не занимают, а вот новых за раз должно быть немного
            if limit and len(created) >= limit:
                logger.warning(
                    "Достиг предела в %d новых кандидатов за цикл — "
                    "остальные заберу в следующий опрос.",
                    limit,
                )
                break
            try:
                candidate = await context.recruiting.intake_from_hh(session, item)
            except Exception:
                logger.exception("Не смог завести кандидата %s", item.full_name)
                failed += 1
                continue
            if candidate is None:
                known += 1
            else:
                created.append(
                    {
                        "name": candidate.short_name,
                        "age": candidate.age,
                        "city": candidate.city,
                        "experience": candidate.experience_years,
                        "vacancy": candidate.vacancy_title,
                        "deal": candidate.bitrix_deal_id,
                        "id": candidate.id,
                        "resume": candidate.resume_url,
                        "phone": candidate.phone,
                    }
                )

    # «Заведено новых: 0» само по себе не говорит, всё ли в порядке: так
    # выглядят и уже разобранные отклики, и молчаливая поломка. Разделяем
    logger.info(
        "Откликов получено: %d — новых: %d, уже были: %d, не удалось завести: %d",
        len(incoming),
        len(created),
        known,
        failed,
    )
    if failed:
        await _notify_owner(
            f"Не смог завести кандидатов: {failed}. Причина — в окне бота, "
            "строка с «Не смог завести кандидата»."
        )
    if created:
        await _notify_new_responses(created)


async def _notify_new_responses(items: list[dict]) -> None:
    """По сообщению на кандидата — чтобы к каждому шли свои кнопки.

    Одним списком читать удобнее, но тогда исход приходится диктовать
    отдельно, называя фамилию. Отдельные сообщения дают нажать «Не подходит»
    прямо под тем, кого только что обзвонили.
    """
    bot = get_bot()
    settings = build_context().settings
    chat_ids = settings.telegram_allowed_user_ids

    header = f"<b>Новых откликов: {len(items)}</b>"
    for chat_id in chat_ids:
        await bot.send_message(chat_id, header, parse_mode="HTML")

    for item in items:
        details = [str(part) for part in (item["age"], item["city"]) if part]
        if item["experience"]:
            details.append(f"опыт {item['experience']} г.")
        suffix = f" — {', '.join(details)}" if details else ""
        deal = f"\nсделка #{item['deal']}" if item["deal"] else ""
        vacancy = f"\n{item['vacancy']}" if item["vacancy"] else ""
        # Ссылка прямо в сообщении: посмотреть резюме — одно нажатие,
        # без входа на hh.ru и поиска человека там
        phone = f"\n{item['phone']}" if item.get("phone") else ""
        resume = (
            f"\n<a href=\"{item['resume']}\">Открыть резюме</a>"
            if item.get("resume")
            else ""
        )
        text = f"<b>{item['name']}</b>{suffix}{vacancy}{phone}{resume}{deal}"

        markup = keyboards.quick_actions(item["id"]) if item.get("id") else None
        for chat_id in chat_ids:
            await bot.send_message(
                chat_id, text, parse_mode="HTML", reply_markup=markup
            )


async def fire_due_reminders() -> None:
    context = build_context()
    bot = get_bot()
    now = datetime.now(context.settings.tz)

    with session_scope() as session:
        due = context.reminders.due(session, now)
        for reminder in due:
            try:
                await bot.send_message(reminder.chat_id, f"⏰ {reminder.text}")
                context.reminders.mark_sent(session, reminder.id)
            except Exception:
                logger.exception("Не смог отправить напоминание %s", reminder.id)


async def send_daily_report() -> None:
    with session_scope() as session:
        report = build_daily_report(session)
    await _notify_owner(report.render())


async def _notify_owner(text: str) -> None:
    settings = build_context().settings
    bot = get_bot()
    for user_id in settings.telegram_allowed_user_ids:
        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
        except Exception:
            logger.exception("Не смог написать пользователю %s", user_id)


def build_scheduler() -> AsyncIOScheduler:
    settings = build_context().settings
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    scheduler.add_job(
        fire_due_reminders,
        IntervalTrigger(minutes=1),
        id="reminders",
        replace_existing=True,
        # Окно консоли встаёт на паузу от случайного щелчка мышью. Напоминания
        # от этого не теряются — они лежат в базе, — но планировщик пишет
        # предупреждение на каждый пропущенный тик
        misfire_grace_time=300,
    )

    if build_context().hh is not None:
        # По часам, а не по интервалу: интервал отсчитывается от запуска, и
        # «дважды в день» после каждого перезапуска съезжает на новое время.
        # Час дня человек называет сам и знает, когда смотреть.
        times = settings.hh_poll_at
        if times:
            # Каждое время — отдельный CronTrigger в OrTrigger. Один общий
            # CronTrigger с hour="10,17" и minute="0,30" сработал бы ещё и
            # в 10:30, и в 17:00: списки часов и минут перемножаются
            trigger = OrTrigger(
                [
                    CronTrigger(hour=t.hour, minute=t.minute, timezone=settings.tz)
                    for t in times
                ]
            )
            when = ", ".join(t.strftime("%H:%M") for t in times)
        else:
            trigger = IntervalTrigger(minutes=settings.hh_poll_interval_minutes)
            when = f"каждые {settings.hh_poll_interval_minutes} мин"

        logger.info(
            "Отклики с hh.ru: опрос %s, беру не старше %d дн., "
            "не больше %d новых за раз",
            when,
            settings.hh_skip_older_than_days,
            settings.hh_max_new_per_poll,
        )
        scheduler.add_job(
            poll_hh_responses,
            trigger,
            id="hh_poll",
            replace_existing=True,
            misfire_grace_time=600,
            # Первый опрос — сразу при запуске, а не в ближайший назначенный
            # час. Иначе после перезапуска отклики ждут полдня, и проверить,
            # работает ли связь с hh.ru, можно только набравшись терпения
            next_run_time=datetime.now(settings.tz),
        )

    scheduler.add_job(
        send_daily_report,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.report_time.hour,
            minute=settings.report_time.minute,
        ),
        id="daily_report",
        replace_existing=True,
    )

    return scheduler
