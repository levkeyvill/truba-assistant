r"""Погода в городе: Open-Meteo, раз в полчаса, кеш на диске.

Ключа не нужно — координаты лежат в настройках (`WEATHER_LAT`, `WEATHER_LON`),
а подпись города по-русски (`WEATHER_CITY`). Сеть отсюда ходит в одном месте:
`api.open-meteo.com`, без редиректов, с таймаутом 8 секунд, и только из
фонового потока — в потоке окна и в голосовом цикле сети нет. Пока город
не выбран (у чужого человека его нет), не ходим в сеть вовсе.

Обновляет `Watcher`: он же кладёт свежее в кеш `data/weather.json` и шлёт
телефону сообщение `weather`. Нет сети — телефон получает прошлый кеш и его
возраст в минутах (`age_min`), а страница сама скажет «обновлено N ч назад».

Коды погоды — таблица WMO из документации Open-Meteo. Нам хватает одиннадцати
слов; значки рисует страница, здесь только имена, и они совпадают с `ICONS`.
"""

import json
import threading
import time
from pathlib import Path
from urllib.parse import quote

import config

CACHE_PATH = config.DATA_DIR / "weather.json"

# Полчаса между походами в сеть. Погода меняется медленно, телефон стоит
# сбоку и смотрит издали: поминутные обновления ему ничего не дают, а сеть
# и трафик — едят.
TTL = 30 * 60
NET_TIMEOUT = 8.0
# На сколько дней вперёд берём прогноз: сегодня нужен и завтра.
DAYS = 2

API = "https://api.open-meteo.com/v1/forecast"

# Коды WMO → (слово по-русски, имя значка страницы). Таблица из
# https://open-meteo.com/en/docs, проверена 26.09.2026. Слов одиннадцать:
# телефон смотрит издали, «умеренная морось» там не читается.
CODES = {
    0:  ("ясно",        "sun"),
    1:  ("ясно",        "sun"),
    2:  ("малооблачно", "sun-cloud"),
    3:  ("облачно",     "cloud"),
    45: ("туман",       "fog"),
    48: ("туман",       "fog"),
    51: ("морось",      "rain"),
    53: ("морось",      "rain"),
    55: ("морось",      "rain"),
    56: ("морось",      "rain"),
    57: ("морось",      "rain"),
    61: ("дождь",       "rain"),
    63: ("дождь",       "rain"),
    65: ("ливень",      "rain"),
    66: ("дождь",       "rain"),
    67: ("дождь",       "rain"),
    71: ("снег",        "snow"),
    73: ("снег",        "snow"),
    75: ("снегопад",    "snow"),
    77: ("снег",        "snow"),
    80: ("дождь",       "rain"),
    81: ("дождь",       "rain"),
    82: ("ливень",      "rain"),
    85: ("снегопад",    "snow"),
    86: ("снегопад",    "snow"),
    95: ("гроза",       "storm"),
    96: ("гроза",       "storm"),
    97: ("гроза",       "storm"),
    99: ("гроза",       "storm"),
}
# Кода нет или он новый: молчим, а не выдумываем. Пустое слово страница
# не рисует, лишнего значка не будет.
UNKNOWN = ("", "")

_net_lock = threading.Lock()


# --- Разбор ---------------------------------------------------------------


def describe(code) -> tuple[str, str]:
    """Код погоды WMO → (слово, имя значка)."""
    return CODES.get(_целое(code), UNKNOWN)


def message(данные: dict) -> dict | None:
    """Ответ Open-Meteo → сообщение телефону. None — разбирать нечего."""
    if not isinstance(данные, dict):
        return None
    сейчас = данные.get("current")
    if not isinstance(сейчас, dict):
        return None
    температура = _целое(сейчас.get("temperature_2m"))
    if температура is None:
        return None
    слово, значок = describe(сейчас.get("weather_code"))
    return {
        "type": "weather",
        "city": config.WEATHER_CITY,
        "now": {
            "temp": температура,
            "feels": _целое(сейчас.get("apparent_temperature"), температура),
            "text": слово,
            "icon": значок,
            "wind": _ветер(сейчас.get("wind_speed_10m")),
        },
        "days": days(данные.get("daily")),
    }


def days(данные) -> list:
    """Дневная часть ответа: сегодня и завтра, как их ждёт телефон."""
    if not isinstance(данные, dict):
        return []
    даты = данные.get("time")
    максимумы = _ряд(данные.get("temperature_2m_max"))
    минимумы = _ряд(данные.get("temperature_2m_min"))
    коды = _ряд(данные.get("weather_code"))
    осадки = _ряд(данные.get("precipitation_probability_max"))

    итог = []
    for место, дата in enumerate(даты if isinstance(даты, list) else []):
        максимум = _целое(_take(максимумы, место))
        минимум = _целое(_take(минимумы, место))
        if максимум is None and минимум is None:
            continue
        слово, значок = describe(_take(коды, место))
        вероятность = _take(осадки, место)
        итог.append({
            "date": str(дата)[:10],
            "max": максимум,
            "min": минимум,
            "text": слово,
            "icon": значок,
            # None — прогноза осадков не было вовсе. Ноль — будет, но не
            # начнётся, и это разные вещи: их нельзя сливать.
            "rain": int(вероятность) if вероятность is not None else None,
        })
    return итог[:DAYS]


# --- Кеш ------------------------------------------------------------------


def cached() -> dict | None:
    """Сообщение из кеша с его возрастом в минутах. None — кеша нет.

    Кеш чужого города не отдаём: координаты внутри неё не совпадают с
    настройками, значит это прогноз того места, которое хозяин уже сменил
    или вовсе убрал. Показывать его — значит врать.
    """
    данные = _прочитанный()
    if данные is None:
        return None
    сообщение = {k: v for k, v in данные.items() if k != "at"}
    for ключ in ("lat", "lon"):
        сообщение.pop(ключ, None)
    сообщение["type"] = "weather"
    сообщение["age_min"] = _возраст(данные.get("at"))
    return сообщение


def _прочитанный() -> dict | None:
    """Кеш с диска, если он наш и разбирается. Иначе None."""
    try:
        данные = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(данные, dict) or not isinstance(данные.get("now"), dict):
        return None
    return данные if _наш_город(данные) else None


def _наш_город(данные: dict) -> bool:
    """Про тот ли город этот кеш.

    Без координат в кеше (старый файл, написанный до этого изменения) ответ
    осторожный — «нет»: показывать прогноз неизвестного города хуже, чем не
    показать никакого.
    """
    if not _город_задан():
        return False
    try:
        lat = float(данные.get("lat"))
        lon = float(данные.get("lon"))
    except (TypeError, ValueError):
        return False
    try:
        наш_lat = float(config.WEATHER_LAT)
        наш_lon = float(config.WEATHER_LON)
    except (TypeError, ValueError):
        return False
    # Сотые доли градуса — это примерно километр, город туда не уедет.
    return abs(lat - наш_lat) < 0.01 and abs(lon - наш_lon) < 0.01


def _записать(сообщение: dict) -> None:
    """Кладёт сообщение в кеш вместе с координатами города.

    Координаты в кеше — не украшение: без них файл не знает, чей это прогноз,
    и после смены города старый висил бы на телефоне ещё полчаса.

    Сбой диска — не повод ругаться: телефон получит следующее обновление, а
    кеш просто останется старым. Пишем рядом и подменяем: оборванная запись
    на середине убила бы кеш целиком, и погода на экране пропала бы до
    следующего обновления.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        временный = CACHE_PATH.with_name(CACHE_PATH.name + ".new")
        временный.write_text(
            json.dumps({**сообщение,
                        "lat": config.WEATHER_LAT, "lon": config.WEATHER_LON,
                        "at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
        временный.replace(CACHE_PATH)
    except OSError:
        return


def _свежий(path=None, ttl: float = TTL) -> bool:
    """Протух ли кеш. Путь берём здесь, а не в значении по умолчанию: иначе
    он застыл бы на импорте, и подмена пути (её делают тесты) не влияла бы на
    проверку — кеш в тесте вечно считался бы протухшим.

    Кеш чужого города протухшим не считается: в сеть надо идти за новым."""
    цель = Path(path) if path is not None else Path(CACHE_PATH)
    try:
        if (time.time() - цель.stat().st_mtime) >= ttl:
            return False
    except OSError:
        return False
    return _прочитанный() is not None


def _возраст(отметка) -> int:
    """Сколько минут кешу, по отметке в нём.

    Часы компа иногда врут, поэтому возраст может уйти в ноль — и это ничего,
    врать дальше не о чем: телефон всё равно покажет дату обновления.
    """
    try:
        минут = int((time.time() - float(отметка)) // 60)
    except (TypeError, ValueError):
        return 0
    return max(минут, 0)


# --- Сеть -----------------------------------------------------------------


def _город_задан() -> bool:
    """Выбран ли город для погоды.

    Город и обе координаты. Пустого города в выпущенной Трубе нет: погода
    включается настройкой, иначе она спрашивала бы чужой город и ходила в сеть
    без спроса.
    """
    город = str(getattr(config, "WEATHER_CITY", "") or "").strip()
    try:
        lat = float(getattr(config, "WEATHER_LAT", None))
        lon = float(getattr(config, "WEATHER_LON", None))
    except (TypeError, ValueError):
        return False
    return bool(город and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0)


def url() -> str:
    """Адрес прогноза для города из настроек.

    Города нет — адреса тоже: `refresh` уходит мимо, в сеть не ходит.
    """
    if not _город_задан():
        return ""
    return (
        f"{API}?latitude={config.WEATHER_LAT}&longitude={config.WEATHER_LON}"
        "&current=temperature_2m,apparent_temperature,weather_code,"
        "wind_speed_10m,precipitation"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
        "precipitation_probability_max"
        f"&timezone=auto&forecast_days={DAYS}"
        # Без этого Open-Meteo отдаёт км/ч, а на экране подписано «м/с».
        "&wind_speed_unit=ms"
    )


def refresh() -> dict | None:
    """Обновляет кеш погоды, если он протух. Возвращает сообщение для телефона.

    Свежий кеш не трогаем и в сеть не идём — за это отвечает `TTL`. Сети нет —
    тоже не беда: возвращается прошлый кеш со своим `age_min`, и страница
    честно скажет, насколько он старый. Города не выбрано — не ходим в сеть
    вовсе: у чужого человека это лишний поход в интернет и город не его.
    """
    if not _город_задан():
        return cached()
    if not _net_lock.acquire(blocking=False):
        # Поток один: два одновременных похода в сеть ничего не дают.
        return cached()
    try:
        if _свежий():
            return cached()
        try:
            данные = _скачать(url())
        except Exception:
            return cached()
        сообщение = message(данные)
        if сообщение is None:
            return cached()
        _записать(сообщение)
        return сообщение
    finally:
        _net_lock.release()


def refresh_soon() -> None:
    """Запускает обновление в отдельном потоке — не из потока окна."""
    threading.Thread(target=refresh, daemon=True).start()


def _скачать(адрес: str):
    import httpx

    with httpx.Client(timeout=NET_TIMEOUT, follow_redirects=False) as client:
        ответ = client.get(адрес, headers={"Accept": "application/json"})
        ответ.raise_for_status()
        return ответ.json()


# --- Поиск города ----------------------------------------------------------
#
# Геокодер того же Open-Meteo: ключа не нужно, отвечает по-русски и отдаёт
# координаты, которые `url()` и подставляет. Им ищет город пульт — вручную
# вбивать координаты хозяину незачем.

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
# Сколько вариантов показываем: больше — шум, десятка с одного конца не бывает.
GEOCODE_COUNT = 5


def find_places(query: str) -> list[dict]:
    """Города по названию: [{name, region, country, lat, lon}]. Ошибки — пусто.

    Сеть недоступна или ответ неожиданный — возвращаем пустой список, а в
    пульте это читается как «ничего не нашлось». Исключение наружу не
    пробрасываем: поиск города не должен ронять всю страницу.
    """
    text = str(query or "").strip()
    if not text:
        return []
    адрес = (f"{GEOCODER}?name={quote(text)}&count={GEOCODE_COUNT}"
             "&language=ru&format=json")
    try:
        данные = _скачать(адрес)
    except Exception:
        return []
    return places(данные)


def places(данные) -> list[dict]:
    """Ответ геокодера → список мест для выбора в пульте."""
    if not isinstance(данные, dict):
        return []
    готовые = []
    for item in _ряд(данные.get("results")):
        if not isinstance(item, dict):
            continue
        название = str(item.get("name") or "").strip()
        lat, lon = item.get("latitude"), item.get("longitude")
        if not название or lat is None or lon is None:
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        готовые.append({
            "name": название,
            # Область и страница необязательны: у мелких городов их может не
            # быть, и пустая строка честнее, чем выдуманное значение.
            "region": str(item.get("admin1") or item.get("admin2") or ""),
            "country": str(item.get("country") or ""),
            "lat": lat,
            "lon": lon,
        })
    return готовые


# --- Фон ------------------------------------------------------------------


class Watcher:
    """Обновляет погоду и шлёт её телефону. Отдельный поток, не чаще TTL.

    Обновление идёт и без телефона: телефон может прийти через минуту, и пусть
    лучше сразу увидит погоду, чем пустоту.
    """

    def __init__(self, server, period: float = TTL):
        self.server = server
        self.period = period
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Первая попытка сразу: телефон подключается в первую же минуту.
        while True:
            self._показать()
            if self._stop.wait(self.period):
                return

    def _показать(self) -> None:
        """Обновляет и отправляет.

        Не обновилось — шлём прошлое: телефон лучше покажет погоду час назад,
        чем ничего.
        """
        сообщение = refresh()
        if сообщение is None:
            return
        try:
            self.server.send_weather(сообщение)
        except Exception:
            pass


# --- Мелочи ---------------------------------------------------------------


def _целое(value, замена=None) -> int | None:
    """Температура из ответа — число. `true` числом не считаем, NaN — тоже."""
    if isinstance(value, bool) or value is None:
        return замена
    try:
        число = float(value)
    except (TypeError, ValueError):
        return замена
    if число != число:
        return замена
    return int(round(число))


def _ветер(value) -> float:
    """Скорость ветра в м/с с одним знаком после запятой."""
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _ряд(значение) -> list:
    return значение if isinstance(значение, list) else []


def _take(ряд: list, место: int):
    """Элемент по индексу, если он есть, — иначе None."""
    return ряд[место] if 0 <= место < len(ряд) else None
