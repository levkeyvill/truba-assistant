"""Заметки в Obsidian: где его хранилище и как открыть тему из пульта.

Obsidian держит список хранилищ в `%APPDATA%\\obsidian\\obsidian.json`.
Читать его приходится на каждый запрос списка, поэтому подменяется он
целиком: настоящий файл хозяина в тесте не читается и не пишется.

Проверяется главное: путь к файлу собирается только через `topic_path`,
то есть имя из запроса не может вывести ссылку `obsidian://` за папку
заметок. Пульт виден всей сети, а такая ссылка открыла бы хозяину любой
файл на его диске.

Здесь же — разбор Markdown заметки: он вынесен в чистую функцию
(`ui/web/notes_markdown.js`) и проверяется в node, без браузера. Тест на
python зовёт node, чтобы этот файл нельзя было забыть: без node проверка
пропускается, как и остальные проверки страниц.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core import notes
from ui.web_runtime import WebRuntime


class MarkdownTests(unittest.TestCase):
    """Синтаксис и разбор: самые частые опечатки видны только в браузере."""

    def test_the_pult_scripts_collect(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node не установлен")
        веб = config.ROOT / "ui" / "web"
        for имя in ("pult.js", "notes_markdown.js"):
            with self.subTest(файл=имя):
                result = subprocess.run([node, "--check", str(веб / имя)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_markdown_of_a_note_is_parsed(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node не установлен")
        result = subprocess.run(
            [node, str(config.ROOT / "tests" / "notes_markdown.mjs")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_russian_function_called_in_the_pult_exists(self):
        # `node --check` молчит о вызове несуществующей функции: страница
        # просто падает в тот момент, когда до неё доходит. Именно так
        # однажды потерялась лента «Голоса» (coordination/ГРАБЛИ.md).
        скрипт = (config.ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        объявлены = set(re.findall(r"function\s+([А-Яа-яЁё][\wЁё]*)", скрипт))
        вызваны = set(re.findall(r"(?<![\w.])([а-яё][а-яё0-9]*)\s*\(", скрипт, re.I))
        # Методы и чужие имена в список не входят: проверяем только то, что
        # начинается с «заметки» или «нарисовать» — наш код этой страницы.
        наши = {имя for имя in вызваны
                if имя.startswith("заметки") or имя.startswith("нарисоватьЗаметки")}
        self.assertTrue(наши, 'раздел «Заметки» не найден в pult.js')
        self.assertEqual(sorted(наши - объявлены), [],
                         "вызваны, но не объявлены: вызов упал бы в браузере")


class _Tmp(unittest.TestCase):
    """Папка заметок и obsidian.json — обе во временной папке прогона."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-obsidian-"))
        # Хранилище — родитель папки заметок, а не сосед: Obsidian видит только
        # то, что внутри его хранилища, иначе кнопка «Открыть в Obsidian»
        # обещала бы хозяину то, чего он не увидит.
        self.vault = self.tmp / "Хранилище"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.заметки = self.vault / "Заметки Трубы"
        saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.заметки)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", saved))
        # %APPDATA% тоже подменяем: настоящая папка Obsidian — хозяина.
        patcher = mock.patch.dict(os.environ, {"APPDATA": str(self.tmp / "appdata")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _obsidian_json(self, данные) -> None:
        path = notes.obsidian_json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(данные, encoding="utf-8")

    def _хранилище(self) -> dict:
        return {"vaults": {"abc": {"path": str(self.vault), "ts": 1, "open": True}}}

    def _runtime(self) -> WebRuntime:
        runtime = object.__new__(WebRuntime)
        runtime._remember = lambda kind, payload: None
        return runtime


class VaultTests(_Tmp):
    def test_vault_around_the_notes_folder_is_found(self):
        self._obsidian_json(json.dumps(self._хранилище()))
        self.assertEqual(notes.obsidian_vault(), self.vault)

    def test_vault_may_be_the_notes_folder_itself(self):
        # Хозяин мог открыть «Заметки Трубы» как хранилище — это самый
        # вероятный случай, и он тоже должен работать.
        self._obsidian_json(json.dumps(
            {"vaults": {"x": {"path": str(self.заметки)}}}))
        self.assertEqual(notes.obsidian_vault(), self.заметки)

    def test_no_obsidian_json_means_no_vault(self):
        # Хозяин мог и не ставить Obsidian. Молча считаем, что хранилища нет.
        self.assertIsNone(notes.obsidian_vault())

    def test_a_broken_json_means_no_vault(self):
        for мусор in ("не json вовсе", "{", "[]", '{"vaults": "не словарь"}',
                      '{"vaults": {"a": "не словарь"}}',
                      '{"vaults": {"a": {"path": ""}}}'):
            with self.subTest(мусор=мусор):
                self._obsidian_json(мусор)
                self.assertIsNone(notes.obsidian_vault())

    def test_a_vault_elsewhere_does_not_count(self):
        # Obsidian открыт, но на другую папку: читать его нечем, и кнопка
        # «Открыть в Obsidian» показываться не должна.
        self._obsidian_json(json.dumps(
            {"vaults": {"x": {"path": str(self.tmp / "Чужая папка")}}}))
        self.assertIsNone(notes.obsidian_vault())

    def test_a_bare_relative_path_is_not_a_vault(self):
        self._obsidian_json(json.dumps({"vaults": {"x": {"path": "Заметки"}}}))
        self.assertIsNone(notes.obsidian_vault())

    def test_without_appdata_nothing_is_read_from_the_working_folder(self):
        # Без %APPDATA% искать негде: иначе чтение ушло бы в папку проекта.
        with mock.patch.dict(os.environ, {"APPDATA": ""}):
            self.assertIsNone(notes.obsidian_json())
            self.assertIsNone(notes.obsidian_vault())


class ListFieldTests(_Tmp):
    def test_the_list_carries_the_vault_path(self):
        self._obsidian_json(json.dumps(self._хранилище()))
        answer = self._runtime().notes_list()
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["obsidian"], str(self.vault))

    def test_the_field_is_null_without_obsidian(self):
        self.assertIsNone(self._runtime().notes_list()["obsidian"])


class LinkTests(_Tmp):
    def test_the_link_points_at_the_topic_file(self):
        path = notes.add("Книги", "Тема", "Первая", "текст")
        link = notes.obsidian_link("Книги", "Тема")
        self.assertTrue(link.startswith("obsidian://open?path="), link)
        from urllib.parse import unquote
        # Пробелы и кириллица в пути закодированы: иначе Windows или сам
        # Obsidian разрежут ссылку на полпути.
        self.assertNotIn(" ", link.split("?path=", 1)[1])
        self.assertEqual(unquote(link.split("?path=", 1)[1]), str(path))

    def test_the_link_cannot_walk_out_of_the_folder(self):
        for плохо, тема in (("..", "Побег"), ("Книги", ".."),
                            ("C:\\Windows", "Побег"), ("", "Побег")):
            with self.subTest(раздел=плохо, тема=тема):
                with self.assertRaises(ValueError):
                    notes.obsidian_link(плохо, тема)

    def test_a_missing_topic_is_not_a_link(self):
        with self.assertRaises(FileNotFoundError):
            notes.obsidian_link("Книги", "Нет такой")


class OpenThroughRuntimeTests(_Tmp):
    """`POST /api/notes/open` с `obsidian: true` — через слой веб-пульта."""

    def test_the_link_is_opened_and_startfile_is_not_really_called(self):
        path = notes.add("Книги", "Тема", "Первая", "текст")
        with mock.patch.object(os, "startfile") as opened:
            answer = self._runtime().notes_open(
                {"section": "Книги", "topic": "Тема", "obsidian": True})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["path"], str(path))
        self.assertEqual(opened.call_args[0][0], answer["link"])
        self.assertTrue(answer["link"].startswith("obsidian://open?path="))

    def test_out_of_the_folder_is_refused_before_anything_is_opened(self):
        with mock.patch.object(os, "startfile") as opened:
            with self.assertRaises(ValueError):
                self._runtime().notes_open(
                    {"section": "..", "topic": "Побег", "obsidian": True})
        opened.assert_not_called()

    def test_obsidian_opens_a_file_not_a_folder(self):
        with mock.patch.object(os, "startfile") as opened:
            with self.assertRaises(ValueError):
                self._runtime().notes_open({"obsidian": True})
        opened.assert_not_called()

    def test_without_the_flag_the_folder_still_opens_as_before(self):
        # Обычное открытие не должно было сломаться рядом с новой веткой.
        with mock.patch.object(os, "startfile") as opened:
            self._runtime().notes_open({})
        self.assertEqual(opened.call_args[0][0], str(self.заметки))


if __name__ == "__main__":
    unittest.main()
