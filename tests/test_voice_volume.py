"""Громкость голоса: словами, ползунком и с телефона.

Хозяин просил убавлять и прибавлять голос, не трогая громкость системы.
Поэтому проверяем ровно три вещи: что уровень доезжает до сигнала
одинаково в телефоне и колонках (одно место — `_sound_of`), что крайние
уровни не уезжают за себя и что всё это сохраняется и показывается
всем сразу.

Ни звука, ни сети, ни микрофона: подставлены динамик, синтез, сервер
телефона и временный `settings.json` (его путь и без нас подменяет
`tests/test_data_guard.py` на весь прогон).
"""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from say_helpers import assert_said
from core import commands, settings
from core.voice_loop import VoiceLoop, is_dismissal, voice_gain
from ui.web_runtime import WebRuntime


def _temp_settings():
    """Настройки — во временный файл, как это делает весь остальной прогон."""
    return mock.patch.object(
        settings, "SETTINGS_PATH",
        Path(tempfile.mkdtemp(prefix="volume-")) / "settings.json")


class _Speaker:
    """Динамик: запоминает сигнал, который в него отправили."""

    gapless = True

    def __init__(self, *args, **kwargs):
        self.waves = []
        self.idle = threading.Event()
        self.idle.set()

    def say(self, wave, rate, gap=True):
        self.waves.append(np.asarray(wave, dtype=np.float32))
        return True

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Voice:
    """Синтез: отдаёт ровно единицу, чтобы множитель был виден в цифрах."""

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.ones(100, dtype=np.float32), 24000


class _Server:
    """Сервер телефона: запоминает, что разослали."""

    def __init__(self):
        self.volumes = []

    def send_volume(self, value):
        self.volumes.append(value)

    def send_mode(self, mode):
        pass

    def send_state(self, state, text=""):
        pass

    def send_system(self, data):
        pass


def _loop():
    """Голосовой цикл из заглушек: ни микрофона, ни синтеза, ни динамика."""
    loop = object.__new__(VoiceLoop)
    loop._voice = _Voice()
    loop._ref = None
    loop._speaker = _Speaker()
    loop._fallback = None
    loop._server = None
    loop._open = False
    loop._last_turn = 0.0
    loop._speaking_text = ""
    loop._spoke_at = 0.0
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    return loop


# --- Разбор фраз ---------------------------------------------------------


class VolumeCommandTests(unittest.TestCase):
    def test_quieter_and_louder_are_steps(self):
        # Шаг: «тише» и «громче» (прочие формы — в следующем тесте).
        for фраза, шаг in (("тише", -1), ("громче", 1)):
            got = commands.understand(фраза)
            self.assertIsNotNone(got, фраза)
            self.assertEqual(got.action, "volume", фраза)
            self.assertEqual(got.step, шаг, фраза)
            # Сдвиг и точное число не путаем: «громче» — это шаг, а
            # «громкость пять» — ровно пять, а не «плюс пять».
            self.assertEqual(got.target, "", фраза)

    def test_the_usual_ways_are_in_the_table(self):
        # У модели инструмента громкости нет: привычные формы — в таблице,
        # иначе «потише» ушло бы к ней, и громкость не поменялась бы.
        for фраза, шаг in (("потише", -1), ("говори тише", -1), ("сделай потише", -1),
                           ("убавь громкость", -1), ("убавь", -1),
                           ("погромче", 1), ("говори громче", 1),
                           ("прибавь громкость", 1)):
            with self.subTest(фраза=фраза):
                got = commands.understand(фраза)
                self.assertIsNotNone(got, фраза)
                self.assertEqual((got.action, got.step), ("volume", шаг), фраза)
        got = commands.understand("поставь громкость на семь")
        self.assertEqual((got.action, got.target), ("volume", 7))

    def test_exact_level_in_words_and_digits(self):
        for фраза, уровень in (("громкость пять", 5), ("громкость на 5", 5),
                               ("громкость на семь", 7),
                               ("громкость десять", 10)):
            got = commands.understand(фраза)
            self.assertIsNotNone(got, фраза)
            self.assertEqual(got.action, "volume", фраза)
            self.assertEqual(got.target, уровень, фраза)
            self.assertEqual(got.step, 0, фраза)

    def test_tiho_is_not_about_volume(self):
        # «Тихо», «помолчи» и «замолчи» — просьба замолчать, а не громкость.
        # Если бы они попали в разбор команд, «замолчи» убавлял бы её на
        # ступень вместо того, чтобы она перестала говорить.
        for фраза in ("тихо", "помолчи", "замолчи"):
            got = commands.understand(фраза)
            self.assertNotEqual(getattr(got, "action", None), "volume", фраза)

    def test_tiho_still_closes_the_conversation(self):
        # Проверка на то, что «тихо» не сломалось и как прощание.
        for фраза in ("тихо", "помолчи", "замолчи"):
            self.assertTrue(is_dismissal(фраза), фраза)

    def test_tishe_does_not_close_the_conversation(self):
        # «Тише» — про громкость, разговор из-за него закрываться не должен.
        for фраза in ("тише", "сделай потише"):
            self.assertFalse(is_dismissal(фраза), фраза)

    def test_words_about_something_else_are_left_to_the_model(self):
        # «Громкость» сама по себе — не команда: нужен уровень или шаг.
        # Иначе «сделай громкость колонок» убавила бы её голос.
        for фраза in ("сделай громкость колонок", "как громкость в ютубе",
                      "как дела"):
            got = commands.understand(фраза)
            self.assertNotEqual(getattr(got, "action", None), "volume", фраза)

    def test_other_commands_still_work(self):
        self.assertEqual(commands.understand("сделай скриншот").action, "screenshot")
        self.assertEqual(commands.understand("сохрани момент").action, "moment")


# --- Применение к сигналу ------------------------------------------------


class VoiceGainTests(unittest.TestCase):
    """Множитель по уровню: одинаковый шаг на слух и никогда не больше 1."""

    def setUp(self):
        self._saved = config.VOICE_VOLUME
        self.addCleanup(lambda: setattr(config, "VOICE_VOLUME", self._saved))

    def test_ten_is_exactly_as_before(self):
        config.VOICE_VOLUME = 10
        self.assertAlmostEqual(voice_gain(), 1.0, places=6)

    def test_seven_and_one_match_the_three_decibel_step(self):
        config.VOICE_VOLUME = 7
        self.assertAlmostEqual(voice_gain(), 0.35, delta=0.01)
        config.VOICE_VOLUME = 1
        self.assertAlmostEqual(voice_gain(), 0.045, delta=0.005)

    def test_every_step_is_quieter_than_the_one_above(self):
        # Уровень 1 — самый тихий, 10 — самый громкий: по списку вниз.
        gains = []
        for level in range(1, 11):
            config.VOICE_VOLUME = level
            gains.append(voice_gain())
        self.assertEqual(gains, sorted(gains))
        self.assertLessEqual(max(gains), 1.0)

    def test_junk_level_does_not_break_the_sound(self):
        # Мусок в настройках не должен заглушить её совсем.
        for junk in (None, "а", 0, 99):
            config.VOICE_VOLUME = junk
            self.assertGreater(voice_gain(), 0.0, junk)


class ApplyToSoundTests(unittest.TestCase):
    """Громкость доезжает до сигнала в динамике — и в одном месте."""

    def setUp(self):
        self._saved = config.VOICE_VOLUME
        self.addCleanup(lambda: setattr(config, "VOICE_VOLUME", self._saved))

    def _сказано(self, level):
        config.VOICE_VOLUME = level
        loop = _loop()
        loop._say_plainly("Проверка.", loop._speaker)
        return loop._speaker.waves

    def test_ten_passes_the_signal_through_untouched(self):
        waves = self._сказано(10)
        self.assertTrue(waves)
        for wave in waves:
            self.assertTrue(np.allclose(wave, 1.0))

    def test_seven_is_about_a_third_of_the_volume(self):
        waves = self._сказано(7)
        self.assertTrue(waves)
        for wave in waves:
            self.assertAlmostEqual(float(wave[0]), 0.35, delta=0.01)

    def test_one_is_almost_inaudible(self):
        waves = self._сказано(1)
        self.assertTrue(waves)
        for wave in waves:
            self.assertAlmostEqual(float(wave[0]), 0.045, delta=0.005)

    def test_the_same_gain_goes_to_the_phone(self):
        # Телефон и колонки берут куски из одного `_sound_of`, поэтому разницы
        # быть не может: громкость одна на оба выхода.
        config.VOICE_VOLUME = 6
        loop = _loop()
        loop._say_plainly("Проверка.", loop._speaker)
        self.assertTrue(loop._speaker.waves)
        for wave in loop._speaker.waves:
            self.assertAlmostEqual(float(wave[0]), voice_gain(), places=6)


# --- Выполнение голосом --------------------------------------------------


class VolumeCommandRunTests(unittest.TestCase):
    """Голосовая команда: поменяла, сохранила, ответила новой громкостью."""

    def setUp(self):
        self._saved = config.VOICE_VOLUME
        self.guard = _temp_settings()
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.addCleanup(lambda: setattr(config, "VOICE_VOLUME", self._saved))

        self.server = _Server()
        self.loop = _loop()
        self.loop._server = self.server
        self.said = []
        self.loop._say_back = lambda words: self.said.append(words)
        self.loop._remember = lambda said, answered: None

    def _run(self, фраза):
        order = commands.understand(фраза)
        self.assertIsNotNone(order, фраза)
        self.loop._run_command(order, фраза)
        return order

    def test_step_down_changes_and_saves(self):
        config.VOICE_VOLUME = 7
        self._run("тише")
        self.assertEqual(config.VOICE_VOLUME, 6)
        self.assertEqual(settings.load_settings()["voice_volume"], 6)
        assert_said(self, self.said[-1], "voice_quieter")

    def test_step_up_changes_and_saves(self):
        config.VOICE_VOLUME = 7
        self._run("громче")
        self.assertEqual(config.VOICE_VOLUME, 8)
        self.assertEqual(settings.load_settings()["voice_volume"], 8)

    def test_exact_level_is_spoken_in_words(self):
        config.VOICE_VOLUME = 7
        self._run("громкость пять")
        self.assertEqual(config.VOICE_VOLUME, 5)
        assert_said(self, self.said[-1], "voice_volume", level="пять")

    def test_bottom_edge_stays_and_says_so(self):
        config.VOICE_VOLUME = 1
        self._run("тише")
        self.assertEqual(config.VOICE_VOLUME, 1)
        assert_said(self, self.said[-1], "voice_min")

    def test_top_edge_stays_and_says_so(self):
        config.VOICE_VOLUME = 10
        self._run("громче")
        self.assertEqual(config.VOICE_VOLUME, 10)
        assert_said(self, self.said[-1], "voice_max")

    def test_the_new_level_reaches_the_phone_and_the_journal(self):
        config.VOICE_VOLUME = 7
        self._run("тише")
        self.assertEqual(self.server.volumes, [6])
        self.assertIn(("volume", 6), self.loop.events)


# --- Регулятор на телефоне ----------------------------------------------


class VolumeFromPhoneTests(unittest.TestCase):
    """Кнопки «+» и «−» на телефоне меняют тот же уровень и сохраняют его."""

    def setUp(self):
        self._saved = config.VOICE_VOLUME
        self.guard = _temp_settings()
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.addCleanup(lambda: setattr(config, "VOICE_VOLUME", self._saved))

        self.server = _Server()
        self.runtime = object.__new__(WebRuntime)
        self.runtime.server = self.server
        self.runtime._lock = threading.Lock()
        self.runtime._ids = iter(range(1, 100))
        self.runtime._events = []
        self.runtime._overview = {}
        self.remembered = []
        self.runtime._remember = lambda kind, payload: self.remembered.append(
            (kind, payload))

    def _шаг(self, step):
        self.runtime.handle_event("volume", {"type": "volume", "step": step})

    def test_plus_step_raises_and_saves(self):
        config.VOICE_VOLUME = 7
        self._шаг(1)
        self.assertEqual(config.VOICE_VOLUME, 8)
        self.assertEqual(settings.load_settings()["voice_volume"], 8)
        self.assertEqual(self.server.volumes, [8])

    def test_minus_step_lowers_and_saves(self):
        config.VOICE_VOLUME = 7
        self._шаг(-1)
        self.assertEqual(config.VOICE_VOLUME, 6)
        self.assertEqual(settings.load_settings()["voice_volume"], 6)

    def test_edges_do_not_move(self):
        config.VOICE_VOLUME = 10
        self._шаг(1)
        self.assertEqual(config.VOICE_VOLUME, 10)
        self.assertEqual(self.server.volumes, [])
        config.VOICE_VOLUME = 1
        self._шаг(-1)
        self.assertEqual(config.VOICE_VOLUME, 1)
        self.assertEqual(self.server.volumes, [])

    def test_plus_at_ten_is_not_an_error(self):
        # «+» на максимуме отвечает «громче некуда» без ошибки.
        toasts = []
        self.server.send_toast = lambda text, ok=True: toasts.append(text)
        config.VOICE_VOLUME = 10
        self._шаг(1)
        self.assertFalse([kind for kind, _payload in self.remembered if kind == "error"])
        self.assertEqual(toasts, ["громче некуда"])
        config.VOICE_VOLUME = 1
        self._шаг(-1)
        self.assertEqual(toasts, ["громче некуда", "тише некуда"])

    def test_the_journal_says_the_new_level(self):
        config.VOICE_VOLUME = 5
        self._шаг(1)
        self.assertIn(("volume", 6), self.remembered)
        self.assertEqual(WebRuntime._log_messages("volume", 6), ["громкость голоса: 6"])

    def test_a_zero_or_broken_step_is_ignored(self):
        config.VOICE_VOLUME = 5
        for payload in ({"step": 0}, {}, {"step": "минус один"}, {"step": None},
                        "не словарь"):
            self.runtime.handle_event("volume", payload)
            self.assertEqual(config.VOICE_VOLUME, 5, payload)
        self.assertEqual(self.server.volumes, [])

    def test_voice_state_carries_the_level_for_the_pult(self):
        config.VOICE_VOLUME = 4
        self.runtime.voice = NS(running=True, ready=True, in_conversation=False,
                                _idle_state=lambda: "listening")
        self.assertEqual(self.runtime.voice_state()["volume"], 4)

    def test_out_of_range_is_refused(self):
        self.assertFalse(self.runtime.voice_volume(level=99)["ok"])
        self.assertFalse(self.runtime.voice_volume(level=0)["ok"])


# --- Ползунок в пульте ---------------------------------------------------


class VolumeSaveTests(unittest.TestCase):
    """Ползунок в «Голос → Озвучивание»: целое в пределах 1…10."""

    def setUp(self):
        self._saved = config.VOICE_VOLUME
        self.guard = _temp_settings()
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.addCleanup(lambda: setattr(config, "VOICE_VOLUME", self._saved))

        self.server = _Server()
        self.runtime = object.__new__(WebRuntime)
        self.runtime.server = self.server
        self.runtime.brain = None
        self.runtime.voice = NS(_brain=None, _close_conversation=lambda: None,
                                running=False)
        self.runtime._lock = threading.Lock()
        self.runtime._provider_test_lock = threading.Lock()
        self.runtime._enroll = None
        self.runtime._jobs = {}
        self.runtime._audio_stale = False
        self.runtime._remember = lambda kind, payload: None

    def test_a_level_in_range_is_saved_and_pushed_to_phones(self):
        result = self.runtime.save_settings({"voice_volume": 7})
        self.assertTrue(result["ok"], result)
        self.assertEqual(config.VOICE_VOLUME, 7)
        self.assertIn(7, self.server.volumes)

    def test_a_level_out_of_range_is_refused(self):
        for bad in (0, 11, "много"):
            result = self.runtime.save_settings({"voice_volume": bad})
            self.assertFalse(result["ok"], bad)
            self.assertIn("voice_volume", " ".join(result["errors"]), bad)

    def test_a_fractional_level_is_refused(self):
        # Уровень целый: 7.5 не на что превращать в децибелы.
        result = self.runtime.save_settings({"voice_volume": 7.5})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
