"""Проба микрофона в шаге 4 мастера: пульт не исполняют, но эти функции
вырезаются из `pult.js` целиком и живут в node (tests/wizard_sound_probe.mjs):
сервер, устройства и голос — подмены, ничего живого не трогается."""

import shutil
import subprocess
import unittest

import config


class ПробаЗвукаВМастереTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def setUp(self):
        if self.node is None:
            self.skipTest("node не установлен")

    def _запустить(self):
        result = subprocess.run(
            [self.node, str(config.ROOT / "tests" / "wizard_sound_probe.mjs")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_pult_script_is_valid(self):
        result = subprocess.run(
            [self.node, "--check", str(config.ROOT / "ui" / "web" / "pult.js")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_wizard_saves_before_it_records(self):
        # «Записать и послушать» в мастере: выбор → сохранение → запись, и при
        # ошибке сохранения не пишем, а говорим почему.
        self._запустить()

    def test_the_level_timer_pauses_for_the_probe(self):
        # Проба держит микрофон три секунды: таймер полоски должен встать ДО
        # запроса и вернуться в `finally`, иначе замер с пробой столкнутся.
        скрипт = (config.ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        блок = скрипт.split("async function мастерПроверитьЗвук(")[1].split("\n}\n")[0]
        self.assertLess(блок.index("мастерУровеньВыключить();"),
                        блок.index("/api/audio/test?probe=1"),
                        "таймер уровня должен остановиться до запроса пробы")
        self.assertIn("finally {", блок)
        self.assertIn("мастерШаг === 4", блок)
        self.assertIn("!document.hidden", блок)
        self.assertIn("мастерУровеньВключить(мастерШаги[4]);", блок)

    def test_the_wizard_does_not_reuse_the_settings_check(self):
        # У формы настроек своя кнопка «Сохранить», и мастер её не требует:
        # общая `проверитьЗвук` остаётся только форме.
        скрипт = (config.ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("звукПроверить.addEventListener('click', () => мастерПроверитьЗвук(эл));",
                      скрипт)
        блок = скрипт.split("async function проверитьЗвук(")[1].split("\n}\n")[0]
        self.assertIn("Сначала нажми «Сохранить»", блок)
        self.assertEqual(скрипт.count("function проверитьЗвук("), 1)


if __name__ == "__main__":
    unittest.main()