r"""Файлы хозяина: найти по названию и открыть — по всем дискам, без индекса.

Хозяин (30.09): «если я скажу „открой на диске ц документ“ и точное название, и
скажу „прочитай его“ — она это сможет?» Своей программы поиска у него нет
(Everything не стоит, а ставить ничего не будем), поэтому ищем сами: обходом
папок в ширину, с пределом времени.

Порядок обхода — сначала то, где файлы лежат у хозяина: Рабочий стол,
Документы, Загрузки, Изображения, Видео, Музыка, папка заметок и хранилища
Obsidian, потом корни дисков. `drive` («C», «D») сужает поиск до одного диска и
его стандартных папок, если они на нём.

Мимо заходим в папки, где хозяиных файлов нет и быть не может: `Windows`,
`Program Files`, `AppData`, `node_modules`, `.git`, `.venv`, `venv`,
`__pycache__`, `.cache` и всё, что начинается с точки. Скрытое и системное — мимо,
ошибки доступа — молча мимо, по ссылкам (junction и symlink) не ходим: иначе
поиск зациклится и уйдёт в чужую папку.

Индекса и фоновых потоков здесь нет намеренно: хозяин ждёт ответа голосом, а не
индексацию диска. Не успели за `SECONDS` — отдаём лучшее из найденного и честно
говорим, что поиск не закончен (`searched_all: False`).

Открытие — `os.startfile`, то есть программа по умолчанию, ровно как у хозяина по
двойному щелчку. Исполняемые файлы и скрипты не открываем никогда: запуск по
названию файла — это запуск программ, а для них есть `launch_app` с белым
списком.

Ни одна функция здесь не бросает исключения: их зовёт инструмент модели, а
ошибка вместо ответа уронила бы разговор.
"""

import os
import re
import time
from collections import deque
from pathlib import Path

from core import folders

# Сколько секунд идёт поиск. Хозяин ждёт ответа голосом, а не полного обхода
# дисков: не успели — отдаём лучшее и говорим об этом честно.
SECONDS = 6.0
# Сколько лучших совпадений отдаём: больше пяти модель и хозяин не разберут, а
# переспросить надо.
BEST = 5
# Слова короче трёх букв («по», «и», «за») в имя файла ничего не различают.
MIN_WORD = 3
# Глубина обхода. Ограничение не по времени, а по здравому смыслу: глубже
# двенадцати вложенностей файлы хозяина не кладут, а цикл по ссылкам мы и так
# не ходим.
MAX_DEPTH = 12

# Папки, в которые не заходим: там либо системное, либо чужие хозяину файлы.
SKIP = frozenset({
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "appdata", "node_modules",
    ".git", ".venv", "venv", "__pycache__", ".cache",
})

# Расширения, которые не открываем никогда: это запуск программ, а не открытие
# документа. «Открыть по названию .exe» — ровно то, чем и пользуется вредоносный
# файл.
EXEC = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".scr", ".pif", ".lnk", ".reg", ".hta", ".cpl",
    ".jar", ".py", ".pyw",
})

# Windows: атрибуты файла. Скрытое и системное мимо, `REPARSE` — это ссылка
# (junction или symlink), по ней не ходим.
HIDDEN = 0x2
SYSTEM = 0x4
REPARSE = 0x400

# Стандартные папки в порядке важности: там, где файлы хозяина лежат чаще
# всего. Порядок тот же, что у `core/folders.py::KNOWN`, плюс заметки.
STANDARD = ("desktop", "documents", "downloads", "pictures", "videos",
            "music", "notes")
def find(words: str, drive: str = "",
         limit_seconds: float = SECONDS) -> dict:
    """Файлы по словам названия. `{"ok", "found", "why", "searched_all"}`.

    `found` — до `BEST` лучших путей (`Path`), `searched_all` — False, если за
    `limit_seconds` обход не дошёл до конца. `why` — причина, когда ничего не
    нашлось или поиск не закончен; при успехе он пуст, кроме случая
    «не успела».
    """
    искомые = _Запрос(_слова(words))
    искомые.слитно = _слитно(words)
    if not искомые:
        return {"ok": False, "found": [], "why": "не названо, что искать",
                "searched_all": True}

    начало = time.monotonic()
    найдено: dict = {}
    закончили = True
    встречено = 0
    корни = [Path(корень) for корень in _корни(drive)]
    # Уже пройденные папки: без этого обход корня диска зашёл бы в «Документы»
    # второй раз, и один и тот же файл показался бы хозяину двумя кандидатами.
    пройдено = {_путь_ключ(корень) for корень in корни}
    очередь = deque((корень, 0) for корень in корни)
    while очередь:
        if time.monotonic() - начало > limit_seconds:
            закончили = False
            break
        папка, глубина = очередь.popleft()
        for entry in _содержимое(папка):
            if time.monotonic() - начало > limit_seconds:
                закончили = False
                очередь.clear()
                break
            встречено += 1
            _заметить(entry, искомые, найдено, встречено)
            if not _заходить(entry) or глубина >= MAX_DEPTH:
                continue
            ключ = _путь_ключ(entry.path)
            if ключ in пройдено:
                continue
            пройдено.add(ключ)
            очередь.append((Path(entry.path), глубина + 1))

    лучшие = _лучшие(найдено)
    return {"ok": bool(лучшие), "found": лучшие,
            "why": _почему(искомые, лучшие, limit_seconds, закончили),
            "searched_all": закончили}


def open_file(path) -> dict:
    """Открыть файл или папку в программе по умолчанию. `{"ok", "text"}`.

    Тот же `os.startfile`, что у запуска программ и у открытия папки: хозяин
    получит ровно то окно, которое было бы по двойному щелчку. Исполняемое и
    скрипты — отказ: запуск по названию файла не наша работа.
    """
    цель = _путь(path)
    if цель is None:
        return {"ok": False, "text": "не поняла, какой файл открыть",
                "path": ""}
    имя = цель.name
    if not цель.exists():
        return {"ok": False, "text": f"файла «{имя}» больше нет",
                "path": str(цель)}
    if цель.is_dir():
        return _открыть_проводником(цель, имя)
    if цель.suffix.lower() in EXEC:
        return {"ok": False,
                "text": "запускать программы по названию файла не буду",
                "path": str(цель)}
    try:
        os.startfile(str(цель))
    except Exception as exc:
        return {"ok": False, "text": f"не открыла файл: {exc}",
                "path": str(цель)}
    return {"ok": True, "text": f"Открыла «{имя}».", "path": str(цель)}


def _открыть_проводником(цель: Path, имя: str) -> dict:
    """Папка — тот же `startfile`, что у `folders.open_folder`."""
    try:
        os.startfile(str(цель))
    except Exception as exc:
        return {"ok": False, "text": f"не открыла папку: {exc}",
                "path": str(цель)}
    return {"ok": True, "text": f"Открыла папку «{имя}».", "path": str(цель)}


def короткий(path) -> str:
    """Путь для модели: вместо `C:\\Users\\Имя\\` — `~\\`.

    В пути имя пользователя Windows, а модели оно ни к чему: только место в
    запросе и лишние слова о человеке, которого она не знает.
    """
    text = str(path or "")
    try:
        home = str(Path.home())
    except Exception:
        home = ""
    if home and text.lower().startswith(home.lower()):
        return "~" + text[len(home):]
    return text


def папка_пути(path) -> str:
    """Папка файла для ответа модели — тоже без имени пользователя."""
    цель = _путь(path)
    return короткий(цель.parent) if цель is not None else ""
# --- Где искать -------------------------------------------------------------


def _корни(drive: str) -> list:
    """Откуда идём: свои папки, хранилища Obsidian, потом корни дисков.

    `drive` — только этот диск и его стандартные папки, если они на нём.
    """
    from core import notes

    буква = str(drive or "").strip().upper()
    свои = []
    for folder_id in STANDARD:
        try:
            папка = folders.path_of(folder_id)
        except Exception:
            папка = None
        if папка is not None and (not буква or _на_диске(папка, буква)):
            свои.append(Path(папка))
    try:
        хранилища = notes._vaults()
    except Exception:
        хранилища = []
    for vault in хранилища:
        папка = _путь(vault)
        if папка is not None and (not буква or _на_диске(папка, буква)):
            свои.append(папка)
    for one in folders._drives():
        title = str(one.get("title", ""))
        if буква and title.rsplit(" ", 1)[-1].upper() != буква:
            continue
        корень = _путь(one.get("path"))
        if корень is not None:
            свои.append(корень)
    return свои


def _на_диске(путь, буква: str) -> bool:
    """Папка на этом диске — как у `Path.drive`, но безопасно."""
    try:
        return str(Path(путь).drive).rstrip(":\\").upper() == буква
    except Exception:
        return False


# --- Обход ------------------------------------------------------------------


def _содержимое(папка: Path) -> list:
    """Содержимое папки списком. Нет доступа — пусто, молча."""
    try:
        with os.scandir(str(папка)) as вещи:
            return list(вещи)
    except (OSError, ValueError):
        return []


def _свойства(entry) -> tuple:
    """`(это папка, атрибуты, время изменения)` для элемента каталога."""
    try:
        stat = entry.stat(follow_symlinks=False)
    except (OSError, ValueError):
        return False, 0, 0.0
    return (bool(_папка(entry)), int(getattr(stat, "st_file_attributes", 0) or 0),
            float(getattr(stat, "st_mtime", 0.0) or 0.0))


def _папка(entry) -> bool:
    """Элемент — папка (не по ссылке). Ошибка — False."""
    try:
        return bool(entry.is_dir(follow_symlinks=False))
    except (OSError, ValueError, AttributeError):
        return False


def _мимо(entry, папка: bool, атрибуты: int) -> bool:
    """Скрытое, системное или ссылка — мимо; служебная папка — тоже."""
    if атрибуты & (HIDDEN | SYSTEM | REPARSE):
        return True
    return bool(папка and _пропустить_папку(entry.name))


def _заходить(entry) -> bool:
    """Идти ли в этот элемент дальше: он папка и не мимо."""
    if not _папка(entry):
        return False
    return not _мимо(entry, True, _свойства(entry)[1])


def _пропустить_папку(имя: str) -> bool:
    """Системная папка, служебная или скрытая по имени."""
    name = str(имя or "").strip().lower()
    return (not name or name.startswith(".") or name in SKIP)


def _путь_ключ(путь) -> str:
    """Ключ папки для «уже пройдена», без учёта регистра и конечного слэша."""
    try:
        return os.path.normcase(os.path.normpath(str(путь)))
    except Exception:
        return str(путь)


def _заметить(entry, искомые: list, найдено: dict, порядок: int) -> None:
    """Файл подходит — запомнить его очки. Один и тот же файл — один раз."""
    try:
        if not entry.is_file(follow_symlinks=False):
            return
    except (OSError, ValueError, AttributeError):
        return
    _, атрибуты, когда = _свойства(entry)
    # Скрытый, системный или ссылка — мимо и как файл: в проводнике у хозяина их
    # тоже не видно, а «отчёт» из AppData ему не нужен.
    if _мимо(entry, False, атрибуты):
        return
    очки = _очки(entry.name, искомые)
    if not очки[0]:
        return
    ключ = _путь_ключ(entry.path)
    прежний = найдено.get(ключ)
    if прежний is not None and прежний[0] >= очки:
        return
    # `порядок` — номер находки: при равных очках и времени вперёд идёт тот, кого
    # обход встретил раньше, а свои папки обходятся раньше корня диска.
    найдено[ключ] = (очки, когда, порядок, str(entry.path))
# --- Совпадение -------------------------------------------------------------


def _слова(words: str) -> list:
    """Слова запроса от трёх букв: без регистра и `ё`, латиница как есть."""
    текст = re.sub(r"[^0-9a-zA-Zа-яё]+", " ",
                   str(words or "").lower().replace("ё", "е"))
    return [word for word in текст.split() if len(word) >= MIN_WORD]


def _слова_имени(имя: str) -> list:
    """Слова имени файла — те же буквы, что и в запросе."""
    return [word for word in re.sub(
        r"[^0-9a-zA-Zа-яё]+", " ", str(имя or "").lower().replace("ё", "е")
    ).split() if word]


class _Запрос(list):
    """Слова запроса плюс он же слитно — «higgstts3flashrt».

    30.09, живая проверка: «higgs tts 3 flash rt» (так пишет распознавание)
    против файла «Higgs_TTS3_FlashRT_…» — слова «3» и «rt» короче трёх букв
    выпадают, «tts» ≠ «tts3», и нужный файл делил место с лицензиями. Слитно
    запрос целиком входит в имя — это и есть самое сильное совпадение.
    """

    слитно: str = ""


def _слитно(text: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", str(text or "").lower().replace("ё", "е"))


# Слитное совпадение считаем только от этой длины: «отчёт» слитно входит в
# «отчётность» — это уже работа обычных слов, а не склейки.
MIN_SLITNO = 8


def _очки(имя: str, искомые: list) -> tuple:
    """`(сколько слов нашлось, точное ли имя)`. Ноль слов — не совпадение.

    Совпадение слова — по началу слова, падежный хвост терпим тем же
    правилом, что у папок (`folders._слово_подходит`): «отчёты» находит
    «отчёт», а «столовая» не считается «стол». Имя можно сказать без
    расширения: «отчёт» — это «отчёт.docx».
    """
    слова = _слова_имени(имя)
    if not слова:
        return 0, 0
    слитно = getattr(искомые, "слитно", "")
    if len(слитно) >= MIN_SLITNO and слитно in _слитно(Path(str(имя)).stem):
        # Весь запрос целиком — сильнее любого частичного совпадения по словам.
        return len(искомые) + 1, int(слитно == _слитно(Path(str(имя)).stem))
    нашлось = sum(1 for word in искомые
                  if any(folders._слово_подходит(одно, word) for одно in слова))
    if not нашлось:
        return 0, 0
    return нашлось, int(_слова(Path(str(имя)).stem) == list(искомые))


def _лучшие(найдено: dict) -> list:
    """Лучшие совпадения: больше слов — точнее имя — свежее файл. До `BEST`.

    При равенстве и очков, и времени вперёд идёт тот, кого обход встретил раньше:
    свои стандартные папки обходятся раньше корня диска, и одинаковые файлы в
    них важнее лежащих в корне.
    """
    if not найдено:
        return []
    лучшие = max(очки for очки, _, _, _ in найдено.values())
    равные = [(когда, порядок, путь)
              for очки, когда, порядок, путь in найдено.values()
              if очки == лучшие]
    равные.sort(key=lambda one: (-one[0], one[1], one[2].lower()))
    return [Path(путь) for _, _, путь in равные[:BEST]]


def _почему(искомые: list, лучшие: list, предел: float, закончили: bool) -> str:
    """Честная причина: не нашла или не успела."""
    if закончили:
        return "" if лучшие else f"не нашла файла про «{' '.join(искомые)}»"
    не_успела = f"искала {_секунды(предел)}, дальше не успела"
    if лучшие:
        return не_успела
    return f"не нашла файла про «{' '.join(искомые)}» — {не_успела}"


def _секунды(предел: float) -> str:
    """«шесть секунд» — числами вслух не читаем, а словами."""
    from core.speech_text import cardinal

    try:
        return f"{cardinal(int(round(float(предел))))} секунд"
    except Exception:
        return f"{int(round(float(предел)))} секунд"


# --- Мелочи -----------------------------------------------------------------


def _путь(значение):
    try:
        return Path(str(значение)) if значение else None
    except Exception:
        return None