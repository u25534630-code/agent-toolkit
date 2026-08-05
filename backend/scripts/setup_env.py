"""Мастер настройки: задаёт вопросы и создаёт файл .env.

    python -m scripts.setup_env

Редактировать .env руками не обязательно — мастер спросит только то, что нужно,
и объяснит, где брать каждое значение. Пустой ответ означает «пока не подключаю»:
бот запустится и без Битрикса, без hh.ru и без таблицы.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ENV_PATH = Path(".env")
EXAMPLE_PATH = Path(".env.example")


def ask(prompt: str, default: str = "", required: bool = False, secret: bool = False) -> str:
    """Спросить значение и подтвердить, что оно принято.

    Подтверждение важнее, чем кажется: без него непонятно, двинулся мастер
    дальше или Enter не дошёл до окна, и человек жмёт ещё раз.
    """
    suffix = f" [{default}]" if default else ""
    attempts = 0
    while True:
        try:
            value = input(f"{prompt}{suffix}: ").strip() or default
        except EOFError:
            # Ввод закрыт: в цикле смысла нет, иначе мастер зациклится молча
            print("\nВвод недоступен — прерываю настройку.")
            raise SystemExit(1)

        if value or not required:
            if value:
                shown = "*" * 8 if secret else value
                print(f"  принято: {shown}")
            else:
                print("  пропущено")
            return value

        attempts += 1
        print("  Это значение обязательно, без него бот не запустится.")
        if attempts >= 5:
            print("Прерываю настройку. Запустите start.bat ещё раз, когда будет значение.")
            raise SystemExit(1)


def section(title: str, hint: str = "") -> None:
    print(f"\n{'─' * 60}\n{title}")
    if hint:
        print(hint)


def main() -> None:
    if ENV_PATH.exists():
        answer = input(
            ".env уже существует. Перезаписать? Старый сохраню как .env.backup (д/н): "
        ).strip().lower()
        if answer not in ("д", "да", "y", "yes"):
            print("Ничего не меняю.")
            return
        shutil.copy(ENV_PATH, Path(".env.backup"))
        print("Старый файл сохранён как .env.backup")

    print("\nНастройка бота. Значения берите из своей заметки.")
    values: dict[str, str] = {}

    section(
        "1. Telegram — обязательно",
        "Токен выдал @BotFather, ID подсказал @userinfobot.",
    )
    values["TELEGRAM_BOT_TOKEN"] = ask("Токен бота", required=True, secret=True)
    user_id = ask("Ваш Telegram ID (число)", required=True)
    values["TELEGRAM_ALLOWED_USER_IDS"] = f"[{user_id}]"

    section(
        "2. Anthropic — можно пропустить",
        "Ключ из console.anthropic.com, начинается на sk-ant-.\n"
        "Пустой ответ — команды разбираются правилами, без оплаты. Работает,\n"
        "но хуже понимает вольные формулировки: говорите «Фамилия, что произошло».",
    )
    values["ANTHROPIC_API_KEY"] = ask("Ключ Anthropic", secret=True)

    section(
        "3. Google-таблица — можно пропустить",
        "Пустой ответ — бот не будет писать в таблицу.",
    )
    spreadsheet = ask("ID таблицы (часть ссылки между /d/ и /edit)")
    values["GOOGLE_SPREADSHEET_ID"] = spreadsheet
    if spreadsheet:
        values["GOOGLE_CREDENTIALS_FILE"] = ask(
            "Путь к файлу ключа", default="./service-account.json"
        )

    section(
        "4. Битрикс — можно пропустить",
        "Пустой ответ — бот работает без CRM, всё остальное не страдает.",
    )
    webhook = ask("Ссылка входящего вебхука", secret=True)
    values["BITRIX_WEBHOOK_URL"] = webhook
    if webhook:
        values["BITRIX_DEAL_CATEGORY_ID"] = ask("Номер воронки HR", default="0")

    section(
        "5. hh.ru — можно пропустить",
        "Токены получает scripts/hh_auth.py. Пустой ответ — отклики не забираем.",
    )
    token = ask("HH_ACCESS_TOKEN", secret=True)
    if token:
        values["HH_ACCESS_TOKEN"] = token
        values["HH_REFRESH_TOKEN"] = ask("HH_REFRESH_TOKEN", secret=True)
        values["HH_CLIENT_ID"] = ask("HH_CLIENT_ID")
        values["HH_CLIENT_SECRET"] = ask("HH_CLIENT_SECRET", secret=True)
        values["HH_EMPLOYER_ID"] = ask("HH_EMPLOYER_ID")

    section(
        "6. Как запускаем",
        "На домашнем компьютере лучше модель поменьше — medium требует памяти\n"
        "и дольше грузится. Качество на коротких фразах отличается несильно.",
    )
    values["WHISPER_MODEL"] = ask("Модель распознавания речи", default="small")
    dry = ask("Пробный режим — ничего не записывать наружу? (д/н)", default="д")
    values["DRY_RUN"] = "true" if dry.lower() in ("д", "да", "y", "yes") else "false"

    # За основу берём .env.example, чтобы сохранить комментарии и всё остальное
    lines = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    result: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in values:
                result.append(f"{key}={values[key]}")
                continue
        result.append(line)

    ENV_PATH.write_text("\n".join(result) + "\n", encoding="utf-8")

    print(f"\n{'─' * 60}")
    print("Готово, файл .env создан.\n")
    print("Что включено:")
    print(f"  Telegram   — да")
    print(f"  Разбор команд — {'Anthropic' if values.get('ANTHROPIC_API_KEY') else 'правилами, без оплаты'}")
    print(f"  Таблица    — {'да' if values.get('GOOGLE_SPREADSHEET_ID') else 'нет'}")
    print(f"  Битрикс    — {'да' if values.get('BITRIX_WEBHOOK_URL') else 'нет'}")
    print(f"  hh.ru      — {'да' if values.get('HH_ACCESS_TOKEN') else 'нет'}")
    if values["DRY_RUN"] == "true":
        print(
            "\nПробный режим включён: бот отвечает и всё понимает, но наружу\n"
            "ничего не пишет. Когда убедитесь, что всё верно, запустите мастер\n"
            "заново и ответьте «н» на вопрос про пробный режим."
        )
    print("\nТеперь запускайте бота: start.bat (Windows) или ./start.sh")


if __name__ == "__main__":
    main()
