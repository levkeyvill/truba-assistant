"""Пульт: «Голос → Звук», «Твой компьютер» в «Озвучивании», город погоды.

Тесты пульта ищут строки в pult.js — страницу они не исполняют. Поэтому
здесь же проверки на грабли, которые уже случались: латинская буква в
кириллическом имени и `число()` на строке из поля формы.
"""

import re
import unittest
from pathlib import Path

import config

PULT = Path(config.ROOT) / "ui" / "web"


class ЗвукСтраницаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.скрипт = (PULT / "pult.js").read_text(encoding="utf-8")
        cls.разметка = (PULT / "pult.html").read_text(encoding="utf-8")

    def test_подраздел_звук_последний_в_голосе(self):
        self.assertIn("голос: ['голос', 'слух', 'распознавание', 'звук']", self.скрипт)
        self.assertIn("['звук', 'Звук', 'Откуда Труба слышит и куда говорит']", self.скрипт)
        self.assertIn('data-голос="звук"', self.разметка)

    def test_маршруты(self):
        for маршрут in ("/api/audio'", "/api/audio/level", "/api/audio/test",
                        "/api/hardware", "/api/voices/status", "/api/voices/install",
                        "/api/weather/find?q="):
            self.assertIn(маршрут, self.скрипт)

    def test_поле_output_одно_и_в_звуке(self):
        self.assertEqual(self.скрипт.count("настрПоле(звук, 'output', output)"), 1)
        self.assertNotIn("настрПоле(телефон, 'output'", self.скрипт)

    def test_звуковые_ключи_не_шлются_без_списков(self):
        self.assertIn("mic_name: звукСписки ? эл.mic_name.value : undefined", self.скрипт)
        self.assertIn("speaker_name: звукСписки ? эл.speaker_name.value : undefined",
                      self.скрипт)

    def test_вход_из_поля_формы_читается_как_число(self):
        # `число()` берёт только настоящие числа: на «1» из <select> она
        # отвечала пустотой, и «Вход 2» тихо становился «Входом 1».
        self.assertNotIn("число(эл.mic_channel.value)", self.скрипт)
        self.assertNotIn("число(пункт.dataset.channels)", self.скрипт)
        self.assertIn("function звукЧисло(", self.скрипт)

    def test_нет_латинской_p_вместо_кириллической(self):
        # Параметр `п`, а внутри `p.value` — ReferenceError на первом же
        # сохранённом микрофоне.
        for найдено in re.finditer(r"\(п\) =>\s*\n?\s*([^;]+);", self.скрипт):
            self.assertNotRegex(найдено.group(1), r"(?<![\wа-яё])p\.")

    def test_уровень_возвращается_при_возврате_в_окно(self):
        self.assertIn("addEventListener('visibilitychange'", self.скрипт)

    def test_погода_уходит_только_при_правке(self):
        self.assertIn("if (эл.погЧерновик) {", self.скрипт)
        self.assertIn("'Без погоды'", self.скрипт)

    def test_качественные_голоса_спрашивают_перед_закрытием(self):
        self.assertIn("Пульт закроется и откроется окно установки", self.скрипт)


if __name__ == "__main__":
    unittest.main()
