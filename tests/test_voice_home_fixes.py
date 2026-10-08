"""Вымышленные документы и сигнал: без микрофона, моделей и действий ОС."""
import tempfile
import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import documents
from core.audio_in import Listener, VAD_HOP
from core.speech_text import speech_chunks
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime
from test_echo_vs_owner import _loop


class SpeechChunks(unittest.TestCase):
    def test_preserves_long_text_numbers_and_punctuation(self):
        text = ('Вымышленный пример: коэффициент 1,5; версия 2.0. '
                'Это длинное объяснение, в котором сохраняются все слова. ') * 15
        parts = speech_chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertEqual(''.join(parts), text)

    def test_short_text_is_unchanged(self):
        self.assertEqual(speech_chunks('Короткий ответ.'), ['Короткий ответ.'])

    def test_long_token_still_fits(self):
        text = 'x' * 1001
        self.assertEqual(''.join(speech_chunks(text)), text)
        self.assertTrue(all(len(p) <= 300 for p in speech_chunks(text)))

    def test_stream_never_receives_a_whole_long_answer(self):
        received, closed = [], []
        def stream(text, *args, **kwargs):
            received.append(text)
            self.assertLessEqual(len(text), 300)
            try:
                yield np.ones(4, dtype=np.float32), 24000
            finally:
                closed.append(text)
        loop = object.__new__(VoiceLoop)
        loop._voice, loop._ref = NS(stream=stream), None
        text = 'Вымышленный разбор, длинное предложение без окончания. ' * 20
        with mock.patch('core.voice_loop.voice_gain', return_value=1.0):
            parts, streamed = loop._sound_of(text, NS(gapless=True))
            list(parts)
        self.assertTrue(streamed)
        self.assertEqual(''.join(received), text)
        self.assertEqual(received, closed)

    def test_interruption_closes_the_active_synthesis(self):
        closed = threading.Event()
        def stream(*args, **kwargs):
            try:
                yield np.ones(4, dtype=np.float32), 24000
                yield np.ones(4, dtype=np.float32), 24000
            finally:
                closed.set()
        loop = object.__new__(VoiceLoop)
        loop._voice, loop._ref = NS(stream=stream), None
        with mock.patch('core.voice_loop.voice_gain', return_value=1.0):
            parts, _ = loop._sound_of('Вымышленный текст. ' * 70, NS(gapless=True))
            next(parts)
            parts.close()
        self.assertTrue(closed.is_set())


class ContinuousListening(unittest.TestCase):
    def test_vad_reset_waits_for_audio_analysis(self):
        entered, release, reset, attempted = (threading.Event() for _ in range(4))
        listener = object.__new__(Listener)
        listener._stop, listener._muted = threading.Event(), threading.Event()
        listener._queue = queue.Queue()
        listener._queue.put(np.zeros(VAD_HOP, dtype=np.float32))
        listener.speaker_echo = None
        listener._barge_threshold = 0.0
        listener._reset_echo()
        def probability(chunk):
            entered.set()
            release.wait(2)
            listener._stop.set()
            return 0.0
        listener._vad = NS(probability=probability, reset=reset.set)
        worker = threading.Thread(target=lambda: list(listener.phrases()))
        def unmute():
            attempted.set()
            listener.unmute()
        resetter = threading.Thread(target=unmute)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            resetter.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(reset.wait(0.05), 'VAD сброшен во время разбора')
        finally:
            release.set()
            worker.join(2)
            if resetter.ident is not None:
                resetter.join(2)
        self.assertTrue(reset.is_set())
        self.assertFalse(worker.is_alive())
        self.assertFalse(resetter.is_alive())

    def test_capture_keeps_running_during_a_blocked_answer(self):
        answering, barged = threading.Event(), threading.Event()
        handled = []
        loop = object.__new__(VoiceLoop)
        loop._stop = threading.Event()
        loop._emit = mock.Mock()
        def phrases():
            yield 'первая'
            if not answering.wait(2):
                return
            barged.set()
            yield 'перебивание'
        loop._listener = NS(phrases=phrases, stop=lambda: None)
        def handle(phrase, heard_at=None):
            handled.append(phrase)
            self.assertIsInstance(heard_at, float)
            if phrase == 'первая':
                answering.set()
                self.assertTrue(barged.wait(2), 'прослушивание ждало окончания ответа')
        loop._handle = handle
        loop._listen()
        self.assertEqual(handled, ['первая', 'перебивание'])

    def test_short_multiword_reply_after_speech_is_accepted(self):
        loop = _loop('Вымышленный ответ про погоду.')
        wave = np.zeros(int(config.SAMPLE_RATE), dtype=np.float32)
        with mock.patch.multiple(config, LISTEN_MODE='always', VOICE_APP_GUARD=False), \
             mock.patch.object(VoiceLoop, '_is_owner', return_value=(True, None)):
            self.assertTrue(loop._should_answer('Открой браузер.', wave, time.perf_counter()))

    def test_imperative_is_distinct_from_an_infinitive_in_her_answer(self):
        loop = _loop('Могу прочитать документ и показать результаты.')
        wave = np.zeros(int(config.SAMPLE_RATE), dtype=np.float32)
        with mock.patch.multiple(config, LISTEN_MODE='always', VOICE_APP_GUARD=False), \
             mock.patch.object(VoiceLoop, '_is_owner', return_value=(True, None)):
            self.assertTrue(loop._should_answer('Прочитай документ.', wave, time.perf_counter()))

    def test_short_owner_can_repeat_a_quoted_command(self):
        loop = _loop('Можно сказать: прочитай документ.')
        wave = np.zeros(int(config.SAMPLE_RATE), dtype=np.float32)
        def owner(phrase):
            loop._short_voice_score = 0.45
            return True, None
        with mock.patch.multiple(config, LISTEN_MODE='always', VOICE_APP_GUARD=False,
                                 OWNER_THRESHOLD=0.4), \
             mock.patch.object(VoiceLoop, '_is_owner', side_effect=owner):
            self.assertTrue(loop._should_answer('Прочитай документ.', wave, time.perf_counter()))

    def test_unconfirmed_exact_echo_of_a_command_is_rejected(self):
        loop = _loop('Можно сказать: прочитай документ.')
        wave = np.zeros(int(config.SAMPLE_RATE), dtype=np.float32)
        def owner(phrase):
            loop._short_voice_score = 0.2
            return True, None
        with mock.patch.multiple(config, LISTEN_MODE='always', VOICE_APP_GUARD=False,
                                 OWNER_THRESHOLD=0.4), \
             mock.patch.object(VoiceLoop, '_is_owner', side_effect=owner):
            self.assertFalse(loop._should_answer('Прочитай документ.', wave, time.perf_counter()))


class RtfReading(unittest.TestCase):
    def read(self, source, limit=12000):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'вымышленный-пример.rtf'
            path.write_bytes(source)
            return documents.read_text(path, limit=limit)

    def test_unicode_rtf(self):
        result = self.read(br'{\rtf1\ansi\uc1 \u1055?\u1088?\u1080?\u1074?\u1077?\u1090?\par Test}')
        self.assertTrue(result['ok'], result)
        self.assertIn('Привет', result['text'])
        self.assertIn('Test', result['text'])

    def test_cyrillic_codepage(self):
        result = self.read(br"{\rtf1\ansi\ansicpg1251 \'cf\'f0\'e8\'e2\'e5\'f2}")
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['text'].strip(), 'Привет')

    def test_styles_do_not_become_text(self):
        result = self.read(br'{\rtf1\ansi {\fonttbl{\f0 Arial;}}\b Example\b0\par Second}')
        self.assertTrue(result['ok'], result)
        self.assertNotIn('Arial', result['text'])
        self.assertIn('Example', result['text'])

    def test_unescaped_cyrillic_uses_the_declared_codepage(self):
        result = self.read(('{\\rtf1\\ansi\\ansicpg1251 Привет}').encode('cp1251'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['text'].strip(), 'Привет')

    def test_truncation_is_explicit(self):
        result = self.read(b'{\\rtf1\\ansi ' + b'Example ' * 50 + b'}', limit=50)
        self.assertTrue(result['cut'])
        self.assertLessEqual(len(result['text']), 50)

    def test_invalid_rtf_is_rejected(self):
        self.assertFalse(self.read(b'not an RTF')['ok'])


class HomeActions(unittest.TestCase):
    def setUp(self):
        self.runtime = object.__new__(WebRuntime)
        self.runtime._remember = mock.Mock()

    def test_search_is_local_and_preserves_query(self):
        with mock.patch('core.browser_search.open_query', return_value={
                'ok': True, 'query': 'Вымышленный запрос C++'}) as opened:
            result = self.runtime.home_action({'action': 'search', 'query': 'Вымышленный запрос C++'})
        self.assertTrue(result['ok'])
        opened.assert_called_once_with('Вымышленный запрос C++')

    def test_empty_search_does_not_open_a_browser(self):
        with mock.patch('core.browser_search.open_query') as opened:
            self.assertFalse(self.runtime.home_action({'action': 'search', 'query': ' '})['ok'])
        opened.assert_not_called()

    def test_voice_volume_uses_existing_persistence(self):
        self.runtime.voice_volume = mock.Mock(return_value={'ok': True, 'value': 4})
        self.assertTrue(self.runtime.home_action({'action': 'volume', 'level': 4})['ok'])
        self.runtime.voice_volume.assert_called_once_with(level=4)

    def test_clipboard_read_does_not_invoke_a_model(self):
        self.runtime.voice = NS(running=True, ready=True, read_aloud=mock.Mock(return_value=(True, 'Буфер')))
        with mock.patch('core.clipboard.text', return_value={'ok': True, 'text': 'Вымышленный текст'}):
            result = self.runtime.home_action({'action': 'clipboard'})
        self.assertTrue(result['ok'])
        self.runtime.voice.read_aloud.assert_called_once_with('Вымышленный текст', name='Буфер обмена')

    def test_failure_to_start_reading_is_visible(self):
        self.runtime.voice = NS(running=True, ready=True, read_aloud=mock.Mock(return_value=(False, 'занята')))
        with mock.patch('core.clipboard.text', return_value={'ok': True, 'text': 'Вымышленный текст'}):
            result = self.runtime.home_action({'action': 'clipboard'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'занята')

    def test_unrecognized_action_cannot_reach_pc_control(self):
        self.assertFalse(self.runtime.home_action({'action': 'power'})['ok'])

    def test_invalid_timer_never_writes(self):
        with mock.patch('core.reminders.add') as add:
            self.assertFalse(self.runtime.home_action({'action': 'timer', 'seconds': 0})['ok'])
        add.assert_not_called()


if __name__ == '__main__':
    unittest.main()
