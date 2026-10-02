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
                        "/api/audio/test/play",
                        "/api/hardware", "/api/voices/status", "/api/voices/install",
                        "/api/weather/find?q="):
            self.assertIn(маршрут, self.скрипт)

    def test_запись_и_прослушивание_разными_кнопками(self):
        # Кнопка одна («записать и послушать») проигрывала запись один раз мимо
        # хозяина, и проверить звук было нечем. Теперь их две.
        форма = self.скрипт.split("function построитьНастройки(")[1].split("\n}\n")[0]
        self.assertIn("звукПроверить.textContent = 'Записать 3 секунды'", форма)
        self.assertIn("звукПрослушать.textContent = 'Прослушать'", форма)
        self.assertIn("звукПрослушать.disabled = true", форма)
        self.assertIn("звукПрослушать, звукЗапись, звукСтатус", форма)
        self.assertNotIn("Записать и послушать", self.скрипт)

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

    def test_качественные_голоса_спрашивают_перед_установкой(self):
        """Пока библиотек нет, пульт спрашивает разрешение — и говорит чем платим.

        Текст изменился вместе с самой установкой: библиотеки теперь ставятся
        прямо в пульте, с полосой, и пульт может один раз перезапуститься «без
        голоса». Проверяем смысл: вопрос задаётся ДО запроса, сказано, что
        установка идёт прямо здесь с полосой, что пульт, возможно, один раз
        перезапустится, и что выбранные модели скачаются потом.
        """
        блок = self.скрипт.split("async function поставитьГолоса(")[1].split("\n}\n")[0]
        self.assertIn("window.confirm(", блок, "перед установкой спросим разрешение")
        # Вопрос — до запроса, а не после: иначе пульт уже начал ставить.
        self.assertLess(блок.index("window.confirm("), блок.index("await fetch("),
                        "сначала спросим хозяина, потом шлём запрос")
        self.assertIn("прямо здесь, с полосой", блок,
                      "хозяин должен знать, что установка идёт в этом же пульте")
        self.assertIn("перезапустится секунд на десять", блок,
                      "про возможный перезапуск пульта сказано прямо")
        self.assertIn("Потом скачаются выбранные модели", блок,
                      "скачивание моделей будет после установки — и это сказано")

    def test_отмена_установки_идёт_на_свой_маршрут(self):
        # Отмена скачивания и отмена установки — разные команды на сервере:
        # гасить надо именно тот процесс, который идёт.
        self.assertIn("/api/voices/libs/cancel", self.скрипт)


if __name__ == "__main__":
    unittest.main()
