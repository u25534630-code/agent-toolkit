"""Заменить ссылку вебхука в .env — с проверкой до записи.

    python -m scripts.set_webhook

Спрашивает новую ссылку, проверяет её на портале (кто за ней стоит,
администратор ли, какие права, читается ли CRM) и только потом пишет в .env.
Ссылка, которая не работает, до файла не доходит: иначе о подмене узнаёшь
через час, когда бот молча перестанет заводить кандидатов.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from app.config import get_settings
from scripts.setup_bitrix import write_env


def probe(url: str) -> bool:
    """Показать, что за вебхуком, и сказать, годится ли он."""
    client = httpx.Client(timeout=25.0)
    try:
        try:
            profile = client.post(url + "profile", json={}).json()
        except Exception as error:  # noqa: BLE001
            print(f"  Портал не ответил: {error}")
            return False

        if "error" in profile:
            print(
                "  Битрикс не принял ссылку: "
                f"{profile.get('error_description') or profile['error']}"
            )
            return False

        result = profile.get("result") or {}
        who = f"{result.get('NAME', '')} {result.get('LAST_NAME', '')}".strip()
        admin = result.get("ADMIN")
        print(f"  Пользователь: {who or 'без имени'} (id {result.get('ID')})")
        print(f"  Администратор портала: {'да' if admin else 'нет'}")

        scope = client.post(url + "scope", json={}).json().get("result") or []
        print(f"  Права вебхука: {', '.join(s for s in scope if s) or 'пусто'}")
        if "crm" not in scope:
            print("\n  Нет права crm — в настройках вебхука поставьте галочку CRM")
            print("  и нажмите «Сохранить», иначе бот не сможет работать с CRM.")
            return False

        deals = client.post(url + "crm.deal.list", json={"select": ["ID"]}).json()
        if "error" in deals:
            print(
                "\n  CRM закрыта для этого пользователя: "
                f"{deals.get('error_description') or deals['error']}"
            )
            print("  Такой вебхук боту не подойдёт — нужен от того, у кого есть")
            print("  доступ к сделкам и контактам (обычно администратор).")
            return False

        print("  Сделки читаются — доступ к CRM есть.")
        return True
    finally:
        client.close()


def main() -> None:
    if not Path(".env").exists():
        print("Файл .env не найден — запускать нужно из папки backend.")
        return

    current = get_settings().bitrix_webhook_url
    print("\nСейчас в настройках:")
    # Целиком не печатаем: ссылка работает как пароль, а окно могут снимать
    print(f"  {current[:40] + '…' if current else 'ссылки нет'}\n")

    print("Новая ссылка выглядит так:")
    print("  https://ваш-портал.bitrix24.ru/rest/1/xxxxxxxxxxxx/\n")

    if not sys.stdin.isatty():
        print("Ввод недоступен — запустите webhook.bat.")
        return

    url = input("Вставьте ссылку (правая кнопка мыши — Вставить): ").strip()
    if not url:
        print("Пусто, ничего не меняю.")
        return

    url = url.strip('"\'')
    if not url.startswith("https://") or "/rest/" not in url:
        print("\nЭто не похоже на ссылку вебхука: в ней должно быть /rest/.")
        return
    if not url.endswith("/"):
        # Битрикс склеивает адрес с именем метода — без слэша получается мусор
        url += "/"

    print("\nПроверяю ссылку…\n")
    if not probe(url):
        print("\nВ .env ничего не записал: эта ссылка боту не подойдёт.")
        return

    answer = input("\nЗаписать эту ссылку в настройки? (д/н): ").strip().lower()
    if answer not in ("д", "да", "y", "yes"):
        print("Ничего не трогаю.")
        return

    write_env({"BITRIX_WEBHOOK_URL": url})
    print("\nГотово. Перезапустите бота: закройте его окно и запустите start.bat.")


if __name__ == "__main__":
    main()
