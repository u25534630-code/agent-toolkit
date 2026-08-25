#!/usr/bin/env python3
"""
Общий клиент Replicate для генерации картинок скиллом.

Модель одна — openai/gpt-image-2. Она закрывает и обложки-мокапы, и
инфографику внутрь PDF: чисто рисует кириллицу, держит точный текст в
блоках и не путает подписи.

Здесь только транспорт: поиск токена, создание prediction, опрос статуса,
скачивание файла и разбор metrics. Промпты живут в вызывающих скриптах.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

MODEL = "openai/gpt-image-2"
POST_URL = f"https://api.replicate.com/v1/models/{MODEL}/predictions"
GET_URL = "https://api.replicate.com/v1/predictions/{id}"

# quality=medium — рабочее значение, а не экономия.
# low мылит кириллицу, high стоит втрое дороже и на схемах и обложках
# разницы не даёт. Поднимать до high имеет смысл только когда один и тот же
# визуал трижды не вышел на medium.
DEFAULT_QUALITY = "medium"
QUALITY_CHOICES = ["low", "medium", "high"]

# Других значений модель не принимает.
ASPECT_CHOICES = ["3:2", "2:3", "1:1"]

ENV_VAR = "REPLICATE_API_TOKEN"


@dataclass
class ImageJob:
    """Одна картинка к генерации."""
    name: str
    prompt: str
    dest: Path
    quality: str = DEFAULT_QUALITY
    aspect: str = "3:2"


@dataclass
class ImageResult:
    name: str
    ok: bool
    dest: Path | None = None
    error: str = ""
    metrics: dict = field(default_factory=dict)

    def report_line(self) -> str:
        """Строка для отчёта: чем и в каком качестве сделан файл."""
        if not self.ok:
            return f"{self.name} — ошибка: {self.error}"
        variant = self.metrics.get("model_variant", "?")
        target = self.metrics.get("resolution_target", "?")
        seconds = self.metrics.get("predict_time")
        tail = f", {seconds:.0f} с" if isinstance(seconds, (int, float)) else ""
        return f"{self.dest.name} — {MODEL}, {variant}, {target}{tail}"


def load_token(start_dir: Path | None = None) -> str:
    """Найти REPLICATE_API_TOKEN: сначала окружение, затем .env вверх по дереву.

    Никаких путей, привязанных к конкретной машине: ищем .env от указанной
    папки вверх до папки с .git включительно.
    """
    token = os.environ.get(ENV_VAR)
    if token:
        return token.strip()

    base = Path(start_dir or Path.cwd()).resolve()
    for directory in [base, *base.parents]:
        env_file = directory / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{ENV_VAR}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        if (directory / ".git").exists():
            break

    sys.exit(
        f"❌ Не найден {ENV_VAR}.\n"
        f"   Задай переменную окружения или положи строку {ENV_VAR}=... "
        f"в .env в корне рабочего репозитория.\n"
        f"   Токен берётся на replicate.com/account/api-tokens."
    )


def generate_one(token: str, job: ImageJob, timeout: int = 300,
                 poll_seconds: int = 3) -> ImageResult:
    """Сгенерировать одну картинку и сохранить в job.dest."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "input": {
            "prompt": job.prompt,
            "quality": job.quality,
            "aspect_ratio": job.aspect,
        }
    }

    try:
        response = requests.post(
            POST_URL, headers=headers, data=json.dumps(payload), timeout=30
        )
        if response.status_code >= 400:
            return ImageResult(
                job.name, False,
                error=f"POST {response.status_code}: {response.text[:200]}",
            )

        prediction_id = response.json().get("id")
        if not prediction_id:
            return ImageResult(job.name, False, error="Replicate не вернул id prediction")

        deadline = time.time() + timeout
        status_url = GET_URL.format(id=prediction_id)
        while time.time() < deadline:
            time.sleep(poll_seconds)
            data = requests.get(
                status_url, headers={"Authorization": f"Bearer {token}"}, timeout=30
            ).json()
            status = data.get("status")

            if status == "succeeded":
                output = data.get("output")
                image_url = output[0] if isinstance(output, list) else output
                if not image_url:
                    return ImageResult(job.name, False, error="пустой output")
                image = requests.get(image_url, timeout=120)
                job.dest.parent.mkdir(parents=True, exist_ok=True)
                job.dest.write_bytes(image.content)
                return ImageResult(
                    job.name, True, dest=job.dest, metrics=data.get("metrics") or {}
                )

            if status in ("failed", "canceled"):
                return ImageResult(job.name, False, error=f"{status}: {data.get('error')}")

        return ImageResult(job.name, False, error=f"таймаут {timeout} с")

    except Exception as exc:  # noqa: BLE001 — сетевые сбои не должны ронять весь пакет
        return ImageResult(job.name, False, error=repr(exc))


def generate_many(token: str, jobs: list[ImageJob], max_workers: int = 4,
                  timeout: int = 300) -> list[ImageResult]:
    """Сгенерировать пакет картинок параллельно, сохраняя порядок jobs в отчёте."""
    if not jobs:
        return []

    results: dict[str, ImageResult] = {}
    workers = max(1, min(max_workers, len(jobs)))
    with cf.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(generate_one, token, job, timeout): job.name for job in jobs
        }
        for future in cf.as_completed(futures):
            result = future.result()
            results[result.name] = result
            print(("✅ " if result.ok else "❌ ") + result.report_line(), flush=True)

    return [results[job.name] for job in jobs if job.name in results]


def add_common_arguments(parser) -> None:
    """Единые флаги качества и пропорций для всех скриптов генерации."""
    parser.add_argument(
        "--quality",
        default=DEFAULT_QUALITY,
        choices=QUALITY_CHOICES,
        help="Качество gpt-image-2. По умолчанию medium — см. references/visuals-image2.md",
    )
    parser.add_argument(
        "--aspect-ratio",
        default="3:2",
        choices=ASPECT_CHOICES,
        dest="aspect",
        help="Пропорции картинки",
    )
