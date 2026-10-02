"""«Выключить голос» во время загрузки и скачивание моделей голоса.

На чистой установке хозяин выбрал Higgs: включение голоса 15 минут молча
качало 9 ГБ внутри загрузки, телефон висел на «загружаюсь», а «Выключить
голос» не делал ничего — загрузка не смотрела, что её отменили. С тех пор
загрузка проверяет отмену между шагами, веса качает отдельный процесс (его
можно убить), и с 02.10 — только пульт и только по выбору хозяина: голос
больше не качает модель вовсе и вместо этого отказывается словами.

Настоящие модели, сеть и процессы здесь не запускаются — только подмены.
"""

import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import config
from core import app_icons, voice_loop, voice_models
from ui.web_runtime import WebRuntime


def _цикл():
    """Цикл без __init__: только то, что нужно загрузке и `_run`."""
    цикл = voice_loop.VoiceLoop.__new__(voice_loop.VoiceLoop)
    цикл._stop = threading.Event()
    цикл._emit = mock.Mock()
    цикл._server = None
    цикл._external_server = None
    цикл._voice = None
    цикл.ready = False
    return цикл


class ОтменаЗагрузкиTests(unittest.TestCase):
    def test_отмена_между_шагами_бросает_остановку(self):
        цикл = _цикл()
        цикл._check_stop()  # не выключали — идём дальше
        цикл._stop.set()
        with self.assertRaises(voice_loop._Stopped):
            цикл._check_stop()

    def test_выключили_при_загрузке_не_готова_и_не_говорит(self):
        цикл = _цикл()
        цикл._phone_state = mock.Mock()
        цикл._load = mock.Mock(side_effect=voice_loop._Stopped())
        цикл._say_back = mock.Mock()
        цикл._run()
        self.assertFalse(цикл.ready)
        цикл._say_back.assert_not_called()
        события = [c.args[0] for c in цикл._emit.call_args_list]
        self.assertIn("stopped", события)
        self.assertNotIn("ready", события)
        self.assertNotIn("error", события)
        цикл._phone_state.assert_called_with("voiceoff")
        self.assertEqual(цикл.loading_text, "")

    def test_нет_модели_голос_не_стартует_и_ничего_не_качает(self):
        # 02.10: хозяин выбрал Higgs, включил голос — и молча пошли 9,3 ГБ.
        # Теперь включение голоса в сеть не ходит вовсе: модель качает пульт
        # по выбору хозяина, а голос только спрашивает, есть ли она.
        цикл = _цикл()
        скачать = mock.Mock(side_effect=AssertionError("голос не качает модель"))
        with mock.patch.object(config, "TTS_ENGINE", "higgs"), \
                mock.patch.object(voice_models, "installed", return_value=False), \
                mock.patch.object(voice_models, "downloading", return_value=None), \
                mock.patch.object(voice_models, "download", скачать), \
                mock.patch.object(voice_models, "clear_progress"):
            with self.assertRaises(RuntimeError) as ошибка:
                цикл._check_model()
        скачать.assert_not_called()
        self.assertIn("не скачана", str(ошибка.exception))
        self.assertIn("9,3 ГБ", str(ошибка.exception))
        self.assertIn("Голос", str(ошибка.exception))

    def test_модель_есть_голос_идёт_дальше(self):
        цикл = _цикл()
        with mock.patch.object(config, "TTS_ENGINE", "higgs"), \
                mock.patch.object(voice_models, "installed", return_value=True):
            цикл._check_model()  # отказа не было

    def test_silero_о_моделях_ничего_не_знает(self):
        # Silero лежит в самой установке, лишних проверок ему не надо.
        цикл = _цикл()
        спрашивали = mock.Mock()
        with mock.patch.object(config, "TTS_ENGINE", "silero"), \
                mock.patch.object(voice_models, "installed", спрашивали):
            цикл._check_model()
        спрашивали.assert_not_called()

    def test_идёт_качается_отказ_с_процентом(self):
        # Тот же отказ, но с «качается, N %»: хозяин не должен гадать, почему
        # голос не включился, когда пульт прямо сейчас качает эту модель.
        цикл = _цикл()
        with mock.patch.object(config, "TTS_ENGINE", "higgs"), \
                mock.patch.object(voice_models, "installed", return_value=False), \
                mock.patch.object(voice_models, "downloading", return_value=42):
            with self.assertRaises(RuntimeError) as ошибка:
                цикл._check_model()
        self.assertIn("качается, 42 %", str(ошибка.exception))

    def test_espeech_тоже_не_качается_сам(self):
        # Отказ тот же: у ESpeech тоже есть модель на 2,7 ГБ, и молча тянуть
        # её при включении голоса — ровно то, на что хозяин жаловался.
        цикл = _цикл()
        with mock.patch.object(config, "TTS_ENGINE", "espeech"), \
                mock.patch.object(voice_models, "installed", return_value=False), \
                mock.patch.object(voice_models, "downloading", return_value=None):
            with self.assertRaises(RuntimeError) as ошибка:
                цикл._check_model()
        self.assertIn("2,7 ГБ", str(ошибка.exception))


class _Процесс:
    """Подмена Popen: «качает», пока его не убьют или не закончит сам."""

    def __init__(self, шагов_до_конца=None, код=0):
        self.убит = False
        self._шагов = шагов_до_конца
        self.returncode = None
        self._код = код

    def poll(self):
        if self.убит:
            self.returncode = -9
        elif self._шагов is not None:
            self._шагов -= 1
            if self._шагов < 0:
                self.returncode = self._код
        return self.returncode

    def kill(self):
        self.убит = True

    def wait(self):
        return self.poll()


class СкачиваниеМоделиTests(unittest.TestCase):
    """Скачивание отдельным процессом: отмена, прогресс, честный отказ сети."""

    def setUp(self):
        папка = tempfile.TemporaryDirectory()
        self.addCleanup(папка.cleanup)
        подмена = mock.patch.object(voice_models, "LOG_DIR", Path(папка.name))
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_кнопка_отмены_убивает_процесс(self):
        процесс = _Процесс()
        стоп = threading.Event()
        стоп.set()
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(voice_models, "cache_bytes", return_value=0):
            итог = voice_models.download("higgs", стоп, poll=0.01)
        self.assertFalse(итог)
        self.assertTrue(процесс.убит)

    def test_докачал_прогресс_шёл(self):
        процесс = _Процесс(шагов_до_конца=3)
        прогресс = []
        байты = iter([100, 200, 300, 400, 500, 600])
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(voice_models, "cache_bytes",
                                  side_effect=lambda: next(байты)):
            итог = voice_models.download("higgs", threading.Event(),
                                         прогресс.append, poll=0.01)
        self.assertTrue(итог)
        self.assertFalse(процесс.убит)
        self.assertTrue(прогресс)
        # Считается прирост от начала, а не весь кеш с другими моделями.
        self.assertEqual(прогресс[0], 100)

    def test_сбой_сети_честная_ошибка(self):
        процесс = _Процесс(шагов_до_конца=0, код=1)
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(voice_models, "cache_bytes", return_value=0):
            with self.assertRaises(RuntimeError) as ошибка:
                voice_models.download("higgs", threading.Event(), poll=0.01)
        self.assertIn("не скачалась", str(ошибка.exception))
        self.assertIn("с того же места", str(ошибка.exception))

    def test_считается_вся_папка_кеша_а_не_только_hub(self):
        # 02.10: прогресс стоял на «0,0 ГБ» при скачанных 257 МБ — считался
        # только `hub`, а половина прироста лежит в `xet`.
        with tempfile.TemporaryDirectory() as папка:
            корень = Path(папка)
            (корень / "hub").mkdir()
            (корень / "xet").mkdir()
            (корень / "hub" / "blob").write_bytes(b"x" * 100)
            (корень / "xet" / "chunk").write_bytes(b"y" * 300)
            with mock.patch.dict("os.environ", {"HF_HOME": папка}):
                self.assertEqual(voice_models.hf_dir(), Path(папка))
                self.assertEqual(voice_models.cache_bytes(), 400)

    def test_espeech_качает_два_файла_а_higgs_весь_репозиторий(self):
        # Списком файлов решает сам загрузчик синтеза: Higgs тянет снимок
        # целиком, ESpeech — ровно два файла.
        self.assertIsNone(voice_models.targets("higgs")["files"])
        self.assertEqual(len(voice_models.targets("espeech")["files"]), 2)
        self.assertIn("snapshot_download", voice_models._код(
            voice_models.targets("higgs")))
        self.assertIn("hf_hub_download", voice_models._код(
            voice_models.targets("espeech")))


class СостояниеГолосаTests(unittest.TestCase):
    def _среда(self, голос):
        среда = WebRuntime.__new__(WebRuntime)
        среда.voice = голос
        return среда

    def test_пульт_видит_что_грузится_и_что_выключается(self):
        стоп = threading.Event()
        голос = types.SimpleNamespace(running=True, ready=False, in_conversation=False,
                                      loading_text="качаю модель Higgs: 1,0 из ~9,3 ГБ (10 %)",
                                      _stop=стоп)
        состояние = self._среда(голос).voice_state()
        self.assertIn("10 %", состояние["loading_text"])
        self.assertFalse(состояние["stopping"])
        стоп.set()
        self.assertTrue(self._среда(голос).voice_state()["stopping"])

    def test_подмена_голоса_не_ломает_состояние(self):
        # В тестах голос бывает Mock: его «атрибуты» — тоже Mock, их не берём.
        состояние = self._среда(mock.Mock(running=True, ready=False)).voice_state()
        self.assertEqual(состояние["loading_text"], "")
        self.assertFalse(состояние["stopping"])


class ЗначокSquirrelTests(unittest.TestCase):
    """Discord запускается через Update.exe, у которого значка нет."""

    def setUp(self):
        папка = tempfile.TemporaryDirectory()
        self.addCleanup(папка.cleanup)
        self.корень = Path(папка.name) / "Discord"
        (self.корень / "app-1.0.9260").mkdir(parents=True)
        (self.корень / "Update.exe").write_bytes(b"MZ")
        (self.корень / "app-1.0.9260" / "Discord.exe").write_bytes(b"MZ")

    def _кнопка(self):
        return {"path": str(self.корень / "Update.exe"),
                "args": ["--processStart", "Discord.exe"]}

    def test_берёт_логотип_app_ico(self):
        (self.корень / "app.ico").write_bytes(b"\0")
        self.assertEqual(app_icons._icon_file(self._кнопка()), self.корень / "app.ico")

    def test_без_app_ico_берёт_саму_программу(self):
        self.assertEqual(app_icons._icon_file(self._кнопка()),
                         self.корень / "app-1.0.9260" / "Discord.exe")

    def test_переменные_в_пути_раскрываются(self):
        with mock.patch.dict("os.environ", {"ТРУБА_ПРОБА": str(self.корень)}):
            путь = app_icons._icon_file({"path": "%ТРУБА_ПРОБА%\\Firefox.exe"})
        self.assertEqual(путь, self.корень / "Firefox.exe")

    def test_обычная_программа_как_была(self):
        путь = str(self.корень / "app-1.0.9260" / "Discord.exe")
        self.assertEqual(app_icons._icon_file({"path": путь}), Path(путь))


if __name__ == "__main__":
    unittest.main()
