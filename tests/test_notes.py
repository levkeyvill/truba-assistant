r"""Заметки: хранилище, причёсывание, диктовка, инструменты, API.

Ни сети, ни микрофона, ни облака. `core/notes.py` пишет только во временную
папку, причёсывание и мозг подменены, проводник — заглушкой. Настоящие
«Документы» и `settings.json` тест не трогает: папку подменяет
`tests/test_data_guard.py` на весь прогон.

Не здесь проверяется то, что видно только на живой машине: сколько на
самом деле секунд отвечает облако на причёсывание и как Obsidian рисует
сворачиваемую цитату.
"""

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import commands, hands, notes
from core.voice_loop import VoiceLoop


class TmpFolder(unittest.TestCase):
    """Общая заготовка: папка заметок во временной на весь тест."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-notes-"))
        self._saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", self._saved))

    def when(self, day=27, hour=2, minute=15):
        return datetime.datetime(2026, 9, day, hour, minute)


# --- Хранилище -----------------------------------------------------------


class StorageTests(TmpFolder):
    def test_first_entry_creates_a_file_obsidian_can_open(self):
        path = notes.add("Книги", "Мастер и Маргарита", "Воланд как зеркало",
                         "Абзац.\n\nИ второй.", raw="ну значит волданд это",
                         when=self.when())
        self.assertEqual(path, self.tmp / "Книги" / "Мастер и Маргарита.md")
        body = path.read_text(encoding="utf-8")
        self.assertTrue(body.startswith("---\n"))
        self.assertIn("тема: Мастер и Маргарита", body)
        self.assertIn("раздел: Книги", body)
        self.assertIn("создано: 2026-09-27", body)
        self.assertIn("tags: [заметки-трубы, книги]", body)
        self.assertIn("# Мастер и Маргарита", body)
        self.assertIn("## 27.09.2026, 02:15 — Воланд как зеркало", body)
        # Сырое в своей свёртке: спрятано, но на месте.
        self.assertIn("> [!quote]- Как было сказано", body)
        self.assertIn("> ну значит волданд это", body)

    def test_second_entry_goes_to_the_end_and_moves_updated(self):
        notes.add("Книги", "Тема", "Первая", "Один.", when=self.when(26, 23, 0))
        path = notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when())
        body = path.read_text(encoding="utf-8")
        # Новая запись — в конец: тема это хронология, а не свалка.
        self.assertLess(body.index("— Первая"), body.index("— Вторая"))
        self.assertIn("обновлено: 2026-09-27", body)
        self.assertEqual([e["title"]
                          for e in notes.read("Книги", "Тема")["entries"]],
                         ["Первая", "Вторая"])

    def test_updated_changes_when_the_day_changes(self):
        path = notes.add("Книги", "Тема", "Первая", "Один.", when=self.when(26))
        self.assertIn("обновлено: 2026-09-26", path.read_text(encoding="utf-8"))
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27))
        body = path.read_text(encoding="utf-8")
        self.assertIn("обновлено: 2026-09-27", body)
        self.assertIn("создано: 2026-09-26", body)

    def test_entry_without_raw_has_no_quote_block(self):
        # Запись от модели (hands.save_note): сырого нет, и пустая свёртка
        # в файле смотрелась бы мусором.
        path = notes.add("Разное", "Входящие", "Без обработки", "Текст.")
        self.assertNotIn("[!quote]", path.read_text(encoding="utf-8"))

    def test_names_are_cleaned_of_forbidden_characters(self):
        path = notes.add("Книги:Мир", "Мастер/Маргарита #1", "Тема", "Текст.")
        self.assertEqual(path.name, "Мастер Маргарита 1.md")
        self.assertEqual(path.parent.name, "Книги Мир")

    def test_long_name_is_cut(self):
        path = notes.add("Идеи", "а" * 200, "Тема", "Текст.")
        self.assertLessEqual(len(path.stem), notes.MAX_NAME)

    def test_read_parses_own_format(self):
        notes.add("Книги", "Тема", "Первая", "Один текст.",
                  raw="первое сырое", when=self.when())
        notes.add("Книги", "Тема", "Вторая", "Второй текст.", when=self.when(27, 3))
        data = notes.read("Книги", "Тема")
        self.assertEqual(data["topic"], "Тема")
        self.assertEqual(data["front"]["раздел"], "Книги")
        first, second = data["entries"]
        self.assertEqual(first["when"], "27.09.2026, 02:15")
        self.assertEqual(first["title"], "Первая")
        self.assertEqual(first["text"], "Один текст.")
        self.assertEqual(first["raw"], "первое сырое")
        self.assertEqual(second["raw"], "")

    def test_read_survives_a_file_edited_by_hand(self):
        # Хозяин правит файл в Obsidian: снёс шапку, вписал свой текст.
        path = notes.add("Книги", "Тема", "Первая", "Один.")
        body = path.read_text(encoding="utf-8")
        path.write_text(
            "# Тема\n\nСтрока, вписанная руками.\n\n" + body[body.index("## "):],
            encoding="utf-8")
        data = notes.read("Книги", "Тема")
        self.assertEqual(data["front"], {})
        self.assertEqual(data["entries"][0]["title"], "Первая")

    def test_text_without_a_heading_is_not_lost(self):
        path = notes.add("Книги", "Тема", "Первая", "Один.")
        path.write_text(path.read_text(encoding="utf-8")
                        + "\nПросто строка без заголовка.\n", encoding="utf-8")
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27, 4))
        self.assertIn("Просто строка без заголовка.",
                      path.read_text(encoding="utf-8"))

    def test_missing_topic_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            notes.read("Книги", "Нет такой")

    def test_sections_list_topics_with_counts(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27, 4))
        listed = notes.sections()
        self.assertEqual([s["name"] for s in listed], ["Книги"])
        topic = listed[0]["topics"][0]
        self.assertEqual(topic["name"], "Тема")
        self.assertEqual(topic["entries"], 2)
        self.assertEqual(topic["updated"], "2026-09-27")

    def test_entry_deletion_moves_it_to_trash(self):
        notes.add("Книги", "Тема", "Первая", "Один.", when=self.when())
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27, 4))
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.delete_entry("Книги", "Тема", 0, heading)
        self.assertEqual([e["title"] for e in notes.read("Книги", "Тема")["entries"]],
                         ["Вторая"])
        trash = (self.tmp / notes.TRASH / "Тема — удалённое.md").read_text(
            encoding="utf-8")
        self.assertIn("— Первая", trash)
        self.assertIn("Один.", trash)

    def test_wrong_heading_leaves_the_file_whole(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27, 4))
        path = self.tmp / "Книги" / "Тема.md"
        before = path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "изменился"):
            notes.delete_entry("Книги", "Тема", 0, "другая запись")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_topic_deletion_moves_the_file_to_trash(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        notes.delete_topic("Книги", "Тема")
        self.assertFalse((self.tmp / "Книги" / "Тема.md").exists())
        self.assertTrue((self.tmp / notes.TRASH / "Тема — удалённое.md").exists())

    def test_empty_section_folder_is_removed(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        notes.delete_topic("Книги", "Тема")
        self.assertFalse((self.tmp / "Книги").exists())

    def test_names_cannot_leave_the_notes_folder(self):
        for имя in ("..", "..\\..\\Windows", "C:\\Windows", "/etc/passwd",
                    "Книги\\..\\..\\Побег"):
            with self.subTest(имя=имя):
                with self.assertRaises(ValueError):
                    notes.topic_path("Книги", имя)
        self.assertEqual(list(self.tmp.rglob("*.md")), [])

    def test_section_name_cannot_leave_either(self):
        with self.assertRaises(ValueError):
            notes.topic_path("..", "Тема")

    def test_no_leftover_part_files(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        self.assertEqual([p.name for p in self.tmp.rglob("*.part")], [])


# --- Правка записей -------------------------------------------------------


class EditTests(TmpFolder):
    def test_an_entry_gets_a_new_heading_and_text_with_its_own_date(self):
        notes.add("Книги", "Тема", "Первая", "Старый текст.",
                  raw="ну значит так", when=self.when(26, 23, 0))
        notes.add("Книги", "Тема", "Вторая", "Два.", when=self.when(27, 4))
        path = self.tmp / "Книги" / "Тема.md"
        before = path.read_text(encoding="utf-8")
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.edit_entry("Книги", "Тема", 0, heading, "  Правленый  ",
                         "Новый текст.")
        after = path.read_text(encoding="utf-8")
        entry = notes.read("Книги", "Тема")["entries"][0]
        # Дата прежняя: правка мысли не делает её сегодняшней.
        self.assertEqual(entry["when"], "26.09.2026, 23:00")
        self.assertEqual(entry["title"], "Правленый")
        self.assertEqual(entry["text"], "Новый текст.")
        # Сырая диктовка — единственная честная копия того, что он сказал.
        self.assertEqual(entry["raw"], "ну значит так")
        # Соседняя запись и голова файла — байт в байт как были.
        self.assertIn("## 27.09.2026, 04:15 — Вторая\n\nДва.\n", after)
        # Голова та же, кроме даты «обновлено» — она теперь сегодняшняя.
        def без_даты(текст):
            голова = текст[:текст.index("## 26.09.")]
            return [с for с in голова.split("\n") if not с.startswith("обновлено:")]
        self.assertEqual(без_даты(after), без_даты(before))
        self.assertIn("создано: 2026-09-26", after)
        self.assertIn(f"обновлено: {datetime.date.today():%Y-%m-%d}", after)

    def test_a_stale_heading_is_refused_and_nothing_is_lost(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        path = self.tmp / "Книги" / "Тема.md"
        before = path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "изменился"):
            notes.edit_entry("Книги", "Тема", 0, "устаревшая запись",
                             "Новое", "Текст.")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_wrong_number_is_refused(self):
        notes.add("Книги", "Тема", "Первая", "Один.")
        with self.assertRaisesRegex(ValueError, "номером нет"):
            notes.edit_entry("Книги", "Тема", 5, "что угодно", "Новое", "Текст.")

    def test_editing_a_note_without_raw_adds_no_quote(self):
        # Цитаты не было — правка не должна её выдумывать: сырого диктования
        # у рукописной записи не существует.
        path = notes.add("Книги", "Тема", "Первая", "Один.", when=self.when())
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.edit_entry("Книги", "Тема", 0, heading, "Правлена", "Два.")
        self.assertNotIn("[!quote]", path.read_text(encoding="utf-8"))
        self.assertEqual(notes.read("Книги", "Тема")["entries"][0]["text"],
                         "Два.")

    def test_a_heading_inside_the_text_is_pushed_down(self):
        # Иначе разбор принял бы `## ` за новую запись.
        path = notes.add("Книги", "Тема", "Первая", "Один.", when=self.when())
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.edit_entry("Книги", "Тема", 0, heading, "Правлена",
                         "# Шапка\n\n## Подшапка\n\nТекст.")
        body = path.read_text(encoding="utf-8")
        self.assertIn("### Шапка", body)
        self.assertIn("### Подшапка", body)
        self.assertEqual(len(notes.read("Книги", "Тема")["entries"]), 1)

    def test_an_empty_title_keeps_the_date_and_drops_the_name(self):
        notes.add("Книги", "Тема", "Первая", "Один.", when=self.when())
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.edit_entry("Книги", "Тема", 0, heading, "  ", "Два.")
        self.assertEqual(notes.read("Книги", "Тема")["entries"][0]["heading"],
                         "27.09.2026, 02:15")

    def test_an_empty_text_is_refused_and_nothing_changes(self):
        path = notes.add("Книги", "Тема", "Первая", "Один.")
        before = path.read_text(encoding="utf-8")
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        with self.assertRaisesRegex(ValueError, "пустой текст"):
            notes.edit_entry("Книги", "Тема", 0, heading, "Новое", "   \n ")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_editing_nothing_goes_to_the_trash_nowhere(self):
        # Правка — не удаление: корзина заметок остаётся нетронутой.
        notes.add("Книги", "Тема", "Первая", "Один.")
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        notes.edit_entry("Книги", "Тема", 0, heading, "Правлена", "Два.")
        self.assertFalse((self.tmp / notes.TRASH).exists())


# --- Переименование тем ---------------------------------------------------


class RenameTests(TmpFolder):
    def test_a_topic_is_renamed_in_the_same_section(self):
        notes.add("Книги", "Мастер", "Первая", "Один.", when=self.when())
        path = notes.rename_topic("Книги", "Мастер", "Мастер и Маргарита")
        self.assertEqual(path, self.tmp / "Книги" / "Мастер и Маргарита.md")
        self.assertFalse((self.tmp / "Книги" / "Мастер.md").exists())
        body = path.read_text(encoding="utf-8")
        self.assertIn("тема: Мастер и Маргарита", body)
        self.assertIn("# Мастер и Маргарита", body)
        self.assertIn("tags: [заметки-трубы, книги]", body)
        # Записи — не трогаем.
        self.assertIn("## 27.09.2026, 02:15 — Первая", body)
        self.assertIn("Один.", body)
        self.assertEqual(notes.read("Книги", "Мастер и Маргарита")["topic"],
                         "Мастер и Маргарита")

    def test_a_topic_moves_to_another_section(self):
        notes.add("Книги", "Мастер", "Первая", "Один.")
        path = notes.rename_topic("Книги", "Мастер", "Мастер", "Идеи")
        self.assertEqual(path, self.tmp / "Идеи" / "Мастер.md")
        body = path.read_text(encoding="utf-8")
        self.assertIn("раздел: Идеи", body)
        self.assertIn("tags: [заметки-трубы, идеи]", body)
        # Старый раздел опустел — папка без файлов только мешает.
        self.assertFalse((self.tmp / "Книги").exists())
        self.assertIn("Один.", body)

    def test_renaming_only_the_case_is_not_a_refusal(self):
        # На Windows «Книга.md» и «книга.md» — один и тот же файл, и хозяин
        # вправе поправить регистр: это не «тема с таким именем уже есть».
        notes.add("Книги", "Книга", "Первая", "Один.")
        path = notes.rename_topic("Книги", "Книга", "книга")
        self.assertEqual(path.name, "книга.md")
        # Имя на диске — новое, а не прежнее «Книга.md» с новой шапкой.
        self.assertEqual(sorted(p.name for p in (self.tmp / "Книги").iterdir()),
                         ["книга.md"])
        body = path.read_text(encoding="utf-8")
        self.assertIn("тема: книга", body)
        self.assertIn("Один.", body)

    def test_renaming_into_an_existing_topic_is_refused(self):
        # Склеивать нельзя: две разные мысли молча слились бы в одну.
        first = notes.add("Книги", "Первая", "Запись", "Один.")
        second = notes.add("Книги", "Вторая", "Запись", "Два.")
        before = (first.read_text(encoding="utf-8"),
                  second.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "уже есть"):
            notes.rename_topic("Книги", "Первая", "Вторая")
        self.assertEqual(first.read_text(encoding="utf-8"), before[0])
        self.assertEqual(second.read_text(encoding="utf-8"), before[1])

    def test_a_missing_topic_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            notes.rename_topic("Книги", "Нет такой", "Другая")

    def test_rename_into_a_name_that_leaves_the_folder_is_refused(self):
        path = notes.add("Книги", "Тема", "Первая", "Один.")
        before = path.read_text(encoding="utf-8")
        for имя in ("..", "..\\..\\Windows", "C:\\Windows", "/etc/passwd"):
            with self.subTest(имя=имя):
                with self.assertRaises(ValueError):
                    notes.rename_topic("Книги", "Тема", имя)
        with self.assertRaises(ValueError):
            notes.rename_topic("Книги", "Тема", "Тема", "..")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*")),
                         ["Книги", "Тема.md"])

    def test_renaming_a_file_edited_by_hand_keeps_its_entries(self):
        # Файл без нашей шапки: переименование не должно навязывать шапку
        # поверх чужой правки — меняется только строка `# `.
        path = notes.add("Книги", "Тема", "Первая", "Один.")
        body = path.read_text(encoding="utf-8")
        path.write_text("# Тема\n\nСвоей строкой.\n\n" + body[body.index("## "):],
                        encoding="utf-8")
        new = notes.rename_topic("Книги", "Тема", "Другая")
        after = new.read_text(encoding="utf-8")
        self.assertTrue(after.startswith("# Другая\n"))
        self.assertIn("Своей строкой.", after)
        self.assertNotIn("тема:", after)
