"""Запуск голоса при выключенном слухе — без приветствия."""

import types
import unittest
from unittest import mock

import config
from core import voice_loop
from ui.web_runtime import WebRuntime


class ТихийСтартTests(unittest.TestCase):
    def _среда(self):
        среда = WebRuntime.__new__(WebRuntime)
        среда.voice = types.SimpleNamespace(running=False, quiet_start=False)
        среда.voice_mode = mock.Mock()
        среда.voice_toggle = mock.Mock()
        среда._remember = mock.Mock()
        return среда

    def test_слух_выключен_стартует_молча(self):
        среда = self._среда()
        with mock.patch.object(config, "VOICE_AUTOSTART", True), \
                mock.patch.object(config, "LISTEN_MODE", "off"):
            среда.voice_autostart()
        # `on` — тот режим слуха, что был до «не слушает».
        среда.voice_mode.assert_called_once_with("on")
        среда.voice_toggle.assert_called_once()
        self.assertTrue(среда.voice.quiet_start)

    def test_слух_включён_здоровается(self):
        среда = self._среда()
        with mock.patch.object(config, "VOICE_AUTOSTART", True), \
                mock.patch.object(config, "LISTEN_MODE", "name"):
            среда.voice_autostart()
        среда.voice_mode.assert_not_called()
        self.assertFalse(среда.voice.quiet_start)

    def test_флажок_разовый_и_есть_по_умолчанию(self):
        # Цикл собирают и вручную (тесты, подмены) — поле обязано быть.
        self.assertFalse(voice_loop.VoiceLoop.quiet_start)
        исходник = open(voice_loop.__file__, encoding="utf-8").read()
        self.assertIn("self.quiet_start = False", исходник)
        self.assertIn('тихо = self.quiet_start or config.LISTEN_MODE == "off"', исходник)


if __name__ == "__main__":
    unittest.main()
