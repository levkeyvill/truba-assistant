"""Свежая установка без prompts/persona.md: Труба всё равно с характером.

28.09: мозг открывал persona.md напрямую и без файла не создавался — на
чистой установке она бы молчала (в пробную установку файл попал случайно).
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core import brain, personas, settings


class СвежийХарактерTests(unittest.TestCase):
    def test_мозг_берёт_готовый_характер(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "PERSONA_PRESET", "calm"):
                текст = brain.read_persona(Path(tmp) / "persona.md")
        self.assertTrue(текст)
        self.assertEqual(текст, personas.text("calm").strip())

    def test_мозг_читает_свой_файл(self):
        with tempfile.TemporaryDirectory() as tmp:
            свой = Path(tmp) / "persona.md"
            свой.write_text("  свой характер \n", encoding="utf-8")
            self.assertEqual(brain.read_persona(свой), "свой характер")

    def test_форма_показывает_готовый_характер(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(settings, "PERSONA_PATH", Path(tmp) / "persona.md"), \
                    mock.patch.object(config, "PERSONA_PRESET", "friendly"):
                self.assertEqual(settings.load_persona(), personas.text("friendly"))

    def test_свой_текст_главнее(self):
        with tempfile.TemporaryDirectory() as tmp:
            свой = Path(tmp) / "persona.md"
            свой.write_text("свой характер", encoding="utf-8")
            with mock.patch.object(settings, "PERSONA_PATH", свой):
                self.assertEqual(settings.load_persona(), "свой характер")


if __name__ == "__main__":
    unittest.main()
