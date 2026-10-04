"""Качественные голоса в пульте: выбор моделей, кнопка, полоса, Панель.

Отзыв хозяина от 02.10: «почему нет выбора?», «сразу скачивать то, что выбрал
пользователь», и Панель, которая «поползла» от длинного текста загрузки.
Проверяем здесь и тексты `pult.js` (страницу тесты не исполняют), и сами
функции: они вырезаются из `pult.js` целиком и живут в node с подменой сервера,
DOM, таймеров и `window.confirm` (tests/voices_pult.mjs). Ни голоса, ни сети,
ни настоящих моделей тут не нужно.
"""

import shutil
import subprocess
import unittest

import config

PULT = config.ROOT / "ui" / "web" / "pult.js"
ПРОБА = config.ROOT / "tests" / "voices_pult.mjs"


class ПодписиМоделейTests(unittest.TestCase):
    """Цифры в подписях — те, что хозяин спрашивает, а не прежние."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT.read_text(encoding="utf-8")

    def test_размеры_настоящие(self):
        # 9,3 ГБ — сколько качать Higgs, 4,3 ГБ — сколько он занимает на
        # видеокарте, 2,7 ГБ — сколько качать ESpeech.
        for размер in ("9,3 ГБ", "4,3 ГБ", "2,7 ГБ"):
            self.assertIn(размер, self.скрипт, f"в пульте нет размера {размер}")
        # Старый «1,5 ГБ» хозяину врал: модель весит больше.
        self.assertNotIn("1,5 ГБ", self.скрипт,
                         "прежний размер ESpeech в пульте остаться не должен")

    def test_подпись_модели_объясняет_обе_цифры_higgs(self):
        веса = self.скрипт.split("const ВЕСА_ГОЛОСОВ = [")[1].split("\n];\n")[0]
        self.assertIn("скачать 9,3 ГБ, на видеокарте 4,3 ГБ", веса,
                      "две цифры у Higgs — разные вещи, и подпись это говорит")
        self.assertIn("нужна RTX 40 или 50", веса)

    def test_выбор_движка_не_спорит_с_блоком(self):
        # Подписи движков — без цифр (длинные обрезались в закрытом
        # списке), а подсказка ведёт в блок, где цифры правильные. Так одно
        # место не может врать, пока другое говорит правду.
        выбор = self.скрипт.split("const tts_engine = настрВыбор([")[1].split("]);")[0]
        self.assertIn("['higgs', 'Higgs · по образцу']", выбор)
        self.assertIn("['espeech', 'ESpeech · по образцу']", выбор)
        self.assertNotIn("ГБ", выбор)
        self.assertIn("в «Качественных голосах» выше", self.скрипт)

    def test_маршруты_скачивания(self):
        for маршрут in ("/api/voices/download'", "/api/voices/download/cancel",
                        "/api/voices/install", "/api/voices/libs/cancel"):
            self.assertIn(маршрут, self.скрипт)


class СтрокаСостоянияTests(unittest.TestCase):
    """Панель: длинный текст загрузки уходит из строки состояния под карточку."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT.read_text(encoding="utf-8")

    def test_строка_состояния_короткая(self):
        строка = self.скрипт.split("эл.состояние.textContent = панельГолосОшибка")[1]
        строка = строка.split("эл.состояние.classList")[0]
        self.assertIn("'Загружается…'", строка)
        self.assertIn("'Выключается…'", строка)
        # `голосЗагрузка` собирает «Загружается: качаю модель Higgs: …» —
        # такая длинная строка и разъезжала в карточке.
        self.assertNotIn("голосЗагрузка(", строка,
                         "длинный loading_text не должен быть в строке состояния")

    def test_подробность_рисуется_под_карточкой(self):
        self.assertIn("панельГолосПодробности(эл, голос);", self.скрипт)
        блок = self.скрипт.split("function панельГолосПодробности(")[1].split("\n}\n")[0]
        self.assertIn("голос.loading_text", блок)
        # Ничего не идёт — строки нет вовсе, а не пустое место.
        self.assertIn("эл.подробности.hidden = !подробность && !качается && !ставятся;",
                      блок)
        # Под карточкой — обе полосы: и скачивание, и установку библиотек.
        self.assertIn("голосаПолоса(эл.подробностиПолоса, голосаХод);", блок)
        self.assertIn("голосаБибПолоса(эл.подробностиБибПолоса, голосаБибХод);", блок)

    def test_кнопка_голоса_не_прыгает_от_длины_текста(self):
        # «Включить голос» и «Выключить голос» — разная длина, а место одно.
        стили = (config.ROOT / "ui" / "web" / "pult.css").read_text(encoding="utf-8")
        self.assertIn(".панель-голос .голос-кнопка.главная { min-width:",
                      стили.replace("\n", " ").replace("  ", " "))


class ПробаВNodeTests(unittest.TestCase):
    """Проба самих функций пульта в node (tests/voices_pult.mjs)."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")

    def setUp(self):
        if self.node is None:
            self.skipTest("node не установлен")

    def _запустить(self):
        result = subprocess.run([self.node, str(ПРОБА)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_страница_пульта_не_сломана(self):
        # Одна ошибка имени роняет страницу целиком (ГРАБЛИ).
        result = subprocess.run([self.node, "--check", str(PULT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_проба_сама_по_себе_проходит(self):
        self.assertIn("Voices in the pult OK", self._запустить())

    def test_кнопка_шлёт_то_что_выбрано(self):
        # Отдельно от пробы в node: сам факт, что `.mjs` проверяет именно
        # `поставитьГолоса` и все четыре маршрута — иначе проба молча ни о чём.
        скрипт = ПРОБА.read_text(encoding="utf-8")
        for кусок in ("поставитьГолоса", "/api/voices/download",
                      "/api/voices/install", "/api/voices/download/cancel",
                      "/api/voices/libs/cancel"):
            self.assertIn(кусок, скрипт)

    def test_проба_не_трогает_живого(self):
        # Проба обязана быть подменой: никаких настоящих файлов и сети.
        скрипт = ПРОБА.read_text(encoding="utf-8")
        self.assertNotIn("node:fs", скрипт.replace("node:fs/promises", ""))
        self.assertIn("import { readFile } from 'node:fs/promises';", скрипт)


if __name__ == "__main__":
    unittest.main()
