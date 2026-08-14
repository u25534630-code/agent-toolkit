"""Пометки бота о себе: что он уже делал и когда.

Отдельно от кандидатов: это не данные подбора, а память процесса, которая
должна пережить перезапуск и выключенный на ночь компьютер.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.db.models import AppState
from app.db.session import session_scope

logger = logging.getLogger(__name__)

LAST_HH_POLL = "last_hh_poll"


def get_time(key: str) -> datetime | None:
    with session_scope() as session:
        row = session.get(AppState, key)
        raw = row.value if row else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("Не разобрал сохранённое время %s=%r", key, raw)
        return None


def set_time(key: str, value: datetime) -> None:
    with session_scope() as session:
        row = session.get(AppState, key)
        if row is None:
            row = AppState(key=key)
            session.add(row)
        row.value = value.isoformat()
        row.updated_at = datetime.now()
