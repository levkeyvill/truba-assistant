r"""Заметки: папка, файлы, разбор и удаление. Без сети и без звука.

Хозяин диктует мысли голосом, а читать их потом будет в **Obsidian** и в
пульте, удалять — из пульта. Поэтому файл должен быть обычным текстом в
Markdown, а не какой-то базой: его легко открыть в чужой программе, найти
поиском и не бояться потерять.

Устройство папки:

    <папка>\<Раздел>\<Тема>.md

Один файл на тему, записи внутри — по датам, новые в конец. Разделы
заводятся любые короткие («Книги», «Проекты», «Идеи», «Разное»), но
четыре первых создаются сразу: иначе первая же мысль легла бы в папку
без названия.

Формат файла — с frontmatter, как его понимает Obsidian:

    ---
    тема: Мастер и Маргарита
    раздел: Книги
    создано: 2026-09-27
    обновлено: 2026-09-27
    tags: [заметки-трубы, книги]
    ---

    # Мастер и Маргарита

    ## 27.09.2026, 02:15 — Воланд как зеркало

    Причёсанный текст абзацами.

    > [!quote]- Как было сказано
    > сырой текст диктовки

Сворачиваемая цитата в конце — это надиктованное как есть. Распознавание
речи путает слова, и если что-то пойдёт не так, сырое всегда под рукой:
его видно, но оно не мешает читать.

Удалённое не стирается, а уходит в `<папка>\.trash\` — так же, как корзина
самого Obsidian: хозяин удаляет из пульта, но может вернуть.

Запись на диск идёт через временный файл и замену (как в tools/backup.py):
оборванная запись посреди вызова выглядела бы как настоящая заметка.
"""
import ctypes
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import config

# Папка по умолчанию — рядом с «Документами» хозяина, а не внутри проекта:
# он всё равно переезжает, а заметки должны остаться при нём.
DEFAULT_FOLDER = "Заметки Трубы"
# Разделы, которые заводятся сразу. Остальные хозяин называет сам, и папка
# под такой раздел создаётся при первой же записи.
DEFAULT_SECTIONS = ("Книги", "Проекты", "Идеи", "Разное")
# Куда уходит удалённое. Скрытая папка — в Obsidian она не мешает.
TRASH = ".trash"
# Общий тег: по нему в Obsidian находится всё наше разом.
TAG = "заметки-трубы"
# Длина имени файла. Windows держит 255, но длинные имена в Проводнике и
# в списке тем не читаются — короткое имя важнее.
MAX_NAME = 80
# Сколько знаков отдавать модели на причёсывание и на чтение.
POLISH_TOKENS = 1600
READ_CHARS = 6000

# Windows не берёт эти символы, а Obsidian — решётку и скобки. Заменяем их
# пробелом, а не выкидываем: «C: Мастер» читается, «CМастер» — нет.
BAD_CHARS = '\\/":*?<>#^[]'
# Имя, из которого файлы не сделать: путь вверх, сетевой или дисковой путь.
# Мусорные символы внутри имени мы чистим, а вот попытку выйти из папки
# заметок — отклоняем целиком: тихо починить её нельзя, а вот незаметно
# испортить чужой файл — можно.
ESCAPES = re.compile(r"(^\s*[\\/])|(^\s*[A-Za-z]:[\\/])|(\.\.)")

# Заметки пишутся из двух потоков сразу: диктовка уходит в фон, а пульт в
# это время правит ту же тему. Файл темы — это «прочитал, поправил строки,
# записал обратно», и без замка две правки затирают друг друга. RLock, а не
# Lock: `delete_entry` внутри зовёт `read`.
_LOCK = threading.RLock()


def documents_dir() -> Path:
    """Папка «Документы» так, как её знает сама Windows.

    Через SHGetFolderPathW, а не склейкой `%USERPROFILE%\\Documents`:
    папку переносят на другой диск, и после этого склейка уводит записи
    в никуда. Спрашиваем систему — и заодно FallbackKnownFolder.
    """
    try:
        buf = ctypes.create_unicode_buffer(1024)
        # CSIDL_PERSONAL = 5: «Документы» именно этого пользователя.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 \
                and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Documents"


def root() -> Path:
    """Папка заметок. Читается на каждый вызов, а не один раз.

    Хозяин меняет её в пульте, и после сохранения заметки должны пойти туда
    же, куда он указал, — без перезапуска программы.
    """
    configured = str(getattr(config, "NOTES_DIR", "") or "").strip()
    if configured:
        return Path(configured)
    return documents_dir() / DEFAULT_FOLDER


def _clean(name: str) -> str:
    """Имя раздела или темы: без запретных символов, обрезанное по длине."""
    text = str(name or "")
    if ESCAPES.search(text):
        raise ValueError(f"недопустимое имя: {name}")
    for char in BAD_CHARS:
        text = text.replace(char, " ")
    text = re.sub(r"\s+", " ", text).strip(" .\u2013\u2014")
    if len(text) > MAX_NAME:
        text = text[:MAX_NAME].rstrip(" .\u2013\u2014")
    if not text:
        raise ValueError("пустое имя")
    return text


def soft_name(name: str) -> str:
    """Имя от модели — мягко, а не отказом.

    `_clean` отклоняет `..` и слеши целиком: так безопасно для имён из
    запроса пульта. Но модель честно придумывает «Итоги...» или «Книги/
    фантастика», и отказ тут выбросил бы причёсанную заметку во «Входящие».
    Поэтому слеши — в пробел, многоточие — в «…», а дальше тот же `_clean`.
    """
    text = re.sub(r"[\\/]+", " ", str(name or ""))
    text = re.sub(r"\.{2,}", "…", text)
    # От «..» или «/» после чистки остаётся одно многоточие — это не имя.
    if not re.search(r"\w", text):
        raise ValueError(f"недопустимое имя: {name}")
    return _clean(text)


def topic_path(section: str, topic: str) -> Path:
    """Путь к файлу темы. Всё, что не под этой папкой, — отказ.

    Ещё одна проверка после сборки: `_clean` могла пропустить что-то
    неожиданное, а файл мы сейчас создадим или сотрём.
    """
    folder = _clean(section)
    name = _clean(topic)
    here = root()
    path = here / folder / f"{name}.md"
    try:
        inside = path.resolve().is_relative_to(here.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ValueError("имя выходит за папку заметок")
    return path


def _write(path: Path, text: str) -> None:
    """Пишет через временный файл: оборванная запись не остаётся видимой."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        partial.write_text(text, encoding="utf-8", newline="\n")
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _front(topic: str, section: str, tag: str, when: datetime) -> list[str]:
    """Шапка файла. Имена в ней — те же, что на диске, иначе поиск по frontmatter
    ищет не по тому ключу, какой видит хозяин.

    `создано` — дата первой записи, а не сегодняшняя: файл заводят под первую
    мысль, и по «создано» видно, с какого дня тема пошла. Дальше его трогает
    только `_stamp` (обновляет `обновлено`).
    """
    today = when.strftime("%Y-%m-%d")
    return [
        "---",
        f"тема: {topic}",
        f"раздел: {section}",
        f"создано: {today}",
        f"обновлено: {today}",
        f"tags: [{TAG}, {tag}]",
        "---",
        "",
        f"# {topic}",
        "",
    ]

def _tag_of(section: str) -> str:
    """Тег раздела для frontmatter: без пробелов и кавычек, как в Obsidian."""
    return re.sub(r"[^\w\-]+", "-", section.strip().lower()).strip("-") or "заметки"


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return []


def _front_of(lines: list[str]) -> dict:
    """Разобранный frontmatter. Словарь — даже если шапки нет вовсе."""
    data: dict = {}
    if not lines or lines[0].strip() != "---":
        return data
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        if value.strip():
            data[key.strip()] = value.strip()
    return data


def _stamp(lines: list[str], when: datetime) -> None:
    """Меняет `обновлено` в шапке. Нет шапки — не заводим её с нуля:
    файл правили руками, и наша шапка поверх ничего лишнего не значит."""
    today = when.strftime("%Y-%m-%d")
    start = 1 if lines and lines[0].strip() == "---" else -1
    for index in range(start, len(lines)):
        if lines[index].strip() == "---" and start > 0:
            break
        if lines[index].startswith("обновлено:"):
            lines[index] = f"обновлено: {today}"
            return


# --- Разбор файла ---------------------------------------------------------
#
# Разбираем построчно по строкам `## `, а не регуляркой по всему файлу.
# Так разбор терпим к правкам руками: лишний текст, который хозяин вписал
# в Obsidian, попадёт в соседнюю запись, а не пропадёт и не сломает разбор.

HEADING = re.compile(r"^##\s+(.*)$")
QUOTE = re.compile(r"^>\s*\[!quote\]")
WHEN_TITLE = re.compile(r"^(\d{2}\.\d{2}\.\d{4},?\s*\d{1,2}:\d{2})\s*[—–-]\s*(.+)$")


def _entry_of(chunk: list[str]) -> dict:
    """Кусок файла от `## ` до следующего `## ` — одна запись."""
    heading = HEADING.match(chunk[0])
    title = heading.group(1).strip() if heading else ""
    when, name = "", title
    matched = WHEN_TITLE.match(title)
    if matched:
        when, name = matched.group(1), matched.group(2).strip()
    body: list[str] = []
    raw: list[str] = []
    quoted = False
    for line in chunk[1:]:
        if QUOTE.match(line.strip()):
            quoted = True
            continue
        if quoted:
            # Внутри свёрнутой цитаты всё, что начинается с `> `, — сырое.
            stripped = line[1:].strip() if line.startswith(">") else line
            raw.append(stripped)
        else:
            body.append(line)
    return {
        "heading": title,
        "when": when,
        "title": name,
        "text": "\n".join(body).strip(),
        "raw": "\n".join(raw).strip(),
    }


def _chunks(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Голова файла (шапка и заголовок) и куски записей."""
    head: list[str] = []
    entries: list[list[str]] = []
    for line in lines:
        if HEADING.match(line):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
        else:
            head.append(line)
    return head, entries


# --- Чтение ---------------------------------------------------------------


def sections() -> list[dict]:
    """Разделы с темами: имя, путь, число записей и когда обновляли.

    Пустой раздел (папка без файлов) в список не попадает: показывать
    хозяину нечего, а пульт по нему только щёлкал бы впустую.
    """
    here = root()
    out: list[dict] = []
    if not here.is_dir():
        return out
    for folder in sorted(here.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        topics = []
        for file in sorted(folder.glob("*.md"), key=lambda p: p.name.lower()):
            lines = _read_lines(file)
            _head, chunks = _chunks(lines)
            front = _front_of(lines)
            topics.append({
                "name": front.get("тема") or file.stem,
                "path": str(file),
                "entries": len(chunks),
                "updated": front.get("обновлено", ""),
            })
        if topics:
            out.append({
                "name": folder.name,
                "path": str(folder),
                "updated": max((t["updated"] for t in topics), default=""),
                "topics": topics,
            })
    return out


def read(section: str, topic: str) -> dict:
    """Одна тема целиком: шапка и все записи по порядку.

    Терпит к правкам руками: чужой текст, своя шапка и даже полное
    отсутствие frontmatter — всё разбирается, ничего не теряется.
    """
    path = topic_path(section, topic)
    if not path.is_file():
        raise FileNotFoundError(f"нет такой темы: {topic}")
    lines = _read_lines(path)
    head, chunks = _chunks(lines)
    front = _front_of(lines)
    title = ""
    for line in head:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "path": str(path),
        "section": folder_name(section),
        "topic": title or front.get("тема") or path.stem,
        "front": front,
        "head": head,
        "entries": [_entry_of(chunk) for chunk in chunks],
    }


def folder_name(section: str) -> str:
    """Раздел на диске по имени, как его положил `_clean`."""
    try:
        return _clean(section)
    except ValueError:
        return str(section or "")


def topic_text(section: str, topic: str, limit: int = READ_CHARS) -> str:
    """Записи темы текстом для модели — с датами, новые в конце.

    Обрезаем с начала, а не с конца: свежая мысль нужнее давней, и хозяин
    спрашивает «что я говорил» — про последнее.
    """
    data = read(section, topic)
    pieces = []
    for entry in data["entries"]:
        when = entry["when"] or "без даты"
        head = f"{when} — {entry['title']}" if entry["title"] else when
        pieces.append(f"## {head}\n{entry['text']}".strip())
    text = "\n\n".join(pieces)
    return text[-limit:] if len(text) > limit else text


# --- Obsidian -------------------------------------------------------------
#
# Хозяин читает заметки в основном в Obsidian, а из пульта только заглядывает.
# Obsidian держит список своих хранилищ в `%APPDATA%\obsidian\obsidian.json` —
# читаем его и проверяем, что папка заметок лежит внутри одного из них.
# Если нет — пульт молча предложит «Открыть файл», и это не поломка:
# хозяин мог и не ставить Obsidian.


def obsidian_json() -> Path | None:
    """Где Obsidian держит список хранилищ. Нет %APPDATA% — None.

    Путь намеренно не подставляется относительным: иначе чтение ушло бы
    в папку, откуда запущена Труба, и искало файл там, где его нет.
    """
    base = os.environ.get("APPDATA", "").strip()
    if not base:
        return None
    return Path(base) / "obsidian" / "obsidian.json"


def _vaults() -> list[Path]:
    """Все хранилища из obsidian.json. Любая ошибка — пустой список.

    Кривой json, нет файла, Obsidian не установлен — молча считаем, что
    хранилищ нет: хозяин увидит обычную кнопку «Открыть файл», а не ошибку.
    """
    try:
        path = obsidian_json()
        if path is None:
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    vaults = data.get("vaults") if isinstance(data, dict) else None
    if not isinstance(vaults, dict):
        return []
    out = []
    for item in vaults.values():
        raw = str(item.get("path", "")).strip() if isinstance(item, dict) else ""
        if raw:
            out.append(Path(raw))
    return out


def obsidian_vault() -> Path | None:
    """Хранилище, внутри которого лежит папка заметок. Нет — None.

    Именно «внутри»: папка заметок сама по себе хранилищем не является, и
    Obsidian её тогда просто не увидит.
    """
    here = root()
    for vault in _vaults():
        try:
            if here.resolve().is_relative_to(vault.resolve()):
                return vault
        except (OSError, ValueError):
            continue
    return None


def obsidian_link(section: str, topic: str) -> str:
    """Ссылка `obsidian://open?path=…` на файл темы.

    Путь собирается только через `topic_path`, а тот отказывает имени,
    уводящему за папку заметок: иначе ссылка открыла бы хозяину любой
    файл на диске, а пульт виден всей сети.
    """
    path = topic_path(section, topic)
    if not path.is_file():
        raise FileNotFoundError(f"нет такой темы: {topic}")
    return "obsidian://open?path=" + quote(str(path), safe="/:")


# --- Запись ---------------------------------------------------------------


def ensure_root() -> Path:
    """Папка заметок и четыре раздела. Без этого первая мысль легла бы
    в папку, которой нет, а хозяин потом искал бы её в Obsidian не там."""
    here = root()
    for name in DEFAULT_SECTIONS:
        try:
            (here / _clean(name)).mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError):
            continue
    return here


def add(section: str, topic: str, title: str, text: str,
        raw: str = "", when: datetime | None = None) -> Path:
    """Дописывает запись в конец темы. Возвращает путь к файлу.

    Новые записи всегда в конец: тема — это хронология, и мысль, которую
    он сказал полчаса назад, не должна оказываться после сегодняшней.
    """
    with _LOCK:
        stamp = when or datetime.now()
        path = topic_path(section, topic)
        if path.is_file():
            lines = _read_lines(path)
            if not lines or lines[0].strip() != "---":
                # Файл правили руками и шапки в нём уже нет — добавляем запись
                # как есть, не навязывая свою шапку поверх чужой правки.
                pass
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = list(_front(topic, folder_name(section), _tag_of(section), stamp))

        when = f"{stamp:%d.%m.%Y, %H:%M}"
        title = " ".join(str(title or "").split())
        heading = f"## {when} — {title}" if title else f"## {when}"
        # Заголовки `#`/`##` внутри текста разбор принял бы за новую запись —
        # опускаем их до `###`, внутри записи это просто подзаголовок.
        body = re.sub(r"(?m)^#{1,2}(?=\s)", "###", (text or "").strip())
        block = [heading, "", body, ""]
        if (raw or "").strip():
            block += ["> [!quote]- Как было сказано"]
            block += [f"> {line}" for line in raw.strip().split("\n")]
            block += [""]
        while lines and not lines[-1].strip():
            lines.pop()
        lines += [""] + block
        _stamp(lines, stamp)
        _write(path, "\n".join(lines).rstrip("\n") + "\n")
    return path


# --- Удаление -------------------------------------------------------------
#
# Ничего не стираем: всё уходит в `.trash`, как корзина самого Obsidian.
# Удаление из пульта должно быть отменимо — хозяин не обязан помнить,
# что именно он час назад попросил убрать.

TRASH_NAME = "{name} — удалённое.md"


def _trash_path(name: str) -> Path:
    folder = root() / TRASH
    folder.mkdir(parents=True, exist_ok=True)
    return folder / TRASH_NAME.format(name=_clean(name))


def _to_trash(path: Path, name: str, body: str = "") -> Path:
    """Дописывает кусок в корзину заметок. Путь не убирает — это отдельное.

    `body` — что именно удалили. По умолчанию — весь файл: так уходит тема.
    """
    target = _trash_path(name)
    if not body:
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = ""
    stamp = datetime.now().strftime("%d.%m.%Y, %H:%M")
    piece = f"\n\n---\n\nУдалено {stamp} из «{path.stem}».\n\n{body.strip()}\n"
    try:
        target.write_text(
            (target.read_text(encoding="utf-8") if target.exists() else
             f"# {name} — удалённое\n") + piece,
            encoding="utf-8", newline="\n",
        )
    except OSError as exc:
        # Не молча: удаление идёт следом, и без корзины запись пропала бы
        # насовсем, хотя пульт обещает «вернуть можно».
        raise OSError(f"корзина заметок не записалась: {exc}") from exc
    return target


def _prune_section(section: str) -> None:
    """Пустой раздел убираем: папка без файлов в Проводнике и в Obsidian
    только мешает, а заметок в ней уже нет."""
    try:
        folder = root() / folder_name(section)
    except ValueError:
        return
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def delete_entry(section: str, topic: str, index: int, heading: str) -> Path:
    """Убирает одну запись. Возвращает путь к файлу, из которого убрали.

    Заголовок под этим номером должен совпасть с тем, что хозяин видел на
    экране. Не совпал — значит файл он правил руками, пока смотрел, и
    номер теперь про другую запись: трогать нельзя, поэтому возвращаем
    ошибку, а пульт пусть перезагрузит список.
    """
    with _LOCK:
        data = read(section, topic)
        entries = data["entries"]
        try:
            number = int(index)
        except (TypeError, ValueError):
            raise ValueError("неверный номер записи") from None
        if not 0 <= number < len(entries):
            raise ValueError("записи с таким номером нет")
        entry = entries[number]
        if (entry["heading"] or "").strip() != (heading or "").strip():
            raise ValueError("файл изменился — посмотри заметку заново")

        path = Path(data["path"])
        _head, chunks = _chunks(_read_lines(path))
        removed = chunks.pop(number)
        # Удалённое уходит в корзину до того, как файл переписывается: если
        # запись записать не выйдет, заметка останется на месте целиком.
        _to_trash(path, path.stem, "\n".join(removed).strip())
        if chunks:
            # Собираем заново, сохраняя голову и порядок оставшихся кусков.
            rebuilt = list(_head)
            for chunk in chunks:
                rebuilt += chunk
            while rebuilt and not rebuilt[-1].strip():
                rebuilt.pop()
            _stamp(rebuilt, datetime.now())
            _write(path, "\n".join(rebuilt).rstrip("\n") + "\n")
            return path
        # Запись была последней — тема без неё пустая, и файл уходит целиком.
        path.unlink(missing_ok=True)
        _prune_section(section)
    return _trash_path(path.stem)


def delete_topic(section: str, topic: str) -> Path:
    """Убирает тему целиком. Файл уезжает в корзину заметок."""
    with _LOCK:
        path = topic_path(section, topic)
        if not path.is_file():
            raise FileNotFoundError(f"нет такой темы: {topic}")
        target = _to_trash(path, path.stem)
        path.unlink(missing_ok=True)
        _prune_section(section)
    return target


# --- Правка ---------------------------------------------------------------
#
# Хозяин хочет поправить заметку из пульта, а не открывать Obsidian: мысль
# легла не туда, заголовок неудачный, или он просто продиктовал ещё одну.
# Правится только причёсанный текст — сырая диктовка в свёртке не трогается.


def _head_line(entry: dict, title: str) -> str:
    """Новая строка `## ` для записи, с прежней датой.

    Дата — часть записи, а не украшение: переименование мысли не должно
    делать её свежей. Записи без даты (правили руками) даты не получают.
    """
    title = " ".join(str(title or "").split())
    when = (entry.get("when") or "").strip()
    if when:
        return f"## {when} — {title}" if title else f"## {when}"
    if title:
        return f"## {title}"
    # Даты нет и заголовка нет — строку не выдумываем, оставляем прежнюю.
    return f"## {entry.get('heading', '')}".rstrip()


def edit_entry(section: str, topic: str, index: int, heading: str,
               title: str, text: str) -> Path:
    """Заменяет заголовок и текст записи под номером `index`. Путь к файлу.

    Проверки те же, что в `delete_entry`: номер должен существовать, а
    заголовок под ним — совпасть с тем, что хозяин видел на экране. Хозяин
    правит файл руками, пока смотрит пульт, и номер уже про другую запись.

    Сырая диктовка в свёртке переписывается из прежней и остаётся на месте:
    распознавание речи врало, и это единственная честная копия того, что он
    сказал. В корзину ничего не уходит — запись не удаляли, её исправили.
    """
    with _LOCK:
        data = read(section, topic)
        entries = data["entries"]
        try:
            number = int(index)
        except (TypeError, ValueError):
            raise ValueError("неверный номер записи") from None
        if not 0 <= number < len(entries):
            raise ValueError("записи с таким номером нет")
        entry = entries[number]
        if (entry["heading"] or "").strip() != (heading or "").strip():
            raise ValueError("файл изменился — посмотри заметку заново")
        body = (text or "").strip()
        if not body:
            raise ValueError("пустой текст")
        # Заголовки `#`/`##` внутри текста разбор принял бы за новую запись —
        # опускаем их до `###`, как и при добавлении.
        body = re.sub(r"(?m)^#{1,2}(?=\s)", "###", body)

        block = [_head_line(entry, title), "", body, ""]
        if entry.get("raw"):
            block += ["> [!quote]- Как было сказано"]
            block += [f"> {line}" for line in entry["raw"].split("\n")]
            block += [""]

        path = Path(data["path"])
        _head, chunks = _chunks(_read_lines(path))
        chunks[number] = block
        rebuilt = list(_head)
        for chunk in chunks:
            rebuilt += chunk
        while rebuilt and not rebuilt[-1].strip():
            rebuilt.pop()
        _stamp(rebuilt, datetime.now())
        _write(path, "\n".join(rebuilt).rstrip("\n") + "\n")
    return path


def _reface(lines: list[str], topic: str, section: str,
            when: datetime) -> None:
    """Переименовывает тему внутри файла: шапка, тег и строка `# `.

    Меняются только строки, которые уже есть: файл, правленный руками, не
    должен получить нашу шапку поверх чужой правки. Записи не трогаем.
    """
    # Первая `---` открывает шапку, а не закрывает её: закрывает вторая.
    in_front = bool(lines) and lines[0].strip() == "---"
    start = 1 if in_front else 0
    titled = True
    for index in range(start, len(lines)):
        line = lines[index]
        if in_front:
            if line.strip() == "---":
                in_front = False
            elif line.startswith("тема:"):
                lines[index] = f"тема: {topic}"
            elif line.startswith("раздел:"):
                lines[index] = f"раздел: {section}"
            elif line.startswith("tags: ["):
                lines[index] = f"tags: [{TAG}, {_tag_of(section)}]"
            continue
        if HEADING.match(line):
            break  # дальше записи — их не трогаем
        if titled and line.startswith("# "):
            lines[index] = f"# {topic}"
            titled = False
    _stamp(lines, when)


def rename_topic(section: str, topic: str, new_topic: str,
                 new_section: str = "") -> Path:
    """Переименовывает тему и/ или переносит её в другой раздел.

    Модель иногда кладёт мысль не в тот раздел, и хозяин переносит её руками
    — папку в Obsidian он всё равно видит, заметку переносит не охотно.

    Сначала пишем новый файл и только потом убираем старый: если запись
    не вышла, заметка остаётся на месте целиком. Склеивать с темой-тёзкой не
    надо — две разные мысли молча слились бы в одну.
    """
    with _LOCK:
        old = topic_path(section, topic)
        if not old.is_file():
            raise FileNotFoundError(f"нет такой темы: {topic}")
        раздел = str(new_section or "").strip() or section
        target = topic_path(раздел, new_topic or topic)
        if str(target) == str(old):
            return old
        # На Windows «Книга.md» и «книга.md» — один и тот же файл: переименование
        # только регистром это правка имени, а не «тема с таким именем уже есть».
        same = target.exists() and os.path.samefile(target, old)
        if target.exists() and not same:
            raise ValueError("тема с таким именем уже есть")
        lines = _read_lines(old)
        _reface(lines, target.stem, target.parent.name, datetime.now())
        if same and old.name != target.name:
            # Только регистр: запись поверх «Книга.md» оставила бы на диске
            # прежнее имя — Windows хранит регистр от старого файла. Сначала
            # переименовать сам файл, потом писать.
            old.rename(target)
        _write(target, "\n".join(lines).rstrip("\n") + "\n")
        if not same:
            old.unlink(missing_ok=True)
            _prune_section(section)
    return target


def known_topics() -> list[dict]:
    """Список разделов с темами для подсказки модели: куда положить мысль."""
    return [{"name": s["name"],
             "topics": [t["name"] for t in s["topics"]]}
            for s in sections()]

# --- Причёсывание ---------------------------------------------------------
#
# Один разовый запрос без истории разговора: `_ask_plainly` не смотрит ни в
# что, кроме промпта. Иначе модель отвечала бы в стиле последнего
# разговора, а нам нужен текст заметки, а не беседа.


POLISH_ASK = """Ниже человек надиктовал мысль голосом. Распознавание речи ошибается \
в словах, поэтому в тексте бывают опечатки и пропуски.

Сделай из него аккуратную заметку:
- исправь явные ошибки распознавания;
- расставь знаки препинания и заглавные буквы;
- убери слова-паразиты и повторы;
- разбей на абзацы или пункты, если мысль длинная.

И обязательно:
- НИЧЕГО не добавляй от себя: ни своих мыслей, ни советов, ни «интересно \
сказано»;
- не меняй смысл и не сокращай мысли, даже если формулировка корявая;
- сохраняй его слова и его формулировки, насколько можно.

Верни JSON:
{"section": "...", "topic": "...", "title": "...", "text": "..."}

section и topic — где положить заметку. Выбирай из уже существующих, если \
подходит: %(known)s
Иначе придумай новые короткие названия. Раздел — коротко: Книги, Проекты, \
Идеи, Разное или свой.

title — заголовок самой записи, 3–7 слов: о чём эта мысль.

text — сама заметка абзацами. Голосовые команды вроде «запиши мысль по книге \
…» в текст не попадают: это подсказка, куда класть, а не часть мысли.

Надиктовано:
%(said)s

Команда, которой он это начал (может помочь с выбором раздела):
%(command)s"""


def _ask(brain, prompt: str) -> str:
    """Один разовый вопрос облаку. Пустая строка — не ответило."""
    answer = brain._ask_plainly(prompt, POLISH_TOKENS, json_mode=True)
    choice = answer.choices[0]
    return (choice.message.content or "").strip()


def _parsed(text: str) -> dict:
    """JSON из ответа. Модель иногда оборачивает его в ```json — режем."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        body = body.rsplit("```", 1)[0]
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("не словарь")
    return data


def _known_text(known) -> str:
    lines = []
    for section in known or []:
        name = str(section.get("name", "")).strip()
        topics = ", ".join(str(t).strip() for t in section.get("topics", []) if t)
        lines.append(f"- {name}: {topics}" if topics else f"- {name}")
    return "\n".join(lines) or "(пока пусто)"


def polish(brain, said: str, command: str = "", known=None) -> dict:
    """Причёсывает надиктованное. Возвращает раздел, тему, заголовок и текст.

    Облако не ответило или ответило кривым JSON — пишем сырое как есть в
    «Разное / Входящие» под заголовком «Без обработки». Надиктованное
    терять нельзя: это его мысль, и второй раз он её может не вспомнить.
    Помечено это полем `raw` — голосовой цикл по нему говорит другое.
    """
    said = (said or "").strip()
    if brain is None:
        return _as_is(said)
    try:
        data = _parsed(_ask(brain, POLISH_ASK % {
            "said": said,
            "command": (command or "").strip() or "(сказал только «запиши заметку»)",
            "known": _known_text(known),
        }))
    except Exception:
        return _as_is(said)
    text = str(data.get("text", "")).strip()
    section = str(data.get("section", "")).strip()
    topic = str(data.get("topic", "")).strip()
    if not text or not section or not topic:
        return _as_is(said)
    title = str(data.get("title", "")).strip() or "Без темы"
    try:
        # Имена от модели — строго через `_clean`, а не через `folder_name`:
        # та для поиска раздела на диске и на отказ молча отдаёт строку как
        # есть, а тут «..» уехало бы за папку заметок.
        section, topic = soft_name(section), soft_name(topic)
    except ValueError:
        return _as_is(said)
    return {"section": section, "topic": topic, "title": title,
            "text": text, "raw": False}


def _as_is(said: str) -> dict:
    return {"section": "Разное", "topic": "Входящие", "title": "Без обработки",
            "text": said, "raw": True}
