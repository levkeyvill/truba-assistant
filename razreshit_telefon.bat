@echo off
title Razreshit telefonu podklyuchatsya k Trube
echo.
echo   Dobavlyaet odno pravilo v brandmauer Windows:
echo.
echo     - vhodyashchie podklyucheniya
echo     - tolko port 8765
echo     - tolko chastnye seti (domashniy Wi-Fi)
echo.
echo   Internet i drugie porty ne zatragivayutsya.
echo   V chuzhih setyah pravilo ne deystvuet.
echo.
pause

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   OSHIBKA: net prav administratora.
    echo.
    echo   Zakroy eto okno. Nazhmi na fayl PRAVOY knopkoy
    echo   i vyberi "Zapusk ot imeni administratora".
    echo.
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="Truba - telefon" >nul 2>&1
netsh advfirewall firewall add rule name="Truba - telefon" dir=in action=allow protocol=TCP localport=8765 profile=private

if errorlevel 1 (
    echo.
    echo   Ne poluchilos dobavit pravilo.
) else (
    echo.
    echo   GOTOVO!
    echo   Otkroy na telefone adres iz pulta i obnovi stranicu.
)
echo.
pause
