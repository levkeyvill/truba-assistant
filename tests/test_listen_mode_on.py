"""Слух включается из «не слушает» тем режимом, что был до него.

Кнопки «включить слух» (Панель, главная, круг телефона, автозапуск голоса)
шлют `on`, а пульт подставляет последний выбранный режим: «слышит всё» или
«по имени». Настройки и телефон подменены: живой `settings.json` не трогается.
"""

import types
import unittest
from pathlib import Path
from unittest import mock

import config
from core import settings
from ui.web_runtime import WebRuntime

ROOT = Path(__file__).resolve().parent.parent


class РежимВключенияTests(unittest.TestCase):
    def test_сохранённый_режим_главнее(self):
        self.assertEqual(settings.listen_mode_on(
            {"listen_mode_on": "always", "listen_mode": "off"}), "always")

    def test_без_сохранённого_берётся_текущий(self):
        self.assertEqual(settings.listen_mode_on({"listen_mode": "always"}), "always")
        self.assertEqual(settings.listen_mode_on({"listen_mode": "name"}), "name")

    def test_слух_выключен_и_ничего_не_выбирали_по_имени(self):
        self.assertEqual(settings.listen_mode_on({"listen_mode": "off"}), "name")
        self.assertEqual(settings.listen_mode_on(
            {"listen_mode_on": "off", "listen_mode": "off"}), "name")
        self.assertEqual(settings.listen_mode_on(
            {"listen_mode_on": "мусор", "listen_mode": "off"}), "name")


class ПультTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.LISTEN_MODE, getattr(config, "LISTEN_MODE_ON", None))
        self.addCleanup(self._restore)
        self.записано = []
        patcher = mock.patch.object(settings, "save_settings",
                                    side_effect=lambda values: self.записано.append(dict(values)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.среда = WebRuntime.__new__(WebRuntime)
        self.среда.voice = types.SimpleNamespace(_close_conversation=mock.Mock())
        self.среда.server = types.SimpleNamespace(send_mode=mock.Mock())
        self.среда.voice_state = mock.Mock(return_value={})

    def _restore(self):
        config.LISTEN_MODE, config.LISTEN_MODE_ON = self._saved

    def test_выкл_и_обратно_возвращает_всё(self):
        config.LISTEN_MODE, config.LISTEN_MODE_ON = "name", "name"
        self.среда.voice_mode("always")
        self.среда.voice_mode("off")
        self.среда.voice_mode("on")
        self.assertEqual(config.LISTEN_MODE, "always")
        self.assertEqual(self.записано[-1], {"listen_mode": "always",
                                             "listen_mode_on": "always"})
        # Телефону уходит настоящий режим, а не слово `on`.
        self.assertEqual(self.среда.server.send_mode.call_args_list[-1],
                         mock.call("always"))

    def test_выкл_не_затирает_режим_включения(self):
        config.LISTEN_MODE, config.LISTEN_MODE_ON = "always", "always"
        self.среда.voice_mode("off")
        self.assertEqual(self.записано[-1], {"listen_mode": "off"})
        self.assertEqual(config.LISTEN_MODE_ON, "always")

    def test_по_имени_тоже_запоминается(self):
        config.LISTEN_MODE, config.LISTEN_MODE_ON = "always", "always"
        self.среда.voice_mode("name")
        self.среда.voice_mode("off")
        self.среда.voice_mode("on")
        self.assertEqual(config.LISTEN_MODE, "name")

    def test_телефон_может_прислать_on(self):
        self.среда.voice_mode = mock.Mock()
        self.среда._remember = mock.Mock()
        self.среда.handle_event("mode", {"value": "on"})
        self.среда.voice_mode.assert_called_once_with("on")


class КнопкиTests(unittest.TestCase):
    """Кнопки включения слуха шлют `on`, а не жёстко «по имени»."""

    def test_пульт_главная_и_телефон(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        главная = (ROOT / "ui" / "web" / "home.js").read_text(encoding="utf-8")
        телефон = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("'/api/voice/mode', { value: 'name' }", пульт)
        self.assertEqual(пульт.count("'/api/voice/mode', { value: 'on' }"), 2)
        self.assertIn("mode === 'off' ? 'on' : 'off'", главная)
        self.assertIn("send({ type: 'mode', value: 'on' })", телефон)


if __name__ == "__main__":
    unittest.main()
