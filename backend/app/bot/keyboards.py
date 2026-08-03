"""Клавиатуры бота."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import Candidate


def confirm(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, выполнить", callback_data=f"ok:{token}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"no:{token}"),
            ]
        ]
    )


def choose_candidate(token: str, candidates: list[Candidate]) -> InlineKeyboardMarkup:
    """Когда под описание подходит несколько человек — спрашиваем, а не гадаем."""
    rows = [
        [
            InlineKeyboardButton(
                text=_option_label(candidate),
                callback_data=f"pick:{token}:{candidate.id}",
            )
        ]
        for candidate in candidates[:8]
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"no:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _option_label(candidate: Candidate) -> str:
    parts = [candidate.short_name]
    if candidate.vacancy_title:
        parts.append(candidate.vacancy_title)
    if candidate.city:
        parts.append(candidate.city)
    return " · ".join(parts)[:60]
