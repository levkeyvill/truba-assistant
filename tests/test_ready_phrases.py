"""Фразы при включении голоса: набор характера и свои варианты.

Поле «Фразы при включении голоса» принимает по одной фразе на строку.
Пустое поле оставляет фразы выбранного характера.
"""

import unittest
from pathlib import Path

import config
from core import personas, settings, voice_loop

ROOT = Path(config.ROOT)


class ФразыХарактераTests(unittest.TestCase):
    def test_нет_да_чё(self):
        for ключ in personas.PRESETS:
            for фраза in personas.ready_words(ключ):
                self.assertNotIn("чё?", фраза.lower(), ключ)
        for фраза in voice_loop.READY_WORDS:
            self.assertNotIn("чё?", фраза.lower())


class СвоиФразыTests(unittest.TestCase):
    def setUp(self):
        self._было = (config.READY_PHRASES, config.PERSONA_PRESET)

    def tearDown(self):
        config.READY_PHRASES, config.PERSONA_PRESET = self._было

    def test_свои_главнее_характера(self):
        config.PERSONA_PRESET = "pizdabol"
        config.READY_PHRASES = ["Здорово, я на месте.", "Готова, жги."]
        for _ in range(10):
            self.assertIn(voice_loop.ready_phrase(), config.READY_PHRASES)

    def test_пусто_значит_фразы_характера(self):
        config.PERSONA_PRESET = "calm"
        config.READY_PHRASES = []
        for _ in range(10):
            self.assertIn(voice_loop.ready_phrase(), personas.ready_words("calm"))

    def test_проверка_списка(self):
        v = settings.validate_ready_phrases
        self.assertEqual(v(["  Слушаю.  ", "", "Слушаю.", 5, "На   связи."]),
                         ["Слушаю.", "На связи."])
        self.assertEqual(v("Слушаю."), [], "строка вместо списка — фразы характера")
        self.assertEqual(v(None), [])
        self.assertEqual(len(v([f"Фраза {i}" for i in range(50)])),
                         settings.READY_PHRASES_MAX)
        длинная = v(["слово " * 40])[0]
        self.assertLessEqual(len(длинная), settings.READY_PHRASE_CHARS)
        self.assertFalse(длинная.endswith(" "))

    def test_по_умолчанию_пусто(self):
        self.assertEqual(settings.DEFAULTS["ready_phrases"], [])


class ПультTests(unittest.TestCase):
    def test_поле_в_характере_и_сохранение(self):
        pult = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("'Фразы при включении голоса'", pult)
        self.assertIn("ready_phrases: настрФразы(эл.ready_phrases.value)", pult)
        self.assertIn("эл.ready_phrases.value = Array.isArray(s.ready_phrases)", pult)
        среда = (ROOT / "ui" / "web_runtime.py").read_text(encoding="utf-8")
        self.assertIn("settings.validate_ready_phrases(", среда)


if __name__ == "__main__":
    unittest.main()
