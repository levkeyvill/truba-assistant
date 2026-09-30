r"""Документы хозяина: прочитать файл и отдать текст, чтобы она пересказала.

Хозяин (пункт 5 плана `coordination/РУКИ_ПЛАН.md`): «перескажи последний
скачанный PDF», «прочитай этот документ», «что в файле с отчётом?». Читаем
здесь, на компе, и отдаём в облако только прочитанное — тем же правилом, что
снимок экрана (см. `JUDGED` в `core/hands.py`).

Какой файл — четыре способа, и все четыре честно отказывают, если не вышло:

  * `open_document()` — то, что хозяин сейчас открыл в программе (Obsidian,
    Word, Блокнот, Notepad++…): обходим видимые окна сверху вниз, своё окно
    пульта пропускаем, а имя файла достаём по заголовку и по программе;
  * `selected()` — файл, выделенный в окне проводника на переднем плане
    (COM `Shell.Application`, окно с тем же `HWND`, что и впереди; если
    впереди не проводник — последнее окно, где что-то выделено);
  * `latest_download()` — самый новый в «Загрузках» (`core/folders.py`), без
    недокачанных `.part`/`.crdownload`/`.tmp`;
  * `find_by_name(words, drive)` — по словам названия на всех дисках, тем же
    обходом, что и поиск файла (`core/files.py`); `drive` сужает до одного
    диска.

Форматов восемь (`SUPPORTED`); остальное — «такой формат читать не умею:
.xlsx». PDF — `pypdf`, Word — `python-docx` (чистый Python, без pandas и
прочего; Microsoft `markitdown` отвергнут: с нужными расширениями он тянет
~300 МБ). Обе библиотеки импортируются внутри функций, поэтому проект и
тесты работают и без них — с честным отказом.

Три отказа, которые нужны чаще всего: «PDF под паролем», «в PDF нет текста —
похоже, скан, читать сканы не умею» и «файл слишком большой». Обрезка идёт по
границе абзаца, потом предложения, потом слова: отрывок на середине слова
модель прочтёт как отрывок. В журнал и в историю текст документа не уходит
никогда — только имя, число страниц и знаков (`journal_line`).

Ни одна функция здесь не бросает исключения: их зовёт инструмент модели, а
ошибка вместо ответа уронила бы разговор.
"""

import os
import re
import time
from pathlib import Path

import psutil

# Упорядоченный набор: так он и увидит модель, и хозяин в списке. Хвосты
# падежей не нужны — расширение файла сравнивается целиком.
SUPPORTED = (".pdf", ".docx", ".txt", ".md", ".csv", ".log", ".json", ".ini")

# Сколько знаков отдаём модели. Больше не надо: она пересказывает, а не
# читает вслух, и запрос раздувается на каждый запрос.
TEXT_LIMIT = 12000
# Файл больше — честный отказ, а не чтение: на 50 МБ Word хозяина пришлось
# бы ждать минуту, а он ждёт ответа голосом.
MAX_BYTES = 50 * 1024 * 1024
# Потолок на чтение. Хозяин ждёт ответа голосом, а не книгу вслух: упираемся
# во время и отдаём начало с честным `cut`.
SECONDS = 10.0
# Сколько равных совпадений отдаём модели: больше пяти она и хозяин не
# разберут, а переспросить надо.
CANDIDATES = 5
# Недокачанное мимо: браузер ещё пишет файл, читать его нельзя.
PART_SUFFIXES = (".part", ".crdownload", ".tmp", ".download")

# --- Что открыто в редакторе ----------------------------------------------
#
# Хозяин 30.09: «у меня в основном md-файлы, они либо в Obsidian, либо в
# текстовом приложении открываются». Окна читаем, а не открываем: `EnumWindows`
# через `ctypes` (`pywin32` в пульте работает, но оконные функции там лучше не
# трогать — ГРАБЛИ, «Пульт не видит pywin32»), имя процесса — `psutil`.
#
# Программы, у которых файл берётся не из заголовка, а по-своему.
OBSIDIAN = "obsidian.exe"
WORD = "winword.exe"
# Наше окно пульта и его заголовок: их пропускаем, иначе «открытый документ»
# — это пульт Трубы.
НАШИ_ПРОЦЕССЫ = ("python.exe", "pythonw.exe")
ЗАГОЛОВОК_ПУЛЬТА = "Труба — пульт"
# Папки внутри хранилища Obsidian, куда за заметкой не ходим: там её
# настройки, её корзина и её служебное.
СКРЫТЫЕ_ПАПКИ = (".obsidian", ".trash", ".git", "node_modules")
# Предел обхода хранилища — тот же, что у поиска по названию: хозяин открыл
# заметку, а не велел проиндексировать диск.
ОБХОД_СЕКУНД = 2.0
# Хвост заголовка Obsidian: « - Obsidian», с версией или без неё. Отрезаем
# его, чтобы имя заметки и хранилища остались двумя кусками по разделителю.
_ХВОСТ_OBSIDIAN = re.compile(r"\s+-\s+Obsidian(\s+v[\d.]+)?\s*$", re.IGNORECASE)
# Кусок заголовка с расширением из `SUPPORTED`: имя файла или полный путь
# (Notepad++ так умеет). Всё остальное (имя папки, программа, версия) под это
# не подводится, иначе мы бы искали файл по слову из заголовка редактора.
_КУСОК_ФАЙЛА = re.compile(
    r"(?:[A-Za-z]:[\\/][^<>|\r\n]*?)?"
    r"[^\\/:*?\"<>|\r\n]*?\.(?:"
    + "|".join(раздел.lstrip(".") for раздел in SUPPORTED)
    + r")(?![0-9A-Za-z])",
    re.IGNORECASE)


def read_text(path, limit: int = TEXT_LIMIT) -> dict:
    """Текст документа по пути. Ответ — тот же словарь, что у остальных.

    `{"ok", "name", "text", "chars" (знаков всего, до обрезки), "pages"
    (у PDF), "cut"}`, а при отказе — ещё `why` и пустой текст. Ни одного
    исключения наружу: вызывают голосовой инструмент и тесты.
    """
    try:
        target = Path(str(path))
    except Exception:
        return _отказ("", "не поняла, какой файл читать")
    name = target.name
    suffix = target.suffix.lower()
    if suffix not in SUPPORTED:
        return _отказ(name, "такой формат читать не умею: "
                           f"{suffix or 'без расширения'}")
    try:
        if not target.is_file():
            return _отказ(name, "файла нет")
        size = target.stat().st_size
    except OSError as exc:
        return _отказ(name, f"не прочитала файл: {exc}")
    if size > MAX_BYTES:
        return _отказ(name, f"файл слишком большой ({size // 1048576} МБ) — "
                           "такие читать не умею")
    начало = time.monotonic()
    try:
        if suffix == ".pdf":
            return _read_pdf(target, limit, начало)
        if suffix == ".docx":
            return _read_docx(target, limit, начало)
        return _read_plain(target, limit)
    except ImportError as exc:
        return _отказ(name, f"нечем читать: нет библиотеки ({exc})")
    except Exception as exc:
        return _отказ(name, f"не прочитала: {type(exc).__name__}: {exc}")


def pick(which: str, words: str = "", drive: str = "") -> dict:
    """Какой файл читать — по четырём способам сразу. Готовый ответ с отказом.

    `{"ok", "path"}` либо `{"ok": False, "why"}`, а при нескольких равных
    совпадениях — ещё `candidates`: модель переспросит хозяина, а не выберет
    наугад.
    """
    which = str(which or "").strip()
    if which == "open":
        открыт = open_document()
        if not открыт.get("ok"):
            return {"ok": False,
                    "why": str(открыт.get("why") or "не вижу открытого документа")}
        return {"ok": True, "path": открыт["path"],
                "unsaved": bool(открыт.get("unsaved"))}
    if which == "selected":
        путь = selected()
        if путь is None:
            return {"ok": False,
                    "why": "не вижу выделенного файла в проводнике"}
        if путь.is_dir():
            return {"ok": False,
                    "why": f"выделена папка «{путь.name}», а нужен файл"}
        return {"ok": True, "path": путь}
    if which == "by_name":
        слова = str(words or "").strip()
        if not слова:
            return {"ok": False, "why": "название файла не названо"}
        нашлось = find_by_name(слова, drive)
        if not нашлось:
            return {"ok": False, "why": f"не нашла файла про «{слова}»"}
        if len(нашлось) > 1:
            return {
                "ok": False,
                "why": f"под «{слова}» нашлось несколько: "
                       + ", ".join(one.name for one in нашлось)
                       + ". Переспроси хозяина, какой из них",
                "candidates": [one.name for one in нашлось],
            }
        return {"ok": True, "path": нашлось[0]}
    if which == "latest_download":
        путь = latest_download()
        if путь is None:
            return {"ok": False,
                    "why": "в загрузках нет таких файлов — нечего читать"}
        return {"ok": True, "path": путь}
    return {"ok": False,
            "why": f"не поняла, какой документ читать: {which or '(пусто)'}"}


def journal_line(прочитан: dict) -> str:
    """Строка в журнал: «отчёт.pdf, 12 стр., 8400 знаков». Без текста.

    Текст документа в журнал не уходит никогда: журнал хозяин читает сам и
    видит его без телефона.
    """
    куски = [str(прочитан.get("name") or "документ")]
    try:
        страницы = int(прочитан.get("pages") or 0)
    except (TypeError, ValueError):
        страницы = 0
    if страницы:
        куски.append(f"{страницы} стр.")
    from core.speech_text import plural

    знаков = int(прочитан.get("chars") or 0)
    куски.append(f"{знаков} {plural(знаков, ('знак', 'знака', 'знаков'))}")
    return ", ".join(куски)


def journal_aloud_line(прочитан: dict) -> str:
    """Строка в журнал для чтения вслух: «документ вслух: отчёт.pdf, 12 стр.».

    Без знаков в отличие от `journal_line`: их никто не считал вслух, а
    страницы хозяину полезны — он видит, документ это или записка. Текст
    документа сюда не идёт никогда.
    """
    куски = ["документ вслух: " + str(прочитан.get("name") or "документ")]
    try:
        страницы = int(прочитан.get("pages") or 0)
    except (TypeError, ValueError):
        страницы = 0
    if страницы:
        куски.append(f"{страницы} стр.")
    return ", ".join(куски)


# --- Чтение вслух ----------------------------------------------------------
#
# Хозяин просит «прочитай вслух» — и текст из файла идёт прямо в её синтез на
# компьютере, в облако не уходит. Для синтеза файл должен звучать как речь, а не
# как исходник: `## Заголовок`, `**жирный**`, `[текст](адрес)` и `|ячейка|`
# вслух читаются мусором, и хозяин услышит не документ, а его разметку.
#
# Смысл строк убираем целиком (`_строка_без_смысла`), а не вырезаем символы по
# одному: в тексте встречаются `C++`, `a * b` и `2 * 3`, и поштучная замена
# съедала бы куски полезного. Убирается только разметка — то, что читатель видит
# как оформление, а не как слово.

# Ссылка .md: `[текст](адрес)` и картинка `![](адрес)` — остаётся текст.
_ССЫЛКА = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# Голые ссылки и «почта ссылкой»: адрес вслух не прочесть.
_ГОЛАЯ_ССЫЛКА = re.compile(
    r"(?:https?://|ftp://|www\.|mailto:)\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b",
    re.IGNORECASE)
# Оформление в середине строки: `**жирный**`, `_курсив_`, `` `код` ``, `~~зачёркнутый~~`.
_ВНУТРИ = re.compile(r"(?<![\w])(?:[*_~`]{1,3})(?=\S)|(?<=\S)(?:[*_~`]{1,3})(?![\w])")
# Начало строки: заголовок, цитата, маркер списка, нумерация.
_НАЧАЛО = re.compile(r"^\s{0,3}(?:#{1,6}\s*|>\s?|[-*+]\s+|\d+[.)]\s+)")
# Строка-разделитель таблицы (`|---|---|`) — данных в ней нет.
_РАЗДЕЛИТЕЛЬ = re.compile(r"^[\s|:-]+$")
# Предложение вместе со знаком конца: точка нужна синтезу как пауза, без неё
# «Раз. Два!» читается слитно.
_КОНЕЦ_ПРЕДЛОЖЕНИЯ = re.compile(r"[^.!?…]*[.!?…]+|[^.!?…]+$")
# Длиннее этого предложения режем по запятым: одна фраза в синтез — это много
# секунд, а «хватит» посреди неё обрывает всё молчание сразу.
ДЛИННОЕ_ПРЕДЛОЖЕНИЕ = 300
# Сколько знаков в куске, если предложение длиннее и запятых в нём нет.
КУСОК_ПО_ЗНАКАМ = 200


def plain_for_speech(text: str) -> str:
    """Текст документа без разметки `.md` — то, что можно прочесть вслух.

    Смысловая часть не трогается: убираются заголовки, выделения, цитаты,
    адреса ссылок и вертикальные черты таблиц (ячейки остаются ячейками, но
    через запятую — «Кран, 1500» читается словами, а «Кран | 1500» вслух
    произносится как «Кран стоит сто пятьдесят вертикальная черта»). Пустые
    строки сжимаются: подряд идущие переносы синтез читает как паузу.

    Разбивка на предложения — не здесь, а в `sentences`: она нужна чтению
    вслух, а пересказу она ни к чему.
    """
    абзацы: list[str] = []
    for строка in str(text or "").replace("\r\n", "\n").split("\n"):
        if _РАЗДЕЛИТЕЛЬ.match(строка) and "|" in строка:
            # Строка-разделитель таблицы: читать нечего.
            continue
        if not строка.strip():
            continue
        абзацы.append(_строка_без_смысла(строка))
    return "\n".join(кусок for кусок in абзацы if кусок)


def sentences(text: str, limit: int = ДЛИННОЕ_ПРЕДЛОЖЕНИЕ) -> list[str]:
    """Текст — списком предложений, каждое не длиннее `limit` знаков.

    Режем по точкам, вопросам и многоточиям; предложение длиннее `limit` (300
    знаков) идёт в синтез целиком — это много секунд непрерывной речи, а
    остановить её на середине можно только «хватит». Такое режем по запятым, а
    если и запятых нет — по знакам: иначе «хватит» посреди безостановочной
    речи не сработает вовсе.
    """
    куски: list[str] = []
    for абзац in str(text or "").split("\n"):
        for часть in _КОНЕЦ_ПРЕДЛОЖЕНИЯ.findall(абзац.strip()):
            часть = (часть or "").strip()
            if часть:
                куски.extend(_короткие(часть, limit))
    return куски


def _короткие(часть: str, limit: int) -> list[str]:
    """Предложение короче `limit` — как есть. Длинное — по запятым и знакам."""
    if limit <= 0 or len(часть) <= limit:
        return [часть]
    куски: list[str] = []
    for кусок in часть.split(","):
        кусок = кусок.strip()
        if not кусок:
            continue
        while len(кусок) > limit:
            куски.append(кусок[:КУСОК_ПО_ЗНАКАМ].strip())
            кусок = кусок[КУСОК_ПО_ЗНАКАМ:].strip()
        if кусок:
            куски.append(кусок)
    return куски or [часть]


def _строка_без_смысла(строка: str) -> str:
    """Одна строка документа — без разметки и без разделителей таблиц."""
    строка = _НАЧАЛО.sub("", строка)
    if "|" in строка:
        # Таблица: ячейки через запятую. Черты-разделители внутри строки и
        # пустые ячейки (первая и последняя бывают от края) — мимо.
        ячейки = [ячейка.strip() for ячейка in строка.strip().strip("|").split("|")]
        строка = ", ".join(ячейка for ячейка in ячейки
                          if ячейка and not _РАЗДЕЛИТЕЛЬ.match(ячейка))
    строка = _ССЫЛКА.sub(r"\1", строка)
    строка = _ГОЛАЯ_ССЫЛКА.sub(" ссылка ", строка)
    строка = _ВНУТРИ.sub("", строка)
    # Хвост из одних знаков препинания — оформление, а не слово.
    строка = re.sub(r"[\s|:;~`#*_>-]+$", "", строка)
    return re.sub(r"\s+", " ", строка).strip()


# --- Чтение по формату ----------------------------------------------------


def _read_plain(target: Path, limit: int) -> dict:
    """Текстовый файл: UTF-8 (с BOM и без), иначе cp1251.

    Две кодировки — те, что встречаются у хозяина на самом деле. Разбор
    остального (ucs-2, cp866) — не наш случай: честнее сказать «не поняла
    кодировку», чем отдать модели мусор.
    """
    try:
        данные = target.read_bytes()
    except OSError as exc:
        return _отказ(target.name, f"не прочитала файл: {exc}")
    текст = None
    for кодировка in ("utf-8-sig", "cp1251"):
        try:
            текст = данные.decode(кодировка)
            break
        except UnicodeDecodeError:
            continue
    if текст is None:
        return _отказ(target.name, "не поняла кодировку файла")
    if not текст.strip():
        return _отказ(target.name, "в файле нет текста")
    обрезанный, cut = _обрезать(текст, limit)
    return _готово(target.name, обрезанный, len(текст), 0, cut)


def _read_pdf(target: Path, limit: int, начало: float) -> dict:
    """PDF — `pypdf`, по страницам, пока не набрали `limit` и не вышло время.

    Большой PDF целиком не читаем: хозяину нужен пересказ, а не книга.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(target))
    if reader.is_encrypted:
        return _отказ(target.name, "PDF под паролем — читать не умею")
    try:
        всего = len(reader.pages)
    except Exception:
        всего = 0
    куски = []
    взято = 0
    for страница in reader.pages:
        if взято >= limit or time.monotonic() - начало > SECONDS:
            break
        try:
            кусок = страница.extract_text() or ""
        except Exception:
            кусок = ""
        if кусок.strip():
            куски.append(кусок.strip())
            взято += len(кусок)
    текст = "\n\n".join(куски)
    if not текст.strip():
        return _отказ(target.name, "в PDF нет текста — похоже, скан, читать "
                                   "сканы не умею")
    обрезанный, cut = _обрезать(текст, limit)
    # Не все страницы успели прочитаться — `cut` тоже: отдан только кусок.
    return _готово(target.name, обрезанный, len(текст), всего,
                   cut or len(куски) < всего)


def _read_docx(target: Path, limit: int, начало: float) -> dict:
    """Word — абзацы и таблицы, строка таблицы ячейками через « | ».

    Идём по телу документа, а не по `paragraphs` и `tables` по отдельности:
    так абзац и следующая за ним таблица остаются в том порядке, в каком
    хозяин их написал, и пересказ не перескакивает через полстраницы.
    """
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    документ = docx.Document(str(target))
    куски = []
    взято = 0
    for узел in документ.element.body:
        if взято >= limit or time.monotonic() - начало > SECONDS:
            break
        if узел.tag == qn("w:p"):
            строка = Paragraph(узел, документ).text.strip()
        elif узел.tag == qn("w:tbl"):
            строка = "\n".join(
                " | ".join(клетка.text.strip() for клетка in row.cells)
                for row in Table(узел, документ).rows)
        else:
            continue
        if строка.strip():
            куски.append(строка)
            взято += len(строка)
    текст = "\n\n".join(куски)
    if not текст.strip():
        return _отказ(target.name, "в документе нет текста")
    обрезанный, cut = _обрезать(текст, limit)
    return _готово(target.name, обрезанный, len(текст), 0, cut)


def _обрезать(text: str, limit: int) -> tuple:
    """Обрезать по границе абзаца, предложения или слова. `(текст, cut)`.

    Режем с конца, а не с начала: у хозяина важен порядок, пересказ должен
    идти с начала документа. Абзац важнее предложения, предложение — слова,
    и только когда границы нет вовсе режем по символу. Границу ищем ближе к
    половине, а не к самому краю: иначе от документа останется огрызок.
    """
    if limit <= 0 or len(text) <= limit:
        return text, False
    начало = text[:limit]
    for кусок in ("\n\n", "\n", ". ", ".\n", "! ", "?\n", "? ", "; ", ", "):
        граница = начало.rfind(кусок)
        if граница > limit // 2:
            return начало[:граница + len(кусок)].rstrip(), True
    пробел = начало.rfind(" ")
    if пробел > limit // 2:
        return начало[:пробел].rstrip(), True
    return начало.rstrip(), True


# --- Что открыто в редакторе ----------------------------------------------


def open_document() -> dict:
    """Файл, который хозяин сейчас смотрит в программе. `{"ok", "path", "unsaved", "why"}`.

    Обходим видимые окна **сверху вниз** и берём первое, из которого понятно,
    какой файл открыт. Перед нами может стоять пульт, проводник или браузер —
    они файл не называют, поэтому идём дальше, а не отказываем сразу. Ничего
    не открываем, не закрываем и не нажимаем: только читаем заголовки.

    `unsaved` — в редакторе есть несохранённые правки (звёздочка в заголовке):
    читаем мы **сохранённый** файл, и сказать об этом должна модель, а не мы.
    """
    try:
        окна = _видимые_окна()
    except Exception:
        окна = []
    видел = ""
    for _hwnd, заголовок, pid in окна:
        if _наше_окно(pid, заголовок):
            continue
        if not видел:
            видел = str(заголовок or "")
        try:
            найден = _файл_из_окна(_имя_процесса(pid), заголовок, pid)
        except Exception:
            найден = None
        if найден is None:
            continue
        return {"ok": True, "path": найден[0],
                "unsaved": bool(найден[1]), "why": ""}
    if видел:
        return {"ok": False, "path": None, "unsaved": False,
                "why": f"вижу окно «{видел}», но не нашла сам файл"}
    return {"ok": False, "path": None, "unsaved": False,
            "why": "не вижу открытого документа"}


def _видимые_окна() -> list:
    """Окна верхнего уровня в z-порядке: список `(handle, заголовок, pid)`.

    `EnumWindows` обходит сверху вниз — так и в документации Windows, и это
    ровно тот порядок, который нужен: «прочитай открытый документ» значит
    верхнее окно редактора, а не то, которое открыли раньше всех. Впереди
    стоящее окно (`GetForegroundWindow`) ставим первым: по клику мыши именно
    оно и есть то, на которое смотрит хозяин.

    Видимые и с непустым заголовком: служебные окна без заголовка файла не
    называют, а обходят их десятки.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list = []

    def обойти(hwnd, _lp):
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
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                found.append((int(hwnd), заголовок, int(pid.value)))
        except Exception:
            pass
        return True

    обход = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                               wintypes.LPARAM)(обойти)
    user32.EnumWindows(обход, 0)
    try:
        впереди = int(user32.GetForegroundWindow() or 0)
    except Exception:
        впереди = 0
    if впереди:
        for окно in found:
            if окно[0] == впереди:
                found.remove(окно)
                found.insert(0, окно)
                break
    return found


def _наше_окно(pid: int, заголовок: str) -> bool:
    """Окно самой Трубы — читать из него нечего.

    Пульт — это `pythonw.exe`, у которого в команде `ui.window`, а окно его
    зовётся «Труба — пульт». Проверяем оба признака: имя процесса у процесса,
    а заголовок — у окна, и у пульта они сходятся не всегда.
    """
    if ЗАГОЛОВОК_ПУЛЬТА in str(заголовок or ""):
        return True
    if _имя_процесса(pid).lower() not in НАШИ_ПРОЦЕССЫ:
        return False
    if int(pid or 0) == os.getpid():
        return True
    return "ui.window" in " ".join(_команда_процесса(pid)).replace("\\", "/")


def _имя_процесса(pid: int) -> str:
    """Имя exe процесса окна. У `psutil` это **метод**, а не поле (ГРАБЛИ)."""
    try:
        return str(psutil.Process(int(pid)).name() or "")
    except Exception:
        return ""


def _команда_процесса(pid: int) -> list:
    """Командная строка процесса: список кусков. Пусто — не прочиталась."""
    try:
        return [str(кусок) for кусок in (psutil.Process(int(pid)).cmdline() or [])]
    except Exception:
        return []


def _файл_из_окна(имя: str, заголовок: str, pid: int | None = None):
    """Файл из окна программы: пара `(путь, несохранён)` или None.

    Порядок по программе, а не по заголовку: у Word в заголовке бывает
    «Документ1», а настоящий путь знает только сам Word; у Obsidian в
    заголовке нет имени файла, есть имя заметки и хранилища.
    """
    имя = str(имя or "").lower()
    if имя == OBSIDIAN:
        return _файл_из_obsidian(заголовок)
    if имя == WORD:
        путь = _файл_из_word()
        if путь is not None:
            return путь, False
    return _файл_из_заголовка(заголовок, pid)


def _файл_из_obsidian(заголовок: str):
    """Заметка Obsidian по заголовку «<заметка> - <хранилище> - Obsidian v1.x».

    Версия на конце может и отсутствовать, а хранилища берём те же, что и пульт
    (`core/notes.py::_vaults`, тот же `obsidian.json`) — иначе искали бы в папке,
    которой у хозяина нет.
    """
    куски = re.split(r"\s+-\s+", _ХВОСТ_OBSIDIAN.sub("", str(заголовок or "").strip()))
    if len(куски) < 2:
        return None
    заметка, хранилище = куски[0].strip(), куски[1].strip()
    if not заметка or not хранилище:
        return None
    папка = _хранилище(хранилище)
    if папка is None:
        return None
    нашли = _заметка_в_хранилище(папка, заметка)
    if нашли is None:
        return None
    return нашли, str(заголовок or "").lstrip().startswith("*")


def _хранилище(имя: str):
    """Хранилище Obsidian, у которого имя папки совпало. None — нет такого."""
    from core import notes

    нужно = str(имя or "").strip().lower()
    if not нужно:
        return None
    for папка in notes._vaults():
        try:
            if папка.name.lower() == нужно:
                return папка
        except (OSError, ValueError):
            continue
    return None


def _заметка_в_хранилище(хранилище: Path, имя: str):
    """`<имя>.md` внутри хранилища. Из одноимённых — самый недавно изменённый.

    Обход с пределом `ОБХОД_СЕКУНД` и без `.obsidian`/`.trash`/`.git`: там
    настройки, удалённое и служебное, а открытой заметки тем более нет.
    """
    начало = time.monotonic()
    цель = f"{str(имя).strip()}.md".lower()
    if цель == ".md":
        return None
    лучший = None
    свежее = -1.0
    стек = [хранилище]
    while стек:
        if time.monotonic() - начало > ОБХОД_СЕКУНД:
            break
        папка = стек.pop()
        try:
            вещи = list(папка.iterdir())
        except OSError:
            continue
        for одна in вещи:
            try:
                папка_ли = одна.is_dir()
            except OSError:
                continue
            if папка_ли:
                if одна.name.lower() not in СКРЫТЫЕ_ПАПКИ:
                    стек.append(одна)
                continue
            if одна.name.lower() != цель:
                continue
            try:
                когда = одна.stat().st_mtime
            except OSError:
                continue
            if когда > свежее:
                свежее, лучший = когда, одна
    return лучший


def _файл_из_word():
    """Настоящий путь документа Word: COM знает его, заголовок — нет.

    `GetActiveObject` берёт уже запущенный Word, а не создаёт новый; COM зовём
    не из главного потока (инструмент приходит из голосового цикла), поэтому
    свой `CoInitialize` — иначе `com_error` (см. `selected`).
    """
    try:
        import pythoncom
        from win32com.client import GetActiveObject
    except Exception:
        return None
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        полный = str(GetActiveObject("Word.Application")
                      .ActiveDocument.FullName or "")
    except Exception:
        return None
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    путь = _путь(полный)
    if путь is None or not путь.is_file():
        return None
    return путь


def _из_командной_строки(pid: int | None, имя: str):
    """Файл с этим именем из командной строки процесса редактора.

    Двойной щелчок и «Открыть с помощью» передают путь программе аргументом:
    `notepad.exe "D:\\…\\отчёт.md"`. В «Недавние» такой файл попадает не
    всегда (30.09: Блокнот, открытый не через проводник, там не нашёлся) —
    а в командной строке он есть. Аргумент с другим именем (открыли другой
    файл уже внутри программы) — мимо.
    """
    if not pid or not имя:
        return None
    import psutil

    try:
        процесс = psutil.Process(int(pid))
        аргументы = процесс.cmdline()[1:]
        папка = процесс.cwd()
    except Exception:
        return None
    for арг in аргументы:
        арг = str(арг or "").strip().strip('"')
        if not арг or арг.startswith(("-", "/")) and not Path(арг).is_absolute():
            continue
        путь = Path(арг)
        if not путь.is_absolute():
            путь = Path(папка) / путь
        try:
            if путь.name.casefold() == имя.casefold() and путь.is_file():
                return путь
        except OSError:
            continue
    return None


def _файл_из_заголовка(заголовок: str, pid: int | None = None):
    """Файл по имени из заголовка окна: Блокнот, Notepad++, VS Code, Typora.

    В заголовке у них либо имя файла с расширением («отчёт.md - Блокнот»,
    «отчёт.md - папка - Visual Studio Code»), либо полный путь (Notepad++
    так умеет) — тогда берём его, если файл на месте. Звёздочка в начале —
    несохранённые правки, имя от неё отрезаем.
    """
    текст = str(заголовок or "").strip()
    кусок = _КУСОК_ФАЙЛА.search(текст)
    if кусок is None:
        return None
    имя = кусок.group(0).lstrip("*").strip()
    несохранён = текст.lstrip().startswith("*")
    if not имя:
        return None
    if "\\" in имя or "/" in имя:
        # Полный путь в заголовке — берём, только если файл действительно
        # есть: иначе это мусор из имени папки, а не документ.
        путь = _путь(имя)
        if путь is not None and путь.is_file():
            return путь, несохранён
    нашли = _из_командной_строки(pid, имя) or _из_недавних(имя)
    if нашли is None:
        кандидаты = find_by_name(Path(имя).stem)
        нашли = кандидаты[0] if кандидаты else None
    if нашли is None:
        return None
    return нашли, несохранён


def _из_недавних(имя: str):
    """Файл по имени через «Недавние» Windows: самый свежий ярлык `.lnk`.

    `%APPDATA%\Microsoft\Windows\Recent\*.lnk` — ярлыки на недавно открытые
    файлы; имя ярлыка совпадает с именем файла, а внутри цель
    (`TargetPath`). Одного имени в «Недавних» бывает несколько — из разных
    папок, — и свежий ярлык как раз отвечает на «что у меня открыто».
    """
    база = os.environ.get("APPDATA", "").strip()
    нужно = str(имя or "").strip().lower()
    if not база or not нужно:
        return None
    папка = Path(база) / "Microsoft" / "Windows" / "Recent"
    свежий = None
    свежее = -1.0
    try:
        ярлыки = list(папка.glob("*.lnk"))
    except OSError:
        return None
    for ярлык in ярлыки:
        if _имя_ярлыка(ярлык) != нужно:
            continue
        try:
            когда = ярлык.stat().st_mtime
        except OSError:
            continue
        if когда > свежее:
            свежее, свежий = когда, ярлык
    if свежий is None:
        return None
    путь = _путь(_цель_ярлыка(свежий))
    if путь is None or not путь.is_file():
        return None
    return путь


def _имя_ярлыка(ярлык: Path) -> str:
    """Имя файла, на который указывает ярлык, в нижнем регистре.

    Windows не даёт два файла с именем в одной папке, и второй разводит по
    « (2)»: `отчёт.md.lnk` и `отчёт (2).md.lnk` — это один и тот же файл из
    двух папок. Отбрасываем такую приписку, иначе второй ярлык мы бы никогда
    не рассмотрели, а хозяин открыл бы именно его. Приписка стоит **перед
    расширением** — `отчёт (2).md`, а не после него.
    """
    return _ПОВТОР_ЯРЛЫКА.sub("", ярлык.stem).strip().lower()


# Приписка Windows второму файлу с тем же именем: «отчёт (2).md».
_ПОВТОР_ЯРЛЫКА = re.compile(r"\s*\(\d+\)(?=\.[^.]*$)")


def _цель_ярлыка(ярлык: Path) -> str:
    """Настоящий путь, на который указывает ярлык `.lnk`. Пусто — не вышло.

    Читаем ярлык через `WScript.Shell` (`CreateShortcut(p).TargetPath`) — так
    же, как это делает сама Windows, — и в своём потоке с `CoInitialize`.
    """
    try:
        import pythoncom
        from win32com.client import Dispatch
    except Exception:
        return ""
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        return str(Dispatch("WScript.Shell")
                   .CreateShortcut(str(ярлык)).TargetPath or "")
    except Exception:
        return ""
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# --- Какой документ -------------------------------------------------------


def selected():
    """Файл, выделенный в проводнике на переднем плане. None — не вижу.

    `Shell.Application.Windows()` отдаёт окна в z-порядке, сверху вниз, и у
    каждого есть `HWND`; тот, чей равен `GetForegroundWindow()`, — впереди.
    `GetForegroundWindow` берём через `ctypes`, а не `win32gui`: в пульте
    `.pth` не обрабатываются и `win32gui` там нет
    (`coordination/ГРАБЛИ.md`, «Пульт не видит pywin32»).

    COM зовётся не из главного потока (инструмент приходит из голосового
    цикла), поэтому свой `CoInitialize` — иначе `com_error`.
    """
    try:
        import ctypes

        впереди = int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None
    try:
        import pythoncom
        from win32com.client import Dispatch
    except Exception:
        return None
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        # Всё COM-касание — внутри инициализированного апартамента: разорвать
        # его раньше, чем долили окна, значит получить `com_error` на первом
        # же обращении к `Document`.
        return _выделенное_в_окнах(Dispatch("Shell.Application").Windows(), впереди)
    except Exception:
        return None
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _выделенное_в_окнах(окна, впереди: int):
    """Первый выделенный в окне с тем же HWND, что впереди, — иначе запасной."""
    запас = None
    for окно in list(окна or ()):
        путь = _выделенное_в(окно)
        if not путь:
            continue
        try:
            if int(окно.HWND) == впереди:
                return _путь(путь)
        except Exception:
            pass
        # Впереди не проводник — берём последнее окно с выделением: порядок
        # обхода сверху вниз и есть «последнее открытое».
        if запас is None:
            запас = путь
    return _путь(запас) if запас else None


def latest_download():
    """Самый новый читаемый файл в «Загрузках». None — там ничего нет.

    Путь спрашиваем у `core/folders.py`: папку могли перенести на другой
    диск, и наш склеенный путь вёл бы в никуда.
    """
    from core import folders

    папка = folders.path_of("downloads")
    if папка is None:
        return None
    try:
        вещи = list(папка.iterdir())
    except OSError:
        return None
    лучший = None
    свежее = -1.0
    for одна in вещи:
        if одна.suffix.lower() not in SUPPORTED or _не_докачан(одна):
            continue
        try:
            if not одна.is_file():
                continue
            когда = одна.stat().st_mtime
        except OSError:
            continue
        if когда > свежее:
            свежее, лучший = когда, одна
    return лучший


def find_by_name(words: str, drive: str = "") -> list:
    """Файлы по словам названия: до `CANDIDATES` лучших. Пусто — не нашла.

    Ищем по всем дискам через `core/files.py`: хозяин 30.09 спросил «открой на
    диске ц документ договор», а в «Загрузках», «Документах» и на «Рабочем
    столе» его нет. Порядок обхода и предел времени — те же, что у поиска
    файлов; `drive` сужает его до одного диска. Из найденного берём только то,
    что умеем читать (`SUPPORTED`) и что браузер уже дописал.
    """
    from core import files

    искомые = _слова(words)
    if not искомые:
        return []
    try:
        # Предел времени — тот же, что у поиска файлов (`core/files.py`): он
        # ищет «отчёт», а не роет диск, и не успел — честно признаётся.
        что = files.find(words, str(drive or ""))
    except Exception:
        return []
    return [одно for одно in (что.get("found") or [])
            if одно.suffix.lower() in SUPPORTED and not _не_докачан(одно)][:CANDIDATES]


# --- Мелочи по дороге -----------------------------------------------------


def _выделенное_в(окно):
    """Путь к первому выделенному в окне проводника файлу или None."""
    try:
        выбранное = окно.Document.SelectedItems()
        if int(выбранное.Count) < 1:
            return None
        # FolderItems.Item считает с нуля: Item(1) при одном выделенном файле
        # падает (30.09 — живой проводник, подмена в тестах врала так же).
        предмет = выбранное.Item(0)
        return str(предмет.Path or "")
    except Exception:
        return None


def _слова(words: str) -> list:
    """Слова запроса длиннее двух букв — короче ничего не различают."""
    текст = re.sub(r"[^0-9a-zA-Zа-яё]+", " ",
                   (words or "").lower().replace("ё", "е"))
    return [word for word in текст.split() if len(word) > 2]


def _не_докачан(путь: Path) -> bool:
    """Ещё пишется браузером — читать нельзя."""
    return str(путь.name).lower().endswith(PART_SUFFIXES)


def _путь(значение):
    try:
        return Path(str(значение)) if значение else None
    except Exception:
        return None


def _отказ(name: str, why: str) -> dict:
    """Честный отказ: текста нет, но имя и причина — есть."""
    return {"ok": False, "name": str(name or ""), "why": str(why),
            "text": "", "chars": 0, "pages": 0, "cut": False}


def _готово(name: str, text: str, chars: int, pages: int, cut: bool) -> dict:
    return {"ok": True, "name": str(name or ""), "text": text,
            "chars": int(chars), "pages": int(pages), "cut": bool(cut)}
