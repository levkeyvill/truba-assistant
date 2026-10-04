"""Границы разговоров, общие правила беседы и даты в журнале."""

import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import brain as brain_module
from core.brain import Brain, TALK_RULES
from core.voice_loop import VoiceLoop
from ui import web_runtime
from ui.web_runtime import WebRuntime


def _brain(history=()):
    brain = object.__new__(Brain)
    brain._reply_lock = threading.RLock()
    brain._history = deque(history, maxlen=20)
    brain._keep_history = mock.Mock()
    brain._persona = "Характер"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    return brain


def _turn(role, content, at, talk_end=None):
    turn = {"role": role, "content": content, "at": at}
    if talk_end is not None:
        turn["talk_end"] = talk_end
    return turn


def _notes(messages):
    return [(i, message["content"]) for i, message in enumerate(messages)
            if message["role"] == "system" and
            message["content"].startswith("— Здесь закончился прошлый разговор")]


class TalkBoundaryTests(unittest.TestCase):
    def test_end_talk_saves_once_and_ignores_empty_history(self):
        brain = _brain([_turn("assistant", "Готово.", "2026-10-03T11:44:00")])
        with mock.patch.object(brain_module, "datetime", wraps=datetime) as clock:
            clock.now.side_effect = [datetime(2026, 10, 3, 11, 45, 36),
                                     datetime(2026, 10, 3, 11, 47)]
            brain.end_talk()
            brain.end_talk()
        self.assertEqual(brain._history[-1]["talk_end"], "2026-10-03T11:45:36")
        brain._keep_history.assert_called_once_with()

        empty = _brain()
        empty.end_talk()
        empty._keep_history.assert_not_called()

    def test_close_marker_appears_between_nearby_conversations(self):
        brain = _brain([
            _turn("user", "Открой Chrome", "2026-10-03T11:44:00"),
            _turn("assistant", "Не могу.", "2026-10-03T11:45:00",
                  "2026-10-03T11:45:36"),
            _turn("user", "Открой диск D", "2026-10-03T11:47:00"),
        ])
        messages = brain._messages("папку Torrent", None)
        self.assertEqual(_notes(messages), [
            (3, "— Здесь закончился прошлый разговор (в 11:45) и начался "
             "новый (03.10, 11:47). Всё выше — прошлые разговоры: "
             "это то, что уже было. —")])

    def test_gap_without_close_marker_uses_old_threshold(self):
        for at, expected in (("2026-10-03T11:47:00", False),
                             ("2026-10-03T12:26:01", True)):
            with self.subTest(at=at):
                brain = _brain([
                    _turn("assistant", "Не могу.", "2026-10-03T11:45:00"),
                    _turn("user", "Новый вопрос", at),
                ])
                notes = _notes(brain._messages("ответь", None))
                self.assertEqual(bool(notes), expected)
                if expected:
                    self.assertIn("и начался новый (03.10, 12:26)", notes[0][1])
                    self.assertNotIn("(в 11:45)", notes[0][1])

    def test_last_closed_turn_marks_end_of_history_before_tail(self):
        history = [_turn("assistant", "Пока.", "2026-10-03T11:45:00",
                         "2026-10-03T11:45:36")]
        brain = _brain(history)
        with mock.patch.object(brain_module, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 3, 11, 48)
            messages = brain._messages("новая просьба", None)
        self.assertEqual(_notes(messages), [
            (2, "— Здесь закончился прошлый разговор (в 11:45) и начался "
             "новый (03.10, 11:48). Всё выше — прошлые разговоры: "
             "это то, что уже было. —")])
        self.assertEqual(messages[-1], {"role": "user", "content": "новая просьба"})
        self.assertEqual(messages[-2]["role"], "system")
        open_history = [_turn("assistant", "Пока.", "2026-10-03T11:45:00")]
        self.assertEqual(_notes(_brain(open_history)._messages("новая просьба",
                                                                None)), [])

    def test_same_history_keeps_prefix_for_cache(self):
        brain = _brain([
            _turn("assistant", "Пока.", "2026-10-03T11:45:00",
                  "2026-10-03T11:45:36"),
            _turn("user", "Новый разговор", "2026-10-03T11:47:00"),
        ])
        first = brain._messages("один вопрос", None)
        second = brain._messages("другой вопрос", None)
        self.assertEqual(first[:-2], second[:-2])

    def test_rules_follow_persona_before_live_context(self):
        brain = _brain()
        brain._abilities = lambda: "Умения"
        system = brain._messages("привет", "рядом с компьютером")[0]["content"]
        self.assertTrue(system.startswith("Характер\n\nУмения\n\n" + TALK_RULES + "\n\n"))
        self.assertIn("\n\n## Что происходит прямо сейчас\n\n", system)

    def test_abilities_stay_in_first_system_until_programs_change(self):
        brain = _brain()
        brain._abilities = lambda: "Умения: редактор"
        with mock.patch.object(brain_module, "_now_line",
                               side_effect=["Часы 1", "Часы 2", "Часы 3"]):
            first = brain._messages("первый вопрос", None)
            second = brain._messages("второй вопрос", None)
            brain._abilities = lambda: "Умения: калькулятор"
            changed = brain._messages("третий вопрос", None)

        self.assertEqual(first[0]["content"],
                         "Характер\n\nУмения: редактор\n\n" + TALK_RULES)
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[-2]["content"], second[-2]["content"])
        self.assertEqual(changed[0]["content"],
                         "Характер\n\nУмения: калькулятор\n\n" + TALK_RULES)
        for messages in (first, second, changed):
            self.assertNotIn("Умения:", messages[-2]["content"])


class VoiceCloseTests(unittest.TestCase):
    @staticmethod
    def _loop(brain):
        loop = object.__new__(VoiceLoop)
        loop._open = True
        loop._brain = brain
        loop._last_turn = 0.0
        loop._first_pending = False
        loop._search_next = False
        loop._forget_held = lambda: None
        loop._emit = lambda *args: None
        loop._tell_phone = lambda **kwargs: None
        loop._digest_later = lambda: None
        return loop

    def test_close_calls_end_talk(self):
        brain = mock.Mock()
        loop = self._loop(brain)
        loop._close_conversation()
        brain.end_talk.assert_called_once_with()
        self.assertFalse(loop._open)

    def test_end_talk_error_does_not_interrupt_close(self):
        brain = mock.Mock()
        brain.end_talk.side_effect = RuntimeError("диск занят")
        loop = self._loop(brain)
        closed = []
        loop._emit = lambda kind, value: closed.append((kind, value))
        loop._close_conversation()
        self.assertFalse(loop._open)
        self.assertIn(("conversation", False), closed)


class LogDateTests(unittest.TestCase):
    def test_one_date_line_per_day_and_another_after_midnight(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = object.__new__(WebRuntime)
            runtime._lock = threading.Lock()
            runtime._log_path = Path(folder) / "session.log"
            runtime._last_log_date = None
            with mock.patch.object(web_runtime, "datetime", wraps=datetime) as clock:
                clock.now.side_effect = [datetime(2026, 10, 3, 11, 44),
                                         datetime(2026, 10, 3, 11, 45),
                                         datetime(2026, 10, 4, 0, 1)]
                runtime.log_message("первая")
                runtime.log_message("вторая")
                runtime.log_message("третья")
            self.assertEqual(runtime.logs(), [
                "[11:44:00] ——— 03.10.2026 ———",
                "[11:44:00] первая",
                "[11:45:00] вторая",
                "[00:01:00] ——— 04.10.2026 ———",
                "[00:01:00] третья",
            ])


if __name__ == "__main__":
    unittest.main()
