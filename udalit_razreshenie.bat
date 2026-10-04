@echo off
title Ubrat razreshenie dlya telefona
echo.
echo   Udalyaet pravilo "Truba - telefon" iz brandmauera.
echo   Posle etogo telefon perestanet podklyuchatsya.
echo.
pause

net session >nul 2>&1
if errorlevel 1 (
    echo   Nuzhny prava administratora:
    echo   pravoy knopkoy po faylu - "Zapusk ot imeni administratora"
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="Truba - telefon"
echo.
pause
