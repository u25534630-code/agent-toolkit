"""Доменный слой: что происходит с кандидатом при каждом действии рекрутера.

Здесь собрана вся логика «перевести стадию в Битриксе + дописать строку в
таблицу + поставить или снять напоминания». Бот и планировщик вызывают эти
методы и сами ничего не знают про внешние системы.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    CallLog,
    CallOutcome,
    Candidate,
    CandidateStatus,
    normalize_name,
)
from app.integrations.bitrix import BitrixClient
from app.integrations.claude import Command
from app.integrations.hh import HHCandidate, HHClient
from app.integrations.sheets import SheetsClient
from app.services.reminders import ReminderService

logger = logging.getLogger(__name__)

ACTION_TO_STATUS: dict[str, CandidateStatus] = {
    "reject": CandidateStatus.rejected,
    "schedule_interview": CandidateStatus.interview_scheduled,
    "interview_passed": CandidateStatus.interview_passed,
    "no_answer": CandidateStatus.no_answer,
    "hired": CandidateStatus.hired,
}

ACTION_TO_OUTCOME: dict[str, CallOutcome] = {
    "reject": CallOutcome.rejected,
    "schedule_interview": CallOutcome.interview_scheduled,
    "interview_passed": CallOutcome.interview_passed,
    "no_answer": CallOutcome.no_answer,
    "reserve": CallOutcome.reserve,
    "hired": CallOutcome.hired,
    "note": CallOutcome.note,
}


class RecruitingService:
    def __init__(
        self,
        bitrix: BitrixClient | None,
        sheets: SheetsClient | None,
        reminders: ReminderService,
        hh: HHClient | None = None,
    ) -> None:
        self._bitrix = bitrix
        self._sheets = sheets
        self._reminders = reminders
        self._hh = hh
        self._settings = get_settings()

    # ---------- Поиск кандидата ----------

    @staticmethod
    def find_candidates(session: Session, reference: str) -> list[Candidate]:
        """Найти кандидатов по тому, как их назвал рекрутер.

        Ищем среди незакрытых: «Петрова не подходит» почти наверняка про ту
        Петрову, с которой сейчас работаем, а не про закрытую полгода назад.
        """
        needle = f"%{normalize_name(reference)}%"
        active = [s for s in CandidateStatus if s.is_active]
        statement = (
            select(Candidate)
            .where(Candidate.status.in_(active))
            .where(Candidate.search_key.like(needle))
            .order_by(Candidate.updated_at.desc())
            .limit(10)
        )
        found = list(session.scalars(statement))
        if found:
            return found

        # Кандидат мог быть закрыт по ошибке — расширяем поиск на всех
        fallback = (
            select(Candidate)
            .where(Candidate.search_key.like(needle))
            .order_by(Candidate.updated_at.desc())
            .limit(10)
        )
        return list(session.scalars(fallback))

    @staticmethod
    def active_last_names(session: Session, limit: int = 60) -> list[str]:
        """Подсказка для распознавания речи и для промпта разбора команд."""
        statement = (
            select(Candidate.last_name)
            .where(Candidate.last_name.is_not(None))
            .where(Candidate.status.in_([s for s in CandidateStatus if s.is_active]))
            .order_by(Candidate.updated_at.desc())
            .limit(limit)
        )
        return [name for name in session.scalars(statement) if name]

    # ---------- Приём откликов с hh.ru ----------

    async def intake_from_hh(
        self, session: Session, incoming: HHCandidate
    ) -> Candidate | None:
        """Завести кандидата по отклику. Повторный отклик не создаёт дубль."""
        existing = session.scalar(
            select(Candidate).where(
                Candidate.hh_negotiation_id == incoming.negotiation_id
            )
        )
        if existing:
            return None

        candidate = Candidate(
            hh_negotiation_id=incoming.negotiation_id,
            hh_resume_id=incoming.resume_id,
            hh_vacancy_id=incoming.vacancy_id,
            resume_url=incoming.resume_url,
            full_name=incoming.full_name,
            first_name=incoming.first_name,
            last_name=incoming.last_name,
            phone=incoming.phone,
            age=incoming.age,
            city=incoming.city,
            experience_years=incoming.experience_years,
            salary_expectation=incoming.salary_expectation,
            vacancy_title=incoming.vacancy_title,
            status=CandidateStatus.new,
        )
        session.add(candidate)
        session.flush()

        if self._bitrix:
            # Контакт + сделка в воронке HR; повторный отклик переиспользует
            # уже существующую карточку, а не плодит вторую
            contact_id, deal_id = await self._bitrix.create_candidate(candidate)
            candidate.bitrix_contact_id = contact_id
            candidate.bitrix_deal_id = deal_id

        session.flush()
        return candidate

    # ---------- Применение команды ----------

    async def add_candidate(self, session: Session, command: Command) -> Candidate:
        """Завести кандидата вручную — когда он пришёл не с hh.ru.

        Нужно и само по себе (звонок по объявлению, рекомендация знакомого),
        и чтобы ботом можно было пользоваться, пока hh.ru не подключён.
        """
        full_name = (command.candidate_ref or "").strip()
        parts = full_name.split()

        candidate = Candidate(
            full_name=full_name,
            last_name=parts[0] if parts else None,
            first_name=parts[1] if len(parts) > 1 else None,
            phone=command.phone,
            city=command.city,
            vacancy_title=command.position,
            comment=command.comment,
            status=CandidateStatus.new,
        )
        session.add(candidate)
        session.flush()

        if self._bitrix:
            contact_id, deal_id = await self._bitrix.create_candidate(candidate)
            candidate.bitrix_contact_id = contact_id
            candidate.bitrix_deal_id = deal_id
            session.flush()

        return candidate

    async def apply(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        """Выполнить разобранную команду. Возвращает текст отчёта для бота."""
        outcome = ACTION_TO_OUTCOME.get(command.action, CallOutcome.note)
        session.add(
            CallLog(
                candidate_id=candidate.id,
                outcome=outcome,
                reject_reason=command.reject_reason,
                raw_input=command.comment,
            )
        )

        if command.comment:
            candidate.comment = command.comment

        handlers = {
            "reject": self._reject,
            "schedule_interview": self._schedule_interview,
            "interview_passed": self._interview_passed,
            "no_answer": self._no_answer,
            "reserve": self._reserve,
            "hired": self._hired,
            "note": self._note,
        }
        handler = handlers.get(command.action)
        if handler is None:
            return "Не понял, что нужно сделать."

        return await handler(session, candidate, command, chat_id)

    # ---------- Обработчики действий ----------

    async def _reject(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        candidate.status = CandidateStatus.rejected
        candidate.reject_reason = command.reject_reason

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(
                candidate.bitrix_deal_id,
                CandidateStatus.rejected,
                reject_reason=command.reject_reason,
            )
        self._reminders.cancel_all(session, candidate.id)

        # Кандидат мог уже быть в таблице — отмечаем там отказ, строку не удаляем
        if candidate.sheet_row_tracking and self._sheets:
            await asyncio.to_thread(
                self._sheets.update_tracking_status,
                candidate.sheet_row_tracking,
                "Отказ",
                command.reject_reason,
            )

        reason = f" ({command.reject_reason})" if command.reject_reason else ""
        lines = [f"Закрыл {self._label(candidate)} → Не подходит{reason}."]

        # Отказ на hh.ru уходит соискателю, поэтому только по явной настройке
        if (
            self._settings.hh_send_rejection
            and self._hh
            and candidate.hh_negotiation_id
        ):
            try:
                action = await self._hh.discard(candidate.hh_negotiation_id)
            except Exception as error:  # noqa: BLE001 — причину показываем как есть
                logger.warning("Отказ на hh.ru не ушёл: %s", error)
                lines.append("Отказ на hh.ru отправить не смог — сделайте вручную.")
            else:
                if action:
                    lines.append("Отказ на hh.ru отправлен.")
                    # Статус видит только работодатель — человеку нужен текст
                    template = self._settings.hh_rejection_message.strip()
                    if template:
                        text = template.format(
                            name=candidate.first_name or candidate.short_name
                        )
                        sent = await self._hh.send_message(
                            candidate.hh_negotiation_id, text
                        )
                        lines.append(
                            "Кандидату написал." if sent
                            else "Сообщение кандидату не ушло — напишите на сайте."
                        )
                else:
                    lines.append(
                        "На hh.ru отказ недоступен по этому отклику — "
                        "возможно, он уже закрыт."
                    )

        return "\n".join(lines)

    async def _schedule_interview(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        when = command.interview_datetime
        if when is None:
            return "Не разобрал дату собеседования — назовите её ещё раз."

        when = when.replace(tzinfo=self._settings.tz) if when.tzinfo is None else when
        candidate.status = CandidateStatus.interview_scheduled
        candidate.interview_at = when

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(
                candidate.bitrix_deal_id, CandidateStatus.interview_scheduled
            )
            await self._bitrix.add_interview_activity(
                candidate.bitrix_deal_id, candidate.short_name, when
            )

        added_to_sheet = False
        if self._sheets and not candidate.sheet_row_tracking:
            candidate.sheet_row_tracking = await asyncio.to_thread(
                self._sheets.append_tracking, candidate
            )
            added_to_sheet = candidate.sheet_row_tracking is not None

        self._reminders.schedule_interview(session, candidate, when, chat_id)

        lines = [
            f"{self._label(candidate)} → собеседование "
            f"{when.strftime('%d.%m в %H:%M')}."
        ]
        if added_to_sheet:
            lines.append(f"Добавил в «{self._settings.sheet_tracking_name}».")
        elif self._sheets and self._settings.dry_run:
            lines.append("Пробный режим — в таблицу не записывал.")
        lines.append("Напомню за сутки уточнить явку и за час до собеседования.")
        return "\n".join(lines)

    async def _interview_passed(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        candidate.status = CandidateStatus.interview_passed

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(
                candidate.bitrix_deal_id, CandidateStatus.interview_passed
            )
        self._reminders.cancel_all(session, candidate.id)

        moved_to_interns = False
        if self._sheets:
            if candidate.sheet_row_tracking:
                await asyncio.to_thread(
                    self._sheets.update_tracking_status,
                    candidate.sheet_row_tracking,
                    "Прошёл собеседование",
                    command.comment,
                )
            candidate.sheet_row_intern = await asyncio.to_thread(
                self._sheets.append_intern, candidate
            )
            moved_to_interns = candidate.sheet_row_intern is not None

        message = f"{self._label(candidate)}: собеседование пройдено."
        if moved_to_interns:
            message += f" Добавил на вкладку «{self._settings.sheet_interns_name}»."
        elif self._sheets and self._settings.dry_run:
            message += " Пробный режим — в таблицу не записывал."
        return message

    async def _no_answer(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        candidate.status = CandidateStatus.no_answer

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(
                candidate.bitrix_deal_id,
                CandidateStatus.no_answer,
                comment="Недозвон",
            )
        self._reminders.schedule_callback(session, candidate, chat_id)
        return f"{self._label(candidate)} → недозвон. Напомню перезвонить завтра."

    async def _reserve(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        """Кадровый резерв. Не отказ — кандидат остаётся в поиске по фамилии."""
        candidate.status = CandidateStatus.reserve

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(
                candidate.bitrix_deal_id, CandidateStatus.reserve, comment=command.comment
            )
        self._reminders.cancel_all(session, candidate.id)

        if self._sheets and candidate.sheet_row_tracking:
            await asyncio.to_thread(
                self._sheets.update_tracking_status,
                candidate.sheet_row_tracking,
                "Кадровый резерв",
                command.comment,
            )
        return f"{self._label(candidate)} → кадровый резерв. Найду, когда понадобится."

    async def _hired(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        candidate.status = CandidateStatus.hired

        if self._bitrix and candidate.bitrix_deal_id:
            await self._bitrix.set_stage(candidate.bitrix_deal_id, CandidateStatus.hired)
        self._reminders.cancel_all(session, candidate.id)

        if self._sheets and candidate.sheet_row_tracking:
            await asyncio.to_thread(
                self._sheets.update_tracking_status,
                candidate.sheet_row_tracking,
                "Вышел на работу",
                command.comment,
            )
        return f"{self._label(candidate)} → на работе."

    async def _note(
        self, session: Session, candidate: Candidate, command: Command, chat_id: int
    ) -> str:
        if self._bitrix and candidate.bitrix_deal_id and command.comment:
            await self._bitrix.update_deal(
                candidate.bitrix_deal_id, {"COMMENTS": command.comment}
            )
        return f"Записал заметку по {self._label(candidate)}."

    # ---------- Мелочи ----------

    @staticmethod
    def _label(candidate: Candidate) -> str:
        deal = f" (сделка #{candidate.bitrix_deal_id})" if candidate.bitrix_deal_id else ""
        return f"{candidate.short_name}{deal}"


def describe_command(command: Command, candidate: Candidate | None = None) -> str:
    """Предпросмотр перед подтверждением — что бот собирается сделать."""
    if command.action == "add_candidate":
        details = [
            part
            for part in (command.position, command.city, command.phone)
            if part
        ]
        suffix = f" — {', '.join(details)}" if details else ""
        return f"Завести кандидата: {command.candidate_ref}{suffix}?"

    if candidate is None:
        return "Не понял, о ком речь."

    name = candidate.short_name
    match command.action:
        case "reserve":
            return f"Отправить {name} в кадровый резерв?"
        case "reject":
            reason = command.reject_reason or "без причины"
            if get_settings().hh_send_rejection and candidate.hh_negotiation_id:
                return (
                    f"Закрыть {name} как не подходящего ({reason}) "
                    "и отправить отказ на hh.ru?"
                )
            return f"Закрыть {name} как не подходящего ({reason})?"
        case "schedule_interview":
            when = command.interview_datetime
            when_text = when.strftime("%d.%m в %H:%M") if when else "дата не разобрана"
            return f"Назначить {name} собеседование на {when_text}?"
        case "interview_passed":
            return f"{name}: собеседование пройдено — добавить в стажёры?"
        case "no_answer":
            return f"Отметить недозвон по {name}?"
        case "hired":
            return f"Отметить выход на работу: {name}?"
        case "note":
            return f"Записать заметку по {name}: «{command.comment or ''}»?"
        case _:
            return "Не понял команду."


def format_datetime(value: datetime, tz) -> str:
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M")
