"""Документы: чтение файлов и инструмент `read_document`.

Файлы создаются во временной папке; системные папки и COM проводника
подменяются. Тест не обращается к личным файлам и живому проводнику.

PDF с текстом собирается здесь же, минимальным файлом с текстовым потоком:
`pypdf` умеет читать, но не рисовать, а `reportlab` в проекте нет и тянуть
его ради теста незачем. Объекты нумеруются подряд, у каждого — своя строка в
xref: иначе `pypdf` чинит ссылки и ругается в журнал.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from core import commands, documents, folders, hands
from core.brain import Brain

ФОРМАТ = ("pdf", "docx", "txt", "md", "csv", "log", "json", "ini")
ОБРАЗЕЦ = "Отчёт за сентябрь: выручка выросла на 12 процентов."


def pdf_bytes(страницы: list) -> bytes:
    """Минимальный PDF с текстовым потоком. `[["строка", …], …]` — по страницам.

    Порядок объектов: каталог, дерево страниц, сами страницы, их потоки и
    шрифт последним. Шрифт после потоков — обязательно, иначе он занимает
    номер чужого объекта, и ссылки в файле оказываются неверными.
    """
    сколько = len(страницы)
    потоки = 3 + сколько
    шрифт = 3 + сколько * 2
    куски = [
        (1, "<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in range(3, 3 + сколько))}]"
            f" /Count {сколько} >>"),
    ]
    for номер, строки in enumerate(страницы, start=3):
        куски.append((
            номер,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {шрифт} 0 R >> >> "
            f"/Contents {номер + сколько} 0 R >>"))
    for номер, строки in enumerate(страницы, start=потоки):
        поток = "BT /F1 12 Tf 72 720 Td 14 TL\n"
        поток += "".join(f"({строка}) Tj T*\n" for строка in строки)
        поток += "ET"
        куски.append((номер, f"<< /Length {len(поток)} >>\nstream\n{поток}endstream"))
    куски.append((шрифт, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    pdf = bytearray(b"%PDF-1.4\n")
    смещения = {}
    for номер, тело in куски:
        смещения[номер] = len(pdf)
        pdf += f"{номер} 0 obj\n{тело}\nendobj\n".encode("latin-1", "replace")
    начало = len(pdf)
    всего = шрифт + 1
    pdf += f"xref\n0 {всего}\n0000000000 65535 f \n".encode("latin-1")
    for номер in range(1, всего):
        pdf += f"{смещения[номер]:010d} 00000 n \n".encode("latin-1")
    pdf += (f"trailer\n<< /Size {всего} /Root 1 0 R >>\nstartxref\n{начало}\n"
            "%%EOF\n").encode("latin-1")
    return bytes(pdf)


def docx_bytes(абзацы: list, таблица=None) -> bytes:
    """Настоящий `.docx` через `python-docx`, во временный файл."""
    import docx

    папка = Path(tempfile.mkdtemp(prefix="truba-docx-"))
    target = папка / "образец.docx"
    документ = docx.Document()
    for строка in абзацы:
        документ.add_paragraph(строка)
    if таблица:
        сетка = документ.add_table(rows=len(таблица), cols=len(таблица[0]))
        for номер, row in enumerate(таблица):
            for колонка, значение in enumerate(row):
                сетка.rows[номер].cells[колонка].text = значение
    документ.save(str(target))
    данные = target.read_bytes()
    shutil.rmtree(str(папка), ignore_errors=True)
    return данные


def blank_pdf() -> bytes:
    """Пустая страница — «скан»: страница есть, текста на ней нет."""
    from pypdf import PdfWriter

    папка = Path(tempfile.mkdtemp(prefix="truba-pdf-"))
    target = папка / "скан.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(str(target), "wb") as поток:
        writer.write(поток)
    данные = target.read_bytes()
    shutil.rmtree(str(папка), ignore_errors=True)
    return данные


def locked_pdf(исходник: Path) -> bytes:
    """Тот же PDF под паролем — читать его нечем."""
    from pypdf import PdfWriter

    папка = Path(tempfile.mkdtemp(prefix="truba-pdf-"))
    target = папка / "замок.pdf"
    writer = PdfWriter(clone_from=str(исходник))
    writer.encrypt("секрет")
    with open(str(target), "wb") as поток:
        writer.write(поток)
    данные = target.read_bytes()
    shutil.rmtree(str(папка), ignore_errors=True)
    return данные


class ЧтениеФорматов(unittest.TestCase):
    """Каждый поддерживаемый формат читается, остальные — честный отказ."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-documents-"))
        self.addCleanup(shutil.rmtree, str(self.папка), True)

    def _файл(self, имя: str, данные: bytes) -> Path:
        target = self.папка / имя
        target.write_bytes(данные)
        return target

    def test_набор_форматов_как_в_задании(self):
        self.assertEqual(tuple(раздел.lstrip(".") for раздел in documents.SUPPORTED),
                         ФОРМАТ)

    def test_pdf_читается_постранично(self):
        target = self._файл("отчёт.pdf",
                            pdf_bytes([["First page."], ["Second page."]]))
        что = documents.read_text(target)
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["name"], "отчёт.pdf")
        self.assertIn("First page.", что["text"])
        self.assertIn("Second page.", что["text"])
        self.assertEqual(что["pages"], 2)
        self.assertFalse(что["cut"])
        self.assertEqual(что["chars"], len(что["text"]))

    def test_docx_читает_абзацы_и_таблицу(self):
        target = self._файл("смета.docx", docx_bytes(
            ["Заголовок раздела.", "Второй абзац."],
            [["Позиция", "Сумма"], ["Кран", "1500"]]))
        что = documents.read_text(target)
        self.assertTrue(что["ok"], что)
        self.assertIn("Второй абзац.", что["text"])
        # Строка таблицы — ячейками через « | », иначе пересказ не поймёт, где
        # что стоит.
        self.assertIn("Позиция | Сумма", что["text"])
        self.assertIn("Кран | 1500", что["text"])

    def test_текстовые_форматы_читаются_как_текст(self):
        for раздел in ("txt", "md", "csv", "log", "json", "ini"):
            with self.subTest(формат=раздел):
                target = self._файл(f"файл.{раздел}", ОБРАЗЕЦ.encode("utf-8"))
                что = documents.read_text(target)
                self.assertTrue(что["ok"], что)
                self.assertIn("выросла на 12 процентов", что["text"])
                self.assertEqual(что["pages"], 0)
                self.assertEqual(что["chars"], len(ОБРАЗЕЦ))
                self.assertFalse(что["cut"])

    def test_неизвестный_формат_отказывает(self):
        что = documents.read_text(self._файл("таблица.xlsx", b"x"))
        self.assertFalse(что["ok"])
        self.assertIn("такой формат читать не умею", что["why"])
        self.assertIn(".xlsx", что["why"])
        self.assertEqual(что["text"], "")

    def test_пустой_pdf_это_скан(self):
        что = documents.read_text(self._файл("скан.pdf", blank_pdf()))
        self.assertFalse(что["ok"])
        self.assertIn("скан", что["why"])
        self.assertIn("читать сканы не умею", что["why"])

    def test_зашифрованный_pdf_отказывает(self):
        обычный = self._файл("обычный.pdf", pdf_bytes([["Secret text."]]))
        что = documents.read_text(self._файл("замок.pdf", locked_pdf(обычный)))
        self.assertFalse(что["ok"])
        self.assertIn("паролем", что["why"])

    def test_огромный_файл_отказывает_без_чтения(self):
        # 50 МБ ради одного теста — расточительство: опускаем сам порог
        # (`MAX_BYTES`) и оставляем файл крошечным. Подменять `stat` нельзя —
        # на нём держится и проверка «файл ли это».
        target = self._файл("тяжёлый.txt", ОБРАЗЕЦ.encode("utf-8"))
        with mock.patch.object(documents, "MAX_BYTES", 10):
            что = documents.read_text(target)
        self.assertFalse(что["ok"])
        self.assertIn("слишком большой", что["why"])
        self.assertEqual(что["text"], "")

    def test_отсутствующий_файл_не_роняет(self):
        что = documents.read_text(self.папка / "нет-такого.txt")
        self.assertFalse(что["ok"])
        self.assertIn("нет", что["why"])

    def test_пустой_файл_отказывает(self):
        что = documents.read_text(self._файл("пустой.txt", b""))
        self.assertFalse(что["ok"])
        self.assertIn("нет текста", что["why"])

    def test_битые_файлы_не_роняют(self):
        # `pypdf` на обрывке сам пишет в журнал «EOF marker not found» — это
        # его лог, а не наш вывод: глушим на время проверки.
        import logging

        журнал = logging.getLogger("pypdf")
        уровень = журнал.level
        журнал.setLevel(logging.CRITICAL)
        self.addCleanup(журнал.setLevel, уровень)
        for имя, данные in (("мусор.docx", b"not a zip at all"),
                            ("мусор.pdf", b"%PDF-1.4\nbroken"),
                            ("мусор.txt", "   \n  ".encode("utf-8"))):
            with self.subTest(файл=имя):
                что = documents.read_text(self._файл(имя, данные))
                self.assertFalse(что["ok"])
                self.assertTrue(что["why"], что)


class Кодировки(unittest.TestCase):
    """UTF-8 с BOM и без, а также cp1251 — как у хозяина на диске."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-enc-"))
        self.addCleanup(shutil.rmtree, str(self.папка), True)

    def _проверить(self, имя, данные, ждём):
        target = self.папка / имя
        target.write_bytes(данные)
        что = documents.read_text(target)
        self.assertTrue(что["ok"], что)
        self.assertIn(ждём, что["text"])
        return что

    def test_utf8_без_bom(self):
        self._проверить("обычный.txt", "Привет, мир".encode("utf-8"), "Привет, мир")

    def test_utf8_с_bom(self):
        # BOM без `utf-8-sig` остаётся в тексте первым символом, и в начале
        # фразы он читается вслух как «п» перед словом.
        что = self._проверить("с_bom.txt", "﻿Привет".encode("utf-8"), "Привет")
        self.assertFalse(что["text"].startswith("﻿"), repr(что["text"]))

    def test_cp1251(self):
        self._проверить("старое.txt", "Отчёт за год".encode("cp1251"), "Отчёт за год")

    def test_две_кодировки_дают_один_текст(self):
        слова = "Проверка кодировки"
        (self.папка / "а.txt").write_text(слова, encoding="utf-8")
        (self.папка / "б.txt").write_bytes(слова.encode("cp1251"))
        self.assertEqual(documents.read_text(self.папка / "а.txt")["text"],
                         documents.read_text(self.папка / "б.txt")["text"])


class Обрезка(unittest.TestCase):
    """Больше `limit` — обрезаем по границе и честно говорим об этом `cut`."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-cut-"))
        self.addCleanup(shutil.rmtree, str(self.папка), True)

    def _текст(self, имя, текст):
        target = self.папка / имя
        # `newline=""` — иначе Windows перепишет «\n» в «\r\n», и счёт знаков
        # в тесте не совпадёт с содержимым файла.
        target.write_text(текст, encoding="utf-8", newline="")
        return target

    def test_короткий_файл_не_обрезается(self):
        текст = "Всего тридцать знаков текста."
        что = documents.read_text(self._текст("короткий.txt", текст))
        self.assertFalse(что["cut"])
        self.assertEqual(что["chars"], len(текст))

    def test_обрезка_идёт_по_границе_абзаца(self):
        # Два абзаца, режем посередине второго: обрыв не должен прийтись на
        # середину слова.
        первый = "Первый абзац. " * 20
        второй = "Второй абзац. " * 20
        target = self._текст("длинный.txt", первый + "\n\n" + второй)
        что = documents.read_text(target, limit=len(первый) + len("\n\n") + 20)
        self.assertTrue(что["cut"])
        self.assertNotIn("Второй", что["text"])
        self.assertLessEqual(len(что["text"]), len(первый) + 22)
        # `chars` — про весь файл, а не про отданный кусок: хозяину полезно
        # знать, сколько он не дослушал.
        self.assertEqual(что["chars"], len(первый + "\n\n" + второй))

    def test_без_абзацев_режем_по_предложению(self):
        текст = " ".join(f"Предложение номер {one}." for one in range(1, 40))
        что = documents.read_text(self._текст("предложения.txt", текст), limit=200)
        self.assertTrue(что["cut"])
        self.assertTrue(что["text"].endswith("."), что["text"][-40:])

    def test_одно_длинное_слово_обрезается_хоть_как(self):
        # Границы нет вовсе — режем по символу, но честно: `cut` и никакой
        # ошибки наружу.
        что = documents.read_text(self._текст("слитно.txt", "я" * 500), limit=100)
        self.assertTrue(что["ok"], что)
        self.assertTrue(что["cut"])
        self.assertEqual(len(что["text"]), 100)
        self.assertEqual(что["chars"], 500)

    def test_предел_поменьше_единицы_не_ломает(self):
        target = self._текст("обычный.txt", ОБРАЗЕЦ)
        for предел in (0, -5, 10 ** 9):
            with self.subTest(limit=предел):
                что = documents.read_text(target, limit=предел)
                self.assertTrue(что["ok"], что)
                self.assertIsInstance(что["cut"], bool)

    def test_по_умолчанию_двенадцать_тысяч(self):
        # Размер ответа инструмента ограничен: текст длиннее уходит в облако
        # весь, и запрос на каждом круге пухнет.
        self.assertEqual(documents.TEXT_LIMIT, 12000)
        длинный = "абзац. " * 5000
        что = documents.read_text(self._текст("длинный.txt", длинный))
        self.assertTrue(что["cut"])
        self.assertLessEqual(len(что["text"]), documents.TEXT_LIMIT)
        self.assertGreater(что["chars"], documents.TEXT_LIMIT)


class СтандартныеПапки(unittest.TestCase):
    """«Загрузки», «Документы», «Рабочий стол» — подменённые, не живые.

    Подменяем `core/folders.path_of`: настоящие папки хозяина тест не
    открывает и не перебирает, тем более в «Загрузках» (задание, «Чего НЕ
    делать»). Диски и хранилища Obsidian — тоже подмена: поиск по названию
    идёт по всем дискам (`core/files.py`), и без неё тест ушёл бы рыться в
    настоящих дисках хозяина.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-folders-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
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
        патчер = mock.patch("core.notes._vaults", return_value=[])
        патчер.start()
        self.addCleanup(патчер.stop)

    def _лежит(self, куда, имя, текст="страница", когда=None):
        target = self.папки[куда] / имя
        target.write_text(текст, encoding="utf-8")
        if когда is not None:
            os.utime(str(target), (когда, когда))
        return target


class ПоследнийСкачанный(СтандартныеПапки):
    """`latest_download`: самый новый, а недокачанное и нечитаемое — мимо."""

    def test_берёт_самый_новый(self):
        старый = self._лежит("downloads", "старый.pdf", "старый", 1_000_000)
        новый = self._лежит("downloads", "новый.pdf", "новый", 2_000_000)
        self.assertEqual(documents.latest_download(), новый)
        self.assertNotEqual(documents.latest_download(), старый)

    def test_пропускает_недокачанное(self):
        # Браузер ещё пишет файл: прочесть можно только половину, а хозяин
        # увидит «документ» и поверит.
        self._лежит("downloads", "целый.pdf", "целый", 1_000_000)
        for хвост in (".part", ".crdownload", ".tmp", ".download"):
            with self.subTest(хвост=хвост):
                self._лежит("downloads", f"новый.pdf{хвост}", "новый", 9_000_000)
                self.assertEqual(documents.latest_download().name, "целый.pdf")

    def test_пропускает_неподдерживаемое(self):
        self._лежит("downloads", "целый.pdf", "целый", 1_000_000)
        self._лежит("downloads", "новый.xlsx", "новый", 9_000_000)
        self.assertEqual(documents.latest_download().name, "целый.pdf")

    def test_папки_мимо(self):
        (self.папки["downloads"] / "папка.pdf").mkdir()
        self.assertIsNone(documents.latest_download())

    def test_пустые_загрузки_дают_отказ(self):
        self.assertIsNone(documents.latest_download())
        что = documents.pick("latest_download")
        self.assertFalse(что["ok"])
        self.assertIn("загрузках", что["why"])

    def test_смотрит_только_в_загрузки(self):
        self._лежит("documents", "чужой.pdf", "чужой", 9_000_000)
        self.assertIsNone(documents.latest_download())


class ПоНазванию(СтандартныеПапки):
    """`find_by_name`: слова названия — по всем дискам тем же поиском.

    Обход и очки — у `core/files.py` (см. `tests/test_files.py`), здесь проверяется
    только чтение: из найденного берётся то, что умеем читать.
    """

    def test_одно_совпадение(self):
        отчёт = self._лежит("downloads", "отчёт за сентябрь.txt")
        self.assertEqual(documents.find_by_name("отчёт за сентябрь"), [отчёт])

    def test_ищет_во_всех_трёх_папках(self):
        # Разные имена в каждой папке: с одинаковыми все три стали бы
        # кандидатами, и проверка ничего бы не говорила.
        for куда, имя in (("downloads", "отчёт из загрузок.txt"),
                          ("documents", "отчёт из документов.txt"),
                          ("desktop", "отчёт с рабочего стола.txt")):
            with self.subTest(папка=куда):
                target = self._лежит(куда, имя)
                self.assertEqual(documents.find_by_name(имя[:-4]), [target])

    def test_находит_и_во_вложенной_папке(self):
        вложенная = self.папки["documents"] / "Архив"
        вложенная.mkdir()
        target = вложенная / "старый отчёт.txt"
        target.write_text("да", encoding="utf-8")
        self.assertEqual(documents.find_by_name("отчёт"), [target])

    def test_лезет_и_глубже_одной_вложенности(self):
        # Поиск охватывает и вложенные папки, а не только верхний уровень.
        глубоко = self.папки["documents"] / "один" / "два" / "три"
        глубоко.mkdir(parents=True)
        target = глубоко / "отчёт.txt"
        target.write_text("да", encoding="utf-8")
        self.assertEqual(documents.find_by_name("отчёт"), [target])

    def test_несколько_равных_возвращает_кандидатов(self):
        первый = self._лежит("downloads", "отчёт.txt")
        второй = self._лежит("documents", "отчёт.txt")
        нашлось = documents.find_by_name("отчёт")
        self.assertEqual(set(нашлось), {первый, второй})
        # `pick` при равенстве не выбирает наугад, а просит переспросить.
        что = documents.pick("by_name", "отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(len(что["candidates"]), 2)
        self.assertIn("Переспроси", что["why"])

    def test_лучшее_совпадение_выигрывает(self):
        слабое = self._лежит("downloads", "отчёт старый.txt")
        self._лежит("downloads", "заметки.txt")
        сильное = self._лежит("downloads", "отчёт за сентябрь 2026.txt")
        нашлось = documents.find_by_name("отчёт за сентябрь 2026")
        self.assertEqual(нашлось, [сильное])
        self.assertNotIn(слабое, нашлось)

    def test_кандидатов_не_больше_пяти(self):
        for one in range(7):
            self._лежит("downloads", f"отчёт {one}.txt")
        self.assertEqual(len(documents.find_by_name("отчёт")), documents.CANDIDATES)

    def test_ничего_не_нашлось(self):
        self._лежит("downloads", "отчёт.txt")
        self.assertEqual(documents.find_by_name("договор аренды"), [])
        что = documents.pick("by_name", "договор аренды")
        self.assertFalse(что["ok"])
        self.assertIn("не нашла", что["why"])

    def test_пустой_запрос_и_одни_падежи_дают_отказ(self):
        for слова in ("", "   ", "и"):
            with self.subTest(слова=слова):
                self.assertEqual(documents.find_by_name(слова), [])
        self.assertFalse(documents.pick("by_name", "")["ok"])

    def test_нечитаемое_мимо(self):
        self._лежит("downloads", "отчёт.xlsx")
        self.assertEqual(documents.find_by_name("отчёт"), [])

    def test_одно_совпадение_идёт_в_pick(self):
        target = self._лежит("downloads", "отчёт.txt")
        self.assertEqual(documents.pick("by_name", "отчёт"),
                         {"ok": True, "path": target})


def _окно(hwnd, выделено):
    """Поддельное окно проводника: `HWND` и список выделенного."""
    предметы = NS(
        Count=len(выделено),
        # FolderItems.Item нумерует выделение с нуля, как COM проводника.
        Item=lambda номер: NS(Path=выделено[номер]),
    )
    return NS(HWND=hwnd, Document=NS(SelectedItems=lambda: предметы))


def _подмена_com(окна, впереди):
    """Подменяет весь COM: и `Dispatch`, и `GetForegroundWindow`."""
    оболочка = NS(Windows=lambda: окна)
    Dispatch = mock.Mock(return_value=оболочка)
    pythoncom = NS(CoInitialize=mock.Mock(), CoUninitialize=mock.Mock())
    клиент = NS(Dispatch=Dispatch)
    return mock.patch.dict(
        "sys.modules",
        {"win32com.client": клиент, "pythoncom": pythoncom},
    ), mock.patch("ctypes.windll.user32.GetForegroundWindow",
                  return_value=впереди), Dispatch


class ВыделенноеВПроводнике(unittest.TestCase):
    """`selected()` — по подменённому COM, без настоящего проводника.

    Настоящий `Shell.Application` вернул бы живые окна хозяина, а открывать
    окна проводника тест не должен (задание, «Чего НЕ делать»).
    """

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-selected-"))
        self.addCleanup(shutil.rmtree, str(self.папка), True)
        self.файл = self.папка / "отчёт.txt"
        self.файл.write_text(ОБРАЗЕЦ, encoding="utf-8")

    def _запустить(self, окна, впереди):
        подмена, перед, _ = _подмена_com(окна, впереди)
        with подмена, перед:
            return documents.selected()

    def test_берёт_окно_что_впереди(self):
        self.assertEqual(
            self._запустить([_окно(10, []), _окно(20, [str(self.файл)])], 20),
            self.файл)

    def test_впереди_проводник_без_выделения_берёт_другой(self):
        # Впереди открыто окно, где ничего не выбрано: тогда берём последнее
        # окно, где выделение есть.
        self.assertEqual(
            self._запустить([_окно(20, [str(self.файл)]), _окно(10, [])], 10),
            self.файл)

    def test_впереди_чужое_окно(self):
        # Впереди не проводник (HWND 999 не из списка) — берём последнее
        # окно с выделением.
        self.assertEqual(
            self._запустить([_окно(20, [str(self.файл)])], 999), self.файл)

    def test_нигде_нет_выделения(self):
        self.assertIsNone(self._запустить([_окно(20, []), _окно(10, [])], 20))

    def test_окон_нет_вообще(self):
        self.assertIsNone(self._запустить([], 20))

    def test_битое_окно_не_роняет(self):
        # Окно без `Document` (или проводник уже закрылся) — не повод ронять
        # разговор.
        кривое = NS(HWND=20)
        self.assertIsNone(self._запустить([кривое], 20))

    def test_в_pick_выбранное_читается(self):
        подмена, перед, _ = _подмена_com([_окно(20, [str(self.файл)])], 20)
        with подмена, перед:
            что = documents.pick("selected")
        self.assertTrue(что["ok"], что)
        self.assertEqual(что["path"], self.файл)

    def test_pick_без_выделения_отказывает(self):
        подмена, перед, _ = _подмена_com([_окно(20, [])], 20)
        with подмена, перед:
            что = documents.pick("selected")
        self.assertFalse(что["ok"])
        self.assertIn("не вижу выделенного файла", что["why"])

    def test_pick_с_папкой_отказывает(self):
        подмена, перед, _ = _подмена_com([_окно(20, [str(self.папка)])], 20)
        with подмена, перед:
            что = documents.pick("selected")
        self.assertFalse(что["ok"])
        self.assertIn("нужен файл", что["why"])

    def test_неизвестный_способ_отказывает(self):
        что = documents.pick("какой-то_выдуманный")
        self.assertFalse(что["ok"])
        self.assertIn("не поняла", что["why"])


def _мозг():
    """Голый `Brain` без сети: этот файл проверяет раздачу, а не облако."""
    import threading
    from collections import deque

    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = None
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.last_sources = []
    brain.on_event = None
    brain.actions = {}
    return brain


class Инструмент(СтандартныеПапки):
    """`hands.read_document`: объявление, наборы, раздача и журнал."""

    def _ответ(self, аргументы, события=None):
        return json.loads(hands.run_document(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None))

    def test_принадлежит_наборам(self):
        # Не в `LOCAL`: модель нужна сама, чтобы пересказать хозяину; в
        # `GUARDED` — нужна его цитата; в `JUDGED` — наружу уходит текст
        # документа, ровно как снимок экрана.
        self.assertNotIn(hands.DOC_NAME, hands.LOCAL)
        self.assertIn(hands.DOC_NAME, hands.GUARDED)
        self.assertIn(hands.DOC_NAME, hands.JUDGED)
        self.assertNotIn(hands.DOC_NAME, hands.CONFIRM)

    def test_because_обязателен(self):
        параметры = hands.DOC_TOOL["function"]["parameters"]
        self.assertIn("because", параметры["required"])
        self.assertIn("because", параметры["properties"])

    def test_какие_способы_и_режимы_объявлены(self):
        параметры = hands.DOC_TOOL["function"]["parameters"]
        self.assertEqual(параметры["properties"]["which"]["enum"],
                         ["open", "selected", "latest_download", "by_name"])
        self.assertEqual(параметры["properties"]["mode"]["enum"],
                         ["retell", "aloud"])
        self.assertEqual(параметры["properties"]["which"]["enum"],
                         list(hands.DOC_WHICH))
        self.assertEqual(параметры["properties"]["mode"]["enum"],
                         list(hands.DOC_MODES))
        self.assertIn("which", параметры["required"])

    def test_описание_учит_модель_пересказывать(self):
        описание = hands.DOC_TOOL["function"]["description"]
        self.assertIn("перескажи", описание)
        self.assertIn("retell", описание)
        self.assertIn("aloud", описание)
        # Обрезанный текст и выдумки — самые частые промахи здесь.
        self.assertIn("cut", описание)
        self.assertIn("не выдумывай", описание.lower())

    def test_читает_и_отдаёт_текст_модели(self):
        target = self._лежит("downloads", "отчёт.txt", ОБРАЗЕЦ)
        ответ = self._ответ({"which": "by_name", "name": "отчёт",
                             "because": "что в файле с отчётом?"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertEqual(ответ["name"], "отчёт.txt")
        self.assertIn("выросла на 12 процентов", ответ["text"])
        self.assertEqual(ответ["chars"], len(ОБРАЗЕЦ))
        self.assertFalse(ответ["cut"])
        # Путь в облако не отдаём: в нём имя пользователя Windows.
        self.assertNotIn(str(target), json.dumps(ответ, ensure_ascii=False))

    def test_последний_скачанный_тоже_читается(self):
        self._лежит("downloads", "старый.txt", "старый", 1_000_000)
        self._лежит("downloads", "свежий.txt", ОБРАЗЕЦ, 2_000_000)
        ответ = self._ответ({"which": "latest_download",
                             "because": "перескажи последний скачанный PDF"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertEqual(ответ["name"], "свежий.txt")

    def test_выбранный_в_проводнике_читается(self):
        target = self.папки["desktop"] / "выделенный.txt"
        target.write_text(ОБРАЗЕЦ, encoding="utf-8")
        подмена, перед, _ = _подмена_com([_окно(20, [str(target)])], 20)
        with подмена, перед:
            ответ = self._ответ({"which": "selected",
                                 "because": "прочитай выделенный документ"})
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertIn("выросла", ответ["text"])

    def test_кривые_аргументы_дают_ошибку(self):
        for аргументы in ("не json", "[]", ""):
            with self.subTest(аргументы=аргументы):
                self.assertIn("error", json.loads(hands.run_document(аргументы)))

    def test_неизвестные_which_и_mode_дают_ошибку(self):
        for аргументы in ({"which": "какой-то"},
                          {"which": "by_name", "mode": "крик"},
                          {"mode": "retell"}):
            with self.subTest(аргументы=аргументы):
                self.assertIn("error", self._ответ(аргументы))

    def test_чтение_вслух_без_голоса_отказывает(self):
        # Голос выключен — читать некому, и «прочитала» было бы враньём.
        # Сам разбор режима `aloud` живёт в tests/test_read_aloud.py.
        ответ = self._ответ({"which": "by_name", "name": "отчёт",
                             "mode": "aloud", "because": "прочитай вслух"})
        self.assertIn("вслух", ответ["error"])
        self.assertIn("читать вслух нечем", ответ["error"])

    def test_не_нашла_документ_отказывает(self):
        ответ = self._ответ({"which": "by_name", "name": "договор аренды",
                             "because": "что в файле с договором?"})
        self.assertIn("error", ответ)
        self.assertIn("не нашла", ответ["error"])

    def test_судья_слышит_способ_словами(self):
        for аргументы in ({"which": "selected"},
                          {"which": "latest_download"},
                          {"which": "by_name", "name": "отчёт"}):
            with self.subTest(аргументы=аргументы):
                слова = hands.action_words(hands.DOC_NAME, аргументы)
                self.assertIn("прочитать документ", слова)
                # Судья перечислений не видит — только слова.
                self.assertNotIn("by_name", слова)
                self.assertNotIn("latest_download", слова)

    def test_судье_не_ломают_кривые_аргументы(self):
        for аргументы in ({}, {"which": None}, {"which": "выдумка"}, None,
                          "мусор"):
            with self.subTest(аргументы=аргументы):
                self.assertIsInstance(
                    hands.action_words(hands.DOC_NAME, аргументы), str)

    def test_в_журнал_идут_только_размеры(self):
        self._лежит("downloads", "отчёт.txt", ОБРАЗЕЦ)
        события = []
        self._ответ({"which": "by_name", "name": "отчёт",
                     "because": "что в файле с отчётом?"}, события)
        self.assertTrue(события, события)
        вид, строка = события[0]
        self.assertEqual(вид, "document")
        self.assertIn("отчёт.txt", строка)
        self.assertRegex(строка, r"\d+ знак")
        # Текст документа в журнал не уходит никогда.
        self.assertNotIn("выросла", json.dumps(события, ensure_ascii=False))

    def test_неудача_тоже_в_журнале(self):
        события = []
        self._ответ({"which": "by_name", "name": "договор аренды",
                     "because": "что в договоре?"}, события)
        self.assertEqual(события[0][0], "document_failed")

    def test_строка_журнала_без_страниц_у_текста(self):
        # У `.txt` страниц нет, и выдумывать их в строке нельзя.
        self.assertEqual(documents.journal_line(
            {"name": "отчёт.txt", "pages": 0, "chars": 42}),
            "отчёт.txt, 42 знака")
        self.assertEqual(documents.journal_line(
            {"name": "отчёт.pdf", "pages": 12, "chars": 8400}),
            "отчёт.pdf, 12 стр., 8400 знаков")

    def test_журнал_пульта_умеет_эту_строку(self):
        from ui.web_runtime import WebRuntime

        self.assertEqual(
            WebRuntime._log_messages("document", "отчёт.pdf, 12 стр., 8400 знаков"),
            ["документ: отчёт.pdf, 12 стр., 8400 знаков"])
        self.assertEqual(
            WebRuntime._log_messages("document_failed", "PDF под паролем"),
            ["документ не прочитан: PDF под паролем"])


class НаборИнструментов(unittest.TestCase):
    """`brain`: инструмент в наборе всегда и в стабильном порядке."""

    def _имена(self, текст="как дела?"):
        return [spec["function"]["name"]
                for spec in _мозг()._tool_list(текст)]

    def test_инструмент_всегда_в_наборе(self):
        # Ни от `apps.json`, ни от настроек голоса он не зависит: файлы у
        # хозяина есть всегда, а набор на этом держится кешем.
        self.assertIn(hands.DOC_NAME, self._имена())
        self.assertEqual(self._имена("как дела?"),
                         self._имена("перескажи последний скачанный PDF"))

    def test_стоит_сразу_за_напоминаниями(self):
        имена = self._имена()
        после = имена[имена.index(hands.CANCEL_REM_NAME) + 1:]
        self.assertEqual(после[0], hands.DOC_NAME)

    def test_до_действий_и_не_зависит_от_них(self):
        brain = _мозг()
        brain.actions = {"screenshot": lambda: "готово"}
        с_действиями = [spec["function"]["name"]
                        for spec in brain._tool_list("сделай скриншот")]
        without = self._имена()
        self.assertLess(с_действиями.index(hands.DOC_NAME),
                        с_действиями.index(hands.SHOT_NAME))
        self.assertIn(hands.DOC_NAME, without)


class Способности(unittest.TestCase):
    """Промпт и страница «Команды» должны знать про чтение документов."""

    def test_в_промпте_есть_строка_о_документах(self):
        # Без строки в `abilities` модель на «перескажи последний скачанный
        # PDF» отвечала бы «я не умею», ровно как с заметками до их
        # инструмента.
        from core import abilities

        подходящие = [строка for строка in abilities.ACTIONS
                      if "документ" in строка]
        self.assertTrue(подходящие)
        for слово in ("PDF", "Word", "пересказ"):
            self.assertTrue(any(слово.lower() in строка.lower()
                                for строка in подходящие), слово)
        self.assertIn("read_document", abilities.describe([]))

    def test_строка_в_commands_skills(self):
        строки = [навык for навык in commands.SKILLS
                  if навык.title == "Документы"]
        self.assertEqual(len(строки), 1)
        навык = строки[0]
        self.assertTrue(навык.examples)
        # Примеры — про пересказ, а не про запуск и не про открытие.
        self.assertTrue(any("перескажи" in пример.lower()
                            for пример in навык.examples), навык.examples)
        self.assertIn("пересказ", навык.does.lower() + навык.does)
        # Навык виден и на странице «Команды» — она собирается оттуда же.
        self.assertIn("Документы",
                      [одна["title"] for одна in commands.commands_guide()["skills"]])


class Зависимости(unittest.TestCase):
    """Зависимости чтения документов указаны в `requirements.txt` с версиями."""

    def _файл(self):
        return Path(__file__).resolve().parents[1] / "requirements.txt"

    def test_обе_библиотеки_записаны(self):
        строки = self._файл().read_text(encoding="utf-8")
        for пакет in ("pypdf", "python-docx"):
            with self.subTest(пакет=пакет):
                строки_пакета = [строка.strip() for строка in строки.splitlines()
                                 if строка.strip().startswith(пакет + "==")]
                self.assertEqual(len(строки_пакета), 1, строки_пакета)
                # Версия должна быть явно зафиксирована.
                версия = строки_пакета[0].split("==", 1)[1]
                self.assertTrue(версия and " " not in версия, строки_пакета[0])

    def test_версии_совпадают_с_установленными(self):
        import importlib.metadata as метаданные

        строки = self._файл().read_text(encoding="utf-8")
        for пакет in ("pypdf", "python-docx"):
            with self.subTest(пакет=пакет):
                записано = [строка.strip() for строка in строки.splitlines()
                            if строка.strip().startswith(пакет + "==")][0]
                стоит = метаданные.version(пакет)
                self.assertEqual(записано, f"{пакет}=={стоит}")

    def test_импорт_проекта_не_требует_их_заранее(self):
        # `documents` импортирует `pypdf` и `python-docx` внутри функций: без
        # них проект и тесты должны работать, а чтение — честно отказывать.
        исходник = Path(documents.__file__).read_text(encoding="utf-8")
        начало = исходник.index("SUPPORTED")
        тело = исходник[начало:]
        for пакет in ("import pypdf", "import docx"):
            with self.subTest(пакет=пакет):
                self.assertNotIn(пакет, тело.split("\ndef ")[0])


if __name__ == "__main__":
    unittest.main()
