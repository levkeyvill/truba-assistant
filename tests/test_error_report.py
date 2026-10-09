"""Журнал и отчёт о проблеме: копия журнала, архив без разговоров, запись падений.

Все пути подменены на временную папку: живые `data/` и «Загрузки» не
трогаются, проводник не открывается. Строки журнала ниже выдуманы для теста.
"""

import logging
import socket
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from core import error_report, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ROOT = Path(__file__).resolve().parent.parent
ДОМ = str(Path.home())

# Выдуманный журнал: речь со словом «ошибка», сбой, веха и путь с домом.
ЖУРНАЛ = "\n".join([
    "[10:00:00] ——— 01.01.2030 ———",
    "[10:00:01] веб-пульт запущен",
    "[10:00:05] услышала: тестовая фраза про ошибку в коде (1.0 с речи)",
    "[10:00:06] ответила: тестовый ответ, ошибка бывает у всех",
    "[10:00:07] пропустил [не позвали по имени]: тестовая ошибка в разговоре",
    "[10:00:08] голос похож на 0.50 (2.0 с речи)",
    "[10:00:09] ошибка голоса: APITimeoutError: Request timed out.",
    f"[10:00:10] голос не включился сам: нет файла {ДОМ}\\модель.bin",
    "[10:00:11] напоминание сработало: тестовое напоминание, не вышло",
    "[10:00:12] токены: 100 на входе (0 из кеша), 5 на выходе",
]) + "\n"


class ПапкаTests(unittest.TestCase):
    """Общая подмена путей на временную папку."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.загрузки = self.tmp / "Загрузки"
        self.загрузки.mkdir()
        self.журнал = self.tmp / "session.log"
        self.журнал.write_text(ЖУРНАЛ, encoding="utf-8")
        for имя, значение in (("SESSION_LOG", self.журнал),
                              ("ERRORS_LOG", self.tmp / "errors.log"),
                              ("CRASH_LOG", self.tmp / "crash.log"),
                              ("INSTALL_LOGS", (self.tmp / "install.log",))):
            patcher = mock.patch.object(error_report, имя, значение)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(error_report, "_загрузки", return_value=self.загрузки)
        patcher.start()
        self.addCleanup(patcher.stop)


class ЖурналTests(ПапкаTests):
    def test_сбои_без_речи(self):
        текст = error_report.session_errors()
        self.assertIn("APITimeoutError", текст)
        self.assertIn("веб-пульт запущен", текст)
        self.assertIn("голос не включился сам", текст)
        # Речь и личное не берутся, даже со словами «ошибка» и «не вышло».
        self.assertNotIn("тестовая фраза", текст)
        self.assertNotIn("тестовый ответ", текст)
        self.assertNotIn("тестовая ошибка в разговоре", текст)
        self.assertNotIn("тестовое напоминание", текст)
        self.assertNotIn("токены", текст)

    def test_папка_пользователя_скрыта(self):
        текст = error_report.session_errors()
        self.assertNotIn(ДОМ, текст)
        self.assertIn("%USERPROFILE%", текст)

    def test_копия_журнала_в_загрузках(self):
        путь = error_report.save_journal()
        self.assertEqual(путь.parent, self.загрузки)
        self.assertEqual(путь.read_text(encoding="utf-8"), ЖУРНАЛ)

    def test_журнала_нет(self):
        self.журнал.unlink()
        with self.assertRaises(FileNotFoundError):
            error_report.save_journal()

    def test_архив_для_автора(self):
        error_report.write_error("тестовая ошибка", f"Traceback\n  File \"{ДОМ}\\x.py\"")
        (self.tmp / "install.log").write_text("установка: тест", encoding="utf-8")
        путь = error_report.build_report()
        self.assertEqual(путь.parent, self.загрузки)
        with zipfile.ZipFile(путь) as архив:
            имена = set(архив.namelist())
            self.assertEqual(имена, {"сведения.txt", "журнал-сбои.txt",
                                     "errors.log", "install.log"})
            всё = " ".join(архив.read(имя).decode("utf-8") for имя in имена)
        self.assertIn("Труба", всё)
        self.assertIn("тестовая ошибка", всё)
        self.assertNotIn("тестовая фраза", всё)
        self.assertNotIn(ДОМ, всё)

    def test_большой_файл_ошибок_обрезается(self):
        большой = self.tmp / "errors.log"
        большой.write_bytes(b"x" * (error_report.MAX_BYTES + 10))
        error_report.write_error("ещё одна")
        self.assertLess(большой.stat().st_size, error_report.MAX_BYTES)


class ЗаписьПаденийTests(ПапкаTests):
    def setUp(self):
        super().setUp()
        было = (sys.excepthook, threading.excepthook, list(logging.getLogger().handlers))

        def вернуть():
            import faulthandler

            faulthandler.disable()
            if error_report._crash_file is not None:
                error_report._crash_file.close()
                error_report._crash_file = None
            sys.excepthook, threading.excepthook = было[0], было[1]
            logging.getLogger().handlers[:] = было[2]

        self.addCleanup(вернуть)
        error_report.install()

    def test_ошибка_в_потоке_и_logging_в_файл(self):
        def упасть():
            raise RuntimeError("тестовый сбой потока")

        поток = threading.Thread(target=упасть, name="тест-поток")
        поток.start()
        поток.join()
        logging.getLogger("тест").warning("тестовое предупреждение")
        текст = (self.tmp / "errors.log").read_text(encoding="utf-8")
        self.assertIn("тестовый сбой потока", текст)
        self.assertIn("тест-поток", текст)
        self.assertIn("тестовое предупреждение", текст)

    def test_необработанная_ошибка_в_файл(self):
        try:
            raise ValueError("тестовая необработанная")
        except ValueError:
            with mock.patch("sys.stderr"):
                sys.excepthook(*sys.exc_info())
        текст = (self.tmp / "errors.log").read_text(encoding="utf-8")
        self.assertIn("тестовая необработанная", текст)

    def test_отметка_запуска_в_crash_log(self):
        self.assertIn("запуск", (self.tmp / "crash.log").read_text(encoding="utf-8"))


def _порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class АдресаTests(ПапкаTests):
    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_порт())
        cls.server.start()
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        super().setUp()
        среда = WebRuntime.__new__(WebRuntime)
        среда._log_path = self.журнал
        self.server.runtime = среда
        self.addCleanup(lambda: setattr(self.server, "runtime", None))
        patcher = mock.patch.object(error_report, "reveal")
        self.показано = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(self.server._app)
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_сохранить_журнал(self):
        ответ = self.client.post("/api/logs/save").json()
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue((self.загрузки / ответ["name"]).is_file())
        self.показано.assert_called_once()

    def test_архив_и_телеграм(self):
        ответ = self.client.post("/api/logs/report").json()
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(ответ["name"].endswith(".zip"))
        self.assertIn("t.me", ответ["telegram"])

    def test_открыть_папку(self):
        ответ = self.client.post("/api/logs/open").json()
        self.assertTrue(ответ["ok"], ответ)
        self.показано.assert_called_once_with(self.журнал)

    def test_пустой_журнал_404(self):
        self.журнал.unlink()
        ответ = self.client.post("/api/logs/save")
        self.assertEqual(ответ.status_code, 404)

    def test_только_с_этого_компьютера(self):
        for адрес in ("/api/logs/save", "/api/logs/report", "/api/logs/open"):
            with self.subTest(адрес=адрес):
                self.assertEqual(self.вдали.post(адрес).status_code, 403)
        self.показано.assert_not_called()


class ПультTests(unittest.TestCase):
    def test_кнопки_журнала_и_окно_перезапуска(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        # Окно пульта не скачивает по ссылке — ссылки «скачать» больше нет.
        self.assertNotIn("скачать.href = '/api/logs/download'", пульт)
        for адрес in ("/api/logs/save", "/api/logs/open", "/api/logs/report"):
            self.assertIn(f"'{адрес}'", пульт)
        self.assertIn("if (данные.restart) {\n      показатьПерезапуск(",
                      пульт.replace("\r\n", "\n"))
        self.assertIn("} else if (данные.close) {", пульт)

    def test_запись_падений_включается_при_запуске(self):
        окно = (ROOT / "ui" / "window.py").read_text(encoding="utf-8")
        self.assertIn("error_report.install()", окно)


if __name__ == "__main__":
    unittest.main()
