"""Следы прежних падений: свои снимки памяти и режим FTH у python.

Папка снимков, реестр и окно прав администратора подменены: живые
`%LOCALAPPDATA%\\CrashDumps` и реестр не трогаются. Снимки ниже — выдуманные
файлы с одним списком модулей, как его пишет Windows.
"""

import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from core import crash_traces, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ROOT = Path(__file__).resolve().parent.parent


def снимок(путь: Path, модули: list, хвост: int = 0) -> None:
    """Минимальный снимок: заголовок, каталог из одного потока, модули."""
    заголовок_размер, каталог_размер = 32, 12
    список_rva = заголовок_размер + каталог_размер
    имена_rva = список_rva + 4 + len(модули) * 108
    имена = b""
    записи = b""
    for модуль in модули:
        данные = модуль.encode("utf-16-le")
        запись = bytearray(108)
        struct.pack_into("<QI", запись, 0, 0x10000, 0x1000)
        struct.pack_into("<I", запись, 20, имена_rva + len(имена))
        записи += bytes(запись)
        имена += struct.pack("<I", len(данные)) + данные
    тело = (b"MDMP" + struct.pack("<IIIIIQ", 0xA793, 1, заголовок_размер, 0, 0, 0)
            + struct.pack("<III", 4, 4 + len(записи), список_rva)
            + struct.pack("<I", len(модули)) + записи + имена + b"\0" * хвост)
    путь.write_bytes(тело)


class СнимкиTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.папка = self.tmp / "CrashDumps"
        self.папка.mkdir()
        self.корень = self.tmp / "Труба"
        patcher = mock.patch.object(crash_traces, "dumps_dir", return_value=self.папка)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_список_модулей(self):
        модули = [r"C:\py\pythonw.exe", r"D:\Труба\.venv\Lib\x.pyd"]
        снимок(self.папка / "pythonw.exe.1.dmp", модули)
        self.assertEqual(crash_traces.dump_modules(self.папка / "pythonw.exe.1.dmp"), модули)

    def test_не_снимок(self):
        (self.папка / "pythonw.exe.2.dmp").write_bytes(b"not a dump")
        self.assertEqual(crash_traces.dump_modules(self.папка / "pythonw.exe.2.dmp"), [])

    def test_только_свои(self):
        свой = str(self.корень / ".venv" / "Lib" / "site-packages" / "x.pyd")
        снимок(self.папка / "pythonw.exe.10.dmp", [r"C:\py\pythonw.exe", свой])
        снимок(self.папка / "python.exe.11.dmp", [r"C:\py\python.exe", свой.upper()])
        # Тот же python, но другая программа — чужой снимок.
        снимок(self.папка / "pythonw.exe.12.dmp", [r"C:\py\pythonw.exe", r"D:\Другое\x.pyd"])
        # Папка с похожим началом имени — тоже чужая.
        снимок(self.папка / "pythonw.exe.13.dmp", [str(self.корень) + "2\\x.pyd"])
        снимок(self.папка / "Adobe.exe.14.dmp", [свой])
        (self.папка / "pythonw.exe.15.dmp").write_bytes(b"MDMP broken")
        свои = [п.name for п in crash_traces.own_dumps(self.корень)]
        self.assertEqual(свои, ["python.exe.11.dmp", "pythonw.exe.10.dmp"])

    def test_нет_папки(self):
        self.папка.rmdir()
        self.assertEqual(crash_traces.own_dumps(self.корень), [])


class FthTests(unittest.TestCase):
    PY = r"C:\Users\x\uv\python\cpython-3.11\pythonw.exe"

    def setUp(self):
        patcher = mock.patch.object(crash_traces, "interpreters", return_value=[
            self.PY, self.PY.replace("pythonw", "python")])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _реестр(self, hklm, hkcu=None):
        import winreg

        def значения(корень, ключ):
            self.assertEqual(ключ, crash_traces.LAYERS_KEY)
            return hklm if корень == winreg.HKEY_LOCAL_MACHINE else (hkcu or {})
        return mock.patch.object(crash_traces, "_значения", side_effect=значения)

    def test_находит_свой_python(self):
        with self._реестр({self.PY.upper(): "FaultTolerantHeap",
                           r"C:\Игра\game.exe": "FaultTolerantHeap",
                           self.PY.replace("pythonw", "python"): "~ RUNASADMIN"},
                          {self.PY: "~ HIGHDPIAWARE FaultTolerantHeap"}):
            отметки = crash_traces.fth_marks()
        self.assertEqual(отметки, [("HKLM", self.PY.upper(), "FaultTolerantHeap"),
                                   ("HKCU", self.PY, "~ HIGHDPIAWARE FaultTolerantHeap")])

    def test_reg_файл(self):
        текст = crash_traces.reg_text([(self.PY, "FaultTolerantHeap"),
                                       (r"C:\py\python.exe", "~ RUNASADMIN FaultTolerantHeap")])
        строки = текст.split("\r\n")
        self.assertEqual(строки[0], "Windows Registry Editor Version 5.00")
        self.assertIn('"C:\\\\Users\\\\x\\\\uv\\\\python\\\\cpython-3.11\\\\pythonw.exe"=-', строки)
        # Другие флаги совместимости остаются, снимается только FTH.
        self.assertIn('"C:\\\\py\\\\python.exe"="~ RUNASADMIN"', строки)
        состояние = строки.index(f"[HKEY_LOCAL_MACHINE\\{crash_traces.FTH_STATE_KEY}]")
        self.assertIn('"C:\\\\py\\\\python.exe"=-', строки[состояние:])
        self.assertNotIn("\r\r", текст)

    def test_без_fth(self):
        self.assertEqual(crash_traces._без_fth("FaultTolerantHeap"), "")
        self.assertEqual(crash_traces._без_fth("~ FaultTolerantHeap"), "")
        self.assertEqual(crash_traces._без_fth("~ WIN7RTM FaultTolerantHeap"), "~ WIN7RTM")


class ОчисткаTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.свой = self.tmp / "pythonw.exe.1.dmp"
        self.чужой = self.tmp / "pythonw.exe.2.dmp"
        снимок(self.свой, [r"C:\py\pythonw.exe"], хвост=1000)
        снимок(self.чужой, [r"C:\py\pythonw.exe"])
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(crash_traces, "own_dumps", return_value=[self.свой]).start()
        self.права = mock.patch.object(crash_traces, "run_elevated", return_value=True).start()

    def test_удаляет_свои_и_снимает_fth(self):
        отметка = [("HKLM", r"C:\py\pythonw.exe", "FaultTolerantHeap")]
        прочитано = {}

        def права(программа, параметры):
            путь = Path(параметры.split('"')[1])
            прочитано["reg"] = путь.read_bytes().decode("utf-16")
            return True
        self.права.side_effect = права
        размер = self.свой.stat().st_size
        with mock.patch.object(crash_traces, "fth_marks", side_effect=[отметка, [], []]):
            итог = crash_traces.clean()
        self.assertFalse(self.свой.exists())
        self.assertTrue(self.чужой.exists())
        self.assertEqual((итог["removed"], итог["freed"]), (1, размер))
        self.assertTrue(итог["fth_cleared"])
        self.assertEqual(self.права.call_args.args[0], "reg.exe")
        self.assertIn("pythonw.exe\"=-", прочитано["reg"])
        # Временный .reg за собой не оставляет.
        self.assertFalse(Path(self.права.call_args.args[1].split('"')[1]).exists())

    def test_отказ_в_правах(self):
        отметка = [("HKLM", r"C:\py\pythonw.exe", "FaultTolerantHeap")]
        self.права.return_value = False
        with mock.patch.object(crash_traces, "fth_marks", return_value=отметка):
            итог = crash_traces.clean()
        self.assertFalse(итог["fth_cleared"])
        self.assertEqual(итог["left"]["fth"], [r"C:\py\pythonw.exe"])

    def test_fth_у_пользователя_без_окна_прав(self):
        отметка = [("HKCU", r"C:\py\pythonw.exe", "FaultTolerantHeap")]
        with mock.patch.object(crash_traces, "fth_marks", side_effect=[отметка, [], []]), \
                mock.patch.object(crash_traces, "_снять_у_себя") as у_себя:
            итог = crash_traces.clean()
        у_себя.assert_called_once_with([(r"C:\py\pythonw.exe", "FaultTolerantHeap")])
        self.права.assert_not_called()
        self.assertTrue(итог["fth_cleared"])

    def test_нечего_убирать(self):
        with mock.patch.object(crash_traces, "fth_marks", return_value=[]), \
                mock.patch.object(crash_traces, "own_dumps", return_value=[]):
            итог = crash_traces.clean()
        self.assertEqual(итог["removed"], 0)
        self.assertIsNone(итог["fth_cleared"])
        self.права.assert_not_called()


def _порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class АдресаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_порт())
        cls.server.start()
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        self.server.runtime = WebRuntime.__new__(WebRuntime)
        self.addCleanup(lambda: setattr(self.server, "runtime", None))
        for имя, значение in (
                ("find", {"dumps": 2, "bytes": 10, "fth": []}),
                ("clean", {"removed": 2, "freed": 10, "failed": [], "fth_cleared": None,
                           "left": {"dumps": 0, "bytes": 0, "fth": []}})):
            patcher = mock.patch.object(crash_traces, имя, return_value=значение)
            setattr(self, имя, patcher.start())
            self.addCleanup(patcher.stop)
        self.client = TestClient(self.server._app)
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_что_осталось(self):
        ответ = self.client.get("/api/logs/traces").json()
        self.assertEqual((ответ["ok"], ответ["dumps"]), (True, 2))

    def test_убрать(self):
        ответ = self.client.post("/api/logs/traces/clean").json()
        self.assertEqual((ответ["ok"], ответ["removed"]), (True, 2))
        self.clean.assert_called_once()

    def test_только_с_этого_компьютера(self):
        self.assertEqual(self.вдали.get("/api/logs/traces").status_code, 403)
        self.assertEqual(self.вдали.post("/api/logs/traces/clean").status_code, 403)
        self.clean.assert_not_called()


class ПультTests(unittest.TestCase):
    def test_строка_следов_на_странице_логов(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("fetch('/api/logs/traces')", пульт)
        self.assertIn("fetch('/api/logs/traces/clean', { method: 'POST' })", пульт)
        self.assertIn("'Убрать следы'", пульт)


if __name__ == "__main__":
    unittest.main()
