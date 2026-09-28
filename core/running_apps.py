"""Что сейчас запущено на компьютере — для кнопки «Из запущенных…».

Хозяин 28 сентября сказал прямо: чтобы добавить программу, не нужно искать
файл на диске C — покажи то, что уже запущено, выбрал — и кнопка готова.

Список собирается из окон верхнего уровня (то, что человек видит на экране),
а не из всех процессов: служебных окон и системных окон тут не место.

Всё в `try` намеренно: программа закрылась между перечислением окон и
чтением пути — это обычное дело, а список из-за одного такого окна
пропадать не должен.
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

import psutil

from core import app_icons, launcher

# Сколько строк показываем. Больше — не список, а простыня.
MAX_ITEMS = 60
# Название на кнопке телефона длиннее не помещается.
TITLE_LIMIT = 60

# Служебные и системные окна. Пользователь их не запускал, а кнопка «закрыть
# Проводник» ему не нужна. И сам пульт с телефоном тоже: у пульта своя кнопка.
SKIP_NAMES = frozenset({
    "msedgewebview2.exe", "explorer.exe", "applicationframehost.exe",
    "textinputhost.exe", "systemsettings.exe", "shellexperiencehost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe",
})
# Приложения из Магазина лежат в защищённой папке, и запустить их файлом
# нельзя — значит, и предлагать их нельзя.
STORE_MARK = "\\windowsapps\\"

# Окно не программа, если у него есть владелец (диалог, меню, всплывашка),
# если оно служебное или если его «спрятали» средствами Windows (свернули
# в трей). Такое окно человек не открывал, чтобы пользоваться программой.
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
DWMWA_CLOAKED = 14


def _спрятано(hwnd: int) -> bool:
    """Окно спрятано средствами Windows: свёрнуто в трей или «забыто»."""
    try:
        значение = wintypes.DWORD()
        результат = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED,
            ctypes.byref(значение), ctypes.sizeof(значение))
    except Exception:
        return False
    return результат == 0 and значение.value != 0


def _windows() -> list:
    """Видимые окна верхнего уровня: список `(handle, заголовок, pid)`.

    Обход `EnumWindows`, а не `psutil`: заголовок окна знает только Windows,
    а по заголовку хозяин и узнаёт программу.
    """
    user32 = ctypes.windll.user32
    found: list = []

    def обойти(hwnd, _счётчик):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            длина = user32.GetWindowTextLengthW(hwnd)
            if длина <= 0:
                return True
            буфер = ctypes.create_unicode_buffer(длина + 1)
            user32.GetWindowTextW(hwnd, буфер, длина + 1)
            заголовок = буфер.value.strip()
            if not заголовок:
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            if _спрятано(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                found.append((int(hwnd), заголовок, int(pid.value)))
        except Exception:
            pass
        return True

    обход = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(обойти)
    user32.EnumWindows(обход, 0)
    return found



def _own_pids() -> set:
    """Наш процесс и его дети: пульт, сервер, окно Трубы.

    В списке им не место: хозяин не открывал Трубу, чтобы пользоваться Трубой.
    """
    свои = {os.getpid()}
    try:
        свои |= {процесс.pid for процесс in
                 psutil.Process(os.getpid()).children(recursive=True)}
    except Exception:
        pass
    return свои


def _описание(exe: str) -> str:
    """«Описание файла» из сведений exe. Нет сведений — имя файла.

    Имя файла у Discord — `Discord.exe`, а в описании «Discord», и хозяин
    называет кнопку словом, а не расширением.
    """
    имя = Path(exe).stem
    try:
        import win32api

        перевод = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")
        язык, кодировка = перевод[0]
        текст = win32api.GetFileVersionInfo(
            exe, "\\StringFileInfo\\%04x%04x\\FileDescription" % (язык, кодировка))
        if isinstance(текст, str) and текст.strip():
            return текст.strip()[:TITLE_LIMIT]
    except Exception:
        pass
    return имя[:TITLE_LIMIT]


def _лаунчер(exe: str):
    r"""Программа с обновлением: `(путь, аргументы, способ запуска)` или None.

    Discord, Slack и подобные лежат в `…\Discord\app-1.0.9\Discord.exe`, а
    обновление стирает папку версии. Прямой путь завтра перестанет существовать,
    поэтому запускаем через `Update.exe --processStart` — так уже записано
    в `apps.json`, и это единственный путь, переживающий обновление.
    """
    try:
        папка = Path(exe).parent
        if not папка.name.lower().startswith("app-"):
            return None
        обновлятор = папка.parent / "Update.exe"
        if not обновлятор.is_file():
            return None
        # Только имя файла: полный путь ведёт в `app-1.0.9…`, а её обновление
        # сотрёт — ровно то, от чего `Update.exe` и спасает.
        return str(обновлятор), ["--processStart", Path(exe).name], "shell"
    except Exception:
        return None


def _имя_файла(значение) -> str:
    """Имя exe из строки, как бы она ни была записана."""
    текст = str(значение or "").strip().strip('"')
    if not текст:
        return ""
    return Path(текст.replace("\\", "/")).name.lower()


def _уже_есть(имя_exe: str) -> bool:
    """Есть ли такая программа в текущем списке (`apps.json`).

    Сравниваем имя exe без учёта регистра по `process`, `path` и аргументу
    `--processStart`: у Discord в `path` лежит `Update.exe`, а работает
    `Discord.exe`, и без аргумента мы решили бы, что программа не добавлена.
    """
    if not имя_exe:
        return False
    for запись in launcher.read_list():
        if not isinstance(запись, dict):
            continue
        кандидаты = [_имя_файла(запись.get("process")),
                     _имя_файла(запись.get("path"))]
        аргументы = запись.get("args")
        if isinstance(аргументы, list):
            for номер, аргумент in enumerate(аргументы):
                if (str(аргумент) == "--processStart"
                        and номер + 1 < len(аргументы)):
                    кандидаты.append(_имя_файла(аргументы[номер + 1]))
        if имя_exe.lower() in [имя for имя in кандидаты if имя]:
            return True
    return False


def _значок(exe: str, номер: int) -> str:
    """Значок программы строкой data URL. Не получилось — пустая строка."""
    try:
        picture = app_icons.picture_for(
            {"id": "running-%d" % номер, "icon_source": "exe", "path": exe})
        if picture is None:
            return ""
        return app_icons.as_data_url(picture) or ""
    except Exception:
        return ""


def list_running() -> list:
    """Запущенные программы для пульта: одна строка на exe, по названию.

    Строки: `title`, `path`, `args`, `how`, `process`, `already`, `icon`.
    """
    свои = _own_pids()
    готово: dict = {}
    for _handle, _заголовок, pid in _windows():
        try:
            if pid in свои:
                continue
            # У psutil имя и путь — методы, а не поля. Первая версия брала поле
            # и получала «<bound method …>» (core/ducking.py — та же грабля).
            exe = str(psutil.Process(pid).exe() or "").strip()
            имя = Path(exe).name
            if not имя:
                continue
            if имя.lower() in SKIP_NAMES:
                continue
            if STORE_MARK in exe.lower():
                continue
            ключ = exe.lower()
            if ключ in готово:
                continue
            запуск = _лаунчер(exe)
            if запуск:
                путь, аргументы, способ = запуск
            else:
                путь, аргументы, способ = exe, [], "shell"
            готово[ключ] = {
                "title": _описание(exe),
                "path": путь,
                "args": аргументы,
                "how": способ,
                "process": имя,
                "already": _уже_есть(имя),
                "icon": "",
                # Настоящий exe для значка: у лаунчера в `path` лежит
                # `Update.exe`, а логотип нужен от самой программы. Поле
                # служебное, в ответе его не будет.
                "_exe": exe,
            }
        except Exception:
            # Окно закрылось, процесс защищён, exe не прочитался — идём дальше.
            continue
    строки = sorted(готово.values(), key=lambda строка: строка["title"].lower())
    for номер, строка in enumerate(строки):
        строка["icon"] = _значок(строка.pop("_exe"), номер)
    return строки[:MAX_ITEMS]
