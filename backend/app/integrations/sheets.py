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

# Наше поле -> варианты названий колонки. Первыми в кортеже идут точные
# названия из рабочей таблицы, дальше — синонимы на случай переименования.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    # «отслеживание проходящих»
    "row_no": ("п/п", "№", "no"),
    "full_name": (
        "ф.и.о. кандидата",
        "фио кандидата",
        "ф.и.о.",
        "фио",
        "кандидат",
        "фамилия имя",
        "имя",
    ),
    "position": ("должность", "вакансия", "позиция"),
    "city": ("город", "регион"),
    "interview_date": (
        "дата собеседования",
        "дата собеса",
        "собеседование",
        "собес",
    ),
    "interview_time": ("время", "время собеседования"),
    "interviewer": ("ответственный", "кто проводит", "интервьюер"),
    "feedback": ("обратная связь", "комментарий", "примечание", "заметка"),
    # «Стажеры»
    "hire_date": ("дата выхода на стажировку", "дата выхода", "дата начала"),
    "crm": ("crm", "срм", "ссылка crm"),
    "source": ("с какого сайта сотрудник", "с какого сайта", "источник", "сайт"),
    "attestation": ("аттестация",),
    # Могут появиться позже — заполняются, только если колонка есть
    "phone": ("телефон", "номер", "тел", "контакт"),
    "resume": ("резюме", "ссылка на резюме"),
    "salary": ("ожидаемая зп", "зарплата", "ожидания", "оклад", "зп"),
    "age": ("возраст", "лет"),
    "experience": ("опыт работы", "опыт", "стаж"),
    "status": ("статус", "результат", "этап"),
    "lead_id": ("лид в битриксе", "id лида", "лид"),
    # Самое общее — намеренно последним, чтобы «Дата собеседования» не
    # перехватывалась общим «дата»
    "date": ("дата", "дата звонка", "дата добавления"),
}


def _normalize(header: str) -> str:
    return re.sub(r"\s+", " ", header or "").strip().lower().replace("ё", "е")


def match_columns(headers: list[str]) -> dict[str, int]:
    """Сопоставить заголовки листа с нашими полями.

    Два прохода: сначала точные совпадения, потом вхождение подстроки, причём
    выигрывает самый длинный подошедший синоним. Иначе «Дата собеседования»
    досталась бы полю `date` просто потому, что содержит слово «дата».
    """
    columns: dict[str, int] = {}
    taken: set[int] = set()

    for index, header in enumerate(headers):
        normalized = _normalize(header)
        if not normalized:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if field not in columns and normalized in aliases:
                columns[field] = index
                taken.add(index)
                break

    for index, header in enumerate(headers):
        if index in taken:
            continue
        normalized = _normalize(header)
        if not normalized:
            continue
        best: tuple[int, str] | None = None
        for field, aliases in COLUMN_ALIASES.items():
            if field in columns:
                continue
            for alias in aliases:
                if alias in normalized and (best is None or len(alias) > best[0]):
                    best = (len(alias), field)
        if best:
            columns[best[1]] = index
            taken.add(index)

    return columns


# Колонки, без которых лист теряет смысл. Остальные заполняются, если есть.
REQUIRED_TRACKING = {"full_name", "position", "interview_date", "feedback"}
REQUIRED_INTERNS = {"full_name", "position", "hire_date"}


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

    def missing(self, required: set[str]) -> list[str]:
        """Каких из нужных этому листу колонок не нашлось."""
        return sorted(required - set(self.columns))


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
        columns = match_columns(headers)

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
        """Строка на вкладку «отслеживание проходящих».

        Дата и время собеседования разнесены по двум колонкам — так в таблице.
        Колонка «кто проводит» остаётся пустой: бот не знает, кто из коллег
        возьмёт собеседование, это заполняется руками.
        """
        layout = self.layout(self._settings.sheet_tracking_name)
        interview = self._local(candidate.interview_at)
        values = {
            "full_name": candidate.full_name,
            "position": candidate.vacancy_title,
            "city": candidate.city,
            "interview_date": interview.strftime("%d.%m") if interview else None,
            "interview_time": interview.strftime("%H-%M") if interview else None,
            "feedback": candidate.comment,
            # Заполнятся, только если такие колонки в листе есть
            "date": datetime.now(self._settings.tz).strftime("%d.%m.%Y"),
            "phone": candidate.phone,
            "resume": candidate.resume_url,
            "salary": candidate.salary_expectation,
            "age": candidate.age,
            "experience": candidate.experience_years,
            "status": "Собеседование назначено",
            "lead_id": candidate.bitrix_lead_id,
        }
        return self._append(layout, values)

    def append_intern(self, candidate: Candidate) -> int | None:
        """Строка на вкладку «Стажеры».

        В колонку CRM кладём ссылку на резюме — в таблице там встречаются и
        ссылки, и телефоны. «Аттестация» остаётся пустой, её ставит человек.
        """
        layout = self.layout(self._settings.sheet_interns_name)
        values = {
            "hire_date": datetime.now(self._settings.tz).strftime("%d.%m.%y"),
            "full_name": candidate.full_name,
            "position": candidate.vacancy_title,
            "crm": candidate.resume_url or candidate.phone,
            "source": self._settings.sheet_source_label,
            "feedback": candidate.comment,
            # Заполнятся, только если такие колонки в листе есть
            "phone": candidate.phone,
            "resume": candidate.resume_url,
            "interview_date": self._fmt_dt(candidate.interview_at),
            "status": "Стажировка",
            "lead_id": candidate.bitrix_lead_id,
        }
        return self._append(layout, values)

    def update_tracking_status(
        self, row: int, status: str, comment: str | None = None
    ) -> None:
        """Дописать результат в уже добавленную строку, не создавая новую."""
        layout = self.layout(self._settings.sheet_tracking_name)
        # В рабочей таблице результат живёт в «Обратной связи», отдельной
        # колонки «Статус» нет — пишем в обе, какая найдётся
        feedback = " — ".join(part for part in (status, comment) if part)
        for field, value in (("status", status), ("feedback", feedback)):
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

    def _local(self, value: datetime | None) -> datetime | None:
        return value.astimezone(self._settings.tz) if value else None

    def _fmt_dt(self, value: datetime | None) -> str | None:
        local = self._local(value)
        return local.strftime("%d.%m.%Y %H:%M") if local else None

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
