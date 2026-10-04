"""Папка по названию: `files.find(folders_only=True)` и `hands.run_folder`.

Живьём 03.10 «открой на диске D папку Torrent» отвечала отказом: инструмент
знал только стандартные папки. Теперь любую папку можно назвать — ищет тот же
обход, что и файлы.

Тест ни по одному настоящему диску не ходит и ни одного окна не открывает:
корни поиска подменены временным деревом, `os.startfile` — заглушкой. Проверяется
разделение «файлы / папки», открытие одной, отказ при нескольких равных и при
отсутствии, работа старого вызова со стандартной папкой и объявление инструмента.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from say_helpers import assert_said
from core import files, folders, hands, screen


class Мир(unittest.TestCase):
    """Временное дерево вместо настоящих папок и дисков.

    Корни подменены (`files._корни`), поэтому обход не может выйти за пределы
    временной папки ни при каких обстоятельствах.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-folder-name-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
        self.загрузки = self.корень / "Загрузки"
        self.загрузки.mkdir()
        self.torrent = self.корень / "Torrent"
        self.torrent.mkdir()
        self.документы = self.корень / "Документы"
        self.документы.mkdir()
        # Второй «диск» — под ним и кладётся вторая папка с тем же именем.
        self.второй = self.корень / "C"
        self.второй.mkdir()

        патчер = mock.patch.object(files, "_корни", return_value=[self.корень])
        патчер.start()
        self.addCleanup(патчер.stop)
        self.окно = mock.patch.object(os, "startfile")
        self.начатfile = self.окно.start()
        self.addCleanup(self.окно.stop)
        # Пометка «уже сделала» живёт в процессе: без сброса второй вызов
        # подряд получил бы отказ вместо папки.
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def _второй_torrent(self) -> Path:
        """Вторая «Torrent» — на другом диске, для проверки выбора."""
        папка = self.второй / "Torrent"
        папка.mkdir()
        return папка

    def _ответ(self, аргументы, события=None):
        """Ответ инструмента разобранным словарём, как его видит мозг."""
        return json.loads(hands.run_folder(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None))


class ПоискПапок(Мир):
    """`files.find(folders_only=True)`: папки вместо файлов."""

    def test_папка_находится_по_своему_названию(self):
        что = files.find("torrent", folders_only=True)
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["found"], [self.torrent])

    def test_торрент_находит_латинскую_папку(self):
        # Сказанное по-русски «торрент» — это латинская папка `Torrent`:
        # слова сравниваются латиницей, когда точного совпадения не было.
        что = files.find("торрент", folders_only=True)
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["found"], [self.torrent])
        self.assertTrue(что["approx"])

    def test_файл_с_тем_же_именем_не_попадает(self):
        # Поиск папок не должен отдавать файл: открывать надо то, что просили.
        одно = self.загрузки / "Torrent.txt"
        одно.write_text("не папка", encoding="utf-8")
        что = files.find("torrent", folders_only=True)
        self.assertNotIn(одно, что["found"])
        # А в поиске файлов наоборот — папки там нет.
        self.assertEqual(files.find("torrent")["found"], [одно])

    def test_без_флага_поиск_по_прежнему_про_файлы(self):
        # Обратная сторона разделения: папку `отчёт` файловый поиск не находит.
        (self.документы / "отчёт").mkdir()
        документ = self.документы / "отчёт.docx"
        документ.write_text("отчёт за сентябрь", encoding="utf-8")
        что = files.find("отчёт")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["found"], [документ])
        self.assertNotIn(self.документы / "отчёт", что["found"])

    def test_обе_одинаковые_папки_попадают_в_результат(self):
        вторая = self._второй_torrent()
        что = files.find("torrent", folders_only=True)
        self.assertEqual(set(что["found"]), {self.torrent, вторая})


class ОткрытиеПоНазванию(Мир):
    """`hands.run_folder` с `name`: одна папка — открыта, выбор — хозяину."""

    def test_одна_папка_открыта(self):
        # Поиск подменён: на диске D нашлась единственная «Torrent».
        найдено = {"ok": True, "found": [self.torrent], "why": "",
                   "searched_all": True}
        with mock.patch.object(files, "find", return_value=найдено) as поиск:
            события = []
            ответ = self._ответ({"name": "Torrent", "drive": "D"}, события)
        self.assertTrue(ответ["ok"], ответ)
        assert_said(self, ответ["text"], "folder", name="«Torrent»")
        # Поиск шёл именно за папками и на названном диске.
        self.assertEqual(поиск.call_args.args, ("Torrent", "D"))
        self.assertTrue(поиск.call_args.kwargs["folders_only"])
        self.начатfile.assert_called_once_with(str(self.torrent))
        self.assertEqual(события, [("folder", "Torrent")])

    def test_две_равные_не_открываем_а_спрашиваем(self):
        # Одинаково названы — открывать наугад нельзя, выбирает хозяин.
        найдено = {"ok": True,
                   "found": [self.torrent, self._второй_torrent()],
                   "why": "", "searched_all": True}
        with mock.patch.object(files, "find", return_value=найдено):
            ответ = self._ответ({"name": "Torrent"})
        self.assertFalse(ответ["ok"], ответ)
        self.assertIn("какую открыть", ответ["text"])
        self.assertEqual([одна["name"] for одна in ответ["candidates"]],
                         ["Torrent", "Torrent"])
        # В облако пути целиком не уходят: в них имя пользователя Windows.
        self.assertNotIn(str(self.корень),
                         json.dumps(ответ, ensure_ascii=False))
        self.начатfile.assert_not_called()

    def test_при_выборе_второй_круг_нужен(self):
        # Голосу сказать нечего — вопрос задаст модель, поэтому `confirm` молчит.
        зов = [{"name": hands.FOLDER_NAME,
                "args": json.dumps({"name": "Torrent"}, ensure_ascii=False)}]
        несколько = json.dumps(
            {"ok": False, "candidates": [{"name": "Torrent", "folder": "D"}],
             "text": "Нашлось несколько папок «Torrent» — какую открыть?"},
            ensure_ascii=False)
        одна = json.dumps(
            {"ok": True, "name": "Torrent", "text": "Открыла папку «Torrent»."},
            ensure_ascii=False)
        self.assertEqual(hands.confirm(зов, results=[одна]),
                         "Открыла папку «Torrent».")
        self.assertEqual(hands.confirm(зов, results=[несколько]), "")


class ЧегоНеНашлось(Мир):
    """Отказ вместо открытия наугад."""

    def test_ни_одной_папки_честно_сказано(self):
        найдено = {"ok": False, "found": [],
                   "why": "не нашла папки про «Torrent»", "searched_all": True}
        with mock.patch.object(files, "find", return_value=найдено):
            ответ = self._ответ({"name": "Torrent"})
        self.assertIn("не нашла", ответ["error"])
        self.начатfile.assert_not_called()

    def test_без_имени_просим_назвать(self):
        # Молча открывать наугад нельзя: что открывать, никто не сказал.
        ответ = self._ответ({"drive": "D"})
        self.assertIn("не названо", ответ["error"])
        self.начатfile.assert_not_called()

    def test_ошибка_поиска_не_роняет_разговор(self):
        сбой = mock.patch.object(files, "find",
                                 side_effect=OSError("диск отвалился"))
        with сбой:
            ответ = self._ответ({"name": "Torrent"})
        self.assertIn("не искала папку", ответ["error"])
        self.начатfile.assert_not_called()


class СловаСудьи(Мир):
    """`action_words`: судье нужны слова, а не перечисление id."""

    def test_название_и_диск_слышны_судье(self):
        self.assertEqual(
            hands.action_words(hands.FOLDER_NAME,
                               {"name": "Torrent", "drive": "D"}),
            "открыть папку «Torrent» на диске D")
        self.assertEqual(
            hands.action_words(hands.FOLDER_NAME, {"name": "Torrent"}),
            "открыть папку «Torrent»")

    def test_стандартная_папка_по_прежнему_титулом(self):
        self.assertEqual(
            hands.action_words(hands.FOLDER_NAME, {"folder": "downloads"}),
            "открыть папку «Загрузки»")


class СтараяПапка(Мир):
    """Стандартные папки не должны пострадать от папок по названию."""

    def test_стандартная_папка_открывается_как_раньше(self):
        with mock.patch.object(folders, "path_of", return_value=self.загрузки), \
                mock.patch.object(screen, "known_folder",
                                  return_value=self.загрузки):
            ответ = json.loads(hands.run_folder(
                json.dumps({"folder": "downloads",
                            "because": "открыть загрузки"}), None))
        self.assertTrue(ответ["ok"], ответ)
        assert_said(self, ответ["text"], "folder", name="загрузки")
        self.assertNotIn("path", ответ)
        self.начатfile.assert_called_once_with(str(self.загрузки))


class Объявление(unittest.TestCase):
    """Схема `FOLDER_TOOL`: `folder` больше не обязателен."""

    def setUp(self):
        self.параметры = hands.FOLDER_TOOL["function"]["parameters"]
        self.свойства = self.параметры["properties"]

    def test_папка_не_обязательна(self):
        self.assertNotIn("folder", self.параметры["required"])
        self.assertIn("because", self.параметры["required"])

    def test_есть_название_и_диск(self):
        self.assertEqual(self.свойства["name"]["type"], "string")
        self.assertEqual(self.свойства["drive"]["enum"],
                         [""] + list(hands.DRIVE_LETTERS))

    def test_в_enum_стандартных_папок_есть_пусто(self):
        # Пустая строка — «папки нет в списке, ищи по названию»: без неё модель
        # вынуждена была бы выдумывать id.
        self.assertEqual(self.свойства["folder"]["enum"],
                         [""] + [одна["id"] for одна in folders.KNOWN])


if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()
