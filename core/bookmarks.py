r"""Панель закладок Firefox — для подменю на телефоне.

Хозяин хочет нажимать не «Firefox», а сразу «GitHub» или «Кинопоиск».
Закладки Firefox об этом знают, а проект — нет, поэтому берём их оттуда.

Почему так сложно, а не «открыл places.sqlite и прочитал»:

* Firefox держит `places.sqlite` и `favicons.sqlite` открытыми, пока он сам
  работает. Открытая база отдаёт мусор, а иногда просто «database is
  locked». Поэтому обе копируем во временную папку и читаем копию.
* Копию открываем строго на чтение (`file:…?mode=ro`): sqlite по умолчанию
  пробует долепить свой служебный `-wal` и для этого требует прав на запись.
* Значки лежат не в `places.sqlite`, а в отдельной `favicons.sqlite`, и
  лежат не по одному: на сайт их может быть несколько размеров. Берём тот,
  чья ширина ближе к 64, — ровно столько нужно телефону.

Любая ошибка здесь не должна ронять пульт: закладок может не быть (Firefox
не установлен), база может оказаться чужой версии. Тогда возвращаем пустой
список и пишем строку в журнал.
"""

from pathlib import Path

import config

# Папка значков — на уровне модуля, чтобы тесты подменяли её целиком
# (см. tests/test_data_guard.py).
CACHE_DIR = config.DATA_DIR / "icons" / "sites"

# Значок рисуется на телефоне 44 точки, но берётся в настоящих точках
# экрана — это около ста двадцати. 64 достаточно с запасом и заметно
# легче, чем сотня килобайт на каждый сайт.
SIZE = 64

# Значки Firefox не меняются по десять раз в минуту, а база не маленькая.
# Между чтениями отдаём прошлый результат.
MIN_AGE = 60.0

# Папка закладок на панели — именно её хозяин имеет в виду под «закладками».
TOOLBAR_GUID = "toolbar_____"

# Прямые дети папки панели: type = 1 (закладка), сортировка по position.
# Схема настоящего Firefox (сверено 26.09 на профиле хозяина): закладки —
# moz_bookmarks (parent, position, fk → moz_places.id), адреса — moz_places.
_BOOKMARKS_SQL = """
SELECT moz_places.url, COALESCE(moz_bookmarks.title, moz_places.title)
FROM moz_bookmarks
JOIN moz_places ON moz_places.id = moz_bookmarks.fk
WHERE moz_bookmarks.type = 1
  AND moz_bookmarks.parent = (SELECT id FROM moz_bookmarks WHERE guid = ?)
ORDER BY moz_bookmarks.position
"""

# Значок страницы: moz_pages_w_icons.id → moz_icons_to_pages.page_id →
# moz_icons.id. Высоты у значков нет, они квадратные.
_ICONS_SQL = """
SELECT moz_icons.data, moz_icons.width, moz_icons.width
FROM moz_pages_w_icons
JOIN moz_icons_to_pages ON moz_icons_to_pages.page_id = moz_pages_w_icons.id
JOIN moz_icons ON moz_icons.id = moz_icons_to_pages.icon_id
WHERE moz_pages_w_icons.page_url = ?
ORDER BY ABS(COALESCE(moz_icons.width, 0) - ?)
LIMIT 1
"""

# Страницы с точно таким адресом может не быть (закладка на корень сайта,
# а заходили на вложенную) — тогда корневой значок сайта: root = 1.
_ROOT_ICON_SQL = """
SELECT data, width, width FROM moz_icons
WHERE root = 1 AND icon_url LIKE ?
ORDER BY ABS(COALESCE(width, 0) - ?)
LIMIT 1
"""

_cache: list = []
_cached_at: float = -1e9


def profiles_ini():
    """Где лежит profiles.ini.

    Отдельной функцией, а не константой: тесту нужен свой путь, иначе он
    полез бы в живой профиль хозяина.
    """
    import os

    return Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "profiles.ini"


def forget() -> None:
    """Забыть прочитанное — следующий `read` пойдёт в базу заново."""
    global _cached_at
    _cached_at = -1e9


def _sections(text: str) -> dict:
    """Разбор profiles.ini своими руками.

    ConfigParser тут лишний и даже мешает: у Firefox в этом файле нет ничего,
    кроме секций и строк `ключ=значение`, а пустые значения и повторы он
    порой трактует как ошибку формата.
    """
    found: dict = {}
    name = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            name = line.strip("[]").strip()
            found.setdefault(name, {})
            continue
        if not line or line.startswith((";", "#")) or name is None:
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[name][key.strip().lower()] = value.strip()
    return found


def _folder(root, value: str):
    """Путь из profiles.ini. Он записан относительно папки Firefox."""
    target = Path(value)
    return target if target.is_absolute() else root / target


def profile():
    """Папка профиля по умолчанию. None — разобраться не вышло.

    Firefox сам пишет, какой профиль выбран: в секции `Install…` строка
    `Default=Profiles/…`. Если её нет (правленый профиль, нестандартная
    установка), берём помеченный `Default=1`, а если и его нет — папку с
    обычным для Firefox именем.
    """
    path = profiles_ini()
    try:
        found = _sections(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        found = {}

    root = path.parent

    for section, values in found.items():
        if section.lower().startswith("install") and values.get("default"):
            folder = _folder(root, values["default"])
            if (folder / "places.sqlite").is_file():
                return folder

    for section, values in found.items():
        if not section.lower().startswith("profile"):
            continue
        if values.get("default") != "1" or not values.get("path"):
            continue
        folder = _folder(root, values["path"])
        if (folder / "places.sqlite").is_file():
            return folder

    # Запасной путь: папка обычного вида. Имена у них разные, поэтому берём
    # ту, где действительно есть база.
    try:
        for folder in sorted(root.glob("*.default-release")):
            if (folder / "places.sqlite").is_file():
                return folder
    except OSError:
        pass
    return None


def _fit(image):
    """Ставит значок в квадрат SIZE×SIZE, сохраняя пропорции.

    Тот же приём, что в app_icons: у сайтов значки квадратные, но не
    обязаны быть такими, и без подгонки они смотрелись бы разного размера.
    """
    from PIL import Image

    image = image.convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box:
        image = image.crop(box)
    if not image.width or not image.height:
        return None
    scale = (SIZE * 0.94) / max(image.size)
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2))
    return canvas


def _host(url: str) -> str:
    """Имя файла значка — по хосту, без www и без слешей."""
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return "".join(char for char in host if char.isalnum() or char in ".-_")


def _as_png(blob: bytes, host: str):
    """PNG-файл значка в кеше или None — значка нет.

    Pillow читает PNG, ICO и JPEG. SVG он не умеет, а Firefox для части
    сайтов кладёт именно SVG: такой значок пропускаем, телефон нарисует
    букву — это лучше, чем пустая кнопка.
    """
    import io

    try:
        from PIL import Image
    except ImportError:
        return None

    if not host:
        return None
    target = CACHE_DIR / f"{host}.png"
    if target.is_file():
        return target

    try:
        with Image.open(io.BytesIO(blob)) as source:
            source.load()
            image = _fit(source)
    except Exception:
        return None
    if image is None:
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        image.save(target, "PNG")
    except OSError:
        return None
    return target


def _collect(folder, on_error=None) -> list:
    """Читает закладки и значки из копий баз Firefox."""
    import shutil
    import sqlite3
    import tempfile
    from contextlib import closing

    from core import app_icons

    places = folder / "places.sqlite"
    favicons = folder / "favicons.sqlite"
    if not places.is_file():
        if on_error:
            on_error(f"в профиле Firefox нет places.sqlite: {folder}")
        return []

    work = Path(tempfile.mkdtemp(prefix="truba-firefox-"))
    try:
        try:
            shutil.copy2(places, work / "places.sqlite")
            have_icons = favicons.is_file()
            if have_icons:
                shutil.copy2(favicons, work / "favicons.sqlite")
        except OSError as exc:
            if on_error:
                on_error(f"не скопировали базу Firefox: {exc}")
            return []

        def open_ro(name):
            # Слеши обязательны: в file:-URI обратный слэш не разделитель,
            # и база просто не откроется. mode=ro тоже обязателен — иначе
            # sqlite пойдёт дописывать свой -wal и потребует прав на запись.
            uri = f"file:{(work / name).as_posix()}?mode=ro"
            return closing(sqlite3.connect(uri, uri=True))

        with open_ro("places.sqlite") as db:
            rows = db.execute(_BOOKMARKS_SQL, (TOOLBAR_GUID,)).fetchall()

        rows = [(str(url or ""), str(title or "")) for url, title in rows]
        rows = [row for row in rows if row[0].startswith(("http://", "https://"))]

        items = [{"title": title or url, "url": url} for url, title in rows]
        if not items:
            return items

        # Значки читаем одним соединением на все сайты: открывать базу
        # на каждый сайт — это заметно дольше, а она уже скопирована.
        pictures: dict = {}
        if have_icons:
            try:
                with open_ro("favicons.sqlite") as icons:
                    for url, in [(r[0],) for r in rows]:
                        found = icons.execute(_ICONS_SQL, (url, SIZE)).fetchone()
                        if not (found and found[0]) and _host(url):
                            origin = url.split("://", 1)[0] + "://" + _host(url) + "/%"
                            found = icons.execute(_ROOT_ICON_SQL, (origin, SIZE)).fetchone()
                        if found and found[0]:
                            pictures[url] = _as_png(bytes(found[0]), _host(url))
            except Exception as exc:
                # Значок — украшение. Нет значка, телефон нарисует букву.
                if on_error:
                    on_error(f"значки сайтов не прочитались: "
                             f"{type(exc).__name__}: {exc}")
                pictures = {}

        for item in items:
            picture = pictures.get(item["url"])
            if picture is not None:
                item["image"] = app_icons.as_data_url(picture)
        return items
    except Exception as exc:
        if on_error:
            on_error(f"закладки Firefox не прочитались: {type(exc).__name__}: {exc}")
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read(force: bool = False, on_error=None) -> list:
    """Закладки панели Firefox: `[{id, title, url, image}]`.

    `image` — data-URL PNG, как у значков программ; без значка его нет.
    Пустой список — это «закладок нет», а не поломка: хозяин мог и не
    открывать Firefox. Ошибка уходит в `on_error` строкой, пульт не падает.

    Перечитываем не чаще раза в минуту; `force` — исключение.
    """
    global _cache, _cached_at

    import time

    if not force and time.monotonic() - _cached_at < MIN_AGE:
        return [dict(item) for item in _cache]

    folder = profile()
    if folder is None:
        if on_error:
            on_error("профиль Firefox не найден — панель закладок пуста")
        _cache, _cached_at = [], time.monotonic()
        return []

    items = _collect(folder, on_error)
    for number, item in enumerate(items, 1):
        item["id"] = f"bm:{number}"
    _cache, _cached_at = items, time.monotonic()
    return [dict(item) for item in items]
