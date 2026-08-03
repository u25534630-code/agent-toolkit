"""Напоминания в Telegram.

Хранятся в БД, а не только в планировщике: перезапуск процесса не должен
терять напоминание о завтрашнем собеседовании. Планировщик раз в минуту
забирает те, чьё время пришло.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Candidate, Reminder, ReminderKind

logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def schedule_interview(
        self, session: Session, candidate: Candidate, when: datetime, chat_id: int
    ) -> None:
        """Два напоминания: уточнить явку за сутки и предупредить за час."""
        self.cancel_all(session, candidate.id)
        now = datetime.now(self._settings.tz)

        confirm_at = when - timedelta(hours=self._settings.interview_confirm_hours_before)
        reminder_at = when - timedelta(hours=self._settings.interview_reminder_hours_before)

        planned = [
            (
                ReminderKind.confirm,
                confirm_at,
                f"Уточнить у {candidate.short_name}, будет ли он на собеседовании "
                f"{when.strftime('%d.%m в %H:%M')}."
                + (f"\nТелефон: {candidate.phone}" if candidate.phone else ""),
            ),
            (
                ReminderKind.interview,
                reminder_at,
                f"Собеседование с {candidate.short_name} в "
                f"{when.strftime('%H:%M')}."
                + (f"\nТелефон: {candidate.phone}" if candidate.phone else ""),
            ),
        ]

        for kind, fire_at, text in planned:
            # Собес назначили на сегодня — напоминание «за сутки» уже неактуально
            if fire_at <= now:
                continue
            session.add(
                Reminder(
                    candidate_id=candidate.id,
                    kind=kind,
                    fire_at=fire_at,
                    text=text,
                    chat_id=chat_id,
                )
            )

    def schedule_callback(
        self, session: Session, candidate: Candidate, chat_id: int, hours: int = 20
    ) -> None:
        fire_at = datetime.now(self._settings.tz) + timedelta(hours=hours)
        session.add(
            Reminder(
                candidate_id=candidate.id,
                kind=ReminderKind.callback,
                fire_at=fire_at,
                text=f"Перезвонить {candidate.short_name} — вчера не дозвонились."
                + (f"\nТелефон: {candidate.phone}" if candidate.phone else ""),
                chat_id=chat_id,
            )
        )

    @staticmethod
    def cancel_all(session: Session, candidate_id: int) -> None:
        session.execute(
            update(Reminder)
            .where(Reminder.candidate_id == candidate_id)
            .where(Reminder.sent.is_(False))
            .where(Reminder.cancelled.is_(False))
            .values(cancelled=True)
        )

    @staticmethod
    def due(session: Session, now: datetime) -> list[Reminder]:
        statement = (
            select(Reminder)
            .where(Reminder.sent.is_(False))
            .where(Reminder.cancelled.is_(False))
            .where(Reminder.fire_at <= now)
            .order_by(Reminder.fire_at)
        )
        return list(session.scalars(statement))

    @staticmethod
    def mark_sent(session: Session, reminder_id: int) -> None:
        session.execute(
            update(Reminder).where(Reminder.id == reminder_id).values(sent=True)
        )
