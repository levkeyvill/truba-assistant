"""Поиск с поправкой на распознавание речи: `files.find` нестрогим кругом.

Распознавание коверкает названия: папка `Torrent`, а сказали «Torent» или
«торрент». Обычный круг такое не находит, и тогда срабатывает запасной —
сравнение латиницей с допуском на одну-две правки.

Тест ни по одному настоящему диску не ходит и ни одного окна не открывает:
корни подменены временным деревом, `os.startfile` — заглушкой. Проверяется,
что нестрогий круг ловит коверканье, короткие слова не путает между собой,
а обычные совпадения никогда не уступает «похожим».
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from say_helpers import assert_said
from core import files, hands


class Мир(unittest.TestCase):
    """Временное дерево вместо настоящих папок и дисков.

    Корни подменены (`files._корни`), поэтому обход не может выйти за пределы
    временной папки ни при каких обстоятельствах.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-misheard-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
        self.torrent = self._папка("Torrent")
        self.проекты = self._папка("Проекты")
        патчер = mock.patch.object(files, "_корни", return_value=[self.корень])
        патчер.start()
        self.addCleanup(патчер.stop)
        self.окно = mock.patch.object(os, "startfile")
        self.начатfile = self.окно.start()
        self.addCleanup(self.окно.stop)
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def _папка(self, имя) -> Path:
        папка = self.корень / имя
        папка.mkdir()
        return папка

    def _папки(self, *имена) -> None:
        for имя in имена:
            self._папка(имя)

    def _ответ(self, инструмент, аргументы, события=None):
        """Ответ инструмента разобранным словарём, как его видит мозг."""
        return json.loads(инструмент(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None))


class НестрогийКруг(Мир):
    """`files.find`: коверканное название находится, а обычный круг молчит."""

    def test_одна_буква_минус_у_латинского_имени(self):
        # «Torent» — то же «Torrent», что распознавание потеряло.
        что = files.find("Torent", folders_only=True)
        self.assertEqual(что["found"], [self.torrent])
        self.assertTrue(что["approx"])

    def test_русское_слово_находит_латинское_имя(self):
        # Сказанное по-русски «торрент» и латинская папка `Torrent`.
        что = files.find("торрент", folders_only=True)
        self.assertEqual(что["found"], [self.torrent])
        self.assertTrue(что["approx"])

    def test_латинское_слово_находит_русское_имя(self):
        # Обратная сторона: «Proekty» — это папка «Проекты» кириллицей.
        что = files.find("Proekty", folders_only=True)
        self.assertEqual(что["found"], [self.проекты])
        self.assertTrue(что["approx"])

    def test_короткие_слова_между_собой_не_путаются(self):
        # Правка в коротком слове меняет смысл: «стол» — это не «стул».
        self._папки("Стул", "Стол")
        что = files.find("стол", folders_only=True)
        self.assertEqual(что["found"], [self.корень / "Стол"])
        self.assertFalse(что["approx"])

    def test_точное_совпадение_не_уступает_нестрогому(self):
        # Пока обычный круг что-то нашёл, «похожие» в результат не попадают.
        self._папки("Торренты", "Торрент")
        что = files.find("торрент", folders_only=True)
        self.assertEqual(что["found"], [self.корень / "Торрент"])
        self.assertFalse(что["approx"])

    def test_approx_только_на_нестрогом_круге(self):
        # Точное имя нашлось обычным кругом — признака в ответе нет.
        что = files.find("torrent", folders_only=True)
        self.assertEqual(что["found"], [self.torrent])
        self.assertFalse(что["approx"])

    def test_ничего_похожего_не_придумываем(self):
        # Похожего имени на диске нет — и отвечаем честно, без «найдено».
        что = files.find("калькулятор", folders_only=True)
        self.assertFalse(что["ok"], что)
        self.assertEqual(что["found"], [])
        self.assertFalse(что["approx"])

    def test_файлы_тоже_ищутся_нестрого(self):
        документ = self.корень / "торрент.txt"
        документ.write_text("нет", encoding="utf-8")
        что = files.find("торент")
        self.assertEqual(что["found"], [документ])
        self.assertTrue(что["approx"])


class ОтветИнструмента(Мир):
    """`approx` в ответах инструментов — модели видно, что нашла не буквально."""

    def test_папка_по_названию_помечает_приблизительность(self):
        ответ = self._ответ(hands.run_folder, {"name": "торрент"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(ответ["approx"])
        assert_said(self, ответ["text"], "folder", name="«Torrent»")
        self.начатfile.assert_called_once_with(str(self.torrent))

    def test_файл_помечает_приблизительность(self):
        документ = self.корень / "торрент.txt"
        документ.write_text("нет", encoding="utf-8")
        ответ = self._ответ(hands.run_find_file, {"name": "торент"})
        self.assertTrue(ответ["approx"])
        self.assertEqual(ответ["name"], "торрент.txt")

    def test_при_точном_совпадении_метки_нет(self):
        for инструмент, аргументы in (
                (hands.run_folder, {"name": "torrent"}),
                (hands.run_find_file, {"name": "торрент.txt"})):
            with self.subTest(инструмент=инструмент.__name__):
                документ = self.корень / "торрент.txt"
                документ.write_text("нет", encoding="utf-8")
                ответ = self._ответ(инструмент, аргументы)
                self.assertNotIn("approx", ответ)


if __name__ == "__main__":
    unittest.main()
