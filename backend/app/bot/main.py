"""Экземпляр бота и диспетчера."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject, Update

from app.bot.handlers import router
from app.config import get_settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            token=get_settings().telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


async def _trace_updates(
    handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
    event: TelegramObject,
    data: dict[str, Any],
) -> Any:
    """Записать каждое входящее сообщение в журнал.

    Когда бот молчит, без этого не отличить «сообщение не дошло» от
    «дошло, но обработчик промолчал» — а это разные починки.
    """
    inner = getattr(event, "event", None) if isinstance(event, Update) else event
    user = getattr(inner, "from_user", None)
    logger.info(
        "Входящее: %s от %s",
        type(inner).__name__,
        getattr(user, "id", "неизвестно"),
    )
    return await handler(event, data)


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.update.outer_middleware(_trace_updates)
        _dispatcher.include_router(router)
    return _dispatcher
