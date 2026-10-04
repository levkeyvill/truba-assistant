"""Автозапуск с Windows: ярлык, галочка в пульте и запуск в трее.

Настоящая папка автозагрузки не трогается никогда: `test_data_guard` подменяет
`autostart.STARTUP_DIR` на временную, а здесь ещё и подменяется создание
ярлыка — COM в тестах не нужен, проверяем то, что мы туда передали.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import autostart, settings
from ui.web_runtime import WebRuntime
from ui.window import разобрать_аргументы


class ЯрлыкТесты(unittest.TestCase):
    """enable() создаёт ярлык, disable() убирает только свой."""

    def setUp(self):
        self.папка = tempfile.TemporaryDirectory(prefix="truba-autostart-")
        self.addCleanup(self.папка.cleanup)
        self.startup = Path(self.папка.name) / "Startup"
        self.записано = []

        def записать(путь, target, args, workdir, icon):
            self.записано.append(
                {"путь": Path(путь), "target": target, "args": args,
                 "workdir": workdir, "icon": icon})
            Path(путь).write_text("ярлык", encoding="utf-8")

        self.подмена = patch.multiple(autostart, STARTUP_DIR=self.startup,
                                      _записать=записать)
        self.подмена.start()
        self.addCleanup(self.подмена.stop)

    def test_enable_создаёт_ярлык_с_нужными_полями(self):
        autostart.enable()
        ярлык = self.startup / "Труба.lnk"
        self.assertTrue(ярлык.is_file())
        self.assertEqual(len(self.записано), 1)
        поля = self.записано[0]
        self.assertEqual(поля["target"], os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe"))
        self.assertEqual(поля["args"], f'"{autostart.путь_скрипта()}" /tray')
        self.assertEqual(поля["workdir"], str(autostart.корень()))
        self.assertEqual(поля["icon"], autostart.значок())

    def test_enabled_видит_свой_ярлык(self):
        self.assertFalse(autostart.enabled())
        autostart.enable()
        # Читать ярлык тоже подменяем: настоящий .lnk умеет читать только COM.
        with patch.object(autostart, "_прочитать", return_value={
                "target": "wscript.exe", "arguments": autostart.аргументы(),
                "workdir": str(autostart.корень())}):
            self.assertTrue(autostart.enabled())

    def test_enabled_ложь_для_чужого_ярлыка(self):
        autostart.enable()
        with patch.object(autostart, "_прочитать", return_value={
                "target": "wscript.exe",
                "arguments": '"D:\\другая\\Труба.vbs" /tray', "workdir": ""}):
            self.assertFalse(autostart.enabled())

    def test_enabled_не_падает_без_com(self):
        autostart.enable()
        with patch.object(autostart, "_прочитать", side_effect=OSError("нет COM")):
            self.assertFalse(autostart.enabled())

    def test_disable_убирает_только_свой_ярлык(self):
        autostart.enable()
        чужой = self.startup / "Steam.lnk"
        чужой.write_text("чужой", encoding="utf-8")
        autostart.disable()
        self.assertFalse((self.startup / "Труба.lnk").exists())
        self.assertTrue(чужой.is_file())

    def test_disable_без_ярлыка_не_ошибка(self):
        autostart.disable()
        self.assertFalse((self.startup / "Труба.lnk").exists())

    def test_ошибка_enable_понятным_текстом(self):
        with patch.object(autostart, "_записать", side_effect=OSError("доступ закрыт")):
            with self.assertRaisesRegex(ValueError, "ярлык автозапуска не создался"):
                autostart.enable()


class ТрейТесты(unittest.TestCase):
    def test_разбор_аргументов(self):
        self.assertFalse(разобрать_аргументы([])["в_трей"])
        self.assertTrue(разобрать_аргументы(["--tray"])["в_трей"])
        self.assertFalse(разобрать_аргументы(None)["в_трей"])
        # Неизвестные ключи не включают трей: молчаливый пульт хозяину не нужен.
        self.assertFalse(разобрать_аргументы(["--что-то"])["в_трей"])


def _среда(журнал=None):
    """Настоящий `save_settings`, но без пульта, ключей и настроек хозяина."""
    runtime = object.__new__(WebRuntime)
    runtime.brain = None
    runtime.voice = SimpleNamespace(_brain=None)
    runtime._lock = threading.Lock()
    runtime._ids = iter(range(1000, 100000))
    runtime._events = []
    runtime._overview = {}
    runtime.log_message = lambda text: None
    runtime._remember = lambda kind, payload: (
        журнал.append((kind, payload)) if журнал is not None else None)
    return runtime


class ГалочкаТесты(unittest.TestCase):
    """Сохранение настроек зовёт enable/disable и отвечает фактом."""

    def setUp(self):
        self.папка = tempfile.TemporaryDirectory(prefix="truba-autostart-")
        self.addCleanup(self.папка.cleanup)
        self.startup = Path(self.папка.name) / "Startup"
        self.ярлык = self.startup / "Труба.lnk"

        def записать(путь, target, args, workdir, icon):
            Path(путь).parent.mkdir(parents=True, exist_ok=True)
            Path(путь).write_text("ярлык", encoding="utf-8")

        def прочитать(путь):
            return {"target": "wscript.exe",
                    "arguments": autostart.аргументы() if Path(путь).is_file() else "",
                    "workdir": str(autostart.корень())}

        for имя, значение in (("STARTUP_DIR", self.startup),
                              ("_записать", записать), ("_прочитать", прочитать)):
            patcher = patch.object(autostart, имя, значение)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _сохранить(self, payload):
        журнал = []
        среда = _среда(журнал)
        with tempfile.TemporaryDirectory() as папка:
            with patch.object(settings, "SETTINGS_PATH", Path(папка) / "settings.json"), \
                 patch.object(settings, "ENV_PATH", Path(папка) / ".env"):
                ответ = среда.save_settings(payload)
                сохранено = settings.load_settings()
        return ответ, сохранено, журнал

    def test_галочка_создаёт_ярлык_и_не_пишет_в_settings(self):
        ответ, сохранено, журнал = self._сохранить({"autostart": True})
        self.assertTrue(ответ["ok"])
        self.assertTrue(self.ярлык.is_file())
        self.assertTrue(ответ["settings"]["autostart"])
        self.assertNotIn("autostart", сохранено)
        self.assertIn(("autostart", True), журнал)

    def test_галочка_выключенная_убирает_ярлык(self):
        self._сохранить({"autostart": True})
        self.assertTrue(self.ярлык.is_file())
        ответ, сохранено, журнал = self._сохранить({"autostart": False})
        self.assertTrue(ответ["ok"])
        self.assertFalse(self.ярлык.exists())
        self.assertFalse(ответ["settings"]["autostart"])
        self.assertNotIn("autostart", сохранено)
        self.assertIn(("autostart", False), журнал)

    def test_несменённая_галочка_ярлык_не_трогает(self):
        # «Сохранить» жмут ради любой настройки: ярлык не пересоздаётся, а
        # журнал не засоряется строкой «автозапуск включён» на каждое нажатие.
        self._сохранить({"autostart": True})
        with patch.object(autostart, "enable") as enable, \
             patch.object(autostart, "disable") as disable:
            ответ, _сохранено, журнал = self._сохранить({"autostart": True})
        self.assertTrue(ответ["ok"])
        enable.assert_not_called()
        disable.assert_not_called()
        self.assertEqual(журнал, [])

    def test_ошибка_возвращается_у_галочки(self):
        среда = _среда()
        with patch.object(autostart, "enable",
                          side_effect=ValueError("ярлык автозапуска не создался: отказ")):
            with tempfile.TemporaryDirectory() as папка:
                with patch.object(settings, "SETTINGS_PATH", Path(папка) / "settings.json"), \
                     patch.object(settings, "ENV_PATH", Path(папка) / ".env"):
                    ответ = среда.save_settings({"autostart": True})
        self.assertFalse(ответ["ok"])
        self.assertTrue(ответ["errors"][0].startswith("autostart:"))

    def test_снимок_настроек_отдаёт_факт(self):
        среда = _среда()
        self.assertFalse(среда.settings_snapshot()["autostart"])
        autostart.enable()
        self.assertTrue(среда.settings_snapshot()["autostart"])


class ЖурналТесты(unittest.TestCase):
    def test_строка_журнала_про_автозапуск(self):
        включён = WebRuntime._log_messages("autostart", True)
        выключен = WebRuntime._log_messages("autostart", False)
        self.assertIn("автозапуск с Windows", включён[0])
        self.assertIn("включён", включён[0])
        self.assertIn("выключен", выключен[0])


if __name__ == "__main__":
    unittest.main()

