"""Кнопка «заметка» на телефоне: открывает ту же диктовку, что и голос.

Без сети, звука и микрофона: голосовой цикл собран из заглушек, поток
диктовки ждётся явно.
"""

import threading
import unittest
from pathlib import Path
from unittest import mock

from core import voice_loop
from core.voice_loop import DICTATION_START, VoiceLoop


def _цикл(ready=True):
    loop = object.__new__(VoiceLoop)
    loop.said, loop.events = [], []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._say_back = loop.said.append
    loop._open_conversation = lambda: None
    loop._turn = None
    loop._dictation = None
    loop._dictation_at = 0.0
    loop._dictation_command = ""
    loop.ready = ready
    loop._thread = mock.Mock(is_alive=lambda: ready)
    loop._speaker_idle = lambda: True
    loop.shut_up = mock.Mock()
    # Потоки, что жили до теста: их не ждём. 28.09 весь набор разросся на
    # 30 с — ждали по 2 с каждый фоновый поток серверов из других тестов.
    loop._потоки_до = set(threading.enumerate())
    return loop


def _дождаться(loop):
    for thread in threading.enumerate():
        if thread in loop._потоки_до or thread is threading.current_thread():
            continue
        if thread.daemon:
            thread.join(timeout=2)


class StartNoteTests(unittest.TestCase):
    def test_button_opens_the_dictation_like_the_voice_command(self):
        loop = _цикл()
        self.assertTrue(loop.start_note())
        _дождаться(loop)
        self.assertEqual(loop._dictation, [])
        self.assertEqual(loop.said, [DICTATION_START])
        self.assertIn(("note", "диктовка начата"), loop.events)

    def test_voice_off_means_nobody_to_dictate_to(self):
        loop = _цикл(ready=False)
        self.assertFalse(loop.start_note())
        self.assertIsNone(loop._dictation)
        self.assertEqual(loop.said, [])

    def test_second_press_does_not_restart_a_running_dictation(self):
        loop = _цикл()
        loop._dictation = ["уже надиктовано"]
        self.assertTrue(loop.start_note())
        _дождаться(loop)
        self.assertEqual(loop._dictation, ["уже надиктовано"])
        self.assertEqual(loop.said, [])

    def test_press_while_she_speaks_shuts_her_up_first(self):
        loop = _цикл()
        loop._speaker_idle = lambda: False
        loop.start_note()
        _дождаться(loop)
        loop.shut_up.assert_called_once()


class RuntimeButtonTests(unittest.TestCase):
    def _runtime(self, started):
        from ui.web_runtime import WebRuntime

        runtime = object.__new__(WebRuntime)
        runtime.voice = mock.Mock(start_note=mock.Mock(return_value=started))
        runtime.server = mock.Mock()
        runtime._remember = mock.Mock()
        return runtime

    def test_button_reaches_the_voice_loop(self):
        runtime = self._runtime(True)
        runtime._note_start()
        runtime.voice.start_note.assert_called_once()
        runtime.server.send_toast.assert_not_called()

    def test_voice_off_is_told_to_the_phone(self):
        runtime = self._runtime(False)
        runtime._note_start()
        text, ok = runtime.server.send_toast.call_args[0]
        self.assertIn("голос выключен", text)
        self.assertFalse(ok)

    def test_phone_page_has_the_button(self):
        page = Path(voice_loop.__file__).resolve().parents[1] / "web" / "index.html"
        html = page.read_text(encoding="utf-8")
        self.assertIn("id: 'note_start'", html)
        self.assertIn("action.id === 'note_start'", html)


if __name__ == "__main__":
    unittest.main()
