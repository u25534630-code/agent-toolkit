"""Распознавание голосовых сообщений.

faster-whisper локально: голоса кандидатов и рекрутера никуда не уходят, платить
за минуты не нужно. Модель грузится один раз при первом вызове — старт занимает
несколько секунд, дальше расшифровка идёт в пуле потоков, чтобы не блокировать
цикл событий бота.

Фамилии активных кандидатов подставляются в initial_prompt: русские фамилии —
слабое место распознавания, а подсказка заметно поднимает точность.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class Transcriber:
    def __init__(self) -> None:
        settings = get_settings()
        self._model_name = settings.whisper_model
        self._device = settings.whisper_device
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure_model(self):
        if self._model is not None:
            return self._model

        async with self._lock:
            if self._model is None:  # мог загрузиться, пока ждали блокировку
                from faster_whisper import WhisperModel

                logger.info("Гружу whisper «%s» на %s", self._model_name, self._device)
                compute_type = "int8" if self._device == "cpu" else "float16"
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self._model_name,
                    device=self._device,
                    compute_type=compute_type,
                )
        return self._model

    async def transcribe(
        self, audio_path: Path, hint_names: list[str] | None = None
    ) -> str:
        model = await self._ensure_model()

        initial_prompt = None
        if hint_names:
            initial_prompt = "Фамилии кандидатов: " + ", ".join(hint_names[:40]) + "."

        def _run() -> str:
            segments, _info = model.transcribe(
                str(audio_path),
                language="ru",
                initial_prompt=initial_prompt,
                vad_filter=True,
                beam_size=5,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()

        text = await asyncio.to_thread(_run)
        logger.info("Расшифровано: %s", text[:200])
        return text
