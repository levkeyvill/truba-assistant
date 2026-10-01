"""Полоска уровня выбранного микрофона на шаге 4 мастера.

Пульт целиком не исполняют, но его функции вырезаются из `pult.js` насквозь и
живут в node (tests/wizard_mic_level.mjs): сервер, устройства, таймеры и окно —
подмены, ничего живого не трогается.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

import config


class УровеньМастераTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        cls.скрипт = (config.ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")

    def setUp(self):
        if self.node is None:
            self.skipTest("node не установлен")

    def _запустить(self):
        result = subprocess.run(
            [self.node, str(config.ROOT / "tests" / "wizard_mic_level.mjs")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_polling_names_the_chosen_microphone_and_stops_on_leaving(self):
        # Запрос сам называет микрофон и вход (включая несохранённый выбор),
        # опрос идёт при выключенном голосе и останавливается на уходе.
        self._запустить()

    def test_the_wizard_asks_about_the_chosen_microphone(self):
        # Имя и вход берутся из полей шага, а не из сохранённых настроек:
        # иначе полочка смотрела бы на прошлый микрофон.
        блок = self.скрипт.split("function мастерУровеньАдрес(")[1].split("\n}\n")[0]
        self.assertIn("эл.mic_name.value", блок)
        self.assertIn("эл.mic_channel.value", блок)
        self.assertIn("preview=1", блок)

    def test_the_settings_page_keeps_its_own_plain_request(self):
        # Вкладка «Голос → Звук» не шлёт параметров выбора и осталась как была.
        блок = self.скрипт.split("async function звукУровеньОдинРаз(")[1].split("\n}\n")[0]
        self.assertIn("fetch('/api/audio/level', { cache: 'no-store' })", блок)
        self.assertNotIn("preview", блок)

    def test_the_polling_starts_even_with_the_voice_off(self):
        # Отзыв чистой установки: полоска молчала, пока голос не запустится.
        блок = self.скрипт.split("async function мастерШагЗвукЖивой(")[1].split("\n}\n")[0]
        self.assertIn("мастерУровеньВключить(эл);", блок)
        self.assertNotIn("if (работает) {\n    звукУровеньВключить", блок)

    def test_the_wizard_poll_is_its_own_and_bounded(self):
        # Свой таймер (шаг 4 не должен мешать вкладке «Звук») и пауза около
        # 0,4 с — столько же, сколько сервер разрешает замерять.
        self.assertIn("let мастерУровеньТаймер = null;", self.скрипт)
        self.assertIn("const МАСТЕР_УРОВЕНЬ_ПАУЗА = 400;", self.скрипт)
        self.assertIn("мастерУровеньВыключить();\n  if (!слой || !мастерОткрыт) return;",
                      self.скрипт)

    def test_coming_back_to_the_window_restarts_the_wizard_poll(self):
        # Возврат видимого окна на шаг 4 снова спрашивает уровень.
        блок = self.скрипт.split("addEventListener('visibilitychange'")[1].split("\n});")[0]
        self.assertIn("мастерУровеньВыключить()", блок)
        self.assertIn("мастерШаг === 4", блок)
        self.assertIn("мастерУровеньВключить(мастерШаги[4]);", блок)


if __name__ == "__main__":
    unittest.main()