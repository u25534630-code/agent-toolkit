@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================
echo   Рекрутинговый ассистент
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден.
    echo.
    echo Установите Python 3.13 с python.org/downloads/windows
    echo Берите "установщик Windows (64-разрядная версия)".
    echo При установке обязательно отметьте "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Первый запуск: готовлю окружение. Это займёт несколько минут.
    echo.
    python -m venv .venv
    if errorlevel 1 (
        echo Не удалось создать окружение.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo Устанавливаю библиотеки...
    python -m pip install --upgrade pip --quiet
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Не удалось установить библиотеки. Проверьте интернет.
        pause
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

if not exist ".env" (
    echo.
    echo Файл настроек не найден, запускаю мастер.
    echo.
    python -m scripts.setup_env
    if not exist ".env" (
        echo Настройка не завершена.
        pause
        exit /b 1
    )
)

echo.
echo Запускаю. Первый раз загрузится модель распознавания речи —
echo несколько минут, дальше будет быстро.
echo.
echo Бот работает, пока открыто это окно. Чтобы остановить — Ctrl+C
echo или просто закройте окно.
echo.

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
