"""Потоковая озвучка и учёт кусков на телефоне — без видеокарты, сети и звука."""

import threading
import time
import unittest
from unittest import mock

import numpy as np
import torch

import config
from core.higgs_voice import BOC_ID, EOC_ID, _FastBody, _pack, _Sampler, apply_delay, remove_delay
from core.phone import PhoneSpeaker
from core.voice_loop import (BYE_WORDS, CLOUD_DOWN_WORDS, VoiceLoop, goodbye_words,
                              is_dismissal)


class _Quiet:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Speaker:
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


class _StreamingVoice:
    def __init__(self, parts=3):
        self.parts = parts
        self.closed = 0

    def stream(self, text, ref=None, speed=1.0):
        try:
            for _ in range(self.parts):
                yield np.zeros(480, dtype=np.float32), 24000
        finally:
            self.closed += 1

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        raise AssertionError("поток должен был пойти мимо say()")


class _Brain:
    def __init__(self, sentences):
        self.sentences = sentences

    ended = False

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        self.voice = voice
        self.aloud = aloud
        self.can_end = can_end
        yield from self.sentences


def _loop(voice, speaker, sentences):
    loop = object.__new__(VoiceLoop)
    loop._brain = _Brain(sentences)
    loop._voice = voice
    loop._ref = None
    loop._speaker = speaker
    loop._fallback = None
    loop._server = None
    loop._listener = _Quiet()
    loop._ducker = _Quiet()
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._open = False
    loop._last_turn = 0.0
    loop._speaking_text = ""
    loop._spoke_at = 0.0
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    return loop


class StreamTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.OUTPUT, config.TTS_GAP)
        config.OUTPUT = "speakers"
        config.TTS_GAP = 0.2

    def tearDown(self):
        config.OUTPUT, config.TTS_GAP = self._saved

    def test_sentence_goes_in_parts_without_gaps_and_pause_after(self):
        speaker = _Speaker()
        voice = _StreamingVoice(parts=3)
        loop = _loop(voice, speaker, ["Первое предложение.", "Второе."])
        loop._answer("вопрос")
        says = [c for c in speaker.calls if c[0] == "say"]
        self.assertEqual(len(says), 6)
        self.assertTrue(all(gap is False for _, _, gap in says))
        self.assertEqual([c for c in speaker.calls if c[0] == "pause"],
                         [("pause", 0.2), ("pause", 0.2)])
        # Предложение попадает в ленту один раз, а не на каждый кусок.
        self.assertEqual([p for k, p in loop.events if k == "sentence"],
                         ["Первое предложение.", "Второе."])
        self.assertEqual(voice.closed, 2)

    def test_interrupt_mid_sentence_closes_stream(self):
        speaker = _Speaker()
        voice = _StreamingVoice(parts=5)
        loop = _loop(voice, speaker, ["Длинное предложение.", "Не должно прозвучать."])
        original = speaker.say

        def say_then_interrupt(wave, rate, gap=True):
            original(wave, rate, gap)
            loop._interrupt.set()

        speaker.say = say_then_interrupt
        loop._answer("вопрос")
        self.assertEqual(len([c for c in speaker.calls if c[0] == "say"]), 1)
        self.assertEqual(voice.closed, 1)

    def test_model_can_end_conversation(self):
        speaker = _Speaker()
        loop = _loop(_StreamingVoice(parts=1), speaker, ["Ладно, молчу."])
        loop._brain.ended = True
        loop._open = True
        loop._last_turn = 1.0
        loop._digest_later = lambda: None
        loop._answer("мы же с тобой уже поговорили", can_end=True)
        self.assertTrue(loop._brain.aloud)
        self.assertTrue(loop._brain.can_end)
        self.assertFalse(loop._open)
        kinds = [k for k, _ in loop.events]
        self.assertIn("ended_by_model", kinds)
        # Разговор закрыт, и только потом в журнал идут замер и итог ответа —
        # иначе в «Голосе» строка о закрытии потерялась бы за ними.
        self.assertEqual(loop.events[-3][0:2], ("conversation", False))

    def test_says_why_when_model_fails(self):
        speaker = _Speaker()

        class Broken(_Brain):
            def reply(self, *args, **kwargs):
                raise RuntimeError("Error code: 403 - unsupported_country_region_territory")
                yield

        loop = _loop(_StreamingVoice(parts=2), speaker, [])
        loop._brain = Broken([])
        loop._answer("проверка звука")
        said = [p for k, p in loop.events if k == "sentence"]
        self.assertEqual(said, ["Облако меня не пускает из этой страны — нужен VPN или другой сервис."])
        self.assertEqual(len([c for c in speaker.calls if c[0] == "say"]), 2)
        self.assertEqual(loop.events[-1][1]["sentences"], 1)

    def test_filler_alone_is_not_an_answer(self):
        # «Секунду, гляну», а потом облако отвалилось — молчать нельзя.
        speaker = _Speaker()

        class Broken(_Brain):
            def reply(self, *args, **kwargs):
                yield "Секунду, гляну."
                raise RuntimeError("APITimeoutError: Request timed out.")

        loop = _loop(_StreamingVoice(parts=1), speaker, [])
        loop._brain = Broken([])
        loop._answer("какой курс?")
        said = [p for k, p in loop.events if k == "sentence"]
        # Про обрыв связи теперь говорят один раз и впрямую: повторять
        # «не могу достучаться» на каждую фразу она больше не станет.
        self.assertEqual(said, ["Секунду, гляну.", CLOUD_DOWN_WORDS])

    def test_silent_model_end_still_says_goodbye(self):
        # 26 сентября: «говорю "спасибо большое, удачи тебе" — а она молчит».
        speaker = _Speaker()
        loop = _loop(_StreamingVoice(parts=1), speaker, [])
        loop._brain.ended = True
        loop._open = True
        loop._last_turn = 1.0
        loop._digest_later = lambda: None
        loop._answer("ну всё, давай, удачи тебе", can_end=True)
        said = [p for k, p in loop.events if k == "sentence"]
        self.assertEqual(len(said), 1)
        self.assertIn(said[0], BYE_WORDS["luck"])
        self.assertFalse(loop._open)

    def test_dismissal_is_answered_then_closed(self):
        speaker = _Speaker()
        loop = _loop(_StreamingVoice(parts=1), speaker, [])
        loop._trust_next = False
        loop._open = True
        loop._last_turn = time.monotonic()
        loop._spoke_at = -1000.0
        loop._digest_later = lambda: None
        old = config.LISTEN_MODE
        try:
            config.LISTEN_MODE = "name"
            self.assertFalse(loop._should_answer("Спасибо большое, удачи тебе"))
        finally:
            config.LISTEN_MODE = old
        said = [p for k, p in loop.events if k == "sentence"]
        self.assertEqual(len(said), 1)
        self.assertIn(said[0], BYE_WORDS["luck"])
        self.assertFalse(loop._open)

    def test_goodbye_matches_how_he_said_it(self):
        self.assertTrue(is_dismissal("Спасибо большое, удачи тебе"))
        self.assertTrue(is_dismissal("Всего хорошего"))
        self.assertIn(goodbye_words("спасибо"), BYE_WORDS["thanks"])
        self.assertIn(goodbye_words("Всё, пока"), BYE_WORDS["bye"])
        self.assertIn(goodbye_words("Хватит, отдыхай"), BYE_WORDS["quiet"])
        self.assertIn(goodbye_words("Хорошего вечера"), BYE_WORDS["wish"])

    def test_not_gapless_output_uses_whole_sentence(self):
        speaker = _Speaker()
        speaker.gapless = False

        class Whole:
            def say(self, text, ref=None, nfe_step=None, speed=1.0):
                return np.zeros(4800, dtype=np.float32), 24000

            def stream(self, *args, **kwargs):
                raise AssertionError("в колонки поток не идёт")

        loop = _loop(Whole(), speaker, ["Одно предложение целиком."])
        loop._answer("вопрос")
        self.assertEqual(speaker.calls, [("say", 4800, True)])


class _Server:
    def __init__(self):
        self.sent = 0
        self.handlers = []
        self.connected = True
        self.audio_ready = True

    def attach(self, handler):
        self.handlers.append(handler)

    def detach(self, handler):
        self.handlers.remove(handler)

    def send_audio(self, wave, rate):
        self.sent += 1

    def send_stop(self):
        pass


class PhoneAccountingTests(unittest.TestCase):
    def test_idle_only_after_every_part_reported(self):
        server = _Server()
        speaker = PhoneSpeaker(server, gap=0.1)
        for _ in range(3):
            speaker.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
        speaker.pause(0.1)
        self.assertFalse(speaker.idle.is_set())
        for _ in range(3):
            speaker.on_played()
        self.assertFalse(speaker.idle.is_set())
        speaker.on_played()
        self.assertTrue(speaker.idle.is_set())

    def test_gap_is_added_to_whole_sentence(self):
        server = _Server()
        sent = []
        server.send_audio = lambda wave, rate: sent.append(len(wave))
        speaker = PhoneSpeaker(server, gap=0.5)
        speaker.say(np.zeros(1000, dtype=np.float32), 1000)
        speaker.say(np.zeros(1000, dtype=np.float32), 1000, gap=False)
        self.assertEqual(sent, [1500, 1000])


class _Link:
    """Сервер телефона, который подключается по таймеру.

    `after=None` — телефон не возвращается вовсе, `0` — он уже на связи.
    `audio` — включится ли на нём звук (как после касания экрана).
    """

    def __init__(self, after: float | None = 0.0, audio: bool = True):
        self.connected = False
        self.audio_ready = False
        self._after = after
        self._audio = audio

    def start_link(self):
        if self._after is None:
            return
        if self._after <= 0:
            self.connected = True
            self.audio_ready = self._audio
            return
        threading.Timer(self._after, self._back).start()

    def _back(self):
        self.connected = True
        self.audio_ready = self._audio

    def send_state(self, state, text=""):
        pass

    def send_line(self, who, text):
        pass

    def send_sound(self, sound):
        pass


class PhoneWaitTests(unittest.TestCase):
    """Пропал телефон на пару секунд — ждём его, а не говорим в колонки."""

    def setUp(self):
        self._saved = (config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP)
        config.OUTPUT = "phone"
        config.TTS_GAP = 0.2
        # Запасной динамик подменяем: настоящий открыл бы звуковое
        # устройство, а тест об этом ничего не знает.
        import core.audio_out as audio_out

        self._patcher = mock.patch.object(audio_out, "Speaker", _Speaker)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def tearDown(self):
        config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP = self._saved

    def _loop(self, link):
        loop = object.__new__(VoiceLoop)
        loop._server = link
        loop._speaker = _Speaker()
        loop._fallback = None
        loop._stop = threading.Event()
        loop._interrupt = threading.Event()
        loop.events = []
        loop._emit = lambda kind, payload: loop.events.append((kind, payload))
        return loop

    def test_connected_phone_is_silent(self):
        # Без этой проверки каждый ответ писал в журнал «телефон пропал».
        config.PHONE_GRACE = 5.0
        link = _Link(after=0.0)
        link.start_link()
        loop = self._loop(link)

        self.assertIs(loop._output(time.monotonic() + 5.0), loop._speaker)
        self.assertIs(loop._output(), loop._speaker)
        self.assertEqual(loop.events, [])

    def test_phone_returns_during_wait_and_answers_there(self):
        config.PHONE_GRACE = 5.0
        # Телефон возвращается через 0.3 с — как после засыпания страницы.
        link = _Link(after=0.3)
        loop = self._loop(link)
        link.start_link()

        out = loop._output()

        self.assertIs(out, loop._speaker)
        self.assertIn("phone_wait", [kind for kind, _ in loop.events])
        # В колонки не пошли, значит и жаловаться не на что.
        self.assertNotIn("no_phone", [kind for kind, _ in loop.events])

    def test_phone_stays_gone_falls_back_to_speakers(self):
        config.PHONE_GRACE = 0.2
        loop = self._loop(_Link())

        began = time.monotonic()
        out = loop._output()
        waited = time.monotonic() - began

        self.assertIsNot(out, loop._speaker)
        self.assertIn("no_phone", [kind for kind, _ in loop.events])
        self.assertGreaterEqual(waited, 0.15)  # а всё-таки подождали
        self.assertLess(waited, 3.0)

    def test_zero_grace_does_not_wait_at_all(self):
        config.PHONE_GRACE = 0.0
        loop = self._loop(_Link())

        began = time.monotonic()
        loop._output()

        self.assertLess(time.monotonic() - began, 0.1)
        self.assertNotIn("phone_wait", [kind for kind, _ in loop.events])

    def test_speakers_output_never_waits(self):
        config.OUTPUT = "speakers"
        config.PHONE_GRACE = 30.0
        loop = self._loop(_Link())

        began = time.monotonic()
        out = loop._output()

        self.assertIs(out, loop._speaker)
        self.assertLess(time.monotonic() - began, 0.1)

    def test_voice_off_does_not_raise_fallback_speaker(self):
        # Ждать сорвалось: поднимать колонки и ругаться «телефон не на связи»
        # незачем, говорить всё равно некому.
        config.PHONE_GRACE = 30.0
        loop = self._loop(_Link())
        loop._stop.set()

        began = time.monotonic()
        out = loop._output()

        self.assertLess(time.monotonic() - began, 1.0)
        self.assertIs(out, loop._speaker)
        self.assertIsNone(loop._fallback)
        self.assertNotIn("no_phone", [k for k, _ in loop.events])


class _Watched(_Speaker):
    """Динамик, который запоминает, когда прозвучал первый кусок."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_at = None

    def say(self, wave, rate, gap=True):
        if self.first_at is None:
            self.first_at = time.monotonic()
        super().say(wave, rate, gap)


class _ThinkingBrain(_Brain):
    """Модель думает перед первым предложением — как облако в жизни."""

    def __init__(self, sentences, think):
        super().__init__(sentences)
        self.think = think

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        self.voice = voice
        self.aloud = aloud
        self.can_end = can_end
        time.sleep(self.think)
        yield from self.sentences


class PhoneWaitWhileThinkingTests(unittest.TestCase):
    """Пока модель думает, телефон возвращается — и ждать уже не нужно."""

    def setUp(self):
        self._saved = (config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP)
        config.OUTPUT = "phone"
        config.TTS_GAP = 0.2
        # Запасной динамик подменяем: настоящий открыл бы звуковое
        # устройство, а тест об этом ничего не знает.
        import core.audio_out as audio_out

        self._patcher = mock.patch.object(audio_out, "Speaker", _Watched)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def tearDown(self):
        config.OUTPUT, config.PHONE_GRACE, config.TTS_GAP = self._saved

    def _loop(self, link, sentences=("Держи трубу.",), think=0.0):
        speaker = _Watched()
        loop = _loop(_StreamingVoice(parts=1), speaker, list(sentences))
        loop._server = link
        loop._brain = _ThinkingBrain(list(sentences), think)
        link.start_link()
        return loop, speaker

    def test_phone_back_while_thinking_does_not_add_waiting(self):
        # Модель думает 0.3 с, телефон возвращается через 0.2 с: к началу
        # ответа он уже на связи, ждать нечего.
        config.PHONE_GRACE = 4.0
        loop, speaker = self._loop(_Link(after=0.2), think=0.3)

        began = time.monotonic()
        loop._answer("что нового?")

        self.assertTrue(speaker.calls)  # ответ ушёл в телефон
        self.assertIsNone(loop._fallback)
        self.assertNotIn("no_phone", [k for k, _ in loop.events])
        self.assertLess(speaker.first_at - began, 0.6)

    def test_grace_is_counted_from_the_start_of_the_answer(self):
        # Модель думает 0.3 с, срок ожидания 0.5: ждать остаётся 0.2 с,
        # а не полные 0.5 после мышления.
        config.PHONE_GRACE = 0.5
        loop, speaker = self._loop(_Link(after=None), think=0.3)

        began = time.monotonic()
        loop._answer("что нового?")
        waited = loop._fallback.first_at - began

        self.assertEqual(speaker.calls, [])  # в телефон говорить некому
        self.assertIn("no_phone", [k for k, _ in loop.events])
        self.assertGreaterEqual(waited, 0.4)
        self.assertLess(waited, 0.65)

    def test_nothing_to_say_means_no_waiting(self):
        # Модель не выдала ни одного предложения: динамик не выбирали,
        # телефон и не ждали.
        config.PHONE_GRACE = 4.0
        loop, speaker = self._loop(_Link(), sentences=[], think=0.3)

        loop._answer("что нового?")

        self.assertEqual(speaker.calls, [])
        self.assertIsNone(loop._fallback)
        self.assertNotIn("phone_wait", [k for k, _ in loop.events])
        self.assertNotIn("no_phone", [k for k, _ in loop.events])


class HiggsPieces(unittest.TestCase):
    def test_delay_round_trip(self):
        codes = torch.randint(0, 1024, (30, 8))
        delayed = apply_delay(codes)
        self.assertEqual(tuple(delayed.shape), (37, 8))
        self.assertTrue(torch.equal(remove_delay(delayed), codes))

    def test_sampler_delay_and_end(self):
        sampler = _Sampler(8)
        logits = torch.zeros(8, 1026)
        logits[:, 5] = 10.0
        first = sampler.step(logits.clone(), temperature=0.0, top_k=None)
        self.assertEqual(int(first[0]), 5)
        self.assertTrue(bool((first[1:] == BOC_ID).all()))
        for _ in range(7):
            sampler.step(logits.clone(), temperature=0.0, top_k=None)
        logits[0, EOC_ID] = 20.0
        sampler.step(logits.clone(), temperature=0.0, top_k=None)
        self.assertFalse(sampler.done)
        after_eoc = 0
        while not sampler.done:
            sampler.step(logits.clone(), temperature=0.0, top_k=None)
            after_eoc += 1
        # После EOC в нулевой книге остальным нужно ещё N-2 шага.
        self.assertEqual(after_eoc, 6)

    def _fake_voice(self, frames):
        import core.higgs_voice as hv

        voice = object.__new__(hv.HiggsVoice)
        voice._lock = threading.RLock()
        voice._books = 8
        voice._spoken = lambda text, speed: text
        voice._rows = lambda text, ref: ((torch.zeros(8, dtype=torch.long), False)
                                         for _ in range(frames + 7))
        voice._window = lambda rows, a, b, c: np.zeros((b - a) * 960, dtype=np.float32)
        return voice

    def test_gentle_stream_keeps_pace_with_sound(self):
        # 26 сентября: пока она отвечает, видеокарта на 100% посреди игры.
        # Генерация здесь «мгновенная», значит всё время уходит в сон:
        # звук 16 с, запас STREAM_LEAD — синтез идёт со скоростью звука.
        import core.higgs_voice as hv
        from unittest import mock

        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        with mock.patch.object(hv, "_clock", lambda: now[0]), \
                mock.patch.object(hv, "_sleep", sleep):
            parts = self._fake_voice(400).stream("текст", gentle=True)
            next(parts)
            self.assertEqual(now[0], 0.0)  # первый кусок — без задержки
            list(parts)
        self.assertAlmostEqual(now[0], 400 / 25 - hv.STREAM_LEAD, delta=1.0)
        now[0] = 0.0
        with mock.patch.object(hv, "_clock", lambda: now[0]), \
                mock.patch.object(hv, "_sleep", sleep):
            list(self._fake_voice(400).stream("текст", gentle=False))
        self.assertEqual(now[0], 0.0)

    def test_gentle_lead_counts_played_sound_not_generated_frames(self):
        # Секунда сгенерированного звука звучит 1/speed секунды. Если считать
        # запас по кадрам модели, при speed 1.2 тормоз ждал бы звука дольше,
        # чем он идёт, и куски начали бы отставать: на телефоне — дыры.
        import core.higgs_voice as hv
        from unittest import mock

        for speed in (1.0, 1.2, 1.5, 0.8):
            with self.subTest(speed=speed):
                now = [0.0]
                parts = self._fake_voice(400).stream("текст", speed=speed,
                                                     gentle=True)
                with mock.patch.object(hv, "_clock", lambda: now[0]), \
                        mock.patch.object(hv, "_sleep",
                                          lambda s: now.__setitem__(0, now[0] + s)):
                    chunks = list(parts)
                spoken = sum(len(wave) for wave, _ in chunks) / 24000
                # Столько секунд речи синтез обязан успеть сделать.
                self.assertAlmostEqual(spoken, 400 / 25 / speed, delta=0.1)
                # И не позже, чем речь реально доиграет, иначе динамик замолчит.
                self.assertLess(now[0], spoken)

    @unittest.skipUnless(torch.cuda.is_available() and torch.cuda.get_device_capability() >= (8, 9),
                         "нужна видеокарта с FP8")
    def test_fp8_linear_close_to_bf16(self):
        # Хребет Higgs в FP8: вдвое меньше видеопамяти. Ошибка умножения
        # должна остаться на уровне шума, иначе поплывёт голос.
        fast = object.__new__(_FastBody)
        fast.unit = torch.ones((), device="cuda")
        g = torch.Generator().manual_seed(0)
        w = torch.randn(256, 512, generator=g).to(torch.bfloat16)
        x = torch.randn(1, 37, 512, generator=g).to("cuda", torch.bfloat16)
        packed = _pack(w, "cuda", fp8=True)
        self.assertEqual(packed[0].dtype, torch.float8_e4m3fn)
        got = fast._lin(x, packed).float()
        want = torch.nn.functional.linear(x, w.cuda()).float()
        self.assertEqual(got.shape, want.shape)
        self.assertLess(((got - want).norm() / want.norm()).item(), 0.05)


if __name__ == "__main__":
    unittest.main()
