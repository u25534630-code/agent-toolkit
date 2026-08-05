"""Точка входа: FastAPI + бот + планировщик в одном процессе.

FastAPI нужен для health-check и REST, на который позже сядет фронтенд
(его пишем с Claude Sonnet 5). Бот работает на long polling — вебхук
Telegram потребовал бы публичного HTTPS, а это лишняя зависимость на старте.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from aiogram.exceptions import TelegramConflictError
from fastapi import FastAPI
from sqlalchemy import select

from app.bot.main import get_bot, get_dispatcher
from app.config import get_settings
from app.db.models import Candidate, CandidateStatus
from app.db.session import init_db, session_scope
from app.deps import build_context
from app.scheduler import build_scheduler
from app.services.reports import build_daily_report

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _report_polling_stopped(task: asyncio.Task) -> None:
    """Сказать вслух, если приём сообщений прекратился.

    Задача поллинга никем не ожидается, поэтому её исключение до сих пор
    пропадало: в журнале оставалась строка «Polling stopped», процесс жил
    дальше, а бот молчал — со стороны неотличимо от «программа сломалась».
    """
    if task.cancelled():  # штатная остановка при выключении
        return

    error = task.exception()
    if error is None:
        logger.error("Бот перестал принимать сообщения. Перезапустите start.bat")
        return

    if isinstance(error, TelegramConflictError):
        logger.error(
            "Бот уже запущен где-то ещё — Телеграм отдаёт сообщения только "
            "одному экземпляру. Закройте другие чёрные окна с ботом и "
            "запустите start.bat заново. Если окон не видно, откройте "
            "«Диспетчер задач» и снимите все процессы python.exe."
        )
        return

    logger.error("Бот перестал принимать сообщения: %s", error, exc_info=error)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    init_db()

    context = build_context()

    if settings.dry_run:
        logger.warning("DRY_RUN включён — во внешние системы ничего не пишется")

    # Сразу показываем, как легли колонки таблицы: молчаливое несовпадение
    # заголовков — самый неприятный способ узнать о проблеме через неделю
    if context.sheets:
        from app.integrations.sheets import REQUIRED_INTERNS, REQUIRED_TRACKING

        sheets_to_check = (
            (settings.sheet_tracking_name, REQUIRED_TRACKING),
            (settings.sheet_interns_name, REQUIRED_INTERNS),
        )
        for sheet_name, required in sheets_to_check:
            try:
                layout = context.sheets.layout(sheet_name)
                logger.info(
                    "Лист «%s»: колонки %s",
                    sheet_name,
                    ", ".join(sorted(layout.columns)),
                )
                missing = layout.missing(required)
                if missing:
                    logger.warning(
                        "Лист «%s»: не нашёл колонки для %s — они не заполнятся",
                        sheet_name,
                        ", ".join(missing),
                    )
            except Exception:
                logger.exception("Лист «%s» недоступен", sheet_name)

    scheduler = build_scheduler()
    scheduler.start()

    bot = get_bot()
    dispatcher = get_dispatcher()
    polling = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False))
    polling.add_done_callback(_report_polling_stopped)
    logger.info("Бот запущен")

    try:
        yield
    finally:
        polling.cancel()
        scheduler.shutdown(wait=False)
        await context.close()
        await bot.session.close()
        logger.info("Остановлено")


app = FastAPI(title="Рекрутинговый ассистент", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    context = build_context()
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "parser": "anthropic" if settings.anthropic_configured else "rules",
        "bitrix_enabled": context.bitrix is not None,
        "hh_enabled": context.hh is not None,
        "sheets_enabled": context.sheets is not None,
    }


@app.get("/api/candidates")
async def list_candidates(status: str | None = None, limit: int = 100) -> list[dict]:
    """Для фронтенда: список кандидатов."""
    with session_scope() as session:
        statement = select(Candidate).order_by(Candidate.updated_at.desc()).limit(limit)
        if status:
            statement = statement.where(Candidate.status == CandidateStatus(status))

        return [
            {
                "id": c.id,
                "full_name": c.full_name,
                "phone": c.phone,
                "city": c.city,
                "age": c.age,
                "experience_years": c.experience_years,
                "salary_expectation": c.salary_expectation,
                "vacancy_title": c.vacancy_title,
                "resume_url": c.resume_url,
                "status": c.status.value,
                "reject_reason": c.reject_reason,
                "interview_at": c.interview_at.isoformat() if c.interview_at else None,
                "bitrix_deal_id": c.bitrix_deal_id,
                "bitrix_contact_id": c.bitrix_contact_id,
            }
            for c in session.scalars(statement)
        ]


@app.get("/api/stats/daily")
async def daily_stats(day: date | None = None) -> dict:
    """Для фронтенда: сводка за день."""
    with session_scope() as session:
        report = build_daily_report(session, day)

    return {
        "day": report.day.isoformat(),
        "total_calls": report.total_calls,
        "reached": report.reached,
        "no_answer": report.no_answer,
        "rejected": report.rejected,
        "reserved": report.reserved,
        "interviews_scheduled": report.interviews_scheduled,
        "interviews_passed": report.interviews_passed,
        "hired": report.hired,
        "productive": report.productive,
        "reject_reasons": dict(report.reject_reasons),
    }
