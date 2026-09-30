r"""Стандартные папки Windows — те, что у каждого свои: загрузки, документы,
рабочий стол, музыка, видео, и папка заметок Трубы.

Хозяин говорит «открой загрузки» или «открой рабочий стол» — и это должно
работать у каждого сразу, без настройки. Поэтому пути спрашиваются у самой
Windows (`SHGetKnownFolderPath`, общая функция `core/screen.py`), а не
склеиваются руками: папку могли перенести на другой диск, и тогда наш путь
вёл бы в никуда.

Своих папок здесь нет. Хозяин добавляет их в пульте на странице «Программы»
новым видом пункта `folder` — и такая папка получает кнопку на телефоне,
запуск голосом и прозвища. Стандартные папки нужны именно тем, что есть у
всегда и нигде не настраиваются.

Ни одна функция здесь не бросает исключения: зовут её и голосовая команда, и
инструмент модели, а ошибка вместо ответа уронила бы разговор.
"""

import os
from pathlib import Path

# Опознаватели Known Folders (FOLDERID_*). Загрузки — самый ходовой, поэтому
# он идёт первым и в коде, и в пересказе хозяину.
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
FOLDERID_DOCUMENTS = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"
FOLDERID_MUSIC = "{4BD8D571-6D19-48D3-BE97-422220080E43}"
FOLDERID_VIDEOS = "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"

# Упорядоченный набор: так он и увидит модель в `enum`, и хозяин в списке.
# `words` — то, чем человек называет папку вслух. Хвосты («загрузки», «загрузок»)
# не перечисляем: `find` узнаёт слово по началу, а падежи и предлоги отсекает
# сама команда (core/commands.py).
KNOWN = (
    {"id": "downloads", "title": "Загрузки", "folder_id": FOLDERID_DOWNLOADS,
     "words": ("загрузки", "закачки", "скачанное")},
    {"id": "documents", "title": "Документы", "folder_id": FOLDERID_DOCUMENTS,
     "words": ("документы", "документ")},
    {"id": "desktop", "title": "Рабочий стол", "folder_id": FOLDERID_DESKTOP,
     "words": ("рабочий стол", "стол")},
    {"id": "pictures", "title": "Изображения", "folder_id": FOLDERID_PICTURES,
     "words": ("картинки", "изображения", "фотки")},
    {"id": "music", "title": "Музыка", "folder_id": FOLDERID_MUSIC,
     "words": ("музыка",)},
    {"id": "videos", "title": "Видео", "folder_id": FOLDERID_VIDEOS,
     "words": ("видео",)},
    {"id": "notes", "title": "Заметки", "folder_id": None,
     "words": ("заметки", "заметка")},
)

# Диски: «открой диск D», «открой диск С» (хозяин, 30.09). Как распознавание
# пишет букву, заранее не угадать: «диск д», «диск дэ», «диск D» — берём все
# привычные произношения. Остальные буквы — только сама буква.
DRIVE_SOUNDS = {
    # Хозяин говорит «диск ц» (30.09) — первым, остальное тоже бывает.
    "C": ("ц", "цэ", "це", "с", "си"),
    "D": ("д", "дэ", "де", "ди"),
    "E": ("е", "э"),
    "F": ("ф", "эф"),
    "G": ("г", "гэ", "же", "джи"),
}
# GetDriveTypeW: сменный (флешка), жёсткий, сетевой. Дисковод и «нет
# диска» — мимо: открыть их — окно с ошибкой.
DRIVE_TYPES = (2, 3, 4)


def _drives() -> tuple:
    """Диски, которые есть на этом компьютере, — при запуске пульта."""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        mask = int(kernel.GetLogicalDrives())
    except Exception:
        return ()
    found = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = chr(ord("A") + i)
        try:
            if int(kernel.GetDriveTypeW(f"{letter}:\\")) not in DRIVE_TYPES:
                continue
        except Exception:
            continue
        sounds = (letter.lower(),) + DRIVE_SOUNDS.get(letter, ())
        found.append({"id": f"drive_{letter.lower()}", "title": f"Диск {letter}",
                      "path": f"{letter}:\\",
                      "words": tuple(f"диск {s}" for s in sounds)})
    return tuple(found)


KNOWN = KNOWN + _drives()

# Сколько первых букв слова сравниваем. «загрузки» и «загрузках» — одно слово,
# а «стол» и «столовая» — нет, поэтому короткие слова сравниваем целиком.
STEM = 5
# Насколько длинным может быть падежный хвост: «заметки» ⊃ «заметками».
TAIL = 4


def _by_id(folder_id: str) -> dict | None:
    for one in KNOWN:
        if one["id"] == folder_id:
            return one
    return None


def title_of(folder_id: str) -> str:
    """Человеческое название папки. Незнакомая — отдаётся как есть."""
    one = _by_id(str(folder_id or ""))
    return one["title"] if one else str(folder_id or "")


def path_of(folder_id: str) -> Path | None:
    """Где папка на самом деле. None — такой папки у нас нет.

    Заметки — отдельно: их папку хозяин меняет в пульте, и она может быть
    совсем не в «Документах», поэтому берём её у `core/notes.py` на каждый
    вызов, а не один раз при запуске.
    """
    one = _by_id(str(folder_id or ""))
    if one is None:
        return None
    if one.get("path"):
        return Path(one["path"])
    if one["id"] == "notes":
        try:
            from core import notes

            return notes.root()
        except Exception:
            return None

    from core import screen

    return screen.known_folder(one["folder_id"], Path.home() / one["title"])


def find(words: str) -> str | None:
    """Опознаватель стандартной папки по словам фразы. None — не наша.

    Сравниваем по словам, а не по вхождению подстроки: иначе «стол» нашлось бы
    внутри «столовая», и «открой столовую» открыло бы рабочий стол. Порядок
    `KNOWN` — и порядок ответа: первое подошедшее слово и выигрывает.
    """
    text = (words or "").lower().replace("ё", "е")
    куски = [кусок.strip("-") for кусок in text.split() if кусок.strip("-")]
    for one in KNOWN:
        for word in one["words"]:
            if _говорит_о_папке(куски, word):
                return one["id"]
    return None


def _говорит_о_папке(куски: list, word: str) -> bool:
    """Слова папки есть в фразе — целиком или с падежным хвостом."""
    части = word.split()
    for начало in range(len(куски) - len(части) + 1):
        окно = куски[начало:начало + len(части)]
        if all(_слово_подходит(окно[i], части[i]) for i in range(len(части))):
            return True
    return False


def _слово_подходит(кусок: str, слово: str) -> bool:
    """«заметками» ⊃ «заметки», но «столовая» — не «стол».

    Хвост падежа короткий, а короткие слова вроде «стол» сравниваем целиком:
    иначе «столовая» сочла бы себя рабочим столом, а это кухня.
    """
    if кусок == слово:
        return True
    return (len(слово) >= STEM
            and кусок.startswith(слово[:STEM])
            and len(кусок) - len(слово) <= TAIL)


def open_folder(folder_id: str) -> dict:
    """Открывает стандартную папку. Ответ — тот же словарь, что у программ.

    `os.startfile` — тот же путь, что у запуска программ: проводник открывается
    так же, как его открывает хозяин двойным щелчком, и права не нужны.
    """
    one = _by_id(str(folder_id or ""))
    if one is None:
        return {"ok": False, "text": "Не знаю такой папки.", "path": ""}
    title = one["title"]
    path = path_of(one["id"])
    if path is None or not path.is_dir():
        return {"ok": False, "text": f"папки «{title}» нет", "path": ""}
    try:
        os.startfile(str(path))
    except Exception as exc:
        return {"ok": False, "text": f"не открыла папку: {exc}", "path": str(path)}
    return {"ok": True, "text": f"Открыла {_в_винительном(title)}.", "path": str(path)}


# Текст ответа читается вслух, поэтому падеж: «Открыла загрузки», но «Открыла
# музыку». Падежи выписаны здесь, а не склеены по первой букве.
_VINIELI = {
    "Загрузки": "загрузки",
    "Документы": "документы",
    "Рабочий стол": "рабочий стол",
    "Изображения": "изображения",
    "Музыка": "музыку",
    "Видео": "видео",
    "Заметки": "заметки",
}


def _в_винительном(title: str) -> str:
    if title.startswith("Диск "):
        return "диск " + title[5:]  # «Открыла диск D» — букву не в строчную
    return _VINIELI.get(title, title.lower())
