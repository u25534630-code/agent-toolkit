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
import sys
from pathlib import Path
from urllib.parse import urlencode

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
        "3. Вас перебросит на redirect_uri, в адресной строке появится ?code=...\n"
        "   Скопируйте значение code — только его, без остального адреса.\n"
    )

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
        print("\nГотово. Записал в .env:\n")
        for key, value in values.items():
            shown = value if key in ("HH_CLIENT_ID", "HH_EMPLOYER_ID") else "*" * 12
            print(f"  {key}={shown}")
        print("\nПерезапустите бота: он начнёт забирать отклики каждые 15 минут.")
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
