"""«Сохранить» в «Программах» не теряет `process` и `aliases` (28.09).

У ChatGPT запускается LaunchCodex.exe, а живёт ChatGPT.exe: без `process`
«закрой ChatGPT» ищет не тот процесс. Поле пропало после сохранения в пульте —
редактор его не знал, а сервер писал только знакомое.
"""

import types
import unittest
from pathlib import Path
from unittest import mock

import config
from core import launcher
from ui.web_runtime import WebRuntime

PULT = Path(config.ROOT) / "ui" / "web" / "pult.js"


class ПоляПрограммTests(unittest.TestCase):
    def _сохранить(self, items, прежние):
        среда = WebRuntime.__new__(WebRuntime)
        среда.voice = types.SimpleNamespace(_apps=None)
        среда._bg = mock.Mock()
        записано = {}
        with mock.patch.object(launcher, "read_list", return_value=прежние), \
                mock.patch.object(launcher, "save_list",
                                  side_effect=lambda список: записано.setdefault("список", список)):
            итог = среда.apps_save(items)
        return итог, записано.get("список")

    def _chatgpt(self, **лишнее):
        запись = {"id": "chatgpt", "title": "ChatGPT", "kind": "app",
                  "path": r"C:\Codex\LaunchCodex.exe", "args": [], "how": "shell"}
        запись.update(лишнее)
        return запись

    def test_process_приходит_и_сохраняется(self):
        итог, список = self._сохранить([self._chatgpt(process="ChatGPT.exe")], [])
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(список[0]["process"], "ChatGPT.exe")

    def test_не_прислали_берём_из_прежней_записи(self):
        прежние = [self._chatgpt(process="ChatGPT.exe", aliases=["чатик"])]
        итог, список = self._сохранить([self._chatgpt()], прежние)
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(список[0]["process"], "ChatGPT.exe")
        self.assertEqual(список[0]["aliases"], ["чатик"])

    def test_путь_вместо_имени_отклоняется(self):
        итог, _ = self._сохранить([self._chatgpt(process=r"C:\x\ChatGPT.exe")], [])
        self.assertFalse(итог["ok"])

    def test_кривые_прозвища_отклоняются(self):
        итог, _ = self._сохранить([self._chatgpt(aliases="чатик")], [])
        self.assertFalse(итог["ok"])

    def test_пульт_носит_поля_дальше(self):
        скрипт = PULT.read_text(encoding="utf-8")
        self.assertIn("out.process = String(app.process || '')", скрипт)
        self.assertIn("б.process = String(п.process).trim()", скрипт)
        self.assertIn("б.aliases = п.aliases.map", скрипт)


class ЯрлыкTests(unittest.TestCase):
    """Программу добавили ярлыком: закрывать надо exe, а не «….lnk» (OBS)."""

    def test_имя_процесса_из_цели_ярлыка(self):
        запись = {"path": r"C:\Start Menu\OBS Studio (64bit).lnk"}
        with mock.patch.object(launcher, "_shortcut_target_name",
                               return_value="obs64.exe"):
            self.assertEqual(launcher.process_name(запись), "obs64.exe")

    def test_ярлык_не_прочитался_остаётся_имя(self):
        запись = {"path": r"C:\нет\такого.lnk"}
        with mock.patch.object(launcher, "_shortcut_target_name", return_value=""):
            self.assertEqual(launcher.process_name(запись), "такого.lnk")

    def test_явный_process_главнее_ярлыка(self):
        запись = {"path": r"C:\x\OBS.lnk", "process": "obs64.exe"}
        with mock.patch.object(launcher, "_shortcut_target_name") as цель:
            self.assertEqual(launcher.process_name(запись), "obs64.exe")
        цель.assert_not_called()


if __name__ == "__main__":
    unittest.main()
