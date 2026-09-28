"""Серверная часть мастера первого запуска: маршруты и то, что он видит.

Железа, микрофонов, сети и segno здесь нет ничего — всё подменено. Проверяем
то, что хозяину показывается: `/api/audio` отдаёт текущий выбор, `/api/hardware`
отдаёт железо и совет, QR принимает только свой адрес, `first_run_done`
различает свежую установку и ту, где настройки уже были.
"""

import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import hardware, settings, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ПОРТ: list = []


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _пульт() -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер и голос."""
    среда = object.__new__(WebRuntime)
    среда._lock = threading.Lock()
    среда._update_lock = threading.Lock()
    среда._bg = lambda func, *args: None
    среда._remember = lambda kind, payload: None
    среда._hw_cache = None
    среда.voice = NS(running=False, _listener=None, _speaker_idle=lambda: True,
                    in_conversation=False)
    return среда


def _звук(микрофон, колонки):
    """Поддельный sounddevice с одним входом и одним выходом."""
    return NS(
        query_devices=lambda *a, **k: (микрофон + колонки if not a else a[0]),
        query_hostapis=lambda idx: {"name": "WASAPI"},
        default=NS(device=[0, 1]),
    )


class ApiBase(unittest.TestCase):
    """Поднимаем сервер один раз на весь класс — как в `test_stt_settings`."""

    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)
        ПОРТ.append(cls.server.port)

    def setUp(self):
        self.server.runtime = _пульт()
        self.client = TestClient(self.server._app,
                                 base_url=f"http://127.0.0.1:{ПОРТ[0]}")
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app,
                                base_url=f"http://127.0.0.1:{ПОРТ[0]}",
                                client=("192.168.1.50", 5555))


class AudioApiTests(ApiBase):
    """`/api/audio`: списки устройств и текущий выбор."""

    def test_the_page_gets_lists_and_the_current_choice(self):
        микрофон = [{"name": "Микрофон (USB)", "hostapi": 1,
                     "max_input_channels": 1, "max_output_channels": 0,
                     "default_samplerate": 48000.0}]
        колонки = [{"name": "Колонки (USB)", "hostapi": 1,
                    "max_input_channels": 0, "max_output_channels": 2,
                    "default_samplerate": 48000.0}]
        with mock.patch.dict("sys.modules",
                             {"sounddevice": _звук(микрофон, колонки)}):
            тело = self.client.get("/api/audio").json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual([у["name"] for у in тело["inputs"]], ["Микрофон (USB)"])
        self.assertEqual([у["name"] for у in тело["outputs"]], ["Колонки (USB)"])
        for ключ in ("mic_name", "mic_channel", "output", "speaker_name"):
            self.assertIn(ключ, тело["current"])

    def test_nothing_is_selected_means_the_system_devices(self):
        # У человека, который ничего не выбирал, в настройках пусто, и это
        # нормально: голос берёт системный микрофон.
        self.assertEqual(config.MIC_NAME, "")
        self.assertEqual(config.MIC_CHANNEL, 0)
        self.assertEqual(config.SPEAKER_NAME, "")

    def test_level_answers_words_when_the_voice_is_off(self):
        # Голос выключен — микрофон не открыт, и «0» означал бы «микрофон молчит».
        тело = self.client.get("/api/audio/level").json()
        self.assertFalse(тело["ok"])
        self.assertIn("голос", тело["error"])

    def test_level_follows_the_microphone_while_the_voice_runs(self):
        self.server.runtime.voice = NS(
            running=True, _listener=NS(last_level=0.42),
            _speaker_idle=lambda: True, in_conversation=False)
        тело = self.client.get("/api/audio/level").json()
        self.assertTrue(тело["ok"])
        self.assertAlmostEqual(тело["level"], 0.42, places=2)

    def test_level_is_kept_inside_zero_and_one(self):
        # Полоска в пульте не должна выехать за края из-за одного щелчка.
        self.server.runtime.voice = NS(
            running=True, _listener=NS(last_level=7.5),
            _speaker_idle=lambda: True, in_conversation=False)
        self.assertEqual(self.client.get("/api/audio/level").json()["level"], 1.0)

    def test_nothing_is_available_from_the_phone(self):
        # Запись голоса хозяина не должна включаться со страницы с чужим Host.
        self.assertEqual(self.вдали.post("/api/audio/test").status_code, 403)
        self.assertEqual(self.вдали.get("/api/audio").status_code, 403)

    def test_check_asks_to_turn_the_voice_off(self):
        # Работающий голос держит микрофон: второй раз его не открыть.
        self.server.runtime.voice = NS(
            running=True, _listener=NS(last_level=0.0),
            _speaker_idle=lambda: True, in_conversation=False)
        ответ = self.client.post("/api/audio/test")
        self.assertEqual(ответ.status_code, 400)
        self.assertIn("микрофон", ответ.json()["error"])


class HardwareApiTests(ApiBase):
    """`/api/hardware`: что есть и что по силам."""

    def test_the_page_gets_hardware_and_advice(self):
        hw = {"gpus": [{"name": "NVIDIA GeForce RTX 5070 Ti", "vram_gb": 15.9,
                        "driver": "616.56", "compute_cap": 12.0}],
              "cpu": {"name": "", "cores": 8, "threads": 16},
              "ram_gb": 32.0, "disk_free_gb": 300.0}
        with mock.patch.object(hardware, "detect", return_value=hw):
            self.server.runtime._hw_cache = None
            тело = self.client.get("/api/hardware").json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["hw"]["ram_gb"], 32.0)
        self.assertEqual(тело["recommend"]["voice"], "higgs")
        self.assertTrue(тело["recommend"]["voice_why"])

    def test_the_answer_is_cached_so_nvidia_smi_is_not_spammed(self):
        # Вкладка железа спрашивает его часто, а nvidia-smi занимает секунды.
        with mock.patch.object(hardware, "detect",
                               return_value={"gpus": [], "cpu": {},
                                             "ram_gb": 8.0,
                                             "disk_free_gb": 100.0},
                               wraps=hardware.detect) as определили:
            self.server.runtime._hw_cache = None
            self.client.get("/api/hardware")
            self.client.get("/api/hardware")
        self.assertEqual(определили.call_count, 1)

    def test_not_available_from_the_phone(self):
        self.assertEqual(self.вдали.get("/api/hardware").status_code, 403)


class QrTests(ApiBase):
    """QR-код адреса телефона: только свой адрес, и без segno — не падать."""

    def _свой(self) -> str:
        from core.phone import local_addresses

        return f"http://{local_addresses()[0]}:{config.PHONE_PORT}"

    def test_another_address_is_refused(self):
        # Маршрут рисует адрес на экране телефона. Генератор QR для чужой
        # ссылки отсюда — это способ отправить человека куда угодно от
        # имени Трубы.
        ответ = self.client.get("/api/phone/qr",
                                params={"url": "https://example.com"})
        self.assertEqual(ответ.status_code, 400)

    def test_local_address_with_another_port_is_refused(self):
        ответ = self.client.get("/api/phone/qr",
                                params={"url": f"http://127.0.0.1:1/"})
        self.assertIn(ответ.status_code, (400, 403))

    def test_our_own_address_is_drawn(self):
        try:
            import segno  # noqa: F401
        except ImportError:
            # segno ставится установщиком, а тесты обязаны идти и без него.
            ответ = self.client.get("/api/phone/qr", params={"url": self._свой()})
            self.assertEqual(ответ.status_code, 501)
            self.assertIn("segno", ответ.json()["error"])
            return
        ответ = self.client.get("/api/phone/qr", params={"url": self._свой()})
        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(ответ.headers["content-type"], "image/svg+xml")
        self.assertIn(b"<svg", ответ.content)

    def test_svg_is_returned_when_the_library_is_there(self):
        # Библиотеки в копии разработчика может не быть, а у хозяина после
        # установки она будет. Поэтому рисуем подменой: с настоящей segno
        # формат тот же — SVG.
        отдан = {}
        вызван = {}

        def сохранить(буфер, **kwargs):
            отдан.update(kwargs)
            буфер.write(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")

        def нарисовать(текст, **kwargs):
            вызван.update(kwargs)
            вызван["text"] = текст
            return NS(save=сохранить)

        подделка = NS(make=нарисовать)
        with mock.patch.dict("sys.modules", {"segno": подделка}):
            ответ = self.client.get("/api/phone/qr", params={"url": self._свой()})
        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(ответ.headers["content-type"], "image/svg+xml")
        self.assertIn(b"<svg", ответ.content)
        self.assertEqual(отдан.get("kind"), "svg")
        self.assertEqual(вызван.get("text"), self._свой())
        # Прозрачный фон: QR ложится на тёмную подложку мастера, и белая
        # заливка прямоугольником испортила бы его.
        self.assertIsNone(вызван.get("light"))

    def test_empty_url_is_refused(self):
        self.assertEqual(self.client.get("/api/phone/qr").status_code, 400)


class FirstRunTests(unittest.TestCase):
    """`first_run_done`: у кого мастер, а у кого нет."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-firstrun-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.файл = self.папка / "settings.json"
        self._был = settings.SETTINGS_PATH
        settings.SETTINGS_PATH = self.файл
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        settings.SETTINGS_PATH = self._был

    def test_no_file_means_a_fresh_install(self):
        # Свежая установка: мастер нужен, город не выбран.
        значения = settings.load_settings()
        self.assertFalse(значения["first_run_done"])
        self.assertEqual(значения["weather_city"], "")
        self.assertIsNone(значения["weather_lat"])

    def test_file_without_the_key_means_the_person_is_not_new(self):
        # Файл писала версия без мастера: человек им уже пользовался, и
        # показывать ему мастер незачем.
        self.файл.write_text('{"voice_name": "мой"}', encoding="utf-8")
        self.assertTrue(settings.load_settings()["first_run_done"])

    def test_key_in_the_file_is_obeyed(self):
        self.файл.write_text('{"first_run_done": false}', encoding="utf-8")
        self.assertFalse(settings.load_settings()["first_run_done"])
        self.файл.write_text('{"first_run_done": true}', encoding="utf-8")
        self.assertTrue(settings.load_settings()["first_run_done"])

    def test_broken_file_is_still_a_seen_file(self):
        self.файл.write_text("не json", encoding="utf-8")
        # Настройки не прочитались, но папка явно не свежая — мастер не крутим.
        self.assertTrue(settings.load_settings()["first_run_done"])


class WeatherApiTests(ApiBase):
    """Погода: по умолчанию её нет, а город ищется геокодером."""

    def setUp(self):
        super().setUp()
        self.было = (config.WEATHER_CITY, config.WEATHER_LAT,
                     config.WEATHER_LON)

    def tearDown(self):
        (config.WEATHER_CITY, config.WEATHER_LAT,
         config.WEATHER_LON) = self.было

    def test_without_a_city_there_is_no_trip(self):
        # У чужого человека города нет, и ходить в сеть «просто так» нельзя.
        config.WEATHER_CITY, config.WEATHER_LAT, config.WEATHER_LON = "", None, None
        ходили = []
        with mock.patch.object(weather, "_скачать",
                               side_effect=lambda адрес: ходили.append(адрес)):
            self.assertIsNone(weather.refresh())
        self.assertEqual(ходили, [])

    def test_the_geocoder_answer_is_understood(self):
        ответ = {"results": [
            {"name": "Уфа", "admin1": "Башкортостан",
             "country": "Россия", "latitude": 54.73, "longitude": 55.97},
            {"name": "Уфа", "admin1": "Алтайский край",
             "country": "Россия", "latitude": 52.0, "longitude": 85.0},
        ]}
        with mock.patch.object(weather, "_скачать", return_value=ответ):
            места = weather.find_places("Уфа")
        self.assertEqual(len(места), 2)
        self.assertEqual(места[0]["name"], "Уфа")
        self.assertEqual(места[0]["region"], "Башкортостан")
        self.assertEqual(места[0]["country"], "Россия")
        self.assertAlmostEqual(места[0]["lat"], 54.73, places=2)
        self.assertAlmostEqual(места[0]["lon"], 55.97, places=2)

    def test_the_geocoder_is_asked_in_russian(self):
        ходили = []
        with mock.patch.object(weather, "_скачать",
                               side_effect=lambda адрес: ходили.append(адрес)
                               or {"results": []}):
            weather.find_places("Уфа")
        self.assertIn("geocoding-api.open-meteo.com", ходили[0])
        self.assertIn("language=ru", ходили[0])

    def test_no_network_is_an_empty_list_not_a_crash(self):
        # Поиск города не должен ронять страницу настроек.
        with mock.patch.object(weather, "_скачать", side_effect=OSError("сети нет")):
            self.assertEqual(weather.find_places("Уфа"), [])

    def test_empty_query_asks_nothing(self):
        ходили = []
        with mock.patch.object(weather, "_скачать",
                               side_effect=lambda адрес: ходили.append(адрес)):
            self.assertEqual(weather.find_places(""), [])
        self.assertEqual(ходили, [])

    def test_the_page_gets_the_places(self):
        ответ = {"results": [{"name": "Уфа", "admin1": "Башкортостан",
                              "country": "Россия", "latitude": 54.73,
                              "longitude": 55.97}]}
        with mock.patch.object(weather, "_скачать", return_value=ответ):
            тело = self.client.get("/api/weather/find",
                                   params={"q": "Уфа"}).json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["places"][0]["name"], "Уфа")

    def test_search_is_not_available_from_the_phone(self):
        self.assertEqual(self.вдали.get("/api/weather/find").status_code, 403)


class VoicesApiTests(ApiBase):
    """Качественные голоса: статус и отказ вместо установки."""

    def test_status_says_what_can_be_done(self):
        with mock.patch.object(hardware, "detect",
                               return_value={"gpus": [], "cpu": {},
                                             "ram_gb": 8.0,
                                             "disk_free_gb": 100.0}), \
                mock.patch("core.hardware.voices_installed", return_value=False):
            тело = self.client.get("/api/voices/status").json()
        self.assertTrue(тело["ok"], тело)
        self.assertFalse(тело["running"])
        self.assertEqual(тело["steps"], [])
        # Видеокарты нет — зачем предлагать кнопку, которая ничего не даст.
        self.assertFalse(тело["possible"])
        self.assertTrue(тело["why"])

    def test_install_is_not_available_from_the_phone(self):
        self.assertEqual(self.вдали.post("/api/voices/install").status_code, 403)

    def test_the_journal_says_it_in_words(self):
        # Хозяин читает журнал, а не коды: «поставились» и «не поставились»
        # должны быть видны там словами.
        строки = WebRuntime._log_messages
        self.assertEqual(строки("voices_step", "ставлю torch с CUDA"),
                         ["качественные голоса: ставлю torch с CUDA"])
        self.assertEqual(строки("voices_done", {"ok": True}),
                         ["качественные голоса поставлены — перезапусти пульт"])
        self.assertIn("не поставились",
                      строки("voices_failed", {"error": "сеть отвалилась"})[0])


class ДляВсехТест(unittest.TestCase):
    """Приложение выкладывается для всех: личного в коде быть не должно.

    Проверяем текстом файлов, а не поведением: уехавший в публичный репозиторий
    город или ник хозяина — это не баг, который заметят тесты, а то, что не
    должен существовать вовсе.
    """

    def _текст(self, путь: str) -> str:
        return (config.ROOT / путь).read_text(encoding="utf-8")

    def test_no_city_of_the_author_in_the_config(self):
        self.assertNotIn("Нефтекамск", self._текст("config.py"))

    def test_no_nickname_of_the_author_in_the_config(self):
        self.assertNotIn("MrKips", self._текст("config.py"))

    def test_no_name_of_his_sound_card_in_the_config(self):
        self.assertNotIn("Rubix", self._текст("config.py"))

    def test_no_home_address_on_the_phone_page(self):
        self.assertNotIn("192.168.0.103", self._текст("web/index.html"))

    def test_replay_guard_is_off_by_default(self):
        # Сторож мгновенного повтора NVIDIA — привычка машины автора. У другого
        # человека повтора нет, и нажимать горячие клавиши ему незачем.
        self.assertFalse(config.REPLAY_GUARD)
        self.assertFalse(settings.DEFAULTS["replay_guard"])

    def test_weather_starts_empty(self):
        # Без города погода просто выключена: телефон её не показывает, и в
        # сеть никто не ходит.
        self.assertEqual(config.WEATHER_CITY, "")
        self.assertIsNone(config.WEATHER_LAT)
        self.assertIsNone(config.WEATHER_LON)


class СписокПрограммТест(unittest.TestCase):
    """Свежая установка: список программ не должен быть пустым."""

    def test_without_apps_json_the_default_list_is_used(self):
        from core import launcher

        папка = Path(tempfile.mkdtemp(prefix="truba-apps-"))
        self.addCleanup(shutil.rmtree, папка, True)
        свой = папка / "apps.json"
        запасной = папка / "apps.default.json"
        запасной.write_text(
            '[{"id": "youtube", "title": "YouTube", "kind": "url",'
            ' "url": "https://youtube.com"}]', encoding="utf-8")
        with mock.patch.object(launcher, "APPS_FILE", свой), \
                mock.patch.object(launcher, "DEFAULT_FILE", запасной):
            список = launcher.read_list()
        self.assertEqual([п["id"] for п in список], ["youtube"])

    def test_own_list_wins_over_the_default(self):
        from core import launcher

        папка = Path(tempfile.mkdtemp(prefix="truba-apps-"))
        self.addCleanup(shutil.rmtree, папка, True)
        свой = папка / "apps.json"
        запасной = папка / "apps.default.json"
        свой.write_text('[{"id": "discord", "title": "Discord",'
                        ' "kind": "app", "path": "Discord.exe"}]',
                        encoding="utf-8")
        запасной.write_text('[{"id": "youtube", "title": "YouTube"}]',
                            encoding="utf-8")
        with mock.patch.object(launcher, "APPS_FILE", свой), \
                mock.patch.object(launcher, "DEFAULT_FILE", запасной):
            self.assertEqual([п["id"] for п in launcher.read_list()], ["discord"])

    def test_the_shipped_default_list_has_a_browser_and_youtube(self):
        # В папке Трубы лежит запасной список: у нового человека телефон
        # показывает две кнопки, а не пустой экран.
        from core import launcher

        запасной = launcher.DEFAULT_FILE
        self.assertTrue(запасной.is_file(), запасной)
        список = json.loads(запасной.read_text(encoding="utf-8"))
        self.assertEqual([п["id"] for п in список], ["youtube", "browser"])
        for запись in список:
            self.assertEqual(запись["kind"], "url")
        браузер = список[1]
        self.assertEqual(браузер["title"], "Браузер")
        # Вид `url` открывает адрес браузером по умолчанию — так и задумано.
        self.assertTrue(браузер["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
