"""Google Sheets: вкладки «отслеживание проходящих» и «стажеры».

Прочитать вашу таблицу заранее не удалось (не расшарена на аккаунт, с которого
шла разработка), поэтому колонки не зашиты в код: при первом обращении читается
строка заголовков и сопоставляется с нашими полями по названию. Регистр,
лишние пробелы и часть синонимов игнорируются — см. COLUMN_ALIASES.

Если колонка не нашлась, она просто не заполняется, а бот при старте пишет
список несопоставленных полей.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import get_settings
from app.db.models import Candidate

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Наше поле -> варианты названий колонки в таблице
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("дата", "дата звонка", "дата добавления"),
    "hire_date": ("дата выхода", "дата начала", "выход"),
    "full_name": ("фио", "имя", "кандидат", "фамилия имя"),
    "phone": ("телефон", "номер", "контакт", "тел"),
    "vacancy": ("вакансия", "должность", "позиция"),
    "city": ("город", "регион"),
    "age": ("возраст", "лет"),
    "experience": ("опыт", "стаж", "опыт работы"),
    "salary": ("ожидаемая зп", "зп", "зарплата", "ожидания", "оклад"),
    "resume": ("резюме", "ссылка", "ссылка на резюме", "hh"),
    "interview_date": ("дата собеседования", "собеседование", "собес", "дата собеса"),
    "mentor": ("наставник", "куратор", "ответственный"),
    "status": ("статус", "результат", "этап"),
    "comment": ("комментарий", "примечание", "заметка"),
    "lead_id": ("лид в битриксе", "лид", "битрикс", "id лида"),
}


def _normalize(header: str) -> str:
    return re.sub(r"\s+", " ", header or "").strip().lower().replace("ё", "е")


@dataclass(slots=True)
class SheetLayout:
    """Соответствие «наше поле -> индекс колонки» для одного листа."""

    title: str
    columns: dict[str, int]
    width: int

    def row(self, values: dict[str, Any]) -> list[Any]:
        """Собрать строку нужной ширины, разложив значения по их колонкам."""
        row: list[Any] = [""] * self.width
        for field, value in values.items():
            index = self.columns.get(field)
            if index is not None and value is not None:
                row[index] = value
        return row

    @property
    def missing(self) -> list[str]:
        return [f for f in COLUMN_ALIASES if f not in self.columns]


class SheetsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._spreadsheet_id = settings.google_spreadsheet_id
        self._dry_run = settings.dry_run
        self._layouts: dict[str, SheetLayout] = {}

        credentials = Credentials.from_service_account_file(
            settings.google_credentials_file, scopes=SCOPES
        )
        self._api = build("sheets", "v4", credentials=credentials).spreadsheets()

    # ---------- Раскладка листа ----------

    def layout(self, sheet_name: str) -> SheetLayout:
        if sheet_name in self._layouts:
            return self._layouts[sheet_name]

        response = (
            self._api.values()
            .get(spreadsheetId=self._spreadsheet_id, range=f"'{sheet_name}'!1:1")
            .execute()
        )
        headers = (response.get("values") or [[]])[0]

        columns: dict[str, int] = {}
        for index, header in enumerate(headers):
            normalized = _normalize(header)
            if not normalized:
                continue
            for field, aliases in COLUMN_ALIASES.items():
                if field in columns:
                    continue
                if normalized in aliases or any(a in normalized for a in aliases):
                    columns[field] = index
                    break

        layout = SheetLayout(
            title=sheet_name, columns=columns, width=max(len(headers), 1)
        )
        self._layouts[sheet_name] = layout
        logger.info(
            "Лист «%s»: сопоставлено %d колонок из %d заголовков",
            sheet_name,
            len(columns),
            len(headers),
        )
        return layout

    # ---------- Запись ----------

    def append_tracking(self, candidate: Candidate) -> int | None:
        """Строка на вкладку «отслеживание проходящих»."""
        layout = self.layout(self._settings.sheet_tracking_name)
        values = {
            "date": datetime.now(self._settings.tz).strftime("%d.%m.%Y"),
            "full_name": candidate.full_name,
            "phone": candidate.phone,
            "vacancy": candidate.vacancy_title,
            "city": candidate.city,
            "age": candidate.age,
            "experience": candidate.experience_years,
            "salary": candidate.salary_expectation,
            "resume": candidate.resume_url,
            "interview_date": self._fmt_dt(candidate.interview_at),
            "status": "Собеседование назначено",
            "comment": candidate.comment,
            "lead_id": candidate.bitrix_lead_id,
        }
        return self._append(layout, values)

    def append_intern(self, candidate: Candidate, mentor: str | None = None) -> int | None:
        """Строка на вкладку «стажеры»."""
        layout = self.layout(self._settings.sheet_interns_name)
        values = {
            "hire_date": datetime.now(self._settings.tz).strftime("%d.%m.%Y"),
            "date": datetime.now(self._settings.tz).strftime("%d.%m.%Y"),
            "full_name": candidate.full_name,
            "phone": candidate.phone,
            "vacancy": candidate.vacancy_title,
            "mentor": mentor,
            "interview_date": self._fmt_dt(candidate.interview_at),
            "resume": candidate.resume_url,
            "status": "Стажировка",
            "comment": candidate.comment,
            "lead_id": candidate.bitrix_lead_id,
        }
        return self._append(layout, values)

    def update_tracking_status(
        self, row: int, status: str, comment: str | None = None
    ) -> None:
        """Обновить статус уже добавленной строки — без пересоздания."""
        layout = self.layout(self._settings.sheet_tracking_name)
        for field, value in (("status", status), ("comment", comment)):
            index = layout.columns.get(field)
            if index is None or value is None:
                continue
            cell = f"'{layout.title}'!{self._col_letter(index)}{row}"
            if self._dry_run:
                logger.info("DRY_RUN: %s = %s", cell, value)
                continue
            self._api.values().update(
                spreadsheetId=self._spreadsheet_id,
                range=cell,
                valueInputOption="USER_ENTERED",
                body={"values": [[value]]},
            ).execute()

    def _append(self, layout: SheetLayout, values: dict[str, Any]) -> int | None:
        row = layout.row(values)
        if self._dry_run:
            logger.info("DRY_RUN: в «%s» строка %s", layout.title, row)
            return None

        response = (
            self._api.values()
            .append(
                spreadsheetId=self._spreadsheet_id,
                range=f"'{layout.title}'!A:A",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )
        return self._row_number(response)

    # ---------- Мелочи ----------

    def _fmt_dt(self, value: datetime | None) -> str | None:
        return value.astimezone(self._settings.tz).strftime("%d.%m.%Y %H:%M") if value else None

    @staticmethod
    def _col_letter(index: int) -> str:
        letters = ""
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    @staticmethod
    def _row_number(response: dict[str, Any]) -> int | None:
        """Из ответа вида «'лист'!A12:M12» достать 12."""
        updated_range = (response.get("updates") or {}).get("updatedRange", "")
        match = re.search(r"![A-Z]+(\d+)", updated_range)
        return int(match.group(1)) if match else None
