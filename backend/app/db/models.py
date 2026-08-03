"""Модели БД. Наша база — связующее звено между hh.ru, Битриксом и таблицей."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CandidateStatus(str, enum.Enum):
    new = "new"
    called = "called"
    no_answer = "no_answer"
    rejected = "rejected"
    interview_scheduled = "interview_scheduled"
    interview_passed = "interview_passed"
    hired = "hired"

    @property
    def is_final(self) -> bool:
        return self in (CandidateStatus.rejected, CandidateStatus.hired)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)

    # hh.ru
    hh_negotiation_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    hh_resume_id: Mapped[str | None] = mapped_column(String(64), index=True)
    hh_vacancy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    resume_url: Mapped[str | None] = mapped_column(String(512))

    # Личные данные
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    last_name: Mapped[str | None] = mapped_column(String(128), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(64))
    age: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str | None] = mapped_column(String(128))
    experience_years: Mapped[float | None]
    salary_expectation: Mapped[str | None] = mapped_column(String(128))
    vacancy_title: Mapped[str | None] = mapped_column(String(255))

    # Нормализованное имя для поиска. Отдельная колонка, потому что LIKE/ILIKE
    # в SQLite сворачивает регистр только для латиницы: «петрова» не нашла бы
    # «Петрова». Заполняется автоматически, см. слушатель ниже.
    search_key: Mapped[str] = mapped_column(String(320), default="", index=True)

    # Внешние системы
    bitrix_lead_id: Mapped[int | None] = mapped_column(Integer, index=True)
    sheet_row_tracking: Mapped[int | None] = mapped_column(Integer)
    sheet_row_intern: Mapped[int | None] = mapped_column(Integer)

    # Наш процесс
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus), default=CandidateStatus.new, index=True
    )
    reject_reason: Mapped[str | None] = mapped_column(String(255))
    interview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    calls: Mapped[list["CallLog"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )

    @property
    def short_name(self) -> str:
        """«Петрова Анна» -> для сообщений в боте."""
        return " ".join(filter(None, [self.last_name, self.first_name])) or self.full_name


def normalize_name(value: str | None) -> str:
    """Регистр и «ё» не должны мешать найти человека."""
    return (value or "").strip().lower().replace("ё", "е")


@event.listens_for(Candidate, "before_insert")
@event.listens_for(Candidate, "before_update")
def _fill_search_key(mapper, connection, target: Candidate) -> None:
    parts = [target.full_name, target.last_name, target.first_name]
    target.search_key = normalize_name(" ".join(p for p in parts if p))


class ReminderKind(str, enum.Enum):
    confirm = "confirm"  # за сутки: уточнить, будет ли на собесе
    interview = "interview"  # за час: собес скоро
    callback = "callback"  # перезвонить по недозвону


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    kind: Mapped[ReminderKind] = mapped_column(Enum(ReminderKind))
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    text: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(Integer)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    candidate: Mapped[Candidate] = relationship(back_populates="reminders")


class CallOutcome(str, enum.Enum):
    no_answer = "no_answer"
    rejected = "rejected"
    interview_scheduled = "interview_scheduled"
    interview_passed = "interview_passed"
    hired = "hired"
    note = "note"

    @property
    def is_productive(self) -> bool:
        """Результативный звонок — тот, после которого кандидат продвинулся."""
        return self in (
            CallOutcome.interview_scheduled,
            CallOutcome.interview_passed,
            CallOutcome.hired,
        )


class CallLog(Base):
    """Один разобранный отчёт рекрутера о звонке. Основа вечерней сводки."""

    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    outcome: Mapped[CallOutcome] = mapped_column(Enum(CallOutcome), index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255))
    raw_input: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), index=True
    )

    candidate: Mapped[Candidate] = relationship(back_populates="calls")


class PendingAction(Base):
    """Разобранная, но ещё не подтверждённая команда.

    Бот показывает предпросмотр с кнопками; до нажатия «Подтвердить» ничего
    во внешние системы не пишется.
    """

    __tablename__ = "pending_actions"
    __table_args__ = (UniqueConstraint("token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now()
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
