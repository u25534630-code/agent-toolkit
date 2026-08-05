"""Разбор команд без нейросети — по ключевым словам.

Запасной вариант для случая, когда ключ Anthropic недоступен (например, нечем
оплатить). Реплики рекрутера однотипны — «фамилия, что произошло» — поэтому
правила покрывают большинство фраз. На вольных формулировках точность ниже,
чем у модели, но за это ничего не нужно платить.

Ключевой приём: фамилия сопоставляется со списком активных кандидатов, а не
угадывается из текста. Даже если распознавание речи исказило окончание,
«петров» найдёт «Петрова».
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta

from app.config import get_settings
from app.integrations.claude import Command

logger = logging.getLogger(__name__)

# Порядок важен: проверяем от более частного к общему. «прошла собеседование»
# должно сработать раньше, чем «собеседование».
ACTION_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "interview_passed",
        (
            "прошел собес", "прошла собес", "прошел собеседование",
            "прошла собеседование", "на стажировку", "выходит стажироваться",
            "берем на стажировку", "стажировк",
        ),
    ),
    (
        "hired",
        ("вышел на работу", "вышла на работу", "оформили", "в штат", "принят на работу"),
    ),
    (
        "no_answer",
        (
            "не дозвон", "недозвон", "не берет трубку", "не берёт трубку",
            "не отвечает", "не дозвонил", "не взял трубку", "не взяла трубку",
            "трубку не бер", "не подходит к телефону",
        ),
    ),
    # Резерв проверяем раньше отказа: «пока не берём, в резерв» содержит
    # и то и другое, но это не отказ — к человеку вернутся
    ("reserve", ("резерв", "на будущее", "пока не нужен", "пока не нужна")),
    (
        "reject",
        (
            "не подходит", "не подошел", "не подошла", "не подойдет",
            "отказ", "отказал", "отказалась", "не берем", "не берём",
            "не наш", "мимо",
        ),
    ),
    (
        "schedule_interview",
        ("собес", "собеседование", "назначил", "назначила", "пригласил", "пригласила"),
    ),
    (
        "add_candidate",
        (
            "запиши кандидат", "заведи", "добавь кандидат", "новый кандидат",
            "запиши", "добавь", "телефон", "тел.",
        ),
    ),
    ("note", ("заметка", "запомни", "пометь")),
]

# Слова самой команды не должны попадать в имя. Во фразе «Запиши кандидата:
# Иванова Мария» с заглавной буквы стоит и «Запиши», а имя ищется как раз
# по заглавным буквам — без вычистки кандидат называется «Запиши Иванова».
_COMMAND_WORDS = sorted(
    {marker for _, markers in ACTION_PATTERNS for marker in markers}
    | {"кандидата", "кандидат", "нового", "новая"},
    key=len,
    reverse=True,
)
_COMMAND_RE = re.compile(
    "|".join(re.escape(word) for word in _COMMAND_WORDS), re.IGNORECASE
)


def _strip_commands(text: str) -> str:
    """Убрать служебные слова, оставив то, что сказал человек по существу."""
    return re.sub(r"\s+", " ", _COMMAND_RE.sub(" ", text or "")).strip()

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
    "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5,
    "воскресенье": 6, "воскресение": 6,
}

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}

# Слова, после которых обычно идёт причина отказа
REASON_MARKERS = ("потому что", "причина", "так как", "нет ", "не устраивает", "далеко")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower().replace("ё", "е")


class RuleParser:
    """Тот же интерфейс, что у CommandParser, но без обращений к API."""

    def __init__(self) -> None:
        self._settings = get_settings()

    async def parse(
        self, text: str, known_candidates: list[str] | None = None
    ) -> Command:
        raw = text or ""
        normalized = _normalize(raw)
        if not normalized:
            return Command(action="unknown", confidence=0.0)

        action = self._detect_action(normalized)
        phone = self._extract_phone(raw)

        # «Иванова Мария, монтажник, телефон 900...» — команды нет, но есть
        # телефон, значит человека заводят
        if action is None and phone:
            action = "add_candidate"
        if action is None:
            return Command(action="unknown", confidence=0.0, candidate_ref=None)

        if action == "add_candidate":
            return self._parse_add(raw, normalized, phone)

        name, matched_known = self._extract_name(raw, normalized, known_candidates or [])
        if not name:
            return Command(action=action, confidence=0.3)

        command = Command(
            action=action,
            candidate_ref=name,
            # Совпадение с активным кандидатом — сильный признак,
            # что реплика разобрана верно
            confidence=0.9 if matched_known else 0.7,
        )

        if action == "reject":
            command.reject_reason = self._extract_reason(normalized)
        if action == "schedule_interview":
            when = self._extract_datetime(normalized)
            if when is None:
                # Без даты команда бессмысленна — пусть бот переспросит
                command.confidence = 0.4
            else:
                command.interview_at = when.isoformat()

        return command

    # ---------- Составляющие ----------

    @staticmethod
    def _detect_action(normalized: str) -> str | None:
        for action, markers in ACTION_PATTERNS:
            if any(marker in normalized for marker in markers):
                return action
        return None

    @staticmethod
    def _extract_phone(raw: str) -> str | None:
        """Самая длинная цепочка цифр, похожая на номер.

        Требовать ровно десять цифр нельзя: вслух номер часто диктуют не
        полностью или распознавание глотает последние цифры — «903-576-60».
        Лучше записать неполный номер, который человек допишет, чем не
        завести кандидата вовсе.
        """
        best: str | None = None
        for chunk in re.findall(r"\+?\d[\d\-\s()]{5,}\d", raw or ""):
            digits = re.sub(r"\D", "", chunk)
            if len(digits) >= 7 and (best is None or len(digits) > len(best)):
                best = digits
        return best

    def _extract_name(
        self, raw: str, normalized: str, known: list[str]
    ) -> tuple[str | None, bool]:
        """Сначала ищем среди активных кандидатов, потом — по написанию."""
        for candidate_name in known:
            stem = _normalize(candidate_name)[:-1] or _normalize(candidate_name)
            if stem and stem in normalized:
                return candidate_name, True

        # С заглавной буквы — но не служебное слово команды
        cleaned = _strip_commands(raw)
        capitalized = re.findall(r"\b[А-ЯЁ][а-яё]{2,}\b", cleaned)
        if capitalized:
            return capitalized[0], False

        # Иначе первое слово до запятой: «петрова, не подходит»
        head = _strip_commands(normalized).split(",")[0].split()
        return (head[0].capitalize(), False) if head else (None, False)

    def _parse_add(self, raw: str, normalized: str, phone: str | None) -> Command:
        # Режем исходную строку, а не приведённую к нижнему регистру: должность
        # и город попадут в таблицу как их произнесли
        cleaned = _strip_commands(raw)
        # Точку с пробелом считаем разделителем наравне с запятой: вслух
        # диктуют «Гандау Михаил Константинович. Телефон 903-576-60»
        parts = [
            stripped
            for part in re.split(r"[,;]|\.\s+", cleaned)
            if (stripped := part.strip(" :;.—-"))
        ]

        # Имя берём только из первого куска до запятой. Иначе во фразе
        # «Иванова, повар, Екатеринбург» город тоже написан с заглавной,
        # и кандидат получает фамилию «Иванова Екатеринбург».
        head = parts[0] if parts else cleaned
        # До трёх слов: фамилия, имя и отчество, если продиктовали полностью
        capitalized = re.findall(r"\b[А-ЯЁ][а-яё]{2,}\b", head)
        if capitalized:
            name = " ".join(capitalized[:3])
        else:
            name = head.title() or None

        position, city = None, None
        for part in parts[1:]:
            lowered = part.lower()
            if any(ch.isdigit() for ch in part) or "телефон" in lowered or "тел" == lowered[:3]:
                continue
            if position is None:
                position = part
            elif city is None:
                city = part

        return Command(
            action="add_candidate",
            candidate_ref=name,
            phone=phone,
            position=position,
            city=city,
            confidence=0.8 if name else 0.3,
        )

    @staticmethod
    def _extract_reason(normalized: str) -> str | None:
        for action, markers in ACTION_PATTERNS:
            if action != "reject":
                continue
            for marker in markers:
                index = normalized.find(marker)
                if index == -1:
                    continue
                tail = normalized[index + len(marker) :].strip(" ,.—-")
                if tail:
                    return tail[:120]
        # Причина могла прозвучать до отказа: «нет опыта, не подходит»
        for marker in REASON_MARKERS:
            index = normalized.find(marker)
            if index != -1:
                return normalized[index:].split(",")[0].strip()[:120]
        return None

    def _extract_datetime(self, normalized: str) -> datetime | None:
        now = datetime.now(self._settings.tz)
        day = self._extract_date(normalized, now)
        if day is None:
            return None
        moment = self._extract_time(normalized) or time(10, 0)
        return datetime.combine(day, moment, tzinfo=self._settings.tz)

    @staticmethod
    def _extract_date(normalized: str, now: datetime) -> date | None:
        if "послезавтра" in normalized:
            return (now + timedelta(days=2)).date()
        if "завтра" in normalized:
            return (now + timedelta(days=1)).date()
        if "сегодня" in normalized:
            return now.date()

        for word, index in WEEKDAYS.items():
            if word in normalized:
                ahead = (index - now.weekday()) % 7 or 7
                return (now + timedelta(days=ahead)).date()

        # 12.08 или 12.08.2026
        match = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", normalized)
        if match:
            day, month = int(match.group(1)), int(match.group(2))
            year = int(match.group(3) or now.year)
            year += 2000 if year < 100 else 0
            try:
                return date(year, month, day)
            except ValueError:
                return None

        # 12 августа
        match = re.search(r"\b(\d{1,2})\s+([а-я]+)", normalized)
        if match:
            day = int(match.group(1))
            word = match.group(2)
            for stem, month in MONTHS.items():
                if word.startswith(stem):
                    year = now.year + (1 if month < now.month else 0)
                    try:
                        return date(year, month, day)
                    except ValueError:
                        return None
        return None

    @staticmethod
    def _extract_time(normalized: str) -> time | None:
        # 15:00, 15-00, 15.00 — «15-00» так и пишут в таблице
        match = re.search(r"\b(\d{1,2})[:.\-](\d{2})\b", normalized)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour < 24 and minute < 60:
                return time(hour, minute)

        # «в 15», «в 9 часов»
        match = re.search(r"\bв\s+(\d{1,2})(?:\s*час\w*)?\b", normalized)
        if match:
            hour = int(match.group(1))
            if hour < 24:
                # «в 3» про собеседование почти наверняка про день, а не ночь
                return time(hour + 12 if hour < 8 else hour, 0)
        return None
