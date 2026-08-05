"""Обработчики Telegram: голос, текст, кнопки, команды."""

from __future__ import annotations

import json
import logging
import secrets
import tempfile
from datetime import date, timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command as BotCommand
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot import keyboards
from app.db.models import Candidate, CandidateStatus, PendingAction
from app.db.session import session_scope
from app.deps import build_context
from app.integrations.claude import Command
from app.integrations.stt import SpeechModelUnavailable
from app.services.candidates import RecruitingService, describe_command
from app.services.reports import build_daily_report

logger = logging.getLogger(__name__)
router = Router()


def _allowed(user_id: int | None) -> bool:
    allowed = build_context().settings.telegram_allowed_user_ids
    if not allowed or user_id in allowed:
        return True
    # Молча игнорировать чужих правильно, но в журнале это должно быть видно:
    # иначе опечатка в своём же id выглядит как «бот сломался»
    logger.warning(
        "Сообщение от %s пропущено: этого id нет в TELEGRAM_ALLOWED_USER_IDS=%s",
        user_id,
        allowed,
    )
    return False


@router.message(BotCommand("start"))
async def cmd_start(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        "Готов к работе.\n\n"
        "После звонка просто скажите или напишите одной фразой:\n"
        "· «Петрова не подходит, нет опыта»\n"
        "· «Сидоров, собес в четверг в 15:00»\n"
        "· «Иванова прошла собеседование»\n"
        "· «Кузнецов не берёт трубку»\n\n"
        "Я покажу, что понял, и спрошу подтверждение.\n\n"
        "Команды: /today — сводка за сегодня, /active — кто в работе."
    )


@router.message(BotCommand("today"))
async def cmd_today(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return
    with session_scope() as session:
        report = build_daily_report(session)
    await message.answer(report.render(), parse_mode="HTML")


@router.message(BotCommand("yesterday"))
async def cmd_yesterday(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return
    with session_scope() as session:
        report = build_daily_report(session, date.today() - timedelta(days=1))
    await message.answer(report.render(), parse_mode="HTML")


@router.message(BotCommand("active"))
async def cmd_active(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return

    settings = build_context().settings
    with session_scope() as session:
        candidates = list(
            session.scalars(
                select(Candidate)
                .where(
                    Candidate.status.in_(
                        [
                            CandidateStatus.new,
                            CandidateStatus.called,
                            CandidateStatus.no_answer,
                            CandidateStatus.interview_scheduled,
                        ]
                    )
                )
                .order_by(Candidate.updated_at.desc())
                .limit(30)
            )
        )

        if not candidates:
            await message.answer("Активных кандидатов нет.")
            return

        lines = ["<b>В работе</b>", ""]
        for candidate in candidates:
            line = f"· {candidate.short_name}"
            if candidate.vacancy_title:
                line += f" — {candidate.vacancy_title}"
            if candidate.interview_at:
                when = candidate.interview_at.astimezone(settings.tz)
                line += f" — собес {when.strftime('%d.%m %H:%M')}"
            elif candidate.status is CandidateStatus.no_answer:
                line += " — недозвон"
            elif candidate.status is CandidateStatus.new:
                line += " — новый отклик"
            lines.append(line)

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.voice | F.audio)
async def on_voice(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return

    context = build_context()
    voice = message.voice or message.audio
    notice = await message.answer("Слушаю…")

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / "voice.ogg"
        file = await message.bot.get_file(voice.file_id)
        await message.bot.download_file(file.file_path, destination=audio_path)

        with session_scope() as session:
            hints = RecruitingService.active_last_names(session)

        try:
            text = await context.transcriber.transcribe(audio_path, hint_names=hints)
        except SpeechModelUnavailable as error:
            # Причина известна и не про качество записи — говорим её прямо
            await notice.edit_text(str(error))
            return
        except Exception:
            logger.exception("Не смог расшифровать голосовое")
            await notice.edit_text(
                "Не смог расшифровать голосовое. Напишите текстом, пожалуйста."
            )
            return
    # Временный каталог удалён — аудио не хранится

    if not text:
        await notice.edit_text("Ничего не расслышал. Попробуйте ещё раз.")
        return

    await notice.edit_text(f"Расслышал: «{text}»")
    await _handle_text(message, text)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    if not _allowed(message.from_user.id if message.from_user else None):
        return
    await _handle_text(message, message.text or "")


async def _handle_text(message: Message, text: str) -> None:
    context = build_context()

    with session_scope() as session:
        hints = RecruitingService.active_last_names(session)

    command = await context.parser.parse(text, known_candidates=hints)

    if command.action == "unknown" or not command.candidate_ref:
        await message.answer(
            "Не понял, о ком и что нужно сделать.\n"
            "Скажите так: «Фамилия, что произошло». Например: "
            "«Петрова не подходит, нет опыта»."
        )
        return

    # Нового кандидата не ищем среди существующих — его ещё нет
    if command.action == "add_candidate":
        token = secrets.token_urlsafe(12)[:16]
        with session_scope() as session:
            session.add(
                PendingAction(
                    token=token,
                    chat_id=message.chat.id,
                    candidate_id=None,
                    payload_json=command.model_dump_json(),
                )
            )
        await message.answer(
            describe_command(command), reply_markup=keyboards.confirm(token)
        )
        return

    with session_scope() as session:
        candidates = RecruitingService.find_candidates(session, command.candidate_ref)

        if not candidates:
            await message.answer(
                f"Не нашёл кандидата «{command.candidate_ref}». "
                "Проверьте фамилию или посмотрите /active."
            )
            return

        token = secrets.token_urlsafe(12)[:16]
        session.add(
            PendingAction(
                token=token,
                chat_id=message.chat.id,
                candidate_id=candidates[0].id if len(candidates) == 1 else None,
                payload_json=command.model_dump_json(),
            )
        )

        if len(candidates) > 1:
            await message.answer(
                f"Под «{command.candidate_ref}» подходит несколько человек. Кто именно?",
                reply_markup=keyboards.choose_candidate(token, candidates),
            )
            return

        candidate = candidates[0]
        preview = describe_command(command, candidate)

    await message.answer(preview, reply_markup=keyboards.confirm(token))


@router.callback_query(F.data.startswith("pick:"))
async def on_pick(callback: CallbackQuery) -> None:
    _, token, candidate_id = callback.data.split(":", 2)

    with session_scope() as session:
        pending = session.scalar(
            select(PendingAction).where(PendingAction.token == token)
        )
        if pending is None or pending.resolved:
            await callback.answer("Действие уже неактуально")
            return

        pending.candidate_id = int(candidate_id)
        candidate = session.get(Candidate, int(candidate_id))
        command = Command.model_validate(json.loads(pending.payload_json))
        preview = describe_command(command, candidate)

    await callback.message.edit_text(preview, reply_markup=keyboards.confirm(token))
    await callback.answer()


@router.callback_query(F.data.startswith("no:"))
async def on_cancel(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]

    with session_scope() as session:
        pending = session.scalar(
            select(PendingAction).where(PendingAction.token == token)
        )
        if pending:
            pending.resolved = True

    await callback.message.edit_text("Отменено, ничего не менял.")
    await callback.answer()


@router.callback_query(F.data.startswith("ok:"))
async def on_confirm(callback: CallbackQuery) -> None:
    token = callback.data.split(":", 1)[1]
    context = build_context()

    with session_scope() as session:
        pending = session.scalar(
            select(PendingAction).where(PendingAction.token == token)
        )
        if pending is None or pending.resolved:
            await callback.answer("Действие уже выполнено")
            return

        command = Command.model_validate(json.loads(pending.payload_json))

        if command.action == "add_candidate":
            try:
                candidate = await context.recruiting.add_candidate(session, command)
            except Exception:
                logger.exception("Не смог завести кандидата")
                await callback.message.edit_text(
                    "Не получилось завести кандидата — внешняя система не ответила."
                )
                await callback.answer()
                return

            pending.resolved = True
            result = f"Завёл {candidate.short_name}. Дальше отчитывайтесь как обычно."
            await callback.message.edit_text(result)
            await callback.answer("Готово")
            return

        candidate = session.get(Candidate, pending.candidate_id)
        if candidate is None:
            await callback.message.edit_text("Кандидат не найден.")
            pending.resolved = True
            await callback.answer()
            return

        try:
            result = await context.recruiting.apply(
                session, candidate, command, pending.chat_id
            )
        except Exception:
            logger.exception("Не смог применить команду %s", command.action)
            await callback.message.edit_text(
                "Не получилось выполнить — внешняя система не ответила. "
                "Данные не изменены, попробуйте ещё раз."
            )
            await callback.answer()
            return

        pending.resolved = True

    await callback.message.edit_text(result)
    await callback.answer("Готово")
