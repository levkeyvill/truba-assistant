"""«Выключить голос» во время загрузки и скачивание Higgs (01.10).

На чистой установке хозяин выбрал Higgs: включение голоса 15 минут молча
качало 9 ГБ внутри загрузки, телефон висел на «загружаюсь», а «Выключить
голос» не делал ничего — загрузка не смотрела, что её отменили. Теперь
веса качает отдельный процесс (его можно убить), а загрузка проверяет
отмену между шагами и не говорит «готова», если её выключили.

Настоящие модели, сеть и процессы здесь не запускаются — только подмены.
"""

import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import config
from core import app_icons, higgs_voice, voice_loop
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

    def test_отменённое_скачивание_higgs_останавливает_загрузку(self):
        цикл = _цикл()
        with mock.patch.object(higgs_voice, "weights_ready", return_value=False), \
                mock.patch.object(higgs_voice, "download_weights", return_value=False):
            with self.assertRaises(voice_loop._Stopped):
                цикл._fetch_higgs()

    def test_веса_уже_есть_ничего_не_качает(self):
        цикл = _цикл()
        качать = mock.Mock()
        with mock.patch.object(higgs_voice, "weights_ready", return_value=True), \
                mock.patch.object(higgs_voice, "download_weights", качать):
            цикл._fetch_higgs()
        качать.assert_not_called()

    def test_проценты_видны_а_журнал_не_засоряется(self):
        цикл = _цикл()

        def качать(стоп, показать):
            for гб in (0.1, 0.2, 0.3, 4.65, 4.7):
                показать(int(гб * 1e9))
            return True

        with mock.patch.object(higgs_voice, "weights_ready", return_value=False), \
                mock.patch.object(higgs_voice, "download_weights", side_effect=качать):
            цикл._fetch_higgs()
        тексты = [c.args[1] for c in цикл._emit.call_args_list if c.args[0] == "loading"]
        # Начало, 1 % и 50 % — в журнал; 2 %, 3 % и второй 50 % — только на экран.
        self.assertEqual(sum("%" in т for т in тексты), 2, тексты)
        self.assertTrue(any("50 %" in т for т in тексты), тексты)
        self.assertTrue(any("из ~9,3 ГБ" in т for т in тексты), тексты)


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


class СкачиваниеHiggsTests(unittest.TestCase):
    def setUp(self):
        папка = tempfile.TemporaryDirectory()
        self.addCleanup(папка.cleanup)
        подмена = mock.patch.object(config, "DATA_DIR", Path(папка.name))
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_кнопка_выключения_убивает_процесс(self):
        процесс = _Процесс()
        стоп = threading.Event()
        стоп.set()
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(higgs_voice, "_hub_bytes", return_value=0):
            итог = higgs_voice.download_weights(стоп, poll=0.01)
        self.assertFalse(итог)
        self.assertTrue(процесс.убит)

    def test_докачал_прогресс_шёл(self):
        процесс = _Процесс(шагов_до_конца=3)
        прогресс = []
        байты = iter([100, 200, 300, 400, 500, 600])
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(higgs_voice, "_hub_bytes", side_effect=lambda: next(байты)):
            итог = higgs_voice.download_weights(threading.Event(), прогресс.append, poll=0.01)
        self.assertTrue(итог)
        self.assertFalse(процесс.убит)
        self.assertTrue(прогресс)
        # Считается прирост от начала, а не весь кеш с другими моделями.
        self.assertEqual(прогресс[0], 100)

    def test_сбой_сети_честная_ошибка(self):
        процесс = _Процесс(шагов_до_конца=0, код=1)
        with mock.patch("subprocess.Popen", return_value=процесс), \
                mock.patch.object(higgs_voice, "_hub_bytes", return_value=0):
            with self.assertRaises(RuntimeError) as ошибка:
                higgs_voice.download_weights(threading.Event(), poll=0.01)
        self.assertIn("не скачалась", str(ошибка.exception))
        self.assertIn("с того же места", str(ошибка.exception))


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
