"""Конфигурация из .env. Единственное место, где читается окружение."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)

# Размеры моделей faster-whisper. Вместо размера можно указать путь к папке
# с уже скачанной моделью — тогда проверку пропускаем.
WHISPER_SIZES = {
    "tiny.en", "tiny", "base.en", "base", "small.en", "small",
    "medium.en", "medium", "large-v1", "large-v2", "large-v3", "large",
    "distil-large-v2", "distil-medium.en", "distil-small.en",
    "distil-large-v3", "large-v3-turbo", "turbo",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    telegram_bot_token: str
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)

    # Anthropic
    # Пусто — команды разбираются правилами без обращения к API
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    # Bitrix24. Пусто — работаем без CRM: отклики, таблица, напоминания и
    # отчёты не зависят от Битрикса. Вписать вебхук можно в любой момент.
    bitrix_webhook_url: str = ""

    # Подбор ведётся Сделками в отдельной воронке HR. Номер воронки и коды
    # стадий смотреть через scripts/setup_bitrix.py --show-stages.
    bitrix_deal_category_id: int = 0

    # Коды стадий воронки HR. Полный код выглядит как C7:NEW — префикс
    # с номером воронки подставляется сам, здесь только часть после двоеточия.
    bitrix_stage_new: str = "NEW"  # Новое резюме
    bitrix_stage_called: str = "PREPARATION"  # Первичный созвон
    bitrix_stage_test_task: str = "PREPAYMENT_INVOICE"  # Тестовое задание
    bitrix_stage_interview: str = "EXECUTING"  # Собеседование
    bitrix_stage_intern: str = "FINAL_INVOICE"  # Стажировка
    bitrix_stage_reserve: str = "1"  # Кадровый резерв
    bitrix_stage_hired: str = "WON"  # Вышел на работу
    bitrix_stage_rejected: str = "LOSE"  # Не подходит

    bitrix_uf_resume_url: str = "UF_CRM_RESUME_URL"
    bitrix_uf_age: str = "UF_CRM_AGE"
    bitrix_uf_city: str = "UF_CRM_CITY"
    bitrix_uf_experience: str = "UF_CRM_EXPERIENCE"
    bitrix_uf_salary: str = "UF_CRM_SALARY"
    bitrix_uf_reject_reason: str = "UF_CRM_REJECT_REASON"
    bitrix_uf_vacancy: str = "UF_CRM_VACANCY"
    # «Филиал» в карточке сделки: ЕКБ, ЧЛБ, ТЮМ
    bitrix_uf_branch: str = "UF_CRM_BRANCH"

    # hh.ru
    hh_client_id: str = ""
    hh_client_secret: str = ""
    hh_access_token: str = ""
    hh_refresh_token: str = ""
    hh_employer_id: str = ""
    hh_vacancy_ids: list[str] = Field(default_factory=list)
    # Во сколько проверять отклики. Два раза в день — утром и после обеда:
    # отклики копятся часами, а не минутами, и разбирать их всё равно удобнее
    # пачкой, между обзвонами. Пусто — вернуться к опросу по интервалу ниже.
    hh_poll_times: str = "10:00,17:00"
    # Запасной режим, если hh_poll_times пуст.
    hh_poll_interval_minutes: int = 15
    # Сколько кандидатов заводить за один цикл. Первый запуск иначе вываливает
    # в CRM весь накопленный список разом, а разбирать это придётся руками.
    hh_max_new_per_poll: int = 10
    # Не трогать отклики старше стольких дней. 0 — брать все.
    hh_skip_older_than_days: int = 0
    # Отправлять ли отказ на hh.ru, когда кандидат помечен «не подходит».
    # По умолчанию нет: это сообщение живому человеку, включать осознанно.
    hh_send_rejection: bool = False
    # Текст отказа соискателю. Пусто — статус меняем, письмо не шлём.
    # {name} подставляется именем кандидата.
    hh_rejection_message: str = (
        "{name}, здравствуйте! Большое спасибо за интерес к нашей компании! "
        "К сожалению, сейчас мы не готовы пригласить вас на следующий этап. "
        "Ценим ваше внимание и будем рады взаимодействию в будущем."
    )

    # Google Sheets
    google_credentials_file: str = "./service-account.json"
    google_spreadsheet_id: str = ""
    # Названия листов берутся как есть — в таблице «Стажеры» с заглавной
    sheet_tracking_name: str = "отслеживание проходящих"
    sheet_interns_name: str = "Стажеры"
    # Колонка «С какого сайта сотрудник». В таблице hh.ru записан как «НН»
    sheet_source_label: str = "НН"

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

    @field_validator("whisper_model")
    @classmethod
    def _known_whisper_model(cls, v: str) -> str:
        """Опечатку здесь видно только при первом голосовом — слишком поздно.

        Мастер настройки задаёт вопросы подряд, ответ легко сдвинуть на один:
        так в поле модели оказалось «д» от вопроса про пробный режим. Бот при
        этом поднимался как ни в чём не бывало и падал через час, на первом
        голосовом сообщении.
        """
        value = (v or "").strip()
        if value in WHISPER_SIZES:
            return value
        # Путь к скачанной вручную модели — проверять по списку нечего
        if "/" in value or "\\" in value or Path(value).exists():
            return value
        logger.warning(
            "WHISPER_MODEL=%r — такой модели нет, беру «small». "
            "Допустимые размеры: %s",
            value,
            ", ".join(sorted(WHISPER_SIZES)),
        )
        return "small"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def report_time(self) -> time:
        hour, minute = self.daily_report_time.split(":")
        return time(int(hour), int(minute))

    @property
    def hh_poll_at(self) -> list[time]:
        """Времена опроса hh.ru из hh_poll_times.

        Непонятную запись пропускаем с предупреждением, а не роняем запуск:
        из-за опечатки во времени бот не должен переставать работать целиком.
        """
        result: list[time] = []
        for part in self.hh_poll_times.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                hour, _, minute = part.partition(":")
                result.append(time(int(hour), int(minute or 0)))
            except ValueError:
                logger.warning(
                    "HH_POLL_TIMES: «%s» — не похоже на время вида 10:00, пропускаю",
                    part,
                )
        return sorted(set(result))

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

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
