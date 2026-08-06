"""Проверка настроек: что подключено, что нет и что не работает.

    python -m scripts.check

Ничего не меняет и никуда не пишет — только читает .env и стучится в сервисы,
чтобы ответить на вопрос «я вроде подключила, всё ли правильно». Запускать
можно в любой момент, в том числе при работающем боте.

Отдельный скрипт, а не проверка на старте: при старте важно запуститься даже
с половиной настроек, а здесь наоборот — подробно сказать, чего не хватает.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ENV_PATH = Path(".env")

OK = "[ да ]"
NO = "[ нет ]"
SKIP = "[ -- ]"
BAD = "[ ОШИБКА ]"

problems: list[str] = []


def section(title: str) -> None:
    print(f"\n{'-' * 60}\n{title}")


def line(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def fail(text: str, hint: str = "") -> None:
    line(BAD, text)
    if hint:
        print(f"         {hint}")
    problems.append(text)


# ---------------------------------------------------------------- Telegram


def check_telegram(settings) -> None:
    section("Телеграм")
    if not settings.telegram_bot_token:
        fail("Токен бота не задан", "Запустите мастер: python -m scripts.setup_env")
        return

    import httpx

    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=15,
        )
        data = response.json()
    except Exception as error:  # noqa: BLE001 — показываем причину как есть
        fail(f"Не удалось связаться с Телеграмом: {error}", "Проверьте интернет")
        return

    if not data.get("ok"):
        fail(
            f"Телеграм не принял токен: {data.get('description')}",
            "Токен мог быть отозван. Возьмите новый у @BotFather и запустите мастер",
        )
        return

    bot = data["result"]
    line(OK, f"Бот найден: @{bot.get('username')} ({bot.get('first_name')})")

    if not settings.telegram_allowed_user_ids:
        fail(
            "Не указано, кому можно писать боту",
            "Без этого бот ответит любому. Впишите свой ID через мастер",
        )
    else:
        ids = ", ".join(str(i) for i in settings.telegram_allowed_user_ids)
        line(OK, f"Доступ разрешён: {ids}")


# --------------------------------------------------------------- Разбор команд


def check_parser(settings) -> None:
    section("Разбор команд")
    if not settings.anthropic_configured:
        line(SKIP, "Ключ Anthropic не задан — команды разбираются правилами")
        print("         Бесплатно. Формулировки держите простыми:")
        print('         «Петрова, собес в четверг в 15:00»')
        return

    import httpx

    try:
        response = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=15,
        )
    except Exception as error:  # noqa: BLE001
        fail(f"Не удалось связаться с Anthropic: {error}")
        return

    if response.status_code == 200:
        line(OK, f"Ключ Anthropic принят, модель {settings.anthropic_model}")
    else:
        fail(
            f"Anthropic отклонил ключ (код {response.status_code})",
            "Проверьте, что ключ скопирован целиком и на счёте есть средства",
        )


# ------------------------------------------------------------------ Таблица


def check_sheets(settings) -> None:
    section("Google-таблица")
    if not settings.sheets_configured:
        line(SKIP, "ID таблицы не задан — бот не пишет в таблицу")
        return

    key_path = Path(settings.google_credentials_file)
    if not key_path.exists():
        fail(
            f"Файл ключа не найден: {key_path}",
            "Положите service-account.json в папку backend, рядом со start.bat",
        )
        return

    try:
        robot = json.loads(key_path.read_text(encoding="utf-8")).get("client_email", "")
    except Exception as error:  # noqa: BLE001
        fail(f"Файл ключа испорчен: {error}", "Скачайте ключ заново")
        return
    line(OK, f"Ключ на месте, робот: {robot}")

    from googleapiclient.errors import HttpError

    from app.integrations.sheets import (
        REQUIRED_INTERNS,
        REQUIRED_TRACKING,
        SheetsClient,
    )

    try:
        client = SheetsClient()
        meta = (
            client._api.get(  # noqa: SLF001 — служебный доступ ради диагностики
                spreadsheetId=settings.google_spreadsheet_id
            ).execute()
        )
    except HttpError as error:
        # resp.status есть у всех версий библиотеки, status_code появился позже
        status = error.resp.status
        if status == 403:
            fail(
                "Робота не пустили в таблицу",
                f"Откройте таблицу, «Настройки доступа» -> добавьте {robot} как Редактора",
            )
        elif status == 404:
            fail(
                "Таблица с таким ID не найдена",
                "ID — это часть ссылки между /d/ и /edit",
            )
        else:
            fail(f"Google ответил ошибкой: {error}")
        return
    except Exception as error:  # noqa: BLE001
        if _looks_like_dns(error):
            fail(
                "Компьютер не смог найти сервер Google (sheets.googleapis.com)",
                "Это не настройки — до серверов Google не доходит сеть.\n"
                "         Откройте в браузере https://sheets.googleapis.com —\n"
                "         если тоже не открывается, дело в интернете: включите\n"
                "         VPN либо смените DNS на 1.1.1.1. Всё остальное в боте\n"
                "         продолжает работать.",
            )
        else:
            fail(f"Не удалось открыть таблицу: {error}")
        return

    line(OK, f"Таблица открывается: «{meta['properties']['title']}»")
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]

    for wanted, required, human in (
        (settings.sheet_tracking_name, REQUIRED_TRACKING, "отслеживание проходящих"),
        (settings.sheet_interns_name, REQUIRED_INTERNS, "стажёры"),
    ):
        actual = _find_sheet(titles, wanted)
        if not actual:
            fail(
                f"Не нашёл вкладку «{wanted}» ({human})",
                f"Вкладки в таблице: {', '.join(titles)}",
            )
            continue
        if actual != wanted:
            line(NO, f"Вкладка называется «{actual}», в настройках «{wanted}»")
            print("         Работать будет, но лучше поправить .env")

        try:
            layout = client.layout(actual)
        except Exception as error:  # noqa: BLE001
            fail(f"Не смог прочитать заголовки вкладки «{actual}»: {error}")
            continue

        missing = layout.missing(required)
        if missing:
            fail(
                f"Во вкладке «{actual}» не нашлись колонки: {', '.join(missing)}",
                "Проверьте, что первая строка листа — заголовки",
            )
        else:
            line(OK, f"Вкладка «{actual}»: колонки на месте ({len(layout.columns)} шт.)")


def _looks_like_dns(error: BaseException) -> bool:
    """Не разрешилось имя сервера, а не «что-то пошло не так».

    Разные слои дают разные исключения: httplib2 — ServerNotFoundError,
    сокеты — gaierror. Смотрим всю цепочку причин и текст.
    """
    import socket

    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, socket.gaierror):
            return True
        name = type(seen).__name__
        text = str(seen)
        if "ServerNotFound" in name or "Unable to find the server" in text:
            return True
        if "getaddrinfo failed" in text or "Name or service not known" in text:
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _find_sheet(titles: list[str], wanted: str) -> str | None:
    """Найти вкладку, не придираясь к регистру и «ё»."""

    def norm(value: str) -> str:
        return value.strip().lower().replace("ё", "е")

    for title in titles:
        if norm(title) == norm(wanted):
            return title
    return None


# ------------------------------------------------------------------- Битрикс


def check_bitrix(settings) -> None:
    section("Битрикс")
    if not settings.bitrix_configured:
        line(SKIP, "Вебхук не задан — работаем без CRM, остальное не страдает")
        return

    import httpx

    try:
        profile = httpx.get(
            settings.bitrix_webhook_url + "profile", timeout=20
        ).json()
        scope = httpx.get(settings.bitrix_webhook_url + "scope", timeout=20).json()
    except Exception as error:  # noqa: BLE001
        fail(f"Битрикс не отвечает: {error}", "Проверьте ссылку вебхука")
        return

    if "error" in profile:
        fail(
            f"Битрикс вернул ошибку: {profile.get('error_description') or profile['error']}",
            "Ссылка вебхука неверна или отозвана",
        )
        return

    result = profile.get("result", {})
    who = f"{result.get('NAME', '')} {result.get('LAST_NAME', '')}".strip()
    line(OK, f"Подключение работает, вебхук от имени: {who or 'без имени'}")

    granted = [s for s in scope.get("result", []) if s]
    if "crm" not in granted:
        fail(
            "У вебхука нет прав на CRM",
            "Права выдаёт администратор портала: в настройках вебхука "
            "поставить галочку CRM и сохранить",
        )
    else:
        line(OK, f"Права: {', '.join(granted)}")

    if not settings.bitrix_deal_category_id:
        line(NO, "Номер воронки HR не задан — сделки уйдут в основную воронку")
        print("         Посмотреть номера: python -m scripts.setup_bitrix --show-stages")


# -------------------------------------------------------------------- hh.ru


def check_hh(settings) -> None:
    section("hh.ru")
    if not settings.hh_configured:
        # Раньше здесь было просто «токен не задан». Когда авторизация уже
        # проходила, это сбивает с толку: непонятно, что именно потерялось.
        missing = [
            name
            for name, value in (
                ("HH_ACCESS_TOKEN", settings.hh_access_token),
                ("HH_EMPLOYER_ID", settings.hh_employer_id),
            )
            if not value
        ]
        line(SKIP, "Отклики не забираем — пусто: " + ", ".join(missing))
        filled = [
            name
            for name, value in (
                ("HH_CLIENT_ID", settings.hh_client_id),
                ("HH_CLIENT_SECRET", settings.hh_client_secret),
                ("HH_ACCESS_TOKEN", settings.hh_access_token),
                ("HH_REFRESH_TOKEN", settings.hh_refresh_token),
                ("HH_EMPLOYER_ID", settings.hh_employer_id),
            )
            if value
        ]
        if filled:
            print(f"         Заполнено: {', '.join(filled)}")
            print("         Значит, авторизация проходила, но записалось не всё.")
            print("         Повторите: python -m scripts.hh_auth")
        else:
            print("         Авторизация: python -m scripts.hh_auth")
        return

    import httpx

    try:
        response = httpx.get(
            "https://api.hh.ru/me",
            headers={"Authorization": f"Bearer {settings.hh_access_token}"},
            timeout=20,
        )
    except Exception as error:  # noqa: BLE001
        fail(f"hh.ru не отвечает: {error}")
        return

    if response.status_code == 200:
        data = response.json()
        line(OK, f"Вход выполнен: {data.get('email') or data.get('first_name')}")
    elif response.status_code == 403:
        fail(
            "hh.ru не принял токен",
            "Получите новый: python -m scripts.hh_auth",
        )
    else:
        fail(f"hh.ru ответил кодом {response.status_code}")


# --------------------------------------------------------------------- main


def main() -> int:
    if not ENV_PATH.exists():
        print("Файл настроек .env не найден.")
        print("Запустите мастер: python -m scripts.setup_env")
        return 1

    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception as error:  # noqa: BLE001
        print(f"Не смог прочитать настройки: {error}")
        print("Скорее всего, в .env пустой TELEGRAM_BOT_TOKEN. Запустите мастер заново.")
        return 1

    print("\nПроверяю настройки. Ничего не меняю и никуда не записываю.")

    check_telegram(settings)
    check_parser(settings)
    check_sheets(settings)
    check_bitrix(settings)
    check_hh(settings)

    section("Итог")
    if settings.dry_run:
        line(OK, "Пробный режим включён — наружу ничего не пишется")
    else:
        line(NO, "Пробный режим выключен — бот пишет в таблицу и Битрикс по-настоящему")

    if not problems:
        print("\nВсё, что подключено, работает. Можно запускать: start.bat")
        return 0

    print(f"\nНашлось проблем: {len(problems)}")
    for number, text in enumerate(problems, 1):
        print(f"  {number}. {text}")
    print(f"\nСтроки {SKIP} — это не ошибки, а неподключённые сервисы:")
    print("бот работает и без них. Чинить нужно только то, что выше.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
