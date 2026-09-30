r"""Запуск программ и страниц по кнопке с телефона.

Работает по списку из apps.json: запустить можно только то, что там описано.
Это не паранойя — распознавание ошибается, а кнопку легко задеть локтем;
открытый список означает «что угодно по случайному сигналу».

Пути ищутся при старте: если программы нет, кнопка просто не появится.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import config
from core import hotkeys, safe_files, toggles

APPS_FILE = config.ROOT / "apps.json"
# Список для свежей установки. Своего `apps.json` ещё нет — телефон не должен
# показывать пустой экран: показываем две кнопки, которые есть у каждого.
# Свой файл появляется при первом же сохранении в пульте, и всё, что дальше,
# читается уже только из него. В обновлениях этот файл не участвует
# (core/updater.py: `SKIP_FILES` перечисляет `apps.json`, а не его запасной).
DEFAULT_FILE = config.ROOT / "apps.default.json"
# Найденные пути запоминаем: обход Program Files занимает десятки секунд,
# и повторять его при каждом подключении телефона нельзя.
CACHE_FILE = config.DATA_DIR / "apps_cache.json"

# Как он называет программы вслух. Ключ — id записи в apps.json. Пополняется
# по мере того, как распознавание приносит новые варианты.
# Свои прозвища можно дописать в саму запись apps.json полем `aliases` — они
# прибавляются к этим (см. `aliases_of`). Сам файл переписывать не нужно:
# поле просто читается, если оно есть.
DEFAULT_ALIASES = {
    "firefox": ("фаерфокс", "файрфокс", "фаирфокс", "лиса", "браузер", "фокс"),
    "chatgpt": ("чат гпт", "чатгпт", "чат джипити", "джипити", "гпт", "чат"),
    "claude": ("клод", "клауд", "клаудэ", "клауда"),
    "discord": ("дискорд", "диска", "дискорда", "диск"),
    "youtube": ("ютуб", "ютьюб", "ютуба", "трубу", "youtube"),
    "happ": ("хапп", "хэп", "впн"),
    "steam": ("стим", "стима"),
    "telegram": ("телега", "телеграм", "телеги"),
}


def aliases_of(app: dict) -> list[str]:
    """Все прозвища программы: встроенные и её собственные из apps.json.

    Свои лежат в записи полем `aliases` — списком строк. Поле необязательное,
    и файла `apps.json` ради него переписывать не нужно: читается, если есть.
    """
    own = app.get("aliases")
    extra = [str(one).strip().lower() for one in own] if isinstance(own, list) else []
    return list(DEFAULT_ALIASES.get(app.get("id", ""), ())) + extra


# Куда заглядывать, если в описании указано только имя файла.
SEARCH_DIRS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    Path(os.environ.get("LOCALAPPDATA", "")),
]
# Глубже обычно и не нужно: программы лежат в паре уровней от корня,
# а полный обход диска — это десятки тысяч папок.
MAX_DEPTH = 3

_cache: dict[str, str] | None = None


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(_cache or {}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _scan(folder: Path, name: str, depth: int) -> str | None:
    """Обход вширь с ограничением глубины — вместо бесконечного rglob."""
    if depth < 0 or not folder.exists():
        return None
    try:
        candidate = folder / name
        if candidate.exists():
            return str(candidate)
        for child in folder.iterdir():
            if not child.is_dir():
                continue
            found = _scan(child, name, depth - 1)
            if found:
                return found
    except (OSError, PermissionError):
        pass
    return None


def _find(target: str) -> str | None:
    """Ищет программу: готовый путь, потом кеш, потом обход папок."""
    if not target:
        return None

    direct = Path(os.path.expandvars(target))
    if direct.exists():
        return str(direct)

    found = shutil.which(target)
    if found:
        return found

    cache = _load_cache()
    remembered = cache.get(target)
    if remembered and Path(remembered).exists():
        return remembered

    name = Path(target).name
    for folder in SEARCH_DIRS:
        if not folder:
            continue
        found = _scan(folder, name, MAX_DEPTH)
        if found:
            cache[target] = found
            _save_cache()
            return found
    return None


def read_list() -> list[dict]:
    """Список как он записан в файле, без проверок.

    Пульту нужен именно такой: программу, которую сейчас не нашли, из
    списка убирать нельзя — иначе она молча исчезнет и из настроек тоже,
    и человек не поймёт, куда делась кнопка.

    Своего `apps.json` ещё нет — берём запасной `apps.default.json`:
    пустой экран телефона у нового человека выглядит как поломка.
    """
    for файл in (APPS_FILE, DEFAULT_FILE):
        if not файл.exists():
            continue
        try:
            items = json.loads(файл.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Свой список испорчен — в сторону, а не под затирание: иначе
            # первая же правка в «Программах» записала бы поверх запасной
            # список, и кнопки человека пропали бы насовсем.
            if файл == APPS_FILE:
                safe_files.quarantine(APPS_FILE)
            continue
        except OSError:
            continue
        if isinstance(items, list):
            return items
    return []


def save_list(items: list[dict]) -> None:
    """Перезаписывает список целиком — порядок в файле и есть порядок кнопок."""
    safe_files.write_text(APPS_FILE, json.dumps(items, ensure_ascii=False, indent=2))


# Опознаватель уходит в имена файлов со значками, а Windows и кодировки —
# отдельная боль этого проекта. Поэтому кириллицу раскладываем в латиницу.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def make_id(title: str, taken: list[str]) -> str:
    """Придумывает опознаватель для новой кнопки, не повторяя занятые."""
    letters = []
    for char in title.lower():
        char = TRANSLIT.get(char, char)
        if char.isalnum() or (char and char.isascii()):
            letters.append(char)

    base = "".join(c for c in "".join(letters) if c.isascii() and c.isalnum())
    base = base or "app"

    if base not in taken:
        return base
    number = 2
    while f"{base}{number}" in taken:
        number += 1
    return f"{base}{number}"


def resolve(item: dict) -> str | None:
    """Где на самом деле лежит программа. None — не нашлась."""
    return _find(item.get("path", ""))


# --- Подменю программы ------------------------------------------------------
#
# Хозяин хочет нажимать на значок Discord и попадать в меню, а не в Discord.
# Пункты меню живут в apps.json рядом с программой, поэтому тот же файл —
# и список для пульта, и то, что телефон присылает обратно ключом.
#
# Ключ пункта — «вид:имя», например `hotkey:mute`. Имя приходит с телефона,
# поэтому выполнить можно только то, что лежит в apps.json: чужой ключ
# отбрасывается, а не исполняется как есть.

MENU_LAUNCH = "launch"
KINDS = ("hotkey", "site")
# Виды самих кнопок в apps.json. `folder` — своя папка хозяина: у неё, как у
# программы, есть только путь, и открывается она так же — проводником.
APP_KINDS = ("app", "url", "store", "folder")


def read_menu(item: dict) -> list[dict]:
    """Пункты меню программы, как они записаны, но только годные.

    Плохой пункт молча пропускается: телефон уже получил кнопку, и если
    он пришлёт её ключ, а мы не сможем её выполнить, хозяин получит ошибку
    вместо нажатия.
    """
    raw = item.get("menu")
    if not isinstance(raw, list):
        return []
    good = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        name = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if kind not in KINDS or not name or not title or len(title) > 60:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name):
            continue
        clean = {"kind": kind, "id": name, "title": title}
        icon = str(entry.get("icon") or "").strip()
        if icon and len(icon) <= 40:
            clean["icon"] = icon
        # Переключатель — только честное `true`: строка или единица в этом
        # поле означали бы ошибку правки, а не переключатель.
        if entry.get("toggle") is True:
            clean["toggle"] = True
        elif "toggle" in entry and entry.get("toggle") is not None:
            continue
        # `implies` пока сырая строка: проверить её можно только когда известны
        # все пункты меню, а это видно лишь после обхода (см. ниже).
        implies = str(entry.get("implies") or "").strip()
        if implies:
            clean["implies"] = implies
        if kind == "hotkey":
            keys = str(entry.get("keys") or "").strip()
            try:
                hotkeys.parse(keys)
            except ValueError:
                continue
            clean["keys"] = keys
        else:
            url = str(entry.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or len(url) > 500:
                continue
            clean["url"] = url
        good.append(clean)

    # `implies` смотрит на весь список, а не на один пункт: связать можно
    # только с тем, кто реально есть в этой же программе. Ссылка в пустоту —
    # это опечатка, и подсветка по ней была бы враньём.
    names = {entry["id"] for entry in good}
    for entry in good:
        other = entry.get("implies")
        if other in (None, ""):
            continue
        other = str(other).strip()
        if other in names and other != entry["id"]:
            entry["implies"] = other
        else:
            entry.pop("implies", None)
    return good


def clean_menu(raw) -> list[dict]:
    """Разобранные пункты меню — уже без лишних полей, в заданном порядке.

    В отличие от `read_menu` ничего не пропускает молча: это путь записи,
    и сюда попадает только то, что прошло проверку в пульте.
    """
    items = []
    for entry in raw:
        clean = {"kind": entry["kind"], "id": entry["id"].strip(),
                 "title": entry["title"].strip()}
        icon = str(entry.get("icon") or "").strip()
        if icon:
            clean["icon"] = icon
        if entry["kind"] == "hotkey":
            clean["keys"] = str(entry["keys"]).strip()
        else:
            clean["url"] = str(entry["url"]).strip()
        if entry.get("toggle") is True:
            clean["toggle"] = True
        implies = str(entry.get("implies") or "").strip()
        if implies:
            clean["implies"] = implies
        items.append(clean)
    return items


def build_menu(item: dict, marks: list[dict] | None = None,
               полный: bool = False) -> list[dict]:
    """Готовые пункты меню: `[{key, kind, title, icon|image}]`.

    Ручные пункты идут первыми, в том порядке, как записаны в apps.json,
    а за ними — панель закладок Firefox. Так хозяин видит на первом месте
    то, что настроил сам, и не ищет закладку среди своих пунктов.

    `полный` добавляет ещё и `keys`/`url`. Телефону они не нужны и не
    должны уходить: выполнять пункт будет сервер, и лучше, чтобы само
    сочетание вообще не покидало компьютер. Внутри — другое дело, там без
    них выполнить нечего.
    """
    items = []
    entries = read_menu(item)
    # Состояние переключателей — наш счёт, а не ответ программы (см.
    # `core/toggles.py`), поэтому телефон рисует ровно то, что вернулось
    # здесь, и больше нигде ничего не додумывает.
    app_id = str(item.get("id") or item.get("title", ""))
    on = toggles.states(app_id, item, entries)
    for entry in entries:
        ready = {"key": f"{entry['kind']}:{entry['id']}", "kind": entry["kind"],
                 "title": entry["title"]}
        icon = entry.get("icon")
        if icon:
            ready["icon"] = icon
        elif entry["kind"] == "hotkey":
            ready["icon"] = "keyboard"
        else:
            ready["icon"] = "link"
        if entry.get("toggle"):
            ready["toggle"] = True
            ready["on"] = bool(on.get(ready["key"], False))
        if полный:
            if entry["kind"] == "hotkey":
                ready["keys"] = entry["keys"]
            else:
                ready["url"] = entry["url"]
        items.append(ready)

    if item.get("bookmarks") == "firefox":
        captions = mark_captions(marks or [])
        for mark in marks or []:
            ready = {"key": mark.get("id", ""), "kind": "bookmark",
                     "title": mark.get("title", ""), "url": mark.get("url", "")}
            if mark.get("image"):
                ready["image"] = mark["image"]
            caption = captions.get(mark.get("id", ""))
            if caption:
                ready["caption"] = caption
            items.append(ready)
    return items


def _caption_words(title: str) -> list[str]:
    """Слова названия закладки без мусора в начале и хвоста после « - ».

    «[0.5.5] Hellfire Gemling - Flameblast / Oil Grenade - …» → Hellfire,
    Gemling: версия в скобках и описание после тире на плитке не читаются.
    """
    text = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", title or "")
    text = re.split(r"\s+[-—–|:]\s+", text, maxsplit=1)[0]
    return re.findall(r"[\w'’]+", text)


def mark_captions(marks: list) -> dict:
    """Короткие подписи закладкам с одним и тем же сайтом. {id: подпись}.

    27.09: две закладки mobalytics (гайды PoE 2) на телефоне были одинаковыми
    плитками с одним логотипом — какая из них какая, не понять. Подпись
    получают только такие, у остальных плитка как была. Слов берём столько,
    чтобы подписи в группе различались, но не больше трёх: на плитке место
    на одно-два слова.
    """
    from core.bookmarks import _host

    groups: dict[str, list] = {}
    for mark in marks:
        host = _host(str(mark.get("url", "")))
        if host:
            groups.setdefault(host, []).append(mark)
    out: dict[str, str] = {}
    for same in groups.values():
        if len(same) < 2:
            continue
        words = [_caption_words(str(mark.get("title", ""))) for mark in same]
        caps = [""] * len(same)
        for count in range(1, 4):
            caps = [" ".join(parts[:count]) for parts in words]
            if len(set(caps)) == len(caps):
                break
        for mark, caption in zip(same, caps):
            if caption and mark.get("id"):
                out[mark["id"]] = caption
    return out


def find_app(app_id: str) -> dict | None:
    """Сама запись программы из apps.json. None — такой кнопки нет."""
    if not isinstance(app_id, str):
        return None
    for item in read_list():
        if (item.get("id") or item.get("title", "")) == app_id:
            return item
    return None


def find_menu(app_id: str, key: str, marks: list[dict] | None = None):
    """Пункт меню по ключу — только из apps.json и из закладок.

    None — такого пункта нет. Именно это и должно быть ответом на чужой
    ключ с телефона: выполнять нечего, и выполнять нечего из пришедшего.
    """
    if not isinstance(app_id, str) or not isinstance(key, str):
        return None
    item = find_app(app_id)
    if item is None:
        return None
    for ready in build_menu(item, marks, полный=True):
        if ready["key"] == key:
            return ready
    return None


def menu_states(app_id: str) -> dict:
    """Состояние переключателей программы для телефона: `{ключ: on}`."""
    item = find_app(app_id)
    if item is None:
        return {}
    return toggles.states(app_id, item, read_menu(item))


def flip_menu(app_id: str, key: str) -> tuple[bool, str]:
    """Переворачивает подсветку пункта, ничего не нажимая.

    Для случая, когда хозяин переключил мышкой в самой программе, а телефон
    об этом не узнал: подсветка разошлась — её надо поправить. Сочетание
    здесь не жмётся, поэтому программа может быть даже не запущена.
    """
    ready = find_menu(app_id, key)
    if ready is None:
        return False, f"нет такого пункта меню: {key}"
    if not ready.get("toggle"):
        return False, f"{ready['title']} — не переключатель"
    item = find_app(app_id) or {}
    toggles.flip(app_id, item, ready["key"])
    return True, ready["title"]


def open_url(app_id: str, url: str) -> tuple[bool, str]:
    """Открывает адрес в самой программе: у Firefox это путь плюс адрес.

    Не в браузере по умолчанию: хозяин нажимает «GitHub» в подменю Firefox
    и ждёт вкладку в этом же окне.
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False, f"не открываю такой адрес: {str(url)[:60]}"
    for item in load():
        if item["id"] != app_id:
            continue
        try:
            if item["kind"] == "url":
                os.startfile(url)
                return True, item["title"]
            if item.get("how") == "shell":
                # Оболочке адрес не передать, открываем файл как есть.
                os.startfile(item["path"])
                return True, item["title"]
            subprocess.Popen(
                [item["path"], *item.get("args", []), url],
                cwd=str(Path(item["path"]).parent),
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return True, item["title"]
        except Exception as exc:
            return False, f"{item['title']}: {exc}"
    return False, f"нет такой кнопки: {app_id}"


def open_web(url: str) -> tuple[bool, str]:
    """Открывает адрес в браузере по умолчанию — тем же, что закладки.

    Не в конкретной программе: телефон просит открыть на компьютере запрос из
    карточки поиска, и хозяин ждёт его в том браузере, которым сам пользуется.

    Только `http`/`https`: адрес приходит из интернета, и `file:` или
    `javascript:` открывать ему нельзя (как в `open_url`).
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False, f"не открываю такой адрес: {str(url)[:60]}"
    try:
        os.startfile(url)
        return True, "браузер"
    except Exception as exc:
        return False, f"браузер: {exc}"


def run_menu(app_id: str, key: str, marks: list[dict] | None = None) -> tuple[bool, str]:
    """Выполняет пункт меню, названный телефоном. Возвращает (вышло, что)."""
    if key == MENU_LAUNCH:
        return launch(app_id)

    ready = find_menu(app_id, key, marks)
    if ready is None:
        return False, f"нет такого пункта меню: {key}"

    if ready["kind"] == "bookmark":
        ok, what = open_url(app_id, ready.get("url", ""))
        return ok, f"{what} → {ready['title']}" if ok else f"{what}"

    if ready["kind"] == "site":
        ok, what = open_url(app_id, ready.get("url", ""))
        return ok, f"{what} → {ready['title']}" if ok else f"{what}"

    try:
        hotkeys.press(hotkeys.parse(ready.get("keys", "")))
    except Exception as exc:
        return False, f"{ready['title']}: {exc}"
    if ready.get("toggle"):
        # Сочетание нажали — значит, состояние перевернулось. Не наоборот:
        # нажатие, которое система не приняла, подсветку бы не изменило.
        toggles.flip(app_id, find_app(app_id) or {}, ready["key"])
    return True, ready["title"]


def load() -> list[dict]:
    """Читает список программ и оставляет только реально доступные."""
    ready = []
    for item in read_list():
        kind = item.get("kind", "app")
        entry = {
            "id": item.get("id") or item.get("title", ""),
            "title": item.get("title", "?"),
            "icon": item.get("icon", "app"),
            "kind": kind,
        }
        # Чем рисовать значок, решено в пульте — здесь только передаём дальше.
        for field in ("icon_source", "icon_file"):
            if item.get(field):
                entry[field] = item[field]

        if kind == "url":
            if item.get("url"):
                entry["url"] = item["url"]
                ready.append(entry)
            continue

        if kind == "store":
            # Приложение из Магазина: своего exe у него нет, запускается
            # по опознавателю вида Claude_pzs8sxrjxfjjc!Claude.
            if item.get("app_id"):
                entry["app_id"] = item["app_id"]
                ready.append(entry)
            continue

        if kind == "folder":
            # Своя папка хозяина. Кнопку не убираем, даже если папки нет: иначе
            # она молча исчезла бы и из настроек, и хозяин не понял бы, куда
            # делась. Проводник об этом честно скажет при нажатии.
            path = str(item.get("path") or "").strip()
            if path:
                entry["path"] = path
                ready.append(entry)
            continue

        path = resolve(item)
        if path:
            entry["path"] = path
            entry["args"] = item.get("args", [])
            if item.get("how"):
                entry["how"] = item["how"]
            ready.append(entry)

    return ready


def decorate_menus(apps: list[dict], on_error=None) -> list[dict]:
    """Добавляет кнопкам поле `menu` — для тех, у кого меню вообще есть.

    Закладки Firefox читаются один раз на весь список и только если хотя бы
    у одной кнопки стоит `bookmarks`. Без этого каждый `send_apps` копал бы
    профиль Firefox из-за Discord, а Discord меню может и не иметь.
    """
    raw = {item.get("id") or item.get("title", ""): item for item in read_list()}

    wants = any(
        read_menu(raw.get(app["id"], {})) or raw.get(app["id"], {}).get("bookmarks") == "firefox"
        for app in apps
    )
    marks: list[dict] = []
    if wants:
        from core import bookmarks

        try:
            marks = bookmarks.read(on_error=on_error)
        except Exception as exc:
            # Закладки — украшение. Их отсутствие не должно мешать отправить
            # телефону сами кнопки, поэтому ругаемся и едем дальше без них.
            if on_error:
                on_error(f"закладки Firefox не прочитались: "
                         f"{type(exc).__name__}: {exc}")

    for app in apps:
        items = build_menu(raw.get(app["id"], {}), marks)
        if items:
            app["menu"] = items
    return apps


def launch(app_id: str) -> tuple[bool, str]:
    """Запускает программу, открывает ссылку или папку.

    Возвращает (получилось, что именно).
    """
    for item in load():
        if item["id"] != app_id:
            continue

        try:
            if item["kind"] == "folder":
                # Папка открывается так же, как её открывает сам проводник, —
                # права не нужны. Нет папки — честно говорим, а не открываем
                # ничего похожего.
                path = Path(item["path"])
                if not path.is_dir():
                    return False, f"папки «{item['title']}» нет"
                os.startfile(str(path))
                return True, item["title"]

            if item["kind"] == "url":
                os.startfile(item["url"])
                return True, item["title"]

            if item["kind"] == "store":
                # Магазинные приложения живут в защищённой папке, и запускать
                # их файлом нельзя — только через оболочку по опознавателю.
                os.startfile(f"shell:AppsFolder\\{item['app_id']}")
                return True, item["title"]

            if item.get("how") == "shell":
                # Запуск через оболочку, а не напрямую.
                #
                # Есть программы, которые лежат обычным exe, но числятся
                # за пакетом Windows — например, ChatGPT. Прямой запуск
                # не выдаёт им «личность пакета», и они падают с ошибкой
                # «процесс не имеет идентификатора пакета». Оболочка её
                # выдаёт, поэтому такие зовём через неё.
                os.startfile(item["path"])
                return True, item["title"]

            subprocess.Popen(
                [item["path"], *item.get("args", [])],
                cwd=str(Path(item["path"]).parent),
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return True, item["title"]
        except Exception as exc:
            return False, f"{item['title']}: {exc}"
    return False, f"нет такой кнопки: {app_id}"


# --- Закрытие -------------------------------------------------------------
#
# Имя процесса берём из того же `apps.json`, что и запуск. Искать его иначе
# нельзя: закрывать что попало нельзя, а список — единственное, что запрещает
# закрыть не то.
#
# Кнопки закрытия на телефоне нет намеренно: закрытие необратимо, а локоть
# у экрана телефона вполне может оказаться на месте кнопки. Голосом и руками
# модели — можно, там человек сам сказал, что именно.

# Discord и Telegram запускаются не тем файлом, что в списке: у Discord в
# `path` лежит Update.exe, а работает Discord.exe, указанный в args — то ли
# следующим словом, то ли после знака равно.
PROCESS_FLAG = re.compile(r"^\s*--process-?start(?:=(.+))?\s*$", re.IGNORECASE)

# Чего не касаемся никогда: пульт и всё, чем он запущен; Claude — в нём
# работает помощник, который пишет этот код (закрыть его голосом значит
# оборвать работу посреди правки); и сама Windows.
SELF_NAMES = frozenset({
    "python.exe", "pythonw.exe", "wscript.exe", "claude.exe",
    "explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "lsass.exe",
    "services.exe", "svchost.exe", "smss.exe", "wininit.exe",
})

# Сколько ждём вежливое закрытие, прежде чем завершать.
CLOSE_WAIT = 5.0
# И сколько после terminate, прежде чем kill.
KILL_WAIT = 3.0
# Шаг опроса: процесс может уйти сразу после WM_CLOSE.
POLL = 0.25


def process_name(item: dict) -> str | None:
    """Имя процесса, который закрывать. None — закрывать нечем.

    Порядок тот же, в котором человек сам объяснил бы: сначала явное поле
    `process`, потом `--processStart` в аргументах (промежуточный запускальщик),
    и только потом имя файла из `path`.
    """
    given = str(item.get("process") or "").strip()
    if given:
        return given

    for index, arg in enumerate(item.get("args") or []):
        found = PROCESS_FLAG.match(str(arg))
        if not found:
            continue
        # Либо после знака равно, либо следующим словом.
        if found.group(1):
            return found.group(1).strip()
        following = item["args"][index + 1:index + 2]
        if following:
            return str(following[0]).strip()

    path = str(item.get("path") or "").strip()
    if path:
        name = Path(path.replace("\\", "/")).name
        if name.lower().endswith(".lnk"):
            # Программу добавили ярлыком (28.09, OBS): процесса «…(64bit).lnk»
            # не бывает, закрывать надо то, на что ярлык указывает.
            name = _shortcut_target_name(path) or name
        if name:
            return name
    return None


def _shortcut_target_name(path: str) -> str:
    """Имя exe, на который ведёт ярлык `.lnk`. Не прочитался — пустая строка."""
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return ""
    # Закрытие программ идёт из фонового потока, а COM там не поднят: без
    # CoInitialize WScript.Shell падает, и имя тихо оставалось бы «.lnk».
    pythoncom.CoInitialize()
    оболочка = ярлык = None
    цель = ""
    try:
        оболочка = win32com.client.Dispatch("WScript.Shell")
        ярлык = оболочка.CreateShortcut(os.path.expandvars(path))
        цель = str(ярлык.TargetPath or "").strip()
    except Exception:
        цель = ""
    finally:
        # Объекты COM отпускаем до CoUninitialize: иначе их уборка позже
        # сыплет «Win32 exception occurred releasing IUnknown».
        оболочка = ярлык = None
        pythoncom.CoUninitialize()
    return Path(цель.replace("\\", "/")).name if цель else ""


def _running(name: str) -> list:
    """Процессы с таким именем, кроме самого пульта."""
    import psutil

    found = []
    for proc in psutil.process_iter(["name"]):
        try:
            pname = (proc.info.get("name") or "").strip()
            if not pname or pname.lower() != name.lower():
                continue
            if pname.lower() in SELF_NAMES:
                continue
            found.append(proc)
        except Exception:
            continue
    return found


def _alive(procs: list) -> list:
    """Те из процессов, что ещё не исчезли (или стали зомби)."""
    left = []
    for proc in procs:
        try:
            if proc.is_running() and proc.status() != "zombie":
                left.append(proc)
        except Exception:
            continue
    return left


def _close_windows(pids: set) -> int:
    """Вежливое закрытие: WM_CLOSE видимым окнам верхнего уровня.

    Отдельно от процесса, потому что по одному WM_CLOSE на процесс не
    послать: окон может быть несколько, и не у всех об этом процессе главное
    то, что его закроет.
    """
    if not pids:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    posted = 0
    # Возврат из Python в C обязан быть честным bool: иначе Windows считает,
    # что исключение, и перестаёт звать callback.
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _lparam):
        nonlocal posted
        pid = wintypes.DWORD()
        try:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                if user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
                    posted += 1
        except Exception:
            pass
        return True

    user32.EnumWindows(PROC(each), 0)
    return posted


def _wait_gone(procs: list, seconds: float) -> list:
    """Ждёт, пока процессы исчезнут. Возвращает те, что остались."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    left = _alive(procs)
    while left and time.monotonic() < deadline:
        time.sleep(POLL)
        left = _alive(procs)
    return left


def close(app_id: str) -> tuple[bool, str]:
    """Закрывает программу из `apps.json`. Возвращает (получилось, что именно).

    Два шага, и второй обязателен: Discord и Telegram по крестику не
    закрываются, а прячутся в трей — процесс остаётся живым, и «закрыто»
    оказывается неправдой. Поэтому после WM_CLOSE, который их не берёт,
    процесс всё равно завершается.
    """
    item = None
    for entry in read_list():
        if entry.get("id") == app_id:
            item = entry
            break
    if item is None:
        return False, f"нет такой кнопки: {app_id}"

    title = item.get("title") or app_id
    kind = item.get("kind")

    if kind == "url":
        return False, f"{title} — это вкладка в браузере, её не закрываю"

    if kind == "folder":
        # Искать окно проводника не будем: проводников у человека бывает
        # несколько, а закрыть не тот — значит закрыть не то, что он просил.
        return False, f"{title} — папку закрыть не могу"

    if kind == "store":
        # У магазинного приложения своего имени у процесса нет: имя
        # исполняемого файла задаёт сам Windows, и хозяину оно ничего
        # не скажет. Просить его в apps.json — единственный честный выход.
        name = str(item.get("process") or "").strip()
        if not name:
            return False, (
                f"{title} — не знаю, как её закрыть: у магазинного приложения "
                f"нет своего файла. Укажи в apps.json поле process."
            )
    else:
        name = process_name(item)
        if not name:
            return False, f"{title} — не знаю, как её закрыть: нет ни process, ни path"

    if name.lower() in SELF_NAMES:
        return False, f"{title} — это сам пульт, его не закрываю"

    try:
        procs = _running(name)
    except Exception as exc:
        return False, f"{title}: не получилось найти процесс ({exc})"

    if not procs:
        return True, f"{title} и так не запущен"

    try:
        _close_windows({proc.pid for proc in procs})
    except Exception:
        # Окна закрыть не вышло — не повод отказываться: дальше terminate.
        pass

    left = _wait_gone(procs, CLOSE_WAIT)
    if not left:
        return True, f"{title} закрыта"

    for proc in left:
        try:
            proc.terminate()
        except Exception:
            continue
    left = _wait_gone(left, KILL_WAIT)
    if not left:
        return True, f"{title} закрыта"

    for proc in left:
        try:
            proc.kill()
        except Exception:
            continue
    if _wait_gone(left, KILL_WAIT):
        return False, f"{title} не закрылась: процесс не завершается"
    return True, f"{title} закрыта"
