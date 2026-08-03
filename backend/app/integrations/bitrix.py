"""Клиент Bitrix24 REST.

Работает через входящий вебхук — одинаково в облаке и в коробке. Портал
`bitrix.lovekuhnya.online` — коробочный, но для REST это не имеет значения:
вебхук создаётся в обеих редакциях, метод и формат ответа те же.

Если позже понадобится OAuth-приложение, меняется только `_call` — остальной
код о способе авторизации не знает.
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

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if self._dry_run and not method.endswith((".list", ".get", ".fields")):
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

    # ---------- Лиды ----------

    async def create_lead(self, candidate: Candidate) -> int | None:
        fields = self._lead_fields(candidate)
        fields["TITLE"] = self._lead_title(candidate)
        fields["STATUS_ID"] = self._settings.bitrix_status_new
        fields["SOURCE_ID"] = "WEB"
        fields["SOURCE_DESCRIPTION"] = "Отклик hh.ru"

        if candidate.phone:
            fields["PHONE"] = [{"VALUE": candidate.phone, "VALUE_TYPE": "MOBILE"}]

        result = await self._call(
            "crm.lead.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}}
        )
        if result:
            logger.info("Создан лид #%s для %s", result, candidate.full_name)
        return int(result) if result else None

    async def update_lead(self, lead_id: int, fields: dict[str, Any]) -> None:
        await self._call(
            "crm.lead.update",
            {"id": lead_id, "fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}},
        )

    async def set_status(
        self,
        lead_id: int,
        status: CandidateStatus,
        reject_reason: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Перевести лид в стадию, соответствующую нашему статусу."""
        fields: dict[str, Any] = {"STATUS_ID": self._status_code(status)}

        if status is CandidateStatus.rejected and reject_reason:
            fields[self._settings.bitrix_uf_reject_reason] = reject_reason
            # Битрикс показывает причину закрытия отдельным полем на закрытых стадиях
            fields["STATUS_DESCRIPTION"] = reject_reason

        if comment:
            fields["COMMENTS"] = comment

        await self.update_lead(lead_id, fields)
        logger.info("Лид #%s -> %s", lead_id, fields["STATUS_ID"])

    async def find_lead_by_phone(self, phone: str) -> int | None:
        """Поиск дубля перед созданием лида."""
        result = await self._call(
            "crm.duplicate.findbycomm",
            {"entity_type": "LEAD", "type": "PHONE", "values": [phone]},
        )
        leads = (result or {}).get("LEAD") or []
        return int(leads[0]) if leads else None

    async def add_interview_activity(
        self, lead_id: int, candidate_name: str, when: datetime
    ) -> None:
        """Дело в карточке лида — чтобы собеседование было видно и в Битриксе."""
        await self._call(
            "crm.activity.add",
            {
                "fields": {
                    "OWNER_TYPE_ID": 1,  # 1 = лид
                    "OWNER_ID": lead_id,
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

    async def list_lead_statuses(self) -> list[dict[str, Any]]:
        """Коды стадий воронки лидов — для заполнения .env."""
        return await self._call("crm.status.list", {"filter": {"ENTITY_ID": "STATUS"}}) or []

    async def list_userfields(self) -> list[dict[str, Any]]:
        return await self._call("crm.lead.userfield.list", {}) or []

    async def create_userfield(
        self, field_name: str, label: str, field_type: str = "string"
    ) -> Any:
        return await self._call(
            "crm.lead.userfield.add",
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

    def _status_code(self, status: CandidateStatus) -> str:
        s = self._settings
        return {
            CandidateStatus.new: s.bitrix_status_new,
            CandidateStatus.called: s.bitrix_status_in_process,
            CandidateStatus.no_answer: s.bitrix_status_in_process,
            CandidateStatus.rejected: s.bitrix_status_rejected,
            CandidateStatus.interview_scheduled: s.bitrix_status_interview,
            CandidateStatus.interview_passed: s.bitrix_status_intern,
            CandidateStatus.hired: s.bitrix_status_hired,
        }[status]

    def _lead_title(self, candidate: Candidate) -> str:
        parts = [candidate.full_name]
        if candidate.vacancy_title:
            parts.append(f"— {candidate.vacancy_title}")
        return " ".join(parts)

    def _lead_fields(self, candidate: Candidate) -> dict[str, Any]:
        s = self._settings
        fields: dict[str, Any] = {
            "NAME": candidate.first_name or "",
            "LAST_NAME": candidate.last_name or "",
        }
        optional = {
            s.bitrix_uf_resume_url: candidate.resume_url,
            s.bitrix_uf_age: candidate.age,
            s.bitrix_uf_city: candidate.city,
            s.bitrix_uf_experience: candidate.experience_years,
            s.bitrix_uf_salary: candidate.salary_expectation,
            s.bitrix_uf_vacancy: candidate.vacancy_title,
        }
        fields.update({k: v for k, v in optional.items() if v is not None})
        return fields
