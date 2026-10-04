"""Голосовые команды и диктовка заметки.

Ни микрофона, ни синтеза, ни облака, ни настоящих «Документов»: голосовой цикл
собран из заглушек, причёсывание и запись подменены, папка заметок — временная.
Проверяется путь целиком: фраза → команда `note` → буфер копится → «всё» →
причёсывание → запись в файл.

Не здесь видно, как это звучит и сколько секунд занимает причёсывание у
настоящего облака.
"""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from say_helpers import assert_said
from core import commands, notes
from core.voice_loop import (
    DICTATION_CANCEL_SAY,
    DICTATION_DONE,
    DICTATION_EMPTY,
    DICTATION_START,
    VoiceLoop,
)
from ui.web_runtime import WebRuntime

APPS = [{"id": "youtube", "title": "YouTube", "kind": "url",
         "url": "https://youtube.com"}]


# --- Команды ------------------------------------------------------------


class NoteCommandTests(unittest.TestCase):
    def _вид(self, фраза):
        order = commands.understand(фраза, APPS)
        self.assertIsNotNone(order, фраза)
        return order

    def test_short_ways_to_ask_for_a_note(self):
        # Короткие формы из таблицы. Остальное («сделай заметку», «создай
        # идею») в таблицу не входит: фраза уходит модели, и смысл — «начать
        # запись» — понимает она, инструментом `start_dictation`.
        for фраза in ("запиши заметку", "запиши мысль", "давай заметку",
                      "заметка про книги", "новая заметка"):
            with self.subTest(фраза=фраза):
                self.assertEqual(self._вид(фраза).action, "note")

    def test_living_ways_without_a_short_form_go_to_the_model(self):
        for фраза in ("сделай заметку", "запишите заметку",
                      "создай идею про кофе", "запиши в заметки",
                      "допиши в заметку про трубу"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_a_hint_about_the_book_or_the_project_is_kept(self):
        # Вся фраза уходит в `target`: «по книге Мастер и Маргарита» —
        # подсказка разделу и теме, и без неё причёсывание не поймёт,
        # куда класть.
        for фраза in ("запиши мысль по книге Мастер и Маргарита",
                      "заметка про кофе"):
            with self.subTest(фраза=фраза):
                order = self._вид(фраза)
                self.assertEqual(order.action, "note")
                self.assertEqual(order.target, фраза)

    def test_the_answer_says_that_the_dictation_starts(self):
        assert_said(self, self._вид("запиши заметку").reply, "dictation_start")

    def test_a_clip_is_still_a_moment_not_a_note(self):
        # «Клип» в таблице один — у момента. Формы «запиши клип», «клипани» в
        # таблице нет: фраза уходит модели, и «клип» там уже не заметка.
        for фраза in ("клип", "сохрани клип", "сохрани момент", "момент"):
            with self.subTest(фраза=фраза):
                self.assertEqual(commands.understand(фраза, APPS).action,
                                 "moment")
        for фраза in ("запиши клип", "клипани"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_youtube_is_not_touched(self):
        order = commands.understand("открой ютуб", APPS)
        self.assertEqual(order.action, "launch")
        self.assertEqual(order.target, "youtube")
        # Ролики с каналами ищутся инструментом в облаке, а не разбором
        # голоса: заметки не должны перехватывать и их.
        self.assertIsNone(commands.understand("включи ролик про борщ", APPS))


# --- Диктовка -----------------------------------------------------------


def _цикл(tmp, test):
    """Голосовой цикл из заглушек: ни сети, ни звука, ни облака.

    Ответы, события и реплики копятся в списках на самом цикле. Причёсывание
    и запись подменены — тест смотрит, что и в какой разбор ушло, а не что
    записалось на диск (диск проверяет test_notes.py).
    """
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop.said = []
    loop.lines = []
    loop.remembered = []
    loop.polished = []
    loop.written = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._say_back = loop.said.append
    loop._remember = lambda text, answered: loop.remembered.append((text, answered))
    loop._tell_phone = lambda state="", who="", text="", sound="": (
        loop.lines.append((who or state, text)))
    loop._open = False
    loop._last_turn = 0.0
    loop._last_talk = 0.0
    loop._server = None
    # Фон — сразу: тест проверяет запись, а не гонку за потоком.
    loop._background = lambda fn, *args: fn(*args)
    loop._brain = None
    loop._turn = None
    loop._dictation = None
    loop._dictation_at = 0.0
    loop._dictation_command = ""
    loop._search_next = False
    loop._voice_score = None
    loop._interrupt = threading.Event()
    # Голос запущен: `begin_dictation` честно отвечает «диктовать некому», если
    # модели не подняты, а весь этот тест — про диктовку.
    loop.ready = True
    loop._thread = mock.Mock(is_alive=lambda: True)
    loop._known_apps = lambda: APPS
    loop._answer = lambda *args, **kwargs: loop.events.append(("answer", args))
    # Запись и причёсывание — подменами: тест должен падать на неверном
    # разборе фраз, а не на записи в настоящую папку.
    def fake_polish(brain, said, command="", known=None):
        loop.polished.append((said, command))
        return {"section": "Книги", "topic": "Мастер и Маргарита",
                "title": "Воланд как зеркало", "text": "Причёсанный текст.",
                "raw": False}

    def fake_add(section, topic, title, text, raw="", when=None):
        loop.written.append((section, topic, title, text, raw))
        return Path(tmp) / section / f"{topic}.md"

    patcher = mock.patch.multiple(
        notes, polish=fake_polish, add=fake_add, known_topics=lambda: [])
    patcher.start()
    test.addCleanup(patcher.stop)
    return loop


class _Заготовка(unittest.TestCase):
    """Временная папка заметок и одна принятая фраза."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-dictation-"))
        self._saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", self._saved))

    def _сказать(self, loop, текст):
        """Одна принятая фраза — ровно как её принимает основной цикл."""
        loop._turn_body(текст, np.zeros(8, dtype=np.float32), 0.0, 0.0, False)


class DictationTests(_Заготовка):

    def test_the_command_opens_the_dictation_and_nothing_is_written_yet(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши мысль по книге Мастер и Маргарита")
        self.assertEqual(len(loop.said), 1)
        assert_said(self, loop.said[0], "dictation_start")
        self.assertEqual(loop.polished, [])
        self.assertEqual(loop.written, [])
        # Разу в этот момент открыт: имя звать не надо, он уже диктует.
        self.assertTrue(loop._open)

    def test_phrases_are_collected_and_not_sent_anywhere(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        for фраза in ("вот что я думаю про воланда", "он как зеркало получается"):
            self._сказать(loop, фраза)
        self.assertEqual(loop._dictation,
                         ["вот что я думаю про воланда",
                          "он как зеркало получается"])
        # Ни в облако, ни в разбор команд фразы не ушли.
        self.assertNotIn("answer", [kind for kind, _ in loop.events])

    def test_each_phrase_is_shown_on_the_phone_as_its_line(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "вот что я думаю про воланда")
        self.assertIn(("me", "вот что я думаю про воланда"), loop.lines)

    def test_the_word_ends_it_and_writes_one_note(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "вот что я думаю про воланда")
        self._сказать(loop, "всё")
        self.assertEqual(len(loop.written), 1)
        раздел, тема, заголовок, текст, сырое = loop.written[0]
        self.assertEqual((раздел, тема), ("Книги", "Мастер и Маргарита"))
        self.assertEqual(текст, "Причёсанный текст.")
        # Сырое диктовки в модельную запись не идёт — оно в своей свёртке.
        self.assertEqual(сырое, "вот что я думаю про воланда")
        self.assertIsNone(loop._dictation)

    def test_several_ways_to_say_it_is_over(self):
        for конец in ("всё", "всё.", "записывай", "конец заметки", "хватит"):
            with self.subTest(конец=конец):
                loop = _цикл(self.tmp, self)
                self._сказать(loop, "запиши заметку")
                self._сказать(loop, "мысль про воланда")
                self._сказать(loop, конец)
                self.assertEqual(len(loop.written), 1, конец)

    def test_all_at_the_end_of_a_thought_does_not_end_the_dictation(self):
        # «…как и все» — обычный конец мысли. Голое «всё/все» в конце фразы
        # закончило бы запись посреди диктовки; конец — только отдельной
        # фразой «всё» или хвостом «…записывай».
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "он такой же как и все")
        self.assertEqual(loop._dictation, ["он такой же как и все"])
        self.assertEqual(loop.written, [])

    def test_write_it_down_at_the_end_is_cut_off(self):
        # «мысль такая, всё, записывай» — хвост не часть мысли.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль такая, всё, записывай")
        self.assertEqual(loop.polished[0][0], "мысль такая")
        self.assertEqual(len(loop.written), 1)
        self.assertIsNone(loop._dictation)

    def test_a_word_in_the_middle_of_a_phrase_is_not_a_command(self):
        # «всё» внутри мысли — часть мысли, а не команда закончить.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "всё что я понял про это")
        self.assertEqual(loop._dictation, ["всё что я понял про это"])
        self.assertEqual(loop.written, [])

    def test_phrases_are_glued_into_one_text(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "первая часть")
        self._сказать(loop, "вторая часть")
        self._сказать(loop, "всё")
        self.assertEqual(loop.polished[0][0], "первая часть вторая часть")

    def test_the_command_about_the_book_reaches_the_polishing(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши мысль по книге Мастер и Маргарита")
        self._сказать(loop, "про воланда")
        self._сказать(loop, "всё")
        self.assertEqual(loop.polished[0][1],
                         "запиши мысль по книге Мастер и Маргарита")

    def test_a_command_inside_the_dictation_does_not_start_a_new_note(self):
        # «запиши заметку» посреди диктовки — это просто слова мысли.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "а ещё я хотел записать заметку про кофе")
        self._сказать(loop, "всё")
        self.assertEqual(len(loop.written), 1)
        self.assertEqual(loop.polished[0][0],
                         "а ещё я хотел записать заметку про кофе")

    def test_a_new_dictation_starts_after_the_old_one_is_written(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "первая мысль")
        self._сказать(loop, "всё")
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "вторая мысль")
        self._сказать(loop, "всё")
        self.assertEqual([said for said, _ in loop.polished],
                         ["первая мысль", "вторая мысль"])

    def test_cancellation_throws_the_buffer_away(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль, которую не надо")
        self._сказать(loop, "отмена")
        self.assertEqual(loop.written, [])
        assert_said(self, loop.said[-1], "dictation_cancel")
        self.assertIn(("note", "отменена"), loop.events)
        self.assertIsNone(loop._dictation)

    def test_another_way_to_cancel(self):
        for отмена in ("отмена", "не записывай", "отставить"):
            with self.subTest(отмена=отмена):
                loop = _цикл(self.tmp, self)
                self._сказать(loop, "запиши заметку")
                self._сказать(loop, "мысль")
                self._сказать(loop, отмена)
                self.assertEqual(loop.written, [], отмена)

    def test_nothing_dictated_means_nothing_written(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "всё")
        self.assertEqual(loop.written, [])
        assert_said(self, loop.said[-1], "dictation_empty")

    def test_a_note_survives_a_cloud_that_refused(self):
        # Облако не ответило: пишем сырое в «Разное / Входящие» и говорим
        # об этом — иначе хозяин решит, что мысль потерялась.
        loop = _цикл(self.tmp, self)
        сырое = {"section": "Разное", "topic": "Входящие",
                 "title": "Без обработки", "text": "надиктованное как есть",
                 "raw": True}
        with mock.patch.object(notes, "polish", return_value=сырое):
            self._сказать(loop, "запиши заметку")
            self._сказать(loop, "надиктованное как есть")
            self._сказать(loop, "всё")
        раздел, тема, _, текст, без_сырого = loop.written[0]
        self.assertEqual((раздел, тема), ("Разное", "Входящие"))
        self.assertEqual(текст, "надиктованное как есть")
        # Сырое и есть текст: повторять его в свёртке незачем.
        self.assertEqual(без_сырого, "")
        # Вслух — то же «Готово»: облако молчало, но мысль записана.
        assert_said(self, loop.said[-1], "dictation_done")
        self.assertIn(("note", "облако не ответило — записала сырое"),
                      loop.events)

    def test_silence_ends_the_dictation(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        # Часы подменены: ждать 15 секунд в тесте незачем.
        with mock.patch("core.voice_loop.time.monotonic",
                        return_value=loop._dictation_at + 16.0):
            loop._check_dictation_pause()
        self.assertEqual(len(loop.written), 1)
        self.assertIsNone(loop._dictation)

    def test_a_short_pause_does_not_end_the_dictation(self):
        # Хозяин думает между фразами: пауза в пару секунд — не конец.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        with mock.patch("core.voice_loop.time.monotonic",
                        return_value=loop._dictation_at + 3.0):
            loop._check_dictation_pause()
        self.assertEqual(loop.written, [])
        self.assertEqual(len(loop._dictation), 1)

    def test_silence_does_not_break_into_a_busy_turn(self):
        # Идёт обычный разговорный ход: тишина в него не вклинивается, иначе
        # нажатое «всё» и подлёт одновременно завели бы две записи.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        loop._turn = threading.Lock()
        loop._turn.acquire()
        self.addCleanup(loop._turn.release)
        with mock.patch("core.voice_loop.time.monotonic",
                        return_value=loop._dictation_at + 16.0):
            loop._check_dictation_pause()
        self.assertEqual(loop.written, [])

    def test_silence_without_a_dictation_does_nothing(self):
        loop = _цикл(self.tmp, self)
        with mock.patch("core.voice_loop.time.monotonic", return_value=1e9):
            loop._check_dictation_pause()
        self.assertEqual(loop.written, [])

    def test_a_broken_write_is_not_hidden(self):
        # Диск занят или папка недоступна: молчать про это нельзя — «Готово»
        # она уже сказала, а мысль на диске не появилась.
        loop = _цикл(self.tmp, self)
        with mock.patch.object(notes, "add", side_effect=OSError("диск занят")):
            self._сказать(loop, "запиши заметку")
            self._сказать(loop, "мысль про воланда")
            self._сказать(loop, "всё")
        assert_said(self, loop.said[-1], "note_failed")
        self.assertTrue(any(kind == "error" for kind, _ in loop.events))

    def test_the_note_is_told_out_and_kept_in_the_talk(self):
        # Вслух — короткое «Готово»: ждать облако он не должен. Раздел и тема
        # уходят событием, а в историю мозга — его же словами, чтобы можно
        # было обсудить записанное сразу.
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        self._сказать(loop, "всё")
        self.assertEqual(len(loop.said), 2)
        assert_said(self, loop.said[0], "dictation_start")
        assert_said(self, loop.said[1], "dictation_done")
        self.assertIn(("note", "записала в «Книги / Мастер и Маргарита» "
                      "(2 слов)"), loop.events)
        said, answered = loop.remembered[-1]
        self.assertEqual(said, "запиши заметку")
        self.assertIn("Записала заметку: мысль про воланда", answered)
        self.assertTrue(loop._open)

    def test_the_journal_says_what_happened(self):
        loop = _цикл(self.tmp, self)
        self._сказать(loop, "запиши заметку")
        self.assertIn(("note", "диктовка начата"), loop.events)
        self._сказать(loop, "мысль про воланда")
        self._сказать(loop, "всё")
        self.assertIn(("note", "записала в «Книги / Мастер и Маргарита» "
                      "(2 слов)"), loop.events)
        self.assertIn("заметка: диктовка начата",
                      WebRuntime._log_messages("note", "диктовка начата"))


class BackgroundNoteTests(_Заготовка):
    """«Всё» — и сразу ответ: облако и запись уходят в фон."""

    def _с_пустым_фоном(self):
        """Цикл, у которого фон только запоминает вызов, но не выполняет."""
        loop = _цикл(self.tmp, self)
        отложенный = []
        loop._background = lambda fn, *args: отложенный.append((fn, args))
        return loop, отложенный

    def test_the_answer_comes_before_the_cloud(self):
        # Хозяин не должен ждать облако: «Готово» звучит в ту же секунду,
        # а причёсывания на этот момент ещё не было.
        loop, отложенный = self._с_пустым_фоном()
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        self._сказать(loop, "всё")

        assert_said(self, loop.said[-1], "dictation_done")
        self.assertEqual(loop.polished, [])
        self.assertEqual(loop.written, [])

        # Запустили отложенное — заметка появилась.
        fn, args = отложенный[0]
        fn(*args)
        self.assertEqual([фраза for фраза, _ in loop.polished],
                         ["мысль про воланда"])
        self.assertEqual(len(loop.written), 1)

    def test_a_note_lands_even_when_the_cloud_died(self):
        # Облако упало — мысль всё равно на диске, сырая, в «Разное /
        # Входящие». Вслух больше ничего: «Готово» она уже сказала.
        loop = _цикл(self.tmp, self)
        with mock.patch.object(notes, "polish", side_effect=OSError("облако")):
            self._сказать(loop, "запиши заметку")
            self._сказать(loop, "мысль про воланда")
            self._сказать(loop, "всё")
        self.assertEqual(len(loop.said), 2)
        assert_said(self, loop.said[0], "dictation_start")
        assert_said(self, loop.said[1], "dictation_done")
        self.assertEqual(len(loop.written), 1)
        self.assertEqual(loop.written[0][:2], ("Разное", "Входящие"))
        self.assertIn(("note", "облако не ответило — записала сырое"),
                      loop.events)

    def test_a_broken_file_is_told_out_aloud(self):
        # Записать не вышло — об этом она говорит вслух, даже после «Готово».
        loop = _цикл(self.tmp, self)
        with mock.patch.object(notes, "add", side_effect=OSError("диск занят")):
            self._сказать(loop, "запиши заметку")
            self._сказать(loop, "мысль про воланда")
            self._сказать(loop, "всё")
        assert_said(self, loop.said[-1], "note_failed")
        self.assertTrue(any(kind == "error" for kind, _ in loop.events))

    def test_the_note_goes_into_the_talk_right_away(self):
        # «А что ты там записала» — сразу после «всё», по его же словам.
        loop, отложенный = self._с_пустым_фоном()
        self._сказать(loop, "запиши заметку")
        self._сказать(loop, "мысль про воланда")
        self._сказать(loop, "всё")

        said, answered = loop.remembered[-1]
        self.assertEqual(said, "запиши заметку")
        self.assertEqual(answered, "Записала заметку: мысль про воланда")

    def test_the_background_keeps_a_failure_visible(self):
        # Настоящий поток: исключение внутри него проглатывать нельзя, иначе
        # потерянная запись выглядела бы как успешная.
        loop = _цикл(self.tmp, self)
        # Настоящий `_background` вместо подменённого заготовкой.
        loop._background = VoiceLoop._background.__get__(loop)
        поток_готов = threading.Event()
        loop._emit = lambda kind, payload: (loop.events.append((kind, payload)),
                                            поток_готов.set())

        loop._background(lambda: 1 / 0)
        self.assertTrue(поток_готов.wait(5.0), loop.events)
        self.assertEqual(loop.events[0][0], "error")
        self.assertIn("заметки: ZeroDivisionError", loop.events[0][1])


class NotesLockTests(unittest.TestCase):
    """Файл темы правится из двух потоков сразу — записи не теряются."""

    def test_two_threads_add_into_one_topic(self):
        # Диктовка ушла в фон, а пульт в это же время правит ту же тему:
        # без замка половина записей затиралась бы.
        tmp = Path(tempfile.mkdtemp(prefix="truba-lock-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        self._было = config.NOTES_DIR
        config.NOTES_DIR = str(tmp)
        self.addCleanup(setattr, config, "NOTES_DIR", self._было)

        def пишет(метка):
            for i in range(20):
                notes.add("Книги", "Мастер и Маргарита", f"{метка} {i}",
                          f"Мысль {метка}-{i}.")

        потоки = [threading.Thread(target=пишет, args=(м,))
                  for м in ("один", "два")]
        for поток in потоки:
            поток.start()
        for поток in потоки:
            поток.join()

        body = (tmp / "Книги" / "Мастер и Маргарита.md").read_text(encoding="utf-8")
        self.assertEqual(body.count("Мысль один-"), 20)
        self.assertEqual(body.count("Мысль два-"), 20)


if __name__ == "__main__":
    unittest.main()
