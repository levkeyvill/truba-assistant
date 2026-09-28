"""Защита от чужих голосов в Discord и от повторов «облако недоступно».

Ни звука, ни COM, ни сети: замер громкости и мозг подменяются. Разговор по
сети 26 сентября: хозяин говорил с друзьями, а она отвечала и на фразы
друзей; и в тот же день VPN отвалился, и она двадцать секунд подряд
повторяла, что не может достучаться до облака.
"""

import threading
import time
import unittest
from unittest import mock

import numpy as np

import config
from core.ducking import VoiceAppMeter
from core.voice_loop import CLOUD_DOWN_WORDS, VoiceLoop


# --- Замер громкости ------------------------------------------------------


class RingBufferTests(unittest.TestCase):
    """Кольцевой буфер: доля в промежутке, вытеснение старого, пустота."""

    def setUp(self):
        # Окно в две секунды: проверяем вытеснение, не отращивая 300 замеров.
        self.meter = VoiceAppMeter(apps=("Discord.exe",), interval=0.1, window=2.0)

    def test_share_counts_loud_samples_in_the_window(self):
        for step in range(10):
            # 6 из 10 замеров громкие — 0.6, выше порога 0.25.
            self.meter.add(100.0 + step * 0.1, 0.5 if step < 6 else 0.0, "Discord.exe")
        self.assertAlmostEqual(self.meter.share_during(100.0, 101.0, 0.02), 0.6)
        # Замеры вне промежутка в счёт не идут: берём только громкий кусок.
        self.assertAlmostEqual(self.meter.share_during(100.0, 100.5, 0.02), 1.0)
        # …и только тихий: там, где Discord молчал.
        self.assertAlmostEqual(self.meter.share_during(100.6, 100.9, 0.02), 0.0)

    def test_share_is_zero_when_nobody_was_audible(self):
        for step in range(10):
            self.meter.add(100.0 + step * 0.1, 0.001, "Discord.exe")
        self.assertEqual(self.meter.share_during(100.0, 101.0, 0.02), 0.0)

    def test_empty_buffer_gives_zero(self):
        self.assertEqual(self.meter.share_during(100.0, 101.0, 0.02), 0.0)
        self.assertEqual(self.meter.loudest_app(100.0, 101.0, 0.02), "")

    def test_old_samples_are_pushed_out(self):
        for step in range(40):
            self.meter.add(step * 0.1, 0.5, "Discord.exe")
        # Буфер держит только окно: десять секунд назад из него уже вытеснено,
        # и промежуток в той далёкой части пуст.
        self.assertGreaterEqual(self.meter._rows()[0][0], 1.9)
        self.assertEqual(self.meter.share_during(0.0, 1.0, 0.02), 0.0)
        self.assertAlmostEqual(self.meter.share_during(3.9, 4.0, 0.02), 1.0)

    def test_broken_window_gives_zero(self):
        self.meter.add(100.0, 0.5, "Discord.exe")
        self.assertEqual(self.meter.share_during(101.0, 100.0, 0.02), 0.0)

    def test_loudest_app_name_without_exe(self):
        self.meter.add(100.0, 0.5, "discord.exe")
        self.meter.add(100.1, 0.9, "Telegram.exe")
        self.assertEqual(self.meter.loudest_app(100.0, 100.2, 0.02), "Telegram")

    def test_app_names_are_compared_without_case(self):
        meter = VoiceAppMeter(apps=("Discord.exe", "Telegram.exe"), window=2.0)
        self.assertEqual(meter.apps, ("discord.exe", "telegram.exe"))

    def test_peak_reads_only_voice_apps(self):
        # Настоящий COM не трогаем: подменяем список сессий.
        meter = VoiceAppMeter(apps=("Discord.exe",), window=2.0)
        meter._available = True

        class _Process:
            def __init__(self, pid, name):
                self.pid = pid
                self.name = name

        class _Meter:
            def __init__(self, value):
                self.value = value

            def GetPeakValue(self):
                return self.value

        class _Ctl:
            def __init__(self, value):
                self.value = value

            def QueryInterface(self, iface):
                return _Meter(self.value)

        class _Session:
            def __init__(self, process, value):
                self.Process = process
                self._ctl = _Ctl(value)

        sessions = [
            _Session(_Process(1, "Spotify.exe"), 0.9),   # не голосовая
            _Session(_Process(2, "DISCORD.EXE"), 0.4),   # наша, регистр не важен
            _Session(_Process(meter._pid, "Discord.exe"), 0.99),  # себя не считаем
            _Session(None, 0.5),
        ]
        with mock.patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=sessions), \
                mock.patch("pycaw.pycaw.IAudioMeterInformation", create=True):
            self.assertEqual(meter._session_peak(), (0.4, "discord.exe"))

    def test_without_pycaw_meter_reads_nothing(self):
        meter = VoiceAppMeter(apps=("Discord.exe",), window=2.0)
        meter._available = False
        self.assertEqual(meter._session_peak(), (0.0, ""))
        self.assertFalse(meter.available)
        # Поток при этом не поднимается, а доля остаётся нулевой.
        meter.start()
        self.assertFalse(meter.running)
        self.assertEqual(meter.share_during(0.0, 10.0, 0.02), 0.0)

    def test_stop_joins_the_thread(self):
        meter = VoiceAppMeter(apps=("Discord.exe",), interval=0.01, window=1.0)
        meter._available = True
        meter._session_peak = lambda: (0.5, "discord.exe")
        meter.start()
        deadline = time.time() + 2.0
        while not meter._rows() and time.time() < deadline:
            time.sleep(0.01)
        meter.stop()
        self.assertFalse(meter.running)
        self.assertTrue(meter._rows())


# --- Правило в _should_answer ---------------------------------------------


class _Quiet:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Phone:
    """Сервер телефона: только то, к чему ходит разбор фразы."""

    def __init__(self):
        self.states = []
        self.sounds = []
        self.connected = True

    def send_state(self, state, text=""):
        self.states.append(state)

    def send_line(self, who, text):
        pass

    def send_sound(self, sound):
        self.sounds.append(sound)

    def send_mode(self, mode):
        pass


def _loop(meter=None, open_conversation=True):
    """Голосовой цикл вручную: без микрофона, динамика и мозга."""
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._trust_next = False
    loop._open = open_conversation
    loop._last_turn = time.monotonic() if open_conversation else 0.0
    loop._search_next = False
    loop._spoke_at = -1000.0
    loop._speaking_text = ""
    loop._brain = None
    loop._server = _Phone()
    loop._speaker = None
    loop._fallback = None
    loop._listener = _Quiet()
    loop._ducker = _Quiet()
    loop._voice_meter = meter
    loop._voice_score = None
    # Отпечатка голоса нет: подбирать его в тесте незачем, а `_is_owner`
    # обязан найти поле — иначе упал бы на первом же разборе фразы.
    loop._voiceprint = _Quiet()
    loop._digest_later = lambda: None
    return loop


def _phrase(seconds=1.0):
    return np.zeros(int(config.SAMPLE_RATE * seconds), dtype=np.float32)


def _discord(share, seconds=1.0, name="Discord.exe", end=None):
    """Заполняет буфер так, будто программа звучала долю `share` фразы."""
    meter = VoiceAppMeter(apps=("Discord.exe", "Telegram.exe"), interval=0.01,
                          window=30.0)
    end = time.perf_counter() if end is None else end
    start = end - seconds
    steps = 100
    loud = int(steps * share)
    for step in range(steps):
        when = start + seconds * (step + 0.5) / steps
        meter.add(when, 0.5 if step < loud else 0.0, name)
    return meter


class VoiceAppGuardTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.LISTEN_MODE, config.VOICE_APP_GUARD,
                       config.VOICE_APP_SHARE, config.VOICE_APP_LEVEL)
        config.LISTEN_MODE = "always"
        config.VOICE_APP_GUARD = True
        config.VOICE_APP_SHARE = 0.25
        config.VOICE_APP_LEVEL = 0.02

    def tearDown(self):
        (config.LISTEN_MODE, config.VOICE_APP_GUARD,
         config.VOICE_APP_SHARE, config.VOICE_APP_LEVEL) = self._saved

    def _ignored(self, loop):
        return [p for k, p in loop.events if k == "ignored"]

    def test_friends_in_discord_are_skipped_inside_open_conversation(self):
        # 26 сентября: окно на 180 с было открыто, и она отвечала друзьям.
        loop = _loop(_discord(0.6))
        self.assertFalse(loop._should_answer("ну давай скинь контакт", _phrase()))
        self.assertEqual(
            self._ignored(loop),
            [{"text": "ну давай скинь контакт", "why": "говорили в Discord"}],
        )

    def test_called_by_name_is_answered_anyway(self):
        loop = _loop(_discord(0.6))
        self.assertTrue(loop._should_answer("Труба, выключи свет", _phrase()))
        self.assertEqual(self._ignored(loop), [])

    def test_silent_discord_does_not_stop_anything(self):
        loop = _loop(_discord(0.0))
        self.assertTrue(loop._should_answer("а ты слышишь меня", _phrase()))
        self.assertEqual(self._ignored(loop), [])

    def test_short_burst_below_share_is_ignored(self):
        # Короткое «ага» из колонок — не повод замолчать на весь разговор.
        loop = _loop(_discord(0.1))
        self.assertTrue(loop._should_answer("а ты слышишь меня", _phrase()))

    def test_turned_off_setting_answers(self):
        config.VOICE_APP_GUARD = False
        loop = _loop(_discord(0.9))
        self.assertTrue(loop._should_answer("ну давай скинь контакт", _phrase()))
        self.assertEqual(self._ignored(loop), [])

    def test_phone_button_beats_the_guard(self):
        # Ручное разрешение сильнее любой автоматики.
        loop = _loop(_discord(0.9))
        loop._trust_next = True
        self.assertTrue(loop._should_answer("что угодно", _phrase()))
        self.assertEqual(self._ignored(loop), [])

    def test_telegram_keeps_its_own_name_in_the_log(self):
        loop = _loop(_discord(0.8, name="Telegram.exe"))
        self.assertFalse(loop._should_answer("да ладно тебе", _phrase()))
        self.assertEqual(self._ignored(loop)[0]["why"], "говорили в Telegram")

    def test_without_meter_answers_as_before(self):
        loop = _loop(None)
        self.assertTrue(loop._should_answer("просто фраза", _phrase()))

    def test_name_mode_still_asks_for_the_name_first(self):
        # Правило Discord стоит до режимов, поэтому при открытом звонке в
        # журнале именно оно — пропуск всё равно получился, а причина
        # точнее: слышалось-то чужое из колонок.
        config.LISTEN_MODE = "name"
        loop = _loop(_discord(0.9), open_conversation=False)
        self.assertFalse(loop._should_answer("просто фраза", _phrase()))
        self.assertEqual(
            self._ignored(loop),
            [{"text": "просто фраза", "why": "говорили в Discord"}],
        )
        # А когда в колонках тихо, режим «по имени» работает как прежде.
        quiet = _loop(_discord(0.0), open_conversation=False)
        self.assertFalse(quiet._should_answer("просто фраза", _phrase()))
        self.assertEqual(
            self._ignored(quiet),
            [{"text": "просто фраза", "why": "не позвали по имени"}],
        )


# --- Облако недоступно ----------------------------------------------------


class _Speaker:
    gapless = True

    def __init__(self):
        self.calls = []
        self.idle = threading.Event()
        self.idle.set()

    def say(self, wave, rate, gap=True):
        self.calls.append("say")

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Voice:
    """Синтез без моделей: на любое предложение — короткий кусок."""

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.zeros(240, dtype=np.float32), 24000


class _BrokenBrain:
    """Мозг, который всегда падает заданной ошибкой."""

    ended = False
    last_sources = None

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        self.calls += 1
        raise self.exc
        yield

    def digest(self):
        return None


class _GoodBrain:
    ended = False
    last_sources = None

    def __init__(self, sentences=("Вот твой ответ.",)):
        self.sentences = list(sentences)
        self.calls = 0

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        self.calls += 1
        yield from self.sentences

    def digest(self):
        return None


TIMEOUT = RuntimeError("APITimeoutError: Request timed out.")


def _answer_loop(brain):
    loop = _loop()
    config.OUTPUT = "speakers"
    loop._brain = brain
    loop._voice = _Voice()
    loop._speaker = _Speaker()
    loop._ref = None
    loop._fallback = None
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._loaded = True
    return loop


class CloudDownTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.OUTPUT, config.TTS_GAP)
        config.OUTPUT = "speakers"
        config.TTS_GAP = 0.1

    def tearDown(self):
        config.OUTPUT, config.TTS_GAP = self._saved

    def _said(self, loop):
        return [p for k, p in loop.events if k == "sentence"]

    def _kinds(self, loop):
        return [k for k, _ in loop.events]

    def test_apology_is_spoken_once_then_only_a_sound(self):
        # 26 сентября ~19:05: VPN отвалился, и на каждую фразу она повторяла
        # «не могу достучаться до облака» по 20 секунд подряд.
        loop = _answer_loop(_BrokenBrain(TIMEOUT))
        loop._answer("какой курс доллара")
        self.assertEqual(self._said(loop), [CLOUD_DOWN_WORDS])
        self.assertIn("cloud_down", self._kinds(loop))
        # Вторая фраза: модель даже не зовём, только звук отказа и журнал.
        loop._answer("а сейчас")
        self.assertEqual(self._said(loop), [CLOUD_DOWN_WORDS])
        self.assertEqual(loop._server.sounds.count("fail"), 1)
        self.assertEqual([p for k, p in loop.events if k == "cloud_silent"], ["а сейчас"])

    def test_model_is_not_called_while_cloud_is_down(self):
        brain = _BrokenBrain(TIMEOUT)
        loop = _answer_loop(brain)
        loop._answer("первая")
        loop._answer("вторая")
        self.assertEqual(brain.calls, 1)

    def test_after_a_minute_it_tries_again(self):
        loop = _answer_loop(_BrokenBrain(TIMEOUT))
        loop._answer("первая")
        loop._cloud_down = time.monotonic() - 61.0
        loop._answer("вторая")
        # Пошли в облако снова — и снова промолчали, но уже без извинения.
        self.assertEqual(loop._brain.calls, 2)
        self.assertEqual(self._said(loop), [CLOUD_DOWN_WORDS])
        self.assertEqual(self._kinds(loop).count("cloud_silent"), 1)

    def test_success_clears_the_mark(self):
        loop = _answer_loop(_BrokenBrain(TIMEOUT))
        loop._answer("первая")
        self.assertIsNotNone(loop._cloud_down)
        loop._cloud_down = time.monotonic() - 61.0
        loop._brain = _GoodBrain()
        loop._answer("вторая")
        self.assertIsNone(loop._cloud_down)
        self.assertIn("cloud_back", self._kinds(loop))
        # И следующая фраза идёт в облако как ни в чём не бывало: две из трёх
        # фраз дошли до модели, средняя — та, что разбудила облако.
        loop._answer("третья")
        self.assertEqual(loop._brain.calls, 2)

    def test_region_and_money_errors_are_not_a_network_outage(self):
        for exc, words in (
            (RuntimeError("Error code: 403 - unsupported_country_region_territory"),
             "Облако меня не пускает, похоже, отвалился VPN."),
            (RuntimeError("insufficient_quota: на счёте кончились деньги"),
             "В облаке кончились деньги на счёте."),
        ):
            with self.subTest(exc=str(exc)):
                loop = _answer_loop(_BrokenBrain(exc))
                loop._answer("вопрос")
                self.assertEqual(self._said(loop), [words])
                self.assertIsNone(loop._cloud_down)

    def test_bad_key_is_not_a_network_outage(self):
        loop = _answer_loop(_BrokenBrain(RuntimeError("401 Incorrect API key provided")))
        loop._answer("вопрос")
        self.assertIsNone(loop._cloud_down)
        self.assertNotIn("cloud_down", self._kinds(loop))


if __name__ == "__main__":
    unittest.main()
