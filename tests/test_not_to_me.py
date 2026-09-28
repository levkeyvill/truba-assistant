"""Имя прозвучало, а говорили не с ней: она молчит, окно разговора закрыто.

28.09 в 23:22 хозяин говорил о программе «Труба» (он её автор), а не с ней:
регулярка имени слово нашла, смысл не поняла, она извинилась, что влезла, а
потом выжимка памяти записала его слова как факт о нём. Смысл решает модель,
шаблонов фраз тут нет — есть один инструмент на её выбор.

Проверяются обе половины. Мозг: инструмент появляется только на ходе, прошедшем
из-за имени, и её молчание не попадает ни в историю, ни в память. Голосовой цикл:
флаг `named` доходит от `_turn_body` до мозга, а молчание закрывает окно. Облако
и звук подменены — поток ответов заготовкой, динамик заглушкой.
"""

import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS

import numpy as np

import config
from core.brain import NOT_TO_ME_NAME, Brain
from core.voice_loop import VoiceLoop

# Полсекунды тишины: `_turn_body` меряет фразу, чтобы положить в событие.
ФРАЗА = np.zeros(int(config.SAMPLE_RATE * 0.5), dtype=np.float32)


# --- Подставное облако ------------------------------------------------------


def _чан(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _вызов(имя, аргументы="{}"):
    return _чан(calls=[NS(index=0, id="c1", function=NS(name=имя, arguments=аргументы))])


class _Client:
    """Отдаёт заготовленные потоки и помнит тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _мозг(*потоки):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(потоки)
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    # Настоящий `_keep_history` писал бы в живую data/history.json.
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {}
    brain.last_sources = []
    return brain


def _имён(brain, index=0):
    return [t["function"]["name"] for t in brain._client.bodies[index].get("tools", [])]


def _круги(brain):
    """Сколько раз уходили в облако за весь ответ."""
    return len(brain._client.bodies)



# --- Инструмент даётся только ходу с именем --------------------------------


class НаборИнструментовTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.WEB_SEARCH
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))

    def test_the_name_offers_it(self):
        config.WEB_SEARCH = False
        brain = _мозг([_чан("Договорились.")])
        list(brain.reply("Труба, глянь, что там с задачей", named=True))
        self.assertIn(NOT_TO_ME_NAME, _имён(brain))

    def test_it_is_the_last_tool_so_the_cache_stands(self):
        # Условные инструменты — в конце набора: начало списка и кеш запроса
        # от них не зависят, как у END_TOOL.
        config.WEB_SEARCH = False
        brain = _мозг([_чан("Договорились.")])
        list(brain.reply("Труба, глянь, что там с задачей", named=True))
        self.assertEqual(_имён(brain)[-1], NOT_TO_ME_NAME)

    def test_without_the_name_there_is_nothing_to_decide(self):
        config.WEB_SEARCH = False
        brain = _мозг([_чан("Нормально.")])
        list(brain.reply("как дела?"))
        self.assertNotIn(NOT_TO_ME_NAME, _имён(brain))

    def test_a_search_turn_has_only_the_internet(self):
        # Человек нажал «найти» и сказал, чего хочет: молчать тут незачем.
        config.WEB_SEARCH = True
        brain = _мозг([_чан("Вот что нашёл.")])
        list(brain.reply("найди курс доллара", search=True, named=True))
        self.assertNotIn(NOT_TO_ME_NAME, _имён(brain))

    def test_the_first_talk_has_no_tools_at_all(self):
        config.WEB_SEARCH = False
        brain = _мозг([_чан("Ну что, сидим?")])
        list(brain.reply("Заговори с ним первой", first="(хозяин молчал 30 мин)",
                         named=True))
        self.assertIsNone(brain._client.bodies[0].get("tools", None))


# --- Молчание не оставляет следа --------------------------------------------


class МолчаниеTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))

    def test_a_silent_verdict_leaves_nothing_behind(self):
        # 28.09, 23:22: так она записала в память слова хозяина о его же
        # программе. Молчание — не реплика: ни в историю, ни в счётчик.
        brain = _мозг([_вызов(NOT_TO_ME_NAME)])
        said = list(brain.reply("Труба, я доделываю трубу", named=True))
        self.assertEqual(said, [])
        self.assertTrue(brain.not_to_me)
        self.assertEqual(len(brain._history), 0)
        self.assertEqual(brain._undigested, 0)

    def test_a_silent_verdict_costs_one_round(self):
        # Второго круга нет: круг прерван, как на end_conversation.
        brain = _мозг([_вызов(NOT_TO_ME_NAME)])
        list(brain.reply("Труба, я доделываю трубу", named=True))
        self.assertEqual(_круги(brain), 1)

    def test_what_she_said_anyway_is_kept(self):
        # Сказала вопреки описанию — это уже прозвучало, и в историю идёт.
        brain = _мозг([_чан("Труба — это твоя программа."),
                       _вызов(NOT_TO_ME_NAME)])
        said = list(brain.reply("Труба, это твоя программа", named=True))
        self.assertEqual(said, ["Труба — это твоя программа."])
        self.assertTrue(brain.not_to_me)
        self.assertEqual([t["role"] for t in brain._history], ["user", "assistant"])
        self.assertEqual(brain._undigested, 2)

    def test_the_next_answer_forgets_it(self):
        brain = _мозг([_вызов(NOT_TO_ME_NAME)],
                      [_чан("Нормально, кожаный.")])
        list(brain.reply("Труба, я доделываю трубу", named=True))
        self.assertTrue(brain.not_to_me)
        list(brain.reply("как дела?", aloud=True))
        self.assertFalse(brain.not_to_me)


# --- Голосовой цикл ---------------------------------------------------------


class _Тихо:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Голос:
    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.zeros(240, dtype=np.float32), 24000


class _Динамик:
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


class _Мозг:
    """Мозг, который помнит, с какими флагами его позвали."""

    ended = False
    last_sources = None

    def __init__(self, sentences=(), not_to_me=False):
        self.sentences = list(sentences)
        self.not_to_me = not_to_me
        self.calls = []

    def reply(self, text, **kwargs):
        self.calls.append((text, kwargs))
        self.last_timing = {"rounds": [{"sent": 0.0, "word": 0.0, "sentence": 0.0}]}
        yield from self.sentences


def _цикл(мозг):
    """Голосовой цикл из одних заглушек: ни сети, ни звука, ни облака."""
    loop = object.__new__(VoiceLoop)
    loop._brain = мозг
    loop._voice = _Голос()
    loop._ref = None
    loop._speaker = _Динамик()
    loop._fallback = None
    loop._server = None
    loop._listener = _Тихо()
    loop._ducker = _Тихо()
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._open = False
    loop._last_turn = 0.0
    loop._speaking_text = ""
    loop._spoke_at = 0.0
    loop._first_pending = False
    loop._search_next = False
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._digest_later = lambda: None
    return loop


def _ход():
    """Цикл, у которого видно только флаги, с какими позван мозг."""
    loop = object.__new__(VoiceLoop)
    loop.звонки = []
    loop._search_next = False
    loop._dictation = None
    loop._interrupt = threading.Event()
    loop._known_apps = lambda: []
    loop._voice_score = None
    loop._emit = lambda kind, payload: None
    loop._open_conversation = lambda: None
    loop._run_command = lambda order, text: None
    loop._answer = lambda text, **kw: loop.звонки.append((text, kw))
    return loop


class ФлагиХодаTests(unittest.TestCase):
    def _звонок(self, loop):
        self.assertEqual(len(loop.звонки), 1)
        return loop.звонки[0][1]

    def test_a_phrase_through_the_name_gate_is_marked(self):
        loop = _ход()
        loop._turn_body("Труба, я доделываю трубу", ФРАЗА, 0.0, 0.0, False)
        self.assertIs(self._звонок(loop)["named"], True)

    def test_in_an_open_window_the_name_is_not_needed(self):
        # Разговор и так идёт: имя в фразе не причина, по которой её услышали.
        loop = _ход()
        loop._turn_body("Труба, ну как там с задачей", ФРАЗА, 0.0, 0.0, True)
        self.assertFalse(self._звонок(loop)["named"])

    def test_without_the_name_there_is_nothing_to_solve(self):
        loop = _ход()
        loop._turn_body("ну как там с задачей", ФРАЗА, 0.0, 0.0, False)
        self.assertFalse(self._звонок(loop)["named"])


class АргументыМозгаTests(unittest.TestCase):
    def test_the_search_flag_reaches_the_brain(self):
        # Кнопка «найти»: флаг уходил только в карточку телефона, а мозг о
        # нём не узнавал — и не искал.
        loop = _цикл(_Мозг(["Вот курс."]))
        loop._answer("курс доллара", search=True)
        self.assertIs(loop._brain.calls[0][1]["search"], True)

    def test_the_named_flag_reaches_the_brain(self):
        loop = _цикл(_Мозг())
        loop._answer("Труба, я доделываю трубу", named=True)
        self.assertIs(loop._brain.calls[0][1]["named"], True)

    def test_an_ordinary_turn_gets_neither_key(self):
        loop = _цикл(_Мозг(["Нормально."]))
        loop._answer("как дела?")
        self.assertNotIn("search", loop._brain.calls[0][1])
        self.assertNotIn("named", loop._brain.calls[0][1])


class ОкноПослеМолчанияTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.OUTPUT
        config.OUTPUT = "speakers"
        self.addCleanup(lambda: setattr(config, "OUTPUT", self._saved))

    def _открыт(self, loop):
        loop._open = True
        loop._last_turn = 1.0

    def test_her_silence_closes_the_window(self):
        # Иначе следующие три минуты чужая речь шла бы без имени.
        loop = _цикл(_Мозг(not_to_me=True))
        self._открыт(loop)
        loop._answer("Труба, я доделываю трубу", named=True)
        self.assertEqual([p for k, p in loop.events if k == "ignored"],
                         [{"text": "Труба, я доделываю трубу",
                           "why": "не ей — решила модель"}])
        self.assertFalse(loop._open)
        self.assertNotIn("ended_by_model", [k for k, _ in loop.events])

    def test_nothing_is_spoken_out_loud(self):
        # «Труба» — программа хозяина, а не он звал её: прощаться не с кем.
        loop = _цикл(_Мозг(not_to_me=True))
        self._открыт(loop)
        loop._answer("Труба, я доделываю трубу", named=True)
        self.assertEqual([p for k, p in loop.events if k == "sentence"], [])

    def test_what_she_said_anyway_leaves_the_window_alone(self):
        loop = _цикл(_Мозг(["Это твоя программа."], not_to_me=True))
        self._открыт(loop)
        loop._answer("Труба, это твоя программа", named=True)
        self.assertEqual([p for k, p in loop.events if k == "ignored"], [])
        self.assertTrue(loop._open)

    def test_the_journal_writes_it_like_the_other_skips(self):
        # Пульт печатает `ignored` сам, дописывая «пропустил [причина]».
        from ui.web_runtime import WebRuntime

        self.assertIn("не ей — решила модель",
                      WebRuntime._log_messages(
                          "ignored",
                          {"text": "Труба, я доделываю трубу",
                           "why": "не ей — решила модель"})[0])


if __name__ == "__main__":
    unittest.main()

