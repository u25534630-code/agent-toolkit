"""Разбор реплики рекрутера в структурированную команду.

Claude Opus 5 со structured outputs: модель обязана вернуть JSON по схеме,
парсить свободный текст не нужно. Effort `low` — задача простая, а латентность
важна: рекрутер ждёт ответа между звонками.

Список фамилий активных кандидатов уходит в промпт, чтобы «Петрова не подходит»
надёжно сопоставлялось даже при неидеальной расшифровке речи.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)

Action = Literal[
    "reject",
    "schedule_interview",
    "interview_passed",
    "no_answer",
    "hired",
    "note",
    "unknown",
]

COMMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "reject",
                "schedule_interview",
                "interview_passed",
                "no_answer",
                "hired",
                "note",
                "unknown",
            ],
            "description": "Что рекрутер хочет сделать",
        },
        "candidate_ref": {
            "type": ["string", "null"],
            "description": "Как рекрутер назвал кандидата: фамилия или фамилия и имя",
        },
        "reject_reason": {
            "type": ["string", "null"],
            "description": "Причина отказа, коротко и по-русски. Только для action=reject",
        },
        "interview_at": {
            "type": ["string", "null"],
            "description": (
                "Дата и время собеседования в формате ISO 8601 без таймзоны, "
                "например 2026-08-06T15:00:00. Только для action=schedule_interview"
            ),
        },
        "comment": {
            "type": ["string", "null"],
            "description": "Дополнительная заметка, если она есть в реплике",
        },
        "confidence": {
            "type": "number",
            "description": "Насколько уверенно разобрана реплика, от 0 до 1",
        },
    },
    "required": [
        "action",
        "candidate_ref",
        "reject_reason",
        "interview_at",
        "comment",
        "confidence",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
Ты разбираешь короткие реплики рекрутера о звонках кандидатам в структурированную команду.

Действия:
- reject — кандидат не подходит. Обязательно вытащи причину, если она названа.
- schedule_interview — назначено собеседование. Вытащи дату и время.
- interview_passed — кандидат прошёл собеседование, выходит на стажировку.
- no_answer — не дозвонились, недозвон, не берёт трубку.
- hired — кандидат вышел на работу, оформлен в штат.
- note — рекрутер просто оставляет заметку без смены статуса.
- unknown — из реплики непонятно, что делать.

Правила:
- candidate_ref — ровно то, как рекрутер назвал человека. Не додумывай имя,
  если названа только фамилия.
- Относительные даты («в четверг», «завтра в три», «послезавтра утром»)
  разрешай в конкретные дату и время относительно текущего момента. Если время
  не названо, ставь 10:00. Если день недели назван без уточнения — ближайший
  будущий такой день.
- reject_reason пиши коротко и единообразно: «нет опыта», «не устроила ЗП»,
  «далеко ехать», «нет прав категории C». Не переписывай реплику целиком.
- confidence ниже 0.7 ставь, когда реплика обрывочная, кандидат не назван или
  действие неоднозначно. Лучше низкая уверенность, чем неверное действие:
  при низкой уверенности бот переспросит.
- Отвечай только JSON по схеме.\
"""


class Command(BaseModel):
    action: Action
    candidate_ref: str | None = None
    reject_reason: str | None = None
    interview_at: str | None = None
    comment: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def needs_confirmation(self) -> bool:
        return self.action == "unknown" or self.confidence < 0.7

    @property
    def interview_datetime(self) -> datetime | None:
        if not self.interview_at:
            return None
        try:
            return datetime.fromisoformat(self.interview_at)
        except ValueError:
            logger.warning("Модель вернула неразбираемую дату: %s", self.interview_at)
            return None


class CommandParser:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def parse(self, text: str, known_candidates: list[str] | None = None) -> Command:
        now = datetime.now(self._settings.tz)
        context = [
            f"Текущие дата и время: {now.strftime('%Y-%m-%d %H:%M')}, "
            f"{self._weekday_ru(now)}, таймзона {self._settings.timezone}."
        ]
        if known_candidates:
            context.append(
                "Активные кандидаты (сопоставляй candidate_ref с этим списком, "
                "распознавание речи могло исказить фамилию): "
                + ", ".join(known_candidates)
            )

        response = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT + "\n\n" + "\n".join(context),
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": COMMAND_SCHEMA},
            },
            messages=[{"role": "user", "content": text}],
        )

        if response.stop_reason == "refusal":
            logger.warning("Модель отказалась разбирать реплику")
            return Command(action="unknown", confidence=0.0)

        payload = next((b.text for b in response.content if b.type == "text"), None)
        if not payload:
            return Command(action="unknown", confidence=0.0)

        try:
            return Command.model_validate(json.loads(payload))
        except Exception:
            logger.exception("Не разобрал ответ модели: %s", payload[:300])
            return Command(action="unknown", confidence=0.0)

    @staticmethod
    def _weekday_ru(value: datetime) -> str:
        return (
            "понедельник вторник среда четверг пятница суббота воскресенье".split()
        )[value.weekday()]
