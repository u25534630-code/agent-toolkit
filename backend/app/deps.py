"""Сборка зависимостей. Одно место, где создаются клиенты внешних систем."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.integrations.bitrix import BitrixClient
from app.integrations.claude import CommandParser
from app.integrations.rules import RuleParser
from app.integrations.hh import HHClient
from app.integrations.sheets import SheetsClient
from app.integrations.stt import Transcriber
from app.services.candidates import RecruitingService
from app.services.reminders import ReminderService

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    bitrix: BitrixClient | None
    hh: HHClient | None
    sheets: SheetsClient | None
    parser: CommandParser | RuleParser
    transcriber: Transcriber
    reminders: ReminderService
    recruiting: RecruitingService

    async def close(self) -> None:
        if self.bitrix:
            await self.bitrix.close()
        if self.hh:
            await self.hh.close()


_context: AppContext | None = None


def build_context() -> AppContext:
    global _context
    if _context is not None:
        return _context

    settings = get_settings()

    bitrix = None
    if settings.bitrix_configured:
        bitrix = BitrixClient()
    else:
        logger.warning(
            "Битрикс не настроен — работаем без CRM. Отклики, таблица, "
            "напоминания и отчёты работают как обычно. Чтобы включить CRM, "
            "впишите BITRIX_WEBHOOK_URL в .env и перезапустите."
        )

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
        # Трассировка на весь экран пугает и ничего не подсказывает: причина
        # почти всегда одна из двух, и обе называются одной строкой. Полный
        # разбор — в scripts/check.py, туда и отправляем.
        try:
            sheets = SheetsClient()
        except FileNotFoundError:
            logger.warning(
                "Файл ключа Google не найден: %s. В .env строка "
                "GOOGLE_CREDENTIALS_FILE должна указывать на сам файл ключа, "
                "обычно ./service-account.json. Бот работает, таблица не "
                "заполняется.",
                settings.google_credentials_file,
            )
        except Exception as error:
            logger.warning(
                "Google-таблица недоступна: %s. Частая причина — таблица не "
                "расшарена на адрес сервисного аккаунта. Что именно не так, "
                "покажет check.bat. Бот работает, таблица не заполняется.",
                error,
            )
            logger.debug("Полная ошибка Google Sheets", exc_info=True)
    else:
        logger.warning("GOOGLE_SPREADSHEET_ID не задан — запись в таблицу выключена")

    if settings.anthropic_configured:
        parser: CommandParser | RuleParser = CommandParser()
    else:
        parser = RuleParser()
        logger.warning(
            "Ключ Anthropic не задан — команды разбираются правилами. "
            "Работает без оплаты, но хуже понимает вольные формулировки. "
            "Говорите по образцу: «Фамилия, что произошло»."
        )

    reminders = ReminderService()
    recruiting = RecruitingService(bitrix=bitrix, sheets=sheets, reminders=reminders)

    _context = AppContext(
        settings=settings,
        bitrix=bitrix,
        hh=hh,
        sheets=sheets,
        parser=parser,
        transcriber=Transcriber(),
        reminders=reminders,
        recruiting=recruiting,
    )
    return _context
