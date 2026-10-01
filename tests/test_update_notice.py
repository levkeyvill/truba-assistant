"""Уведомление о новой версии: плашка в пульте по результату проверки.

Живой пульт не поднимаем и в GitHub не ходим. Сценарии разбирает
`update_notice.mjs`: он вырезает из `pult.js` ровно те функции, что решают
судьбу плашки, и кормит их подменённым `/api/about` (поздний результат —
со второй попытки). Проверяем две вещи из отзыва хозяина: позднее
завершение проверки действительно показывается и ложного «есть обновление»
при ошибке или выключенной проверке не бывает.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

import config

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
СЦЕНАРИЙ = Path(config.ROOT) / "tests" / "update_notice.mjs"


class УведомлениеОбОбновленииTests(unittest.TestCase):
    """Плашка «вышла версия N» и запрет на ложное уведомление."""

    @classmethod
    def setUpClass(cls):
        cls.узел = shutil.which("node")
        if not cls.узел:
            raise unittest.SkipTest("node не найден")

    def _запустить(self) -> subprocess.CompletedProcess:
        # node печатает по-русски в UTF-8, а консоль Windows по умолчанию
        # отдаёт cp1251: кодировку задаём явно, иначе текст разъезжается.
        return subprocess.run(
            [self.узел, str(СЦЕНАРИЙ)], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(СЦЕНАРИЙ.parent.parent), timeout=120,
        )

    def test_late_result_is_shown_and_false_alarm_is_not(self):
        # node сам разбирает восемь сценариев: поздний результат, пульт после
        # проверки, latest, ошибка, выключенная проверка, нет связи, потолок
        # попыток и кнопка «О программе». Любой провал assertion виден здесь.
        итог = self._запустить()
        self.assertEqual(итог.returncode, 0, итог.stdout + итог.stderr)
        self.assertIn("8 сценариев пройдено", итог.stdout)

    def test_pult_script_still_parses(self):
        # `node --check` ловит опечатку, которую сценарии могли не задеть.
        итог = subprocess.run([self.узел, "--check", str(PULT_JS)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(итог.returncode, 0, итог.stderr)

    def test_no_install_is_forced_by_the_notice(self):
        # Плашка ведёт в «О программе» и ничего не ставит: установка и
        # перезапуск остаются на кнопках самой страницы.
        скрипт = PULT_JS.read_text(encoding="utf-8")
        начало = скрипт.index("function обнПоказатьПлашку")
        блок = скрипт[начало:скрипт.index("\n}\n", начало)]
        self.assertIn("открыть('программа')", блок)
        for запрещённое in ("/api/update/install", "перезапуститьПульт",
                            "поставитьОбновление"):
            self.assertNotIn(запрещённое, блок)


if __name__ == "__main__":
    unittest.main()
