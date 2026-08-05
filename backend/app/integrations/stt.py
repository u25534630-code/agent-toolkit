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


class SpeechModelUnavailable(RuntimeError):
    """Модель не загрузилась — расшифровать нечем.

    Отдельный тип, чтобы бот сказал человеку, что именно случилось: «не смог
    расшифровать» одинаково звучит и при неразборчивой записи, и при
    отсутствии интернета, а чинится это совершенно по-разному.
    """


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
                try:
                    self._model = await asyncio.to_thread(
                        WhisperModel,
                        self._model_name,
                        device=self._device,
                        compute_type=compute_type,
                    )
                except Exception as error:
                    logger.error(
                        "Модель «%s» не загрузилась: %s", self._model_name, error
                    )
                    logger.debug("Подробности", exc_info=True)
                    raise SpeechModelUnavailable(self._explain(error)) from error
        return self._model

    def _explain(self, error: Exception) -> str:
        """Короткая причина для сообщения в Телеграм."""
        text = f"{type(error).__name__}: {error}".lower()
        network = (
            "getaddrinfo", "name or service", "connection", "timed out",
            "max retries", "ssl", "temporary failure", "нет доступа",
        )
        if any(marker in text for marker in network):
            return (
                "Не смог скачать модель распознавания речи — нет доступа к "
                "серверу, откуда она берётся. Напишите текстом, а модель "
                "поставим отдельно."
            )
        return (
            "Модель распознавания речи не загрузилась. Напишите текстом, "
            "пожалуйста — в окне бота написана причина."
        )

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
