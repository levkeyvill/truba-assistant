"""Звук на телефоне: связь сразу, а касание нужно только звуку.

Страница подключается при загрузке, а `AudioContext` браузер без жеста не
запускает. Значит, «на связи» и «звук готов» — разные вещи, и речь мы шлём
только туда, где второе. Проверяем ровно это: `PhoneServer` помнит, у каких
соединений звук включён, голосовой цикл ждёт именно звука, а карточка
пульта говорит хозяину, что пора коснуться телефона.

Ни сети, ни звука, ни телефона тут нет: `PhoneServer` собирается вручную
(как `WebRuntime` в `test_battery`), вместо колонок — заглушки.
"""

import threading
import time
import unittest
from unittest import mock

import numpy as np

import config
from core.phone import PhoneServer, PhoneSpeaker
from core.voice_loop import VoiceLoop


class _Ws:
    """Соединение вместо WebSocket: нужно только имя, чтобы их различать."""

    def __init__(self, name="телефон"):
        self.name = name

    def __repr__(self):
        return f"<{self.name}>"


def _server():
    """`PhoneServer` без сокета и потока: поднятый сервер тут не нужен."""
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None  # потока сервера тут нет — слать некому и незачем
    return server


def _слушатель(список):
    """Подписка на события сервера — как это делает `PhoneSpeaker`."""
    def слушаю(kind, payload):
        список.append((kind, payload))
    return слушаю


class _Speaker:
    """Колонки вместо настоящих: `_output` их поднимает через этот класс."""

    gapless = True

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.idle = threading.Event()
        self.idle.set()

    def say(self, wave, rate, gap=True):
        self.calls.append(("say", len(wave), gap))

    def pause(self, seconds, rate=24000):
        self.calls.append(("pause", seconds))

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class ServerAudioTests(unittest.TestCase):
    """Сервер помнит, какие соединения могут играть звук."""

    def test_new_connection_is_on_line_but_silent(self):
        server = _server()
        server._clients.add(_Ws())
        self.assertTrue(server.connected)
        self.assertFalse(server.audio_ready)

    def test_audio_ready_marks_the_connection(self):
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        server._on_message(ws, {"type": "audio", "ready": True})
        self.assertTrue(server.audio_ready)
        self.assertIn(ws, server._audio)

    def test_anything_but_true_means_sound_is_off(self):
        # Страница честно сообщает «звука нет» при подключении. `1` и
        # `"true"` — не то же, что `True`, и читаются как выключено.
        server = _server()
        ws = _Ws()
        server._audio.add(ws)
        for значение in (False, 1, "true", None):
            server._on_message(ws, {"type": "audio", "ready": значение})
            self.assertFalse(server.audio_ready, значение)

    def test_one_silent_phone_does_not_spoil_another(self):
        server = _server()
        первый, второй = _Ws("первый"), _Ws("второй")
        server._clients.update((первый, второй))
        server._on_message(второй, {"type": "audio", "ready": True})
        server._on_message(первый, {"type": "audio", "ready": False})
        self.assertTrue(server.audio_ready)
        self.assertIn(второй, server._audio)
        self.assertNotIn(первый, server._audio)

    def test_gone_connection_takes_its_sound_with_it(self):
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        server._on_message(ws, {"type": "audio", "ready": True})
        server._forget(ws)
        self.assertFalse(server.connected)
        self.assertFalse(server.audio_ready)

    def test_events_announce_the_change(self):
        server = _server()
        видел = []
        server.attach(_слушатель(видел))
        ws = _Ws()
        server._on_message(ws, {"type": "audio", "ready": True})
        server._on_message(ws, {"type": "audio", "ready": False})
        self.assertEqual(видел, [("audio_ready", True), ("audio_off", False)])

    def test_other_messages_reach_the_listener_as_before(self):
        # Кнопки, режимы, погода и заряд летят и до касания: `audio` —
        # единственное новое сообщение от страницы.
        server = _server()
        видел = []
        server.attach(_слушатель(видел))
        ws = _Ws()
        server._on_message(ws, {"type": "mode", "value": "name"})
        server._on_message(ws, {"type": "battery", "level": 80})
        self.assertEqual(видел, [
            ("mode", {"type": "mode", "value": "name"}),
            ("battery", {"type": "battery", "level": 80}),
        ])

    def test_broken_message_does_not_kill_the_connection(self):
        server = _server()
        видел = []
        server.attach(_слушатель(видел))
        server._on_message(_Ws(), "не словарь")
        self.assertEqual(видел, [("unknown", "не словарь")])


class AudioOnlyToEarsTests(unittest.TestCase):
    """Речь получают только соединения со звуком.

    Настоящий `send_audio` уходит в цикл событий потока сервера, которого тут
    нет, поэтому подменяем `_broadcast` и смотрим, кто оказался получателем.
    """

    def setUp(self):
        self.server = _server()
        self.глухой = _Ws("без звука")
        self.слышащий = _Ws("со звуком")
        self.server._clients.update((self.глухой, self.слышащий))
        self.server._on_message(self.слышащий, {"type": "audio", "ready": True})

    def _получатели(self, действие):
        """Список получателей: `None` — все подключённые, иначе конкретный."""
        with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
            действие()
        args = шлёт.call_args[0]
        return args[1] if len(args) > 1 else None

    def test_sound_goes_only_where_it_can_be_heard(self):
        # Соединение без звука не отчитается о проигранном, и `PhoneSpeaker`
        # ждал бы его до запасного таймера, оставив микрофон заглушенным.
        цели = self._получатели(
            lambda: self.server.send_audio(np.zeros(240, dtype=np.float32), 24000))
        self.assertEqual(цели, {self.слышащий})

    def test_everything_else_still_goes_to_everyone(self):
        # Кнопки, режимы, погода, заряд — всё это идёт всем подключённым,
        # включая того, кто звук ещё не включил. Остановка тоже: у молчащего
        # в очереди могли остаться куски, иначе они зазвучат после касания.
        for действие in (
            self.server.send_stop,
            lambda: self.server.send_state("listening"),
            lambda: self.server.send_line("bot", "Тут я."),
            lambda: self.server.send_system({"cpu": 1}),
            lambda: self.server.send_mode("name"),
            lambda: self.server.send_apps([]),
            lambda: self.server.send_weather({"now": {}}),
            lambda: self.server.send_sound("open"),
        ):
            self.assertIsNone(self._получатели(действие), действие)



class PhoneSpeakerAccountingTests(unittest.TestCase):
    """Отчёт `played` и счёт кусков: без звука их быть не должно."""

    class _Server:
        def __init__(self, audio_ready):
            self.audio_ready = audio_ready
            self.connected = True
            self.sent = 0

        def attach(self, listener):
            pass

        def detach(self, listener):
            pass

        def send_audio(self, wave, rate):
            self.sent += 1

        def send_stop(self):
            pass

    def _speaker(self, audio_ready):
        server = self._Server(audio_ready)
        return server, PhoneSpeaker(server, gap=0.1)

    def test_silent_connection_does_not_wait_for_a_report(self):
        # Считать куски у соединения без звука незачем: отчёта от него не
        # будет, и микрофон остался бы заглушенным до запасного таймера.
        server, speaker = self._speaker(False)
        for _ in range(3):
            speaker.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
        speaker.pause(0.1)
        self.assertEqual(server.sent, 0)
        self.assertTrue(speaker.idle.is_set())

        began = time.monotonic()
        self.assertTrue(speaker.wait(timeout=5.0))
        self.assertLess(time.monotonic() - began, 1.0)

    def test_sound_on_the_phone_still_counts_every_part(self):
        server, speaker = self._speaker(True)
        for _ in range(3):
            speaker.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
        speaker.pause(0.1)
        self.assertEqual(server.sent, 4)
        self.assertFalse(speaker.idle.is_set())
        for _ in range(4):
            speaker.on_played()
        self.assertTrue(speaker.idle.is_set())

    def test_turned_off_sound_mid_sentence_releases_the_microphone(self):
        # Телефон коснулся экрана, пошла речь, потом звук выключили. Ждать
        # отчёта больше не от кого — ждём только мы.
        server, speaker = self._speaker(True)
        speaker.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
        self.assertFalse(speaker.idle.is_set())
        server.audio_ready = False
        speaker._on_server_event("audio_off", False)
        self.assertTrue(speaker.idle.is_set())

    def test_gone_phone_releases_the_microphone(self):
        server, speaker = self._speaker(True)
        speaker.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
        server.audio_ready = False
        speaker._on_server_event("disconnected", None)
        self.assertTrue(speaker.idle.is_set())



class OutputChoiceTests(unittest.TestCase):
    """`_output` смотрит на звук, а не просто на соединение."""

    def setUp(self):
        self._saved = (config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP)
        config.OUTPUT = "phone"
        config.TTS_GAP = 0.2
        import core.audio_out as audio_out

        # Запасной динамик подменяем: настоящий открыл бы звуковое устройство.
        patcher = mock.patch.object(audio_out, "Speaker", _Speaker)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP = self._saved

    def _loop(self, server):
        loop = object.__new__(VoiceLoop)
        loop._server = server
        loop._speaker = _Speaker()
        loop._fallback = None
        loop._stop = threading.Event()
        loop._interrupt = threading.Event()
        loop.events = []
        loop._emit = lambda kind, payload: loop.events.append((kind, payload))
        return loop

    def test_connected_without_sound_goes_to_speakers(self):
        # Подключение без разрешённого звука направляет речь в колонки.
        config.PHONE_GRACE = 0.2
        server = _server()
        server._clients.add(_Ws())
        loop = self._loop(server)

        out = loop._output()

        self.assertIsNot(out, loop._speaker)
        self.assertIn("no_phone", [kind for kind, _ in loop.events])

    def test_sound_on_the_phone_is_used_right_away(self):
        config.PHONE_GRACE = 5.0
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        server._on_message(ws, {"type": "audio", "ready": True})
        loop = self._loop(server)

        self.assertIs(loop._output(), loop._speaker)
        # Без ожидания и без жалобы: телефон на связи и звук в нём есть.
        self.assertEqual(loop.events, [])

    def test_gone_sound_sends_the_answer_back_to_speakers(self):
        config.PHONE_GRACE = 0.2
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        server._on_message(ws, {"type": "audio", "ready": True})
        server._forget(ws)
        loop = self._loop(server)

        out = loop._output()

        self.assertIsNot(out, loop._speaker)
        self.assertIn("no_phone", [kind for kind, _ in loop.events])

    def test_wait_is_for_the_sound_not_for_the_connection(self):
        # Связь есть с самого начала, звук появляется через 0.2 с — как после
        # касания. Ждать надо и дождаться.
        config.PHONE_GRACE = 5.0
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        loop = self._loop(server)
        timer = threading.Timer(0.2, lambda: server._on_message(
            ws, {"type": "audio", "ready": True}))
        timer.start()
        self.addCleanup(timer.cancel)

        began = time.monotonic()
        out = loop._output()
        waited = time.monotonic() - began

        self.assertIs(out, loop._speaker)
        self.assertIn("phone_wait", [kind for kind, _ in loop.events])
        self.assertNotIn("no_phone", [kind for kind, _ in loop.events])
        self.assertGreaterEqual(waited, 0.1)

    def test_phone_that_never_spoke_falls_back_after_the_grace(self):
        # Телефон подключился, но палец так и не коснулся экрана, потом он
        # ушёл. Ждём ровно PHONE_GRACE и говорим в колонки.
        config.PHONE_GRACE = 0.3
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        loop = self._loop(server)
        timer = threading.Timer(0.1, lambda: server._forget(ws))
        timer.start()
        self.addCleanup(timer.cancel)

        began = time.monotonic()
        out = loop._output()
        waited = time.monotonic() - began

        self.assertIsNot(out, loop._speaker)
        self.assertGreaterEqual(waited, 0.2)
        self.assertLess(waited, 3.0)

    def test_speakers_output_never_waits_for_a_phone(self):
        config.OUTPUT = "speakers"
        config.PHONE_GRACE = 30.0
        server = _server()
        server._clients.add(_Ws())
        loop = self._loop(server)

        began = time.monotonic()
        out = loop._output()

        self.assertIs(out, loop._speaker)
        self.assertLess(time.monotonic() - began, 0.1)

    def test_voice_off_does_not_raise_fallback_speaker(self):
        config.PHONE_GRACE = 30.0
        server = _server()
        server._clients.add(_Ws())
        loop = self._loop(server)
        loop._stop.set()

        out = loop._output()

        self.assertIs(out, loop._speaker)
        self.assertIsNone(loop._fallback)
        self.assertNotIn("no_phone", [k for k, _ in loop.events])


class GreetingTests(unittest.TestCase):
    """Приветствие при старте идёт туда же, куда и вся речь.

    «Я на связи» произносится тем же `_output`, что и вся речь, поэтому
    телефон без звука его и не услышит — услышит комнату.
    """

    def setUp(self):
        self._saved = (config.OUTPUT, config.PHONE_GRACE)
        config.OUTPUT = "phone"
        config.PHONE_GRACE = 0.0
        import core.audio_out as audio_out

        # Запасной динамик подменяем: настоящий открыл бы звуковое устройство.
        patcher = mock.patch.object(audio_out, "Speaker", _Speaker)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        config.OUTPUT, config.PHONE_GRACE = self._saved

    def _loop(self, server):
        loop = object.__new__(VoiceLoop)
        loop._server = server
        loop._speaker = _Speaker()
        loop._fallback = None
        loop._listener = mock.Mock()
        loop._ducker = mock.Mock()
        loop._stop = threading.Event()
        loop._interrupt = threading.Event()
        loop._speaking_text = ""
        loop._spoke_at = 0.0
        loop._open = False
        loop._say_plainly = lambda sentence, speaker: 0
        loop.events = []
        loop._emit = lambda kind, payload: loop.events.append((kind, payload))
        return loop

    def test_greeting_without_phone_sound_goes_to_speakers(self):
        server = _server()
        server._clients.add(_Ws())
        loop = self._loop(server)

        loop._say_back("Я на связи.")

        self.assertEqual(loop._speaker.calls, [])
        self.assertIsNotNone(loop._fallback)

    def test_greeting_with_phone_sound_goes_to_the_phone(self):
        server = _server()
        ws = _Ws()
        server._clients.add(ws)
        server._on_message(ws, {"type": "audio", "ready": True})
        loop = self._loop(server)

        loop._say_back("Я на связи.")

        self.assertIsNone(loop._fallback)
        self.assertIsNotNone(loop._speaker)



if __name__ == "__main__":
    unittest.main()
