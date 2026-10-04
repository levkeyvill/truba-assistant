"""«Озвучивание»: «Твой компьютер» наверху, новый голос по шагам, значок темы.

Тесты проверяют имена элементов и наличие вызываемых функций: опечатка в
них ломает страницу целиком.
"""

import re
import unittest
from pathlib import Path

import config

WEB = Path(config.ROOT) / "ui" / "web"


class ОзвучиваниеTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.скрипт = (WEB / "pult.js").read_text(encoding="utf-8")
        cls.разметка = (WEB / "pult.html").read_text(encoding="utf-8")
        cls.стили = (WEB / "pult.css").read_text(encoding="utf-8")

    def test_твой_компьютер_раньше_способа_озвучивания(self):
        секция = self.скрипт.index("const голосСекция = настрСекция(")
        карточка = self.скрипт.index("голосСекция.appendChild(железоКарточка)", секция)
        способ = self.скрипт.index("const tts_engine = настрВыбор(", секция)
        self.assertLess(карточка, способ)

    def test_новый_голос_по_шагам(self):
        for кусок in ("'Выбери запись'", "'Кусок речи'", "'Добавить'",
                      "'Найти чистый кусок сама'", "'Вырезать вручную'"):
            self.assertIn(кусок, self.скрипт)
        self.assertIn("const образецПоляОбновить = () => {", self.скрипт)

    def test_согласие_на_голос(self):
        self.assertIn("Только свой голос или голос человека, который разрешил", self.скрипт)

    def test_значок_темы_в_шапке(self):
        справа = self.разметка[self.разметка.index('class="шапка-справа"'):]
        self.assertLess(справа.index('id="тема-значок"'), справа.index('id="связь"'))
        self.assertIn("$('тема-значок').addEventListener('click', темаЗначокПереключить);",
                      self.скрипт)
        self.assertIn("async function темаЗначокПереключить()", self.скрипт)

    def test_нет_имён_через_е_вместо_ё(self):
        # Объявлено «тёмная» — «темная» без точек роняло страницу.
        self.assertIsNone(re.search(r"(?<![\wа-яё])темная(?![\wа-яё])", self.скрипт))

    def test_узкая_форма_не_сжимает_поля(self):
        self.assertIn("container-type: inline-size", self.стили)
        self.assertIn("@container (max-width: 680px)", self.стили)


if __name__ == "__main__":
    unittest.main()
