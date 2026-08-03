"""Сборка зависимостей. Одно место, где создаются клиенты внешних систем."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.integrations.bitrix import BitrixClient
from app.integrations.claude import CommandParser
from app.integrations.hh import HHClient
from app.integrations.sheets import SheetsClient
from app.integrations.stt import Transcriber
from app.services.candidates import RecruitingService
from app.services.reminders import ReminderService

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    bitrix: BitrixClient
    hh: HHClient | None
    sheets: SheetsClient | None
    parser: CommandParser
    transcriber: Transcriber
    reminders: ReminderService
    recruiting: RecruitingService

    async def close(self) -> None:
        await self.bitrix.close()
        if self.hh:
            await self.hh.close()


_context: AppContext | None = None


def build_context() -> AppContext:
    global _context
    if _context is not None:
        return _context

    settings = get_settings()

    bitrix = BitrixClient()

    hh = None
    if settings.hh_configured:
        hh = HHClient()
    else:
        logger.warning(
            "hh.ru не настроен — поллинг откликов выключен. "
            "Заполните HH_ACCESS_TOKEN и HH_EMPLOYER_ID в .env"
        )

    sheets = None
    if settings.sheets_configured:
        try:
            sheets = SheetsClient()
        except Exception:
            logger.exception(
                "Google Sheets недоступны — проверьте, что таблица расшарена "
                "на e-mail сервисного аккаунта. Бот продолжит работу без таблицы."
            )
    else:
        logger.warning("GOOGLE_SPREADSHEET_ID не задан — запись в таблицу выключена")

    reminders = ReminderService()
    recruiting = RecruitingService(bitrix=bitrix, sheets=sheets, reminders=reminders)

    _context = AppContext(
        settings=settings,
        bitrix=bitrix,
        hh=hh,
        sheets=sheets,
        parser=CommandParser(),
        transcriber=Transcriber(),
        reminders=reminders,
        recruiting=recruiting,
    )
    return _context
