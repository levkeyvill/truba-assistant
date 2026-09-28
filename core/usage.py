"""Расход токенов и денег: учёт, три периода и цены.

Каждый ответ мозга присылает событие `tokens`
(`{"prompt": N, "cached": N, "completion": N, "calls": N, "model": ...}`).
Пульт (`ui/web_runtime.py`) пишет его сюда строкой в `data/usage.jsonl`,
а Панель спрашивает `summary` и показывает три периода: за текущий
запуск, за сегодня и за неделю.

Деньги считаются по таблице `config.PRICES` — доллары за 1 млн токенов,
проверены хозяином 26.09.2026. У DeepSeek в часы пика цены вдвое выше,
поэтому время каждой записи важно. Модели OpenRouter в таблице не держат:
их цены берутся живые из открытого каталога, без ключа, и лежат в кеше на
сутки. Курс доллара — тот же открытый курс ЦБ, кеш на сутки.

Сеть отсюда ходит только в двух местах: `openrouter.ai/api/v1/models` и
`www.cbr-xml-daily.ru/daily_json.js`. Всё это — в фоне, отдельным потоком,
не чаще раза в сутки, с таймаутом 8 секунд; в голосовом цикле и в потоке
окна сети нет. Нет сети — берём прошлый кеш, нет кеша — модель остаётся
без цены и в сумму не входит.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

USAGE_PATH = config.DATA_DIR / "usage.jsonl"
OPENROUTER_PRICES_PATH = config.DATA_DIR / "openrouter_prices.json"
USD_RUB_PATH = config.DATA_DIR / "usd_rub.json"

# Сколько дней держим историю расхода. Панели хватает недели, месяц — с
# запасом; старше — выкидываем при старте пульта, чтобы файл не рос вечно.
KEEP_DAYS = 35
# Сутки между походами в сеть за ценами и курсом.
PRICE_TTL = 24 * 3600
NET_TIMEOUT = 8.0

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

# Имя модели в событии: провайдер/модель, ровно как Brain.model.
OPENROUTER_PREFIX = "openrouter/"
# Локальный сервер. Ответы от него бесплатны, поэтому цена нулевая, а в
# сводке рядом с нулём стоит пометка «локальная — бесплатно».
LOCAL_PREFIX = "local/"
PERIODS = ("session", "today", "week")

_write_lock = threading.Lock()
_net_lock = threading.Lock()

# --- Запись ---------------------------------------------------------------


def record(data: dict) -> None:
    """Дописывает строку о расходе одного ответа в журнал."""
    if not isinstance(data, dict):
        return
    note = data.get("note")
    entry = {
        # С микросекундами: иначе ответ, случившийся в первую секунду после
        # запуска пульта, обрезался бы до «на секунду раньше» и не попал бы
        # в период «Сеанс».
        "at": datetime.now().isoformat(),
        "model": str(data.get("model") or "")[:120],
        "prompt": _целое(data.get("prompt")),
        "cached": _целое(data.get("cached")),
        "completion": _целое(data.get("completion")),
        "calls": _целое(data.get("calls")),
    }
    if isinstance(note, str) and note:
        entry["note"] = note[:40]
    try:
        with _write_lock:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_PATH.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Сбой диска не должен обрывать разговор: расход — не срочное.
        return


def prune(keep_days: int = KEEP_DAYS, now: datetime | None = None) -> int:
    """Выкидывает записи старше `keep_days` суток, переписывая файл.

    Сколько записей убрано — возвращает для журнала. Держим ровно один
    файл: записей немного, а читать его целиком приходится каждый раз при
    открытии Панели.
    """
    момент = now or datetime.now()
    border = момент - timedelta(days=keep_days)
    try:
        with _write_lock:
            entries = _читать_файл()
            свежие = [e for e in entries if _момент(e) >= border]
            if len(свежие) == len(entries):
                return 0
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            text = "".join(json.dumps(e, ensure_ascii=False) + "\n"
                           for e in свежие)
            временный = USAGE_PATH.with_name(USAGE_PATH.name + ".new")
            временный.write_text(text, encoding="utf-8")
            временный.replace(USAGE_PATH)
            return len(entries) - len(свежие)
    except OSError:
        return 0


def _читать_файл() -> list[dict]:
    """Все записи журнала; битые строки молча пропускаются."""
    try:
        text = USAGE_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for строка in text.splitlines():
        строка = строка.strip()
        if not строка:
            continue
        try:
            item = json.loads(строка)
        except ValueError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _момент(entry: dict) -> datetime:
    """Время записи по полю `at`; не разобралось — считаем самой старой."""
    try:
        момент = datetime.fromisoformat(str(entry.get("at") or ""))
    except (TypeError, ValueError):
        return datetime.min
    if момент.tzinfo is not None:
        момент = момент.astimezone().replace(tzinfo=None)
    return момент


def _целое(value) -> int:
    try:
        число = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, число)


# --- Цены -----------------------------------------------------------------


def price_for(model: str, when: datetime | None = None) -> dict | None:
    """Цена одной модели в долларах за 1 млн токенов или None.

    `when` нужен DeepSeek: в часы пика его цены вдвое выше ночных.

    Локальная модель (`local/…` — ровно как называет себя Brain.model)
    стоит ноль. Не «цены нет»: она действительно бесплатна, и путать это с
    неизвестной ценой нечестно — отсюда и отдельная пометка в сводке.
    """
    имя = str(model or "")
    if not имя:
        return None
    if имя.startswith(LOCAL_PREFIX):
        return {"input": 0.0, "cached": 0.0, "output": 0.0}
    if имя.startswith(OPENROUTER_PREFIX):
        цена = openrouter_prices().get(имя[len(OPENROUTER_PREFIX):])
        return цена if isinstance(цена, dict) else None
    строка = config.PRICES.get(имя)
    if not строка:
        return None
    return _с_пиком(строка, when or datetime.now())


def _с_пиком(строка: dict, when: datetime) -> dict:
    """Удваивает цену DeepSeek, если момент попал в часы пика."""
    цена = {
        "input": float(строка["input"]),
        "cached": float(строка["cached"]),
        "output": float(строка["output"]),
    }
    множитель = float(строка.get("peak") or 1)
    if множитель != 1 and _в_пике(when):
        цена = {к: v * множитель for к, v in цена.items()}
    return цена


def _в_пике(when: datetime) -> bool:
    """Часы пика DeepSeek: 01:00–04:00 и 06:00–10:00 UTC, пн–пт.

    Время записи местное, а прайс — по UTC, поэтому переводим. Правило
    написано вслух, чтобы не зависеть от настройки часов компьютера.
    """
    if when.tzinfo is None:
        when = when.astimezone()
    utc = when.astimezone(timezone.utc)
    if utc.weekday() > 4:
        return False
    return 1 <= utc.hour < 4 or 6 <= utc.hour < 10


def _стоимость(запись: dict, when: datetime) -> float | None:
    """Доллары за одну запись или None, если модели нет в прайсе."""
    цена = price_for(запись.get("model", ""), when)
    if цена is None:
        return None
    prompt = _целое(запись.get("prompt"))
    cached = min(_целое(запись.get("cached")), prompt)
    completion = _целое(запись.get("completion"))
    сумма = ((prompt - cached) * цена["input"]
             + cached * цена["cached"]
             + completion * цена["output"])
    return сумма / 1_000_000


# --- Сводка ---------------------------------------------------------------


def summary(session_start, now: datetime | None = None) -> dict:
    """Три периода расхода: запуск пульта, сегодня и неделя.

    `session_start` — время, когда поднялся пульт; его помнит WebRuntime.
    Сегодня — с полуночи по местному времени, неделя — последние семь
    суток назад от `now`.
    """
    момент = now or datetime.now()
    начало = {
        "session": _момент_из(session_start),
        "today": момент.replace(hour=0, minute=0, second=0, microsecond=0),
        "week": момент - timedelta(days=7),
    }
    накопители = {имя: _Период() for имя in PERIODS}
    for запись in _читать_файл():
        когда = _момент(запись)
        for имя in PERIODS:
            if когда >= начало[имя]:
                накопители[имя].добавить(запись, когда)
    return {
        "periods": {имя: накопители[имя].итог() for имя in PERIODS},
        "rub": usd_rub(),
        "prices": {
            "openai": config.PRICES_CHECKED,
            "deepseek": config.PRICES_CHECKED,
            "openrouter": _дата(openrouter_prices_stamp()),
        },
        "now": момент.isoformat(timespec="seconds"),
    }


def _момент_из(value) -> datetime:
    """Принимает и datetime, и ISO-строку, и None — молча, без исключений."""
    if isinstance(value, datetime):
        момент = value
    else:
        try:
            момент = datetime.fromisoformat(str(value or ""))
        except (TypeError, ValueError):
            момент = datetime.now()
    if момент.tzinfo is not None:
        момент = момент.astimezone().replace(tzinfo=None)
    return момент


def _дата(stamp) -> str | None:
    """Человеческая дата из метки времени кеша."""
    if not stamp:
        return None
    момент = _момент_из(stamp)
    if момент.year <= 1:
        return None
    return момент.strftime("%d.%m.%y")


class _Период:
    """Накопитель одного периода: токены, деньги и разбивка по моделям."""

    def __init__(self) -> None:
        self.prompt = 0
        self.cached = 0
        self.completion = 0
        self.calls = 0
        self.usd = 0.0
        self.unpriced = False
        # Были ли в периоде ответы локальной модели: они ничего не стоят,
        # и пульт должен сказать об этом словами, а не нулём.
        self.local = False
        self.модели: dict[str, dict] = {}

    def добавить(self, запись: dict, когда: datetime) -> None:
        prompt = _целое(запись.get("prompt"))
        cached = min(_целое(запись.get("cached")), prompt)
        completion = _целое(запись.get("completion"))
        self.prompt += prompt
        self.cached += cached
        self.completion += completion
        self.calls += _целое(запись.get("calls"))

        имя = str(запись.get("model") or "") or "без модели"
        строка = self.модели.setdefault(имя, {
            "model": имя, "prompt": 0, "cached": 0, "completion": 0,
            "usd": 0.0})
        строка["prompt"] += prompt
        строка["cached"] += cached
        строка["completion"] += completion

        if str(запись.get("model") or "").startswith(LOCAL_PREFIX):
            self.local = True

        стоит = _стоимость(запись, когда)
        if стоит is None:
            self.unpriced = True
            строка["usd"] = None
            return
        self.usd += стоит
        строка["usd"] = (строка["usd"] or 0.0) + стоит

    def итог(self) -> dict:
        модели = []
        for строка in self.модели.values():
            стоит = строка["usd"]
            модели.append({
                "model": строка["model"],
                "prompt": строка["prompt"],
                "cached": строка["cached"],
                "completion": строка["completion"],
                "usd": round(стоит, 6) if стоит is not None else None,
            })
        # Сначала дорогие, безценные — в конец: так видно, кто съел деньги.
        модели.sort(key=lambda s: (s["usd"] is None, -(s["usd"] or 0.0)))
        return {
            "prompt": self.prompt,
            "cached": self.cached,
            "completion": self.completion,
            "calls": self.calls,
            "usd": round(self.usd, 6),
            "unpriced": self.unpriced,
            "local": self.local,
            "models": модели,
        }


# --- OpenRouter и курс: кеш на сутки, сеть в фоне -------------------------


def openrouter_prices() -> dict:
    """Кеш цен OpenRouter в долларах за 1 млн токенов: {id: {...}}."""
    try:
        данные = json.loads(OPENROUTER_PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(данные, dict):
        return {}
    цены = данные.get("prices")
    if not isinstance(цены, dict):
        return {}
    return {k: v for k, v in цены.items() if isinstance(v, dict)}


def openrouter_prices_stamp():
    """Когда кеш цен OpenRouter обновляли."""
    try:
        данные = json.loads(OPENROUTER_PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return данные.get("at") if isinstance(данные, dict) else None


def usd_rub():
    """Курс доллара ЦБ из кеша или None — тогда Панель молчит про рубли."""
    try:
        данные = json.loads(USD_RUB_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(данные, dict):
        return None
    try:
        курс = float(данные.get("rub"))
    except (TypeError, ValueError):
        return None
    return курс if курс > 0 else None


def _свежий(path: Path, ttl: float = PRICE_TTL) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < ttl
    except OSError:
        return False


def refresh_prices() -> None:
    """Обновляет кеши цен OpenRouter и курса доллара. Сети нет — тоже ок.

    Зовётся из фонового потока пульта. Поток один: два одновременных
    похода в сеть ничего не дают, только мешают друг другу.
    """
    if not _net_lock.acquire(blocking=False):
        return
    try:
        if not _свежий(OPENROUTER_PRICES_PATH):
            _обновить_openrouter()
        if not _свежий(USD_RUB_PATH):
            _обновить_курс()
    finally:
        _net_lock.release()


def refresh_prices_soon() -> None:
    """Запускает обновление кешей в отдельном потоке — не из потока окна."""
    threading.Thread(target=refresh_prices, daemon=True).start()


def _обновить_openrouter() -> None:
    try:
        данные = _скачать(OPENROUTER_URL)
    except Exception:
        return  # нет сети — остаёмся на прошлом кеше
    модели = данные.get("data") if isinstance(данные, dict) else None
    if not isinstance(модели, list):
        return
    цены = {}
    for модель in модели:
        if not isinstance(модель, dict):
            continue
        id_ = модель.get("id")
        прайс = модель.get("pricing")
        if not isinstance(id_, str) or not isinstance(прайс, dict):
            continue
        вход = _число(прайс.get("prompt"))
        выход = _число(прайс.get("completion"))
        if вход is None or выход is None:
            continue
        # Каталог отдаёт цены за токен строками; нам нужны за миллион.
        кеш = _число(прайс.get("input_cache_read"))
        цены[id_] = {
            "input": вход * 1_000_000,
            "cached": (кеш if кеш is not None else вход) * 1_000_000,
            "output": выход * 1_000_000,
        }
    if not цены:
        return
    _записать_json(OPENROUTER_PRICES_PATH, {
        "at": datetime.now().isoformat(timespec="seconds"), "prices": цены})


def _обновить_курс() -> None:
    try:
        данные = _скачать(CBR_URL)
    except Exception:
        return
    valute = данные.get("Valute") if isinstance(данные, dict) else None
    usd = valute.get("USD") if isinstance(valute, dict) else None
    курс = _число(usd.get("Value")) if isinstance(usd, dict) else None
    if курс is None or курс <= 0:
        return
    _записать_json(USD_RUB_PATH, {
        "at": datetime.now().isoformat(timespec="seconds"), "rub": курс})


def _число(value) -> float | None:
    """Цена из строки каталога («0.000015») в число."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _скачать(url: str):
    """Одна загрузка без ключа, с таймаутом и без редиректов."""
    import httpx

    with httpx.Client(timeout=NET_TIMEOUT, follow_redirects=False) as client:
        ответ = client.get(url, headers={"Accept": "application/json"})
        ответ.raise_for_status()
        return ответ.json()


def _записать_json(path: Path, данные: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        временный = path.with_name(path.name + ".new")
        временный.write_text(json.dumps(данные, ensure_ascii=False),
                             encoding="utf-8")
        временный.replace(path)
    except OSError:
        return
