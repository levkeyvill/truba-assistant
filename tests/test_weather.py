"""Погода на телефоне: Open-Meteo, коды WMO, кеш и «не чаще раза в полчаса».

Сети здесь нет ни разу: загрузка подменена (`weather._скачать`), кеш лежит во
временной папке. Ответ Open-Meteo — ровно такой, какой приходит 26.09.2026 на
запрос из `weather.url()`.

Заодно проверяем, что скрипт страницы собирается: вытаскиваем его из
`web/index.html` во временный файл и зовём `node --check`. Без этого опечатка
в JS видна была бы только на телефоне. Если node не установлен — проверка
пропускается, а не роняет прогон.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import settings, weather
from ui.web_runtime import WebRuntime

# Ответ как его отдаёт Open-Meteo: `current` и `daily` рядом, по два дня.
ОТВЕТ = {
    "latitude": 56.08879,
    "longitude": 54.26383,
    "timezone": "Asia/Yekaterinburg",
    "current": {
        "time": "2026-09-26T18:30",
        "temperature_2m": 18.6,
        "apparent_temperature": 17.9,
        "weather_code": 2,
        "wind_speed_10m": 3.42,
        "precipitation": 0.0,
    },
    "daily": {
        "time": ["2026-09-26", "2026-09-27"],
        "temperature_2m_max": [19.4, 17.1],
        "temperature_2m_min": [8.2, 6.9],
        "weather_code": [61, 3],
        "precipitation_probability_max": [65, 10],
    },
}


class WeatherBase(unittest.TestCase):
    """Каждый тест пишет кеш в свою временную папку.

    Путь модуля подменяет ещё `tests/test_data_guard.py`, но тот попадает в
    прогон только через `unittest discover`. Этот класс закрывает и одиночный
    запуск файла: иначе тест дописал бы в живой `data/weather.json`.

    Город тоже подменяется: в выпущенной Трубе его нет, и без него погода
    выключена (см. `НетГородаTests`).
    """

    ГОРОД = "Уфа"
    ШИРОТА = 54.7388
    ДОЛГОТА = 55.9721

    def setUp(self):
        self._папка = Path(tempfile.mkdtemp(prefix="truba-weather-"))
        self.addCleanup(shutil.rmtree, self._папка, True)
        self._было = weather.CACHE_PATH
        self.addCleanup(self._вернуть)
        weather.CACHE_PATH = self._папка / "weather.json"
        self._был_город = (config.WEATHER_CITY, config.WEATHER_LAT,
                           config.WEATHER_LON)
        config.WEATHER_CITY = self.ГОРОД
        config.WEATHER_LAT = self.ШИРОТА
        config.WEATHER_LON = self.ДОЛГОТА
        self.addCleanup(self._вернуть_город)

    def _вернуть(self):
        weather.CACHE_PATH = self._было

    def _вернуть_город(self):
        (config.WEATHER_CITY, config.WEATHER_LAT,
         config.WEATHER_LON) = self._был_город

    def _подменить_сеть(self, ответ=ОТВЕТ, шалеть=False):
        """Вместо похода в сеть — подставной ответ (или отказ)."""
        ходили = []

        def скачать(адрес):
            ходили.append(адрес)
            if шалеть:
                raise OSError("сети нет")
            return json.loads(json.dumps(ответ))

        return ходили, mock.patch.object(weather, "_скачать", скачать)

    def _записать_кеш(self, возраст_минут=0, lat=None, lon=None):
        """Кладёт в кеш сообщение, как это делает сам модуль.

        Стареем и отметку в файле, и его время на диске: `refresh` смотрит
        именно на второе, а `cached` — на первое. Забыть одно из двух, и
        тест врал бы в любую сторону. Координаты — тоже как у модуля: без
        них кеш считается чужим и не отдаётся.
        """
        payload = {
            "type": "weather",
            "city": config.WEATHER_CITY,
            "now": {"temp": 12, "feels": 10, "text": "облачно",
                    "icon": "cloud", "wind": 2.0},
            "days": [],
            "lat": self.ШИРОТА if lat is None else lat,
            "lon": self.ДОЛГОТА if lon is None else lon,
            "at": time.time() - возраст_минут * 60,
        }
        weather.CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        минуту = time.time() - возраст_минут * 60
        os.utime(weather.CACHE_PATH, (минуту, минуту))


class ParseTests(WeatherBase):
    """Ответ Open-Meteo → сообщение телефону."""

    def setUp(self):
        super().setUp()
        self.сообщение = weather.message(ОТВЕТ)

    def test_city_and_type_are_there(self):
        self.assertEqual(self.сообщение["type"], "weather")
        self.assertEqual(self.сообщение["city"], self.ГОРОД)

    def test_now_carries_temperature_and_feels_like(self):
        now = self.сообщение["now"]
        self.assertEqual(now["temp"], 19)
        self.assertEqual(now["feels"], 18)

    def test_now_carries_the_word_and_the_icon(self):
        now = self.сообщение["now"]
        self.assertEqual(now["text"], "малооблачно")
        self.assertEqual(now["icon"], "sun-cloud")

    def test_wind_is_in_metres_per_second(self):
        self.assertEqual(self.сообщение["now"]["wind"], 3.4)

    def test_two_days_are_parsed(self):
        дни = self.сообщение["days"]
        self.assertEqual(len(дни), 2)
        self.assertEqual(дни[0], {"date": "2026-09-26", "max": 19, "min": 8,
                                  "text": "дождь", "icon": "rain", "rain": 65})
        self.assertEqual(дни[1]["date"], "2026-09-27")
        self.assertEqual(дни[1]["text"], "облачно")
        self.assertEqual(дни[1]["rain"], 10)

    def test_age_is_not_set_while_there_is_no_cache(self):
        # Возраст проставляет `cached()`, из ответа сети его брать нечем.
        self.assertNotIn("age_min", self.сообщение)

    def test_broken_answer_is_not_a_message(self):
        for плохое in (None, {}, [], {"current": None}, {"current": {}},
                       {"current": {"temperature_2m": None}},
                       {"current": {"temperature_2m": "холодно"}},
                       {"current": {"temperature_2m": True}}):
            self.assertIsNone(weather.message(плохое), плохое)

    def test_days_survive_a_short_daily(self):
        # Open-Meteo может отдать дату без температур: такой день пропускаем,
        # а не показываем «+0°».
        ответ = {"current": {"temperature_2m": 5.0},
                 "daily": {"time": ["2026-09-26", "2026-09-27"],
                           "temperature_2m_max": [10.0, None],
                           "temperature_2m_min": [3.0, None],
                           "weather_code": [0, 0],
                           "precipitation_probability_max": [0, 0]}}
        self.assertEqual(len(weather.message(ответ)["days"]), 1)

    def test_no_rain_probability_stays_none(self):
        # Ноль и «не сказано» — разные вещи, их нельзя сливать в ноль.
        ответ = {"current": {"temperature_2m": 5.0},
                 "daily": {"time": ["2026-09-26"],
                           "temperature_2m_max": [10.0],
                           "temperature_2m_min": [3.0],
                           "weather_code": [0]}}
        self.assertIsNone(weather.message(ответ)["days"][0]["rain"])


class CodeTests(WeatherBase):
    """Коды WMO → русское слово и имя значка."""

    def test_every_word_of_the_table_is_in_place(self):
        for код, (слово, значок) in weather.CODES.items():
            self.assertTrue(слово, код)
            self.assertTrue(значок, код)

    def test_the_whole_table_is_covered(self):
        # Пропущенный код молча превратился бы в пустое слово, а телефон
        # показал бы ни о чём. Сверяем с таблицей WMO из документации.
        for код in (0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66,
                    67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 97, 99):
            self.assertIn(код, weather.CODES, код)

    def test_clear_sky(self):
        self.assertEqual(weather.describe(0), ("ясно", "sun"))
        self.assertEqual(weather.describe(1), ("ясно", "sun"))

    def test_partly_cloudy(self):
        self.assertEqual(weather.describe(2), ("малооблачно", "sun-cloud"))

    def test_overcast_and_fog(self):
        self.assertEqual(weather.describe(3), ("облачно", "cloud"))
        self.assertEqual(weather.describe(45), ("туман", "fog"))
        self.assertEqual(weather.describe(48), ("туман", "fog"))

    def test_drizzle_rain_and_shower(self):
        for код in (51, 53, 55, 56, 57):
            self.assertEqual(weather.describe(код), ("морось", "rain"))
        for код in (61, 63, 80, 81):
            self.assertEqual(weather.describe(код), ("дождь", "rain"))
        for код in (65, 82):
            self.assertEqual(weather.describe(код), ("ливень", "rain"))

    def test_snow_and_snowfall(self):
        for код in (71, 73, 77):
            self.assertEqual(weather.describe(код), ("снег", "snow"))
        for код in (75, 85, 86):
            self.assertEqual(weather.describe(код), ("снегопад", "snow"))

    def test_thunderstorm(self):
        for код in (95, 96, 97, 99):
            self.assertEqual(weather.describe(код), ("гроза", "storm"))

    def test_unknown_code_says_nothing(self):
        # Не выдумываем: пустое слово страница просто не рисует.
        for код in (4, 6, 21, 100, None, "ясно", ""):
            self.assertEqual(weather.describe(код), ("", ""))

    def test_string_code_from_json_still_works(self):
        # JSON иногда отдаёт числа строками — код не должен молчать.
        self.assertEqual(weather.describe("95"), ("гроза", "storm"))


class CacheTests(WeatherBase):
    """Кеш на диске и его возраст."""

    def test_fresh_cache_does_not_go_to_the_network(self):
        ходили, сеть = self._подменить_сеть()
        self._записать_кеш(возраст_минут=5)
        with сеть:
            сообщение = weather.refresh()
        self.assertEqual(ходили, [])
        self.assertEqual(сообщение["now"]["temp"], 12)

    def test_cache_is_read_with_its_age(self):
        self._записать_кеш(возраст_минут=185)
        сообщение = weather.cached()
        self.assertEqual(сообщение["type"], "weather")
        self.assertEqual(сообщение["age_min"], 185)
        # Служебная отметка наружу не отдаётся.
        self.assertNotIn("at", сообщение)

    def test_missing_cache_is_not_a_message(self):
        self.assertIsNone(weather.cached())

    def test_broken_cache_is_not_a_message(self):
        weather.CACHE_PATH.write_text("не json", encoding="utf-8")
        self.assertIsNone(weather.cached())
        weather.CACHE_PATH.write_text('{"тип": "погода"}', encoding="utf-8")
        self.assertIsNone(weather.cached())

    def test_clock_in_the_past_does_not_make_a_negative_age(self):
        # Часы компа иногда врут: в минус уходить нельзя, телефон сложил бы
        # это с минусом и написал «обновлено -3 ч назад».
        self._записать_кеш(возраст_минут=-90)
        self.assertEqual(weather.cached()["age_min"], 0)


class НетГородаTests(WeatherBase):
    """Город не выбран — погоды нет, и в сеть никто не ходит.

    У человека, который только поставил Трубу, города нет. Спрашивать у
    Open-Meteo «погоду в null» — это поход в интернет без спроса и заведомо
    бесполезный ответ.
    """

    def setUp(self):
        super().setUp()
        config.WEATHER_CITY, config.WEATHER_LAT, config.WEATHER_LON = "", None, None

    def test_no_trip_and_nothing_to_show(self):
        ходили, сеть = self._подменить_сеть()
        with сеть:
            self.assertIsNone(weather.refresh())
        self.assertEqual(ходили, [])
        self.assertIsNone(weather.cached())

    def test_there_is_no_url_to_ask_for(self):
        self.assertEqual(weather.url(), "")

    def test_old_cache_of_a_removed_city_is_not_shown(self):
        # Город сняли — старый прогноз на телефоне висеть не должен: хозяин
        # сказал «без погоды», а он всё ещё видит ту же Уфу.
        self._записать_кеш(возраст_минут=200)
        ходили, сеть = self._подменить_сеть()
        with сеть:
            self.assertIsNone(weather.refresh())
        self.assertEqual(ходили, [])
        self.assertIsNone(weather.cached())


class ЧужойГородTests(WeatherBase):
    """Кеш про другой город — это не наш кеш.

    Файл `data/weather.json` не знает, для какого города он: без координат
    внутри смена города оставляла на телефоне старый прогноз ещё полчаса, а
    снятие города — навсегда. Поэтому координаты кладутся в кеш, и несовпадение
    с настройками означает «показать нечего».
    """

    def test_a_cache_of_another_city_is_not_given_out(self):
        self._записать_кеш(возраст_минут=5, lat=43.1155, lon=131.8855)
        self.assertIsNone(weather.cached())

    def test_a_foreign_cache_is_not_fresh(self):
        # Иначе `refresh` решил бы, что всё в порядке, и в сеть бы не пошёл.
        self._записать_кеш(возраст_минут=5, lat=43.1155, lon=131.8855)
        self.assertFalse(weather._свежий())

    def test_our_own_cache_is_fresh_and_given_out(self):
        self._записать_кеш(возраст_минут=5)
        self.assertTrue(weather._свежий())
        self.assertEqual(weather.cached()["now"]["temp"], 12)

    def test_a_cache_without_coordinates_is_not_trusted(self):
        # Старый файл, написанный до появления координат: чей это город —
        # неизвестно, а показывать прогноз неизвестного города хуже, чем
        # не показывать никакого.
        payload = {"type": "weather", "city": "Уфа",
                   "now": {"temp": 12, "icon": "cloud", "text": "облачно"},
                   "days": [], "at": time.time()}
        weather.CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(weather.cached())
        self.assertFalse(weather._свежий())

    def test_a_changed_city_goes_to_the_network_for_a_new_forecast(self):
        self._записать_кеш(возраст_минут=5, lat=43.1155, lon=131.8855)
        ходили, сеть = self._подменить_сеть()
        with сеть:
            сообщение = weather.refresh()
        self.assertEqual(len(ходили), 1)
        self.assertIn(f"latitude={config.WEATHER_LAT}", ходили[0])
        self.assertEqual(сообщение["now"]["temp"], 19)
        # Новый прогноз уже наш: координаты записаны вместе с ним.
        self.assertEqual(weather.cached()["now"]["temp"], 19)

    def test_the_phone_never_sees_the_coordinates(self):
        # Координаты — дело нашего кеша, телефону они не нужны.
        self._записать_кеш(возраст_минут=5)
        сообщение = weather.cached()
        self.assertNotIn("lat", сообщение)
        self.assertNotIn("lon", сообщение)


class NoNetworkTests(WeatherBase):

    def test_old_cache_survives_a_failed_download(self):
        self._записать_кеш(возраст_минут=200)
        ходили, сеть = self._подменить_сеть(шалеть=True)
        with сеть:
            сообщение = weather.refresh()
        self.assertEqual(len(ходили), 1)
        self.assertEqual(сообщение["now"]["temp"], 12)
        self.assertEqual(сообщение["age_min"], 200)

    def test_failed_download_keeps_the_cache_on_disk(self):
        self._записать_кеш(возраст_минут=200)
        _, сеть = self._подменить_сеть(шалеть=True)
        with сеть:
            weather.refresh()
        self.assertEqual(weather.cached()["now"]["temp"], 12)

    def test_without_a_cache_there_is_nothing_to_send(self):
        _, сеть = self._подменить_сеть(шалеть=True)
        with сеть:
            self.assertIsNone(weather.refresh())


class PeriodTests(WeatherBase):
    """Не чаще раза в полчаса."""

    def setUp(self):
        super().setUp()
        self.ходили, self.сеть = self._подменить_сеть()
        self.сеть.start()
        self.addCleanup(self.сеть.stop)

    def test_twice_in_a_row_means_one_trip(self):
        weather.refresh()
        weather.refresh()
        weather.refresh()
        self.assertEqual(len(self.ходили), 1)

    def test_an_hour_later_the_weather_is_asked_again(self):
        weather.refresh()
        self.assertEqual(len(self.ходили), 1)
        # Стареем кеш на час вперёд: заодно видно, что после обновления данные
        # в кеш действительно легли, а не потерялись по дороге.
        stale = time.time() - weather.TTL - 60
        os.utime(weather.CACHE_PATH, (stale, stale))
        weather.refresh()
        self.assertEqual(len(self.ходили), 2)

    def test_the_trip_asks_for_the_right_city(self):
        weather.refresh()
        адрес = self.ходили[0]
        self.assertIn("api.open-meteo.com", адрес)
        self.assertIn(f"latitude={config.WEATHER_LAT}", адрес)
        self.assertIn(f"longitude={config.WEATHER_LON}", адрес)
        self.assertIn("forecast_days=2", адрес)
        self.assertEqual(weather.NET_TIMEOUT, 8.0)
        self.assertEqual(weather.TTL, 30 * 60)


class WatcherTests(WeatherBase):
    """Фоновый поток шлёт погоду телефону."""

    class _Сервер:
        def __init__(self):
            self.сообщения = []

        def send_weather(self, сообщение):
            self.сообщения.append(сообщение)

    def test_the_phone_gets_the_weather(self):
        сервер = self._Сервер()
        _, сеть = self._подменить_сеть()
        with сеть:
            наблюдатель = weather.Watcher(сервер)
            self.addCleanup(наблюдатель.stop)
            наблюдатель._показать()
        self.assertEqual(len(сервер.сообщения), 1)
        self.assertEqual(сервер.сообщения[0]["type"], "weather")
        self.assertEqual(сервер.сообщения[0]["now"]["text"], "малооблачно")

    def test_a_broken_server_does_not_stop_the_watcher(self):
        class Сломанный:
            def send_weather(self, сообщение):
                raise RuntimeError("телефон отвалился")

        _, сеть = self._подменить_сеть()
        with сеть:
            наблюдатель = weather.Watcher(Сломанный())
            self.addCleanup(наблюдатель.stop)
            наблюдатель._показать()   # не должно бросить

    def test_nothing_is_sent_without_any_data(self):
        сервер = self._Сервер()
        _, сеть = self._подменить_сеть(шалеть=True)
        with сеть:
            наблюдатель = weather.Watcher(сервер)
            self.addCleanup(наблюдатель.stop)
            наблюдатель._показать()
        self.assertEqual(сервер.сообщения, [])


class КоординатыTests(unittest.TestCase):
    """Широта и долгота проверяются по-своему.

    Одна проверка на оба числа превращала долготу Владивостока (131.9) в
    `None`, и погода молча выключалась, хотя город в пульте был выван.
    """

    def test_vladivostok_longitude_is_a_valid_longitude(self):
        self.assertEqual(settings.validate_lon(131.9), 131.9)
        self.assertEqual(settings.validate_lon(-179.5), -179.5)
        self.assertEqual(settings.validate_lon("131.9"), 131.9)

    def test_vladivostok_latitude_is_not_a_valid_latitude(self):
        self.assertIsNone(settings.validate_lat(131.9))
        self.assertIsNone(settings.validate_lat(-131.9))

    def test_the_edge_is_still_the_edge(self):
        self.assertEqual(settings.validate_lat(90.0), 90.0)
        self.assertEqual(settings.validate_lat(-90.0), -90.0)
        self.assertIsNone(settings.validate_lat(90.1))
        self.assertEqual(settings.validate_lon(180.0), 180.0)
        self.assertIsNone(settings.validate_lon(180.1))

    def test_rubbish_is_none_for_both(self):
        for мусор in (None, "", "  ", True, False, "вот тут", [], float("nan")):
            with self.subTest(мусор=мусор):
                self.assertIsNone(settings.validate_lat(мусор))
                self.assertIsNone(settings.validate_lon(мусор))

    def test_coordinate_is_still_the_latitude_check(self):
        # Старое имя оставлено обёрткой: кто-то мог на него ссылаться.
        self.assertEqual(settings.validate_coordinate(55.9), 55.9)
        self.assertIsNone(settings.validate_coordinate(131.9))

    def test_a_far_east_city_counts_as_configured(self):
        было = (config.WEATHER_CITY, config.WEATHER_LAT, config.WEATHER_LON)
        self.addCleanup(self._вернуть, было)
        config.WEATHER_CITY, config.WEATHER_LAT, config.WEATHER_LON = (
            "Владивосток", 43.1155, 131.8855)
        self.assertTrue(settings.weather_ready())

    def _вернуть(self, было):
        (config.WEATHER_CITY, config.WEATHER_LAT,
         config.WEATHER_LON) = было


# --- Смена города: телефон узнаёт сразу --------------------------------------


class СменаГородаTests(unittest.TestCase):
    """Город сменили — телефон узнаёт об этом сразу, а не через полчаса.

    Без этого кеш (а он живёт полчаса) продолжал показывать старый город, а
    снятый город вообще оставался на экране. Настройки хозяина — во временной
    папке, сети нет: `weather.refresh` подменён.
    """

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-city-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=self.папка / "settings.json",
            ENV_PATH=self.папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        for имя in ("NOTES_DIR", "WEATHER_CITY", "WEATHER_LAT", "WEATHER_LON"):
            self.addCleanup(setattr, config, имя, getattr(config, имя))
        self.разослано = []
        self.позван = []
        среда = object.__new__(WebRuntime)
        среда._lock = threading.Lock()
        среда._remember = lambda kind, payload: None
        # Фон зовём сразу: тест проверяет, что позвали, а не планировщик.
        среда._bg = lambda func, *args: func(*args)
        среда.server = NS(send_weather=self.разослано.append)
        self.среда = среда
        self._подменить_погоду({"now": {"temp": 5}})

    def _подменить_погоду(self, ответ):
        подмена = mock.patch.object(
            weather, "refresh", lambda: (self.позван.append(1), ответ)[1])
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_a_new_city_is_sent_to_the_phone_at_once(self):
        ответ = self.среда.save_settings({"weather_city": "Владивосток",
                                         "weather_lat": 43.1155,
                                         "weather_lon": 131.8855})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(len(self.позван), 1, "погоду должны были обновить")
        self.assertEqual(self.разослано, [{"now": {"temp": 5}}])

    def test_a_removed_city_tells_the_phone_the_weather_is_off(self):
        settings.save_settings({"weather_city": "Уфа", "weather_lat": 54.7,
                                "weather_lon": 55.9})
        settings.apply_to_config()
        self._подменить_погоду(None)
        ответ = self.среда.save_settings({"weather_city": "", "weather_lat": None,
                                         "weather_lon": None})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self.разослано, [{"off": True}])

    def test_another_setting_does_not_touch_the_weather(self):
        self.среда.save_settings({"proactive": "rare"})
        self.assertEqual(self.позван, [])
        self.assertEqual(self.разослано, [])

    def test_the_same_city_does_not_go_to_the_network_again(self):
        settings.save_settings({"weather_city": "Уфа", "weather_lat": 54.7,
                                "weather_lon": 55.9})
        settings.apply_to_config()
        self.среда.save_settings({"weather_city": "Уфа", "weather_lat": 54.7,
                                 "weather_lon": 55.9})
        self.assertEqual(self.позван, [])

    def test_saving_a_far_east_city_keeps_its_longitude(self):
        # Проверка на ±90 была общей для обеих координат и тихо ломала это.
        ответ = self.среда.save_settings({"weather_city": "Владивосток",
                                         "weather_lat": 43.1155,
                                         "weather_lon": 131.8855})
        self.assertTrue(ответ["ok"], ответ)
        сохранено = settings.load_settings()
        self.assertEqual(сохранено["weather_lon"], 131.8855)
        self.assertEqual(сохранено["weather_lat"], 43.1155)
        self.assertEqual(config.WEATHER_LON, 131.8855)


class PageScriptTests(unittest.TestCase):
    """Скрипт страницы должен собираться."""

    def test_page_script_passes_node_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node не установлен")
        страница = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        скрипты = re.findall(r"<script>(.*?)</script>", страница, re.S)
        self.assertEqual(len(скрипты), 1, "ожидался один блок <script>")
        папка = Path(tempfile.mkdtemp(prefix="truba-page-"))
        self.addCleanup(shutil.rmtree, папка, True)
        файл = папка / "page.js"
        файл.write_text(скрипты[0], encoding="utf-8")
        result = subprocess.run([node, "--check", str(файл)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_page_knows_every_icon_the_server_can_name(self):
        # Сервер шлёт имя значка погоды, а набор ICONS на странице — наш общий.
        # Неизвестное имя тихо нарисовало бы облако там, где гроза.
        страница = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for _, значок in weather.CODES.values():
            # Имена с дефисом в объекте берутся в кавычках.
            self.assertTrue(f"  {значок}: '" in страница
                            or f"'{значок}':" in страница, значок)


