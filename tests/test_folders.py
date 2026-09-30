"""Папки: стандартные (`core/folders.py`) и свои (`launcher`, `hands`).

Ни одного настоящего окна: `os.startfile` подменён, Known Folders — тоже, а
сами папки лежат во временной папке. Проверяется три вещи: слова узнаются
(`find`), разбор фразы не съедает запуск программы (`commands.understand`)
и своей папки хозяина не выдумывается там, где её нет.

Путь в ответе инструмента в облако не уходит: в нём имя пользователя
Windows, модели он ни к чему. Это тоже здесь проверяется.
"""

import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from core import commands, folders, hands, launcher, notes, screen
from ui.web_runtime import WebRuntime

# Список программ хозяина: одна программа и одна своя папка. Именно с ним
# разбираются фразы «открой дискорд» и «открой проект».
APPS = [
    {"id": "discord", "title": "Discord", "kind": "app"},
    {"id": "proj", "title": "Проект", "kind": "folder", "path": r"C:\x"},
]


class СловаПапок(unittest.TestCase):
    """`find`: как папку называют вслух."""

    def test_каждое_слово_находит_свою_папку(self):
        for слова, ожидаем in (
            ("загрузки", "downloads"),
            ("закачки", "downloads"),
            ("скачанное", "downloads"),
            ("документы", "documents"),
            ("рабочий стол", "desktop"),
            ("картинки", "pictures"),
            ("фотки", "pictures"),
            ("музыка", "music"),
            ("видео", "videos"),
            ("заметки", "notes"),
            ("мои заметки", "notes"),
        ):
            with self.subTest(слова=слова):
                self.assertEqual(folders.find(слова), ожидаем)

    def test_столовая_не_рабочий_стол(self):
        # «стол» ⊂ «столовая», но столовой у Windows нет: короткие слова
        # сравниваются целиком, иначе «открой столовую» открыла бы стол.
        self.assertIsNone(folders.find("столовая"))

    def test_чужая_папка_не_наша(self):
        # Свои папки хозяина лежат в apps.json, а не в Known Folders.
        self.assertIsNone(folders.find("проект"))



class ПутьПапки(unittest.TestCase):
    """`path_of`: спрашиваем у Windows, заметки — у `core.notes`."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-folders-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.загрузки = self.папка / "Загрузки"
        self.загрузки.mkdir()

    def test_путь_спрашивается_у_windows(self):
        спрошено = []

        def известная(folder_id, fallback):
            спрошено.append(folder_id)
            return self.загрузки

        with mock.patch.object(screen, "known_folder", side_effect=известная):
            self.assertEqual(folders.path_of("downloads"), self.загрузки)
        self.assertEqual(спрошено, [folders.FOLDERID_DOWNLOADS])

    def test_папка_заметок_берётся_у_notes(self):
        # Хозяин меняет папку заметок в пульте, поэтому её читают на каждый
        # вызов, а не один раз при запуске.
        свои = self.папка / "Мои заметки"
        with mock.patch.object(notes, "root", return_value=свои) as корень:
            self.assertEqual(folders.path_of("notes"), свои)
        корень.assert_called_once_with()

    def test_неизвестный_id_пути_не_имеет(self):
        with mock.patch.object(screen, "known_folder") as спросили:
            self.assertIsNone(folders.path_of("проект"))
        спросили.assert_not_called()


class ОткрытиеПапки(unittest.TestCase):
    """`open_folder`: успех, папки нет, незнакомый id — и ни одного окна."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-folders-open-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.загрузки = self.папка / "Downloads"
        self.загрузки.mkdir()
        # Подмены точечные: `patch.stopall` останавливал бы ещё и чужие
        # подстановки других тестов, идущих после этого.
        окно = mock.patch.object(os, "startfile")
        self.начатfile = окно.start()
        self.addCleanup(окно.stop)
        подмена = mock.patch.object(
            screen, "known_folder",
            side_effect=lambda folder_id, fallback: self.загрузки)
        подмена.start()
        self.addCleanup(подмена.stop)

    def test_папка_открыта_и_сказано_по_русски(self):
        что = folders.open_folder("downloads")
        self.assertTrue(что["ok"], что)
        self.начатfile.assert_called_once_with(str(self.загрузки))
        self.assertEqual(что["text"], "Открыла загрузки.")
        self.assertEqual(что["path"], str(self.загрузки))

    def test_папки_нет_на_диске(self):
        # Known Folders ответил, а папки там нет: перенесли или удалили.
        нет = self.папка / "нет-такой"
        with mock.patch.object(screen, "known_folder", return_value=нет):
            что = folders.open_folder("downloads")
        self.assertFalse(что["ok"])
        self.assertIn("нет", что["text"])
        self.начатfile.assert_not_called()

    def test_неизвестный_id_ничего_не_открывает(self):
        что = folders.open_folder("проект")
        self.assertFalse(что["ok"])
        self.начатfile.assert_not_called()



class РазборФраз(unittest.TestCase):
    """`commands.understand`: папка раньше запуска, но не в ущерб ему."""

    def test_стандартные_папки_это_команда_folder(self):
        for фраза, ожидаем in (
            ("открой загрузки", "downloads"),
            ("открой папку загрузки", "downloads"),
            ("открой рабочий стол", "desktop"),
            ("открой мои заметки", "notes"),
        ):
            with self.subTest(фраза=фраза):
                порядок = commands.understand(фраза, APPS)
                self.assertIsNotNone(порядок, фраза)
                self.assertEqual((порядок.action, порядок.target),
                                 ("folder", ожидаем))

    def test_программа_по_прежнему_launch(self):
        # Ключевое: строка `folder` не должна была сломать запуск.
        порядок = commands.understand("открой дискорд", APPS)
        self.assertEqual((порядок.action, порядок.target), ("launch", "discord"))

    def test_своя_папка_из_списка_это_launch(self):
        порядок = commands.understand("открой проект", APPS)
        self.assertEqual((порядок.action, порядок.target), ("launch", "proj"))

    def test_папка_с_проектом_не_стандартная(self):
        # «Проект» — не Known Folder. Строка `folder` обязана отдать фразу
        # дальше, а не открыть наугад.
        порядок = commands.understand("открой папку с проектом", APPS)
        self.assertIsNotNone(порядок)
        self.assertNotEqual(порядок.action, "folder")
        self.assertEqual(порядок.target, "proj")


class СвояПапкаХозяина(unittest.TestCase):
    """`launcher`: вид `folder` в apps.json — запуск, но не закрытие."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-folders-launch-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.проект = self.папка / "Проект"
        self.проект.mkdir()

    def _список(self, *записи):
        подмена = mock.patch.object(launcher, "read_list",
                                    return_value=list(записи))
        подмена.start()
        self.addCleanup(подмена.stop)

    def _запись(self, **поле):
        основа = {"id": "proj", "title": "Проект", "kind": "folder",
                  "path": str(self.проект)}
        основа.update(поле)
        return основа

    def test_папка_с_путем_попадает_в_кнопки(self):
        # Кнопка остаётся, даже если папки нет: иначе она молча исчезла бы
        # из настроек, и хозяин не понял бы, куда делась его запись.
        self._список(self._запись())
        кнопки = launcher.load()
        self.assertEqual([к["id"] for к in кнопки], ["proj"])
        self.assertEqual(кнопки[0]["kind"], "folder")
        self.assertEqual(кнопки[0]["path"], str(self.проект))

    def test_папка_без_пути_в_кнопки_не_попадает(self):
        self._список(self._запись(path=""),
                     self._запись(id="proj2", title="Проект 2", path="   "))
        self.assertEqual(launcher.load(), [])

    def test_запуск_зовёт_startfile_с_путём(self):
        self._список(self._запись())
        with mock.patch.object(os, "startfile") as начатfile:
            получилось, что = launcher.launch("proj")
        self.assertTrue(получилось, что)
        self.assertEqual(что, "Проект")
        начатfile.assert_called_once_with(str(self.проект))

    def test_нет_такой_папки_честно_сказано(self):
        self._список(self._запись(path=str(self.папка / "нет-такой")))
        with mock.patch.object(os, "startfile") as начатfile:
            получилось, что = launcher.launch("proj")
        self.assertFalse(получилось)
        self.assertIn("нет", что)
        начатfile.assert_not_called()



class РукиПапок(unittest.TestCase):
    """`hands`: инструмент `open_folder` — объявление и раздача."""

    def setUp(self):
        # Пометка «уже сделала» живёт в процессе: без сброса второй вызов
        # подряд получил бы отказ вместо папки.
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def _папка(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-folders-hands-"))
        self.addCleanup(shutil.rmtree, папка, True)
        return папка

    def test_инструмент_есть_во_всех_трёх_наборах(self):
        self.assertIn(hands.FOLDER_NAME, hands.LOCAL)
        self.assertIn(hands.FOLDER_NAME, hands.GUARDED)
        # Открытая папка — это окно на экране, ровно как у запуска программы.
        self.assertIn(hands.FOLDER_NAME, hands.JUDGED)

    def test_because_обязателен(self):
        параметры = hands.FOLDER_TOOL["function"]["parameters"]
        self.assertIn("because", параметры["required"])
        self.assertIn("because", параметры["properties"])

    def test_enum_из_стандартных_папок(self):
        свойства = hands.FOLDER_TOOL["function"]["parameters"]["properties"]
        self.assertEqual(свойства["folder"]["enum"],
                         [одна["id"] for одна in folders.KNOWN])

    def test_кривые_аргументы_дают_ошибку(self):
        for аргументы in ("не json", "[]", ""):
            with self.subTest(аргументы=аргументы):
                ответ = json.loads(hands.run_folder(аргументы, None))
                self.assertIn("error", ответ)

    def test_успех_без_пути_и_с_русским_названием(self):
        # Путь в ответе был бы в облаке, а в нём имя пользователя Windows.
        папка = self._папка()
        with mock.patch.object(os, "startfile"), \
                mock.patch.object(screen, "known_folder", return_value=папка):
            ответ = json.loads(hands.run_folder(
                json.dumps({"folder": "downloads", "because": "открыть загрузки"}),
                None))
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["text"], "Открыла загрузки.")
        self.assertNotIn("path", ответ)
        self.assertNotIn(str(папка), json.dumps(ответ, ensure_ascii=False))

    def test_не_открылось_даёт_ошибку(self):
        папка = self._папка()
        with mock.patch.object(os, "startfile") as начатfile, \
                mock.patch.object(screen, "known_folder",
                                  return_value=папка / "нет-такой"):
            ответ = json.loads(hands.run_folder('{"folder": "downloads"}', None))
        self.assertIn("error", ответ)
        начатfile.assert_not_called()

    def test_судья_слышит_папку_по_русски(self):
        # Судья не видит перечисления id — только слова.
        слова = hands.action_words(hands.FOLDER_NAME, {"folder": "downloads"})
        self.assertEqual(слова, "открыть папку «Загрузки»")
        self.assertNotIn("downloads", слова)

    def test_описание_запуска_знает_о_своих_папках(self):
        с_папкой = hands.tool(APPS)["function"]["description"]
        self.assertIn("программу или папку", с_папкой)
        без_папки = hands.tool(APPS[:1])["function"]["description"]
        self.assertIn("программу", без_папки)
        self.assertNotIn("или папку", без_папки)

    def test_подтверждение_берётся_из_ответа(self):
        # Второй круг в облако ушёл бы только ради слова «открыла».
        self.assertEqual(hands.CONFIRM[hands.FOLDER_NAME], ("{text}",))


class ПроверкаВПульте(unittest.TestCase):
    """`WebRuntime.apps_save`: у папки путь обязателен, как у программы."""

    def _сохранить(self, items):
        среда = WebRuntime.__new__(WebRuntime)
        среда.voice = types.SimpleNamespace(_apps=None)
        среда._bg = mock.Mock()
        записано = {}
        with mock.patch.object(launcher, "read_list", return_value=[]), \
                mock.patch.object(launcher, "save_list",
                                  side_effect=lambda список: записано.setdefault(
                                      "список", список)):
            итог = среда.apps_save(items)
        return итог, записано.get("список")

    def test_папка_без_пути_не_сохраняется(self):
        итог, список = self._сохранить(
            [{"id": "proj", "title": "Проект", "kind": "folder"}])
        self.assertFalse(итог["ok"])
        self.assertIn("путь", итог["error"])
        self.assertIsNone(список)

    def test_папка_с_путем_сохраняется(self):
        итог, список = self._сохранить(
            [{"id": "proj", "title": "Проект", "kind": "folder", "path": r"C:\x"}])
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(список[0]["path"], r"C:\x")
        self.assertEqual(список[0]["kind"], "folder")


class DriveTests(unittest.TestCase):
    """«Открой диск D» (хозяин, 30.09): диски — из системы, не из списка."""

    def drives(self, letters, types_by_letter=None):
        маска = sum(1 << (ord(б) - ord("A")) for б in letters)
        типы = types_by_letter or {}
        kernel = types.SimpleNamespace(
            GetLogicalDrives=lambda: маска,
            GetDriveTypeW=lambda корень: типы.get(корень[0], 3))
        with mock.patch("ctypes.windll", types.SimpleNamespace(kernel32=kernel)):
            return folders._drives()

    def test_present_drives_become_folders(self):
        найдено = self.drives("CD")
        self.assertEqual([d["id"] for d in найдено], ["drive_c", "drive_d"])
        self.assertEqual(найдено[1]["path"], "D:\\")
        self.assertIn("диск дэ", найдено[1]["words"])
        self.assertIn("диск d", найдено[1]["words"])

    def test_cdrom_is_skipped(self):
        self.assertEqual([d["id"] for d in self.drives("CE", {"E": 5})], ["drive_c"])

    def test_spoken_letters_are_found(self):
        with mock.patch.object(folders, "KNOWN", folders.KNOWN + self.drives("D")):
            for фраза in ("диск д", "диск дэ", "диск d", "диск ди"):
                self.assertEqual(folders.find(фраза), "drive_d", фраза)
            self.assertIsNone(folders.find("диск"))
        with mock.patch.object(folders, "KNOWN", folders.KNOWN + self.drives("C")):
            for фраза in ("диск ц", "диск цэ", "диск c", "диск с", "диск си"):
                self.assertEqual(folders.find(фраза), "drive_c", фраза)

    def test_open_says_the_letter(self):
        with mock.patch.object(folders, "KNOWN", folders.KNOWN + self.drives("D")), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(os, "startfile") as startfile:
            итог = folders.open_folder("drive_d")
        startfile.assert_called_once_with("D:\\")
        self.assertEqual(итог["text"], "Открыла диск D.")


if __name__ == "__main__":
    unittest.main()

    def test_папку_закрыть_не_можно(self):
        # Искать окно проводника нельзя: проводников бывает несколько, и
        # закрыть не тот — значит закрыть не то, что просил хозяин.
        self._список(self._запись())
        получилось, что = launcher.close("proj")
        self.assertFalse(получилось)
        self.assertIn("папку закрыть не могу", что)

    def test_вид_folder_известен_пульту(self):
        self.assertIn("folder", launcher.APP_KINDS)

    def test_ошибка_проводника_честно_сказана(self):
        # Папка есть, но открыть её не вышло: молчать об этом нельзя.
        with mock.patch.object(os, "startfile", side_effect=OSError("занято")):
            что = folders.open_folder("downloads")
        self.assertFalse(что["ok"])
        self.assertIn("занято", что["text"])
