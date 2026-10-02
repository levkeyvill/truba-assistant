"""Перенос настроек в пульте: раздел «Система» и первый шаг мастера.

Хозяин (02.10): «человек удалил Трубу, потом решил заново скачать — чтобы заново
не настраивать». Часть 1 (файл экспорта и импорт, `core/transfer.py` и
маршруты `/api/transfer/*`) сделана сервером; здесь проверяется пульт: кнопки,
галочки частей, спрятанный выбор файла, вопрос перед заменой настроек и то,
что мастер зовёт тот же импорт. Проверяем и тексты `pult.js` (страницу теста
не исполняют), и сами функции: они вырезаются из `pult.js` целиком и живут в
node с подменой сервера, DOM, файлового выбора и `window.confirm`
(tests/transfer_pult.mjs). Ни диска, ни сети, ни настоящей Трубы тут не нужно.
"""

import shutil
import subprocess
import unittest

import config

PULT = config.ROOT / "ui" / "web" / "pult.js"
ПРОБА = config.ROOT / "tests" / "transfer_pult.mjs"


class ПереносВПультеTests(unittest.TestCase):
    """Тексты и маршруты в самом `pult.js`."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT.read_text(encoding="utf-8")

    def test_пояснение_и_кнопки_в_разделе_система(self):
        форма = self.скрипт.split("function построитьНастройки(")[1].split("\n}\n")[0]
        self.assertIn("переносБлок(система, 'Перенос настроек'", форма)
        self.assertIn("переносЧастиЗагрузить(перенос);", форма)
        # Пояснение — одной строкой, без стрелок (правило проекта: стрелка
        # живёт только в путях меню, как у соседнего «Голос → Звук»).
        self.assertIn("ничего не настраивая заново", форма)
        блок = self.скрипт.split("function переносБлок(")[1].split("\n}\n")[0]
        self.assertNotIn("→", блок)

    def test_ключи_предупреждают_человека(self):
        блок = self.скрипт.split("function переносГалочки(")[1].split("\n}\n")[0]
        self.assertIn("в файле будет ключ доступа к облаку — никому его не отправляй",
                      блок)

    def test_вопрос_перед_заменой_настроек(self):
        блок = self.скрипт.split("function переносПрименить(")[1].split("\n}\n")[0]
        self.assertIn("Текущие настройки будут заменены (копия сохранится). ", блок)
        self.assertIn("Пульт перезапустится. Продолжить?", блок)
        self.assertIn("Переношу… пульт перезапустится", блок)

    def test_файл_уходит_в_разбор_телом(self):
        блок = self.скрипт.split("async function переносИмпорт(")[1].split("\n}\n")[0]
        self.assertIn("'/api/transfer/inspect'", блок)
        self.assertIn("'Content-Type': 'application/zip'", блок)
        self.assertIn("body: файл,", блок)

    def test_маршруты_переноса(self):
        for маршрут in ("/api/transfer/parts", "/api/transfer/export",
                        "/api/transfer/reveal", "/api/transfer/inspect",
                        "/api/transfer/apply"):
            self.assertIn(маршрут, self.скрипт)

    def test_ошибки_под_кнопками_и_кнопки_снова_доступны(self):
        # Текст под кнопками с классом `плохо` — как у остальных строк пульта.
        self.assertIn("function переносСказать(", self.скрипт)
        self.assertIn("classList.toggle('плохо', !!плохо)", self.скрипт)
        ждёт = self.скрипт.split("function переносЖдёт(")[1].split("\n}\n")[0]
        self.assertIn("кнопка.disabled = !!ждёт;", ждёт)

    def test_выбор_файла_спрятан_как_у_образца_голоса(self):
        блок = self.скрипт.split("function переносБлок(")[1].split("\n}\n")[0]
        self.assertIn("файл.accept = '.zip';", блок)
        self.assertIn("файл.className = 'образец-файл';", блок)
        self.assertIn("файл.addEventListener('change', () => переносИмпорт(эл));", блок)

    def test_мастер_показывает_тот_же_импорт(self):
        шаг = self.скрипт.split("function мастерШагПривет(")[1].split("\n}\n")[0]
        self.assertIn("Уже пользовался Трубой? Перенеси настройки из файла", шаг)
        self.assertIn("переносБлок(переносМесто, 'Перенос настроек'", шаг)
        # В мастере сохранять нечего: экспорта там нет.
        self.assertIn("ничего не настраивая заново", self.скрипт)


class ПробаВNodeTests(unittest.TestCase):
    """Проба самих функций пульта в node (tests/transfer_pult.mjs)."""

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
        self.assertIn("Transfer in the pult OK", self._запустить())

    def test_проба_проверяет_нужное(self):
        # Отдельно от пробы в node: сам факт, что `.mjs` дёргает именно эти
        # маршруты и оба места, — иначе проба молча ни о чём.
        скрипт = ПРОБА.read_text(encoding="utf-8")
        for кусок in ("/api/transfer/parts", "/api/transfer/export",
                      "/api/transfer/reveal", "/api/transfer/inspect",
                      "/api/transfer/apply", "переносИмпорт", "переносПрименить",
                      "мастерШагПривет"):
            self.assertIn(кусок, скрипт)

    def test_проба_не_трогает_живого(self):
        # Проба обязана быть подменой: никаких настоящих файлов и сети.
        скрипт = ПРОБА.read_text(encoding="utf-8")
        self.assertNotIn("node:fs", скрипт.replace("node:fs/promises", ""))
        self.assertIn("import { readFile } from 'node:fs/promises';", скрипт)


if __name__ == "__main__":
    unittest.main()