r"""YouTube по просьбе: включить ролик, открыть канал, показать выдачу.

Кнопка YouTube в `apps.json` открывает главную, и на «включи ролик про …» её
не хватало. Ищем тем же пакетом `ddgs`, что и `core/web.py`. Замерено
27 сентября 2026 с этой машины:

    DDGS().videos("ремонт видеокарты", region="ru-ru", max_results=5)
        — 1.4 с, поле `content` — прямая ссылка https://www.youtube.com/watch?v=…,
          рядом `title`. Без region="ru-ru" — «No results found», то есть
          регион тут не украшение, а условие работы;

    DDGS().text("Linus Tech Tips канал site:youtube.com", region="ru-ru", …)
        — 2.9 с, но первый запрос бывает до 14 с, поэтому у канала свой предел
          (CHANNEL_TIMEOUT) и он заметно длиннее ролика.

В выдаче текстового поиска рядом с каналом лежат ролики, плейлисты,
`music.youtube.com`, а у канала часто хвост `/about` — это всё отсекает
`_channel_url`.

Основной путь — страница поиска самого YouTube (`_page`, см. ниже), `ddgs` —
запасной. Поиск идёт в отдельном потоке с общим пределом по времени: голосовой
разговор не может молчать дольше нескольких секунд, а сеть лежит куда угодно.
Сеть живёт только в `_page`, `_videos` и `_text`: в тестах подменяют именно
их.

Ничего не нашлось или сеть молчит — открывается страница поиска YouTube по
запросу: это всегда что-то полезное, а хозяину честно говорится, что нашла
только поиск. Врать про найденное нельзя — как и везде в `core/hands.py`.
"""

import json
import re
import threading
import time
from urllib.parse import quote_plus, urlparse

import config

# Пределы по времени. Ролик ищется быстро (1–2 с), канал — обычный текстовый
# поиск, и он иногда думает все четырнадцать секунд.
VIDEO_TIMEOUT = 6
CHANNEL_TIMEOUT = 8
RESULTS = 6
# Название ролика читается вслух ЦЕЛИКОМ: 27.09 в 11:23 она обрезала его до
# 60 знаков с многоточием («…в ESCAPE FROM…»), хозяин не услышал названия и
# трижды переспрашивал, а модель — открывала ролик заново. Длинное название
# обрезается по слову и БЕЗ многоточия: многоточие синтез читает как знак
# препинания, и хозяин слышит, что слова не кончились.
MAX_TITLE = 60
# Обрезка по слову вслух и в журнал. Журнал короче (там 120) — он для истории,
# а не для произнесения.
SAY_LIMIT = 140
# Меньше этого на запасной ddgs не остаётся смысла: он столько не отвечает.
MIN_FALLBACK = 0.5

# Память о последнем открытом: адрес → когда открыли (`time.monotonic`).
# Замок нужен: `act` зовётся из голосового цикла и из потока модели, а словарь
# читается и пишется без всякой защиты.
_recent_lock = threading.Lock()
_recent: dict[str, float] = {}


# --- Сеть в отдельном потоке с пределом по времени -------------------------


def _in_time(work, limit: float):
    """Выполняет `work` в своём потоке и ждёт не дольше `limit` секунд.

    Поток демонический: если сеть зависла намертво, он не должен удерживать
    программу при выходе. Возвращает то, что вернул `work`, и пробрасывает его
    исключение — решать, что с этим делать, вызывающему.
    """
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = work()
        except BaseException as exc:  # noqa: BLE001 — уходит вызывающему
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(limit)
    if thread.is_alive():
        raise TimeoutError(f"не уложилась в {limit:g} с")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _videos(query: str) -> list[dict]:
    """Единственное место, где умеет `videos`. Тесты подменяют его целиком."""
    from ddgs import DDGS

    return list(DDGS(timeout=VIDEO_TIMEOUT).videos(
        query, region="ru-ru", max_results=RESULTS) or [])


def _text(query: str) -> list[dict]:
    """Единственное место, где умеет `text`. Тесты подменяют его целиком."""
    from ddgs import DDGS

    return list(DDGS(timeout=CHANNEL_TIMEOUT).text(
        query, region="ru-ru", max_results=RESULTS) or [])


# --- Страница поиска самого YouTube -----------------------------------------
# Основной путь. 27 сентября поиск видео у ddgs отвечал через раз («No
# results found» на «как заменить термопасту на видеокарте»), а каналы искал
# 6–7 с и на «Wylsacom» подсовывал англоязычный дубль. Обычная страница
# поиска YouTube — та же, что открывает браузер, — отдаёт первый ролик и канал
# за ~1 с: в ней лежит `ytInitialData` с `videoRenderer`/`channelRenderer`.
PAGE_TIMEOUT = 6
_INITIAL = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)


def _page(query: str, channels: bool = False) -> dict | None:
    """`ytInitialData` страницы поиска. Тесты подменяют его целиком."""
    import httpx

    answer = httpx.get(
        search_url(query, channels=channels),
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru"},
        timeout=PAGE_TIMEOUT, follow_redirects=True,
    )
    found = _INITIAL.search(answer.text)
    return json.loads(found.group(1)) if found else None


def _renderers(data, key: str):
    """Все `videoRenderer` / `channelRenderer` из страницы, по порядку."""
    if isinstance(data, dict):
        if key in data and isinstance(data[key], dict):
            yield data[key]
        for value in data.values():
            yield from _renderers(value, key)
    elif isinstance(data, list):
        for value in data:
            yield from _renderers(value, key)


def _page_video(query: str):
    data = _page(query)
    for item in _renderers(data, "videoRenderer"):
        ident = str(item.get("videoId") or "").strip()
        if ident:
            title = "".join(run.get("text", "") for run in
                            item.get("title", {}).get("runs", []) if isinstance(run, dict))
            return f"https://www.youtube.com/watch?v={ident}", title.strip() or "ролик"
    return None


def _page_channel(name: str):
    data = _page(name, channels=True)
    for item in _renderers(data, "channelRenderer"):
        base = (item.get("navigationEndpoint", {}).get("browseEndpoint", {})
                .get("canonicalBaseUrl") or "")
        ident = str(item.get("channelId") or "").strip()
        path = base if base.startswith("/") else (f"/channel/{ident}" if ident else "")
        if path:
            title = str(item.get("title", {}).get("simpleText") or name).strip()
            return "https://www.youtube.com" + path, title
    return None


# Фильтр «только каналы» у страницы поиска YouTube.
CHANNEL_FILTER = "&sp=EgIQAg%253D%253D"
# Хвосты адреса канала: это отдельные страницы, а не сам канал.
TAILS = ("/about", "/videos", "/featured", "/playlists", "/streams",
         "/community", "/shorts", "/store")


# --- Адреса ----------------------------------------------------------------


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _path(url: str) -> str:
    return urlparse(url).path.strip("/")


def _trim_tails(path: str) -> str:
    """Срезает `/about`, `/videos` и прочее: канал — это то, что осталось."""
    for tail in TAILS:
        if path.lower().endswith(tail):
            return path[: -len(tail)].strip("/")
    return path


def _video_url(raw: str) -> str:
    """Прямая ссылка на ролик. Пустая строка — это не ролик.

    Короткие ссылки `youtu.be` и обычные `watch?v=` — всё, что умеет включить
    браузер. `music.youtube.com` сюда не берём: там трек, а не ролик.
    """
    url = (raw or "").strip()
    if not url:
        return ""
    host = _host(url)
    path = _path(url)
    if host == "youtu.be" and path:
        return f"https://youtu.be/{path.split('/')[0]}"
    if host in ("youtube.com", "m.youtube.com", "music.youtube.com") and path == "watch":
        ident = ""
        for part in urlparse(url).query.split("&"):
            if part.startswith("v="):
                ident = part[2:].strip()
                break
        return f"https://www.youtube.com/watch?v={ident}" if ident else ""
    return ""


def _channel_url(raw: str) -> str:
    """Адрес канала без хвостов. Пустая строка — это не канал.

    Канал узнаётся по началу адреса: `@имя`, `channel/UC…`, `c/Имя`, `user/Имя`
    или просто `/Имя`. Ролики, плейлисты, шортс, `music.youtube.com` и
    служебные страницы отсекаются.
    """
    url = (raw or "").strip()
    if not url or _host(url) != "youtube.com":
        return ""
    # Хвост срезается ДО разбора: без этого `/LinusTechTips/about` выглядит
    # как канал с двумя кусками и отбрасывается вовсе.
    path = _trim_tails(_path(url))
    if not path:
        return ""
    first = path.split("/")[0].lower()
    if first in NOT_CHANNEL:
        return ""
    # `@имя`, `channel/UC…`, `c/Имя`, `user/Имя` — с именем после служебного
    # куска. Всё остальное годится только одним куском: `/LinusTechTips`.
    if "/" in path and not (path.startswith("@") or first in ("channel", "c", "user")):
        return ""
    return f"https://www.youtube.com/{path}"


def _link_of(item) -> str:
    """Ссылка из результата: у `videos` это `content`, у `text` — `href`."""
    if not isinstance(item, dict):
        return ""
    for field in ("content", "href", "url"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    return ""


def _title_of(item) -> str:
    return str(item.get("title") or "").strip() if isinstance(item, dict) else ""


def _short(text: str, limit: int = MAX_TITLE) -> str:
    """Обрезает по слову С МНОГОТОЧИЕМ. Только для журнала.

    Вслух так не говорят: 27.09 хозяин услышал «Включила: …ESCAPE FROM…» и
    решил, что она не договорила название. Всё, что произносится, идёт через
    `_say`.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-—…") + "…"


def _say(text: str, limit: int = SAY_LIMIT) -> str:
    """Что произнести вслух: целиком, а очень длинное — по слову, без «…»."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-—…")


# Капс в названии. Синтез читает «ИМБОВАЯ ФИЧА ТУРНИРА» по буквам — как
# аббревиатуру, и получается «иммбоваяаа фииичааа туурниираа». 27.09, 11:23 —
# ровно это и было в журнале. Название обычным регистром читается нормально.
# Капсом считаем больше 60 % заглавных букв: у названий вроде «Wi-Fi или
# Ethernet: что выбрать» заглавных меньше, и такой вид не трогаем.
CAPS_SHARE = 0.6
_LATIN = re.compile(r"[A-Za-z]")
# Само слово в куске «ТУРНИРА,» или «(TARKOV)» — без знаков по краям.
_LETTERS = re.compile(r"[^\W\d_]+")


def _calm(title: str) -> str:
    """Название КАПСОМ — обычным видом. Обычное название не трогаем.

    Русские слова идут строчными (первое слово названия — с заглавной),
    латинские — с одной заглавной: «Имбовая фича турнира, которая довела
    рекрента в Escape From Tarkov: Arena». Ровно то, как это назвал автор
    ролика, — и то, что умеет произнести синтез.
    """
    title = " ".join((title or "").split())
    if not title:
        return ""
    letters = [ch for ch in title if ch.isalpha()]
    if not letters:
        return title
    upper = sum(1 for ch in letters if ch.isupper())
    if upper / len(letters) <= CAPS_SHARE:
        return title

    words = title.split(" ")
    for index, word in enumerate(words):
        # Знаки препинания («ТУРНИРА,») не трогаем — только само слово.
        match = _LETTERS.match(word)
        if match is None:
            continue
        body = match.group()
        head, tail = word[:match.start()], word[match.end():]
        if _LATIN.search(body):
            # Латинское слово: «ESCAPE» → «Escape», «TARKOV» → «Tarkov».
            fixed = body.capitalize()
        else:
            # Русское: строчными, и с заглавной только первое слово названия.
            fixed = body.capitalize() if index == 0 else body.lower()
        words[index] = head + fixed + tail
    return " ".join(words)

# Первые куски адреса, которые каналом не являются.
NOT_CHANNEL = ("watch", "playlist", "shorts", "results", "feed", "embed",
               "attribution_link", "live_chat", "select_site", "account",
               "premium", "gaming")
KINDS = ("video", "channel", "search")



# --- Поиск -----------------------------------------------------------------


def find_video(query: str, timeout: float | None = None):
    """Первый ролик по запросу. `(адрес, название)` или None.

    None — это и «не нашлось», и «сеть не ответила вовремя»: разбираться с
    этим отдельно хозяину незачем, в обоих случаях откроется страница поиска.
    """
    query = (query or "").strip()
    if not query:
        return None
    limit = VIDEO_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + limit
    # Сначала страница поиска самого YouTube — быстро и тот же первый ролик,
    # что увидел бы хозяин; не вышло — запасной путь через ddgs. Предел общий
    # на оба: голос не должен молчать дважды по шесть секунд.
    try:
        first = _in_time(lambda: _page_video(query), limit)
        if first:
            return first
    except Exception:
        pass
    left = deadline - time.monotonic()
    if left < MIN_FALLBACK:
        return None
    try:
        found = _in_time(lambda: _videos(query), left)
    except Exception:
        return None
    for item in found or []:
        url = _video_url(_link_of(item))
        if url:
            return url, _title_of(item) or "ролик"
    return None


def find_channel(name: str, timeout: float | None = None):
    """Первый канал по имени. `(адрес, название)` или None.

    Канал ищем обычным текстовым поиском с `site:youtube.com` — отдельного
    поиска по каналам у ddgs нет.
    """
    name = (name or "").strip()
    if not name:
        return None
    limit = CHANNEL_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + limit
    try:
        first = _in_time(lambda: _page_channel(name), limit)
        if first:
            return first
    except Exception:
        pass
    left = deadline - time.monotonic()
    if left < MIN_FALLBACK:
        return None
    query = f"{name} site:youtube.com"
    try:
        found = _in_time(lambda: _text(query), left)
    except Exception:
        return None
    for item in found or []:
        url = _channel_url(_link_of(item))
        if url:
            return url, _title_of(item) or name
    return None


def search_url(query: str, channels: bool = False) -> str:
    """Страница поиска YouTube. Её открывают, когда ролик или канал не нашлись.

    `channels` — фильтр «показывать только каналы»; он у самой страницы
    закодирован дважды, поэтому вставляется готовым.
    """
    url = ("https://www.youtube.com/results?search_query="
           + quote_plus((query or "").strip()))
    return url + CHANNEL_FILTER if channels else url


def open(url: str) -> tuple[bool, str]:
    """Открывает адрес на YouTube. Чужой адрес не открываем никогда.

    Кнопка YouTube в `apps.json` — `kind: url`, поэтому адрес уходит в браузер
    по умолчанию, и это единственный способ открыть что-то тут вообще.
    """
    url = (url or "").strip()
    if urlparse(url).scheme != "https" or _host(url) not in ("youtube.com", "youtu.be"):
        return False, f"не открываю такой адрес: {url[:60]}"
    from core import launcher

    try:
        return launcher.open_url("youtube", url)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# --- Общий вход: и голос, и модель ----------------------------------------


def phrase(kind: str, title: str) -> str:
    """Что сказать вслух, когда ролик или канал уже открыт.

    Название целиком (обрезается по слову и без многоточия только когда очень
    длинное) и обычным регистром: капс синтез читает по буквам.
    """
    said = _say(_calm(title))
    if kind == "channel":
        return f"Открыла канал {said}"
    return f"Включила: {said}"


def journal(what: dict) -> str:
    """Строка в журнал: «включила «Ремонт видеокарты» (запрос: …)»."""
    query = str(what.get("query", "")).strip()
    kind = what.get("kind")
    if kind == "search":
        return f"открыла поиск «{query}»"
    # Журнал — для истории, а не для произнесения: тут 120 знаков с «…».
    title = _short(_calm(str(what.get("title", ""))), 120) or "ролик"
    if what.get("repeat"):
        return f"уже открыт «{title}», второй раз не открывала (запрос: {query})"
    if kind == "channel":
        return f"открыла канал «{title}» (запрос: {query})"
    return f"включила «{title}» (запрос: {query})"


# --- Не открывать тот же ролик второй раз -----------------------------------
# 27.09, 11:23–11:24: ролик открылся три раза за минуту. Просьба была одна —
# повтор пришёл от модели, увидев жалобу «ты не договариваешь название, да?».
# Защита стоит здесь, у адреса, а не в уговорах модели: иначе любой новый
# повтор проходит тем же путём.
#
# Повтор разрешён, только когда хозяин прямо попросил открыть ещё раз
# (`again`): «открой ещё раз», «заново», «снова». Тогда это не повтор, а
# новая просьба, и молчать о ней нельзя.


def _seconds_since(url: str) -> float | None:
    """Сколько секунд назад открывали этот адрес. None — не открывали."""
    with _recent_lock:
        when = _recent.get(url)
    if when is None:
        return None
    return time.monotonic() - when


def _remember(url: str) -> None:
    with _recent_lock:
        _recent[url] = time.monotonic()


def forget_opened() -> None:
    """Забыть, что открывали. Ими пользуются тесты."""
    with _recent_lock:
        _recent.clear()


def _already_open(kind: str, query: str, url: str, title: str, again: bool) -> dict | None:
    """Ответ, если тот же адрес открывали недавно. None — открывать можно."""
    if again:
        return None
    ago = _seconds_since(url)
    if ago is None or ago >= config.YT_REPEAT_SECONDS:
        return None
    said = _say(_calm(title))
    return {
        "ok": True, "repeat": True, "kind": kind, "query": query, "url": url,
        "title": title,
        "text": "Он уже открыт.",
        # Слова для модели: `text` произносится голосом, а объяснение нужно ей,
        # чтобы не звать инструмент снова и не сказать хозяину «включила».
        "note": f"Этот ролик уже открыт {int(ago)} с назад, второй раз не открываю. "
                f"Если он не играет — скажи хозяину нажать воспроизведение. "
                f"Название: {said}",
    }


def act(query: str, kind: str = "video", again: bool = False) -> dict:
    """Сделать то, что просил хозяин, и вернуть, что получилось.

    Ролик и канал ищутся, и если не нашлось — открывается страница поиска:
    молчать или сказать «не могу» после просьбы «включи» хуже, чем показать
    выдачу. В ответе тогда честно, что нашлась только она.

    `again` — хозяин прямо попросил открыть ещё раз: повтор запрещён, но эта
    просьба его разрешает (см. `_already_open`).
    """
    query = (query or "").strip()
    kind = kind if kind in KINDS else "video"
    if not query:
        return {"ok": False, "error": "не сказано, что искать на YouTube"}
    if kind == "search":
        return _show(query, kind, again)

    found = find_video(query) if kind == "video" else find_channel(query)
    if found is None:
        return _show(query, kind, again)
    url, title = found
    opened = _already_open(kind, query, url, title, again)
    if opened is not None:
        return opened
    ok, what = open(url)
    if not ok:
        return {"ok": False, "error": f"не вышло: {what}"}
    _remember(url)
    return {"ok": True, "kind": kind, "query": query, "url": url, "title": title,
            "text": phrase(kind, title)}


def _show(query: str, kind: str, again: bool = False) -> dict:
    """Страница поиска: запасной выход и прямой ответ на «найди на ютубе»."""
    url = search_url(query, channels=kind == "channel")
    opened = _already_open("search", query, url, "выдача", again)
    if opened is not None:
        return opened
    ok, what = open(url)
    if not ok:
        return {"ok": False, "error": f"не вышло: {what}"}
    _remember(url)
    if kind == "search":
        # Просили именно выдачу: это не запасной путь, а то, что просили, и
        # врать про «не нашла» здесь незачем.
        text = f"Открыла поиск на YouTube: {query}"
    else:
        missed = "Канал" if kind == "channel" else "Ролик"
        text = f"{missed} не нашла, открыла только поиск: {query}"
    return {"ok": True, "kind": "search", "query": query, "url": url, "text": text}
