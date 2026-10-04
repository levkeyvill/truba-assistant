"""Меню пульта листается в низком окне (хозяин 28.09): пункты не пропадают."""

import unittest
from pathlib import Path

import config

WEB = Path(config.ROOT) / "ui" / "web"


class МенюЛистаетсяTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.разметка = (WEB / "pult.html").read_text(encoding="utf-8")
        cls.стили = (WEB / "pult.css").read_text(encoding="utf-8")
        cls.скрипт = (WEB / "pult.js").read_text(encoding="utf-8")

    def test_пункты_в_полосе_между_шапкой_и_низом(self):
        список = self.разметка.index('id="меню-список"')
        self.assertLess(self.разметка.index('class="шапка"'), список)
        self.assertLess(список, self.разметка.index('data-раздел="панель"'))
        self.assertLess(self.разметка.index('data-раздел="программа"'),
                        self.разметка.index('id="меню-вниз"'))
        self.assertLess(self.разметка.index('id="меню-вниз"'),
                        self.разметка.index('class="низ"'))

    def test_полоса_листается_а_не_растягивает(self):
        self.assertIn("overflow-y: auto;", self.стили)
        self.assertIn("min-height: 0;", self.стили)

    def test_кнопки_видны_только_когда_есть_куда(self):
        self.assertIn("менюВверх.hidden = !выше;", self.скрипт)
        self.assertIn("менюВниз.hidden = !ниже;", self.скрипт)

    def test_раскрытое_подменю_показывается(self):
        self.assertGreaterEqual(self.скрипт.count("менюПоказатьРаскрытое();"), 2)
        # Листаем только полосу: scrollIntoView сдвинул бы и страницу.
        блок = self.скрипт[self.скрипт.index("function менюПоказатьРаскрытое"):]
        блок = блок[:блок.index("\nfunction ")] if "\nfunction " in блок else блок
        self.assertNotIn(".scrollIntoView(", блок)


if __name__ == "__main__":
    unittest.main()
