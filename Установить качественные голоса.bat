@echo off
rem Truba installer: quality voices (Higgs, ESpeech), NVIDIA only. ASCII only.
setlocal
cd /d "%~dp0"
title Truba - installing quality voices
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" -Voices %*
if errorlevel 1 (
    echo.
    echo   Installation failed. See the message above and data\install.log
    echo.
    pause
)
endlocal
