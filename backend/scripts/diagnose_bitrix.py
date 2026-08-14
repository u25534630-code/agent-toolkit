"""Почему Битрикс отвечает «Access denied» — разбор по шагам.

    python -m scripts.diagnose_bitrix

Отказ выглядит одинаково в трёх разных случаях: у вебхука нет галочки CRM,
у пользователя закрыта CRM целиком, у него закрыто только добавление. Лечатся
они в разных местах, поэтому сначала нужно понять, какой это случай.

Сначала идут только читающие запросы — они ничего не меняют. В конце скрипт
предлагает создать проверочную сделку и сразу удалить её: право на запись
по-другому не проверяется, но делается это с вашего разрешения и с уборкой
за собой.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx

from app.config import get_settings

TEST_TITLE = "Проверка прав бота"


def _short(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


class Portal:
    def __init__(self, base: str) -> None:
        self._base = base
        self._client = httpx.Client(timeout=25.0)

    def call(self, method: str, payload: dict | None = None) -> tuple[bool, Any]:
        """(получилось, результат или текст отказа)."""
        try:
            response = self._client.post(self._base + method, json=payload or {})
            data = response.json()
        except Exception as error:  # noqa: BLE001
            return False, f"нет связи: {error}"
        if "error" in data:
            return False, data.get("error_description") or data.get("error") or "отказ"
        return True, data.get("result")

    def close(self) -> None:
        self._client.close()


def _is_denied(text: Any) -> bool:
    lowered = str(text).lower()
    return "denied" in lowered or "доступ" in lowered or "прав" in lowered


def main() -> None:
    settings = get_settings()
    if not settings.bitrix_configured:
        print("BITRIX_WEBHOOK_URL не заполнен — проверять нечего.")
        return

    portal = Portal(settings.bitrix_webhook_url)
    try:
        _run(portal, settings)
    finally:
        portal.close()


def _run(portal: Portal, settings) -> None:
    print("\n=== 1. Кто работает от имени вебхука ===\n")
    ok, profile = portal.call("profile")
    if not ok:
        print(f"  Битрикс не принял вебхук: {_short(profile)}")
        print("\n  Ссылка вебхука неверна, отозвана или портал недоступен.")
        print("  Дальше проверять нечего — начните с ссылки в .env.")
        return

    profile = profile or {}
    who = f"{profile.get('NAME', '')} {profile.get('LAST_NAME', '')}".strip()
    print(f"  Пользователь: {who or 'без имени'} (id {profile.get('ID')})")

    ok, is_admin = portal.call("user.admin")
    admin = bool(ok and is_admin)
    print(f"  Администратор портала: {'да' if admin else 'нет'}")

    ok, scope = portal.call("scope")
    granted = [s for s in (scope or []) if s] if ok else []
    print(f"  Права вебхука: {', '.join(granted) or 'не удалось узнать'}")
    if "crm" not in granted:
        print("\n  >>> Причина найдена: у вебхука нет галочки CRM.")
        print("      Разработчикам -> Другое -> Входящий вебхук -> ваш вебхук,")
        print("      поставить CRM и нажать «Сохранить».")
        return

    print("\n=== 2. Что вебхук может прочитать ===\n")
    checks = (
        ("сделки", "crm.deal.list", {"select": ["ID"], "start": 0}),
        ("контакты", "crm.contact.list", {"select": ["ID"], "start": 0}),
        ("воронки", "crm.dealcategory.list", {}),
    )
    reads: dict[str, bool] = {}
    for human, method, payload in checks:
        ok, answer = portal.call(method, payload)
        reads[human] = ok
        print(f"  {human:<10} {'читаются' if ok else 'отказ: ' + _short(answer, 120)}")

    if not reads.get("сделки"):
        print("\n  >>> CRM закрыта для этого пользователя целиком:")
        print("      не только запись, но и чтение.")
        print("      CRM -> Настройки -> Права доступа -> Права доступа:")
        print(f"      роль, в которой состоит {who or 'пользователь вебхука'},")
        print("      должна иметь доступ к Сделкам и Контактам.")
        return

    print("\n=== 3. Может ли вебхук создать сделку ===\n")
    print("  Читающие запросы прошли — значит дело в правах на добавление.")
    print("  Проверить это можно только записью: заведём одну сделку")
    print(f"  с названием «{TEST_TITLE}» и сразу удалим её.\n")

    if not sys.stdin.isatty():
        print("  Ввод недоступен — запустите rights.bat и ответьте на вопрос.")
        return

    answer = input("  Создать проверочную сделку? (д/н): ").strip().lower()
    if answer not in ("д", "да", "y", "yes"):
        print("\n  Хорошо, ничего не создаю.")
        return

    fields: dict[str, Any] = {"TITLE": TEST_TITLE}
    if settings.bitrix_deal_category_id:
        fields["CATEGORY_ID"] = settings.bitrix_deal_category_id

    ok, created = portal.call("crm.deal.add", {"fields": fields})
    if not ok:
        print(f"\n  Отказ: {_short(created)}")
        if _is_denied(created):
            print("\n  >>> Причина найдена: читать CRM пользователь может,")
            print("      создавать сделки — нет.")
            print("      CRM -> Настройки -> Права доступа -> Права доступа,")
            print(f"      роль пользователя {who or ''}: «Добавление» -> Разрешено")
            print("      для Сделок (и для Контактов — там та же история).")
            if admin:
                print("\n      Обратите внимание: пользователь — администратор")
                print("      портала, а ему CRM обычно не отказывает. Стоит")
                print("      заодно проверить сам вебхук: не пересоздавали ли")
                print("      его — при этом права сбрасываются.")
        else:
            print("\n  Это не отказ в правах, а другая ошибка — покажите её строку.")
        return

    print(f"  Сделка создана: #{created} — значит, права на запись есть.")
    ok, removed = portal.call("crm.deal.delete", {"id": created})
    if ok:
        print("  Проверочная сделка удалена, следов не осталось.")
    else:
        print(f"  Удалить не смог: {_short(removed)}")
        print(f"  Удалите сделку #{created} руками — она называется «{TEST_TITLE}».")

    print("\n  >>> Права в порядке. Если бот всё равно получает отказ,")
    print("      значит .env смотрит на другой вебхук: сверьте ссылку")
    print("      BITRIX_WEBHOOK_URL с той, что в настройках вебхука.")


if __name__ == "__main__":
    asyncio.run(asyncio.to_thread(main))
