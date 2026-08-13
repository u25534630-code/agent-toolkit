"""Клиент hh.ru API.

Поток — отклики на свои вакансии: контакты кандидата приходят вместе с
откликом, платный доступ к базе резюме не нужен.

Токен работодателя живёт две недели; при 403 клиент сам обновляет его по
refresh_token и повторяет запрос один раз.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.hh.ru"
TOKEN_URL = "https://api.hh.ru/token"
USER_AGENT = "recruiter-bot/1.0 (bitrix-hh-integration)"


@dataclass(slots=True)
class HHCandidate:
    """Нормализованный кандидат из отклика. Слой между hh.ru и нашей моделью."""

    negotiation_id: str
    resume_id: str | None
    vacancy_id: str | None
    vacancy_title: str | None
    full_name: str
    first_name: str | None
    last_name: str | None
    phone: str | None
    age: int | None
    city: str | None
    experience_years: float | None
    salary_expectation: str | None
    resume_url: str | None


class HHError(RuntimeError):
    pass


class HHClient:
    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._access_token = s.hh_access_token
        self._refresh_token = s.hh_refresh_token
        self._client = httpx.AsyncClient(base_url=API_BASE, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(path, params=params, headers=self._headers())

        if response.status_code in (401, 403) and self._refresh_token:
            logger.info("Токен hh.ru отклонён, обновляю")
            await self._refresh()
            response = await self._client.get(
                path, params=params, headers=self._headers()
            )

        if response.status_code >= 400:
            raise HHError(f"GET {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    async def _send(self, method: str, url: str) -> Any:
        """Выполнить действие по адресу, который выдал сам hh.ru."""
        request = self._client.build_request(method, url, headers=self._headers())
        response = await self._client.send(request)

        if response.status_code in (401, 403) and self._refresh_token:
            logger.info("Токен hh.ru отклонён, обновляю")
            await self._refresh()
            request = self._client.build_request(method, url, headers=self._headers())
            response = await self._client.send(request)

        if response.status_code >= 400:
            raise HHError(
                f"{method} {url} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else None

    # ---------- Отказ ----------

    async def discard(self, negotiation_id: str) -> str | None:
        """Отправить отказ по отклику. Возвращает название действия или None.

        Адрес не зашит: hh.ru отдаёт по каждому отклику список доступных
        действий с готовыми url и method и требует пользоваться ими —
        правила у разных вакансий и работодателей различаются. Отказ живому
        человеку — не то место, где можно угадывать путь.
        """
        negotiation = await self._get(f"/negotiations/{negotiation_id}")
        actions = negotiation.get("actions") or []

        for action in actions:
            name = str(action.get("id") or action.get("name") or "").lower()
            if "discard" not in name:
                continue
            url = action.get("url")
            method = str(action.get("method") or "PUT").upper()
            if not url:
                continue
            await self._send(method, url)
            logger.info("Отказ по отклику %s отправлен (%s)", negotiation_id, name)
            return str(action.get("name") or name)

        available = ", ".join(
            str(a.get("id") or a.get("name")) for a in actions
        ) or "нет ни одного"
        logger.warning(
            "По отклику %s нет действия «отказ». Доступные действия: %s",
            negotiation_id,
            available,
        )
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": USER_AGENT,
        }

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._settings.hh_client_id,
                    "client_secret": self._settings.hh_client_secret,
                },
                headers={"User-Agent": USER_AGENT},
            )
        if response.status_code >= 400:
            raise HHError(
                "Не удалось обновить токен hh.ru — нужна повторная авторизация "
                f"работодателя: {response.text[:300]}"
            )
        data = response.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        logger.warning(
            "Токен hh.ru обновлён. Впишите новые значения в .env, иначе после "
            "перезапуска понадобится ещё одно обновление."
        )

    # ---------- Отклики ----------

    async def list_active_vacancies(self) -> list[dict[str, Any]]:
        """Опубликованные вакансии работодателя.

        Отклики hh.ru отдаёт только в рамках конкретной вакансии — в их
        документации это сказано прямо. Запрос «дай все отклики» без вакансии
        не работает, поэтому сначала узнаём, по чему спрашивать.
        """
        employer_id = self._settings.hh_employer_id
        page = {"per_page": 100, "page": 0}

        # У разных аккаунтов доступны разные адреса: где-то нет прав на
        # менеджерский список, где-то метод просто отсутствует. Последний
        # вариант — обычный поиск вакансий по работодателю: он публичный
        # и работает всегда.
        attempts: list[tuple[str, dict[str, Any]]] = []
        if employer_id:
            attempts.append((f"/employers/{employer_id}/vacancies/active", page))
        attempts.append(("/vacancies/active", page))
        if employer_id:
            attempts.append(("/vacancies", {**page, "employer_id": employer_id}))

        last_error: Exception | None = None
        for path, params in attempts:
            try:
                data = await self._get(path, params)
            except HHError as error:
                # Молча перебирать адреса нельзя: когда не подойдёт ни один,
                # в журнале должно остаться, что именно ответил hh.ru
                logger.warning("Вакансии: %s не подошёл — %s", path, str(error)[:200])
                last_error = error
                continue
            items = data.get("items", [])
            logger.info("Активных вакансий: %d (%s)", len(items), path)
            return items

        if last_error:
            raise last_error
        return []

    async def fetch_new_responses(self, per_page: int = 50) -> list[HHCandidate]:
        """Непросмотренные отклики по вакансиям работодателя."""
        collected: list[HHCandidate] = []
        vacancy_ids: list[str | None] = list(self._settings.hh_vacancy_ids)

        if not vacancy_ids:
            vacancy_ids = [
                str(v.get("id"))
                for v in await self.list_active_vacancies()
                if v.get("id")
            ]
        if not vacancy_ids:
            logger.warning(
                "У работодателя нет активных вакансий — откликам взяться неоткуда"
            )
            return []

        for vacancy_id in vacancy_ids:
            params: dict[str, Any] = {"per_page": per_page, "page": 0, "order_by": "created_at"}
            if vacancy_id:
                params["vacancy_id"] = vacancy_id

            data = await self._get("/negotiations/response", params)
            for item in self._recent_enough(data.get("items", [])):
                try:
                    collected.append(await self._normalize(item))
                except Exception:  # один битый отклик не должен рушить поллинг
                    logger.exception(
                        "Не смог разобрать отклик %s", item.get("id", "<без id>")
                    )

        return collected

    def _recent_enough(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Отсечь отклики старше заданного срока.

        При подключении к работающему аккаунту в списке лежит всё, что
        накопилось за месяцы. Заводить их в CRM задним числом обычно не нужно.
        """
        days = self._settings.hh_skip_older_than_days
        if not days:
            return items

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        fresh = []
        for item in items:
            created = item.get("created_at")
            if not created:
                fresh.append(item)
                continue
            try:
                when = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            except ValueError:
                fresh.append(item)
                continue
            if when >= cutoff:
                fresh.append(item)
        skipped = len(items) - len(fresh)
        if skipped:
            logger.info("Пропустил %d откликов старше %d дн.", skipped, days)
        return fresh

    async def _normalize(self, negotiation: dict[str, Any]) -> HHCandidate:
        resume = negotiation.get("resume") or {}
        resume_id = resume.get("id")

        # В списке откликов резюме приходит урезанным — контакты только в полном
        if resume_id:
            try:
                resume = await self._get(f"/resumes/{resume_id}")
            except HHError:
                logger.warning("Полное резюме %s недоступно, беру краткое", resume_id)

        vacancy = negotiation.get("vacancy") or {}

        return HHCandidate(
            negotiation_id=str(negotiation["id"]),
            resume_id=resume_id,
            vacancy_id=str(vacancy.get("id")) if vacancy.get("id") else None,
            vacancy_title=vacancy.get("name"),
            full_name=self._full_name(resume),
            first_name=resume.get("first_name"),
            last_name=resume.get("last_name"),
            phone=self._phone(resume),
            age=resume.get("age"),
            city=(resume.get("area") or {}).get("name"),
            experience_years=self._experience_years(resume),
            salary_expectation=self._salary(resume),
            resume_url=resume.get("alternate_url"),
        )

    @staticmethod
    def _full_name(resume: dict[str, Any]) -> str:
        parts = [
            resume.get("last_name"),
            resume.get("first_name"),
            resume.get("middle_name"),
        ]
        name = " ".join(p for p in parts if p)
        return name or "Без имени"

    @staticmethod
    def _phone(resume: dict[str, Any]) -> str | None:
        for contact in resume.get("contact") or []:
            value = contact.get("value")
            if isinstance(value, dict) and value.get("formatted"):
                return value["formatted"]
            if isinstance(value, str) and any(ch.isdigit() for ch in value):
                return value
        return None

    @staticmethod
    def _experience_years(resume: dict[str, Any]) -> float | None:
        months = (resume.get("total_experience") or {}).get("months")
        return round(months / 12, 1) if months else None

    @staticmethod
    def _salary(resume: dict[str, Any]) -> str | None:
        salary = resume.get("salary")
        if not salary or salary.get("amount") is None:
            return None
        return f"{salary['amount']:,}".replace(",", " ") + f" {salary.get('currency', '')}".rstrip()
