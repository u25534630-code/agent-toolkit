"""Авторизация работодателя на hh.ru: получить access_token и refresh_token.

hh.ru не выдаёт токен работодателя кнопкой в интерфейсе — нужно пройти OAuth:
приложение отправляет вас на страницу входа, вы разрешаете доступ, hh.ru
возвращает одноразовый код, а код меняется на пару токенов. Руками это
неудобно, поэтому скрипт делает всё, кроме нажатия «Разрешить».

    python -m scripts.hh_auth

Перед запуском нужно зарегистрировать приложение на https://dev.hh.ru/admin
и знать его client_id и client_secret. Redirect URI при регистрации укажите
ровно тот же, что введёте здесь.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from scripts.setup_bitrix import write_env

USER_AGENT = "recruiter-bot/1.0 (bitrix-hh-integration)"
AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = "https://api.hh.ru/token"
ME_URL = "https://api.hh.ru/me"


def ask(prompt: str, secret: bool = False) -> str:
    value = input(prompt).strip()
    if not value:
        print("Пустое значение, попробуйте ещё раз.")
        return ask(prompt, secret)
    return value


class _CodeCatcher(BaseHTTPRequestHandler):
    """Одноразовый обработчик: принимает редирект и забирает из него code."""

    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
        query = parse_qs(urlparse(self.path).query)
        _CodeCatcher.code = (query.get("code") or [None])[0]
        _CodeCatcher.error = (query.get("error_description") or query.get("error") or [None])[0]

        body = (
            "<h2>Готово, можно закрыть вкладку.</h2>"
            if _CodeCatcher.code
            else f"<h2>hh.ru не дал код.</h2><p>{_CodeCatcher.error or ''}</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body>{body}</body></html>".encode())

    def log_message(self, *args) -> None:
        pass  # не засорять вывод строкой лога веб-сервера


def catch_code(redirect_uri: str, timeout: int = 300) -> str | None:
    """Поймать код прямо из редиректа, если redirect_uri ведёт на этот компьютер.

    Иначе человеку нужно выцепить code из адресной строки браузера — а
    браузеры показывают адрес свёрнутым, и в него ещё попадает лишнее.
    """
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return None

    port = parsed.port or 80
    try:
        server = HTTPServer(("127.0.0.1", port), _CodeCatcher)
    except OSError as error:
        print(f"  (порт {port} занят: {error} — придётся скопировать код руками)")
        return None

    server.timeout = timeout
    _CodeCatcher.code = None
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print(f"  Жду ответа от hh.ru на порту {port}. Нажмите «Разрешить» в браузере…")
    thread.join(timeout)
    server.server_close()

    if _CodeCatcher.error:
        print(f"  hh.ru вернул отказ: {_CodeCatcher.error}")
    return _CodeCatcher.code


async def exchange_code(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            headers={"User-Agent": USER_AGENT},
        )
    if response.status_code >= 400:
        print("\nhh.ru отклонил код:")
        print(response.text[:600])
        print(
            "\nЧастые причины:\n"
            "  · redirect_uri не совпадает с указанным при регистрации приложения\n"
            "  · код уже использован или устарел — он живёт несколько минут\n"
            "  · перепутаны client_id и client_secret"
        )
        sys.exit(1)
    return response.json()


async def whoami(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            ME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": USER_AGENT,
            },
        )
    response.raise_for_status()
    return response.json()


async def main() -> None:
    print(
        "\nАвторизация работодателя на hh.ru\n"
        "Приложение регистрируется на https://dev.hh.ru/admin\n"
    )

    client_id = ask("client_id приложения: ")
    client_secret = ask("client_secret приложения: ")
    redirect_uri = ask(
        "redirect_uri (ровно как при регистрации, например https://example.com/): "
    )

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
    )
    print(
        "\n1. Откройте ссылку в браузере и войдите под аккаунтом работодателя:\n\n"
        f"   {AUTHORIZE_URL}?{query}\n\n"
        "2. Нажмите «Разрешить».\n"
    )

    code = catch_code(redirect_uri)
    if code:
        print("  Код получен автоматически.")
    else:
        code = ask("code из адресной строки: ")
    tokens = await exchange_code(client_id, client_secret, redirect_uri, code)

    employer_id = ""
    try:
        me = await whoami(tokens["access_token"])
        employer_id = str((me.get("employer") or {}).get("id") or "")
        if employer_id:
            print(f"\nРаботодатель: {(me.get('employer') or {}).get('name')}")
    except Exception:
        print("\nНе удалось определить employer_id — впишите его вручную.")

    values = {
        "HH_CLIENT_ID": client_id,
        "HH_CLIENT_SECRET": client_secret,
        "HH_ACCESS_TOKEN": tokens["access_token"],
        "HH_REFRESH_TOKEN": tokens.get("refresh_token", ""),
        "HH_EMPLOYER_ID": employer_id,
    }

    # Токены длиной в сотню символов переносят с ошибками, а ошибка вылезет
    # через час в виде «hh.ru не принял токен». Пишем сами.
    env_path = Path(".env")
    if env_path.exists():
        write_env(values, env_path)

        # Перечитываем файл: «записал» должно означать «лежит в файле»,
        # иначе о потере узнаёшь через сутки от бота
        saved = {}
        for row in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in row and not row.strip().startswith("#"):
                key, _, value = row.partition("=")
                saved[key.strip()] = value.strip()

        print("\nГотово. Записал в .env:\n")
        lost = []
        for key, value in values.items():
            if saved.get(key) != value:
                lost.append(key)
                print(f"  {key} — НЕ ЗАПИСАЛОСЬ")
                continue
            shown = value if key in ("HH_CLIENT_ID", "HH_EMPLOYER_ID") else "*" * 12
            print(f"  {key}={shown}")

        if lost:
            print(
                "\nЧасть значений не сохранилась: " + ", ".join(lost) + "\n"
                f"Впишите их в {env_path.resolve()} вручную."
            )
        else:
            print(
                "\nПерезапустите бота: он заберёт отклики сразу и дальше "
                "будет ходить за ними в часы из HH_POLL_TIMES."
            )
    else:
        print("\n.env рядом не нашёлся — запускайте из папки backend.")
        print("Вставьте это в backend/.env вручную:\n")
        for key, value in values.items():
            print(f"{key}={value}")

    print(
        "\nТокен живёт около двух недель. Бот обновляет его сам по refresh_token,\n"
        "но в .env не пишет — при частых перезапусках впишите свежие значения руками."
    )


if __name__ == "__main__":
    asyncio.run(main())
