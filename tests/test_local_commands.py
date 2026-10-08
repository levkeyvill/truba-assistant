"""Локальные действия и поиск: облако и внешние действия подменены."""

import json
import tempfile
import threading
import time
import unittest
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
from urllib.parse import parse_qs, urlparse

import numpy as np

import test_data_guard
import config
from core import browser_search, clipboard, commands, launcher, reminders
from core.brain import Brain, FILLERS, own_turns
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime
from say_helpers import assert_said


def loop_without_io():
    loop = object.__new__(VoiceLoop)
    loop._search_next = False
    loop._dictation = None
    loop._interrupt = threading.Event()
    loop._voice_score = None
    loop._known_apps = lambda: []
    loop._power_turn = mock.Mock(return_value=False)
    loop._analysis_turn = mock.Mock(return_value=False)
    loop._open_conversation = mock.Mock()
    loop._emit = mock.Mock()
    loop._say_back = mock.Mock()
    loop._remember = mock.Mock()
    loop._answer = mock.Mock()
    loop._brain = NS(reply=mock.Mock(side_effect=AssertionError("Облако не нужно")))
    loop.read_aloud = mock.Mock(return_value=(True, "буфер обмена"))
    return loop


def turn(loop, text):
    loop._turn_body(text, np.zeros(config.SAMPLE_RATE // 2), time.perf_counter(), 0.1, True)


class BrowserTests(unittest.TestCase):
    def test_raw_query_keeps_punctuation_and_meaningful_words(self):
        cases = (
            ("Труба, загугли C++ STL сейчас", "C++ STL сейчас"),
            ("пожалуйста, открой поиск в браузере Что это значит?", "Что это значит"),
            ("погугли .NET и C++", ".NET и C++"),
            ("открой поиск C#", "C#"),
            ("найди в интернете, что сейчас умеет Python?", "что сейчас умеет Python"),
        )
        for text, query in cases:
            with self.subTest(text=text):
                self.assertEqual(commands.understand(text).target, query)

    def test_browser_receives_fixed_https_url_with_encoded_query(self):
        query = 'C++ & Python # "тест"; $(echo пример)'
        with mock.patch.object(launcher, "open_web", return_value=(True, "браузер")) as opened:
            result = browser_search.open_query(query)
        self.assertTrue(result["ok"])
        url = urlparse(opened.call_args.args[0])
        self.assertEqual((url.scheme, url.netloc, url.path),
                         ("https", "www.google.com", "/search"))
        self.assertEqual(parse_qs(url.query), {"q": [query]})
        self.assertEqual(url.fragment, "")

    def test_failures_are_not_reported_as_success(self):
        for outcome in ((False, "Нет браузера"), RuntimeError("Ошибка запуска")):
            with self.subTest(outcome=outcome):
                options = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with mock.patch.object(launcher, "open_web", **options):
                    result = browser_search.open_query("рецепт")
                self.assertFalse(result["ok"])
                self.assertIn("Не получилось", result["text"])

    def test_browser_command_does_not_use_the_model_or_search_backend(self):
        cases = (
            ("загугли ремонт велосипеда", "ремонт велосипеда"),
            ("найди в инете ремонт велосипеда", "ремонт велосипеда"),
            ("найди в интернете рецепт блинов", "рецепт блинов"),
            ("поищи, пожалуйста, ремонт велосипеда", "ремонт велосипеда"),
            ("найди внете ремонт велосипеда", "ремонт велосипеда"),
            ("найди, пожалуйста, в инете ремонт велосипеда", "ремонт велосипеда"),
            ("покажи результаты поиска C++", "C++"),
            ("поищи как проверить тормоза", "как проверить тормоза"),
        )
        from core import web

        for text, query in cases:
            with self.subTest(text=text):
                loop = loop_without_io()
                with mock.patch.object(launcher, "open_web", return_value=(True, "браузер")) as opened, \
                        mock.patch.object(web, "run_tool", side_effect=AssertionError("Поиск не нужен")) as research:
                    turn(loop, text)
                opened.assert_called_once()
                self.assertEqual(parse_qs(urlparse(opened.call_args.args[0]).query), {"q": [query]})
                research.assert_not_called()
                loop._answer.assert_not_called()
                loop._brain.reply.assert_not_called()
                loop._say_back.assert_called_once_with("Открыла поиск в браузере.")

    def test_compound_requests_and_negations_are_not_local_browser_commands(self):
        for text in ("не загугли ремонт велосипеда", "если спрошу загугли ремонт велосипеда",
                     "открой поиск C++ и прочитай результаты",
                     "загугли рецепт и сделай заметку",
                     "загугли рецепт но не открывай браузер", "загугли",
                     "не найди в инете рецепт блинов", "если попрошу поищи рецепт блинов",
                     "поищи пожалуйста", "найди в инете пожалуйста"):
            with self.subTest(text=text):
                self.assertIsNone(commands.understand(text))

    def test_research_and_phone_button_keep_the_model_path(self):
        for text, button in (("найди и расскажи про ремонт велосипеда", False),
                             ("поищи ремонт велосипеда и расскажи как сделать", False),
                             ("найди в интернете велосипед и сравни цены", False),
                             ("загугли рецепт и потом объясни его", False),
                             ("проверь в интернете прогноз погоды", False),
                             ("найди ответ на вопрос о ремонте велосипеда", False),
                             ("загугли ремонт велосипеда", True),
                             ("найди в инете ремонт велосипеда", True),
                             ("ремонт велосипеда", True)):
            with self.subTest(text=text):
                loop = loop_without_io()
                loop._search_next = button
                with mock.patch.object(launcher, "open_web") as opened:
                    turn(loop, text)
                opened.assert_not_called()
                self.assertTrue(loop._answer.call_args.kwargs["search"])


class TimerTests(unittest.TestCase):
    def test_duration_is_exact_and_has_no_model(self):
        for text, seconds in (("поставь таймер на пять минут", 300),
                              ("таймер на 30 секунд", 30),
                              ("установи таймер на двадцать пять минут", 1500),
                              ("таймер на полчаса", 1800),
                              ("поставь таймер на час", 3600),
                              ("таймер на одну минуту", 60),
                              ("таймер на две минуты", 120),
                              ("таймер на 100 секунд", 100),
                              ("таймер на 120 секунд", 120)):
            with self.subTest(text=text):
                command = commands.understand(text)
                self.assertEqual((command.action, command.seconds), ("timer", seconds))

    def test_unclear_duration_and_compound_requests_go_to_model(self):
        for text in ("таймер на ноль минут", "таймер на -5 минут", "таймер на 2.5 минуты",
                     "таймер на сколько-нибудь минут", "таймер на пять минут и десять секунд",
                     "таймер на пять минут и расскажи историю", "таймер на завтра",
                     "не ставь таймер на пять минут", "поставь таймер в пять вечера",
                     "напомни через двадцать минут проверить духовку"):
            with self.subTest(text=text):
                self.assertIsNone(commands.understand(text))

    def test_local_timer_uses_existing_store_and_ready_confirmation(self):
        now = datetime(2030, 1, 1, 12, tzinfo=timezone(timedelta(hours=5)))
        for seconds, phrase in ((30, "таймер на 30 секунд"), (90, "таймер на 90 секунд"),
                                (300, "таймер на пять минут")):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as folder:
                loop = loop_without_io()
                with mock.patch.object(reminders, "PATH", Path(folder) / "reminders.json"), \
                        mock.patch.object(reminders, "local_now", return_value=now):
                    turn(loop, phrase)
                    records = reminders.pending()
                self.assertEqual(len(records), 1)
                record = records[0]
                self.assertEqual(record["kind"], "timer")
                self.assertEqual((reminders.parse(record["due"]) - now).total_seconds(), seconds)
                duration = reminders.seconds_said(seconds) if seconds % 60 else reminders.minutes_said(seconds // 60)
                assert_said(self, loop._say_back.call_args.args[0], "timer_set", time=duration)
                assert_said(self, reminders.phrase(record), "timer_done", time=duration)
                self.assertEqual(loop._say_back.call_count, 1)
                loop._answer.assert_not_called()
                loop._brain.reply.assert_not_called()

    def test_timer_storage_failure_is_not_success(self):
        loop = loop_without_io()
        with mock.patch.object(reminders, "add", side_effect=OSError("Диск недоступен")):
            turn(loop, "таймер на пять минут")
        loop._say_back.assert_called_once_with("Не получилось поставить таймер.")
        loop._answer.assert_not_called()


class ClipboardTests(unittest.TestCase):
    def test_only_verbatim_reading_is_local(self):
        for text in ("прочитай скопированное", "прочитай буфер обмена", "зачитай скопированное"):
            self.assertEqual(commands.understand(text).action, "clipboard_read")
        for text in ("переведи скопированное", "перескажи скопированное",
                     "прочитай скопированное и объясни смысл", "не читай скопированное"):
            self.assertIsNone(commands.understand(text))

    def test_clipboard_text_goes_only_to_local_reader(self):
        loop = loop_without_io()
        copied = {"ok": True, "text": "Выдуманный текст для чтения.", "chars": 28, "cut": False}
        with mock.patch.object(clipboard, "text", return_value=copied):
            turn(loop, "прочитай скопированное")
        loop.read_aloud.assert_called_once_with(copied["text"], "буфер обмена")
        loop._answer.assert_not_called()
        loop._brain.reply.assert_not_called()
        self.assertNotIn(copied["text"], str(loop._remember.call_args))
        self.assertNotIn(copied["text"], str(loop._emit.call_args_list))

    def test_empty_clipboard_does_not_start_reading(self):
        loop = loop_without_io()
        with mock.patch.object(clipboard, "text", return_value={"ok": False, "why": "Буфер пуст"}):
            turn(loop, "прочитай буфер обмена")
        loop.read_aloud.assert_not_called()
        loop._say_back.assert_called_once_with("Буфер пуст")


class QueryTests(unittest.TestCase):
    def brain(self):
        brain = object.__new__(Brain)
        brain._history = deque()
        brain.on_event = mock.Mock()
        return brain

    def test_self_contained_search_never_calls_query_model(self):
        brain = self.brain()
        brain._ask_plainly = mock.Mock(side_effect=AssertionError("Лишний запрос"))
        with mock.patch.object(config, "WEB_SEARCH_MODE", "free"):
            self.assertEqual(brain._search_queries("найди в интернете C++ STL", "C++ STL"), ["C++ STL"])
        brain._ask_plainly.assert_not_called()
        self.assertTrue(brain.on_event.call_args.args[1]["direct"])

    def test_contextual_query_is_rewritten(self):
        brain = self.brain()
        brain._history.append({"role": "user", "content": "Выдуманная тема: велосипед"})
        brain._ask_plainly = mock.Mock(return_value=NS(choices=[NS(message=NS(
            content=json.dumps({"queries": ["ремонт велосипеда"]})))]))
        with mock.patch.object(config, "WEB_SEARCH_MODE", "free"):
            self.assertEqual(brain._search_queries("поищи про него подробнее", "про него подробнее"),
                             ["ремонт велосипеда"])
        brain._ask_plainly.assert_called_once()

    def test_timing_separates_wait_phrase_from_answer(self):
        from test_fast_actions import _loop, _timing_of

        loop = _loop([])
        def reply(*a, **kw):
            yield next(iter(FILLERS))
            time.sleep(0.12)
            yield "Ответ на выдуманный вопрос."
        loop._brain.reply = reply
        with mock.patch.object(config, "OUTPUT", "speakers"):
            loop._answer("выдуманный вопрос")
        timing = _timing_of(loop)
        self.assertGreaterEqual(timing["answer"] - timing["total"], 0.1)
        self.assertIn("ответ по делу", WebRuntime._log_messages("timing", timing)[0])


class LocalMemoryTests(unittest.TestCase):
    def brain(self):
        brain = object.__new__(Brain)
        brain._reply_lock = threading.RLock()
        brain._history = deque()
        brain._undigested = 0
        brain._keep_history = lambda: None
        return brain

    def test_local_commands_stay_in_context_without_background_model_call(self):
        brain = self.brain()
        brain._ask_plainly = mock.Mock(side_effect=AssertionError("Память команд не требует облака"))
        for _ in range(3):
            brain.remember("загугли выдуманный рецепт для теста", "Открыла поиск в браузере.")
        self.assertEqual(len(brain._history), 6)
        self.assertEqual(brain.digest(), {})
        self.assertFalse(brain.did_ask)
        brain._ask_plainly.assert_not_called()

    def test_mixed_history_keeps_spoken_facts_and_skips_actions(self):
        brain = self.brain()
        brain.remember("таймер на пять минут", "Таймер пошёл.")
        brain._add_turn("Выдуманный пример: люблю кататься на велосипеде.", "Запомню.")
        kept = own_turns(list(brain._history), 0)
        self.assertEqual([t["content"] for t in kept],
                         ["Выдуманный пример: люблю кататься на велосипеде.", "Запомню."])


if __name__ == "__main__":
    unittest.main()
