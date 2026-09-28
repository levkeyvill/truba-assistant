@echo off
rem Truba installer. ASCII only: cyrillic in a .bat file breaks the encoding.
rem Double-click this file, or run it with keys: -Voices, -NoLaunch, -Repair.
setlocal
cd /d "%~dp0"
title Truba - installing
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" %*
if errorlevel 1 (
    echo.
    echo   Installation failed. See the message above and data\install.log
    echo.
    pause
)
endlocal
