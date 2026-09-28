"""Регрессии разговора без микрофона, сети и звука."""

import queue
import threading
import unittest
from collections import deque
from datetime import datetime, timedelta

import numpy as np

import config
from core.audio_in import Listener
from core.brain import Brain
from core.voice_loop import VoiceLoop, is_dismissal


class ConversationTests(unittest.TestCase):
    def test_long_direct_dismissal_is_not_sent_to_model(self):
        self.assertTrue(is_dismissal(
            "Чего он надумала? Я говорю, блядь, тихо отдыхай, "
            "отдыхай. Всё, успокойся."
        ))
        self.assertFalse(is_dismissal(
            "Когда я говорю 'стоп', я просто объясняю пример."
        ))
        self.assertFalse(is_dismissal("Спасибо за помощь"))
        self.assertTrue(is_dismissal("Ты нахуя, блядь, всё ещё пиздишь-то со мной?"))

    def test_more_ways_to_say_enough(self):
        # Живые фразы хозяина 25 сентября, после которых она продолжала слушать.
        for phrase in ("Стоп. Ладно, хорошо, забей, пофиг.", "Не слушай меня.",
                       "Хватит слушать", "Всё, спасибо.", "Стоп, хватит.", "Проехали."):
            self.assertTrue(is_dismissal(phrase), phrase)
        # Это не прощание — разговор продолжается.
        for phrase in ("Стоп, а что там с погодой?", "Спасибо за помощь",
                       "Забей мне в календарь встречу на завтра и напомни"):
            self.assertFalse(is_dismissal(phrase), phrase)

    def test_off_mode_closes_old_conversation(self):
        loop = object.__new__(VoiceLoop)
        loop._emit = lambda *args: None
        loop._trust_next = False
        loop._open = True
        loop._last_turn = 1.0
        loop._brain = None
        loop._server = None
        loop._speaker = None
        loop._fallback = None
        loop._spoke_at = -1000.0
        loop._speaking_text = ""
        old = config.LISTEN_MODE
        try:
            config.LISTEN_MODE = "off"
            self.assertFalse(loop._should_answer("случайная речь"))
            self.assertFalse(loop._open)
        finally:
            config.LISTEN_MODE = old

    def test_history_has_time_but_api_receives_only_role_and_content(self):
        brain = object.__new__(Brain)
        brain._persona = "тест"
        brain._history = deque([{
            "role": "user", "content": "старый план",
            "at": (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds"),
        }])
        brain._abilities = lambda: ""
        brain._memory = lambda: ""
        messages = brain._messages("новый вопрос", None)
        self.assertTrue(all(set(message) == {"role", "content"} for message in messages))
        self.assertIn("мин назад", messages[-2]["content"])

    def test_mic_queue_keeps_only_short_tail_after_speech(self):
        listener = object.__new__(Listener)
        listener._queue = queue.Queue()
        listener._muted = threading.Event()
        listener._muted.set()
        listener._vad = type("Vad", (), {"reset": lambda self: None})()
        for _ in range(10):
            listener._queue.put(np.zeros(config.SAMPLE_RATE // 10, dtype=np.float32))
        listener.unmute(keep_seconds=0.25)
        self.assertLessEqual(listener._queue.qsize(), 3)


if __name__ == "__main__":
    unittest.main()
