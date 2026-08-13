"""Подготовка Битрикса: воронки, стадии и пользовательские поля сделок.

    python -m scripts.setup_bitrix --show-stages    воронки и коды их стадий
    python -m scripts.setup_bitrix --write-env      вписать коды стадий в .env
    python -m scripts.setup_bitrix --show-fields    поля сделки, которые уже есть
    python -m scripts.setup_bitrix --create-fields  создать недостающие поля

Подбор ведётся Сделками в воронке HR, поэтому смотрим стадии сделок, а не лидов.
Скрипт ничего не удаляет и не перезаписывает: существующие поля пропускаются.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from pathlib import Path

from app.config import get_settings
from app.integrations.bitrix import BitrixClient

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Код поля -> (подпись в карточке, тип в Битриксе)
FIELDS: dict[str, tuple[str, str]] = {
    "UF_CRM_RESUME_URL": ("Ссылка на резюме", "string"),
    "UF_CRM_AGE": ("Возраст", "integer"),
    "UF_CRM_CITY": ("Город", "string"),
    "UF_CRM_EXPERIENCE": ("Опыт, лет", "double"),
    "UF_CRM_SALARY": ("Ожидаемая ЗП", "string"),
    "UF_CRM_REJECT_REASON": ("Причина отказа", "string"),
    "UF_CRM_VACANCY": ("Вакансия", "string"),
}

# Наш статус -> переменная в .env и как стадия называется в воронке HR
STAGE_HINTS = [
    ("BITRIX_STAGE_NEW", "Новое резюме"),
    ("BITRIX_STAGE_CALLED", "Первичный созвон"),
    ("BITRIX_STAGE_TEST_TASK", "Тестовое задание"),
    ("BITRIX_STAGE_INTERVIEW", "Собеседование"),
    ("BITRIX_STAGE_INTERN", "Стажировка"),
    ("BITRIX_STAGE_RESERVE", "Кадровый резерв"),
    ("BITRIX_STAGE_HIRED", "успешная стадия — вышел на работу"),
    ("BITRIX_STAGE_REJECTED", "провальная стадия — не подходит"),
]


async def show_stages(client: BitrixClient) -> None:
    categories = await client.list_deal_categories()

    print("\nВоронки сделок в вашем портале:\n")
    print(f"  {'ID':<6} Название")
    print(f"  {'0':<6} Общая (основная воронка)")
    for category in categories:
        print(f"  {category.get('ID'):<6} {category.get('NAME')}")

    print(
        "\nНайдите воронку HR и впишите её ID в BITRIX_DEAL_CATEGORY_ID.\n"
        "Ниже — стадии каждой воронки.\n"
    )

    for category_id, name in [("0", "Общая")] + [
        (str(c.get("ID")), c.get("NAME")) for c in categories
    ]:
        stages = await client.list_deal_stages(int(category_id))
        if not stages:
            continue
        print(f"--- Воронка {category_id}: {name} ---")
        for stage in stages:
            # Полный код вида C7:EXECUTING; в .env нужна часть после двоеточия
            full = str(stage.get("STATUS_ID"))
            short = full.split(":", 1)[1] if ":" in full else full
            print(f"  {short:<22} {stage.get('NAME'):<28} (полный код {full})")
        print()

    print("Сопоставьте стадии воронки HR с переменными .env:\n")
    for variable, meaning in STAGE_HINTS:
        print(f"  {variable:<24} — {meaning}")

    # Список воронок повторяем в конце: стадий бывает на несколько экранов,
    # и самое нужное — номер воронки — уезжает наверх, где его уже не видно
    print(f"\n{'=' * 60}")
    print("НОМЕРА ВОРОНОК — впишите нужный в BITRIX_DEAL_CATEGORY_ID:\n")
    print(f"  {'0':<6} Общая (основная воронка)")
    for category in categories:
        print(f"  {category.get('ID'):<6} {category.get('NAME')}")
    print(f"{'=' * 60}")


# Переменная .env -> слова, по которым узнаём стадию в названии.
# Порядок важен: «прошёл собеседование» встречается в названиях реже, но
# «собеседование» есть и в «Тестовое задание перед собеседованием».
STAGE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("BITRIX_STAGE_NEW", ("новое резюме", "новый", "новая", "резюме")),
    ("BITRIX_STAGE_CALLED", ("первичный созвон", "созвон", "прозвон", "звонок")),
    ("BITRIX_STAGE_TEST_TASK", ("тестовое", "тест")),
    ("BITRIX_STAGE_INTERVIEW", ("собеседование", "собес", "интервью")),
    ("BITRIX_STAGE_INTERN", ("стаж",)),
    ("BITRIX_STAGE_RESERVE", ("резерв", "на будущее")),
]


# Латиница, неотличимая от кириллицы на глаз. В названиях стадий, набранных
# руками, такие буквы попадаются постоянно: «СТАЖИРOВКА» с латинской O
# выглядит правильно и не совпадает ни с чем.
_LOOKALIKE = str.maketrans("acekmoprstxy", "асекморрстху")


def _norm(text: str) -> str:
    return (text or "").strip().lower().replace("ё", "е").translate(_LOOKALIKE)


def match_stages(stages: list[dict]) -> dict[str, str]:
    """Сопоставить стадии воронки с переменными .env по названиям.

    Коды вида UC_5OFQAH человек переписывает с экрана с ошибками — букву «O»
    от нуля на глаз не отличить. Поэтому берём их из ответа Битрикса.
    """
    result: dict[str, str] = {}
    taken: set[str] = set()

    def short(stage: dict) -> str:
        full = str(stage.get("STATUS_ID") or "")
        return full.split(":", 1)[1] if ":" in full else full

    # Успех и провал Битрикс помечает сам — надёжнее любых слов
    for stage in stages:
        code = short(stage)
        semantics = stage.get("SEMANTICS")
        if semantics == "S" and "BITRIX_STAGE_HIRED" not in result:
            result["BITRIX_STAGE_HIRED"] = code
            taken.add(code)
        elif semantics == "F" and "BITRIX_STAGE_REJECTED" not in result:
            result["BITRIX_STAGE_REJECTED"] = code
            taken.add(code)

    for variable, keywords in STAGE_KEYWORDS:
        for stage in stages:
            code = short(stage)
            if code in taken:
                continue
            name = _norm(str(stage.get("NAME")))
            if any(word in name for word in keywords):
                result[variable] = code
                taken.add(code)
                break

    return result


def write_env(values: dict[str, str], path: Path = Path(".env")) -> None:
    """Записать значения в .env, не трогая остальные строки.

    Ключи, которых в файле нет, дописываются в конец. Раньше они молча
    пропускались: скрипт отчитывался «записал», значение никуда не
    попадало, и обнаруживалось это через сутки — когда бот сообщал, что
    сервис не настроен.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    written: set[str] = set()

    result = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in values and not line.strip().startswith("#"):
            result.append(f"{key}={values[key]}")
            written.add(key)
        else:
            result.append(line)

    missing = [key for key in values if key not in written]
    if missing:
        result.append("")
        for key in missing:
            result.append(f"{key}={values[key]}")

    path.write_text("\n".join(result) + "\n", encoding="utf-8")


async def write_env_stages(client: BitrixClient) -> None:
    settings = get_settings()
    category_id = settings.bitrix_deal_category_id
    stages = await client.list_deal_stages(category_id)
    if not stages:
        print(
            f"В воронке {category_id} стадий не нашлось. Проверьте, что в .env "
            "BITRIX_DEAL_CATEGORY_ID указывает на воронку HR "
            "(python -m scripts.setup_bitrix --show-stages)."
        )
        return

    print(f"\nВоронка {category_id}, стадий: {len(stages)}\n")
    values = match_stages(stages)
    names = {
        str(s.get("STATUS_ID")).split(":", 1)[-1]: s.get("NAME") for s in stages
    }
    for variable, _ in STAGE_HINTS:
        code = values.get(variable)
        if code:
            print(f"  {variable:<24} = {code:<12} ({names.get(code)})")
        else:
            print(f"  {variable:<24} — не нашёл подходящую стадию")

    unmatched = [
        s.get("NAME")
        for s in stages
        if str(s.get("STATUS_ID")).split(":", 1)[-1] not in set(values.values())
    ]
    if unmatched:
        print("\nСтадии, которым не нашлось места: " + ", ".join(map(str, unmatched)))

    if not Path(".env").exists():
        print("\nФайл .env не найден — запускать нужно из папки backend.")
        return

    write_env(values)
    print(f"\nЗаписал в .env: {len(values)} из {len(STAGE_HINTS)}.")
    missing = [v for v, _ in STAGE_HINTS if v not in values]
    if missing:
        print(
            "Не сопоставились: " + ", ".join(missing) + ".\n"
            "Эти стадии останутся со старыми значениями — впишите руками "
            "или скажите, как они называются в вашей воронке."
        )
    print("Перезапустите бота, чтобы настройки вступили в силу.")


# Переменная .env -> слова в названии поля карточки
FIELD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("BITRIX_UF_RESUME_URL", ("ссылка на резюме", "резюме")),
    ("BITRIX_UF_VACANCY", ("кандидат на должность", "должность", "вакансия")),
    ("BITRIX_UF_CITY", ("город",)),
    ("BITRIX_UF_BRANCH", ("филиал", "подразделение")),
    ("BITRIX_UF_AGE", ("возраст",)),
    ("BITRIX_UF_EXPERIENCE", ("опыт", "стаж")),
    ("BITRIX_UF_SALARY", ("зарплат", "ожидан", "оклад", "зп")),
    ("BITRIX_UF_REJECT_REASON", ("причина отказа", "причина")),
]


async def deal_fields_with_titles(client: BitrixClient) -> dict[str, dict]:
    """Поля сделки вместе с названиями.

    crm.deal.userfield.list отдаёт коды без подписей — список из шестидесяти
    строк вида UF_CRM_69A935DCB3313, в котором ничего не найти. Названия
    знает crm.deal.fields.
    """
    result = await client._call("crm.deal.fields", {})  # noqa: SLF001
    return result or {}


def match_fields(fields: dict[str, dict]) -> dict[str, str]:
    """Сопоставить поля карточки с переменными .env по их названиям."""
    named = [
        (code, _norm(str(info.get("title") or info.get("formLabel") or "")))
        for code, info in fields.items()
        if code.startswith("UF_")
    ]

    result: dict[str, str] = {}
    taken: set[str] = set()
    for variable, keywords in FIELD_KEYWORDS:
        best: tuple[int, str] | None = None
        for code, title in named:
            if not title or code in taken:
                continue
            for word in keywords:
                if word in title and (best is None or len(word) > best[0]):
                    best = (len(word), code)
        if best:
            result[variable] = best[1]
            taken.add(best[1])
    return result


async def show_fields(client: BitrixClient) -> None:
    """Показать поля карточки с названиями и предложить сопоставление.

    Свои поля («Должность», «Ссылка на резюме») в портале обычно уже
    заведены — под своими кодами. Создавать рядом ещё одни значит раздвоить
    карточку: рекрутер заполняет одно поле, бот пишет в другое.
    """
    fields = await deal_fields_with_titles(client)
    named = {
        code: str(info.get("title") or "")
        for code, info in fields.items()
        if code.startswith("UF_") and info.get("title")
    }

    if not named:
        print("Пользовательских полей с названиями у сделок нет.")
        return

    print(f"\nПоля карточки сделки ({len(named)}):\n")
    for code, title in sorted(named.items(), key=lambda pair: pair[1]):
        print(f"  {code:<28} {title}")

    guessed = match_fields(fields)
    print(f"\n{'=' * 60}")
    if guessed:
        print("Похоже, это ваши поля:\n")
        for variable, code in guessed.items():
            print(f"  {variable:<26} = {code:<28} ({named.get(code, '')})")
        print(
            "\nЗаписать их в .env: python -m scripts.setup_bitrix --map-fields"
        )
    else:
        print("Подходящих по названию полей не нашлось.")
    print("=" * 60)


async def map_fields(client: BitrixClient) -> None:
    """Вписать коды полей карточки в .env."""
    fields = await deal_fields_with_titles(client)
    guessed = match_fields(fields)
    if not guessed:
        print("Не нашёл полей, похожих на нужные. Впишите коды в .env вручную.")
        return

    titles = {code: str(info.get("title") or "") for code, info in fields.items()}
    for variable, code in guessed.items():
        print(f"  {variable:<26} = {code:<28} ({titles.get(code, '')})")

    if not Path(".env").exists():
        print("\nФайл .env не найден — запускать нужно из папки backend.")
        return

    # Сопоставление угадано по названиям — пусть человек подтвердит,
    # прежде чем бот начнёт писать в эти поля
    if sys.stdin.isatty():
        answer = input("\nЗаписать эти коды в настройки? (д/н): ").strip().lower()
        if answer not in ("д", "да", "y", "yes"):
            print("Ничего не меняю.")
            return

    write_env(guessed)
    print(f"\nЗаписал в .env: {len(guessed)}. Перезапустите бота.")


async def create_fields(client: BitrixClient) -> None:
    existing = {field.get("FIELD_NAME") for field in await client.list_userfields()}

    for code, (label, field_type) in FIELDS.items():
        if code in existing:
            print(f"  уже есть: {code}")
            continue
        try:
            await client.create_userfield(code, label, field_type)
            print(f"  создано:  {code} — {label}")
        except Exception as error:
            print(f"  ошибка:   {code} — {error}")

    print("\nГотово. Коды полей уже прописаны в .env.example как значения по умолчанию.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Подготовка Битрикса")
    parser.add_argument("--show-stages", action="store_true")
    parser.add_argument("--write-env", action="store_true")
    parser.add_argument("--show-fields", action="store_true")
    parser.add_argument("--map-fields", action="store_true")
    parser.add_argument("--create-fields", action="store_true")
    args = parser.parse_args()

    if not (
        args.show_stages
        or args.write_env
        or args.show_fields
        or args.map_fields
        or args.create_fields
    ):
        parser.print_help()
        return

    settings = get_settings()
    if not settings.bitrix_configured:
        print(
            "BITRIX_WEBHOOK_URL не заполнен в .env — скрипту не с чем работать.\n"
            "Битрикс подключать не обязательно: бот работает и без него, "
            "см. docs/bitrix_setup.md."
        )
        return

    print(f"Портал: {settings.bitrix_webhook_url.split('/rest/')[0]}")

    # Скрипт создаёт поля по-настоящему, даже если в .env стоит DRY_RUN
    client = BitrixClient(dry_run=False)
    try:
        if args.show_stages:
            await show_stages(client)
        if args.write_env:
            await write_env_stages(client)
        if args.show_fields:
            await show_fields(client)
        if args.map_fields:
            await map_fields(client)
        if args.create_fields:
            await create_fields(client)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
