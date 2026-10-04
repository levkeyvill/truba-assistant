"""Поиск файла по названию и открытие: `core/files.py` и инструмент `find_file`.

Стандартные папки, хранилища Obsidian и диски подменены временным деревом,
`os.startfile` — тоже. Настоящие файлы и окна тест не трогает.

Проверяется то, что легко сломать: порядок обхода (свои папки раньше корня
диска), пропуск системных и скрытых папок, предел времени (часы подменены),
падежи в словах, имя без расширения, несколько равных совпадений (не больше
пяти), ограничение диском, неходьба по ссылкам, отказ открывать исполняемое,
принадлежность наборам инструментов, второй круг только без открытия и путь
без имени пользователя Windows.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from say_helpers import assert_said
from core import documents, files, folders, hands, notes

ОБРАЗЕЦ = "Отчёт за сентябрь: выручка выросла."
def _скрыть(путь):
    """Поставить Windows-атрибут «скрытый» (тест пропуска скрытого)."""
    import ctypes

    ctypes.windll.kernel32.SetFileAttributesW(str(путь), files.HIDDEN)


class Мир(unittest.TestCase):
    """Временное дерево вместо настоящих папок и дисков.

    Стандартные папки на диске C,
    корень диска C с «Документами» внутри и отдельный диск D, а в корнях —
    системные папки, которые обходить нельзя.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-files-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
        self.диск_c = self.корень / "C"
        self.диск_d = self.корень / "D"
        self.пользователь = self.диск_c / "Users" / "хозяин"
        self.папки = {}
        for имя in ("desktop", "documents", "downloads", "pictures",
                    "videos", "music", "notes"):
            папка = self.пользователь / {
                "desktop": "Рабочий стол", "documents": "Документы",
                "downloads": "Загрузки", "pictures": "Изображения",
                "videos": "Видео", "music": "Музыка", "notes": "Заметки",
            }[имя]
            папка.mkdir(parents=True, exist_ok=True)
            self.папки[имя] = папка
        self.хранилище = self.диск_c / "Obsidian"
        self.хранилище.mkdir(parents=True, exist_ok=True)
        self.документы_на_d = self.диск_d / "Документы"
        self.документы_на_d.mkdir(parents=True, exist_ok=True)
        for имя in ("Windows", "Program Files", "Program Files (x86)",
                    "ProgramData", "$Recycle.Bin", "System Volume Information",
                    "AppData"):
            (self.диск_c / имя).mkdir(exist_ok=True)
            (self.диск_d / имя).mkdir(exist_ok=True)

        патчер = mock.patch.object(
            folders, "path_of",
            side_effect=lambda folder_id: self.папки.get(str(folder_id)))
        патчер.start()
        self.addCleanup(патчер.stop)
        # Хранилища Obsidian и диски — тоже подмена: настоящие папки
        # тест не обходит ни при каких обстоятельствах.
        патчер = mock.patch.object(
            notes, "_vaults", return_value=[self.хранилище])
        патчер.start()
        self.addCleanup(патчер.stop)
        патчер = mock.patch.object(
            folders, "_drives", side_effect=self._диски)
        патчер.start()
        self.addCleanup(патчер.stop)
        self.окно = mock.patch.object(os, "startfile")
        self.начатfile = self.окно.start()
        self.addCleanup(self.окно.stop)
        hands.forget_done()

    def _диски(self):
        return ({"id": "drive_c", "title": "Диск C", "path": str(self.диск_c)},
                {"id": "drive_d", "title": "Диск D", "path": str(self.диск_d)})

    def _лежит(self, куда, имя, текст=ОБРАЗЕЦ, когда=None) -> Path:
        target = self.папки[куда] / имя
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(текст, encoding="utf-8")
        if когда is not None:
            os.utime(str(target), (когда, когда))
        return target

    def _ответ(self, аргументы, события=None):
        return json.loads(hands.run_find_file(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None))
class Поиск(Мир):
    """`files.find`: где ищем, что пропускаем и как считаем очки."""

    def test_свои_папки_раньше_корня_диска(self):
        # Одинаковые имена и одинаковое время изменения — в «Документах» и в
        # корне диска: выигрывает тот, кого обход встретил раньше, а свои папки
        # обходятся раньше корня.
        свой = self._лежит("documents", "отчёт.docx", когда=2_000_000)
        в_корне = self.диск_c / "отчёт.docx"
        в_корне.write_text("из корня", encoding="utf-8")
        os.utime(str(в_корне), (2_000_000, 2_000_000))
        что = files.find("отчёт")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["found"], [свой, в_корне])
        # Оба нашлись, но первым — свой: порядок обхода виден прямо в ответе.
        self.assertEqual(что["found"][0], свой)

    def test_свежий_важнее_порядка_обхода(self):
        # А вот свежесть при равных очках перевешивает порядок: «свежий файл
        # выше при равных» — так и обещано.
        свой = self._лежит("documents", "отчёт.docx", когда=1_000_000)
        в_корне = self.диск_c / "отчёт.docx"
        в_корне.write_text("из корня", encoding="utf-8")
        os.utime(str(в_корне), (9_000_000, 9_000_000))
        что = files.find("отчёт")
        self.assertEqual(что["found"][0], в_корне)
        self.assertEqual(set(что["found"]), {свой, в_корне})

    def test_файл_в_своей_папке_не_считается_дважды(self):
        # Обход корня диска заходит в «Документы» сам; без защиты от повторного
        # обхода один и тот же файл вышел бы двумя кандидатами.
        свой = self._лежит("documents", "отчёт.docx")
        self.assertEqual(files.find("отчёт")["found"], [свой])

    def test_ищет_в_хранилище_obsidian(self):
        target = self.хранилище / "мысли.md"
        target.write_text("да", encoding="utf-8")
        self.assertEqual(files.find("мысли")["found"], [target])

    def test_ищет_на_втором_диске(self):
        target = self.документы_на_d / "договор.docx"
        target.write_text("да", encoding="utf-8")
        self.assertEqual(files.find("договор")["found"], [target])

    def test_системные_папки_мимо(self):
        for имя in ("Windows", "AppData", "Program Files", "ProgramData",
                    "node_modules", ".git", ".venv", "venv", "__pycache__",
                    ".cache"):
            папка = self.диск_c / имя
            if not папка.exists():
                папка.mkdir(exist_ok=True)
            (папка / "отчёт.docx").write_text("нет", encoding="utf-8")
        self.assertEqual(files.find("отчёт")["found"], [])

    def test_скрытая_папка_и_скрытый_файл_мимо(self):
        скрытая = self.диск_c / "Архив"
        скрытая.mkdir()
        (скрытая / "отчёт.docx").write_text("нет", encoding="utf-8")
        _скрыть(скрытая)
        _скрыть(self._лежит("downloads", "старый отчёт.docx"))
        self.assertEqual(files.find("отчёт")["found"], [])

    def test_по_ссылке_не_ходим(self):
        # Ссылка ведёт в папку, до которой обход иначе не дойдёт: самой цели в
        # обход не отдавали. По ссылке не ходим — иначе поиск ушёл бы в цикл или
        # в чужое место.
        цель = self.корень / "вне-дисков"
        цель.mkdir()
        (цель / "отчёт.docx").write_text("нет", encoding="utf-8")
        ссылка = self.диск_c / "Архив-ссылка"
        try:
            os.symlink(str(цель), str(ссылка), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink недоступен на этом компьютере")
        self.assertEqual(files.find("отчёт")["found"], [])
        # Проверка осмысленна только пока цель действительно вне корней.
        self.assertNotIn(files._путь_ключ(цель),
                         {files._путь_ключ(one) for one in files._корни("")})

    def test_предел_времени_честно_говорит(self):
        self._лежит("documents", "отчёт.docx")
        # Часы прыгают вперёд: обход обрывается на первом же шаге.
        часы = mock.Mock(side_effect=[0.0] + [100.0] * 40)
        with mock.patch.object(files.time, "monotonic", часы):
            что = files.find("отчёт", limit_seconds=6.0)
        self.assertFalse(что["searched_all"])
        self.assertIn("не успела", что["why"])

    def test_предел_времени_лучшее_из_найденного(self):
        свой = self._лежит("documents", "отчёт.docx")
        # Первый корень успевает, дальше часы уходят вперёд: отдаём лучшее из
        # найденного и честно признаёмся.
        счётчик = {"n": 0}

        def часы():
            счётчик["n"] += 1
            # Показываем «Рабочий стол» (первый корень, он пустой) и вход в
            # «Документы», а дальше — уже за пределом шести секунд.
            return 0.0 if счётчик["n"] <= 4 else 100.0

        with mock.patch.object(files.time, "monotonic", часы):
            что = files.find("отчёт", limit_seconds=6.0)
        self.assertFalse(что["searched_all"])
        self.assertEqual(что["found"], [свой])
        self.assertIn("не успела", что["why"])

    def test_падежи_и_ё(self):
        target = self._лежит("documents", "Отчёт за сентябрь.docx")
        for слова in ("отчёта", "ОТЧЁТ", "отчет"):
            with self.subTest(слова=слова):
                self.assertEqual(files.find(слова)["found"], [target])

    def test_без_расширения(self):
        target = self._лежит("documents", "план ремонта.docx")
        self.assertEqual(files.find("план ремонта")["found"], [target])

    def test_слова_короче_трёх_букв_молчат(self):
        self._лежит("documents", "отчёт.docx")
        for слова in ("", "   ", "и"):
            with self.subTest(слова=слова):
                что = files.find(слова)
                self.assertFalse(что["ok"])
                self.assertEqual(что["found"], [])

    def test_больше_слов_лучше(self):
        слабое = self._лежит("documents", "отчёт старый.docx")
        сильное = self._лежит("documents", "отчёт за сентябрь.docx")
        нашлось = files.find("отчёт за сентябрь")["found"]
        self.assertEqual(нашлось, [сильное])
        self.assertNotIn(слабое, нашлось)

    def test_точное_имя_выше_свежего(self):
        # «Отчёт.docx» — точное совпадение имени, пусть и старее.
        точное = self._лежит("downloads", "Отчёт.docx", когда=1_000_000)
        размытое = self._лежит("documents", "Отчёт (копия).docx",
                               когда=9_000_000)
        что = files.find("отчёт")
        self.assertEqual(что["found"], [точное])
        self.assertNotIn(размытое, что["found"])

    def test_свежий_при_равных(self):
        старый = self._лежит("downloads", "отчёт.docx", когда=1_000_000)
        свежий = self._лежит("documents", "отчёт.docx", когда=9_000_000)
        что = files.find("отчёт")
        self.assertEqual(что["found"][0], свежий)
        self.assertEqual(set(что["found"]), {старый, свежий})

    def test_равных_не_больше_пяти(self):
        for one in range(7):
            self._лежит("downloads", f"отчёт {one}.docx")
        self.assertEqual(len(files.find("отчёт")["found"]), files.BEST)

    def test_диск_ограничивает(self):
        на_d = self.документы_на_d / "отчёт.docx"
        на_d.write_text("да", encoding="utf-8")
        self._лежит("documents", "отчёт.docx")
        # Диск D — только он: файл из «Документов» на C сюда не попадает.
        self.assertEqual(files.find("отчёт", "D")["found"], [на_d])
        self.assertEqual(len(files.find("отчёт")["found"]), 2)

    def test_ничего_не_нашлось(self):
        self._лежит("documents", "отчёт.docx")
        что = files.find("договор аренды")
        self.assertFalse(что["ok"])
        self.assertEqual(что["found"], [])
        self.assertIn("не нашла", что["why"])
        self.assertTrue(что["searched_all"])
class Открытие(Мир):
    """`files.open_file`: программа по умолчанию и честные отказы."""

    def test_файл_открыт(self):
        target = self._лежит("documents", "отчёт.docx")
        что = files.open_file(target)
        self.assertTrue(что["ok"], что)
        assert_said(self, что["text"], "file_open", name="отчёт.docx")
        self.начатfile.assert_called_once_with(str(target))

    def test_папка_открывается(self):
        что = files.open_file(self.папки["documents"])
        self.assertTrue(что["ok"], что)
        self.начатfile.assert_called_once_with(str(self.папки["documents"]))

    def test_исполняемое_и_скрипты_не_открываем(self):
        # Запуск по названию файла — это запуск программ, а для них есть
        # `launch_app` с белым списком.
        for имя in ("setup.exe", "правка.ps1", "ярлык.lnk", "скрипт.py",
                    "запуск.bat", "драйвер.msi"):
            with self.subTest(имя=имя):
                target = self.папки["documents"] / имя
                target.write_text("нет", encoding="utf-8")
                что = files.open_file(target)
                self.assertFalse(что["ok"])
                self.assertIn("не буду", что["text"])
        self.начатfile.assert_not_called()

    def test_нет_такого_файла(self):
        что = files.open_file(self.папки["documents"] / "нет.docx")
        self.assertFalse(что["ok"])
        self.assertIn("нет", что["text"])
        self.начатfile.assert_not_called()

    def test_пустое_значение_и_ошибка(self):
        self.assertFalse(files.open_file(None)["ok"])
        self.assertFalse(files.open_file("")["ok"])
        # `startfile` на живой папке может упасть (нет программы) — это не
        # исключение наружу, а честный отказ.
        self.начатfile.side_effect = OSError("нет программы")
        self.assertFalse(files.open_file(self.папки["documents"])["ok"])
class Инструмент(Мир):
    """`hands.find_file`: объявление, наборы, ответ и журнал."""

    def test_принадлежит_наборам(self):
        # Открытый файл — окно на экране, ровно как у запуска программы, и
        # файл пользователя — чужой: оба действия требуют проверки судьи.
        self.assertIn(hands.FIND_NAME, hands.JUDGED)
        self.assertIn(hands.FIND_NAME, hands.GUARDED)
        # В `LOCAL` его нет: файл только найден — второй круг нужен, сказать
        # о найденном файле. Решение принимает `confirm_forms`.
        self.assertNotIn(hands.FIND_NAME, hands.LOCAL)
        self.assertNotIn(hands.FIND_NAME, hands.CONFIRM)

    def test_объявление_и_потому_что(self):
        функция = hands.FIND_TOOL["function"]
        self.assertEqual(функция["name"], hands.FIND_NAME)
        параметры = функция["parameters"]
        self.assertIn("because", параметры["required"])
        self.assertIn("name", параметры["required"])
        # Буквы дисков — из настоящего списка плюс пусто («искать везде»).
        self.assertEqual(параметры["properties"]["drive"]["enum"],
                         [""] + list(hands.DRIVE_LETTERS))
        self.assertEqual(параметры["properties"]["open"]["type"], "boolean")
        описание = функция["description"]
        self.assertIn("точным именем из поля name прошлого ответа", описание)
        self.assertIn("без расширения, и open: true", описание)

    def test_второй_круг_только_после_открытия(self):
        # Открыла — модель получила готовую фразу, второй круг ей не нужен.
        for аргументы, ожидаем in (
                ('{"name": "отчёт", "open": true}', ("{text}",)),
                ('{"name": "отчёт"}', None),
                ('{"name": "отчёт", "open": false}', None),
                ("не json", None)):
            with self.subTest(аргументы=аргументы):
                self.assertEqual(
                    hands.confirm_forms({"name": hands.FIND_NAME,
                                         "args": аргументы}),
                    ожидаем)

    def test_открытая_фраза_берётся_из_ответа(self):
        target = self._лежит("documents", "отчёт.docx")
        ответ = self._ответ({"name": "отчёт", "open": True,
                              "because": "открой файл отчёт"})
        self.assertTrue(ответ["opened"], ответ)
        assert_said(self, hands.confirm(
            [{"name": hands.FIND_NAME,
              "args": json.dumps({"name": "отчёт", "open": True})}],
            results=[json.dumps(ответ, ensure_ascii=False)]),
            "file_open", name="отчёт.docx")
        self.начатfile.assert_called_once_with(str(target))

    def test_найдено_одно_отдаёт_путь_и_имя(self):
        target = self._лежит("documents", "отчёт.docx")
        ответ = self._ответ({"name": "отчёт", "because": "найди файл отчёт"})
        self.assertEqual(ответ["name"], "отчёт.docx")
        # Папка и путь — в виде для модели: без имени пользователя Windows.
        self.assertEqual(ответ["folder"],
                         files.короткий(self.папки["documents"]))
        self.assertTrue(files.короткий(target).endswith(ответ["path"]))
        self.assertEqual(ответ["count"], 1)
        self.начатfile.assert_not_called()

    def test_путь_без_имени_пользователя(self):
        # В пути имя человека Windows; модели оно ни к чему — вместо него
        # домашняя папка `~\`. Подменяем домашнюю папку на свою.
        self._лежит("documents", "отчёт.docx")
        with mock.patch.object(Path, "home", return_value=self.пользователь):
            ответ = self._ответ({"name": "отчёт",
                                  "because": "найди файл отчёт"})
        self.assertEqual(ответ["path"], "~" + os.sep + "Документы"
                         + os.sep + "отчёт.docx")
        self.assertEqual(ответ["folder"], "~" + os.sep + "Документы")
        # Настоящее имя пользователя в ответе не остаётся нигде.
        self.assertNotIn("хозяин", json.dumps(ответ, ensure_ascii=False))

    def test_несколько_кандидаты_модели(self):
        self._лежит("downloads", "отчёт.docx")
        self._лежит("documents", "отчёт.docx")
        ответ = self._ответ({"name": "отчёт", "open": True,
                              "because": "открой файл отчёт"})
        self.assertEqual(ответ["count"], 2)
        self.assertEqual(len(ответ["candidates"]), 2)
        for один in ответ["candidates"]:
            self.assertEqual(один["name"], "отчёт.docx")
            self.assertTrue(один["folder"])
        # Несколько совпадений требуют уточнения перед открытием.
        self.начатfile.assert_not_called()
        # И второй круг нужен: формы по аргументам есть, но произносить нечего
        # — в ответе нет готовой фразы, и `confirm` молчит, а модель переспрашивает.
        self.assertEqual(
            hands.confirm_forms(
                {"name": hands.FIND_NAME,
                 "args": json.dumps({"name": "отчёт", "open": True})}),
            ("{text}",))
        self.assertEqual(hands.confirm(
            [{"name": hands.FIND_NAME,
              "args": json.dumps({"name": "отчёт", "open": True})}],
            results=[json.dumps(ответ, ensure_ascii=False)]), "")

    def test_не_нашлось_честно(self):
        self._лежит("documents", "отчёт.docx")
        события = []
        ответ = self._ответ({"name": "договор аренды",
                              "because": "найди файл договор аренды"},
                             события)
        self.assertIn("не нашла", ответ["error"])
        self.assertEqual(события, [("file_missing", "договор аренды")])

    def test_журнал_пульта_умеет_эти_строки(self):
        from ui.web_runtime import WebRuntime

        self.assertEqual(
            WebRuntime._log_messages("file_found", "отчёт.docx, Документы"),
            ["файл найден: отчёт.docx, Документы"])
        self.assertEqual(
            WebRuntime._log_messages("file_opened", "отчёт.docx"),
            ["файл открыт: отчёт.docx"])
        self.assertEqual(
            WebRuntime._log_messages("file_missing", "договор"),
            ["файл не найден: договор"])

    def test_судье_сказано_словами(self):
        слова = hands.action_words(hands.FIND_NAME, {"name": "отчёт"})
        self.assertEqual(слова, "найти файл «отчёт»")
        с_открытием = hands.action_words(
            hands.FIND_NAME, {"name": "отчёт", "drive": "C", "open": True})
        self.assertIn("открыть", с_открытием)
        self.assertIn("C", с_открытием)
        # Судье перечисления не видны: букв диска из enum он не знает.
        self.assertNotIn("drive", с_открытием)

    def test_без_названия_и_битый_json(self):
        self.assertIn("не названо",
                      self._ответ({"name": " ", "because": "найди"})["error"])
        self.assertIn("JSON", hands.run_find_file("не json"))

    def test_в_наборе_сразу_за_read_document(self):
        # Набор не зависит ни от apps.json, ни от настроек: файлы есть всегда,
        # а на этом держится кеш запроса.
        from core.brain import Brain

        brain = object.__new__(Brain)
        brain.provider = "deepseek"
        brain._home = "deepseek"
        brain._home_at = 0.0
        brain._model = "test"
        brain._reply_lock = __import__("threading").RLock()
        brain._history = __import__("collections").deque(maxlen=10)
        brain._persona = "тест"
        brain._abilities = lambda: ""
        brain._memory = lambda: ""
        brain._keep_history = lambda: None
        brain._undigested = 0
        brain._tools_ok = None
        brain._quiet = None
        brain._usage_ok = None
        brain.on_event = None
        brain.actions = {}
        имена = [spec["function"]["name"]
                 for spec in brain._tool_list("как дела?")]
        self.assertIn(hands.FIND_NAME, имена)
        self.assertEqual(имена[имена.index(hands.DOC_NAME) + 1],
                         hands.FIND_NAME)


class ЧтениеПоНазванию(Мир):
    """`read_document` с `by_name` ищет по всем дискам, а не в трёх папках."""

    def _ответ(self, аргументы):
        return json.loads(hands.run_document(
            json.dumps(аргументы, ensure_ascii=False)))

    def test_находит_глубже_трёх_папок(self):
        # Файл глубже трёх вложенностей проверяет обход вложенных папок.
        глубоко = (self.папки["documents"] / "2024" / "март" / "архив"
                   / "отчёт.txt")
        глубоко.parent.mkdir(parents=True)
        глубоко.write_text(ОБРАЗЕЦ, encoding="utf-8")
        ответ = self._ответ({"which": "by_name", "name": "отчёт",
                              "because": "что в файле с отчётом?"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertEqual(ответ["name"], "отчёт.txt")
        self.assertIn("выросла", ответ["text"])

    def test_находит_на_другом_диске(self):
        target = self.документы_на_d / "договор аренды.docx"
        target.write_text(ОБРАЗЕЦ, encoding="utf-8")
        self.assertEqual(documents.find_by_name("договор аренды"), [target])
        # Диск передаётся и сюда: «с диска д» — это про диск.
        self.assertEqual(documents.find_by_name("отчёт", "D"), [])

    def test_нечитаемое_мимо(self):
        (self.папки["documents"] / "отчёт.xlsx").write_text(
            ОБРАЗЕЦ, encoding="utf-8")
        self.assertEqual(documents.find_by_name("отчёт"), [])

    def test_несколько_кандидатов(self):
        первый = self._лежит("downloads", "отчёт.docx")
        второй = self._лежит("documents", "отчёт.docx")
        нашлось = documents.find_by_name("отчёт")
        self.assertEqual(set(нашлось), {первый, второй})
        что = documents.pick("by_name", "отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(len(что["candidates"]), 2)


class Слитно(unittest.TestCase):
    """Слитный запрос должен предпочесть имя модели файлу лицензии."""

    def очки(self, запрос, имя):
        искомые = files._Запрос(files._слова(запрос))
        искомые.слитно = files._слитно(запрос)
        return files._очки(имя, искомые)

    def test_слитный_запрос_сильнее_слов(self):
        нужный = self.очки("higgs tts 3 flash rt", "Higgs_TTS3_FlashRT_technical_brief_RU.md")
        лицензия = self.очки("higgs tts 3 flash rt", "Boson-Higgs-TTS-3-LICENSE.txt")
        self.assertGreater(нужный, лицензия)

    def test_короткое_слитно_не_считается(self):
        # «отчёт» слитно входит в «отчётность» — это не склейка, а слово.
        self.assertEqual(self.очки("отчёт", "отчётность.docx")[0], 0)

    def test_сказано_раздельно_а_в_имени_слитно(self):
        # «Планремонта.txt»: «план ремонта» — это оно, а не файл, где есть
        # только слово «ремонт» или «текст».
        self.assertGreater(self.очки("план ремонта", "Планремонта.txt"),
                           self.очки("план ремонта", "Ремонт кухни.txt"))
        с_лишним = self.очки("текстовый файл план ремонта", "Планремонта.txt")
        self.assertGreater(с_лишним,
                           self.очки("текстовый файл план ремонта", "01 — Закадровый текст.md"))
        self.assertEqual(с_лишним[0], 2)

    def test_слитное_имя_целиком_сильнее_всего(self):
        self.assertEqual(self.очки("планремонта", "План ремонта.txt"), (2, 1))
        self.assertEqual(self.очки("план ремонта", "Планремонта.txt"), (3, 1))
