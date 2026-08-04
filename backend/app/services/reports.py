"""Сводка по результативным звонкам за день."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CallLog, CallOutcome


@dataclass(slots=True)
class DailyReport:
    day: date
    total_calls: int = 0
    reached: int = 0
    no_answer: int = 0
    rejected: int = 0
    reserved: int = 0
    interviews_scheduled: int = 0
    interviews_passed: int = 0
    hired: int = 0
    reject_reasons: Counter = field(default_factory=Counter)

    @property
    def productive(self) -> int:
        return self.interviews_scheduled + self.interviews_passed + self.hired

    def render(self) -> str:
        if self.total_calls == 0:
            return f"Итоги {self.day.strftime('%d.%m')}: звонков не было."

        lines = [
            f"<b>Итоги {self.day.strftime('%d.%m')}</b>",
            "",
            f"Всего отчётов о звонках: {self.total_calls}",
            f"Дозвонились: {self.reached}",
            f"Недозвон: {self.no_answer}",
            "",
            f"<b>Результативных: {self.productive}</b>",
            f"· собеседований назначено: {self.interviews_scheduled}",
            f"· прошли собеседование: {self.interviews_passed}",
            f"· вышли на работу: {self.hired}",
            f"Отказов: {self.rejected}",
            f"В кадровый резерв: {self.reserved}",
        ]

        if self.reject_reasons:
            lines.append("")
            lines.append("Причины отказов:")
            lines.extend(
                f"· {reason} — {count}"
                for reason, count in self.reject_reasons.most_common(5)
            )

        if self.reached:
            conversion = round(self.productive / self.reached * 100)
            lines.append("")
            lines.append(f"Конверсия из дозвона в результат: {conversion}%")

        return "\n".join(lines)


def build_daily_report(session: Session, day: date | None = None) -> DailyReport:
    settings = get_settings()
    target = day or datetime.now(settings.tz).date()

    start = datetime.combine(target, time.min)
    end = datetime.combine(target, time.max)

    logs = list(
        session.scalars(
            select(CallLog)
            .where(CallLog.created_at >= start)
            .where(CallLog.created_at <= end)
        )
    )

    report = DailyReport(day=target, total_calls=len(logs))
    for log in logs:
        match log.outcome:
            case CallOutcome.no_answer:
                report.no_answer += 1
            case CallOutcome.rejected:
                report.rejected += 1
                report.reject_reasons[log.reject_reason or "без причины"] += 1
            case CallOutcome.reserve:
                report.reserved += 1
            case CallOutcome.interview_scheduled:
                report.interviews_scheduled += 1
            case CallOutcome.interview_passed:
                report.interviews_passed += 1
            case CallOutcome.hired:
                report.hired += 1

    report.reached = report.total_calls - report.no_answer
    return report
