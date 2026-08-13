"""Клиент Bitrix24 REST.

Подбор в этом портале ведётся **Сделками** в отдельной воронке HR, а не Лидами:
стадии «Новое резюме → Первичный созвон → Тестовое задание → Собеседование →
Стажировка → Кадровый резерв». Поэтому клиент работает с `crm.deal.*`, а
кандидат представлен парой «контакт + сделка», как в существующих карточках.

Работает через входящий вебхук — одинаково в облаке и в коробке.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings
from app.db.models import Candidate, CandidateStatus

logger = logging.getLogger(__name__)


class BitrixError(RuntimeError):
    pass


class BitrixClient:
    def __init__(self, webhook_url: str | None = None, dry_run: bool | None = None):
        settings = get_settings()
        self._base = webhook_url or settings.bitrix_webhook_url
        self._dry_run = settings.dry_run if dry_run is None else dry_run
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=30.0)
        self._userfields: set[str] | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        read_only = method.endswith((".list", ".get", ".fields", ".findbycomm"))
        if self._dry_run and not read_only:
            logger.info("DRY_RUN: пропускаю %s payload=%s", method, payload)
            return None

        response = await self._client.post(self._base + method, json=payload or {})
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise BitrixError(
                f"{method}: {data.get('error')} — {data.get('error_description')}"
            )
        return data.get("result")

    # ---------- Кандидат целиком ----------

    async def create_candidate(self, candidate: Candidate) -> tuple[int | None, int | None]:
        """Завести контакт и сделку в воронке HR.

        Возвращает (contact_id, deal_id). Если человек уже есть в портале,
        переиспользуем его контакт — 615 карточек в «Новом резюме» намекают,
        что повторные отклики здесь обычное дело.
        """
        contact_id = None
        if candidate.phone:
            contact_id = await self.find_contact_by_phone(candidate.phone)
            if contact_id:
                open_deal = await self.find_open_deal(contact_id)
                if open_deal:
                    logger.info(
                        "%s уже в работе, сделка #%s", candidate.full_name, open_deal
                    )
                    return contact_id, open_deal

        if contact_id is None:
            contact_id = await self.create_contact(candidate)

        deal_id = await self.create_deal(candidate, contact_id)
        return contact_id, deal_id

    # ---------- Контакты ----------

    async def find_contact_by_phone(self, phone: str) -> int | None:
        result = await self._call(
            "crm.duplicate.findbycomm",
            {"entity_type": "CONTACT", "type": "PHONE", "values": [phone]},
        )
        contacts = (result or {}).get("CONTACT") or []
        return int(contacts[0]) if contacts else None

    @staticmethod
    def _source(candidate: Candidate) -> str:
        """Откуда пришёл человек — по факту, а не по умолчанию.

        Подпись «Отклик hh.ru» стояла у всех подряд, включая тех, кого
        рекрутер надиктовал после звонка. В карточке это выглядит как
        достоверный факт, хотя таковым не является.
        """
        return "Отклик hh.ru" if candidate.hh_negotiation_id else "Заведён вручную"

    async def create_contact(self, candidate: Candidate) -> int | None:
        fields: dict[str, Any] = {
            "NAME": candidate.first_name or "",
            "LAST_NAME": candidate.last_name or "",
            "SOURCE_ID": "WEB",
            "SOURCE_DESCRIPTION": self._source(candidate),
        }
        if candidate.phone:
            fields["PHONE"] = [{"VALUE": candidate.phone, "VALUE_TYPE": "MOBILE"}]

        result = await self._call(
            "crm.contact.add",
            {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}},
        )
        return int(result) if result else None

    # ---------- Сделки ----------

    async def find_open_deal(self, contact_id: int) -> int | None:
        """Незакрытая сделка этого контакта в воронке HR."""
        result = await self._call(
            "crm.deal.list",
            {
                "filter": {
                    "CONTACT_ID": contact_id,
                    "CATEGORY_ID": self._settings.bitrix_deal_category_id,
                    "CLOSED": "N",
                },
                "select": ["ID"],
                "order": {"ID": "DESC"},
            },
        )
        return int(result[0]["ID"]) if result else None

    async def create_deal(
        self, candidate: Candidate, contact_id: int | None = None
    ) -> int | None:
        fields = await self.fields_for(candidate)
        fields["TITLE"] = self._deal_title(candidate)
        fields["CATEGORY_ID"] = self._settings.bitrix_deal_category_id
        fields["STAGE_ID"] = self.stage_id(CandidateStatus.new)
        fields["SOURCE_ID"] = "WEB"
        fields["SOURCE_DESCRIPTION"] = self._source(candidate)
        summary = self._summary(candidate)
        if summary:
            fields["COMMENTS"] = summary
        if contact_id:
            fields["CONTACT_ID"] = contact_id

        result = await self._call(
            "crm.deal.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}}
        )
        if result:
            logger.info("Создана сделка #%s для %s", result, candidate.full_name)
        return int(result) if result else None

    async def update_deal(self, deal_id: int, fields: dict[str, Any]) -> None:
        await self._call(
            "crm.deal.update",
            {"id": deal_id, "fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}},
        )

    async def set_stage(
        self,
        deal_id: int,
        status: CandidateStatus,
        reject_reason: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Перевести сделку на стадию, соответствующую нашему статусу."""
        fields: dict[str, Any] = {"STAGE_ID": self.stage_id(status)}

        if status is CandidateStatus.rejected and reject_reason:
            fields[self._settings.bitrix_uf_reject_reason] = reject_reason
        if comment:
            fields["COMMENTS"] = comment

        await self.update_deal(deal_id, fields)
        logger.info("Сделка #%s -> %s", deal_id, fields["STAGE_ID"])

    async def add_interview_activity(
        self, deal_id: int, candidate_name: str, when: datetime
    ) -> None:
        """Дело в карточке сделки — чтобы собеседование было видно и в Битриксе."""
        await self._call(
            "crm.activity.add",
            {
                "fields": {
                    "OWNER_TYPE_ID": 2,  # 2 = сделка
                    "OWNER_ID": deal_id,
                    "TYPE_ID": 2,  # 2 = встреча
                    "SUBJECT": f"Собеседование: {candidate_name}",
                    "START_TIME": when.isoformat(),
                    "END_TIME": when.isoformat(),
                    "COMPLETED": "N",
                    "DIRECTION": 2,
                    "RESPONSIBLE_ID": 1,
                }
            },
        )

    # ---------- Служебное ----------

    async def list_deal_categories(self) -> list[dict[str, Any]]:
        """Воронки сделок — чтобы найти номер воронки HR."""
        return await self._call("crm.dealcategory.list", {}) or []

    async def list_deal_stages(self, category_id: int) -> list[dict[str, Any]]:
        return (
            await self._call(
                "crm.status.list",
                {"filter": {"ENTITY_ID": f"DEAL_STAGE_{category_id}"}},
            )
            or []
        )

    async def known_userfields(self) -> set[str]:
        """Коды пользовательских полей, которые в портале действительно есть.

        Писать в несуществующее поле бесполезно: Битрикс молча его проглотит,
        а человек видит «не заполнено» и не понимает почему. Список читаем
        один раз за запуск.
        """
        if self._userfields is None:
            try:
                self._userfields = {
                    str(field.get("FIELD_NAME")) for field in await self.list_userfields()
                }
            except Exception:  # noqa: BLE001 — без списка просто не фильтруем
                logger.warning("Не смог прочитать список полей сделки")
                self._userfields = set()
        return self._userfields

    async def fields_for(self, candidate: Candidate) -> dict[str, Any]:
        """Поля кандидата, оставив только существующие в портале."""
        known = await self.known_userfields()
        fields = self._deal_fields(candidate)
        if not known:
            return fields
        return {
            code: value
            for code, value in fields.items()
            if not code.startswith("UF_") or code in known
        }

    async def list_userfields(self) -> list[dict[str, Any]]:
        return await self._call("crm.deal.userfield.list", {}) or []

    async def create_userfield(
        self, field_name: str, label: str, field_type: str = "string"
    ) -> Any:
        return await self._call(
            "crm.deal.userfield.add",
            {
                "fields": {
                    "FIELD_NAME": field_name.removeprefix("UF_CRM_"),
                    "USER_TYPE_ID": field_type,
                    "EDIT_FORM_LABEL": {"ru": label},
                    "LIST_COLUMN_LABEL": {"ru": label},
                }
            },
        )

    # ---------- Внутреннее ----------

    def stage_id(self, status: CandidateStatus) -> str:
        """Полный код стадии вида C7:EXECUTING.

        В первой воронке Битрикс хранит стадии без префикса, в остальных —
        с `C<номер>:`. Учитываем оба случая.
        """
        s = self._settings
        code = {
            CandidateStatus.new: s.bitrix_stage_new,
            CandidateStatus.called: s.bitrix_stage_called,
            CandidateStatus.no_answer: s.bitrix_stage_called,
            CandidateStatus.test_task: s.bitrix_stage_test_task,
            CandidateStatus.interview_scheduled: s.bitrix_stage_interview,
            CandidateStatus.interview_passed: s.bitrix_stage_intern,
            CandidateStatus.reserve: s.bitrix_stage_reserve,
            CandidateStatus.hired: s.bitrix_stage_hired,
            CandidateStatus.rejected: s.bitrix_stage_rejected,
        }[status]

        if ":" in code or not s.bitrix_deal_category_id:
            return code
        return f"C{s.bitrix_deal_category_id}:{code}"

    @staticmethod
    def _deal_title(candidate: Candidate) -> str:
        parts = [candidate.full_name]
        if candidate.vacancy_title:
            parts.append(f"— {candidate.vacancy_title}")
        return " ".join(parts)

    @staticmethod
    def _summary(candidate: Candidate) -> str:
        """Что видно в карточке без настройки пользовательских полей.

        Коды полей вроде «Ссылка на резюме» в каждом портале свои, и пока
        они не сопоставлены, ссылка просто некуда не попадает — а открывать
        резюме через сайт hh.ru ради каждого отклика неудобно. Комментарий
        сделки есть везде.
        """
        rows = [
            ("Телефон", candidate.phone),
            ("Город", candidate.city),
            ("Возраст", candidate.age),
            ("Опыт, лет", candidate.experience_years),
            ("Ожидания", candidate.salary_expectation),
            ("Вакансия", candidate.vacancy_title),
            ("Резюме", candidate.resume_url),
        ]
        return "\n".join(f"{label}: {value}" for label, value in rows if value)

    def _deal_fields(self, candidate: Candidate) -> dict[str, Any]:
        s = self._settings
        optional = {
            s.bitrix_uf_resume_url: candidate.resume_url,
            s.bitrix_uf_age: candidate.age,
            s.bitrix_uf_city: candidate.city,
            s.bitrix_uf_experience: candidate.experience_years,
            s.bitrix_uf_salary: candidate.salary_expectation,
            s.bitrix_uf_vacancy: candidate.vacancy_title,
        }
        return {k: v for k, v in optional.items() if v is not None}
