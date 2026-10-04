@echo off
title Razreshit telefonu podklyuchatsya k Trube
echo.
echo   Eto skriptvoy iz papki Truby, na kotoroy zapushchen pul't.
echo.

rem Port odin u vsekh kopiy Truby: rabotaet odna, i ona zhe otkryvaet
rem etot port telefonu. Novyh pravil ne sozdaem.
set PORT=8765
echo   Port Truby: %PORT%
echo.
echo   Dobavlyaet odno pravilo v brandmauer Windows:
echo.
echo     - vhodyashchie podklyucheniya
echo     - tolko port %PORT%
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

rem Staroe pravilo dlya etogo zhe porta udalyom, inache netsh skazhet,
rem chto pravilo s takim imenem uzhe est.
netsh advfirewall firewall delete rule name="Truba - telefon %PORT%" >nul 2>&1
netsh advfirewall firewall add rule name="Truba - telefon %PORT%" dir=in action=allow protocol=TCP localport=%PORT% profile=private

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
