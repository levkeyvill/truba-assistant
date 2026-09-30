"""Перебивание голосом — сразу, по началу его речи.

Хозяин (30.09): «когда её перебиваю, она не перебивается». За всё время в
журнале не было ни одного события `interrupted`: перебивание срабатывало
только после 1.15 с тишины и распознавания, а к тому моменту она давно
договорила, и `_speaker_idle()` отвечал «свободна».

Проверяем ровно то, из чего решается перебивание:

* `Listener` меряет фон её голоса (80-й процентиль по ~2 с) и ждёт его
  голоса — медиана выше `max(BARGE_IN_LEVEL, фон × BARGE_RATIO)`;
* фон набирается только за её речь: в тишине перебивания не бывает;
* срабатывание один раз за реплику, новая реплика — снова можно;
* `VoiceLoop` на срабатывании замолкает и пишет `barge`, при звучащем
  Discord или выключенной галочке — нет, а если фраза оказалась не его
  голосом, рядом с `ignored` встаёт `barge_false`;
* `echo_level` — замер её речи для подбора порогов;
* галочка «Перебивать сразу» сохраняется и доезжает до `config`.

Ни микрофона, ни звука, ни сети: детектор речи и программы с голосом
подменены, сигнал синтетический, файлы настроек — во временной папке.
"""

import json
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core import settings
from core.audio_in import VAD_HOP, Listener
from core.ducking import VoiceAppMeter
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"


# --- Слушатель без микрофона ------------------------------------------------


class _Вад:
    """Детектор речи по расписанию, а не по настоящему звуку.

    План — пара `(вероятность, амплитуда)` на кусок. Настоящий Silero тут не
    нужен: проверяется решение о перебивании, а не распознавание.
    """

    def __init__(self, план):
        self.план = list(план)
        self.at = 0

    def probability(self, chunk) -> float:
        вер, _ = self.план[self.at] if self.at < len(self.план) else (0.0, 0.0)
        self.at += 1
        return вер


class _Очередь(queue.Queue):
    """Очередь микрофона, которая по концу записи останавливает слушателя.

    Настоящая очередь живёт вечно: `phrases()` блокируется в ожидании
    следующего блока, пока его не пришлёт живой микрофон. Здесь запись
    кончилась — и ждать больше нечего.
    """

    def __init__(self, stop, blocks: int):
        super().__init__()
        self._stop = stop
        self._blocks = blocks

    def get(self, timeout=None, block=True):
        if self._blocks <= 0:
            self._stop.set()
            raise queue.Empty
        self._blocks -= 1
        return super().get(timeout=timeout, block=block)


def _куски(секунды: float) -> int:
    return int(секунды * config.SAMPLE_RATE / VAD_HOP)


def _амплитуда(уровень: float) -> float:
    """Амплитуда куска постоянного сигнала с RMS = уровень.

    Перебивание меряет линейную громкость (RMS), а не сжатую `voice_level`
    (30.09, ревью): у постоянного сигнала RMS и есть его амплитуда.
    """
    return float(уровень)


def _речь(секунды: float, уровень: float, prob: float = 1.0):
    return [(prob, _амплитуда(уровень))] * _куски(секунды)


def _тишина(секунды: float, уровень: float = 0.0, prob: float = 0.0):
    return [(prob, _амплитуда(уровень))] * _куски(секунды)


def _слушатель(план, barge: float = None) -> Listener:
    """Слушатель без микрофона: очередь набита заранее, детектор — по плану."""
    stop = threading.Event()
    слушатель = object.__new__(Listener)
    слушатель._queue = _Очередь(stop, 1)
    слушатель._stop = stop
    слушатель._muted = threading.Event()
    слушатель._vad = _Вад(план)
    слушатель._barge_threshold = config.BARGE_IN_LEVEL if barge is None else barge
    # Фразу начинаем всегда: проверяется перебивание, а не порог начала.
    слушатель._may_start = lambda preroll: True
    слушатель.on_voice = None
    слушатель.on_barge = None
    слушатель._reset_echo()
    сигнал = np.concatenate([
        np.full(VAD_HOP, амплитуда, dtype=np.float32) for _, амплитуда in план
    ])
    слушатель._queue.put(сигнал)
    return слушатель


def _перебивания(слушатель: Listener) -> list:
    """Прогнать запись и собрать все срабатывания `on_barge`."""
    звонки: list = []
    слушатель.on_barge = lambda level, floor: звонки.append((level, floor))
    for _ in слушатель.phrases():
        pass
    return звонки


class EchoLevelTests(unittest.TestCase):
    """Замер фона: 80-й процентиль, а не максимум."""

    def _пустой(self) -> Listener:
        слушатель = object.__new__(Listener)
        слушатель._reset_echo()
        return слушатель

    def test_the_floor_is_a_percentile_and_not_a_peak(self):
        # Один щелчок посреди её речи не должен поднимать фон: иначе после
        # него перестало бы срабатывать всё подряд.
        слушатель = self._пустой()
        for _ in range(30):
            слушатель._echo_add(0.03)
        слушатель._echo_add(0.9)
        self.assertAlmostEqual(слушатель._echo_floor(), 0.03, places=6)

    def test_an_empty_floor_is_zero(self):
        # Сравнивать не с чем — и сработать нечему.
        слушатель = self._пустой()
        self.assertEqual(слушатель._echo_floor(), 0.0)
        self.assertEqual(слушатель.echo_measure(), (0.0, 0.0))

    def test_the_measure_returns_the_floor_and_the_peak(self):
        # По этим двум числам в журнале подбираются пороги.
        слушатель = self._пустой()
        for level in (0.02, 0.03, 0.04, 0.3):
            слушатель.last_echo_peak = max(слушатель.last_echo_peak, level)
            слушатель._echo_add(level)
        floor, peak = слушатель.echo_measure()
        self.assertAlmostEqual(floor, 0.04, places=6)
        self.assertAlmostEqual(peak, 0.3, places=6)


class InstantBargeTests(unittest.TestCase):
    """Решает `Listener`: пора ли обрывать её посреди речи."""

    def test_voice_over_quiet_echo_is_a_barge(self):
        # Эхо 0.03, его голос 0.12: вчетверо выше фона — перебиваем.
        план = _речь(1.5, 0.03) + _речь(0.5, 0.12)
        звонки = _перебивания(_слушатель(план))

        self.assertEqual(len(звонки), 1, "перебивание одно за реплику")
        уровень, фон = звонки[0]
        self.assertAlmostEqual(уровень, 0.12, delta=0.01)
        self.assertAlmostEqual(фон, 0.03, delta=0.01)

    def test_voice_over_loud_echo_is_not_a_barge(self):
        # Эхо 0.08 (телефон рядом с микрофоном), его голос 0.10: чуть выше
        # фона, а не в 2.5 раза. Обрывать нельзя — это всё ещё её эхо.
        план = _речь(1.5, 0.08) + _речь(0.6, 0.10)
        self.assertEqual(_перебивания(_слушатель(план)), [])

    def test_a_short_click_does_not_cut_her_off(self):
        # Стук по столу на 100 мс: короче BARGE_MS, медиана окна остаётся
        # её эхом.
        план = _речь(1.5, 0.03) + _речь(0.1, 0.5) + _речь(1.0, 0.03)
        self.assertEqual(_перебивания(_слушатель(план)), [])

    def test_without_a_measured_floor_nothing_fires(self):
        # Первые 0.3 с её речи: фон ещё не с чем сравнивать — ждём. Иначе на
        # зареве мы обрывали бы её на первом же слове.
        план = _речь(0.3, 0.03) + _речь(0.5, 0.12)
        слушатель = _слушатель(план)
        self.assertEqual(_перебивания(слушатель), [])
        self.assertLess(слушатель._echo_samples, int(0.5 * config.SAMPLE_RATE))

    def test_it_fires_only_once_per_turn(self):
        # Вторая его фраза поверх той же её реплики — уже не перебивание.
        план = _речь(1.5, 0.03) + _речь(0.5, 0.12) + _речь(0.5, 0.12)
        self.assertEqual(len(_перебивания(_слушатель(план))), 1)

    def test_a_new_turn_allows_barging_again(self):
        # Новая её реплика — новый фон, и перебивать можно снова.
        слушатель = _слушатель(_речь(1.5, 0.03) + _речь(0.5, 0.12))
        self.assertEqual(len(_перебивания(слушатель)), 1)
        слушатель.listen_while_speaking(config.BARGE_IN_LEVEL)
        self.assertFalse(слушатель._barge_fired, "новая реплика — снова можно")

    def test_while_she_is_quiet_nothing_fires(self):
        # Микрофон не слушает громко — перебивания не бывает вовсе.
        план = _речь(1.5, 0.03) + _речь(0.5, 0.12)
        self.assertEqual(_перебивания(_слушатель(план, barge=0.0)), [])

    def test_stopping_loudly_clears_the_turn(self):
        # `stop_listening_loudly` вызывается в конце её речи: следующая её
        # фраза не должна унаследовать решение прошлой.
        слушатель = object.__new__(Listener)
        слушатель._reset_echo()
        слушатель._barge_fired = True
        слушатель._echo.append(0.03)
        слушатель.stop_listening_loudly()
        self.assertFalse(слушатель._barge_fired)
        self.assertEqual(list(слушатель._echo), [])

    def test_a_broken_subscriber_does_not_stop_the_phrases(self):
        # Оборванный голосовой цикл не должен ронять нарезку фраз.
        def обрыв(level, floor):
            raise RuntimeError("цикл упал")

        план = _речь(1.5, 0.03) + _речь(0.5, 0.12) + _тишина(2.0)
        слушатель = _слушатель(план)
        слушатель.on_barge = обрыв
        self.assertTrue(list(слушатель.phrases()), "микрофон важнее перебивания")

    def test_the_peak_of_her_turn_is_kept_for_the_log(self):
        # Пик нужен журналу: по нему видно, сколько микрофон слышал на её
        # речи, даже если перебивания не вышло.
        план = _речь(1.5, 0.08) + _речь(0.5, 0.10)
        слушатель = _слушатель(план)
        _перебивания(слушатель)
        _, пик = слушатель.echo_measure()
        self.assertAlmostEqual(пик, 0.10, delta=0.01)


# --- Голосовой цикл ---------------------------------------------------------


class _Метр:
    """Программа с голосом: шумит, когда ей скажут, и молчит иначе."""

    def __init__(self, доля: float = 0.0):
        self.available = True
        self._доля = доля

    def share_during(self, start, end, level) -> float:
        return self._доля

    def loudest_app(self, start, end, level) -> str:
        return "Discord.exe" if self._доля else ""


class _Слушатель:
    """Микрофон с заранее заданным замером её речи."""

    def __init__(self, замер=(0.0, 0.0)):
        self._замер = замер

    def echo_measure(self):
        return self._замер


def _цикл(meter=None) -> VoiceLoop:
    """Голосовой цикл вручную: без микрофона, динамика и мозга."""
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._barged = None
    loop._listener = _Слушатель()
    loop._voice_meter = meter
    loop.shut_up = mock.Mock()
    return loop


def _события(loop, kind):
    return [payload for k, payload in loop.events if k == kind]


class VoiceLoopBargeTests(unittest.TestCase):
    """Решает `VoiceLoop`: обрывать ли ей речь."""

    def setUp(self):
        self._было = config.BARGE_INSTANT
        self.addCleanup(setattr, config, "BARGE_INSTANT", self._было)

    def test_voice_over_her_speech_cuts_her_off(self):
        # То самое, о чём хозяин: говорит поверх неё — она замолкает сразу.
        loop = _цикл()
        loop._mic_barge(0.12, 0.03)
        loop.shut_up.assert_called_once()
        self.assertEqual(_события(loop, "barge"),
                         [{"level": 0.12, "floor": 0.03}])

    def test_the_measure_is_kept_for_the_phrase_that_follows(self):
        # Фраза, собирающаяся после обрыва, придёт в `_should_answer`, и там
        # надо знать, что перебивание было.
        loop = _цикл()
        loop._mic_barge(0.12, 0.03)
        self.assertEqual(loop._barged, {"level": 0.12, "floor": 0.03})

    def test_a_phrase_that_is_not_his_reports_a_false_barge(self):
        # Перебили, а услышали её эхо. По журналу видно, что порог ниже или
        # отношение к фону меньше; её речь не возобновляем.
        loop = _цикл()
        loop._mic_barge(0.12, 0.03)
        loop._barge_settled(False, "Отключайся")
        self.assertEqual(_события(loop, "barge_false"),
                         [{"text": "Отключайся", "level": 0.12, "floor": 0.03}])
        self.assertIsNone(loop._barged, "отметка снимается — разбор окончен")

    def test_his_own_phrase_is_not_a_false_barge(self):
        loop = _цикл()
        loop._mic_barge(0.12, 0.03)
        loop._barge_settled(True, "подожди")
        self.assertEqual(_события(loop, "barge_false"), [])

    def test_a_phrase_without_a_barge_says_nothing(self):
        # Перебивания не было — нечего и оправдываться.
        loop = _цикл()
        loop._barge_settled(False, "подожди")
        self.assertEqual(_события(loop, "barge_false"), [])

    def test_friends_in_discord_are_not_his_voice(self):
        # Пока в Discord говорят, микрофон слышит чужие голоса: обрывать её
        # на чужой реплике нельзя.
        loop = _цикл(_Метр(0.6))
        loop._mic_barge(0.12, 0.03)
        loop.shut_up.assert_not_called()
        self.assertEqual(loop.events, [])

    def test_silent_discord_does_not_stop_anything(self):
        # Тишина в колонках — не помеха: хозяин говорит в пустую комнату.
        loop = _цикл(_Метр(0.0))
        loop._mic_barge(0.12, 0.03)
        loop.shut_up.assert_called_once()

    def test_the_turned_off_checkbox_keeps_silence(self):
        # Выключил галочку — обрывать нельзя, даже если голос отчётливо выше.
        config.BARGE_INSTANT = False
        loop = _цикл()
        loop._mic_barge(0.12, 0.03)
        loop.shut_up.assert_not_called()
        self.assertEqual(loop.events, [])

    def test_without_a_meter_nothing_is_blocked(self):
        # pycaw нет (или сломался COM) — метра нет, и обрыв не запрещён.
        loop = _цикл(None)
        loop._mic_barge(0.12, 0.03)
        loop.shut_up.assert_called_once()

    def test_her_turn_is_measured_once_for_the_log(self):
        # По фону и пику подбираются пороги — на живых цифрах, а не на глаз.
        loop = _цикл()
        loop._listener = _Слушатель((0.03, 0.12))
        loop._log_echo()
        self.assertEqual(_события(loop, "echo_level"),
                         [{"floor": 0.03, "peak": 0.12}])

    def test_a_silent_turn_is_not_measured(self):
        # Микрофон на её речи не слышал ничего — замерять нечего, и строка в
        # журнале была бы пустой.
        loop = _цикл()
        loop._listener = _Слушатель((0.0, 0.0))
        loop._log_echo()
        self.assertEqual(_события(loop, "echo_level"), [])

    def test_a_broken_listener_does_not_stop_the_voice(self):
        # Сломанный микрофон не должен ронять голосовой цикл.
        class _Сломанный:
            def echo_measure(self):
                raise RuntimeError("микрофон отвалился")

        loop = _цикл()
        loop._listener = _Сломанный()
        loop._log_echo()
        self.assertEqual(loop.events, [])


class LogLinesTests(unittest.TestCase):
    """Журнал пульта: три новые строки понятны хозяину без пояснений."""

    def setUp(self):
        self.runtime = object.__new__(WebRuntime)

    def _строки(self, kind, payload):
        return self.runtime._log_messages(kind, payload)

    def test_the_barge_line_names_both_numbers(self):
        строки = self._строки("barge", {"level": 0.123, "floor": 0.031})
        self.assertEqual(len(строки), 1)
        self.assertIn("0.123", строки[0])
        self.assertIn("0.031", строки[0])

    def test_the_false_barge_line_says_so_in_plain_words(self):
        строки = self._строки("barge_false",
                              {"text": "Отключайся", "level": 0.1, "floor": 0.05})
        self.assertIn("ложным", строки[0])
        self.assertIn("Отключайся", строки[0])

    def test_the_echo_line_reports_floor_and_peak(self):
        строки = self._строки("echo_level", {"floor": 0.03, "peak": 0.12})
        self.assertIn("фон 0.030", строки[0])
        self.assertIn("пик 0.120", строки[0])


class SettingsTests(unittest.TestCase):
    """Галочка «Перебивать сразу»: умолчание, сохранение, применение."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-barge-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=self.папка / "settings.json",
            ENV_PATH=self.папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        self.addCleanup(setattr, config, "BARGE_INSTANT", config.BARGE_INSTANT)
        # `apply_to_config` переносит в config и папку заметок — возвращаем.
        self.addCleanup(setattr, config, "NOTES_DIR", config.NOTES_DIR)

    def test_it_is_on_by_default(self):
        # Без этого перебивания не было вовсе: за всё время в журнале не
        # случилось ни одного `interrupted`.
        self.assertTrue(config.BARGE_INSTANT)
        self.assertIn("barge_instant", settings.DEFAULTS)
        self.assertTrue(settings.DEFAULTS["barge_instant"])

    def test_the_untoggled_turns_itself_off(self):
        settings.save_settings({"barge_instant": False})
        settings.apply_to_config()
        self.assertFalse(config.BARGE_INSTANT)

    def test_a_saved_value_reaches_the_file(self):
        settings.save_settings({"barge_instant": False})
        сохранено = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertFalse(сохранено["barge_instant"])

    def test_a_missing_key_keeps_it_on(self):
        # Старый settings.json без нового ключа — не поломка: галочка
        # остаётся включённой, как и задумано.
        settings.save_settings({})
        settings.apply_to_config()
        self.assertTrue(config.BARGE_INSTANT)


class PultTests(unittest.TestCase):
    """Пульт: поле, подпись и отправка — как у соседних настроек."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT_JS.read_text(encoding="utf-8")

    def test_the_field_sits_next_to_the_barge_threshold(self):
        # Рядом с «Порогом перебивания»: хозяин настраивает оба вместе.
        self.assertIn("настрПоле(голосСекция, 'barge_in_level', barge_in_level)",
                      self.скрипт)
        self.assertIn("настрПоле(голосСекция, 'barge_instant', barge_instant)",
                      self.скрипт)

    def test_it_has_a_plain_russian_caption(self):
        self.assertIn("barge_instant: ['Перебивать сразу',", self.скрипт)
        self.assertIn("Выключи, если она сама себя обрывает", self.скрипт)

    def test_the_form_sends_and_fills_it(self):
        self.assertIn("barge_instant: эл.barge_instant.checked,", self.скрипт)
        self.assertIn("эл.barge_instant.checked = s.barge_instant !== false;",
                      self.скрипт)
        self.assertIn("duck_level, barge_in_level, barge_instant, послушатьПробу",
                      self.скрипт)
