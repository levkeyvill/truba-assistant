"""Заход первой в голосовом цикле: замок, телефон, обрыв связи, разговор.

Сети, микрофона и настоящих снимков тут нет: сторож вызывается руками, снимок
подменён, а «облако» — заготовкой, которая падает заданной ошибкой.
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import proactive, settings
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

TIMEOUT = RuntimeError("APITimeoutError: Request timed out.")
# Частота в тестах. База 25 минут, но тишину мы задаём руками.
ЧАСТОТА = "sometimes"


class _Quiet:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Speaker:
    gapless = True

    def __init__(self):
        self.calls = []
        self.idle = threading.Event()
        self.idle.set()

    def say(self, wave, rate, gap=True):
        self.calls.append(("say", 240, gap))

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Voice:
    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.zeros(240, dtype=np.float32), 24000


class _Phone:
    def __init__(self):
        self.states = []
        self.lines = []
        self.sounds = []

    def send_state(self, state, text=""):
        self.states.append((state, text))

    def send_line(self, who, text):
        self.lines.append((who, text))

    def send_sound(self, sound):
        self.sounds.append(sound)

    def send_mode(self, mode):
        pass


class _Brain:
    """Мозг, который помнит, чем его позвали."""

    ended = False
    last_sources = None
    did_ask = False

    def __init__(self, sentences=("Как дела?",), exc=None):
        self.sentences = list(sentences)
        self.exc = exc
        self.calls = []
        self.texts = []

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True,
              first=None):
        self.calls.append(dict(text=text, image=image, aloud=aloud,
                               can_end=can_end, first=first))
        if self.exc is not None:
            raise self.exc
        yield from self.sentences

    def digest(self):
        return None


def _loop(brain=None, open_conversation=False, last_talk=None):
    """Голосовой цикл вручную: без микрофона, динамика и сети.

    `last_talk` — сколько секунд назад было последнее событие разговора.
    None — разговор только что открылся, тишина нулевая.
    """
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._brain = brain if brain is not None else _Brain()
    loop._voice = _Voice()
    loop._speaker = _Speaker()
    loop._fallback = None
    loop._ref = None
    loop._server = _Phone()
    loop._listener = _Quiet()
    loop._ducker = _Quiet()
    loop._voice_meter = None
    loop._voice_score = None
    loop._voiceprint = _Quiet()
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._trust_next = False
    loop._search_next = False
    loop._speaking_text = ""
    loop._spoke_at = -1000.0
    loop._open = open_conversation
    loop._last_turn = 0.0
    loop.ready = True
    now = time.monotonic()
    loop._last_talk = now if last_talk is None else now - last_talk
    loop._first_pending = False
    loop._turn = threading.Lock()
    loop._first = proactive.Schedule()
    loop._digest_later = lambda: None
    return loop


def _пометить_пора(loop, отступ=1):
    """Срок «наступил давно» и нужный отступ — чтобы проверки проходили."""
    loop._first.backoff = отступ
    loop._first.due_at = 0.0
    return loop


def _виды(loop):
    return [k for k, _ in loop.events]


def _сказала(loop):
    return [p for k, p in loop.events if k == "sentence"]


class ОбщиеУсловия(unittest.TestCase):
    """Общая заготовка: настройки возвращаются как были."""

    def setUp(self):
        self._saved = (config.PROACTIVE, config.PROACTIVE_LOOK, config.LISTEN_MODE,
                       config.TTS_GAP, config.OUTPUT, config.ALLOW_BARGE_IN)
        config.PROACTIVE = ЧАСТОТА
        config.PROACTIVE_LOOK = False
        config.LISTEN_MODE = "always"
        config.TTS_GAP = 0.0
        config.OUTPUT = "speakers"
        config.ALLOW_BARGE_IN = False
        # Настоящий GetLastInputInfo в тестах не годится: за компом сидит
        # в том числе тот, кто их гоняет. По умолчанию — человек за клавиатурой.
        self._idle = mock.patch.object(proactive, "idle_seconds", return_value=5.0)
        self._idle.start()

    def tearDown(self):
        self._idle.stop()
        (config.PROACTIVE, config.PROACTIVE_LOOK, config.LISTEN_MODE,
         config.TTS_GAP, config.OUTPUT, config.ALLOW_BARGE_IN) = self._saved

    def _готова(self, loop=None, last_talk=4000.0, отступ=1):
        """Цикл, которому «пора»: срок наступил, человек за компом."""
        loop = loop if loop is not None else _loop(last_talk=last_talk)
        loop._first.backoff = отступ
        loop._first.due_at = 0.0
        loop._first.rnd = mock.Mock()
        loop._first.rnd.uniform.return_value = 1.0
        loop._first.rnd.random.return_value = 0.9
        return loop


class ЗамокTests(ОбщиеУсловия):
    """Один разговорный ход за раз: пока занято — не влезаем."""

    def test_busy_turn_means_no_talk(self):
        # Главное правило: пока хозяина слушают и отвечают ему, она молчит.
        loop = self._готова()
        loop._turn.acquire()
        try:
            loop._first_speak()
        finally:
            loop._turn.release()
        self.assertEqual(loop._brain.calls, [])

    def test_lock_is_free_after_a_talk(self):
        # Замок обязан отпуститься, иначе второй заход был бы невозможен.
        loop = self._готова()
        loop._first_speak()
        self.assertTrue(loop._turn.acquire(blocking=False))
        loop._turn.release()

    def test_phrase_under_the_lock_cancels_the_talk(self):
        # Проверка под замком в `_first_speak` не украшение: между первой
        # проверкой и захватом успевает прийти его фраза, и заговорили бы
        # два голоса разом.
        loop = self._готова()
        проверок = []

        def проверка():
            # Первая проверка — ещё тихо, вторая — уже пришла его фраза.
            проверок.append(1)
            return "" if len(проверок) == 1 else "разговор идёт"

        loop._first_ready = проверка
        loop._first_speak()
        self.assertEqual(проверок, [1, 1])
        self.assertEqual(loop._brain.calls, [])


class ТелефонTests(ОбщиеУсловия):
    def test_nudge_never_reaches_the_phone_as_his_words(self):
        loop = self._готова()
        loop._first_speak()
        подсказка = loop._brain.calls[0]["text"]
        self.assertIn("Заговори с ним первой", подсказка)
        # Как его реплика подсказка не должна уйти никуда: ни в ленту
        # телефона, ни в состояние.
        self.assertNotIn(("me", подсказка), loop._server.lines)
        for _, текст in loop._server.states:
            self.assertNotIn(подсказка[:40], текст)

    def test_an_ordinary_answer_still_shows_his_words(self):
        # Проверка на то, что правило не разъехалось на обычный ход.
        loop = self._готова()
        loop._answer("какой курс доллара")
        self.assertIn(("me", "какой курс доллара"), loop._server.lines)


class ОбрывСвязиTests(ОбщиеУсловия):
    def test_outage_stays_silent(self):
        # Из тишины «не могу достучаться» звучит пугающе: её не звали.
        loop = self._готова(_loop(brain=_Brain(exc=TIMEOUT), last_talk=4000.0))
        loop._first_speak()
        self.assertEqual(_сказала(loop), [])
        self.assertNotIn("cloud_down", _виды(loop))
        self.assertIsNotNone(loop._cloud_down)
        # Отметка об обрыве в журнале остаётся — иначе будет тихо совсем.
        self.assertIn("cloud_silent", _виды(loop))

    def test_outage_in_an_ordinary_answer_is_spoken(self):
        # Обычный ход по-прежнему проговаривает обрыв вслух.
        loop = self._готова(_loop(brain=_Brain(exc=TIMEOUT)))
        loop._answer("какой курс доллара")
        self.assertEqual(len(_сказала(loop)), 1)
        self.assertIn("cloud_down", _виды(loop))


class РазговорTests(ОбщиеУсловия):
    def test_conversation_is_open_after_the_talk(self):
        # Иначе ответить ей можно было бы только «Труба, …», а после её
        # шутки хозяин говорит просто «ахах».
        loop = self._готова()
        loop._first_speak()
        self.assertTrue(loop.in_conversation)
        self.assertIn(("conversation", True), loop.events)

    def test_no_timing_is_reported_for_a_first_talk(self):
        # Замер отвечает на вопрос «долго ли он ждал», а при заходе первой
        # никто не ждал.
        loop = self._готова()
        loop._first_speak()
        self.assertNotIn("timing", _виды(loop))
        # А в обычном ответе замер остаётся.
        other = self._готова()
        other._answer("вопрос")
        self.assertIn("timing", _виды(other))


class СнимокTests(ОбщиеУсловия):
    """Экран снимается без звука затвора, файла и телефона."""

    def test_screen_is_looked_at_when_the_draw_falls_on_it(self):
        loop = self._готова()
        config.PROACTIVE_LOOK = True
        loop._first.rnd.random.return_value = 0.1
        снимки = []
        with mock.patch("core.screen.grab", side_effect=lambda *a, **k: "КАДР"), \
                mock.patch("core.screen.as_data_url",
                           side_effect=lambda im: снимки.append(im) or "data:img"):
            loop._first_speak()
        self.assertEqual(снимки, ["КАДР"])
        self.assertEqual(loop._brain.calls[0]["image"], "data:img")
        first = [p for k, p in loop.events if k == "first"][0]
        self.assertTrue(first["look"])
        self.assertIn("Вот его экран прямо сейчас", loop._brain.calls[0]["text"])

    def test_screen_is_skipped_most_of_the_time(self):
        loop = self._готова()
        config.PROACTIVE_LOOK = True
        loop._first.rnd.random.return_value = 0.9
        with mock.patch("core.screen.grab") as grab:
            loop._first_speak()
        grab.assert_not_called()
        self.assertIsNone(loop._brain.calls[0]["image"])
        first = [p for k, p in loop.events if k == "first"][0]
        self.assertFalse(first["look"])

    def test_look_is_off_means_never_a_screenshot(self):
        loop = self._готова()
        config.PROACTIVE_LOOK = False
        with mock.patch("core.screen.grab") as grab:
            loop._first_speak()
        grab.assert_not_called()

    def test_failed_screenshot_does_not_stop_the_talk(self):
        # Снимок — украшение. Не получился — заговаривает всё равно.
        loop = self._готова()
        config.PROACTIVE_LOOK = True
        loop._first.rnd.random.return_value = 0.1
        with mock.patch("core.screen.grab", side_effect=OSError("нет экрана")):
            loop._first_speak()
        self.assertEqual(len(loop._brain.calls), 1)
        self.assertIsNone(loop._brain.calls[0]["image"])
        self.assertIn("error", _виды(loop))


class ВМодельTests(ОбщиеУсловия):
    """Что уходит в модель и что кладётся в историю."""

    def test_model_gets_the_nudge_and_history_gets_the_note(self):
        loop = self._готова()
        loop._first_speak()
        звонок = loop._brain.calls[0]
        self.assertIn("Заговори с ним первой", звонок["text"])
        # В историю уходит пометка о молчании, а не подсказка модели.
        self.assertIn("Труба заговорила первой", звонок["first"])
        self.assertNotIn("Заговори с ним", звонок["first"])
        self.assertTrue(звонок["aloud"])

    def test_log_line_says_how_long_and_whether_the_screen(self):
        loop = self._готова()
        loop._first_speak()
        строка = [p for k, p in loop.events if k == "first"][0]
        self.assertEqual(строка["minutes"], 66)
        self.assertFalse(строка["look"])


class СрокиTests(ОбщиеУсловия):
    def test_the_term_is_moved_right_after_the_talk(self):
        # Иначе следующий тик снова увидел бы «срок наступил» и заговорила
        # бы дважды подряд.
        loop = self._готова()
        self.assertEqual(loop._first.due_at, 0.0)
        loop._first_speak()
        self.assertGreater(loop._first.due_at, time.monotonic())
        self.assertFalse(loop._first.due(ЧАСТОТА, time.monotonic()))

    def test_never_speaks_nothing(self):
        loop = self._готова()
        config.PROACTIVE = "never"
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_earlier_term_is_not_reached(self):
        loop = self._готова()
        loop._first.due_at = time.monotonic() + 600.0
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_short_silence_after_a_talk_is_not_enough(self):
        # Ответили и онемели на минуту — рано: разговор был только что.
        loop = self._готова(last_talk=60.0)
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_off_hearing_means_silence(self):
        loop = self._готова()
        config.LISTEN_MODE = "off"
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_owner_who_left_is_not_disturbed(self):
        loop = self._готова()
        with mock.patch.object(proactive, "idle_seconds", return_value=3600.0):
            loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_voice_not_ready_means_silence(self):
        loop = self._готова()
        loop.ready = False
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_open_conversation_means_silence(self):
        loop = self._готова(_loop(open_conversation=True, last_talk=4000.0))
        loop._last_turn = time.monotonic()
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_cloud_outage_means_silence(self):
        loop = self._готова()
        loop._cloud_down = time.monotonic()
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])

    def test_a_loud_call_means_silence(self):
        # Идёт созвон в Discord — влезать в разговор друзей нельзя.
        loop = self._готова()
        loop._voice_meter = mock.Mock()
        loop._voice_meter.share_during.return_value = 0.5
        loop._first_speak()
        self.assertEqual(loop._brain.calls, [])
        self.assertTrue(loop._voice_meter.share_during.called)


class ОтступТесты(ОбщиеУсловия):
    """Срок отодвигается, пока на заход не отвечают, и возвращается назад."""

    def _следующий_заход(self, loop, молчание=20000.0):
        """Закрыть разговор, выждать тишину и снова сделать срок наступившим."""
        loop._close_conversation()
        loop._last_talk = time.monotonic() - молчание
        # Хвост эха: сразу после собственной речи она «говорит» ещё 6 с
        # (ECHO_TAIL). В тесте эти 6 с просто не ждём.
        loop._spoke_at = -1000.0
        loop._first.due_at = 0.0
        loop._first_pending = False

    def test_backoff_grows_after_each_unanswered_talk(self):
        # ×1 → ×2 → ×4 → и всё: после трёх молчаний «раз в сутки» хуже,
        # чем просто помолчать подольше.
        loop = self._готова()
        for ожидаемый in (2, 4, 4):
            loop._first_speak()
            self._следующий_заход(loop)
            self.assertEqual(loop._first.backoff, ожидаемый)
        self.assertEqual(loop._first.backoff, proactive.MAX_BACKOFF)

    def test_a_reply_resets_the_backoff(self):
        # Отступ 4 — значит тишины должно хватить на четыре базы.
        loop = self._готова(last_talk=20000.0, отступ=4)
        loop._first_speak()
        self.assertTrue(loop._first_pending)
        # Ответ пришёл: `_handle` снял флажок и обнулил счётчик.
        loop._first_pending = False
        loop._first.answered()
        loop._close_conversation()
        self.assertEqual(loop._first.backoff, 1)


class НастройкиTests(unittest.TestCase):
    """Настройка проверяется в пульте и меняется на ходу, без перезапуска."""

    def setUp(self):
        self._saved = (config.PROACTIVE, config.PROACTIVE_LOOK)
        # Настройки — во временный файл, как это делает весь прогон.
        self._guard = mock.patch.object(
            settings, "SETTINGS_PATH",
            Path(tempfile.mkdtemp(prefix="proactive-")) / "settings.json")
        self._guard.start()
        self.addCleanup(self._guard.stop)
        self.addCleanup(self._вернуть)

        self.runtime = object.__new__(WebRuntime)
        self.runtime.server = mock.Mock()
        self.runtime.brain = None
        self.runtime.voice = NS(_brain=None, _close_conversation=lambda: None,
                                running=False)
        self.runtime._lock = threading.Lock()
        self.runtime._provider_test_lock = threading.Lock()
        self.runtime._enroll = None
        self.runtime._jobs = {}
        self.runtime._audio_stale = False
        self.runtime._remember = lambda kind, payload: None

    def _вернуть(self):
        config.PROACTIVE, config.PROACTIVE_LOOK = self._saved

    def test_defaults_are_off_and_look_is_on(self):
        # Молчание по умолчанию: лишние запросы в облако никому не нужны,
        # а хозяин сам решит, когда это включить.
        self.assertEqual(config.PROACTIVE, "never")
        self.assertTrue(config.PROACTIVE_LOOK)
        self.assertIn("proactive", settings.DEFAULTS)
        self.assertIn("proactive_look", settings.DEFAULTS)

    def test_every_frequency_is_accepted(self):
        for частота in proactive.FREQUENCIES:
            ответ = self.runtime.save_settings({"proactive": частота})
            self.assertTrue(ответ["ok"], (частота, ответ))
            self.assertEqual(config.PROACTIVE, частота)
        # И настройка «смотреть на экран» — обычная галочка.
        self.assertTrue(
            self.runtime.save_settings({"proactive_look": False})["ok"])
        self.assertFalse(config.PROACTIVE_LOOK)

    def test_a_nonsense_frequency_is_refused(self):
        # Ошибка у поля: пульт по ней показывает именно «Заговаривать
        # первой», а не «Настройки».
        for плохо in ("всегда", "", 5, None, True):
            ответ = self.runtime.save_settings({"proactive": плохо})
            self.assertFalse(ответ["ok"], плохо)
            self.assertIn("proactive: ", " ".join(ответ["errors"]), плохо)

    def test_a_nonsense_look_flag_is_refused(self):
        for плохо in ("да", 1, None):
            ответ = self.runtime.save_settings({"proactive_look": плохо})
            self.assertFalse(ответ["ok"], плохо)
            self.assertIn("proactive_look: ", " ".join(ответ["errors"]), плохо)

    def test_it_survives_a_reload(self):
        self.runtime.save_settings({"proactive": "often",
                                    "proactive_look": False})
        # Настройка переживает перезапуск пульта: читается из settings.json.
        config.PROACTIVE = "never"
        config.PROACTIVE_LOOK = True
        settings.apply_to_config()
        self.assertEqual(config.PROACTIVE, "often")
        self.assertFalse(config.PROACTIVE_LOOK)

    def test_hand_edited_junk_does_not_break_the_start(self):
        # Мусор в settings.json руками не должен ронять голос на старте.
        settings.SETTINGS_PATH.write_text(
            '{"proactive": "каждый час", "proactive_look": 1}',
            encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.PROACTIVE, "never")
        self.assertTrue(config.PROACTIVE_LOOK)


class ЖурналTests(unittest.TestCase):
    """Строка журнала про заход первой."""

    def _строка(self, payload):
        runtime = object.__new__(WebRuntime)
        return runtime._log_messages("first", payload)

    def test_plain_talk(self):
        self.assertEqual(self._строка({"minutes": 25, "look": False}),
                         ["заговорила сама (молчал 25 мин)"])

    def test_talk_with_a_glance(self):
        self.assertEqual(
            self._строка({"minutes": 40, "look": True}),
            ["заговорила сама (молчал 40 мин), глянув на экран"],
        )


if __name__ == "__main__":
    unittest.main()
