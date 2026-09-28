"""Недоговорённая фраза: не отвечать на «э-э-э…».

27.09, 16:11:37 она услышала «Да, молодец. Слушай, этот, э-э-э...» и через
секунду ответила «Слушаю, не торопись. Что там у тебя?». В 16:11:52 хозяин
сказал «А что ты перебиваешь-то меня? Я ещё не закончил». Микрофон отдаёт
фразу после 1.15 с тишины (audio_in.SILENCE_TO_END), а на этой паузе человек
ещё формулирует мысль.

Теперь фраза, кончающаяся на запинке, уходит не в ответ, а в «держатель»:
следующая фраза склеивается с ней и уходит в ответ одной мыслью, а если
продолжения нет — сторож отвечает на удержанное сам.

Голосовой цикл собран вручную (как в `test_window_hold.py`): без микрофона,
синтеза и облака. Время — подменой `monotonic`, без `sleep`.
"""

import threading
import unittest
from unittest import mock

import numpy as np

import config
from core.voice_loop import VoiceLoop, unfinished


# --- Признак недоговорённой фразы ------------------------------------------


class UnfinishedTests(unittest.TestCase):
    def test_the_phrase_from_the_log_is_unfinished(self):
        # 27.09, 16:11:37 — ровно то, на что она ответила.
        self.assertTrue(unfinished("Да, молодец. Слушай, этот, э-э-э..."))

    def test_a_command_is_not_unfinished(self):
        self.assertFalse(unfinished("Открой дискорд"))

    def test_a_hesitation_in_the_middle_does_not_count(self):
        # «Слушай» в начале — обычное обращение, а фраза договорённая.
        self.assertFalse(unfinished("Слушай, что такое"))

    def test_the_filler_alone_is_unfinished(self):
        # Одно слово целиком: «ну» в конце — человек набирает мысль.
        self.assertTrue(unfinished("…ну"))

    def test_a_trailing_link_word_is_unfinished(self):
        # «Я думаю, что» — связка перед ещё не сказанным.
        self.assertTrue(unfinished("Я думаю, что"))

    def test_ended_by_a_comma_or_a_dash(self):
        self.assertTrue(unfinished("Ну, короче, я хотел сказать,"))
        self.assertTrue(unfinished("Смотри —"))

    def test_a_finished_phrase_is_not_held(self):
        for фраза in ("Что делать", "Сколько времени", "Закрой окно", ""):
            self.assertFalse(unfinished(фраза), фраза)


# --- Держатель ------------------------------------------------------------


def _фраза() -> np.ndarray:
    return np.zeros(config.SAMPLE_RATE, dtype=np.float32)


def _цикл() -> VoiceLoop:
    """Голосовой цикл вручную: распознавание и ответ подменены.

    Собран так же, как в `test_window_hold.py`: замок хода и срок захода
    первой создаются по первому обращению, поэтому `None` у них не мешает.
    """
    loop = object.__new__(VoiceLoop)
    loop._stt = mock.Mock()
    loop._should_answer = mock.Mock(return_value=True)
    loop._turn_body = mock.Mock()
    loop._answer = mock.Mock()
    loop._turn = None
    loop._first = None
    loop._first_pending = False
    loop._voice_score = None
    loop._dictation = None
    loop._open = True
    loop._last_turn = 1e9
    loop._search_next = False
    loop._speech_from = None
    loop._interrupt = threading.Event()
    loop._emit = lambda kind, payload: None
    loop._open_conversation = mock.Mock()
    return loop


def _принять(loop: VoiceLoop, текст: str, now: float) -> None:
    """Одна услышанная фраза в заданный момент."""
    loop._stt.recognize.return_value = текст
    with mock.patch("core.voice_loop.time.monotonic", return_value=now), \
            mock.patch("core.voice_loop.time.perf_counter", return_value=now):
        loop._handle(_фраза())


def _шаг_сторожа(loop: VoiceLoop, now: float) -> None:
    with mock.patch("core.voice_loop.time.monotonic", return_value=now):
        loop._check_held()


class HeldPhraseTests(unittest.TestCase):
    def test_an_unfinished_phrase_does_not_reach_the_answer(self):
        loop = _цикл()

        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        loop._turn_body.assert_not_called()
        loop._answer.assert_not_called()
        self.assertEqual(loop._held_text, "Слушай, этот, э-э-э...")

    def test_it_is_reported_as_waiting_for_more(self):
        loop = _цикл()
        loop._emit = mock.Mock()

        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        loop._emit.assert_called_once_with(
            "ignored",
            {"text": "Слушай, этот, э-э-э...", "why": "жду продолжения"},
        )

    def test_the_next_phrase_in_time_is_glued_into_one_answer(self):
        loop = _цикл()
        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        # Через две секунды (меньше CONTINUE_WAIT) он договорил.
        _принять(loop, "вот, что я хотел сказать", 102.0)

        loop._turn_body.assert_called_once()
        текст = loop._turn_body.call_args[0][0]
        self.assertEqual(текст, "Слушай, этот, э-э-э... вот, что я хотел сказать")
        self.assertIsNone(loop._held_text, "держатель должен быть очищен")

    def test_the_glued_phrase_may_be_unfinished_again(self):
        loop = _цикл()
        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        # Продолжение само кончается запинкой — держим уже склейку.
        _принять(loop, "ну, я думаю, что...", 102.0)

        loop._turn_body.assert_not_called()
        self.assertEqual(
            loop._held_text, "Слушай, этот, э-э-э... ну, я думаю, что..."
        )

    def test_after_the_wait_a_late_phrase_does_not_get_glued(self):
        loop = _цикл()
        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        # Ждём дольше CONTINUE_WAIT: удержанное отвечает сторож, а эта фраза
        # идёт сама по себе.
        _принять(loop, "Что делать", 100.0 + config.CONTINUE_WAIT + 1.0)

        loop._turn_body.assert_called_once()
        self.assertEqual(loop._turn_body.call_args[0][0], "Что делать")

    def test_dictation_is_not_held(self):
        # Во время диктовки фраза идёт в заметку целиком: там «всё» и «стоп»
        # значат совсем другое, а ждать продолжения нельзя.
        loop = _цикл()
        loop._dictation = []

        _принять(loop, "э-э-э", 100.0)

        loop._turn_body.assert_called_once()
        self.assertEqual(loop._turn_body.call_args[0][0], "э-э-э")
        self.assertIsNone(loop._held_text)


class HeldWatchdogTests(unittest.TestCase):
    def _удержать(self, loop, now=100.0):
        _принять(loop, "Слушай, этот, э-э-э...", now)
        self.assertIsNotNone(loop._held_text)

    def test_after_the_wait_and_silence_it_answers(self):
        loop = _цикл()
        self._удержать(loop)

        _шаг_сторожа(loop, 100.0 + config.CONTINUE_WAIT + 0.5)

        loop._turn_body.assert_called_once()
        self.assertEqual(loop._turn_body.call_args[0][0], "Слушай, этот, э-э-э...")
        self.assertIsNone(loop._held_text, "удержано отвечено один раз")

    def test_before_the_wait_it_waits(self):
        loop = _цикл()
        self._удержать(loop)

        _шаг_сторожа(loop, 100.0 + config.CONTINUE_WAIT - 0.5)

        loop._turn_body.assert_not_called()
        self.assertIsNotNone(loop._held_text)

    def test_while_he_is_still_talking_it_waits(self):
        # Человек думает вслух: следующая фраза ещё склеится, отвечать
        # посреди его речи нельзя.
        loop = _цикл()
        self._удержать(loop)
        loop._speech_from = 104.0

        _шаг_сторожа(loop, 110.0)

        loop._turn_body.assert_not_called()
        self.assertIsNotNone(loop._held_text)

    def test_while_she_is_talking_it_tries_later(self):
        # Её ход занят — сторож не ждёт, а пробует на следующем тике.
        loop = _цикл()
        self._удержать(loop)
        loop._turn_lock().acquire()
        try:
            _шаг_сторожа(loop, 110.0)
        finally:
            loop._turn_lock().release()
        loop._turn_body.assert_not_called()
        self.assertIsNotNone(loop._held_text)

        _шаг_сторожа(loop, 111.0)
        loop._turn_body.assert_called_once()

    def test_it_answers_the_same_way_a_turn_does(self):
        # Сторож идёт через `_turn_body`: разбор команд и `_answer` — те же.
        loop = _цикл()
        loop._turn_body = VoiceLoop._turn_body.__get__(loop)
        loop._known_apps = mock.Mock(return_value=[])
        self._удержать(loop)

        with mock.patch("core.commands.understand", return_value=None) as разбор:
            _шаг_сторожа(loop, 110.0)

        разбор.assert_called_once_with("Слушай, этот, э-э-э...", [])
        loop._answer.assert_called_once()
        self.assertEqual(loop._answer.call_args[0][0], "Слушай, этот, э-э-э...")

    def test_nothing_held_nothing_asked(self):
        loop = _цикл()

        _шаг_сторожа(loop, 110.0)

        loop._turn_body.assert_not_called()


class HushTests(unittest.TestCase):
    def test_closing_the_conversation_forgets_the_phrase(self):
        # «Хватит»: ждать продолжения уже не от чего.
        loop = _цикл()
        loop._search_next = False
        loop._first_pending = False
        loop._tell_phone = lambda *args, **kwargs: None
        loop._digest_later = lambda: None
        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        with mock.patch("core.voice_loop.time.monotonic", return_value=105.0):
            loop._close_conversation()

        self.assertIsNone(loop._held_text)

    def test_the_orb_tap_forgets_the_phrase(self):
        # Касание круга на телефоне шлёт `hush` → `voice_hush` → `shut_up`
        # и `_close_conversation`. Проверяем обе половины: держатель должен
        # уйти и оттуда.
        from ui.web_runtime import WebRuntime

        loop = _цикл()
        loop._speaker = None
        loop._fallback = None
        loop._search_next = False
        loop._first_pending = False
        loop._tell_phone = lambda *args, **kwargs: None
        loop._digest_later = lambda: None
        _принять(loop, "Слушай, этот, э-э-э...", 100.0)

        pult = object.__new__(WebRuntime)
        pult.voice = loop
        pult._remember = lambda kind, payload: None
        pult.voice_state = lambda: {}

        pult.handle_event("hush", {"type": "hush"})

        self.assertIsNone(loop._held_text)



class FinishedByPunctuationTests(unittest.TestCase):
    """Точку и вопрос распознавание ставит на законченной фразе."""

    def test_a_question_ending_in_a_filler_word_is_finished(self):
        from core.voice_loop import unfinished

        for фраза in ("Что это?", "Ну вот.", "А что?", "Это что!"):
            with self.subTest(фраза=фраза):
                self.assertFalse(unfinished(фраза))

    def test_a_pure_hesitation_sound_is_unfinished_even_after_a_dot(self):
        from core.voice_loop import unfinished

        for фраза in ("Слушай, э-э.", "Короче, м-м.", "Э-э."):
            with self.subTest(фраза=фраза):
                self.assertTrue(unfinished(фраза))

    def test_no_final_mark_uses_the_word_set(self):
        from core.voice_loop import unfinished

        self.assertTrue(unfinished("Тогда я думаю что"))
        self.assertFalse(unfinished("Тогда я думаю"))

if __name__ == "__main__":
    unittest.main()
