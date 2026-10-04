"""Заметки на экране телефона: список разделов и чтение выбранной темы.

Кнопка «заметка» больше не начинает диктовку сразу — первое касание
открывает оверлей, а «✎ Записать» уже в нём диктует. Телефон — только
читатель: удаления с него нет, и имя темы не может вывести за папку
заметок.

Без сети, звука и микрофона: заметки лежат во временной папке, сервер
подменён, а фон (`_bg`) вызывается сразу — иначе тест ждал бы поток.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import config
from core import notes
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

PAGE = Path(config.ROOT) / "web" / "index.html"


# --- Что уходит в телефон --------------------------------------------------


def _server() -> PhoneServer:
    """`PhoneServer` без сокета и потока: поднятый сервер тут не нужен."""
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None
    return server


def _сообщения(действие) -> list:
    """Что действие кладёт в `_broadcast`, разобранное обратно в словари."""
    server = _server()
    отправлено: list = []

    class _Соединение:
        def send_text(self, payload):
            отправлено.append(json.loads(payload))

    with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
        действие(server)
    шлёт.call_args[0][0](_Соединение())
    return отправлено


class ServerNotesTests(unittest.TestCase):
    def test_the_sections_go_as_one_message(self):
        разделы = [{"name": "Книги", "topics": [
            {"name": "Мастер", "entries": 3, "updated": "2026-09-27"}]}]
        self.assertEqual(
            _сообщения(lambda s: s.send_notes(разделы)),
            [{"type": "notes", "sections": разделы}])

    def test_a_topic_goes_with_its_entries(self):
        ответ = _сообщения(lambda s: s.send_note_topic(
            {"section": "Книги", "topic": "Мастер",
             "entries": [{"when": "27.09.2026, 02:15", "title": "Воланд",
                          "text": "Он был русским дьяволом."}]}))
        self.assertEqual(ответ, [{
            "type": "note_topic", "section": "Книги", "topic": "Мастер",
            "entries": [{"when": "27.09.2026, 02:15", "title": "Воланд",
                         "text": "Он был русским дьяволом."}]}])

    def test_a_failed_reading_carries_the_error(self):
        # Без `section`/`topic` телефон не понял бы, к какой теме относится
        # отказ, и показал бы его не там.
        self.assertEqual(
            _сообщения(lambda s: s.send_note_topic(
                {"section": "Книги", "topic": "Нет", "error": "не прочитала"})),
            [{"type": "note_topic", "section": "Книги", "topic": "Нет",
              "error": "не прочитала"}])


# --- Пульт отдаёт телефону --------------------------------------------------


class _Телефон:
    """Сервер телефона: только то, что телефон спрашивает."""

    def __init__(self):
        self.notes = []
        self.topics = []

    def send_notes(self, sections):
        self.notes.append(sections)

    def send_note_topic(self, data):
        self.topics.append(data)


class RuntimeNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-phone-notes-"))
        saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", saved))
        self.телефон = _Телефон()
        self.runtime = object.__new__(WebRuntime)
        self.runtime.server = self.телефон
        self.runtime.записи = []
        self.runtime._remember = lambda kind, payload: self.runtime.записи.append(
            (kind, payload))
        # Фон здесь — прямой вызов: проверяем то, что телефон получит.
        self.runtime._bg = lambda func, *args: func(*args)

    def test_the_list_comes_without_paths(self):
        notes.add("Книги", "Мастер", "Воланд", "Он был русским дьяволом.")
        self.runtime.handle_event("notes_list", None)
        self.assertEqual(len(self.телефон.notes), 1)
        разделы = self.телефон.notes[0]
        self.assertEqual([s["name"] for s in разделы], ["Книги"])
        тема = разделы[0]["topics"][0]
        self.assertEqual(тема["name"], "Мастер")
        self.assertEqual(тема["entries"], 1)
        self.assertTrue(тема["updated"])
        # Путь к файлу хозяина телефону не нужен и знать его не должен.
        self.assertNotIn("path", json.dumps(разделы, ensure_ascii=False))

    def test_the_topic_comes_with_when_title_and_text(self):
        notes.add("Книги", "Мастер", "Воланд", "Он был русским дьяволом.",
                  raw="как сказал")
        notes.add("Книги", "Мастер", "Бесы", "Вторая мысль.")
        self.runtime.handle_event(
            "notes_topic", {"section": "Книги", "topic": "Мастер"})
        self.assertEqual(len(self.телефон.topics), 1)
        ответ = self.телефон.topics[0]
        self.assertEqual(ответ["section"], "Книги")
        self.assertEqual(ответ["topic"], "Мастер")
        self.assertEqual(len(ответ["entries"]), 2)
        for запись in ответ["entries"]:
            self.assertEqual(set(запись), {"when", "title", "text"})
            self.assertTrue(запись["when"])
            self.assertTrue(запись["title"])
        # Сырое — надиктованное как есть; читать его на телефоне незачем.
        self.assertNotIn("как сказал", json.dumps(ответ, ensure_ascii=False))

    def test_a_path_out_of_the_notes_folder_is_answered_not_raised(self):
        # Имя пришло с телефона: телефон — не пульт, но проверить всё равно
        # надо, иначе чужой путь ушёл бы читать файл хозяина.
        self.runtime.handle_event(
            "notes_topic", {"section": "..", "topic": ".."})
        self.assertEqual(len(self.телефон.topics), 1)
        self.assertIn("error", self.телефон.topics[0])
        self.assertNotIn("entries", self.телефон.topics[0])

    def test_a_missing_topic_is_answered_not_raised(self):
        notes.add("Книги", "Мастер", "Воланд", "Дьявол.")
        self.runtime.handle_event(
            "notes_topic", {"section": "Книги", "topic": "Нет такой"})
        self.assertIn("error", self.телефон.topics[0])

    def test_the_journal_says_who_opened_what(self):
        строки = WebRuntime._log_messages("notes_list", None)
        self.assertEqual(строки, ["телефон открыл заметки"])
        self.assertEqual(
            WebRuntime._log_messages(
                "notes_topic", {"section": "Книги", "topic": "Мастер"}),
            ["телефон читает «Мастер»"])

    def test_an_empty_folder_reaches_the_phone_as_nothing(self):
        # Телефон напишет «Заметок пока нет» — но пустой список, а не обрыв.
        self.runtime.handle_event("notes_list", None)
        self.assertEqual(self.телефон.notes, [[]])


# --- Страница телефона ------------------------------------------------------


class PhonePageNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_the_overlay_is_on_the_page(self):
        self.assertIn('id="notes"', self.html)
        self.assertRegex(self.html, r"#notes\s*\{[^}]*z-index:\s*29")
        # Своё сообщение приходит, когда телефон хочет, чтобы её перерисовали.
        for вид in ("notes", "note_topic"):
            self.assertIn(f"msg.type === '{вид}'", self.html)

    def test_the_note_button_opens_the_notes_and_asks_for_them(self):
        self.assertIn("id: 'note_start'", self.html)
        self.assertIn("action.id === 'note_start'", self.html)
        self.assertIn("send({ type: 'notes_list' })", self.html)
        # Первое касание — оверлей, диктовка только с «✎ Записать».
        self.assertIn("send({ type: 'note_start' })", self.html)
        self.assertIn("listenNow();", self.html)

    def test_the_overlay_code_touches_no_innerhtml(self):
        # В заметке может оказаться что угодно: разметкой оно не станет.
        блок = self.html[self.html.index("/* ---------- Заметки ----------"):
                          self.html.index("/* ---------- Программы ---------- */")]
        self.assertIn("function drawNotes(", блок)
        self.assertIn("function drawNoteTopic(", блок)
        self.assertNotIn("innerHTML", блок)
        self.assertIn("textContent", блок)

    def test_the_overlay_stays_under_the_glow(self):
        # Рамка микрофона — z-index 30, и она должна гореть поверх оверлея.
        self.assertIn("z-index: 30", self.html)

    def test_nothing_in_the_overlay_is_smaller_than_sixteen_px(self):
        # Читают издали: мельче 16 px на этом экране не прочитать.
        стили = self.html[self.html.index("/* НАЧАЛО ОВЕРЛЕЯ ЗАМЕТОК */"):
                          self.html.index("/* КОНЕЦ ОВЕРЛЕЯ ЗАМЕТОК */")]
        self.assertIn("#notes", стили)
        for кусок in стили.split("font-size:")[1:]:
            число = кусок.split("px")[0].strip()
            self.assertGreaterEqual(float(число), 16, кусок.strip()[:40])

    def test_the_topics_are_a_strip_on_top_and_the_text_takes_the_screen(self):
        # Темы — полосой сверху, текст — на весь экран. Столбца слева
        # больше нет, значит, и его ширины в стилях быть не должно.
        стили = self.html[self.html.index("/* НАЧАЛО ОВЕРЛЕЯ ЗАМЕТОК */"):
                          self.html.index("/* КОНЕЦ ОВЕРЛЕЯ ЗАМЕТОК */")]
        # Полоса листается пальцем влево-вправо и не утаскивает страницу.
        self.assertIn("overflow-x: auto", стили)
        self.assertIn("overscroll-behavior-x: contain", стили)
        self.assertNotIn("width: 38%", стили)
        # Текст — на всю ширину, листается вниз.
        self.assertRegex(стили, r"\.nread\s*\{[^}]*flex:\s*1")
        self.assertRegex(стили, r"\.nread\s*\{[^}]*overflow-y:\s*auto")

    def test_the_freshest_topic_comes_first_and_scrolls_into_view(self):
        # Свежие сверху: хозяин продолжает последнее. Открытую тему полоса
        # обязана показать, иначе она останется за краем.
        блок = self.html[self.html.index("/* ---------- Заметки ----------"):
                          self.html.index("/* ---------- Программы ---------- */")]
        self.assertIn("scrollIntoView", блок)
        self.assertIn("inline: 'nearest'", блок)
        # Сортировка именно по `updated` и на убывание, а не «как пришло».
        # `(?s)` — сравнение в коде перенесено на вторую строку.
        self.assertRegex(блок, r"(?s)\.sort\(.*?updated.*?updated.*?-1")


if __name__ == "__main__":
    unittest.main()
