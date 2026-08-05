#!/usr/bin/env bash
# Проверка настроек на macOS и Linux. Windows — check.bat
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f ".venv/.installed" ]; then
    echo "Окружение ещё не готово. Сначала запустите ./start.sh"
    exit 1
fi

source .venv/bin/activate
exec python -m scripts.check
