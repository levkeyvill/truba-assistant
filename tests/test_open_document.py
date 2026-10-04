"""Поиск файла, открытого в редакторе.

`open_document()` обходит видимые окна сверху вниз и возвращает путь к файлу
или причину отказа.

Подменяется внешний мир с сохранением интерфейса API: `EnumWindows` отдаёт
окна сверху вниз, у `.lnk` цель — это `TargetPath`, у Obsidian хранилища лежат
в `obsidian.json` как `{"vaults": {"<id>": {"path": "…"}}}`, а
`psutil.Process.name` — метод, а не поле.
См. coordination/ГРАБЛИ.md, раздел «Бесплатная модель подделывает внешний мир в тестах».

Никаких настоящих окон, папок и личных файлов: перечисление окон
подменяется, `%APPDATA%` (хранилища Obsidian и ярлыки «Недавних») уезжает во
временную папку, стандартные папки подменены, как в `test_documents.py`.
Живые окна эти тесты не проверяют.
"""

import contextlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from say_helpers import assert_said
from core import commands, documents, folders, hands

ОБРАЗЕЦ = "Отчёт за сентябрь: выручка выросла на 12 процентов."


# --- Подмены внешнего мира -----------------------------------------------


def _процесс(имя, команда=()):
    """Подделка `psutil.Process`. Имя и команда — методы, как у настоящего.

    Именно так у настоящего psutil, и именно на этом первая версия модуля
    и споткнулась: подделка с полем вместо метода давала «<bound method …>»,
    и программа не находилась никогда.
    """

    class _П:
        def name(self):
            return имя

        def cmdline(self):
            return list(команда)

    return _П()


def _окна(*список):
    """Окна в z-порядке: сверху вниз. Возвращает подмену для `documents`.

    Список — как настоящий `EnumWindows`: элементы идут от верхнего к нижнему.
    Элемент — это пара «окно, процесс», чтобы в тесте про пульт и проводник
    не расходились заголовок окна и имя процесса под ним.
    """
    окна = [пара[0] if isinstance(пара, tuple) and len(пара) == 2 else пара
            for пара in список]
    return mock.patch.object(documents, "_видимые_окна", return_value=окна)


def _процессы(*список):
    """Подмена `psutil`: по pid отдаём нужный объект (или роняем).

    Принимает те же пары «окно, процесс», что и `_окна`: pid берём из окна.
    """
    словарь = {}
    for пара in список:
        if isinstance(пара, tuple) and len(пара) == 2:
            окно, процесс = пара
            словарь[int(окно[2])] = процесс

    def Process(pid):
        объект = словарь.get(int(pid))
        if объект is None:
            raise RuntimeError("нет такого процесса")
        return объект

    return mock.patch.object(documents, "psutil", NS(Process=Process))


def _мир(*список):
    """Обе подмены разом: окна **и** процессы под ними.

    Окно и имя процесса — это одна и та же правда об окне, и в тесте они не
    должны расходиться: иначе «окно пульта» окажется с именем `notepad.exe`
    и проверить будет нечего. Отдельные `_окна`/`_процессы` оставлены для
    случаев, где нужна только одна из подмен.
    """
    # Без `with` внутри: `ExitStack.__exit__` разобрал бы подмены ещё до
    # возврата, и в тесте читались бы настоящие окна хозяина.
    стек = contextlib.ExitStack()
    стек.enter_context(_окна(*список))
    стек.enter_context(_процессы(*список))
    return стек


def _подмена_com(полный_путь="", цель=""):
    """Подмена `pythoncom` и `win32com.client`: Word и ярлыки «Недавних».

    У настоящего Word `ActiveDocument.FullName` — полный путь, у
    `WScript.Shell.CreateShortcut(p)` — `TargetPath`. Ни того, ни другого
    тест не создаёт: COM здесь заглушка, а файлы — во временной папке.
    """
    word = NS(ActiveDocument=NS(FullName=str(полный_путь or "")))
    оболочка = NS(CreateShortcut=lambda путь: NS(TargetPath=str(цель or "")))
    клиент = NS(
        GetActiveObject=mock.Mock(return_value=word),
        Dispatch=mock.Mock(return_value=оболочка),
    )
    pythoncom = NS(CoInitialize=mock.Mock(), CoUninitialize=mock.Mock())
    return mock.patch.dict("sys.modules", {"win32com.client": клиент,
                                           "pythoncom": pythoncom})


class Мир(unittest.TestCase):
    """Временные папки вместо настоящих: `%APPDATA%` и стандартные папки.

    `%APPDATA%` тест подменяет целиком: оттуда `open_document` берёт и
    хранилища Obsidian (`obsidian.json`), и ярлыки «Недавних». Настоящие
    ярлыки хозяина тест не читает и тем более не удаляет.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-open-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
        self.appdata = self.корень / "appdata"
        self.appdata.mkdir()
        патчер = mock.patch.dict(os.environ, {"APPDATA": str(self.appdata)})
        патчер.start()
        self.addCleanup(патчер.stop)
        # Стандартные папки — тоже подмена: `find_by_name` в них заглянет,
        # а настоящие «Загрузки» хозяина трогать нельзя. Диски — тоже подмена: поиск
        # по названию идёт по всем дискам. Хранилища Obsidian не трогаем: их
        # список и так берётся из подменённого `%APPDATA%` (см. `_хранилище`).
        self.папки = {}
        for имя in ("downloads", "documents", "desktop"):
            папка = self.корень / имя
            папка.mkdir()
            self.папки[имя] = папка
        патчер = mock.patch.object(
            folders, "path_of",
            side_effect=lambda folder_id: self.папки.get(str(folder_id)))
        патчер.start()
        self.addCleanup(патчер.stop)
        патчер = mock.patch.object(folders, "_drives", return_value=())
        патчер.start()
        self.addCleanup(патчер.stop)

    def _лежит(self, куда, имя, текст=ОБРАЗЕЦ, когда=None):
        target = self.папки[куда] / имя
        target.write_text(текст, encoding="utf-8")
        if когда is not None:
            os.utime(str(target), (когда, когда))
        return target

    def _хранилище(self, имя, заметки):
        """Настоящий `obsidian.json` во временной папке + папка хранилища.

        Список хранилищ у Obsidian — это `{"vaults": {"<id>": {"path": "…",
        "open": true}}}`; читает его `core/notes.py::_vaults`, и мы подменяем
        ровно его, а не сам разбор.
        """
        папка = self.корень / имя
        папка.mkdir(exist_ok=True)
        for relative, текст in заметки.items():
            target = папка / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(текст, encoding="utf-8")
        (self.appdata / "obsidian").mkdir(exist_ok=True)
        (self.appdata / "obsidian" / "obsidian.json").write_text(
            json.dumps({"vaults": {"abc": {"path": str(папка), "open": True}}}),
            encoding="utf-8")
        return папка

    def _недавние(self, *ярлыки):
        """Папка «Недавних» с ярлыками: имя ярлыка — имя файла + `.lnk`."""
        папка = self.appdata / "Microsoft" / "Windows" / "Recent"
        папка.mkdir(parents=True, exist_ok=True)
        for имя, когда in ярлыки:
            (папка / f"{имя}.lnk").write_bytes(b"")
            if когда is not None:
                os.utime(str(папка / f"{имя}.lnk"), (когда, когда))
        return папка


def _окно(заголовок, pid=100, имя="notepad.exe", команда=()):
    """Одно поддельное окно: заголовок, pid и процесс под ним."""
    return (pid, заголовок, pid), _процесс(имя, команда)


class ПоЗаголовку(Мир):
    """Имя файла из заголовка окна: Блокнот, Notepad++, VS Code, Typora.

    Программа тут одна и та же (`notepad.exe`): различаются заголовки, и
    именно их мы и разбираем. Файл лежит в «Загрузках», а ярлыков в
    «Недавних» нет — иначе проверить разбор заголовка было бы нечем.
    """

    def _берёт(self, заголовок, имя_файла="отчёт.md"):
        файл = self._лежит("downloads", имя_файла)
        __пара = _окно(заголовок)
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        return что, файл

    def test_блокнот(self):
        что, файл = self._берёт("отчёт.md - Блокнот")
        self.assertEqual(что["path"], файл)
        self.assertFalse(что["unsaved"])

    def test_notepad_со_звёздочкой_читает_сохранённый(self):
        # Звёздочка — несохранённые правки: имя от неё отрезаем, а про
        # несохранённость говорим (`unsaved`), чтобы модель предупредила.
        что, файл = self._берёт("*отчёт.md - Notepad++", имя_файла="отчёт.md")
        self.assertEqual(что["path"], файл)
        self.assertTrue(что["unsaved"])

    def test_visual_studio_code_с_папкой_в_заголовке(self):
        # «отчёт.md - папка - Visual Studio Code»: лишний кусок не должен
        # попасть в имя файла.
        что, файл = self._берёт("отчёт.md - папка - Visual Studio Code")
        self.assertEqual(что["path"], файл)

    def test_notepad_с_полным_путем_берёт_его_сам(self):
        # Notepad++ умеет показывать полный путь. Ярлыка в «Недавних» нет —
        # значит, путь взят из заголовка, а не откуда-то ещё.
        файл = self._лежит("documents", "отчёт.txt")
        __пара = _окно(f"{файл} - Notepad++")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_полный_путь_на_несуществующий_файл_идёт_по_имени(self):
        # Путь в заголовке есть, а файла нет: тогда ищем по имени, а не
        # отдаём хозяину несуществующее.
        файл = self._лежит("downloads", "отчёт.txt")
        __пара = _окно("C:\\нет\\такого\\отчёт.txt - Notepad++")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_в_заголовке_нет_имени_файла(self):
        # «Документ1 - Word»: расширения нет, и выдумывать имя нельзя.
        __пара = _окно("Документ1 - Word")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("Документ1", что["why"])
        self.assertIn("не нашла сам файл", что["why"])

    def test_чужое_расширение_не_читается(self):
        # `.mdx` — не наш формат: под это не подводится, и файл не ищется.
        __пара = _окно("отчёт.mdx - Блокнот")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("не нашла сам файл", что["why"])


class ВObsidian(Мир):
    """Заметка из заголовка Obsidian: имя заметки + имя хранилища.

    Заголовок у него такой: «<заметка> - <хранилище> - Obsidian v1.x.x», и
    версия может отсутствовать. Хранилища — из `obsidian.json`, тот же
    список, что читает пульт.
    """

    def _берёт(self, заголовок):
        __пара = _окно(заголовок, имя=documents.OBSIDIAN)
        with _мир(__пара), _подмена_com():
            return documents.open_document()

    def test_с_версией_в_заголовке(self):
        папка = self._хранилище("Мои заметки", {"отчёт.md": ОБРАЗЕЦ})
        что = self._берёт("отчёт - Мои заметки - Obsidian v1.8.10")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], папка / "отчёт.md")

    def test_без_версии_в_заголовке(self):
        папка = self._хранилище("Мои заметки", {"отчёт.md": ОБРАЗЕЦ})
        что = self._берёт("отчёт - Мои заметки - Obsidian")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], папка / "отчёт.md")

    def test_заметка_в_подпапке_хранилища(self):
        # Заметки Трубы лежат в подпапках хранилища — обход должен туда дойти.
        папка = self._хранилище("Мои заметки",
                                 {"Книбы/Мастер и Маргарита.md": ОБРАЗЕЦ})
        что = self._берёт("Мастер и Маргарита - Мои заметки - Obsidian v1.8.10")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], папка / "Книбы" / "Мастер и Маргарита.md")

    def test_из_двух_одноимённых_берёт_свежую(self):
        # В хранилище две заметки с одним именем: свежая — та, что открыта.
        папка = self._хранилище("Мои заметки", {
            "Книбы/отчёт.md": "старое", "Архив/отчёт.md": "свежее"})
        os.utime(str(папка / "Книбы" / "отчёт.md"), (1_000_000, 1_000_000))
        os.utime(str(папка / "Архив" / "отчёт.md"), (2_000_000, 2_000_000))
        что = self._берёт("отчёт - Мои заметки - Obsidian v1.8.10")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], папка / "Архив" / "отчёт.md")

    def test_служебные_папки_пропускаются(self):
        # В `.obsidian` и `.trash` лежат настройки и удалённое. Заметки с тем
        # же именем там быть не должно — и мы туда не ходим.
        папка = self._хранилище("Мои заметки", {"отчёт.md": ОБРАЗЕЦ})
        for скрытая in (".obsidian", ".trash", ".git"):
            (папка / скрытая).mkdir()
            (папка / скрытая / "отчёт.md").write_text("мусор", encoding="utf-8")
        что = self._берёт("отчёт - Мои заметки - Obsidian v1.8.10")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], папка / "отчёт.md")

    def test_чужое_хранилище_отказывает(self):
        self._хранилище("Мои заметки", {"отчёт.md": ОБРАЗЕЦ})
        что = self._берёт("отчёт - Другойvault - Obsidian v1.8.10")
        self.assertFalse(что["ok"])
        self.assertIn("не нашла сам файл", что["why"])

    def test_нет_obsidian_json_отказывает(self):
        # Obsidian не установлен или список хранилищ битый — молчаливый отказ.
        __пара = _окно("отчёт - Мои заметки - Obsidian v1.8.10",
                               имя=documents.OBSIDIAN)
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("не нашла сам файл", что["why"])


class ВWord(Мир):
    """Word: настоящий путь знает только он сам, через COM.

    `GetActiveObject("Word.Application").ActiveDocument.FullName` — как у
    настоящего Word. Заголовок там бывает «Документ1», поэтому разбор
    заголовка у Word и не первый.
    """

    def test_берёт_путь_из_com(self):
        файл = self._лежит("documents", "счёт.docx", ОБРАЗЕЦ)
        __пара = _окно("Документ1 - Word", имя=documents.WORD)
        with _мир(__пара), \
                _подмена_com(полный_путь=str(файл)):
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_без_com_идёт_по_имени_из_заголовка(self):
        # COM не вышется (Word только что открылся) — общий путь по имени.
        файл = self._лежит("downloads", "счёт.docx", ОБРАЗЕЦ)
        __пара = _окно("счёт.docx - Word", имя=documents.WORD)
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)


class ПорядокОкон(Мир):
    """Верхнее окно редактора — первое, а своё и проводник — не помеха.

    «Прочитай открытый документ» значит то, что хозяин видит сейчас. Перед
    редактором может стоять пульт Трубы, проводник или браузер: они файл не
    называют, и мы идём дальше, а не отказываем.
    """

    def test_берёт_верхнее_окно(self):
        верхний = self._лежит("downloads", "верхний.txt")
        нижний = self._лежит("downloads", "нижний.txt")
        окна = [_окно("верхний.txt - Блокнот", pid=10),
                _окно("нижний.txt - Блокнот", pid=20)]
        with _мир(*окна), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], верхний)
        self.assertNotEqual(что["path"], нижний)

    def test_окно_пульта_впереди_пропускается(self):
        # Пульт — это `pythonw.exe` с `ui.window` в команде и окно
        # «Труба — пульт». Оба признака проверяются, и оба мимо.
        файл = self._лежит("downloads", "отчёт.md")
        окна = [_окно("Труба — пульт", pid=1, имя="pythonw.exe",
                      команда=["pythonw.exe", "-m", "ui.window"]),
                _окно("отчёт.md - Блокнот", pid=2)]
        with _мир(*окна), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_пульт_узнаётся_и_по_имени_процесса(self):
        # Окно без заголовка пульта, но у процесса в команде `ui.window`.
        файл = self._лежит("downloads", "отчёт.md")
        окна = [_окно("Панель", pid=1, имя="python.exe",
                      команда=["python.exe", "ui.window"]),
                _окно("отчёт.md - Блокнот", pid=2)]
        with _мир(*окна), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_проводник_впереди_не_мешает(self):
        # Проводник смотрит в папку, а не открывает файл: идём дальше.
        файл = self._лежит("downloads", "отчёт.md")
        окна = [_окно("Загрузки", pid=1, имя="explorer.exe"),
                _окно("отчёт.md - Блокнот", pid=2)]
        with _мир(*окна), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_браузер_впереди_не_мешает(self):
        # В заголовке браузера расширения нет — файл он не называет.
        файл = self._лежит("downloads", "отчёт.md")
        окна = [_окно("Моя почта — Google Chrome", pid=1, имя="chrome.exe"),
                _окно("отчёт.md - Блокнот", pid=2)]
        with _мир(*окна), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_окон_нет_вообще(self):
        with _окна(), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("не вижу открытого документа", что["why"])

    def test_вижу_окно_но_не_нашла_файла(self):
        # Честная причина словами: окно назвали, а файла в нём не разобрали.
        __пара = _окно("Проигрыватель", имя="wmplayer.exe")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("вижу окно", что["why"])
        self.assertIn("Проигрыватель", что["why"])

    def test_битое_окно_не_роняет(self):
        # Процесс закрылся между перечислением и чтением — обычное дело.
        # Процесса нет: закрылся между перечислением и чтением.
        with _окна((10, "отчёт.md - Блокнот", 10)), _процессы(), \
                _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertTrue(что["why"])

    def test_перечисление_окон_упало(self):
        # Даже если само перечисление вернуло мусор — разговор не роняем.
        with mock.patch.object(documents, "_видимые_окна",
                               side_effect=OSError("сломалось")):
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("не вижу открытого документа", что["why"])


class ПоНедавним(Мир):
    """Общий путь по имени: сначала «Недавние» Windows, потом поиск по папкам.

    Ярлык в «Недавних» называет файл, а внутри — его настоящий путь
    (`TargetPath`). Одного имени в «Недавних» бывает несколько — из разных
    папок, — и свежий ярлык отвечает на «что у меня открыто».
    """

    def test_берёт_цель_ярлыка(self):
        файл = self._лежит("documents", "отчёт.md")
        self._недавние(("отчёт.md", 1_000_000))
        __пара = _окно("отчёт.md - Блокнот")
        with _мир(__пара), \
                _подмена_com(цель=str(файл)):
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_из_двух_ярлыков_берёт_свежий(self):
        # Один и тот же файл из двух папок: Windows разводит ярлыки по
        # « (2)», и свежий — тот, по которому открывали последним.
        старый = self._лежит("documents", "отчёт.md", "старое", 1_000_000)
        свежий = self._лежит("downloads", "отчёт.md", "свежее", 2_000_000)
        папка = self._недавние(("отчёт.md", 1_000_000))
        второй = папка / "отчёт (2).md.lnk"
        второй.write_bytes(b"")
        os.utime(str(второй), (2_000_000, 2_000_000))
        __пара = _окно("отчёт.md - Блокнот")
        цели = {"отчёт.md": старый, "отчёт (2).md": свежий}
        with _мир(__пара), \
                mock.patch.object(documents, "_цель_ярлыка",
                                  side_effect=lambda путь: str(
                                      цели[Path(путь).stem])):
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], свежий)

    def test_без_ярлыка_ищет_по_имени(self):
        # Ярлыка в «Недавних» нет (например, папка «Недавние» чистилась) —
        # тогда работает уже `find_by_name`.
        файл = self._лежит("downloads", "отчёт.txt")
        __пара = _окно("отчёт.txt - Блокнот")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_ни_ярлыка_ни_файла_отказывает(self):
        __пара = _окно("отчёт.txt - Блокнот")
        with _мир(__пара), _подмена_com():
            что = documents.open_document()
        self.assertFalse(что["ok"])
        self.assertIn("отчёт.txt - Блокнот", что["why"])

    def test_ярлык_на_удалённый_файл_не_врёт(self):
        # Ярлык есть, а файла за ним уже нет: отдавать несуществующее нельзя,
        # ищем по имени дальше.
        self._недавние(("отчёт.md", 1_000_000))
        файл = self._лежит("downloads", "отчёт.md")
        __пара = _окно("отчёт.md - Блокнот")
        with _мир(__пара), \
                mock.patch.object(documents, "_цель_ярлыка",
                                  return_value=str(self.корень / "нет.md")):
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)

    def test_без_процента_appdata_отказывает(self):
        # `%APPDATA%` пуст (неправда, но бывает) — ищем по имени, не падаем.
        файл = self._лежит("downloads", "отчёт.md")
        __пара = _окно("отчёт.md - Блокнот")
        with _мир(__пара), \
                mock.patch.dict(os.environ, {"APPDATA": ""}), _подмена_com():
            что = documents.open_document()
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)


def _действие(прочитали=None):
    """Подмена голосового цикла: помнит текст и отвечает как он."""
    прочитали = [] if прочитали is None else прочитали

    def read_aloud(text=None, name="", resume=False):
        прочитали.append((text, name))
        return True, name

    read_aloud.прочитали = прочитали
    return read_aloud


class Инструмент(Мир):
    """`hands.run_document` при `which: open` — оба режима.

    Проверяем ровно то, ради чего это и делалось: при `retell` модель получает
    текст открытого документа (и знает про несохранённые правки), а при
    `aloud` — только готовую фразу, и текста в ответе нет.
    """

    def _открыт(self, имя_файла="отчёт.md", unsaved=False):
        """Файл в «Загрузках» + подмена окон и процессов под это имя."""
        файл = self._лежит("downloads", имя_файла)
        кусок = f"{'*' if unsaved else ''}{имя_файла} - Блокнот"
        return файл, _мир(_окно(кусок))

    def _ответ(self, аргументы, действия=None, события=None):
        return json.loads(hands.run_document(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None,
            действия,
        ))

    def test_pick_open_даёт_путь_и_unsaved(self):
        файл, мир = self._открыт(unsaved=True)
        with мир, _подмена_com():
            что = documents.pick("open")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], файл)
        self.assertTrue(что["unsaved"])

    def test_pick_open_без_окон_отказывает_словами(self):
        with _окна(), _подмена_com():
            что = documents.pick("open")
        self.assertFalse(что["ok"])
        self.assertIn("не вижу открытого документа", что["why"])

    def test_retell_отдаёт_текст_и_говорит_про_правки(self):
        файл, мир = self._открыт(unsaved=True)
        with мир, _подмена_com():
            ответ = self._ответ({"which": "open", "mode": "retell",
                                 "because": "перескажи открытый документ"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertEqual(ответ["name"], "отчёт.md")
        self.assertIn("выросла на 12 процентов", ответ["text"])
        # Модель обязана знать, что читается сохранённый файл.
        self.assertTrue(ответ["unsaved"])
        # Путь в облако не отдаём: в нём имя пользователя Windows.
        self.assertNotIn(str(файл), json.dumps(ответ, ensure_ascii=False))

    def test_retell_без_правок_unsaved_ложный(self):
        _, мир = self._открыт()
        with мир, _подмена_com():
            ответ = self._ответ({"which": "open",
                                 "because": "что в этой заметке?"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertFalse(ответ["unsaved"])

    def test_aloud_без_текста_документа(self):
        прочитали = []
        _, мир = self._открыт()
        with мир, _подмена_com():
            ответ = self._ответ({"which": "open", "mode": "aloud",
                                 "because": "прочитай вслух эту заметку"},
                                действия={"read_aloud": _действие(прочитали)})
        self.assertTrue(ответ.get("ok"), ответ)
        # Строка из файла не должна уйти никуда: ответ это весь текст, который
        # увидит модель.
        self.assertNotIn(ОБРАЗЕЦ, json.dumps(ответ, ensure_ascii=False))
        # Голос при этом текст получил — читать ей надо.
        self.assertEqual(прочитали, [(ОБРАЗЕЦ, "отчёт.md")])
        assert_said(self, ответ["text"], "doc_read", name="отчёт.md")

    def test_не_нашлось_и_в_журнале_и_в_ответе(self):
        события = []
        with _окна(), _подмена_com():
            ответ = self._ответ({"which": "open",
                                 "because": "прочитай открытый документ"},
                                события=события)
        self.assertIn("error", ответ)
        self.assertEqual(события[0][0], "document_failed")
        self.assertIn("не вижу открытого документа", ответ["error"])


class Объявление(unittest.TestCase):
    """Модель и судья должны знать про `open` — словами, а не перечислением."""

    def test_в_enum_есть_open_первым(self):
        параметры = hands.DOC_TOOL["function"]["parameters"]
        self.assertEqual(параметры["properties"]["which"]["enum"],
                         list(hands.DOC_WHICH))
        self.assertEqual(параметры["properties"]["which"]["enum"][0], "open")
        # Остальные способы на месте — мы их не выкидывали.
        for старый in ("selected", "latest_download", "by_name"):
            self.assertIn(старый, hands.DOC_WHICH)

    def test_описание_учит_модель_открытому(self):
        описание = hands.DOC_TOOL["function"]["description"]
        for фраза in ("открытый документ", "то, что у меня открыто",
                      "эту заметку"):
            self.assertIn(фраза, описание)
        # Про несохранённые правки — тоже: без этого модель прочитает
        # сохранённый файл и промолчит.
        self.assertIn("unsaved", описание)

    def test_судье_сказано_словами(self):
        слова = hands.action_words(hands.DOC_NAME, {"which": "open"})
        self.assertIn("открытый", слова.lower())
        # Судье перечислений не видит: `open` в словах оставаться не должен.
        self.assertNotIn("open", слова)
        вслух = hands.action_words(hands.DOC_NAME,
                                   {"which": "open", "mode": "aloud"})
        self.assertIn("вслух", вслух)
        self.assertNotIn("aloud", вслух)

    def test_наборы_не_поменялись(self):
        # Способ добавлен, а правила те же: текст документа в облако уходит
        # при `retell` и не уходит при `aloud`.
        self.assertIn(hands.DOC_NAME, hands.GUARDED)
        self.assertIn(hands.DOC_NAME, hands.JUDGED)
        self.assertNotIn(hands.DOC_NAME, hands.LOCAL)

    def test_строка_в_commands_skills(self):
        строки = [навык for навык in commands.SKILLS
                  if навык.title == "Документы"]
        self.assertEqual(len(строки), 1)
        навык = строки[0]
        # Примеры — про открытый документ: без них хозяин на странице
        # «Команды» его не узнает.
        self.assertTrue(any("открытый документ" in пример.lower()
                            for пример in навык.examples), навык.examples)
        self.assertTrue(any("заметку" in пример.lower()
                            for пример in навык.examples), навык.examples)
        self.assertIn("открытый", навык.does.lower())
        # Навык виден и на странице «Команды» — она собирается оттуда же.
        self.assertIn("Документы",
                      [одна["title"]
                       for одна in commands.commands_guide()["skills"]])

    def test_в_промпте_есть_открытый_документ(self):
        from core import abilities

        подходящие = [строка for строка in abilities.ACTIONS
                      if "документ" in строка]
        self.assertTrue(подходящие)
        # Описание навыка называет поддерживаемые редакторы.
        self.assertIn("Obsidian", abilities.describe([]))


class ИзКоманднойСтроки(unittest.TestCase):
    """Путь документа может быть в аргументах процесса, но не в «Недавних»."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.файл = self.папка / "проба.md"
        self.файл.write_text("# Проба", encoding="utf-8")

    def процесс(self, аргументы, папка=None):
        import psutil

        # cmdline и cwd у psutil — методы, не поля.
        настоящий = mock.Mock()
        настоящий.cmdline.return_value = ["notepad.exe"] + аргументы
        настоящий.cwd.return_value = str(папка or self.папка)
        return mock.patch.object(psutil, "Process", return_value=настоящий)

    def test_полный_путь_в_аргументах(self):
        with self.процесс([str(self.файл)]):
            self.assertEqual(documents._из_командной_строки(42, "проба.md"), self.файл)

    def test_относительный_путь_от_рабочей_папки(self):
        with self.процесс(["проба.md"]):
            self.assertEqual(documents._из_командной_строки(42, "ПРОБА.md"), self.файл)

    def test_другой_файл_в_аргументах_не_берётся(self):
        with self.процесс([str(self.папка / "другой.md")]):
            self.assertIsNone(documents._из_командной_строки(42, "проба.md"))

    def test_заголовок_блокнота_находит_файл_по_процессу(self):
        with self.процесс([str(self.файл)]), \
                mock.patch.object(documents, "_из_недавних", return_value=None):
            найден = documents._файл_из_заголовка("*проба.md – Блокнот", 42)
        self.assertEqual(найден, (self.файл, True))
