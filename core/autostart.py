r"""Автозапуск Трубы вместе с Windows: ярлык в папке автозагрузки.

Состояние намеренно не хранится в `settings.json`. У Windows один способ
узнать, что запускать при входе, — папка автозагрузки, и никакой другой
след не остаётся. Поэтому истина — наличие нашего ярлыка, а пульту после
`enable()`/`disable()` отдаётся то, что лежит в папке на самом деле.

Ярлык один и наш: `Труба.lnk` с `/tray`, чтобы при входе в Windows пульт
поднимался в трее, а не выскакивал окном поверх всего. Чужие ярлыки в
той же папке не трогаем: своё отключение отключает только своё.

Ярлык создаётся через `WScript.Shell` (pywin32) — так же, как это делает
сам Windows для программ в меню «Пуск».
"""

import os
from pathlib import Path

import config

# Папка автозагрузки пользователя. Тесты подменяют её на временную, а на
# живой машине она всегда одна и та же.
STARTUP_DIR = (Path(os.environ.get("APPDATA") or "")
               / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")
ИМЯ_ЯРЛЫКА = "Труба.lnk"
ИМЯ_СКРИПТА = "Труба.vbs"


def корень() -> Path:
    return config.ROOT


def путь_ярлыка() -> Path:
    return STARTUP_DIR / ИМЯ_ЯРЛЫКА


def путь_скрипта() -> Path:
    return корень() / ИМЯ_СКРИПТА


def цель() -> str:
    """Что запускает ярлык: системный wscript, он же открывает .vbs."""
    return str(Path(os.environ.get("SystemRoot") or r"C:\Windows")
               / "System32" / "wscript.exe")


def аргументы() -> str:
    """Аргументы ярлыка: сам скрипт и /tray — пульт сразу в трей."""
    return f'"{путь_скрипта()}" /tray'


def значок() -> str:
    """Значок ярлыка, если в проекте есть .ico. Нет — ярлык без значка."""
    for путь in sorted(корень().glob("*.ico")):
        return str(путь)
    return ""


def _оболочка():
    import win32com.client

    return win32com.client.Dispatch("WScript.Shell")


def _записать(путь: Path, target: str, args: str, workdir: str,
              icon: str) -> None:
    """Создаёт .lnk. Отдельно от enable(), чтобы тесты подменяли это."""
    ярлык = _оболочка().CreateShortcut(str(путь))
    ярлык.TargetPath = target
    ярлык.Arguments = args
    ярлык.WorkingDirectory = workdir
    if icon:
        ярлык.IconLocation = icon
    ярлык.Save()


def _прочитать(путь: Path) -> dict:
    """Читает .lnk: WScript.Shell умеет и открывать готовый ярлык."""
    ярлык = _оболочка().CreateShortcut(str(путь))
    return {"target": str(ярлык.TargetPath or ""),
            "arguments": str(ярлык.Arguments or ""),
            "workdir": str(ярлык.WorkingDirectory or "")}


def enabled() -> bool:
    """Включён ли автозапуск: ярлык есть и указывает на эту копию Трубы.

    Никогда не бросает: галочка в пульте должна показывать состояние, а
    не ронять пульт, если COM или папка автозагрузки недоступны.
    """
    путь = путь_ярлыка()
    if not путь.is_file():
        return False
    try:
        поля = _прочитать(путь)
    except Exception:
        return False
    return str(путь_скрипта()).lower() in поля["arguments"].lower()


def enable() -> None:
    """Кладёт ярлык в папку автозагрузки. Ошибка — текстом, не падать."""
    путь = путь_ярлыка()
    try:
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)
        _записать(путь, цель(), аргументы(), str(корень()), значок())
    except Exception as exc:
        raise ValueError(f"ярлык автозапуска не создался: {exc}") from exc
    if not путь.is_file():
        raise ValueError("ярлык автозапуска не появился в папке автозагрузки")


def disable() -> None:
    """Убирает только наш `Труба.lnk`. Чужое в папке не трогаем."""
    путь = путь_ярлыка()
    try:
        путь.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"ярлык автозапуска не убрался: {exc}") from exc
