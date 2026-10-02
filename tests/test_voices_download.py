"""Скачивание моделей качественных голосов: менеджер в пульте и его API.

02.10: кнопка «Установить качественные голоса» ставила только библиотеки, а
модели потом тянулись молча при включении голоса — 9,3 ГБ без спроса. Теперь
модели качает пульт, по списку от хозяина, по очереди и с отменой; голос в
сеть не ходит вовсе. Здесь проверяется именно менеджер: одна загрузка за раз,
уже скачанное пропускается, повтор — отказ, мало места — отказ с цифрами.

Ни сети, ни процессов, ни настоящих моделей здесь нет — всё подменено.
"""

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

from core import hardware, settings, voice_models, voices_install
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ПОРТ: list = []


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _пульт(сервер=None) -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер, голос и мозг."""
    среда = object.__new__(WebRuntime)
    среда.server = сервер
    среда._lock = threading.Lock()
    среда._download_lock = threading.Lock()
    среда._download_state = {
        "active": False, "model": "", "title": "", "done": 0, "total": 0,
        "queue": [], "error": "", "finished": []}
    среда._download_stop = None
    среда.события = []
    среда._remember = lambda kind, payload: среда.события.append((kind, payload))
    среда.close_pult = lambda: среда.события.append(("closed", None))
    среда.voice = NS(running=False, _listener=None, _speaker_idle=lambda: True,
                     in_conversation=False)
    return среда


def _скачанные(какие=()):
    """Подмена «что уже лежит на диске» — без обращения к кешу."""
    return mock.patch.object(voice_models, "installed",
                             side_effect=lambda имя: имя in какие)


def _место(свободно):
    """Замер свободного места подменён: настоящий диск трогать нельзя."""
    return mock.patch.object(voice_models.shutil, "disk_usage",
                             return_value=NS(free=свободно))


def _без_потока():
    """Фоновый поток скачивания не поднимаем.

    `voices_download` только запускает поток, а проход по очереди проверяется
    отдельно и синхронно. Без этой подмены тест зависел бы от того, успел ли
    поток отработать, и модель качалась бы дважды.
    """
    return mock.patch("ui.web_runtime.threading.Thread")


class МенеджерTests(unittest.TestCase):
    """Пульт качает выбранное, по очереди, и честно отказывает."""

    def setUp(self):
        self.rt = _пульт()
        self.виден = []
        # Настройки и кеш подменены: тест не должен ни писать в settings.json
        # этого компьютера, ни смотреть в настоящий кеш Hugging Face.
        for подмена in (mock.patch("core.settings.save_settings"),
                        mock.patch("core.settings.voices_wanted", return_value=[])):
            подмена.start()
            self.addCleanup(подмена.stop)

    def _загрузчик(self, **итог):
        """Подмена загрузчика: запоминает вызовы, сеть не трогает.

        Вернувшийся подменник открывается как контекстный менеджер — так видно
        в самом тесте, что проверяется подменой, а не настоящей загрузкой.
        """
        виден = self.виден

        def качать(имя, стоп, показать=None, poll=3.0):
            виден.append(имя)
            for байт in итог.get("прогресс", ()):
                показать(байт)
            if "ошибка" in итог:
                raise RuntimeError(итог["ошибка"])
            return итог.get("вернулось", True)

        return mock.patch.object(voice_models, "download", side_effect=качать)

    def _качать(self, модели, **итог):
        """`voices_download` + фоновый проход до конца, без живого потока.

        Поток ждать не надо: он зовёт тот же `_качать_модели`, что и здесь, а
        ожидание в тесте сделало бы его зависимым от таймингов.
        """
        with self._загрузчик(**итог), _без_потока():
            ответ = self.rt.voices_download(модели)
            очередь = ответ.get("queue")
            if очередь:
                self.rt._качать_модели(очередь, self.rt._download_stop)
        return ответ

    def test_качает_по_очереди_и_пропускает_скачанное(self):
        # Хозяин выбрал обе, ESpeech уже на диске — второй раз его не трогаем.
        with mock.patch.object(voice_models, "installed",
                               side_effect=lambda имя: имя == "espeech"), \
                _место(50e9), _без_потока(), self._загрузчик():
            ответ = self.rt.voices_download(["espeech", "higgs"])
            self.rt._качать_модели(ответ["queue"], self.rt._download_stop)
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self.виден, ["higgs"])

    def test_обе_модели_идут_по_очереди(self):
        with _скачанные(), _место(50e9):
            ответ = self._качать(["espeech", "higgs"])
        self.assertEqual(ответ["queue"], ["espeech", "higgs"])
        self.assertEqual(self.виден, ["espeech", "higgs"])
        состояние = self.rt._download()
        self.assertEqual(состояние["finished"], ["espeech", "higgs"])
        self.assertFalse(состояние["active"])

    def test_уже_скачанное_вообще_не_запускает_загрузку(self):
        with _скачанные(["higgs"]), _место(50e9), _без_потока(), \
                mock.patch.object(voice_models, "download",
                                  side_effect=AssertionError("нечего качать")):
            ответ = self.rt.voices_download(["higgs"])
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["already"], ["higgs"])
        self.assertFalse(self.rt._download()["active"])
        self.assertEqual(self.виден, [])

    def test_повтор_во_загрузке_отказ(self):
        # Две быстрые кнопки подряд: вторая не должна ни гасить первую, ни
        # начинать вторую загрузку. Отказ — «уже качаю», а не «погоди».
        отпустить = threading.Event()
        начали = threading.Event()

        def качать(имя, стоп, показать=None, poll=3.0):
            self.виден.append(имя)
            начали.set()
            # Ждём, пока тест проверит повторное нажатие и нажмёт «отмена».
            отпустить.wait(10)
            return not стоп.is_set()

        with _скачанные(), _место(50e9), \
                mock.patch.object(voice_models, "download", side_effect=качать):
            первый = self.rt.voices_download(["higgs"])
            self.assertTrue(первый["ok"], первый)
            self.assertTrue(начали.wait(10), "загрузка не пошла")
            второй = self.rt.voices_download(["espeech"])
            self.assertFalse(второй["ok"])
            self.assertIn("уже качаю", второй["error"])
            # Первая не тронута: её модель на месте, второй не началась.
            self.assertEqual(self.виден, ["higgs"])
            self.assertTrue(self.rt._download()["active"])
            self.rt.voices_download_cancel()
            отпустить.set()
            for _ in range(100):
                if not self.rt._download()["active"]:
                    break
                time.sleep(0.05)
        self.assertFalse(self.rt._download()["active"])
        self.assertEqual(self.виден, ["higgs"])

    def test_мало_места_отказ_с_цифрами(self):
        # Higgs 9,32 ГБ плюс запас 1 ГБ — а свободно 5 ГБ. Обрыв в середине
        # 9 ГБ хозяину понравился бы меньше, чем отказ с цифрами.
        with _скачанные(), _место(5e9), self._загрузчик() as загрузка, \
                _без_потока():
            ответ = self.rt.voices_download(["higgs"])
        self.assertFalse(ответ["ok"])
        self.assertIn("мало места", ответ["error"])
        self.assertIn("5,0 ГБ", ответ["error"])
        self.assertIn("10,3 ГБ", ответ["error"])
        загрузка.assert_not_called()
        self.assertEqual(self.виден, [])

    def test_в_журнал_идёт_каждый_пятый_процент(self):
        # Панель спрашивает состояние сама, а журнал не должен забиваться
        # сотнями одинаковых строк.
        всего = voice_models.size_bytes("higgs")
        with _скачанные(), _место(50e9):
            self._качать(["higgs"], прогресс=[int(всего * д / 100)
                                              for д in (1, 2, 6, 7, 51, 52)])
        строки = [текст for вид, текст in self.rt.события
                  if вид == "voices_download" and "%" in str(текст)]
        self.assertEqual([int(т.split("(")[1].split("%")[0]) for т in строки],
                         [1, 6, 51], строки)
        self.assertIn("9,3 ГБ", строки[0])

    def test_ошибка_сети_честным_текстом_и_очередь_останавливается(self):
        with _скачанные(), _место(50e9):
            self._качать(["higgs", "espeech"],
                         ошибка="модель Higgs не скачалась: сеть отвалилась")
        # Упало первое — второе не начинаем: сначала хозяин узнает про сбой.
        self.assertEqual(self.виден, ["higgs"])
        состояние = self.rt._download()
        self.assertIn("сеть", состояние["error"])
        self.assertFalse(состояние["active"])
        self.assertTrue(any(вид == "voices_download_failed"
                            for вид, _ in self.rt.события))

    def test_отмена_останавливает_очередь_без_ошибки(self):
        with _скачанные(), _место(50e9):
            self._качать(["higgs", "espeech"], вернулось=False)
        self.assertEqual(self.виден, ["higgs"])
        состояние = self.rt._download()
        self.assertFalse(состояние["active"])
        # Отмена — не сбой: хозяин сам остановил, ругаться незачем.
        self.assertEqual(состояние["error"], "")
        self.assertTrue(any("остановил" in str(текст)
                            for вид, текст in self.rt.события
                            if вид == "voices_download"))

    def test_состояние_видно_в_статусе(self):
        self.rt._download_state.update({"active": True, "model": "higgs",
                                        "title": "Higgs", "done": 500,
                                        "total": 1000, "queue": ["espeech"]})
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch.object(voices_install, "check", return_value={"ok": True}), \
                mock.patch.object(voices_install, "weights_installed",
                                  return_value={"espeech": False, "higgs": False}), \
                mock.patch("core.settings.voices_wanted", return_value=["higgs"]):
            статус = self.rt.voices_status()
        self.assertEqual(статус["wanted"], ["higgs"])
        скачивание = статус["download"]
        self.assertTrue(скачивание["active"])
        self.assertEqual(скачивание["title"], "Higgs")
        self.assertEqual(скачивание["done"], 500)
        self.assertEqual(скачивание["total"], 1000)
        self.assertEqual(скачивание["queue"], ["espeech"])

    def test_пустое_состояние_не_ломает_страницу(self):
        # Ключи те же, что при загрузке: иначе пульт ловит `undefined`.
        self.assertEqual(sorted(self.rt._download()),
                         ["active", "done", "error", "finished", "model",
                          "queue", "title", "total"])

    def test_без_выбора_отказ_и_ничего_не_качает(self):
        with _скачанные(), _место(50e9), self._загрузчик() as загрузка, \
                _без_потока():
            ответ = self.rt.voices_download([])
        self.assertFalse(ответ["ok"])
        self.assertIn("выбери модель", ответ["error"])
        загрузка.assert_not_called()

    def test_журнал_говорит_о_ходе_словами_хозяину(self):
        # В журнале хозяин читает строки, а не коды: «качественные голоса: …».
        строки = WebRuntime._log_messages
        self.assertEqual(строки("voices_download", "Higgs: 1,0 из ~9,3 ГБ (10 %)"),
                         ["качественные голоса: Higgs: 1,0 из ~9,3 ГБ (10 %)"])
        self.assertIn("не скачалась", строки("voices_download_failed",
                                             {"error": "сеть"})[0])


class НастройкаTests(unittest.TestCase):
    """Выбор хозяина помнится и не превращается в мусор."""

    def setUp(self):
        папка = tempfile.TemporaryDirectory()
        self.addCleanup(папка.cleanup)
        self.файл = Path(папка.name) / "settings.json"
        подмена = mock.patch.object(settings, "SETTINGS_PATH", self.файл)
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_по_умолчанию_не_выбрано_ничего(self):
        self.assertEqual(settings.voices_wanted(), [])

    def test_выбор_сохраняется_и_читается(self):
        settings.save_voices_wanted(["higgs"])
        self.assertEqual(json.loads(self.файл.read_text(encoding="utf-8"))
                         ["voices_wanted"], ["higgs"])
        self.assertEqual(settings.voices_wanted(), ["higgs"])

    def test_мусор_и_повторы_молча_исчезают(self):
        # Правка руками или старый пульт не должны заставить качать то, чего
        # нет, и не должны сломать запуск пульта.
        self.assertEqual(settings.validate_voices_wanted(
            ["higgs", "higgs", "что-то", 5, None, "espeech"]), ["higgs", "espeech"])
        self.assertEqual(settings.validate_voices_wanted("higgs"), [])
        self.assertEqual(settings.validate_voices_wanted(None), [])
        self.файл.write_text(json.dumps({"voices_wanted": ["ерунда"]}),
                             encoding="utf-8")
        self.assertEqual(settings.voices_wanted(), [])

    def test_имена_моделей_совпадают_с_загрузчиком(self):
        # Иначе пульт показал бы модели, которых скачать нечем.
        self.assertEqual(sorted(settings.VOICES_WANTED_MODELS),
                         sorted(voice_models.MODELS))

    def test_размеры_моделей_правдивые(self):
        # «4,3 ГБ» у Higgs — это видеопамять после сжатия, а не файл: на
        # диске модель весит 9,3 ГБ, ESpeech — 2,7 ГБ.
        self.assertEqual(voice_models.size_text("higgs"), "9,3 ГБ")
        self.assertEqual(voice_models.size_text("espeech"), "2,7 ГБ")


class ApiTests(unittest.TestCase):
    """Маршруты: скачать, отменить, 400 на чужое имя."""

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
        ПОРТ.append(cls.server.port)

    def setUp(self):
        self.server.runtime = _пульт(self.server)
        self.client = TestClient(self.server._app,
                                 base_url=f"http://127.0.0.1:{ПОРТ[0]}")
        self.вдали = TestClient(self.server._app,
                                base_url=f"http://127.0.0.1:{ПОРТ[0]}",
                                client=("192.168.1.50", 5555))

    def test_скачивание_запускается(self):
        self.server.runtime.voices_download = lambda models: {"ok": True,
                                                               "queue": models}
        тело = self.client.post("/api/voices/download",
                                json={"models": ["higgs"]}).json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["queue"], ["higgs"])

    def test_чужое_имя_модели_400(self):
        # Неизвестная модель — ошибка запроса (400), а не «отказ» (409):
        # прислали не то, что есть.
        ответ = self.client.post("/api/voices/download", json={"models": ["vox"]})
        self.assertEqual(ответ.status_code, 400)
        self.assertIn("Higgs", ответ.json()["error"])

    def test_пустой_список_моделей_400(self):
        self.assertEqual(self.client.post("/api/voices/download",
                                          json={"models": []}).status_code, 400)

    def test_отказ_менеджера_409(self):
        # «Уже качаю» и «мало места» — не ошибка запроса, а отказ: 409.
        self.server.runtime.voices_download = lambda models: {
            "ok": False, "error": "уже качаю — дождись окончания"}
        self.assertEqual(self.client.post("/api/voices/download",
                                          json={"models": ["higgs"]}).status_code, 409)

    def test_со_телефона_запрещено(self):
        # Как у установки: скачивание 9 ГБ — только с этого компьютера.
        self.assertEqual(self.вдали.post("/api/voices/download",
                                         json={"models": ["higgs"]}).status_code, 403)
        self.assertEqual(self.вдали.post("/api/voices/download/cancel").status_code, 403)

    def test_отмена_доходит_до_менеджера(self):
        стоп = threading.Event()
        self.server.runtime._download_stop = стоп
        тело = self.client.post("/api/voices/download/cancel").json()
        self.assertTrue(тело["ok"], тело)
        self.assertTrue(стоп.is_set())

    def test_установка_принимает_список_моделей(self):
        # Старый пульт зовёт без тела, новый — со списком: оба должны идти.
        виден = []
        self.server.runtime.voices_install = lambda models=None: (
            виден.append(models) or {"ok": True})
        self.assertTrue(self.client.post("/api/voices/install",
                                         json={"models": ["espeech"]}).json()["ok"])
        self.assertEqual(виден, [["espeech"]])
        self.assertTrue(self.client.post("/api/voices/install").json()["ok"])
        self.assertEqual(виден[-1], None)

    def test_установка_с_чужим_именем_400(self):
        self.assertEqual(self.client.post("/api/voices/install",
                                          json={"models": ["vox"]}).status_code, 400)

    def test_статус_отдаёт_и_выбор_и_ход_скачивания(self):
        # Панель спрашивает состояние по таймеру, а не по событиям.
        self.server.runtime._download_state.update(
            {"active": True, "model": "higgs", "title": "Higgs", "done": 10,
             "total": 100, "queue": []})
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch.object(voices_install, "check", return_value={"ok": True}), \
                mock.patch.object(voices_install, "weights_installed",
                                  return_value={"espeech": True, "higgs": False}), \
                mock.patch("core.settings.voices_wanted", return_value=["higgs"]):
            тело = self.client.get("/api/voices/status").json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["wanted"], ["higgs"])
        self.assertEqual(тело["download"]["title"], "Higgs")
        self.assertEqual(тело["download"]["done"], 10)


if __name__ == "__main__":
    unittest.main()


class КнопкаУстановитьTests(unittest.TestCase):
    """«Установить качественные голоса» помнит выбор и не мечет пультом."""

    def setUp(self):
        self.rt = _пульт()
        self.сохранено = []
        подмена = mock.patch("core.settings.save_voices_wanted",
                             side_effect=lambda модели: self.сохранено.append(
                                 list(модели)) or list(модели))
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_с_библиотеками_качает_в_пульте_и_не_закрывает_его(self):
        # Хозяин нажал «установить» — он ждёт голоса, а не второе окно.
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch.object(self.rt, "voices_download",
                                  return_value={"ok": True, "queue": ["higgs"]}) as качать, \
                mock.patch("core.voices_install.launch_external") as установщик, \
                mock.patch("threading.Timer") as таймер:
            ответ = self.rt.voices_install(["higgs"])
        self.assertTrue(ответ["ok"], ответ)
        качать.assert_called_once_with(["higgs"])
        установщик.assert_not_called()
        таймер.assert_not_called()
        self.assertEqual(self.сохранено, [["higgs"]])

    def test_без_библиотек_и_без_uv_открывает_установщик_и_закрывает_пульт(self):
        # Ставить нечем: в папке Трубы нет uv. Остаётся прежний путь — отдельное
        # окно установщика, как было до 02.10.
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch.object(self.rt, "voices_download") as качать, \
                mock.patch.object(voices_install, "uv_есть", return_value=False), \
                mock.patch("core.voices_install.launch_external",
                           return_value={"ok": True, "close": True}) as установщик, \
                mock.patch("threading.Timer") as таймер:
            ответ = self.rt.voices_install(["higgs"])
        self.assertTrue(ответ["ok"], ответ)
        установщик.assert_called_once()
        качать.assert_not_called()
        таймер.assert_called_once()
        self.assertEqual(self.сохранено, [["higgs"]])

    def test_без_библиотек_но_с_uv_пульт_не_закрывается(self):
        # uv на месте и torch в процессе ещё не загружен — библиотеки ставятся
        # прямо здесь, в фоне: хозяин нажал кнопку и ждёт голоса, а не второе
        # окно (02.10).
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch.object(self.rt, "voices_download") as качать, \
                mock.patch.object(voices_install, "uv_есть", return_value=True), \
                mock.patch.object(voices_install, "torch_в_процессе",
                                  return_value=False), \
                mock.patch("core.voices_install.launch_external") as установщик, \
                mock.patch.object(self.rt, "voices_libs_start",
                                  return_value={"ok": True,
                                                "started": True}) as ставим:
            ответ = self.rt.voices_install(["higgs"])
        self.assertTrue(ответ["ok"], ответ)
        ставим.assert_called_once_with(["higgs"])
        установщик.assert_not_called()
        качать.assert_not_called()

    def test_без_выбора_отказ_и_ничего_не_запускаем(self):
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch.object(self.rt, "voices_download") as качать, \
                mock.patch("core.voices_install.launch_external") as установщик:
            ответ = self.rt.voices_install([])
        self.assertFalse(ответ["ok"])
        self.assertIn("выбери модель", ответ["error"])
        качать.assert_not_called()
        установщик.assert_not_called()

    def test_ошибка_скачивания_идёт_в_журнал_словами(self):
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch.object(self.rt, "voices_download",
                                  return_value={"ok": False,
                                                "error": "мало места на диске"}):
            self.rt.voices_install(["higgs"])
        self.assertTrue(any(вид == "voices_failed" and "мало места" in текст["error"]
                            for вид, текст in self.rt.события
                            if isinstance(текст, dict)))

    def test_при_запуске_пульта_докачивается_выбранное(self):
        # Хозяин выбрал Higgs, закрыл пульт на середине. Это продолжение его
        # выбора, а не самовольство: кнопка обещала, что выбранное скачается.
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch("core.settings.voices_wanted", return_value=["higgs"]), \
                mock.patch.object(voice_models, "needed",
                                  return_value=["higgs"]) as нужно, \
                mock.patch.object(self.rt, "voices_download") as качать:
            self.rt.voices_wanted_startup()
        нужно.assert_called_once_with(["higgs"])
        качать.assert_called_once_with(["higgs"])

    def test_при_запуске_пульта_без_выбора_ничего_не_качает(self):
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch("core.settings.voices_wanted", return_value=[]), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self.rt.voices_wanted_startup()
        качать.assert_not_called()

    def test_при_запуске_пульта_без_библиотек_ничего_не_качает(self):
        # Без библиотек модель всё равно не зазвучит; об этом сказала кнопка.
        with mock.patch.object(hardware, "voices_installed", return_value=False), \
                mock.patch("core.settings.voices_wanted", return_value=["higgs"]), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self.rt.voices_wanted_startup()
        качать.assert_not_called()

    def test_уже_скачанное_при_старте_не_качается_снова(self):
        with mock.patch.object(hardware, "voices_installed", return_value=True), \
                mock.patch("core.settings.voices_wanted", return_value=["higgs"]), \
                mock.patch.object(voice_models, "needed", return_value=[]), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self.rt.voices_wanted_startup()
        качать.assert_not_called()

    def test_ошибка_при_старте_не_роняет_пульт(self):
        with mock.patch.object(hardware, "voices_installed",
                               side_effect=OSError("сломан settings.json")), \
                mock.patch.object(self.rt, "voices_download") as качать:
            self.rt.voices_wanted_startup()  # не поднялось
        качать.assert_not_called()
        self.assertTrue(any(вид == "voices_download_failed"
                            for вид, _ in self.rt.события))
