"""Отмена позднего ответа и визуальные уровни: вымышленные реплики/сигналы."""
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock
import numpy as np

import test_data_guard
import config
from core.cancel_stream import ReplyCancelled, open_reply_stream
from core.voice_turns import LatestTurnRunner
from core.playback_meter import PlaybackMeter
from core.voice_loop import VoiceLoop
from core.audio_out import Speaker
from ui.web_runtime import WebRuntime
from test_brain_first import _brain, _chunk
from test_fast_actions import _loop as answer_loop


class CancelledNetwork(unittest.TestCase):
    def test_cancel_does_not_wait_for_a_blocked_http_open(self):
        entered, release, closed, done = (threading.Event() for _ in range(4))
        cancel = threading.Event()
        def opening():
            entered.set()
            release.wait(2)
            return NS(close=closed.set)
        def run():
            try:
                with self.assertRaises(ReplyCancelled):
                    open_reply_stream(opening, cancel)
            finally:
                done.set()
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            cancel.set()
            self.assertTrue(done.wait(.5), 'отмена ждёт HTTP')
        finally:
            release.set()
            thread.join(2)
        self.assertTrue(closed.wait(1), 'поздний HTTP оставлен открытым')

    def test_late_model_chunk_is_discarded_and_next_reply_can_start(self):
        brain = _brain()
        waiting, release, closed = (threading.Event() for _ in range(3))
        class Stream:
            def __iter__(self):
                yield _chunk('Первое вымышленное предложение. ')
                waiting.set()
                release.wait(2)
                yield _chunk('Поздний хвост старого ответа.')
            def close(self):
                closed.set()
        calls = []
        def create(**body):
            calls.append(body)
            return Stream() if len(calls) == 1 else iter([_chunk('Ответ на новую реплику.')])
        brain._client.chat.completions.create = create
        cancel = threading.Event()
        first = brain.reply('Первый вымышленный вопрос', cancel=cancel)
        with mock.patch.multiple(config, HEDGE=False):
            try:
                self.assertIn('Первое', next(first))
                self.assertTrue(waiting.wait(1))
                cancel.set()
                self.assertEqual(list(first), [])
                self.assertTrue(closed.wait(1))
                second = list(brain.reply('Новая вымышленная реплика', cancel=threading.Event()))
                self.assertEqual(second, ['Ответ на новую реплику.'])
                self.assertNotIn('Поздний хвост', str(brain._history))
            finally:
                release.set()
                first.close()

    def test_opened_stream_closes_even_if_cancelled_before_first_chunk(self):
        closed = threading.Event()
        raw = NS(close=closed.set)
        stream = open_reply_stream(lambda: raw, threading.Event())
        stream.close()
        self.assertTrue(closed.wait(1))

    def test_cancel_while_audio_plays_marks_the_correct_history_answer(self):
        brain = _brain()
        cancel = threading.Event()
        list(brain.reply('Вымышленный вопрос', cancel=cancel))
        brain._add_turn('Другой вымышленный вопрос в чате', 'Ответ в чате')
        cancel.set()
        brain.mark_voice_interrupted(cancel)
        self.assertEqual(brain._history[-1]['content'], 'Ответ в чате')
        self.assertTrue(brain._history[-3]['interrupted'])
        self.assertIn('прерван', brain._history[-3]['content'])


class LatestDialogue(unittest.TestCase):
    def test_starting_an_older_turn_does_not_consume_a_new_search_button(self):
        loop = answer_loop(['Вымышленный ответ.'])
        loop._search_next = True
        loop._dictation = None
        loop._known_apps = lambda: []
        loop._power_turn = loop._analysis_turn = lambda text: False
        loop._answer = mock.Mock()
        loop._turn_body('Расскажи вымышленную историю', np.zeros(100), time.perf_counter(), 0, True,
                        search_requested=False)
        self.assertTrue(loop._search_next)
        self.assertFalse(loop._answer.call_args.kwargs['search'])

    def test_cancelled_reading_does_not_reopen_a_closed_conversation(self):
        loop = object.__new__(VoiceLoop)
        stop = threading.Event()
        stop.set()
        state = {'at': 0, 'активно': True, 'стоп': stop}
        loop._reading, loop._read_stop = state, stop
        loop._stop = threading.Event()
        loop._emit, loop._open_conversation = mock.Mock(), mock.Mock()
        loop._finish_reading(state, 3)
        loop._open_conversation.assert_not_called()

    def test_short_confirmation_can_be_interrupted_without_muting_the_listener(self):
        loop = answer_loop(['Вымышленный ответ.'])
        loop._listener.mute = mock.Mock()
        loop._listener.listen_while_speaking = mock.Mock()
        with mock.patch.object(config, 'ALLOW_BARGE_IN', True):
            loop._say_back('Вымышленное подтверждение.')
        loop._listener.mute.assert_not_called()
        loop._listener.listen_while_speaking.assert_called_once()

    def test_finishing_an_answer_does_not_reset_a_phrase_being_captured(self):
        loop = answer_loop(['Вымышленный законченный ответ.'])
        loop._listener.unmute = mock.Mock()
        with mock.patch.object(config, 'ALLOW_BARGE_IN', True):
            loop._answer('Вымышленный вопрос')
        loop._listener.unmute.assert_not_called()

    def test_three_inputs_do_not_queue_three_answers(self):
        started, release, newest = (threading.Event() for _ in range(3))
        replies, tokens, errors = [], [], []
        runner = LatestTurnRunner(lambda: None, errors.append)
        def old(cancel):
            tokens.append(cancel)
            started.set()
            release.wait(2)
            if not cancel.is_set():
                replies.append('старый')
        try:
            runner.submit(old)
            self.assertTrue(started.wait(1))
            runner.submit(lambda cancel: replies.append('средний'))
            runner.submit(lambda cancel: (tokens.append(cancel), replies.append('новый'), newest.set()))
            self.assertTrue(tokens[0].is_set())
            release.set()
            self.assertTrue(newest.wait(1))
            self.assertEqual(replies, ['новый'])
            self.assertFalse(tokens[-1].is_set())
            self.assertTrue(tokens[0].is_set(), 'новый ход снял старую отмену')
            self.assertEqual(errors, [])
        finally:
            release.set()
            runner.close()

    def test_recognition_accepts_interruption_while_previous_answer_runs(self):
        loop = object.__new__(VoiceLoop)
        loop._stop, loop._interrupt = threading.Event(), threading.Event()
        loop._turn = loop._first = None
        loop._open, loop._last_turn = True, time.monotonic()
        loop._search_next, loop._dictation = False, None
        loop._speaker = loop._fallback = None
        loop._held_text = ''
        loop._emit = mock.Mock()
        loop._take_held = lambda text: text
        loop._should_answer = lambda *a: True
        loop._first_schedule = lambda: NS(answered=lambda: None)
        loop._stt = NS(recognize=lambda wave, sample_rate: 'Первый вопрос' if wave[0] == 1 else 'Новая реплика')
        started, second_answer = threading.Event(), threading.Event()
        spoken = []
        def body(text, *args, **kwargs):
            token = loop._interrupt
            if text == 'Первый вопрос':
                started.set()
                token.wait(1)
                if not token.is_set():
                    spoken.append('запоздалый ответ')
            else:
                spoken.append('новый ответ')
                second_answer.set()
        loop._turn_body = body
        def phrases():
            yield np.ones(config.SAMPLE_RATE, dtype=np.float32)
            if started.wait(1):
                yield np.full(config.SAMPLE_RATE, 2, dtype=np.float32)
        loop._listener = NS(phrases=phrases, stop=lambda: None)
        with mock.patch('core.voice_loop.unfinished', return_value=False):
            loop._listen()
        self.assertTrue(second_answer.is_set())
        self.assertEqual(spoken, ['новый ответ'])

    def test_hush_clears_a_pending_reply(self):
        entered, release = threading.Event(), threading.Event()
        replies = []
        runner = LatestTurnRunner(lambda: None, lambda exc: self.fail(str(exc)))
        try:
            runner.submit(lambda cancel: (entered.set(), release.wait(2)))
            self.assertTrue(entered.wait(1))
            runner.submit(lambda cancel: replies.append('не должно прозвучать'))
            runner.cancel()
            release.set()
        finally:
            runner.close(drain=True)
        self.assertEqual(replies, [])


class SoundAndVisual(unittest.TestCase):
    def test_cancelled_packet_cannot_restart_phone_audio_after_stop(self):
        from core.phone import PhoneSpeaker
        server = NS(audio_ready=True, attach=lambda callback: None,
                    send_audio=mock.Mock(), send_stop=mock.Mock())
        speaker = PhoneSpeaker(server)
        cancel = threading.Event()
        cancel.set()
        speaker.interrupt()
        speaker.say(np.ones(100), 1000, cancel=cancel)
        server.send_audio.assert_not_called()
        self.assertTrue(speaker.idle.is_set())

    def test_cancelled_packet_cannot_enter_local_playback_queue(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch('core.audio_out.wasapi_extra', return_value=None), \
             mock.patch('core.audio_out.sd.play') as play, \
             mock.patch('core.audio_out.sd.stop'):
            speaker = Speaker()
            try:
                speaker.say(np.ones(100), 1000, cancel=cancel)
                self.assertTrue(speaker._queue.empty())
                self.assertTrue(speaker.idle.is_set())
                play.assert_not_called()
            finally:
                speaker.close()
                speaker._thread.join(1)

    def test_meter_tracks_speech_and_silence_and_resets_on_stop(self):
        now = [10.0]
        meter = PlaybackMeter(clock=lambda: now[0])
        meter.append(np.concatenate([np.full(80, .25), np.zeros(80)]), 1000)
        self.assertAlmostEqual(meter.level, .25)
        now[0] += .10
        self.assertEqual(meter.level, 0)
        meter.append(np.full(100, .4), 1000)
        now[0] += .09
        self.assertAlmostEqual(meter.level, .4)
        meter.reset()
        self.assertEqual(meter.level, 0)

    def test_local_speaker_can_play_a_new_answer_after_interrupt(self):
        playing, stopped, new_play = threading.Event(), threading.Event(), threading.Event()
        played = []
        def play(wave, *a, **kw):
            played.append(float(wave[0]))
            if wave[0] == 1:
                playing.set()
            else:
                new_play.set()
        def wait():
            if len(played) == 1:
                stopped.wait(2)
        with mock.patch('core.audio_out.wasapi_extra', return_value=None), \
             mock.patch('core.audio_out.sd.play', side_effect=play), \
             mock.patch('core.audio_out.sd.wait', side_effect=wait), \
             mock.patch('core.audio_out.sd.stop', side_effect=stopped.set):
            speaker = Speaker()
            try:
                speaker.say(np.ones(100), 1000)
                self.assertTrue(playing.wait(1))
                speaker.interrupt()
                speaker.say(np.full(100, 2), 1000)
                self.assertTrue(new_play.wait(1), 'динамик отбрасывает следующую речь')
                self.assertTrue(speaker.wait(1))
                self.assertEqual(played, [1, 2])
            finally:
                speaker.close()
                speaker._thread.join(1)

    def test_visual_state_exposes_speech_levels_without_echo_changing_the_phase(self):
        rt = object.__new__(WebRuntime)
        rt.voice = NS(running=True, ready=True, in_conversation=True,
                      _speaker_idle=lambda: False, _speech_from=1,
                      _listener=NS(last_level=.125),
                      _speaker=NS(meter=NS(level=.25)))
        state = rt.voice_state()
        self.assertEqual(state['activity'], 'speaking')
        self.assertEqual(state['input_level'], .125)
        self.assertEqual(state['output_level'], .25)
        self.assertNotIn('text', state)
        rt.voice._speaker_idle = lambda: True
        self.assertEqual(rt.voice_state()['activity'], 'hearing')


class VisualApi(unittest.TestCase):
    def test_visual_endpoint_is_local_and_does_not_read_events_or_history(self):
        from fastapi.testclient import TestClient
        from core.phone import PhoneServer
        server = PhoneServer(port=0)
        with mock.patch('uvicorn.Server') as transport:
            transport.return_value.serve = mock.AsyncMock()
            server._serve()
        server.runtime = NS(voice_state=lambda: {'activity': 'speaking', 'output_level': .2},
                            events=mock.Mock(side_effect=AssertionError('личные события не нужны')))
        with TestClient(server._app) as client:
            result = client.get('/api/voice/visual')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['voice']['output_level'], .2)
        with TestClient(server._app, client=('192.168.1.50', 5555)) as remote:
            self.assertEqual(remote.get('/api/voice/visual').status_code, 403)
        server.runtime.events.assert_not_called()


if __name__ == '__main__':
    unittest.main()
