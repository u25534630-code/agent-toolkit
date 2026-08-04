"""Фоновые задачи: поллинг откликов, напоминания, вечерний отчёт."""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.main import get_bot
from app.db.session import session_scope
from app.deps import build_context
from app.services.reports import build_daily_report

logger = logging.getLogger(__name__)


async def poll_hh_responses() -> None:
    """Забрать новые отклики, завести лиды, сообщить рекрутеру."""
    context = build_context()
    if context.hh is None:
        return

    try:
        incoming = await context.hh.fetch_new_responses()
    except Exception:
        logger.exception("Поллинг откликов hh.ru упал")
        await _notify_owner(
            "Не смог забрать отклики с hh.ru. Возможно, истёк токен работодателя "
            "— нужна повторная авторизация."
        )
        return

    if not incoming:
        return

    created = []
    with session_scope() as session:
        for item in incoming:
            try:
                candidate = await context.recruiting.intake_from_hh(session, item)
            except Exception:
                logger.exception("Не смог завести кандидата %s", item.full_name)
                continue
            if candidate:
                created.append(
                    {
                        "name": candidate.short_name,
                        "age": candidate.age,
                        "city": candidate.city,
                        "experience": candidate.experience_years,
                        "vacancy": candidate.vacancy_title,
                        "deal": candidate.bitrix_deal_id,
                    }
                )

    if created:
        await _notify_owner(_render_new_responses(created))


def _render_new_responses(items: list[dict]) -> str:
    lines = [f"<b>Новых откликов: {len(items)}</b>", ""]
    for item in items:
        details = [str(part) for part in (item["age"], item["city"]) if part]
        if item["experience"]:
            details.append(f"опыт {item['experience']} г.")
        suffix = f" — {', '.join(details)}" if details else ""
        deal = f" · сделка #{item['deal']}" if item["deal"] else ""
        vacancy = f"\n  {item['vacancy']}" if item["vacancy"] else ""
        lines.append(f"· <b>{item['name']}</b>{suffix}{deal}{vacancy}")
    lines.append("")
    lines.append("Карточки заведены. После обзвона отчитайтесь голосом или текстом.")
    return "\n".join(lines)


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
    )

    if build_context().hh is not None:
        scheduler.add_job(
            poll_hh_responses,
            IntervalTrigger(minutes=settings.hh_poll_interval_minutes),
            id="hh_poll",
            replace_existing=True,
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
