"""API заметок для пульта: список, тема, удаление, открыть.

Сервер поднимается по-настоящему, но на свободном порту и без погоды и
проводника: `os.startfile` подменён, папка заметок — временная. Клиент —
`TestClient`, он стучится как `testclient`, поэтому проходит проверку
«только с компьютера»; для проверки отказа подменён адрес клиента.

Проверяется ещё одно, самое важное: имя из запроса не может вывести за папку
заметок. Пульт виден всей сети, и путь с `..` в имени — это чтение и
удаление чужих файлов хозяина.
"""

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import notes, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class NotesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Погода ходит в сеть, а проводник открывает окна: в тесте они не
        # нужны, иначе тест дёргал бы настоящие окна хозяина.
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            import time
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-api-"))
        self._saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", self._saved))
        # Пульт: настоящие методы, но без журнала и без телефона.
        runtime = object.__new__(WebRuntime)
        runtime._remember = lambda kind, payload: None
        self.server.runtime = runtime
        self.client = TestClient(self.server._app)
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def _заметка(self, раздел="Книги", тема="Тема", заголовок="Первая",
                  текст="Один."):
        return notes.add(раздел, тема, заголовок, текст)

    def test_the_list_shows_sections_topics_and_counts(self):
        self._заметка()
        self._заметка(тема="Другая")
        body = self.client.get("/api/notes").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["root"], str(self.tmp))
        разделы = {s["name"]: s for s in body["sections"]}
        self.assertIn("Книги", разделы)
        темы = {t["name"]: t for t in разделы["Книги"]["topics"]}
        self.assertEqual(set(темы), {"Тема", "Другая"})
        self.assertEqual(темы["Тема"]["entries"], 1)
        self.assertTrue(темы["Тема"]["updated"])

    def test_an_empty_folder_lists_nothing(self):
        # Пока заметок нет, пульт не должен показывать пустые разделы.
        self.assertEqual(self.client.get("/api/notes").json()["sections"], [])

    def test_one_topic_comes_with_its_entries(self):
        self._заметка()
        self._заметка(заголовок="Вторая", текст="Два.")
        body = self.client.get("/api/notes/topic",
                               params={"section": "Книги", "topic": "Тема"}).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["front"]["раздел"], "Книги")
        self.assertEqual([e["title"] for e in body["entries"]],
                         ["Первая", "Вторая"])
        self.assertEqual(body["entries"][0]["text"], "Один.")

    def test_a_missing_topic_is_a_404(self):
        answer = self.client.get("/api/notes/topic",
                                 params={"section": "Книги", "topic": "Нет"})
        self.assertEqual(answer.status_code, 404)
        self.assertFalse(answer.json()["ok"])

    def test_an_entry_is_deleted_by_number_and_heading(self):
        self._заметка()
        self._заметка(заголовок="Вторая", текст="Два.")
        topic = self.client.get("/api/notes/topic",
                                params={"section": "Книги", "topic": "Тема"}).json()
        heading = topic["entries"][0]["heading"]
        answer = self.client.post("/api/notes/delete", json={
            "section": "Книги", "topic": "Тема", "index": 0, "heading": heading})
        self.assertTrue(answer.json()["ok"])
        left = self.client.get("/api/notes/topic",
                               params={"section": "Книги", "topic": "Тема"}).json()
        self.assertEqual([e["title"] for e in left["entries"]], ["Вторая"])
        # Удалённое не пропало, а лежит в корзине заметок.
        self.assertIn("Один.",
                      (self.tmp / notes.TRASH / "Тема — удалённое.md")
                      .read_text(encoding="utf-8"))

    def test_a_stale_heading_is_refused_and_nothing_is_lost(self):
        # Хозяин правил файл в Obsidian, пока смотрел список: номер теперь
        # про другую запись — трогать нельзя.
        self._заметка()
        self._заметка(заголовок="Вторая", текст="Два.")
        answer = self.client.post("/api/notes/delete", json={
            "section": "Книги", "topic": "Тема", "index": 0,
            "heading": "устаревшая запись"})
        self.assertEqual(answer.status_code, 400)
        left = self.client.get("/api/notes/topic",
                               params={"section": "Книги", "topic": "Тема"}).json()
        self.assertEqual(len(left["entries"]), 2)

    def test_a_whole_topic_is_deleted_with_all(self):
        self._заметка()
        answer = self.client.post("/api/notes/delete", json={
            "section": "Книги", "topic": "Тема", "all": True})
        self.assertTrue(answer.json()["ok"])
        self.assertFalse((self.tmp / "Книги" / "Тема.md").exists())
        self.assertTrue((self.tmp / notes.TRASH / "Тема — удалённое.md").exists())

    def test_a_name_cannot_leave_the_notes_folder(self):
        # Пульт виден всей сети, а имя приходит из адресной строки: путь с
        # `..` — это чтение и удаление чужих файлов.
        self._заметка()
        for имя in ("..", "..\\..\\Windows", "C:\\Windows", "/etc/passwd"):
            with self.subTest(имя=имя):
                answer = self.client.get(
                    "/api/notes/topic",
                    params={"section": "Книги", "topic": имя})
                self.assertEqual(answer.status_code, 400, имя)
                answer = self.client.post("/api/notes/delete", json={
                    "section": "Книги", "topic": имя, "all": True})
                self.assertEqual(answer.status_code, 400, имя)
        # Настоящая заметка на месте, а за пределами папки ничего нового.
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*")),
                         ["Книги", "Тема.md"])

    def test_the_phone_gets_nothing(self):
        # Телефон лежит в сети, а папка заметок — личные мысли хозяина.
        for method, path in (("get", "/api/notes"),
                             ("get", "/api/notes/topic?section=Книги&topic=Тема"),
                             ("post", "/api/notes/delete"),
                             ("post", "/api/notes/edit"),
                             ("post", "/api/notes/rename"),
                             ("post", "/api/notes/add"),
                             ("post", "/api/notes/open")):
            with self.subTest(путь=path):
                answer = getattr(self.вдали, method)(path)
                self.assertEqual(answer.status_code, 403, path)
                self.assertIn("только с этого компьютера", answer.json()["error"])

    # --- Правка и дописывание --------------------------------------------

    def test_an_entry_is_edited_by_number_and_heading(self):
        self._заметка()
        self._заметка(заголовок="Вторая", текст="Два.")
        topic = self.client.get("/api/notes/topic",
                                params={"section": "Книги", "topic": "Тема"}).json()
        heading = topic["entries"][0]["heading"]
        answer = self.client.post("/api/notes/edit", json={
            "section": "Книги", "topic": "Тема", "index": 0, "heading": heading,
            "title": "Правлена", "text": "Новый текст."})
        тело = answer.json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["path"], str(self.tmp / "Книги" / "Тема.md"))
        left = self.client.get("/api/notes/topic",
                               params={"section": "Книги", "topic": "Тема"}).json()
        self.assertEqual(left["entries"][0]["title"], "Правлена")
        self.assertEqual(left["entries"][0]["text"], "Новый текст.")

    def test_editing_with_a_stale_heading_changes_nothing(self):
        self._заметка()
        answer = self.client.post("/api/notes/edit", json={
            "section": "Книги", "topic": "Тема", "index": 0,
            "heading": "устаревшая запись", "title": "Правлена", "text": "Новое."})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("изменился", answer.json()["error"])
        self.assertIn("Один.",
                      (self.tmp / "Книги" / "Тема.md").read_text(encoding="utf-8"))

    def test_editing_an_empty_text_is_a_400(self):
        self._заметка()
        topic = self.client.get("/api/notes/topic",
                                params={"section": "Книги", "topic": "Тема"}).json()
        answer = self.client.post("/api/notes/edit", json={
            "section": "Книги", "topic": "Тема", "index": 0,
            "heading": topic["entries"][0]["heading"],
            "title": "Правлена", "text": "   "})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("пустой текст", answer.json()["error"])

    def test_an_entry_is_written_by_hand(self):
        self._заметка()
        answer = self.client.post("/api/notes/add", json={
            "section": "Книги", "topic": "Тема",
            "title": "Руками", "text": "Новый текст."})
        тело = answer.json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["path"], str(self.tmp / "Книги" / "Тема.md"))
        left = self.client.get("/api/notes/topic",
                               params={"section": "Книги", "topic": "Тема"}).json()
        self.assertEqual([e["title"] for e in left["entries"]],
                         ["Первая", "Руками"])

    def test_writing_an_empty_text_is_a_400(self):
        self._заметка()
        answer = self.client.post("/api/notes/add", json={
            "section": "Книги", "topic": "Тема", "title": "Пусто", "text": ""})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("пустой текст", answer.json()["error"])

    def test_a_topic_is_renamed_and_the_new_names_come_back(self):
        self._заметка()
        answer = self.client.post("/api/notes/rename", json={
            "section": "Книги", "topic": "Тема", "new_topic": "Другая тема",
            "new_section": ""})
        тело = answer.json()
        self.assertTrue(тело["ok"], тело)
        # Раздел и тема — как их потом увидит список.
        self.assertEqual(тело["section"], "Книги")
        self.assertEqual(тело["topic"], "Другая тема")
        self.assertEqual(тело["path"],
                         str(self.tmp / "Книги" / "Другая тема.md"))
        self.assertTrue((self.tmp / "Книги" / "Другая тема.md").exists())
        self.assertFalse((self.tmp / "Книги" / "Тема.md").exists())
        listed = self.client.get("/api/notes").json()["sections"]
        self.assertEqual([t["name"] for t in listed[0]["topics"]],
                         ["Другая тема"])

    def test_a_topic_moves_to_another_section(self):
        self._заметка()
        answer = self.client.post("/api/notes/rename", json={
            "section": "Книги", "topic": "Тема", "new_topic": "Тема",
            "new_section": "Идеи"})
        тело = answer.json()
        self.assertTrue(тело["ok"], тело)
        self.assertEqual(тело["section"], "Идеи")
        self.assertIn("раздел: Идеи",
                      (self.tmp / "Идеи" / "Тема.md").read_text(encoding="utf-8"))

    def test_renaming_into_a_taken_name_is_a_400(self):
        self._заметка()
        self._заметка(тема="Другая")
        answer = self.client.post("/api/notes/rename", json={
            "section": "Книги", "topic": "Тема", "new_topic": "Другая",
            "new_section": ""})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("уже есть", answer.json()["error"])
        # Обе темы целы.
        self.assertTrue((self.tmp / "Книги" / "Тема.md").exists())
        self.assertTrue((self.tmp / "Книги" / "Другая.md").exists())

    def test_renaming_cannot_walk_out_of_the_folder(self):
        # Пульт виден всей сети: имя с `..` не должно ни переименовать, ни
        # создать файл за папкой заметок.
        self._заметка()
        for имя in ("..", "..\\..\\Windows", "C:\\Windows", "/etc/passwd"):
            with self.subTest(имя=имя):
                answer = self.client.post("/api/notes/rename", json={
                    "section": "Книги", "topic": "Тема", "new_topic": имя,
                    "new_section": ""})
                self.assertEqual(answer.status_code, 400, имя)
        answer = self.client.post("/api/notes/rename", json={
            "section": "Книги", "topic": "Тема", "new_topic": "Тема",
            "new_section": ".."})
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(sorted(p.name for p in self.tmp.rglob("*")),
                         ["Книги", "Тема.md"])

    def test_opening_the_folder_does_not_really_open_it(self):
        self._заметка()
        with mock.patch.object(os, "startfile") as opened:
            answer = self.client.post("/api/notes/open",
                                      json={"section": "Книги"})
        self.assertTrue(answer.json()["ok"])
        opened.assert_called_once()
        self.assertEqual(opened.call_args[0][0], str(self.tmp / "Книги"))

    def test_opening_a_topic_points_at_its_file(self):
        path = self._заметка()
        with mock.patch.object(os, "startfile") as opened:
            self.client.post("/api/notes/open",
                             json={"section": "Книги", "topic": "Тема"})
        self.assertEqual(opened.call_args[0][0], str(path))

    def test_opening_cannot_walk_out_of_the_folder(self):
        with mock.patch.object(os, "startfile") as opened:
            answer = self.client.post("/api/notes/open",
                                      json={"section": "..", "topic": "Побег"})
        self.assertEqual(answer.status_code, 400)
        opened.assert_not_called()

    def test_the_list_carries_the_obsidian_field(self):
        # Без Obsidian поле null, а не пустая строка: пульт по нему решает,
        # какую кнопку показывать — «Открыть в Obsidian» или «Открыть файл».
        self._заметка()
        self.assertIsNone(self.client.get("/api/notes").json()["obsidian"])

    def test_opening_in_obsidian_gives_a_link_not_a_path(self):
        path = self._заметка()
        with mock.patch.object(os, "startfile") as opened:
            answer = self.client.post("/api/notes/open", json={
                "section": "Книги", "topic": "Тема", "obsidian": True})
        тело = answer.json()
        self.assertTrue(тело["ok"])
        self.assertTrue(тело["link"].startswith("obsidian://open?path="))
        # Реальный Obsidian на машине хозяина не открывается — startfile подменён.
        self.assertEqual(opened.call_args[0][0], тело["link"])
        self.assertEqual(тело["path"], str(path))

    def test_opening_in_obsidian_cannot_walk_out_of_the_folder(self):
        # Та же проверка, что и для проводника: имя из запроса не выводит
        # ссылку за папку заметок, а та открыла бы чужой файл.
        with mock.patch.object(os, "startfile") as opened:
            answer = self.client.post("/api/notes/open", json={
                "section": "..", "topic": "Побег", "obsidian": True})
        self.assertEqual(answer.status_code, 400)
        opened.assert_not_called()


class SettingsTests(unittest.TestCase):
    """Папка заметок в настройках: пусто — по умолчанию, непусто — полный путь."""

    def setUp(self):
        self.runtime = object.__new__(WebRuntime)
        self.runtime.brain = None
        self.runtime.voice = NS(_brain=None, _close_conversation=lambda: None,
                                running=False)
        self.runtime.server = None
        self.runtime._remember = lambda kind, payload: None
        self._saved = config.NOTES_DIR
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", self._saved))

    def test_an_empty_field_means_the_default_folder(self):
        result = self.runtime.save_settings({"notes_dir": ""})
        self.assertTrue(result["ok"], result)
        self.assertEqual(config.NOTES_DIR, "")

    def test_a_full_path_is_accepted_and_applied_right_away(self):
        # Читается на каждый вызов из config, поэтому заметки пойдут туда
        # сразу, без перезапуска голоса.
        result = self.runtime.save_settings({"notes_dir": r"D:\Заметки\Трубы"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(config.NOTES_DIR, r"D:\Заметки\Трубы")

    def test_a_relative_path_is_refused(self):
        # Относительный путь зависит от того, откуда запущен пульт: заметки
        # осели бы в папке проекта, а хозяин бы их там не нашёл.
        for плохо in ("Заметки", "папка/заметки"):
            with self.subTest(notes_dir=плохо):
                result = self.runtime.save_settings({"notes_dir": плохо})
                self.assertFalse(result["ok"])
                self.assertIn("notes_dir", " ".join(result["errors"]))

    def test_the_default_is_the_documents_folder_of_windows(self):
        # Папку «Документы» переносят на другой диск, и склейка с
        # %USERPROFILE% после этого уводила бы заметки в никуда.
        from core import notes as notes_module

        config.NOTES_DIR = ""
        self.assertEqual(notes_module.root(),
                         notes_module.documents_dir() / notes_module.DEFAULT_FOLDER)


if __name__ == "__main__":
    unittest.main()
