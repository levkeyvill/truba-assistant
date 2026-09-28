r"""Нажатие клавиш за человека.

Нужно ради одной вещи: у NVIDIA нет способа позвать «сохранить момент»
снаружи — ни командной строки, ни ключа в реестре, только сочетание
клавиш. Поэтому кнопка на телефоне жмёт это сочетание за хозяина.

Само сочетание не вбито в код, а читается из настроек NVIDIA: он может
поменять его в любой момент, и тогда кнопка должна поехать следом, а не
молча перестать работать.
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

SETTINGS = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "NVIDIA Corporation"
    / "NVIDIA Overlay"
    / "ShareSettings.json"
)

# Что нажимается, если настройки NVIDIA прочитать не вышло. Alt+F10 —
# её заводская комбинация для «сохранить момент».
DEFAULT_SAVE = [0x12, 0x79]

# Человеческие имена клавиш — для журнала и для подписи в пульте.
NAMES = {
    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x5B: "Win",
    0xC0: "Ё", 0x20: "Пробел",
}
for _n in range(1, 25):
    NAMES[0x6F + _n] = f"F{_n}"
for _code in range(0x30, 0x5B):
    NAMES.setdefault(_code, chr(_code))

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

# --- Разбор сочетания строкой ---------------------------------------------
#
# В apps.json и в пульте сочетание пишется человеком: «ctrl+shift+alt+m».
# Дальше его надо превратить в те же коды виртуальных клавиш, что и у NVIDIA,
# иначе одно и то же сочетание жалось бы двумя разными способами.

MODIFIERS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
# Порядок нажатия фиксирован, а не как написали: сначала те модификаторы,
# которые меняют раскладку и перехватывают клавишу, — иначе программа
# получит голую букву вместо сочетания.
MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")

# Клавиши, у которых нет своего кода в букве или цифре.
NAMED = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}

HOW = ("буква латиницей, цифра, f1–f24, space, enter, tab, esc, стрелки")


def _key(name: str) -> int | None:
    """Код одной клавиши по её имени. None — такого имени нет."""
    if name in NAMED:
        return NAMED[name]
    if len(name) == 1 and name.isascii() and name.isalpha():
        return ord(name.upper())
    if len(name) == 1 and name.isascii() and name.isdigit():
        return ord(name)
    if len(name) > 1 and name[0] == "f" and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 24:
            return 0x6F + number
    return None


def parse(text: str) -> list[int]:
    """«ctrl+shift+alt+m» → [0x11, 0x10, 0x12, 0x4D] — коды для `press`.

    Ошибка всегда с ValueError и понятным текстом: сообщение показывается
    хозяину у поля в пульте, где «не знаю такую клавишу» полезнее, чем
    «неверное значение».
    """
    if not isinstance(text, str):
        raise ValueError(f"сочетание нужно строкой, например ctrl+shift+alt+m ({HOW})")
    parts = [part.strip().lower() for part in text.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"в сочетании «{text}» пустое место: напиши ctrl+shift+alt+m")

    modifiers, keys = [], []
    for part in parts:
        code = MODIFIERS.get(part)
        if code is not None:
            if code not in modifiers:
                modifiers.append(code)
            continue
        key = _key(part)
        if key is None:
            raise ValueError(f"не знаю клавишу «{part}»: {HOW}")
        keys.append(key)

    if not keys:
        raise ValueError(f"в сочетании «{text}» нет клавиши — только модификаторы")
    if len(keys) > 1:
        raise ValueError(
            f"в сочетании «{text}» несколько клавиш, а нужна ровно одна"
        )
    order = [MODIFIERS[name] for name in MODIFIER_ORDER
             if MODIFIERS[name] in modifiers]
    return order + keys


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    # Объединение размером с самый крупный свой вариант — MOUSEINPUT,
    # это 32 байта. Если заложить меньше, вся структура INPUT выходит
    # короче сорока байт, SendInput не узнаёт её размер и молча
    # отказывается: возвращает ноль отправленных нажатий.
    _fields_ = [("ki", _KeyInput), ("padding", ctypes.c_byte * 32)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


# Проверяем на месте: цена ошибки — тихо неработающая кнопка.
assert ctypes.sizeof(_Input) == 40, f"INPUT вышел {ctypes.sizeof(_Input)} байт вместо 40"


def combo(action: str = "DVRSave") -> list[int]:
    """Читает сочетание из настроек NVIDIA.

    В файле они лежат кодами виртуальных клавиш: DVRSave — «сохранить
    момент», Screenshot — её собственный снимок экрана, RecordToggle —
    начать и закончить запись.
    """
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        keys = data["settings"]["shortcuts"].get(action)
        if keys:
            return [int(k) for k in keys]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return list(DEFAULT_SAVE) if action == "DVRSave" else []


def describe(keys: list[int]) -> str:
    return "+".join(NAMES.get(k, f"#{k}") for k in keys) if keys else "не задано"


def press(keys: list[int]) -> None:
    """Жмёт сочетание: все клавиши вниз по порядку, потом вверх наоборот.

    Порядок важен: сначала должны лечь модификаторы, иначе программа
    получит голую F10 без Alt.
    """
    if not keys:
        raise ValueError("нечего нажимать")

    events = []
    for code in keys:
        events.append((code, 0))
    for code in reversed(keys):
        events.append((code, KEYEVENTF_KEYUP))

    array = (_Input * len(events))()
    for index, (code, flags) in enumerate(events):
        array[index].type = INPUT_KEYBOARD
        array[index].union.ki = _KeyInput(
            wVk=code,
            # Скан-код добавляем: часть игр и оверлеев читает именно его,
            # а не код виртуальной клавиши.
            wScan=ctypes.windll.user32.MapVirtualKeyW(code, 0),
            dwFlags=flags,
            time=0,
            dwExtraInfo=None,
        )

    sent = ctypes.windll.user32.SendInput(
        len(events), ctypes.byref(array), ctypes.sizeof(_Input)
    )
    if sent != len(events):
        why = ctypes.get_last_error() or ctypes.GetLastError()
        # Код 5 означает, что нажатие отклонила защита Windows: окно сверху
        # запущено от администратора, а пульт — нет. Лечится запуском пульта
        # с теми же правами, а не кодом.
        hint = " — окно сверху запущено от администратора" if why == 5 else ""
        raise OSError(
            f"система приняла {sent} нажатий из {len(events)}, ошибка {why}{hint}"
        )


def save_moment() -> str:
    """Просит NVIDIA сохранить последние минуты игры. Возвращает, что нажали.

    При выключенном повторе сочетание молча ничего не делает — раньше это
    выглядело как «сохранила», а клипа не было. Теперь ReplayOff.
    """
    from core import replay

    replay.require_on()
    keys = combo("DVRSave")
    press(keys)
    return describe(keys)
