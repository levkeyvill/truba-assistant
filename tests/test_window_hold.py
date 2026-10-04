"""Окно разговора — тишина после последнего звука разговора.

Её ход продлевает окно, даже если ответ длится дольше срока. Фраза,
начатая в открытом окне, держит его до своего конца и
принимается, а речь, начатая после окна (Discord, колонки), его не держит.
"""

import unittest
from unittest import mock

import config
from core.voice_loop import VoiceLoop


def _цикл() -> VoiceLoop:
    loop = object.__new__(VoiceLoop)
    loop._open = True
    loop._last_turn = 0.0
    loop._dictation = None
    loop._turn = None
    loop._speaker_idle = lambda: True
    loop._speech_from = None
    loop._close_conversation = mock.Mock()
    return loop


class _Окно(unittest.TestCase):
    def setUp(self):
        подмена = mock.patch.object(config, "FOLLOW_UP_WINDOW", 10.0)
        подмена.start()
        self.addCleanup(подмена.stop)


class WindowHoldTests(_Окно):
    def _шаг(self, loop, now=1_000.0):
        with mock.patch("core.voice_loop.time.monotonic", return_value=now):
            loop._check_window()

    def test_silence_closes_the_window(self):
        loop = _цикл()
        self._шаг(loop)
        loop._close_conversation.assert_called_once()

    def test_her_turn_extends_the_window(self):
        # Думает: ход занят — окно продлевается от «сейчас».
        loop = _цикл()
        lock = loop._turn_lock()
        lock.acquire()
        try:
            self._шаг(loop)
        finally:
            lock.release()
        loop._close_conversation.assert_not_called()
        self.assertEqual(loop._last_turn, 1_000.0)
        with mock.patch("core.voice_loop.time.monotonic", return_value=1_005.0):
            self.assertTrue(loop.in_conversation, "после её хода окно снова целое")

    def test_her_speech_extends_the_window(self):
        loop = _цикл()
        loop._speaker_idle = lambda: False
        self._шаг(loop)
        loop._close_conversation.assert_not_called()

    def test_a_phrase_begun_in_the_window_holds_it(self):
        # Окно с 980 до 990; он начал говорить в 985 и ещё говорит в 1000.
        loop = _цикл()
        loop._last_turn = 980.0
        loop._speech_from = 985.0
        self._шаг(loop)
        loop._close_conversation.assert_not_called()
        self.assertEqual(loop._last_turn, 980.0, "окно не продлеваем — только ждём")

    def test_speech_begun_after_the_window_does_not_hold_it(self):
        # Discord заговорил уже после окна — закрываем.
        loop = _цикл()
        loop._last_turn = 980.0
        loop._speech_from = 995.0
        self._шаг(loop)
        loop._close_conversation.assert_called_once()

    def test_an_open_window_is_left_alone(self):
        loop = _цикл()
        loop._last_turn = 995.0
        self._шаг(loop)
        loop._close_conversation.assert_not_called()
        self.assertEqual(loop._last_turn, 995.0)


class MicVoiceTests(unittest.TestCase):
    def test_the_start_of_speech_is_kept_until_it_ends(self):
        loop = object.__new__(VoiceLoop)
        with mock.patch("core.voice_loop.time.monotonic", return_value=50.0):
            loop._mic_voice(True, 0.3)
        with mock.patch("core.voice_loop.time.monotonic", return_value=52.0):
            loop._mic_voice(True, 0.6)
        self.assertEqual(loop._speech_from, 50.0)
        loop._mic_voice(False)
        self.assertIsNone(loop._speech_from)


class ClosedAtTests(_Окно):
    def _цикл(self):
        loop = object.__new__(VoiceLoop)
        loop._open = True
        loop._last_turn = 100.0
        loop._first_pending = False
        loop._search_next = False
        loop._emit = lambda kind, payload: None
        loop._tell_phone = lambda *args, **kwargs: None
        loop._digest_later = lambda: None
        return loop

    def test_closed_late_remembers_when_the_window_expired(self):
        # Сторож ждал конца его фразы и закрыл в 115, а окно истекло в 110.
        loop = self._цикл()
        with mock.patch("core.voice_loop.time.monotonic", return_value=115.0):
            loop._close_conversation()
        self.assertEqual(loop._closed_at, 110.0)

    def test_closed_early_is_now(self):
        # «Хватит» или касание круга в 103 — окно кончилось сейчас.
        loop = self._цикл()
        with mock.patch("core.voice_loop.time.monotonic", return_value=103.0):
            loop._close_conversation()
        self.assertEqual(loop._closed_at, 103.0)


class StartedInWindowTests(_Окно):
    """Фраза, начатая в открытом окне, принимается, даже если кончилась позже."""

    def _фраза(self, секунды):
        return [0.0] * int(секунды * config.SAMPLE_RATE)

    def _цикл(self, opened, closed, open_now=False, last_turn=0.0):
        loop = object.__new__(VoiceLoop)
        loop._open = open_now
        loop._opened_at = opened
        loop._closed_at = closed
        loop._last_turn = last_turn
        return loop

    def _начало(self, loop, start, секунды=11.0):
        # Часы подменяем так, чтобы начало фразы вышло ровно `start`
        # (monotonic): конец = start + длина + тишина конца фразы.
        from core import audio_in

        конец = start + секунды + audio_in.SILENCE_TO_END
        with mock.patch("core.voice_loop.time.monotonic", return_value=конец), \
                mock.patch("core.voice_loop.time.perf_counter", return_value=конец):
            return loop._started_in_window(self._фраза(секунды), конец)

    def test_started_before_the_window_closed(self):
        # Фраза началась в 105, между открытием в 100 и закрытием в 110.
        loop = self._цикл(opened=100.0, closed=110.0)
        self.assertTrue(self._начало(loop, 105.0))

    def test_started_after_the_window_closed(self):
        loop = self._цикл(opened=100.0, closed=110.0)
        self.assertFalse(self._начало(loop, 111.0))

    def test_started_before_the_window_opened(self):
        loop = self._цикл(opened=100.0, closed=110.0)
        self.assertFalse(self._начало(loop, 95.0))

    def test_window_still_open(self):
        loop = self._цикл(opened=100.0, closed=0.0, open_now=True, last_turn=100.0)
        self.assertTrue(self._начало(loop, 105.0))

    def test_still_open_but_expired_does_not_count(self):
        # Сторож ещё не закрыл (ждёт чужую фразу), а окно истекло в 110:
        # фраза, начатая в 112, в окно не попала.
        loop = self._цикл(opened=100.0, closed=0.0, open_now=True, last_turn=100.0)
        self.assertFalse(self._начало(loop, 112.0))

    def test_never_opened(self):
        loop = self._цикл(opened=0.0, closed=0.0)
        self.assertFalse(self._начало(loop, 105.0))


if __name__ == "__main__":
    unittest.main()
