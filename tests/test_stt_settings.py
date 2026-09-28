"""Настройки распознавания речи: модель, сжатие, смена без перезапуска.

Что тут проверяется и почему именно так:
  - список моделей один (`core/stt_models.py`), и кривое значение — это
    значение по умолчанию, а не отказ: опечатка в пульте не должна оставить
    голос без слуха;
  - `reload_stt` подменяет модель только после успешной загрузки: не
    загрузилась — работает прежняя, а в журнал падает ошибка;
  - сохранение настроек зовёт перезагрузку только когда модель или сжатие
    действительно поменялись;
  - `/api/stt` отдаёт модель и её список, `/api/stt/test` без голоса
    отвечает словами, а не кодом ошибки.

Настоящие модели не качаются и не грузятся: `load_stt` подменён, микрофон —
тоже. settings.json хозяина тест не трогает (путь во временной папке), как в
остальных прогонах.
"""

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
from core import settings, stt_models, voice_loop
from core.phone import PhoneServer
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
PULT_HTML = Path(config.ROOT) / "ui" / "web" / "pult.html"



# --- Список моделей и значения по умолчанию ---------------------------------


class СписокМоделейTests(unittest.TestCase):
    def test_в_списке_три_модели_с_названиями_и_размером(self):
        self.assertEqual(len(stt_models.MODELS), 3)
        for модель in stt_models.MODELS:
            self.assertTrue(модель["id"], модель)
            self.assertTrue(модель["title"], модель)
            self.assertTrue(модель["note"], модель)
            self.assertTrue(модель["size"], модель)

    def test_сжатие_бывает_int8_и_none(self):
        self.assertEqual(stt_models.QUANT_IDS, ("int8", "none"))

    def test_из_списка_берётся_как_есть(self):
        self.assertEqual(
            stt_models.valid_model("gigaam-v3-e2e-ctc"), "gigaam-v3-e2e-ctc")
        self.assertEqual(stt_models.valid_quantization("none"), "none")

    def test_мусор_даёт_значение_по_умолчанию(self):
        for мусор in ("", "   ", "gigaam", None, 42, ["x"], "GigaAM-V3-E2E-RNNT"):
            with self.subTest(мусор=мусор):
                self.assertEqual(stt_models.valid_model(мусор),
                                 stt_models.DEFAULT)
        for мусор in ("", "int4", "fp16", None, 7):
            with self.subTest(мусор=мусор):
                self.assertEqual(stt_models.valid_quantization(мусор), "int8")

    def test_none_превращается_в_none_для_config(self):
        self.assertIsNone(stt_models.quantization_for_config("none"))
        self.assertEqual(stt_models.quantization_for_config("int8"), "int8")
        self.assertEqual(stt_models.quantization_for_config("ерунда"), "int8")

    def test_обратно_из_config_в_слово(self):
        # В config полная модель — это None, а пульту нужно слово из списка.
        self.assertEqual(stt_models.quantization_name(None), "none")
        self.assertEqual(stt_models.quantization_name("none"), "none")
        self.assertEqual(stt_models.quantization_name("int8"), "int8")

    def test_описание_для_чужака_берётся_у_модели_по_умолчанию(self):
        self.assertEqual(stt_models.find_model("ерунда")["id"],
                         stt_models.DEFAULT)

    def test_снимок_для_пульта_не_общий(self):
        # Иначе пульт дописал бы что-нибудь прямо в список моделей.
        первый = stt_models.models_payload()
        первый[0]["title"] = "поменяли"
        self.assertNotEqual(stt_models.MODELS[0]["title"], "поменяли")


class НастройкиTests(unittest.TestCase):
    """Значения доезжают до config как надо."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-stt-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json",
            ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        # `apply_to_config` переносит и папку заметок — возвращаем.
        self._было = (config.STT_MODEL, config.STT_QUANTIZATION, config.NOTES_DIR)
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        (config.STT_MODEL, config.STT_QUANTIZATION,
         config.NOTES_DIR) = self._было

    def test_по_умолчанию_та_же_модель_что_и_была_в_config(self):
        self.assertEqual(settings.DEFAULTS["stt_model"], config.STT_MODEL)
        self.assertEqual(settings.DEFAULTS["stt_quantization"], "int8")

    def test_выбор_доезжает_до_config(self):
        settings.save_settings({"stt_model": "gigaam-v3-e2e-ctc",
                                "stt_quantization": "none"})
        settings.apply_to_config()
        self.assertEqual(config.STT_MODEL, "gigaam-v3-e2e-ctc")
        self.assertIsNone(config.STT_QUANTIZATION)

    def test_мусор_в_settings_не_ломает_config(self):
        settings.save_settings({"stt_model": "ерунда",
                                "stt_quantization": "int4"})
        settings.apply_to_config()
        self.assertEqual(config.STT_MODEL, stt_models.DEFAULT)
        self.assertEqual(config.STT_QUANTIZATION, "int8")

    def test_проверки_мягкие(self):
        self.assertEqual(settings.validate_stt_model("ерунда"),
                         stt_models.DEFAULT)
        self.assertEqual(settings.validate_stt_quantization("ерунда"), "int8")
        self.assertIsNone(settings.stt_quantization_for_config("none"))


# --- Смена модели на живом голосе -------------------------------------------


class _Модель:
    """Заглушка onnx_asr: считает прогоны, отдаёт свой текст."""

    def __init__(self, имя):
        self.имя = имя
        self.прогрето = 0

    def recognize(self, audio, sample_rate=None):
        self.прогрето += 1
        return f"услышано {self.имя}"


def _цикл(старая) -> VoiceLoop:
    """Голос без железа: поток «живой», модель — заготовка."""
    loop = object.__new__(VoiceLoop)
    loop._stt = старая
    loop._stt_name = "gigaam-v3-e2e-rnnt"
    loop._stt_quant = "int8"
    loop._stt_loading = False
    loop._stt_error = ""
    loop._thread = NS(is_alive=lambda: True)
    loop._события = []
    loop._emit = lambda kind, payload: loop._события.append((kind, payload))
    return loop


def _ждать(цикл, timeout=5.0):
    """Ждём конца фоновой загрузки: поток в `reload_stt` живой.

    Проверка без `assert`: функция вызывается из теста, а не из метода, и
    unittest.TestCase.assertEqual тут не на что опереться.
    """
    край = time.monotonic() + timeout
    while цикл._stt_loading and time.monotonic() < край:
        time.sleep(0.02)
    if цикл._stt_loading:
        raise AssertionError("загрузка не закончилась")


class СменаМоделиTests(unittest.TestCase):
    def setUp(self):
        self._было = (config.STT_MODEL, config.STT_QUANTIZATION)
        self.addCleanup(self._вернуть)
        self.старая = _Модель("старая")

    def _вернуть(self):
        config.STT_MODEL, config.STT_QUANTIZATION = self._было

    def test_новая_модель_встаёт_после_успешной_загрузки(self):
        новая = _Модель("новая")
        config.STT_MODEL = "gigaam-v3-e2e-ctc"
        цикл = _цикл(self.старая)
        with mock.patch.object(voice_loop, "load_stt",
                               return_value=новая) as грузка:
            цикл.reload_stt()
            _ждать(цикл)
        self.assertIs(цикл._stt, новая)
        self.assertEqual(цикл._stt_name, "gigaam-v3-e2e-ctc")
        # Прогрев: без него первая фраза ждала бы, пока модель раскачается.
        self.assertEqual(новая.прогрето, 1)
        # `reload_stt` зовёт нашу же `load_stt`, поэтому оба аргумента
        # достаются из config как есть.
        self.assertEqual(грузка.call_args.args,
                         ("gigaam-v3-e2e-ctc", config.STT_QUANTIZATION))
        self.assertEqual(цикл._stt_error, "")

    def test_сжатие_берётся_из_config(self):
        config.STT_QUANTIZATION = None
        цикл = _цикл(self.старая)
        with mock.patch.object(voice_loop, "load_stt",
                               return_value=_Модель("полная")) as грузка:
            цикл.reload_stt()
            _ждать(цикл)
        self.assertIsNone(грузка.call_args.args[1])
        self.assertIsNone(цикл._stt_quant)

    def test_ошибка_загрузки_оставляет_прежнюю_модель(self):
        цикл = _цикл(self.старая)
        with mock.patch.object(voice_loop, "load_stt",
                               side_effect=RuntimeError("нет такой модели")):
            цикл.reload_stt()
            _ждать(цикл)
        self.assertIs(цикл._stt, self.старая)
        self.assertEqual(цикл._stt_name, "gigaam-v3-e2e-rnnt")
        self.assertIn("нет такой модели", цикл._stt_error)
        self.assertEqual(цикл._события[-1][0], "stt_failed")

    def test_ошибка_прогрева_тоже_оставляет_прежнюю(self):
        # Модель загрузилась, но первый же прогон упал: подменять нечего.
        class _Битая(_Модель):
            def recognize(self, audio, sample_rate=None):
                raise RuntimeError("сессия не создалась")

        цикл = _цикл(self.старая)
        with mock.patch.object(voice_loop, "load_stt",
                               return_value=_Битая("битая")):
            цикл.reload_stt()
            _ждать(цикл)
        self.assertIs(цикл._stt, self.старая)
        self.assertEqual(цикл._события[-1][0], "stt_failed")

    def test_события_о_загрузке_и_готовности(self):
        config.STT_MODEL = "gigaam-v3-e2e-ctc"
        цикл = _цикл(self.старая)
        with mock.patch.object(voice_loop, "load_stt",
                               return_value=_Модель("новая")):
            цикл.reload_stt()
            _ждать(цикл)
        виды = [вид for вид, _ in цикл._события]
        self.assertEqual(виды, ["stt_loading", "stt_ready"])
        self.assertIn("gigaam-v3-e2e-ctc", цикл._события[0][1])

    def test_выключенный_голос_ничего_не_грузит(self):
        # Голос не запущен — при включении модель и так встанет из config.
        цикл = _цикл(self.старая)
        цикл._thread = None
        with mock.patch.object(voice_loop, "load_stt") as грузка:
            цикл.reload_stt()
            time.sleep(0.05)
        грузка.assert_not_called()
        self.assertIs(цикл._stt, self.старая)
        self.assertEqual(цикл._события, [])

    def test_повторный_вызов_пока_идёт_загрузка_игнорируется(self):
        цикл = _цикл(self.старая)
        цикл._stt_loading = True
        with mock.patch.object(voice_loop, "load_stt") as грузка:
            цикл.reload_stt()
        грузка.assert_not_called()

    def test_состояние_для_пульта(self):
        цикл = _цикл(self.старая)
        цикл._stt_quant = None
        состояние = цикл.stt_state()
        self.assertEqual(состояние["model"], "gigaam-v3-e2e-rnnt")
        self.assertEqual(состояние["quantization"], "none")
        self.assertFalse(состояние["loading"])
        self.assertIn("Точная", состояние["title"])


# --- Сохранение настроек зовёт перезагрузку ----------------------------------


def _пульт(звонки) -> WebRuntime:
    """Пульт без журнала, без снимка и без сети; фон — прямым вызовом."""
    runtime = object.__new__(WebRuntime)
    runtime._lock = threading.Lock()
    runtime._provider_test_lock = threading.Lock()
    runtime._enroll = None
    runtime._jobs = {}
    runtime._audio_stale = False
    runtime.brain = None
    runtime.voice = NS(reload_stt=lambda: звонки.append("reload"))
    runtime._remember = lambda kind, payload: None
    runtime._bg = lambda func, *args: func(*args)
    return runtime


class СохранениеTests(unittest.TestCase):
    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-stt-save-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json",
            ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        заметки = config.NOTES_DIR
        self.addCleanup(setattr, config, "NOTES_DIR", заметки)
        self._было = (config.STT_MODEL, config.STT_QUANTIZATION)
        self.addCleanup(self._вернуть)
        self.звонки = []
        self.runtime = _пульт(self.звонки)

    def _вернуть(self):
        config.STT_MODEL, config.STT_QUANTIZATION = self._было

    def test_смена_модели_зовёт_перезагрузку(self):
        ответ = self.runtime.save_settings(
            {"stt_model": "gigaam-v3-e2e-ctc", "stt_quantization": "int8"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self.звонки, ["reload"])
        self.assertEqual(config.STT_MODEL, "gigaam-v3-e2e-ctc")

    def test_смена_сжатия_тоже_зовёт(self):
        self.runtime.save_settings({"stt_quantization": "none"})
        self.assertEqual(self.звонки, ["reload"])
        self.assertIsNone(config.STT_QUANTIZATION)

    def test_без_смены_перезагрузки_нет(self):
        self.runtime.save_settings({"stt_model": stt_models.DEFAULT,
                                    "stt_quantization": "int8"})
        self.assertEqual(self.звонки, [])
        self.runtime.save_settings({"hedge": True})
        self.assertEqual(self.звонки, [])

    def test_кривое_значение_не_будит_голос(self):
        ответ = self.runtime.save_settings({"stt_model": "ерунда"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self.звонки, [])

    def test_снимок_для_пульта_содержит_новые_ключи(self):
        self.runtime.save_settings({"stt_model": "gigaam-v3-e2e-ctc"})
        снимок = self.runtime.settings_snapshot()
        self.assertEqual(снимок["settings"]["stt_model"], "gigaam-v3-e2e-ctc")


class СостояниеИПробаTests(unittest.TestCase):
    def setUp(self):
        self.звонки = []
        self.runtime = _пульт(self.звонки)
        self.модель = _Модель("работающая")
        self.runtime.voice = NS(_stt=self.модель, running=False,
                                in_conversation=False,
                                _speaker_idle=lambda: True,
                                _stt_locked=threading.Lock)
        # Настоящий метод класса: он читает те же поля, что и живой цикл.
        self.runtime.voice.stt_state = (
            lambda: VoiceLoop.stt_state(self.runtime.voice))

    def test_состояние_отдаёт_модель_и_списки(self):
        голос = self.runtime.voice
        голос._stt_name = "gigaam-v3-e2e-rnnt"
        голос._stt_quant = "int8"
        голос._stt_loading = False
        голос._stt_error = ""
        состояние = self.runtime.stt_state()
        self.assertTrue(состояние["ok"])
        self.assertEqual(состояние["model"], "gigaam-v3-e2e-rnnt")
        self.assertEqual(состояние["quantization"], "int8")
        self.assertEqual([м["id"] for м in состояние["models"]],
                         list(stt_models.IDS))
        self.assertEqual([к["id"] for к in состояние["quantizations"]],
                         list(stt_models.QUANT_IDS))

    def test_без_голоса_показывается_модель_из_config(self):
        self.runtime.voice = NS(_stt=None)
        состояние = self.runtime.stt_state()
        self.assertEqual(состояние["model"], config.STT_MODEL)
        self.assertFalse(состояние["loading"])

    def test_проба_без_голоса_говорит_словами(self):
        self.runtime.voice = NS(_stt=None)
        ответ = self.runtime.stt_test()
        self.assertFalse(ответ["ok"])
        self.assertIn("голос", ответ["error"])

    def test_проба_пишет_микрофон_и_узнаёт_текст(self):
        # Микрофон подменён: звука в тесте нет и быть не должно.
        дорожка = [0.0] * (config.SAMPLE_RATE * 4)
        with mock.patch("core.selftest.record",
                        return_value=(дорожка, config.SAMPLE_RATE)) as запись:
            ответ = self.runtime.stt_test()
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["text"], "услышано работающая")
        self.assertAlmostEqual(ответ["seconds"], 4.0, places=1)
        self.assertTrue(запись.called)

    def test_при_работающем_голосе_проба_берёт_его_следующую_фразу(self):
        # Голос у хозяина работает всегда, микрофон занят им: проба не пишет
        # второй раз, а ждёт фразу, которую разберёт сам цикл.
        import threading
        import time

        self.runtime.voice.running = True
        self.runtime.voice._last_stt = (time.monotonic() - 5, "старая фраза", 1.0)

        def сказал():
            time.sleep(0.3)
            self.runtime.voice._last_stt = (time.monotonic(), "привет, труба", 1.4)

        threading.Thread(target=сказал, daemon=True).start()
        with mock.patch("core.selftest.record") as запись:
            ответ = self.runtime.stt_test()
        запись.assert_not_called()
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["text"], "привет, труба")
        self.assertEqual(ответ["seconds"], 1.4)

    def test_при_работающем_голосе_тишина_говорит_словами(self):
        self.runtime.voice.running = True
        self.runtime.voice._last_stt = None
        with mock.patch.object(type(self.runtime), "STT_WAIT_SECONDS", 0.4),                 mock.patch("core.selftest.record") as запись:
            ответ = self.runtime.stt_test()
        запись.assert_not_called()
        self.assertFalse(ответ["ok"])
        self.assertIn("ничего не услышала", ответ["error"])

    def test_проба_без_микрофона_говорит_словами(self):
        with mock.patch("core.selftest.record",
                        side_effect=RuntimeError("микрофон не найден")):
            ответ = self.runtime.stt_test()
        self.assertFalse(ответ["ok"])
        self.assertIn("микрофон не найден", ответ["error"])


# --- API -------------------------------------------------------------------

# Порт, на котором поднялся сервер: `_local_secret` сверяет Host с ним.
ПОРТ: list = []


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)
        ПОРТ.append(cls.server.port)

    def setUp(self):
        self.server.runtime = _пульт([])
        self.server.runtime.voice = NS(
            _stt=_Модель("работающая"), running=False, in_conversation=False,
            _speaker_idle=lambda: True, _stt_locked=threading.Lock)
        self.client = TestClient(self.server._app,
                                 base_url=f"http://127.0.0.1:{ПОРТ[0]}")
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app,
                                base_url=f"http://127.0.0.1:{ПОРТ[0]}",
                                client=("192.168.1.50", 5555))

    def test_состояние_отдаёт_модель_и_её_список(self):
        тело = self.client.get("/api/stt").json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["model"], config.STT_MODEL)
        self.assertIn(тело["quantization"], ("int8", "none"))
        self.assertEqual([м["id"] for м in тело["models"]],
                         list(stt_models.IDS))
        self.assertEqual([к["id"] for к in тело["quantizations"]],
                         list(stt_models.QUANT_IDS))

    def test_состояние_не_даётся_сети(self):
        self.assertEqual(self.вдали.get("/api/stt").status_code, 403)

    def test_проба_без_голоса_говорит_словами(self):
        self.server.runtime.voice = NS(_stt=None)
        ответ = self.client.post("/api/stt/test")
        self.assertEqual(ответ.status_code, 400)
        self.assertIn("голос", ответ.json()["error"])

    def test_проба_с_телефона_не_проходит(self):
        self.assertEqual(self.вдали.post("/api/stt/test").status_code, 403)


# --- Пульт: раздел и его кнопки --------------------------------------------


class ПултTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT_JS.read_text(encoding="utf-8")
        cls.разметка = PULT_HTML.read_text(encoding="utf-8")

    def test_раздел_есть_на_странице_голоса(self):
        # «Звук» — последний подраздел «Голоса» (28.09, часть 2а).
        self.assertIn("голос: ['голос', 'слух', 'распознавание', 'звук']", self.скрипт)
        self.assertIn("['распознавание', 'Распознавание',", self.скрипт)
        self.assertIn('data-голос="распознавание"', self.разметка)

    def test_раздел_построен_и_показывается(self):
        self.assertIn(
            "настрСекция(содержимое, 'распознавание', 'Распознавание')",
            self.скрипт)
        self.assertIn("if (выбран === 'распознавание') загрузитьРаспознавание();",
                      self.скрипт)

    def test_в_форме_есть_оба_поля_и_кнопка_проверки(self):
        for кусок in ("настрПоле(расп, 'stt_model', stt_model)",
                      "настрПоле(расп, 'stt_quantization', stt_quantization)",
                      "'Проверить распознавание'",
                      "stt_model: эл.stt_model.value || undefined",
                      "stt_quantization: эл.stt_quantization.value"):
            with self.subTest(кусок=кусок):
                self.assertIn(кусок, self.скрипт)

    def test_обращается_к_своим_адресам(self):
        self.assertIn("'/api/stt'", self.скрипт)
        self.assertIn("'/api/stt/test'", self.скрипт)

    def test_новая_функция_объявлена_и_звана(self):
        for имя in ("загрузитьРаспознавание", "показатьРасп", "распПояснить",
                    "проверитьРаспознавание"):
            with self.subTest(имя=имя):
                self.assertIn("function " + имя + "(", self.скрипт)
        self.assertIn("распПроверить.addEventListener('click'", self.скрипт)


if __name__ == "__main__":
    unittest.main()
