r"""Снимок экрана компа: на диск в полном размере и на телефон помельче.

Телефон стоит рядом с монитором, и заглянуть в него быстрее, чем
сворачивать игру. Поэтому снимок уходит сразу в двух видах: полный PNG
ложится в папку с картинками, а сжатая копия летит на экран телефона.

Мониторов два, и снимать «весь рабочий стол» смысла нет: склейка выходит
3840 на 1080, и на телефоне это нечитаемая полоска. Берём один монитор.
"""

from __future__ import annotations

import ctypes
import io
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

# Ширина копии для телефона. При 1280 кадр Full HD весит около 80 КБ —
# по домашнему Wi-Fi это мгновение, а разглядеть на экране уже можно.
PHONE_WIDTH = 1280
PHONE_QUALITY = 70

_FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"


class _Guid(ctypes.Structure):
    _fields_ = [
        ("d1", wintypes.DWORD),
        ("d2", wintypes.WORD),
        ("d3", wintypes.WORD),
        ("d4", ctypes.c_byte * 8),
    ]


def known_folder(folder_id: str, fallback: Path) -> Path:
    """Папка Known Folders по её опознавателю — так, как её знает сама Windows.

    Спрашиваем систему, а не склеиваем путь руками: любую из этих папок могли
    перенести на другой диск, и тогда наш путь вёл бы в никуда. `fallback` —
    на случай, если система не ответила (в тестах Known Folders подменяют).

    Общая функция одна на всё: `core/folders.py` спрашивает здесь же, своими
    копиями вызовов Windows со временем разъехались бы.
    """
    try:
        guid = _Guid()
        ctypes.windll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(out)
        ) == 0 and out.value:
            return Path(out.value)
    except Exception:
        pass
    return Path(fallback)


def pictures_dir() -> Path:
    """Папка «Изображения» так, как её знает сама Windows."""
    return known_folder(_FOLDERID_PICTURES, Path.home() / "Pictures")


def shots_dir() -> Path:
    return pictures_dir() / "Труба"


def monitors() -> list[dict]:
    """Мониторы с их границами. Основной — первым."""
    import win32api
    import win32con

    found = []
    for handle, _hdc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(handle)
        found.append(
            {
                "name": info["Device"],
                "box": tuple(info["Monitor"]),
                "primary": bool(info["Flags"] & win32con.MONITORINFOF_PRIMARY),
            }
        )
    found.sort(key=lambda m: not m["primary"])
    return found


def _box_for(which: str | int):
    """Границы нужного монитора. None — снимать весь рабочий стол."""
    screens = monitors()
    if not screens:
        return None

    if which == "all":
        return None
    if isinstance(which, int):
        return screens[which]["box"] if 0 <= which < len(screens) else screens[0]["box"]
    if which == "second":
        return screens[1]["box"] if len(screens) > 1 else screens[0]["box"]
    return screens[0]["box"]


def grab(which: str | int = "primary"):
    """Снимает монитор. Возвращает картинку PIL."""
    from PIL import ImageGrab

    box = _box_for(which)
    # all_screens нужен даже для одного монитора: без него второй экран,
    # который лежит в отрицательных координатах, просто не существует.
    return ImageGrab.grab(bbox=box, all_screens=True)


def save(image) -> Path:
    """Кладёт полный снимок в папку с картинками."""
    folder = shots_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    image.save(target, "PNG")
    return target


def as_data_url(image) -> str:
    """Сжатая копия для экрана телефона."""
    copy = image.copy()
    copy.thumbnail((PHONE_WIDTH, PHONE_WIDTH))
    buffer = io.BytesIO()
    copy.convert("RGB").save(buffer, "JPEG", quality=PHONE_QUALITY)

    import base64

    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def recent(count: int = 3) -> list[tuple[Path, str]]:
    """Последние сохранённые Трубой снимки, включая прошлые запуски."""
    from PIL import Image

    try:
        paths = sorted(shots_dir().glob("????-??-??_??-??-??.png"),
                       reverse=True)[:count]
    except OSError:
        return []
    found = []
    for path in reversed(paths):
        try:
            with Image.open(path) as picture:
                found.append((path, as_data_url(picture)))
        except OSError:
            continue  # один битый кадр не прячет остальные
    return found


def take(which: str | int = "primary") -> tuple[Path, str]:
    """Снимает, сохраняет и готовит картинку для телефона."""
    image = grab(which)
    return save(image), as_data_url(image)
