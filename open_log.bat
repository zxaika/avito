@echo off
chcp 65001 >nul
set LOGDIR=%USERPROFILE%\.avito_desktop\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i
set LOGFILE=%LOGDIR%\avito_%TODAY%.log

if not exist "%LOGFILE%" (
    echo.>"%LOGFILE%"
)

start "" notepad "%LOGFILE%"
