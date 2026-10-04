"""Палитра интерфейса. Тёмная, тёплая, без единого зелёного оттенка."""

# Фоны, от самого тёмного к светлому
BG = "#17171b"
PANEL = "#1f1f25"
INPUT = "#282830"
HOVER = "#32323c"
BORDER = "#35353f"

# Текст
TEXT = "#e6e6ea"
MUTED = "#8a8a96"
DIM = "#5f5f6b"

# Акценты. Основной — тёплый янтарь, второй — холодный синий.
ACCENT = "#d98b52"
ACCENT_HOVER = "#e89a5f"
ACCENT_DIM = "#8a5a35"

INFO = "#6b8afd"
INFO_DIM = "#4a5f9e"

WARN = "#d9a94a"
ERROR = "#e0605f"

# Состояние «включено, работает, связь есть». В оформлении зелёного нет —
# ни фонов, ни панелей, ни акцентов, — но показывать им состояние
# общепринято, и отказываться от этого языка глупо. Приглушённый, чтобы
# не кричал с тёмного фона.
OK = "#5fbf7a"

# Роли в чате
USER_COLOR = ACCENT
BOT_COLOR = "#b8b8e8"

# --- Шрифты ---------------------------------------------------------------
#
# Заполняются на старте окна: до создания окна список шрифтов недоступен.

FONT = "Segoe UI"
FONT_MONO = "Consolas"

FONT_CHOICES = ("Open Sans", "Segoe UI", "Manrope", "Montserrat")
MONO_CHOICES = ("Consolas", "Cascadia Mono", "JetBrains Mono", "Courier New")

# --- Размеры --------------------------------------------------------------
#
# Отрицательные размеры tkinter задают пиксели без пересчёта в пункты.
# Так буквы сохраняют целочисленный размер и чёткие края.
# Для четырёх ролей используем именованные размеры.

TITLE_SIZE = -17  # заголовок раздела
BODY_SIZE = -14   # всё обычное: подписи, кнопки, поля
HINT_SIZE = -12   # пояснения под заголовком, мелкая служебная строка
MONO_SIZE = -13   # цифры, пути, логи — там, где важна колонка
# Текст, который читают вслух, — того же роста, что и кнопки. Отдельный
# крупный размер только сбивал: в одном окне оказывалось три разных.
READ_SIZE = BODY_SIZE


def title(bold: bool = True):
    return (FONT, TITLE_SIZE, "bold") if bold else (FONT, TITLE_SIZE)


def body(bold: bool = False):
    return (FONT, BODY_SIZE, "bold") if bold else (FONT, BODY_SIZE)


def hint():
    return (FONT, HINT_SIZE)


def mono(bold: bool = False):
    return (FONT_MONO, MONO_SIZE, "bold") if bold else (FONT_MONO, MONO_SIZE)


def read():
    return (FONT, READ_SIZE)


def dot():
    """Кружок состояния в шапке. Это значок, а не текст — и размер у него
    свой, от высоты строки не зависящий."""
    return (FONT, -22)


def resolve_fonts() -> tuple[str, str]:
    """Выбирает первый установленный шрифт из списка предпочтений.

    Вызывать после создания окна: до этого список шрифтов недоступен.
    Если позже поставить Intro в систему, он подхватится сам.
    """
    global FONT, FONT_MONO

    try:
        from tkinter import font as tkfont

        available = {name.lower() for name in tkfont.families()}
    except Exception:
        return FONT, FONT_MONO

    for name in FONT_CHOICES:
        if name.lower() in available:
            FONT = name
            break

    for name in MONO_CHOICES:
        if name.lower() in available:
            FONT_MONO = name
            break

    return FONT, FONT_MONO
