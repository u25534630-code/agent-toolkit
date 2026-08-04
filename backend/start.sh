#!/usr/bin/env bash
# Запуск бота на macOS и Linux. Windows — start.bat
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
    echo "Python 3 не найден. Установите Python 3.11-3.13 с python.org/downloads"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Первый запуск: готовлю окружение. Это займёт несколько минут."
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

if [ ! -f ".env" ]; then
    echo
    echo "Файл настроек не найден, запускаю мастер."
    echo
    python -m scripts.setup_env
    [ -f ".env" ] || { echo "Настройка не завершена."; exit 1; }
fi

echo
echo "Запускаю. Бот работает, пока открыто это окно. Остановить — Ctrl+C"
echo
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
