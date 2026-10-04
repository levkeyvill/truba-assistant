"""Тихий фон, пока Труба ищет в интернете: звук, телефон и голосовой цикл.

Звука на самом деле нет: `sounddevice.OutputStream` подменён, а файл
хозяина лежит во временной папке. Облако, микрофон и настоящие колонки в
прогоне не участвуют.

Проверяется ровно то, что может сломаться по-настоящему: длина и громкость
круга, свой файл вместо синтеза, порядок «сказала фильтр → фон → ответ»,
что фон гаснет при ошибке и что без настройки SEARCH_SOUND его нет вовсе.
"""

import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import wave as wave_mod
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core import personas, search_hum, settings
from core.brain import FILLERS
from core.phone import PhoneServer
from core.search_hum import SearchHum
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

ROOT = Path(config.ROOT)
WEB_WAIT = personas.PRESETS["pizdabol"]["wait"]["web"][0]


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _ждём(условие, секунд=2.0):
    """Ждёт, пока условие станет правдой. Потоки в тестах живут свои."""
    край = time.monotonic() + секунд
    while time.monotonic() < край:
        if условие():
            return True
        time.sleep(0.01)
    return False


# --- Звук ------------------------------------------------------------------


class PrepareTests(unittest.TestCase):
    """Файл любой: круг без стыка и тихая громкость делаются кодом."""

    RATE = 8000

    def test_stereo_becomes_one_channel(self):
        стерео = np.stack([np.full(8000, 0.2), np.full(8000, 0.4)], axis=1)
        self.assertEqual(search_hum.prepare(стерео, self.RATE).ndim, 1)

    def test_the_end_flows_into_the_beginning(self):
        # Пила: без склейки конец (0.99) прыгал бы в начало (0.0) щелчком.
        пила = np.linspace(0.0, 0.99, 8 * self.RATE, dtype=np.float32)
        круг = search_hum.prepare(пила, self.RATE)
        стык = int(search_hum.LOOP_FADE * self.RATE)
        self.assertEqual(len(круг), len(пила) - стык)
        # Последний звук круга — ровно тот, что стоял перед его первым.
        масштаб = float(круг[0]) / float(пила[стык])
        self.assertAlmostEqual(float(круг[-1]), float(пила[стык - 1]) * масштаб, places=4)

    def test_it_is_quiet_by_peak_and_by_average(self):
        громкий = np.sin(np.linspace(0, 2000, 4 * self.RATE)).astype(np.float32) * 0.9
        круг = search_hum.prepare(громкий, self.RATE)
        self.assertLessEqual(float(np.max(np.abs(круг))), search_hum.PEAK + 1e-6)
        self.assertLessEqual(float(np.sqrt(np.mean(круг ** 2))), search_hum.RMS + 1e-6)
        self.assertGreater(float(np.max(np.abs(круг))), 0.01)

    def test_a_short_file_is_not_eaten_by_the_joint(self):
        короткий = np.ones(400, dtype=np.float32) * 0.1
        self.assertEqual(len(search_hum.prepare(короткий, self.RATE)), 300)


class SoundFileTests(unittest.TestCase):
    """Звук — файл, который едет с программой. Нет его — фона нет, молча."""

    def setUp(self):
        self.папка = tempfile.TemporaryDirectory()
        self.addCleanup(self.папка.cleanup)
        self.путь = Path(self.папка.name) / "search_hum.wav"
        # Кеш модуля общий на процесс: без сброса первый тест сломал бы
        # остальные (а следующий прогон — этот).
        self.addCleanup(setattr, search_hum, "_cache", search_hum._cache)
        search_hum._cache = False
        подмена = mock.patch.object(search_hum, "SOUND_FILES", (self.путь,))
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_the_file_is_read_and_prepared(self):
        import soundfile as sf

        sf.write(str(self.путь), np.full(16000, 0.5, dtype=np.float32), 16000)
        волна, частота = search_hum.sound()
        self.assertEqual(частота, 16000)
        self.assertLessEqual(float(np.max(np.abs(волна))), search_hum.PEAK + 1e-6)

    def test_the_wav_for_the_phone_is_readable_and_mono(self):
        import soundfile as sf

        sf.write(str(self.путь), np.full(16000, 0.5, dtype=np.float32), 16000)
        данные = search_hum.wav_bytes()
        self.assertTrue(данные.startswith(b"RIFF"))
        with tempfile.TemporaryDirectory() as папка:
            файл = Path(папка) / "фон.wav"
            файл.write_bytes(данные)
            with wave_mod.open(str(файл)) as чтение:
                self.assertEqual(чтение.getnchannels(), 1)
                self.assertEqual(чтение.getsampwidth(), 2)
                self.assertEqual(чтение.getframerate(), 16000)

    def test_no_file_means_no_sound(self):
        self.assertIsNone(search_hum.sound())
        self.assertIsNone(search_hum.wav_bytes())

    def test_a_broken_file_means_no_sound(self):
        self.путь.write_bytes(b"\x00\x01not a wav at all")
        self.assertIsNone(search_hum.sound())

    def test_no_file_starts_nothing(self):
        динамик = _Динамик()
        динамик.отпустить.set()
        звонки = []
        фон = SearchHum(send_phone=lambda on, gain: звонки.append(on))
        фон.start(динамик, to_phone=True)
        time.sleep(0.2)
        self.assertEqual((звонки, динамик.ждали), ([], 0))


# --- Фон как объект --------------------------------------------------------


class _Динамик:
    """Динамик вместо колонок: `wait` держится, пока его не отпустят."""

    def __init__(self):
        self.device = None
        self.отпустить = threading.Event()
        self.ждали = 0

    def wait(self, timeout=None):
        self.ждали += 1
        return self.отпустить.wait(timeout)


class _Поток:
    """Заглушка `sounddevice.OutputStream`: пишет, когда его открыли и закрыли."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.запущен = False
        self.закрыт = False

    def start(self):
        self.запущен = True

    def stop(self):
        pass

    def close(self):
        self.закрыт = True


ЗВУК = (np.full(2400, 0.1, dtype=np.float32), 24000)


class SearchHumTests(unittest.TestCase):
    def setUp(self):
        self._было = getattr(config, "SEARCH_SOUND", True)
        config.SEARCH_SOUND = True
        self.addCleanup(lambda: setattr(config, "SEARCH_SOUND", self._было))
        # Звук — заготовка, а не файл из web/: его может и не быть.
        self.addCleanup(setattr, search_hum, "_cache", search_hum._cache)
        search_hum._cache = ЗВУК
        self.потоки = []

        def поток(**kwargs):
            созданный = _Поток(**kwargs)
            self.потоки.append(созданный)
            return созданный

        подмена = mock.patch.object(search_hum.sd, "OutputStream", поток)
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_the_sound_starts_only_after_the_phrase_is_over(self):
        динамик = _Динамик()
        фон = SearchHum(send_phone=None)

        фон.start(динамик, to_phone=False)
        self.assertTrue(_ждём(lambda: динамик.ждали == 1))
        # Фраза «секунду, гляну» ещё звучит — потока быть не должно.
        self.assertEqual(self.потоки, [])

        динамик.отпустить.set()
        self.assertTrue(_ждём(lambda: bool(self.потоки)))
        поток = self.потоки[0]
        self.assertTrue(поток.запущен)
        self.assertEqual(поток.kwargs["samplerate"], ЗВУК[1])
        self.assertEqual(поток.kwargs["channels"], 1)

    def test_the_speakers_fade_in_and_out_to_silence(self):
        # Линейная огибающая доходит до тишины перед закрытием потока;
        # закрытие на 13 % громкости давало щелчок по замеру.
        динамик = _Динамик()
        динамик.отпустить.set()
        фон = SearchHum(send_phone=None)
        фон.start(динамик, to_phone=False)
        self.assertTrue(_ждём(lambda: bool(self.потоки)))
        колбэк = self.потоки[0].kwargs["callback"]
        кусок = np.zeros((1200, 1), dtype=np.float32)
        for _ in range(20):          # 1 с — вход закончен
            колбэк(кусок, 1200, None, None)
        self.assertGreater(float(np.max(np.abs(кусок))), 0.05)
        фон.stop()
        for _ in range(20):          # 1 с — выход закончен
            колбэк(кусок, 1200, None, None)
        self.assertEqual(float(np.max(np.abs(кусок))), 0.0)
        self.assertTrue(_ждём(lambda: self.потоки[0].закрыт))

    def test_stop_before_the_phrase_ends_means_no_sound_at_all(self):
        динамик = _Динамик()
        фон = SearchHum(send_phone=None)

        фон.start(динамик, to_phone=False)
        self.assertTrue(_ждём(lambda: динамик.ждали == 1))
        фон.stop()
        динамик.отпустить.set()
        time.sleep(0.2)

        self.assertEqual(self.потоки, [])

    def test_the_phone_is_told_to_start_and_to_stop(self):
        динамик = _Динамик()
        динамик.отпустить.set()
        звонки = []
        фон = SearchHum(send_phone=lambda on, gain: звонки.append((on, gain)))

        фон.start(динамик, to_phone=True)
        self.assertTrue(_ждём(lambda: bool(звонки)))
        self.assertTrue(звонки[0][0])
        self.assertGreater(звонки[0][1], 0.0)

        фон.stop()
        self.assertTrue(_ждём(lambda: len(звонки) > 1))
        self.assertEqual(звонки[-1], (False, 0.0))
        # Телефон — не колонки: потока sounddevice быть не должно.
        self.assertEqual(self.потоки, [])

    def test_the_turned_off_setting_makes_no_sound_and_no_calls(self):
        config.SEARCH_SOUND = False
        динамик = _Динамик()
        динамик.отпустить.set()
        звонки = []

        фон = SearchHum(send_phone=lambda on, gain: звонки.append((on, gain)))
        фон.start(динамик, to_phone=True)
        фон.start(динамик, to_phone=False)
        time.sleep(0.2)
        фон.stop()

        self.assertEqual(звонки, [])
        self.assertEqual(self.потоки, [])
        self.assertEqual(динамик.ждали, 0)

    def test_stopping_twice_is_harmless(self):
        динамик = _Динамик()
        динамик.отпустить.set()
        звонки = []
        фон = SearchHum(send_phone=lambda on, gain: звонки.append((on, gain)))

        фон.start(динамик, to_phone=True)
        self.assertTrue(_ждём(lambda: bool(звонки)))
        фон.stop()
        фон.stop()
        time.sleep(0.2)

        # Выключили ровно один раз: телефон не должен получать «off» дважды.
        self.assertEqual([звонок[0] for звонок in звонки], [True, False])


# --- Голосовой цикл --------------------------------------------------------


class _Телефон:
    """Сервер телефона: то, к чему ходят фон и `PhoneSpeaker`."""

    def __init__(self):
        self.hum = []
        self.куски = 0
        self.audio_ready = True

    def attach(self, handler):
        pass

    def detach(self, handler):
        pass

    def send_audio(self, wave, rate):
        self.куски += 1

    def send_stop(self):
        pass

    def send_search_hum(self, on, gain=1.0):
        self.hum.append((on, gain))


class _Фон:
    """Запоминалка вместо `SearchHum`: пишет, когда его звали."""

    def __init__(self, порядок):
        self.порядок = порядок
        self.на_телефон = None

    def start(self, speaker, to_phone=False):
        self.на_телефон = to_phone
        self.порядок.append("фон:старт")

    def stop(self):
        self.порядок.append("фон:гас")


class _ДинамикПишущий:
    """Динамик, который пишет в общий список порядок `say`."""

    def __init__(self, порядок):
        self.порядок = порядок

    def say(self, wave, rate, gap=True):
        self.порядок.append("say")

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Тихо:
    def __getattr__(self, имя):
        return lambda *args, **kwargs: None


class _Голос:
    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.zeros(480, dtype=np.float32), 24000


class _Мозг:
    """Отдаёт заготовленные предложения, а `ошибка` — бросает после них."""

    ended = False

    def __init__(self, предложения, ошибка=None):
        self.предложения = list(предложения)
        self.ошибка = ошибка

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        for предложение in self.предложения:
            yield предложение
        if self.ошибка is not None:
            raise self.ошибка


def _цикл(мозг, порядок) -> VoiceLoop:
    """Голосовой цикл вручную: без микрофона, синтеза и облака."""
    loop = object.__new__(VoiceLoop)
    loop._brain = мозг
    loop._voice = _Голос()
    loop._ref = None
    loop._speaker = _ДинамикПишущий(порядок)
    loop._fallback = None
    loop._server = None
    loop._listener = _Тихо()
    loop._ducker = _Тихо()
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._open = False
    loop._last_turn = 0.0
    loop._speaking_text = ""
    loop._spoke_at = 0.0
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._hum = _Фон(порядок)
    return loop


class ГолосовойЦиклTests(unittest.TestCase):
    """Фон встаёт после фразы ожидания и гаснет перед ответом."""

    def setUp(self):
        self._было = (config.OUTPUT, config.TTS_ENGINE, config.TTS_SPEED,
                      config.TTS_NFE)
        config.OUTPUT = "speakers"
        config.TTS_ENGINE = "silero"

    def tearDown(self):
        (config.OUTPUT, config.TTS_ENGINE, config.TTS_SPEED,
         config.TTS_NFE) = self._было

    def test_the_sound_stands_after_the_filler_and_goes_before_the_answer(self):
        порядок = []
        self.assertIn(WEB_WAIT, FILLERS)
        loop = _цикл(_Мозг([WEB_WAIT, "Ответ."]), порядок)

        loop._answer("какой курс доллара")

        self.assertEqual(порядок,
                         ["say", "фон:старт", "фон:гас", "say", "фон:гас"])
        # В колонки фон идёт потоком на компе, а не сообщением на телефон.
        self.assertIs(loop._hum.на_телефон, False)

    def test_the_sound_goes_to_the_phone_when_the_voice_does(self):
        from core.phone import PhoneSpeaker

        порядок = []
        loop = _цикл(_Мозг([WEB_WAIT, "Ответ."]), порядок)
        loop._speaker = PhoneSpeaker(_Телефон(), gap=0.1)

        loop._answer("какой курс доллара")

        self.assertIs(loop._hum.на_телефон, True)
        # Речь ушла кусками в сервер телефона, а фон помечен в списке первым.
        self.assertEqual(порядок, ["фон:старт", "фон:гас", "фон:гас"])
        self.assertEqual(loop._speaker.server.куски, 2)

    def test_an_error_also_switches_the_sound_off(self):
        порядок = []
        loop = _цикл(_Мозг([WEB_WAIT], ошибка=RuntimeError("облако молчит")),
                     порядок)

        loop._answer("какой курс доллара")

        self.assertEqual(порядок[0], "say")
        self.assertEqual(порядок[1], "фон:старт")
        # Слова об ошибке произносятся уже после того, как фон погас, и в
        # конце фона гасит ещё раз — на случай, если он встанет снова.
        self.assertEqual(порядок[2], "фон:гас")
        self.assertEqual(порядок[3], "say")
        self.assertEqual(порядок[-1], "фон:гас")

    def test_an_ordinary_answer_never_starts_the_sound(self):
        порядок = []
        loop = _цикл(_Мозг(["Нормально."]), порядок)

        loop._answer("как дела?")

        # Фон не только не начинается — его ещё и гасят перед фразой: вдруг
        # он остался от прошлого раза.
        self.assertNotIn("фон:старт", порядок)
        self.assertEqual(порядок, ["фон:гас", "say", "фон:гас"])


# --- Телефон: сообщение и сам звук -----------------------------------------


class ServerHumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core import weather

        подмена = mock.patch.object(weather, "Watcher")
        подмена.start()
        cls.addClassCleanup(подмена.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)

    def test_the_phone_gets_the_sound_and_the_silence(self):
        сервер = object.__new__(PhoneServer)
        сервер._clients = set()
        сервер._audio = set()
        сервер._listeners = []
        сервер._loop = None
        отправлено: list = []

        class _Соединение:
            def send_text(self, payload):
                отправлено.append(json.loads(payload))

        with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
            сервер.send_search_hum(True, 0.35)
        шлёт.call_args[0][0](_Соединение())

        self.assertEqual(отправлено,
                         [{"type": "search_hum", "on": True, "gain": 0.35}])

    def test_the_phone_can_take_the_sound_itself(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.server._app)
        with mock.patch.object(search_hum, "_cache", ЗВУК):
            ответ = client.get("/search_hum.wav")

        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(ответ.headers["content-type"], "audio/wav")
        self.assertTrue(ответ.content.startswith(b"RIFF"))

    def test_no_sound_file_is_a_plain_not_found(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.server._app)
        with mock.patch.object(search_hum, "_cache", None):
            self.assertEqual(client.get("/search_hum.wav").status_code, 404)


# --- Настройка и страница --------------------------------------------------


class НастройкаTests(unittest.TestCase):
    """Галочка «Звук во время поиска» сохраняется и проверяется."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-hum-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=self.папка / "settings.json",
            ENV_PATH=self.папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        заметки = config.NOTES_DIR
        self.addCleanup(setattr, config, "NOTES_DIR", заметки)
        self._было = getattr(config, "SEARCH_SOUND", True)
        self.addCleanup(lambda: setattr(config, "SEARCH_SOUND", self._было))
        self.runtime = object.__new__(WebRuntime)
        self.runtime._provider_test_lock = threading.Lock()
        self.runtime._lock = threading.Lock()
        self.runtime.server = mock.Mock()
        self.runtime.brain = None
        self.runtime.voice = mock.Mock(running=False)
        self.runtime._enroll = None
        self.runtime._jobs = {}
        self.runtime._audio_stale = False
        self.runtime._remember = lambda kind, payload: None

    def _сохранено(self):
        return json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))

    def test_it_is_on_by_default_and_lands_in_settings(self):
        self.assertIn("search_sound", settings.DEFAULTS)
        self.assertTrue(settings.DEFAULTS["search_sound"])

        ответ = self.runtime.save_settings({"search_sound": False})

        self.assertTrue(ответ["ok"], ответ)
        self.assertIs(self._сохранено()["search_sound"], False)
        self.assertIs(config.SEARCH_SOUND, False)

    def test_anything_but_a_tick_is_refused(self):
        ответ = self.runtime.save_settings({"search_sound": "да"})

        self.assertFalse(ответ["ok"])
        # Плохое значение не пишется вовсе: файла может не быть ни разу.
        if settings.SETTINGS_PATH.exists():
            self.assertNotIn("search_sound", self._сохранено())


class СтраницаТелефонаTests(unittest.TestCase):
    def test_the_page_knows_the_sound(self):
        страница = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("function searchHum(on, gain)", страница)
        self.assertIn("msg.type === 'search_hum'", страница)
        self.assertIn("fetch('/search_hum.wav')", страница)
        # Фон крутится по кругу, иначе через 2.4 с наступала бы тишина.
        self.assertIn("src.loop = true;", страница)

    def test_the_pult_has_the_tick_and_its_caption(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")

        self.assertIn("настрПоле(поиск, 'search_sound', search_sound)", пульт)
        self.assertIn("search_sound: ['Звук во время поиска'", пульт)
        self.assertIn("search_sound: эл.search_sound.checked,", пульт)
        self.assertIn("эл.search_sound.checked = s.search_sound !== false;", пульт)
        # Подпись «Где искать» знает про все пять поисковиков.
        self.assertIn("DuckDuckGo, Яндекс и Bing", пульт)


if __name__ == "__main__":
    unittest.main()


