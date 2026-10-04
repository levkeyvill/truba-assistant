"""Заметки: то, что поправлено после проверки работы бесплатной модели.

Каждый тест — про конкретную ошибку, которую она оставила:

* имена от модели («Итоги...», «Книги/фантастика») отклонялись целиком, и
  причёсанная заметка уезжала сырой во «Входящие»;
* `## ` внутри причёсанного текста разбор принимал за новую запись;
* корзина не записалась — а запись всё равно удалялась;
* «запиши заметку» и молчание оставляли диктовку открытой навсегда;
* `save_note` от модели всегда писал во «Входящие», даже когда тема названа;
* «Открыть файл» несуществующей темы создавал папку «Тема.md».
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from say_helpers import assert_said
from core import hands, notes
from core.voice_loop import DICTATION_EMPTY, VoiceLoop


def _цикл(test):
    """Голосовой цикл из заглушек: только то, что нужно диктовке.

    Свой, а не из test_notes_voice: импорт чужого тестового модуля по
    другому имени второй раз поднял бы test_data_guard с новой папкой.
    """
    loop = object.__new__(VoiceLoop)
    loop.said, loop.written, loop.events = [], [], []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._say_back = loop.said.append
    loop._remember = lambda text, answered: None
    loop._tell_phone = lambda **kwargs: None
    loop._open_conversation = lambda: None
    loop._turn = None
    loop._dictation = None
    loop._dictation_at = 0.0
    loop._dictation_command = ""
    # Голос запущен: без этого `begin_dictation` честно отвечает «диктовать
    # некому», а тест — про сторож тишины в идущей диктовке.
    loop.ready = True
    loop._thread = mock.Mock(is_alive=lambda: True)
    patcher = mock.patch.multiple(
        notes, known_topics=lambda: [],
        add=lambda *args, **kwargs: loop.written.append(args))
    patcher.start()
    test.addCleanup(patcher.stop)
    return loop


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-notes-fix-"))
        saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", saved))
        # Заметка с тем же текстом второй раз не пишется: память общая.
        hands.forget_done()
        self.addCleanup(hands.forget_done)


class SoftNameTests(_Tmp):
    def test_dots_and_slashes_from_the_model_are_softened(self):
        self.assertEqual(notes.soft_name("Итоги..."), "Итоги…")
        self.assertEqual(notes.soft_name("Книги/Фантастика"), "Книги Фантастика")
        self.assertEqual(notes.soft_name("Книги\\Фантастика"), "Книги Фантастика")

    def test_nothing_but_dots_is_still_refused(self):
        for name in ("..", "/", "../..", "  "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                notes.soft_name(name)

    def test_soft_name_never_leaves_the_folder(self):
        path = notes.topic_path(notes.soft_name("../Книги"), notes.soft_name("..\\x"))
        self.assertTrue(path.resolve().is_relative_to(self.tmp.resolve()))


class HeadingInsideTextTests(_Tmp):
    def test_level_two_heading_in_the_text_does_not_split_the_entry(self):
        notes.add("Книги", "Тема", "Запись", "## Подзаголовок\nтекст\n# ещё")
        entries = notes.read("Книги", "Тема")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertIn("### Подзаголовок", entries[0]["text"])
        self.assertIn("### ещё", entries[0]["text"])

    def test_title_is_one_line(self):
        notes.add("Книги", "Тема", "Две\nстроки", "текст")
        self.assertEqual(notes.read("Книги", "Тема")["entries"][0]["title"],
                         "Две строки")


class TrashFailureTests(_Tmp):
    def test_entry_stays_when_the_trash_cannot_be_written(self):
        notes.add("Книги", "Тема", "Первая", "раз")
        notes.add("Книги", "Тема", "Вторая", "два")
        path = notes.topic_path("Книги", "Тема")
        before = path.read_text(encoding="utf-8")
        heading = notes.read("Книги", "Тема")["entries"][0]["heading"]
        with mock.patch.object(Path, "write_text", side_effect=OSError("диск полон")):
            with self.assertRaises(OSError):
                notes.delete_entry("Книги", "Тема", 0, heading)
        self.assertEqual(path.read_text(encoding="utf-8"), before)


class EmptyDictationTests(_Tmp):
    def test_silence_ends_an_empty_dictation(self):
        loop = _цикл(self)
        loop._start_dictation(mock.Mock(target="запиши заметку"), "запиши заметку")
        self.assertEqual(loop._dictation, [])
        # До первой фразы ждём DICTATION_FIRST_PAUSE (30 с): сразу после
        # «запиши заметку» он ещё собирается с мыслями, и молчание в первые
        # секунды — не конец. Поэтому и закрывает диктовку тишина в 31 с.
        with mock.patch("core.voice_loop.time.monotonic",
                        return_value=loop._dictation_at + 20):
            loop._check_dictation_pause()
        self.assertEqual(loop._dictation, [], "полминуты на первую фразу мало")
        with mock.patch("core.voice_loop.time.monotonic",
                        return_value=loop._dictation_at + 31):
            loop._check_dictation_pause()
        self.assertIsNone(loop._dictation)
        assert_said(self, loop.said[-1], "dictation_empty")
        self.assertEqual(loop.written, [])


class SaveNoteToolTests(_Tmp):
    def test_named_topic_goes_to_the_existing_one(self):
        notes.add("Книги", "Мастер и Маргарита", "Раньше", "было")
        answer = json.loads(hands.run_note(json.dumps(
            {"text": "Воланд — зеркало.", "topic": "Мастер и Маргарита"})))
        self.assertTrue(answer["ok"])
        self.assertIn("Книги — Мастер и Маргарита", answer["text"])
        self.assertEqual(len(notes.read("Книги", "Мастер и Маргарита")["entries"]), 2)

    def test_new_topic_is_created_where_the_model_said(self):
        answer = json.loads(hands.run_note(json.dumps(
            {"text": "Сделать заметки.", "section": "Проекты", "topic": "Труба"})))
        self.assertIn("Проекты — Труба", answer["text"])
        self.assertTrue(notes.topic_path("Проекты", "Труба").is_file())

    def test_no_topic_goes_to_inbox(self):
        answer = json.loads(hands.run_note(json.dumps({"text": "Мысль."})))
        self.assertIn("Разное — Входящие", answer["text"])


class OpenTopicTests(_Tmp):
    def test_missing_topic_is_an_error_and_no_folder_appears(self):
        from ui.web_runtime import WebRuntime

        runtime = object.__new__(WebRuntime)
        with mock.patch("os.startfile") as started:
            with self.assertRaises(FileNotFoundError):
                runtime.notes_open({"section": "Книги", "topic": "Нет такой"})
        started.assert_not_called()
        self.assertFalse((self.tmp / "Книги" / "Нет такой.md").exists())

    def test_section_cannot_open_a_folder_outside(self):
        from ui.web_runtime import WebRuntime

        runtime = object.__new__(WebRuntime)
        with mock.patch("os.startfile") as started:
            with self.assertRaises(ValueError):
                runtime.notes_open({"section": ".."})
        started.assert_not_called()


if __name__ == "__main__":
    unittest.main()
