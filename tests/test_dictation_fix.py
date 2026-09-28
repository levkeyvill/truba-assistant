"""Диктовка 27.09: почему она ломалась и что теперь с ней.

Проверяется ровно то, что чинили, по журналу живого разговора:

* длинная речь режется ДО распознавания (21.09 GigaAM падал на куске длиннее
  ~54 с, и исключение выключало весь голос);
* `heard_at` и сторож тишины: пока хозяин говорит, тишины нет, а до первой
  фразы ждать дольше;
* окно разговора во время диктовки не закрывается — 51 секунда диктовки
  пропала вместе со звуком «закрыто»;
* одна плохая фраза не роняет голос;
* `begin_dictation` — вход без собственной речи, слова скажет модель;
* у модели есть инструмент `start_dictation` (27.09 она говорила «диктуй», а
  режим не включала);
* заметка ловится и в более живых формулировках, но не в прошедшем времени;
* старый разговор отделён от нового.

Ни микрофона, ни синтеза, ни облака, ни настоящих «Документов»: сигнал
синтетический, причёсывание и запись подменены, папка заметок — временная.
"""

import json
import queue
import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import commands, hands, notes
from core.audio_in import VAD_HOP, Listener
from core.brain import Brain
from core.voice_loop import DICTATION_EMPTY, DICTATION_START, VoiceLoop

APPS = [{"id": "youtube", "title": "YouTube", "kind": "url",
         "url": "https://youtube.com"}]


# --- Нарезка фраз на синтетическом сигнале --------------------------------


class _Vad:
    """Детектор речи по расписанию, а не по настоящему звуку.

    `plan` — вероятность по кускам: 1.0 означает речь, 0.0 — тишину. Настоящий
    Silero здесь не нужен: проверяется нарезка, а не распознавание.
    """

    def __init__(self, plan: list[float]):
        self.plan = list(plan)
        self.at = 0

    def probability(self, chunk) -> float:
        value = self.plan[self.at] if self.at < len(self.plan) else 0.0
        self.at += 1
        return value


def _куски(секунды: float) -> int:
    return int(секунды * config.SAMPLE_RATE / VAD_HOP)


class _Очередь(queue.Queue):
    """Очередь микрофона, которая по концу записи останавливает слушателя.

    Настоящая очередь живёт вечно: `phrases()` блокируется в ожидании следующего
    блока, пока его не пришлёт живой микрофон. Здесь запись кончилась — и ждать
    больше нечего.
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


def _слушатель(план: list[float]) -> Listener:
    """Слушатель без микрофона: очередь набита заранее, детектор — по плану."""
    stop = threading.Event()
    слушатель = object.__new__(Listener)
    # Один блок на всю запись: `phrases()` сама режет его на куски по VAD_HOP.
    слушатель._queue = _Очередь(stop, 1)
    слушатель._stop = stop
    слушатель._vad = _Vad(план)
    слушатель._barge_threshold = 0
    слушатель._may_start = lambda preroll: True
    слушатель._queue.put(np.ones(len(план) * VAD_HOP, dtype=np.float32))
    return слушатель


def _нарезать(слушатель: Listener) -> list[np.ndarray]:
    фразы = []
    for фраза in слушатель.phrases():
        фразы.append(фраза)
    return фразы


class PhraseCutTests(unittest.TestCase):
    def test_a_long_speech_without_pauses_is_cut_for_recognition(self):
        # 50 с речи без единой паузы: на таком куске GigaAM падал (21.09).
        # Значит ни одна фраза не длиннее PHRASE_HARD_MAX…
        слушатель = _слушатель([1.0] * _куски(50) + [0.0] * _куски(2))
        фразы = _нарезать(слушатель)
        предел = int(config.PHRASE_HARD_MAX * config.SAMPLE_RATE)
        self.assertGreater(len(фразы), 1, "длинная речь должна резаться")
        for фраза in фразы:
            self.assertLessEqual(len(фраза), предел, "фраза длиннее распознавания")
        # …и при этом ничего не потеряно: вся речь в сумме. Больше быть не
        # может: хвост тишины в распознавание не отдаётся, как и раньше.
        всего = sum(len(фраза) for фраза in фразы)
        self.assertGreaterEqual(всего, 50 * config.SAMPLE_RATE)
        self.assertLessEqual(всего, 52 * config.SAMPLE_RATE)

    def test_a_pause_inside_a_long_speech_splits_it(self):
        # 35 с речи с паузой 0.4 с на 32-й секунде. Пауза короче обычной
        # SILENCE_TO_END, но длинная фраза на ней заканчивается: иначе монолог
        # ушёл бы в распознавание целиком.
        план = ([1.0] * _куски(32) + [0.0] * _куски(0.4)
                + [1.0] * _куски(3) + [0.0] * _куски(2))
        фразы = _нарезать(_слушатель(план))
        self.assertEqual(len(фразы), 2, [len(ф) / config.SAMPLE_RATE for ф in фразы])
        # Первая фраза длиннее обычной: это монолог, а не короткая реплика.
        self.assertGreater(len(фразы[0]),
                           config.PHRASE_SOFT_MAX * config.SAMPLE_RATE)

    def test_heard_at_grows_while_the_person_speaks(self):
        # Пока идёт речь, тишины нет: сторож диктовки ждёт отсюда.
        self.assertEqual(Listener.heard_at, 0.0, "по умолчанию — на уровне класса")
        слушатель = _слушатель([1.0] * _куски(5) + [0.0] * _куски(2))
        _нарезать(слушатель)
        self.assertGreater(слушатель.heard_at, 0.0)

    def test_a_short_phrase_is_not_cut_by_the_soft_limit(self):
        # 10 с речи и тишина: обычная фраза, мягкий предел её не трогает.
        слушатель = _слушатель([1.0] * _куски(10) + [0.0] * _куски(2))
        self.assertEqual(len(_нарезать(слушатель)), 1)


# --- Сторож тишины, окно разговора и плохая фраза -------------------------


def _цикл(ready=True) -> VoiceLoop:
    """Голосовой цикл из заглушек: ни микрофона, ни синтеза, ни облака."""
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop.said = []
    loop.remembered = []
    loop.written = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._say_back = loop.said.append
    loop._remember = lambda text, answered: loop.remembered.append((text, answered))
    loop._tell_phone = lambda *args, **kwargs: None
    loop._open = False
    loop._last_turn = 0.0
    loop._last_talk = 0.0
    loop._server = None
    loop._brain = None
    loop._turn = None
    loop._dictation = None
    loop._dictation_at = 0.0
    loop._dictation_command = ""
    loop._search_next = False
    loop._first_pending = False
    loop._interrupt = threading.Event()
    loop._stop = threading.Event()
    loop._listener = None
    loop.ready = ready
    loop._thread = mock.Mock(is_alive=lambda: ready)
    loop._speaker_idle = lambda: True
    loop._close_conversation = mock.Mock()
    loop._open_conversation = mock.Mock()
    return loop


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="truba-dictation-fix-")
        saved = config.NOTES_DIR
        config.NOTES_DIR = self.tmp
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", saved))
        patcher = mock.patch.multiple(
            notes,
            polish=lambda brain, said, command="", known=None: {
                "section": "Книги", "topic": "Мастер и Маргарита",
                "title": "Заголовок", "text": "Причёсанный текст.", "raw": False},
            add=lambda section, topic, title, text, raw="", when=None: (
                loop_written.append((section, topic, title, text, raw))
                or "путь/тема.md"),
            known_topics=lambda: [])
        self.loop_written = loop_written = []
        patcher.start()
        self.addCleanup(patcher.stop)


class DictationPauseTests(_Tmp):
    def test_the_watch_waits_while_the_microphone_still_hears_speech(self):
        # Фраза без пауз длиннее паузы: сторож закрывал диктовку на полуслове
        # («Нечего записывать»). Считаем от последнего звука, а не от конца
        # законченной фразы.
        loop = _цикл()
        loop._dictation = ["мысль, которая ещё говорится"]
        loop._dictation_at = 100.0
        loop._listener = NS(heard_at=118.0)
        with mock.patch("core.voice_loop.time.monotonic", return_value=120.0):
            loop._check_dictation_pause()
        self.assertIsNotNone(loop._dictation, "он говорит — тишины нет")
        self.assertEqual(self.loop_written, [])

    def test_before_the_first_phrase_it_waits_longer(self):
        # Сразу после «запиши заметку» он ещё формулирует: 20 с тишины — не
        # конец, полминуты — конец.
        loop = _цикл()
        loop._dictation = []
        loop._dictation_at = 100.0
        with mock.patch("core.voice_loop.time.monotonic", return_value=120.0):
            loop._check_dictation_pause()
        self.assertIsNotNone(loop._dictation, "до первой фразы ждём 30 с")
        with mock.patch("core.voice_loop.time.monotonic", return_value=131.0):
            loop._check_dictation_pause()
        self.assertIsNone(loop._dictation, "полминуты молчания — конец мысли")
        self.assertEqual(loop.said[-1], DICTATION_EMPTY)

    def test_silence_after_a_phrase_still_ends_it(self):
        loop = _цикл()
        loop._dictation = ["мысль про воланда"]
        loop._dictation_at = 100.0
        with mock.patch("core.voice_loop.time.monotonic", return_value=116.0):
            loop._check_dictation_pause()
        self.assertIsNone(loop._dictation)
        self.assertEqual(len(self.loop_written), 1)


class WindowTests(unittest.TestCase):
    def test_the_window_does_not_close_during_the_dictation(self):
        # 27.09: окно закрылось со звуком, 51 секунда диктовки пропала.
        loop = _цикл()
        loop._open = True
        loop._last_turn = 0.0
        loop._dictation = ["надиктовано"]
        with mock.patch("core.voice_loop.time.monotonic", return_value=10_000.0):
            loop._check_window()
        loop._close_conversation.assert_not_called()
        self.assertTrue(loop._open)

    def test_an_expired_window_still_closes_itself(self):
        loop = _цикл()
        loop._open = True
        loop._last_turn = 0.0
        with mock.patch("core.voice_loop.time.monotonic", return_value=10_000.0):
            loop._check_window()
        loop._close_conversation.assert_called_once()


class BrokenPhraseTests(unittest.TestCase):
    def test_one_bad_phrase_does_not_stop_the_voice(self):
        # GigaAM упал на куске: исключение вылетало из цикла, и голос
        # выключался до перезапуска. Теперь ошибка уходит в журнал, а следующая
        # фраза обрабатывается как обычно.
        loop = _цикл()
        фразы = ["первая", "вторая", "третья"]
        loop._listener = NS(phrases=lambda: iter(фразы))
        loop._handle = mock.Mock(side_effect=[None, OSError("распознавание упало"),
                                               None])
        loop._listen()
        self.assertEqual(loop._handle.call_count, 3, "третья фраза не обработана")
        ошибки = [text for kind, text in loop.events if kind == "error"]
        self.assertTrue(ошибки, "ошибка должна уйти в журнал")
        self.assertIn("фраза не обработана", ошибки[0])
        self.assertIn("OSError", ошибки[0])


# --- Отдельный вход в диктовку --------------------------------------------


class BeginDictationTests(_Tmp):
    def test_it_starts_without_saying_anything(self):
        # Слова «Диктуй…» в этом пути произносит модель своим ответом.
        loop = _цикл()
        self.assertTrue(loop.begin_dictation("по книге Мастер и Маргарита"))
        self.assertEqual(loop._dictation, [])
        self.assertEqual(loop._dictation_command, "по книге Мастер и Маргарита")
        self.assertEqual(loop.said, [], "begin_dictation не говорит")
        self.assertIn(("note", "диктовка начата"), loop.events)
        loop._open_conversation.assert_called_once()

    def test_a_voice_that_is_not_running_has_nobody_to_dictate_to(self):
        loop = _цикл(ready=False)
        self.assertFalse(loop.begin_dictation())
        self.assertIsNone(loop._dictation)
        self.assertEqual(loop.events, [])

    def test_a_second_call_does_not_wipe_what_was_dictated(self):
        loop = _цикл()
        loop._dictation = ["уже надиктовано"]
        self.assertTrue(loop.begin_dictation())
        self.assertEqual(loop._dictation, ["уже надиктовано"])

    def test_the_voice_command_speaks_after_it_starts(self):
        # Голосовая команда «запиши заметку» — тот же вход плюс короткая
        # реплика, иначе хозяин не поймёт, что его слушают.
        loop = _цикл()
        loop._start_dictation(NS(target="запиши заметку"), "запиши заметку")
        self.assertEqual(loop._dictation, [])
        self.assertEqual(loop.said, [DICTATION_START])


# --- Инструмент start_dictation -------------------------------------------


def _chunk(content=None, calls=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _мозг(actions=None):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client([[_chunk("Ок.")]])
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {} if actions is None else actions
    brain.last_sources = []
    return brain


def _имена(brain) -> list[str]:
    return [t["function"]["name"]
            for t in brain._client.bodies[0].get("tools", [])]


class DictationToolTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        patcher = mock.patch.object(hands, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved[0]))
        self.addCleanup(lambda: setattr(config, "TTS_ENGINE", self._saved[1]))

    def test_the_tool_is_right_after_read_notes_when_the_voice_can_dictate(self):
        brain = _мозг({"dictation": lambda hint="": True})
        list(brain.reply("сделаешь заметку небольшую?"))
        имена = _имена(brain)
        self.assertEqual(имена[имена.index(hands.READ_NAME) + 1], hands.DICTATE_NAME)

    def test_without_the_voice_action_there_is_no_tool(self):
        # Чат без голоса: обещать диктовку нельзя.
        brain = _мозг({})
        list(brain.reply("сделаешь заметку небольшую?"))
        self.assertNotIn(hands.DICTATE_NAME, _имена(brain))

    def test_it_runs_only_when_the_phrase_asks_for_a_note(self):
        # 26.09: инструменты даются всегда, и модель повторяла прошлую просьбу.
        # Теперь проверяется цитата: `because` обязан дословно взяться из
        # последней реплики, иначе диктовка не начинается.
        for фраза, ждём in (("сделаешь заметку небольшую?", True),
                            ("допиши в заметку про трубу", True),
                            ("запиши идею", True),
                            ("как дела?", False),
                            ("что нового?", False)):
            with self.subTest(фраза=фраза):
                # Цитата из прошлой реплики в эту не входит.
                цитата = фраза if ждём else "запиши идею"
                self.assertEqual(
                    hands.asked_for(hands.DICTATE_NAME, фраза, APPS,
                                    because=цитата), ждём)
        # Цитата из прошлой реплики в эту не входит — диктовки не будет.
        self.assertFalse(hands.asked_for(
            hands.DICTATE_NAME, "как дела?", APPS, because="запиши идею"))

    def test_the_confirmation_is_exactly_what_she_says_out_loud(self):
        # Короткий путь вместо второго круга: говорит голос, не модель.
        # Как у записи и YouTube, короткая фраза приходит полем `text` из
        # ответа инструмента, а не шаблоном здесь.
        результат = json.dumps({"ok": True, "text": DICTATION_START},
                                ensure_ascii=False)
        line = hands.confirm([{"name": hands.DICTATE_NAME, "args": "{}"}],
                             apps=APPS, results=[результат])
        self.assertEqual(line, DICTATION_START)

    def test_the_tool_starts_the_dictation_with_the_hint(self):
        начали = []

        def действие(hint=""):
            начали.append(hint)
            return True

        ответ = hands.run_dictation(json.dumps({"hint": "Проекты / Труба"}),
                                    {"dictation": действие})
        self.assertEqual(начали, ["Проекты / Труба"])
        self.assertEqual(json.loads(ответ)["text"], DICTATION_START)

    def test_a_voice_that_is_off_is_told_honestly(self):
        # Голос выключен — это ошибка, а не «записала»: модель спросит текст.
        ответ = hands.run_dictation("{}", {"dictation": lambda hint="": False})
        self.assertIn("Голос выключен", json.loads(ответ)["error"])

    def test_the_model_is_told_about_the_tool(self):
        описание = hands.DICTATE_TOOL["function"]["description"].lower()
        for слово in ("диктовку", "всё", "заметк"):
            self.assertIn(слово, описание)


# --- Голосовая команда «заметка» ------------------------------------------


class NoteCommandTests(unittest.TestCase):
    def _вид(self, фраза):
        order = commands.understand(фраза, APPS)
        self.assertIsNotNone(order, фраза)
        return order

    def test_more_living_ways_to_ask_for_a_note(self):
        # Ровно те формулировки, которые 27.09 ушли в модель вместо записи.
        # Короткой формы у них нет, и это осознанно: короткий список
        # мгновенных команд, а длинные фразы понимает модель.
        for фраза in ("сделай заметку", "сделаешь заметку",
                      "сделай небольшую заметку", "запиши текст",
                      "запиши мне заметку", "допиши в заметку про трубу",
                      "добавь в заметки", "запиши в заметки", "запиши идею"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_the_past_tense_and_questions_are_not_commands(self):
        # Прошедшее время и разговор ПРО заметку — не приказ.
        for фраза in ("ты записала это в заметки или нет?",
                      "спасибо за заметку",
                      "я вчера сделал заметку в блокноте",
                      "сделал заметку",
                      "где мои заметки?",
                      "прочитай заметку"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_the_old_ways_still_work(self):
        for фраза in ("запиши заметку", "давай заметку", "заметка про книги",
                      "запиши мысль по книге Мастер и Маргарита"):
            with self.subTest(фраза=фраза):
                self.assertEqual(self._вид(фраза).action, "note")

    def test_a_clip_is_still_a_moment(self):
        # «Клип» в таблице один — у момента. «Запиши клип» и «клипани» форм
        # не имеют и уходят модели.
        self.assertEqual(commands.understand("клип", APPS).action, "moment")
        self.assertEqual(commands.understand("сохрани клип", APPS).action,
                         "moment")
        self.assertEqual(commands.understand("сохрани момент", APPS).action,
                         "moment")
        for фраза in ("запиши клип", "клипани"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_the_guards_still_hold(self):
        # NOT_ORDER, QUOTED и MAX_WORDS — как у всех остальных команд.
        self.assertIsNone(commands.understand('я говорю: «запиши заметку»', APPS))
        long = ("запиши заметку и ещё раз скажи то же самое медленнее и ещё "
                "раз и ещё раз и ещё раз и ещё раз")
        self.assertIsNone(commands.understand(long, APPS))


# --- Старые разговоры отдельно от нового ----------------------------------


class HistoryGapTests(unittest.TestCase):
    @staticmethod
    def _когда(минут_назад: float) -> str:
        return (datetime.now() - timedelta(minutes=минут_назад)).isoformat(
            timespec="seconds")

    @staticmethod
    def _мозг(история) -> Brain:
        brain = object.__new__(Brain)
        brain._persona = "тест"
        brain._history = deque(история, maxlen=50)
        brain._abilities = lambda: ""
        brain._memory = lambda: ""
        return brain

    def _пометки(self, brain) -> list[str]:
        return [m["content"] for m in brain._messages("сейчас", None)
                if m.get("role") == "system"
                and "Дальше новый разговор" in m["content"]]

    def test_a_long_gap_is_marked_as_a_new_conversation(self):
        # Ночной рассказ про диктовку (02:14) и утренняя просьба: без пометки
        # модель повторяла слова из старого разговора вместо дела.
        brain = self._мозг([
            {"role": "user", "content": "вчера про диктовку",
             "at": self._когда(60 * 12)},
            {"role": "assistant", "content": "Да, слушаю.",
             "at": self._когда(60 * 12 - 1)},
            {"role": "user", "content": "сделаешь заметку небольшую?",
             "at": self._когда(1)},
        ])
        пометки = self._пометки(brain)
        self.assertEqual(len(пометки), 1, пометки)
        self.assertIn("прошлые разговоры", пометки[0])

    def test_a_short_gap_is_not_marked(self):
        brain = self._мозг([
            {"role": "user", "content": "как дела", "at": self._когда(20)},
            {"role": "assistant", "content": "Нормально.", "at": self._когда(19)},
            {"role": "user", "content": "а ты можешь заметку", "at": self._когда(1)},
        ])
        self.assertEqual(self._пометки(brain), [])

    def test_turns_without_a_time_get_no_mark(self):
        # Время неизвестно — сравнивать нечего, и выдумывать его нельзя.
        brain = self._мозг([
            {"role": "user", "content": "без времени"},
            {"role": "assistant", "content": "Ок."},
            {"role": "user", "content": "и это тоже", "at": self._когда(1)},
        ])
        self.assertEqual(self._пометки(brain), [])

    def test_the_same_history_always_gives_the_same_messages(self):
        # Пометки детерминированы историей — иначе сдвигался бы кеш начала
        # запроса на каждом ответе.
        история = [
            {"role": "user", "content": "ночью", "at": self._когда(60 * 9)},
            {"role": "assistant", "content": "Слушаю.",
             "at": self._когда(60 * 9 - 1)},
            {"role": "user", "content": "утром", "at": self._когда(2)},
        ]
        первый = self._мозг(история)._messages("сейчас", None)
        второй = self._мозг(история)._messages("сейчас", None)
        # Часы в хвосте меняются минута за минутой, сравниваем начало.
        self.assertEqual(первый[:4], второй[:4])


if __name__ == "__main__":
    unittest.main()
