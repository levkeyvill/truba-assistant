"""Вид главной в «Настройки → Главная»: значения по умолчанию и проверка.

Настройки пишутся во временную папку: живой `settings.json` не трогается.
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import settings
from ui.web_runtime import WebRuntime

ROOT = Path(config.ROOT)


class ВидГлавнойTests(unittest.TestCase):
    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-home-look-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(settings, SETTINGS_PATH=папка / "settings.json",
                                      ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)

    def _среда(self):
        runtime = object.__new__(WebRuntime)
        runtime._provider_test_lock = threading.Lock()
        runtime._lock = threading.Lock()
        runtime.server = mock.Mock()
        runtime.brain = None
        runtime.voice = NS(_brain=None, _close_conversation=lambda: None, running=False)
        runtime._enroll = None
        runtime._jobs = {}
        runtime._audio_stale = False
        runtime._remember = lambda kind, payload: None
        return runtime

    def test_по_умолчанию_кольцо_70_и_80(self):
        s = settings.load_settings()
        self.assertEqual((s["home_shape"], s["home_roundness"], s["home_reaction"],
                          s["home_brightness"], s["home_shimmer"], s["home_spin"],
                          s["home_jumps"], s["home_color"], s["home_gradient"],
                          s["home_backdrop"]),
                         ("ring", 70, 80, 100, True, 70, 60, "blue", False, True))

    def test_куб_и_свои_проценты_сохраняются(self):
        ответ = self._среда().save_settings({
            "home_shape": "cube", "home_roundness": 100, "home_reaction": 150,
            "home_brightness": 60, "home_shimmer": False})
        self.assertTrue(ответ.get("ok"), ответ)
        сохранено = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(сохранено["home_shape"], "cube")
        self.assertEqual(сохранено["home_roundness"], 100)
        self.assertFalse(сохранено["home_shimmer"])

    def test_мусор_отклоняется(self):
        for поле, значение in (("home_shape", "пирамида"), ("home_roundness", 20),
                               ("home_reaction", 500), ("home_brightness", "ярко"),
                               ("home_spin", 5), ("home_jumps", 101),
                               ("home_color", "green"), ("home_gradient", "да"),
                               ("home_shimmer", "да")):
            with self.subTest(поле=поле):
                ответ = self._среда().save_settings({поле: значение})
                self.assertFalse(ответ.get("ok"), ответ)

    def test_раздел_есть_в_пульте(self):
        js = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        html = (ROOT / "ui" / "web" / "pult.html").read_text(encoding="utf-8")
        self.assertIn("['главная', 'Главная',", js)
        self.assertIn('data-настройка="главная"', html)
        self.assertIn("главнаяОбразецСоздать(", js)
        # Вид применяется к главной при загрузке пульта, а не только из настроек.
        self.assertIn("главнаяВид(s);", js)


if __name__ == "__main__":
    unittest.main()
