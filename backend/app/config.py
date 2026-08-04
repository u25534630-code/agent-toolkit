"""Конфигурация из .env. Единственное место, где читается окружение."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    telegram_bot_token: str
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-opus-5"

    # Bitrix24. Пусто — работаем без CRM: отклики, таблица, напоминания и
    # отчёты не зависят от Битрикса. Вписать вебхук можно в любой момент.
    bitrix_webhook_url: str = ""

    bitrix_status_new: str = "NEW"
    bitrix_status_in_process: str = "IN_PROCESS"
    bitrix_status_interview: str = "UC_INTERVIEW"
    bitrix_status_intern: str = "UC_INTERN"
    bitrix_status_hired: str = "CONVERTED"
    bitrix_status_rejected: str = "JUNK"

    bitrix_uf_resume_url: str = "UF_CRM_RESUME_URL"
    bitrix_uf_age: str = "UF_CRM_AGE"
    bitrix_uf_city: str = "UF_CRM_CITY"
    bitrix_uf_experience: str = "UF_CRM_EXPERIENCE"
    bitrix_uf_salary: str = "UF_CRM_SALARY"
    bitrix_uf_reject_reason: str = "UF_CRM_REJECT_REASON"
    bitrix_uf_vacancy: str = "UF_CRM_VACANCY"

    # hh.ru
    hh_client_id: str = ""
    hh_client_secret: str = ""
    hh_access_token: str = ""
    hh_refresh_token: str = ""
    hh_employer_id: str = ""
    hh_vacancy_ids: list[str] = Field(default_factory=list)
    hh_poll_interval_minutes: int = 15

    # Google Sheets
    google_credentials_file: str = "./service-account.json"
    google_spreadsheet_id: str = ""
    # Названия листов берутся как есть — в таблице «Стажеры» с заглавной
    sheet_tracking_name: str = "отслеживание проходящих"
    sheet_interns_name: str = "Стажеры"

    # Прочее
    database_url: str = "sqlite:///./recruiter.db"
    timezone: str = "Europe/Moscow"
    daily_report_time: str = "19:00"
    interview_confirm_hours_before: int = 24
    interview_reminder_hours_before: int = 1
    whisper_model: str = "medium"
    whisper_device: str = "cpu"
    dry_run: bool = False
    log_level: str = "INFO"

    @field_validator("bitrix_webhook_url")
    @classmethod
    def _webhook_trailing_slash(cls, v: str) -> str:
        # Битрикс склеивает URL с именем метода — без слэша получится /rest/1/tokencrm.lead.add
        if not v:
            return v
        return v if v.endswith("/") else v + "/"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def report_time(self) -> time:
        hour, minute = self.daily_report_time.split(":")
        return time(int(hour), int(minute))

    @property
    def bitrix_configured(self) -> bool:
        return bool(self.bitrix_webhook_url)

    @property
    def hh_configured(self) -> bool:
        return bool(self.hh_access_token and self.hh_employer_id)

    @property
    def sheets_configured(self) -> bool:
        return bool(self.google_spreadsheet_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # значения берутся из .env и окружения
