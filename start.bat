@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

echo Запуск Avito Desktop Manager...
echo Лог: %USERPROFILE%\.avito_desktop\logs\
echo.

python run_app.py
if errorlevel 1 (
    echo.
    echo Ошибка запуска. Откройте лог: %USERPROFILE%\.avito_desktop\logs\
    echo Или выполните: open_log.bat
    pause
)
