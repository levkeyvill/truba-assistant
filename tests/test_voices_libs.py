"""Установка библиотек качественных голосов из пульта.

Пока torch не загружен, Windows позволяет заменить его файлы. Если голос
уже использовал torch, пульт перезапускается без голоса перед установкой.

Здесь проверяется весь путь: состояние установки для полосы, отмена, честная
ошибка, флаг и перезапуск, продолжение после перезапуска и то, что после
успеха скачиваются выбранные модели. Ни uv, ни сети, ни настоящих файлов — всё
подменено (`subprocess`, железо, настройки).
"""

import importlib
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from core import hardware, voice_models, voices_install
from ui.web_runtime import WebRuntime


def _пульт() -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер, голос и мозг."""
    среда = object.__new__(WebRuntime)
    среда.server = None
    среда._lock = threading.Lock()
    среда._download_lock = threading.Lock()
    среда._download_state = {
        "active": False, "model": "", "title": "", "done": 0, "total": 0,
        "queue": [], "error": "", "finished": []}
    среда._download_stop = None
    среда._libs_lock = threading.Lock()
    среда._libs_state = {"active": False, "step": 0, "steps": 2, "title": "",
                         "downloaded": 0, "error": "", "done": False}
    среда._libs_stop = None
    среда.события = []
    среда._remember = lambda kind, payload: среда.события.append((kind, payload))
    среда.close_pult = lambda: среда.события.append(("closed", None))
    среда.voice = NS(running=False)
    return среда


class ФлагTests(unittest.TestCase):
    """Флаг «поставить после перезапуска» — на временной папке, не в data."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-libs-flag-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.object(voices_install, "PENDING_PATH",
                                    self.папка / "voices_install_pending.json")
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_запись_и_чтение_совпадают(self):
        self.assertTrue(voices_install.write_pending(["higgs", "espeech"]))
        флаг = voices_install.read_pending()
        self.assertEqual(флаг["models"], ["higgs", "espeech"])
        self.assertTrue(флаг.get("at"), "в флаге видно, когда он оставлен")

    def test_нет_флага_это_пустой_словарь(self):
        self.assertEqual(voices_install.read_pending(), {})

    def test_битый_флаг_не_роняет_пульт(self):
        (self.папка / "voices_install_pending.json").write_text("не json",
                                                                encoding="utf-8")
        self.assertEqual(voices_install.read_pending(), {})

    def test_очистка_убирает_файл(self):
        voices_install.write_pending(["higgs"])
        voices_install.clear_pending()
        self.assertFalse((self.папка / "voices_install_pending.json").exists())
        # Повторная очистка не должна падать: флага уже нет.
        voices_install.clear_pending()

    def test_не_записался_значит_правда_не_записался(self):
        # На месте файл, а не папка: `mkdir` не сможет пройти, и запись флага
        # обязана честно сказать об этом.
        (self.папка / "не-папка").write_text("я файл", encoding="utf-8")
        сломанная = mock.patch.object(voices_install, "PENDING_PATH",
                                      self.папка / "не-папка" / "файл.json")
        with сломанная:
            self.assertFalse(voices_install.write_pending(["higgs"]))


class УстановкаВПультеTests(unittest.TestCase):
    """Пока torch не загружен — библиотеки ставятся здесь, без перезапуска."""

    def setUp(self):
        self.rt = _пульт()
        # Настройки подменены: тест не пишет в settings.json этого компьютера.
        for подмена in (mock.patch("core.settings.save_settings"),
                        mock.patch("core.settings.voices_wanted",
                                   return_value=["higgs"])):
            подмена.start()
            self.addCleanup(подмена.stop)

    def _поставить(self):
        """Прогнать фоновую установку целиком (поток — подменой `install`)."""
        self.rt._ставить_библиотеки(threading.Event(), ["higgs"])

    def test_после_успеха_качаются_выбранные_модели(self):
        # Ради этого хозяин и нажимают кнопку: сначала библиотеки, потом модели.
        with mock.patch.object(voices_install, "install",
                               return_value={"ok": True, "restart": False}) as ставим, \
                mock.patch.object(self.rt, "voices_download",
                                  return_value={"ok": True, "queue": ["higgs"]}) as качать:
            self._поставить()
        ставим.assert_called_once()
        качать.assert_called_once_with(["higgs"])
        состояние = self.rt._libs()
        self.assertFalse(состояние["active"])
        self.assertTrue(состояние["done"])
        self.assertEqual(состояние["error"], "")

    def test_шаги_видны_в_состоянии_для_полосы(self):
        шаги = []

        def установка(on_step=None, on_progress=None, stop=None):
            шаги.append(on_step)
            on_step(step=1, steps=2, title="torch с CUDA", text="ставлю torch")
            on_progress(1_200_000_000)
            on_step(step=2, steps=2, title="библиотеки голосов", text="ставлю пакеты")
            on_progress(4_500_000_000)
            return {"ok": True, "restart": False}

        with mock.patch.object(voices_install, "install", установка), \
                mock.patch.object(self.rt, "voices_download"):
            self._поставить()
        self.assertEqual(len(шаги), 1)
        состояние = self.rt._libs()
        self.assertEqual(состояние["step"], 2)
        self.assertEqual(состояние["steps"], 2)
        self.assertEqual(состояние["title"], "библиотеки голосов")
        self.assertEqual(состояние["downloaded"], 4_500_000_000)

    def test_ошибка_шага_честным_текстом_и_установка_кончена(self):
        отказ = {"ok": False, "error": "качественные голоса не поставились: шаг 1 "
                                        "из 2 — torch с CUDA, подробности в "
                                        "data\\voices_install.log"}
        with mock.patch.object(voices_install, "install", return_value=отказ), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self._поставить()
        состояние = self.rt._libs()
        self.assertFalse(состояние["active"], "после отказа полоса не должна врать")
        self.assertFalse(состояние["done"])
        self.assertIn("voices_install.log", состояние["error"])
        # Ошибка установки — не повод скачивать модели: библиотек нет.
        качать.assert_not_called()
        self.assertTrue(any(вид == "voices_failed"
                            for вид, _ in self.rt.события))

    def test_отмена_не_ошибка(self):
        with mock.patch.object(voices_install, "install",
                               return_value={"ok": False, "cancelled": True}), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self._поставить()
        состояние = self.rt._libs()
        self.assertFalse(состояние["active"])
        self.assertEqual(состояние["error"], "")
        качать.assert_not_called()
        self.assertFalse(any(вид == "voices_failed"
                             for вид, _ in self.rt.события))

    def test_само_упало_тоже_ошибка(self):
        with mock.patch.object(voices_install, "install",
                               side_effect=RuntimeError("диск отвалился")), \
                mock.patch.object(self.rt, "voices_download"):
            self._поставить()
        self.assertIn("диск отвалился", self.rt._libs()["error"])

    def test_повтор_во_время_установки_отказ(self):
        # Второе нажатие не должно запустить вторую установку поверх первой:
        # два uv, качающих в одну папку, кончились бы ошибкой.
        with self.rt._замок_библиотек():
            self.rt._libs_state["active"] = True
        with mock.patch.object(self.rt, "_ставить_библиотеки") as фон:
            ответ = self.rt.voices_libs_start(["higgs"])
        self.assertFalse(ответ["ok"])
        self.assertIn("уже ставятся", ответ["error"])
        фон.assert_not_called()

    def test_отмена_ставит_событие(self):
        стоп = threading.Event()
        with self.rt._замок_библиотек():
            self.rt._libs_stop = стоп
        self.assertTrue(self.rt.voices_libs_cancel()["ok"])
        self.assertTrue(стоп.is_set())

    def test_запуск_говорит_про_пользу(self):
        # Ставим то, что выбрали, и поток поднимается (сам `install` —
        # подменой, чтобы uv не запускался).
        with mock.patch.object(self.rt, "_ставить_библиотеки") as фон:
            ответ = self.rt.voices_libs_start(["espeech"])
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(фон.call_args[0][1], ["espeech"])

    def test_состояние_видно_в_статусе_пульта(self):
        self.rt._libs_state.update({"active": True, "step": 1, "steps": 2,
                                    "title": "torch с CUDA", "downloaded": 42})
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch.object(voices_install, "check",
                                  return_value={"ok": True, "cuda_index": "cu130"}), \
                mock.patch.object(voices_install, "weights_installed",
                                  return_value={"espeech": False, "higgs": False}):
            статус = self.rt.voices_status()
        ход = статус["libs"]
        self.assertTrue(ход["active"])
        self.assertEqual(ход["title"], "torch с CUDA")
        self.assertEqual(ход["downloaded"], 42)


class TorchВПроцессеTests(unittest.TestCase):
    """torch в памяти: флаг и перезапуск пульта «без голоса», uv не трогаем."""

    def setUp(self):
        self.rt = _пульт()
        self.папка = Path(tempfile.mkdtemp(prefix="truba-libs-restart-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.object(voices_install, "PENDING_PATH",
                                    self.папка / "voices_install_pending.json")
        подмена.start()
        self.addCleanup(подмена.stop)
        сохранения = mock.patch("core.settings.save_voices_wanted",
                                side_effect=lambda модели: list(модели))
        сохранения.start()
        self.addCleanup(сохранения.stop)

    def test_флаг_и_перезапуск_без_запуска_uv(self):
        self.rt.update_restart = lambda: {"ok": True, "restart": True}
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch.object(voices_install, "uv_есть", return_value=True), \
                mock.patch.object(voices_install, "torch_в_процессе",
                                  return_value=True), \
                mock.patch.object(voices_install, "install") as ставим, \
                mock.patch.object(self.rt, "voices_libs_start") as фон:
            ответ = self.rt.voices_install(["higgs"])
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(ответ["restart"], "пульт перезапускается, а не падает")
        ставим.assert_not_called()
        фон.assert_not_called()
        self.assertEqual(voices_install.read_pending()["models"], ["higgs"])

    def test_не_смогли_перезапустить_флаг_убираем(self):
        # Иначе пульт после следующего запуска снова попытался бы ставить то,
        # что уже не просили.
        self.rt.update_restart = lambda: {"ok": False, "error": "перезапуск не получился"}
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch.object(voices_install, "uv_есть", return_value=True), \
                mock.patch.object(voices_install, "torch_в_процессе",
                                  return_value=True), \
                mock.patch.object(self.rt, "voices_libs_start"):
            ответ = self.rt.voices_install(["higgs"])
        self.assertFalse(ответ["ok"])
        self.assertEqual(voices_install.read_pending(), {})

    def test_после_перезапуска_установка_идёт_сама(self):
        voices_install.write_pending(["higgs"])
        with mock.patch.object(voices_install, "uv_есть", return_value=True), \
                mock.patch.object(self.rt, "voices_libs_start",
                                  return_value={"ok": True, "started": True}) as ставим:
            начали = self.rt.voices_libs_startup()
        self.assertTrue(начали, "пульт должен сказать, что голос в этот раз не включать")
        ставим.assert_called_once_with(["higgs"])
        self.assertEqual(voices_install.read_pending(), {},
                         "отработавший флаг убираем, иначе поставим ещё раз")

    def test_без_флага_ничего_не_происходит(self):
        with mock.patch.object(voices_install, "uv_есть", return_value=True), \
                mock.patch.object(self.rt, "voices_libs_start") as ставим:
            self.assertFalse(self.rt.voices_libs_startup())
        ставим.assert_not_called()

    def test_окно_не_включает_голос_когда_ставит_библиотеки(self):
        # Ключевое место решения: с флагом голос не поднимается, иначе torch
        # снова окажется в памяти и установка снова не пойдёт.
        скрипт = (Path(__file__).resolve().parents[1]
                  / "ui" / "window.py").read_text(encoding="utf-8")
        кусок = скрипт.split("ставим_библиотеки = среда.voices_libs_startup()")[1]
        self.assertIn("if not ставим_библиотеки:", кусок)
        self.assertIn("threading.Thread(target=среда.voice_autostart", кусок)
        self.assertLess(кусок.index("if not ставим_библиотеки:"),
                        кусок.index("voice_autostart"),
                        "голос включается только когда библиотеки не ставятся")

    def test_после_установки_silero_включается_сам(self):
        # Пульт поднялся «без голоса», выбран обычный Silero: после установки
        # он включается как обычно, иначе голос молчал бы до перезапуска.
        import config
        self.rt.voice_autostart = mock.Mock()
        with mock.patch.object(voices_install, "install",
                               return_value={"ok": True, "restart": False}), \
                mock.patch.object(self.rt, "voices_download"), \
                mock.patch.object(config, "TTS_ENGINE", "silero"):
            self.rt._ставить_библиотеки(threading.Event(), [])
        self.rt.voice_autostart.assert_called_once()

    def test_после_установки_качественного_голоса_автозапуск_не_нужен(self):
        # Движок Higgs: голос и так поднимется сам после скачивания модели
        # (`_голос_после_скачивания`), второй раз трогать его нельзя.
        import config
        self.rt.voice_autostart = mock.Mock()
        with mock.patch.object(voices_install, "install",
                               return_value={"ok": True, "restart": False}), \
                mock.patch.object(self.rt, "voices_download"), \
                mock.patch.object(config, "TTS_ENGINE", "higgs"):
            self.rt._ставить_библиотеки(threading.Event(), [])
        self.rt.voice_autostart.assert_not_called()


class ОтменаПроцессаTests(unittest.TestCase):
    """Отмена гасит установку деревом — вместе с потомками uv.

    `.venv\\Scripts\\python.exe` и сам uv — это обёртки, и `kill()` гасит только
    их (`coordination/ГРАБЛИ.md`), поэтому гасить надо деревом.
    """

    def test_отмена_останавливает_и_гасит_процесс(self):
        живой = threading.Event()

        class Процесс:
            """Живой процесс: `poll` возвращает `None`, пока его не погасили."""

            def __init__(self):
                self.returncode = None
                self.погашен = False

            def poll(self):
                return 0 if self.погашен else None

        процесс = Процесс()
        погашены = []

        def popen(*args, **kwargs):
            живой.set()
            return процесс

        def ждать_остановки(stop, сколько):
            return живой.wait(1.0) and stop is not None and stop.is_set()

        def гасить(п):
            погашены.append(п)
            п.погашен = True
            п.returncode = 1

        журнал = Path(tempfile.gettempdir()) / "voices_install.log"
        with mock.patch.object(voices_install.subprocess, "Popen", popen), \
                mock.patch.object(voice_models, "_ждём_остановки", ждать_остановки), \
                mock.patch.object(voice_models, "kill_tree", гасить), \
                mock.patch.object(voices_install, "LOG_PATH", журнал):
            стоп = threading.Event()
            threading.Timer(0.05, стоп.set).start()
            итог = voices_install._запустить(["uv", "pip", "install"],
                                             Path(tempfile.gettempdir()),
                                             журнал, stop=стоп)
        self.assertTrue(итог.get("cancelled"), итог)
        self.assertEqual(погашены, [процесс], "процесс надо погасить деревом")
        self.addCleanup(журнал.unlink, True)


class ЗапретTorchTests(unittest.TestCase):
    """Пока uv меняет torch на диске, пульт не загружает его в память.

    Голос, «Послушать голос» или проверка звука посреди установки загрузили бы
    старый torch: Windows заперла бы его файлы, uv упал бы на полпути и мог
    оставить сломанным даже обычный Silero.
    """

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-libs-guard-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        for цель, значение in (
                ("check", lambda root=None: {"ok": True, "cuda_index": "cu130"}),
                ("команды", lambda root=None, cuda_index="cu130": [["uv", "1"], ["uv", "2"]]),
                ("cache_bytes", lambda root=None: 0),
                ("LOG_PATH", self.папка / "voices_install.log")):
            подмена = mock.patch.object(voices_install, цель, значение)
            подмена.start()
            self.addCleanup(подмена.stop)

    def _установка(self, итог_шага: dict) -> tuple[dict, list]:
        """Установка, во время шага которой кто-то пробует загрузить torch."""
        попытки = []

        def запустить(команда, root, журнал, **_):
            # Как будто голос включили посреди установки: torch ещё не в
            # памяти, и его импорт идёт к файлам на диске.
            with mock.patch.dict(sys.modules):
                for имя in [и for и in sys.modules if и.split(".")[0] in ("torch", "torchaudio")]:
                    del sys.modules[имя]
                try:
                    importlib.import_module("torch")
                    попытки.append("загрузился")
                except ImportError as exc:
                    попытки.append(str(exc))
            return dict(итог_шага)

        with mock.patch.object(voices_install, "_запустить", запустить):
            итог = voices_install.install(root=self.папка)
        return итог, попытки

    def _запрет_снят(self):
        self.assertFalse(any(isinstance(f, voices_install._ЗапретTorch)
                             for f in sys.meta_path),
                         "после установки torch снова можно грузить")

    def test_во_время_установки_torch_не_грузится(self):
        итог, попытки = self._установка({"ok": True})
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(len(попытки), 2, "оба шага")
        for текст in попытки:
            self.assertIn("ставлю библиотеки голосов", текст)
        self._запрет_снят()

    def test_запрет_снят_и_после_ошибки(self):
        итог, попытки = self._установка({"ok": False, "returncode": 1})
        self.assertFalse(итог["ok"])
        self._запрет_снят()

    def test_запрет_снят_и_после_отмены(self):
        итог, _ = self._установка({"ok": False, "cancelled": True})
        self.assertTrue(итог.get("cancelled"))
        self._запрет_снят()

    def test_голос_не_включается_во_время_установки(self):
        rt = _пульт()
        rt._libs_state["active"] = True
        rt.voice = NS(running=False, start=mock.Mock())
        with self.assertRaisesRegex(RuntimeError, "Ставлю библиотеки голосов"):
            rt.voice_toggle()
        rt.voice.start.assert_not_called()


class МаршрутОтменыTests(unittest.TestCase):
    """`POST /api/voices/libs/cancel` — только со своего компьютера."""

    def test_маршрут_есть_и_он_про_секрет(self):
        скрипт = (Path(__file__).resolve().parents[1]
                  / "core" / "phone.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/voices/libs/cancel")', скрипт)
        блок = скрипт.split('@app.post("/api/voices/libs/cancel")')[1].split("\n        @app")[0]
        self.assertIn("_local_secret(request)", блок)
        self.assertIn("rt.voices_libs_cancel", блок)


if __name__ == "__main__":
    unittest.main()
