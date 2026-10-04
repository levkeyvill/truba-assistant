"""Расход в токенах и начало запроса, которое не ползёт: без сети.

Проверяем, что журнал показывает, сколько стоил ответ и сколько из этого
провайдер взял из кеша, и что история режется пачкой — начало запроса
не съезжает на каждом ответе.
"""

import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import memory, web
from core.brain import Brain, HISTORY_KEEP_BACK, read_usage


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


def _spent(prompt=0, cached=None, completion=0, hit=None, details=True):
    """Кусок потока с расходом. Такой приходит последним, с пустым choices."""
    if details and cached is not None:
        usage = NS(prompt_tokens=prompt, completion_tokens=completion,
                   prompt_tokens_details=NS(cached_tokens=cached))
    elif hit is not None:
        # DeepSeek: попадание в кеш отдельным полем.
        usage = NS(prompt_tokens=prompt, completion_tokens=completion,
                   prompt_tokens_details=None, prompt_cache_hit_tokens=hit)
    else:
        usage = NS(prompt_tokens=prompt, completion_tokens=completion,
                   prompt_tokens_details=None)
    return NS(choices=[], usage=usage)


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams, provider="deepseek"):
    brain = object.__new__(Brain)
    brain.provider = provider
    brain._home = provider
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(streams)
    brain._reply_lock = threading.RLock()
    brain._history = deque()
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {}
    return brain


class TokenUsageTests(unittest.TestCase):
    """usage приходит с пустым choices — на нём разбор не должен падать."""

    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_last_chunk_with_usage_does_not_break_the_answer(self):
        brain = _brain([[_chunk("Привет, кожаный, я тут и слушаю тебя. "),
                         _chunk("Как сам, кожаный?"),
                         _spent(prompt=3120, cached=2800, completion=45)]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        said = list(brain.reply("привет"))
        self.assertEqual(said, ["Привет, кожаный, я тут и слушаю тебя.",
                                    "Как сам, кожаный?"])
        self.assertEqual(events[-1], ("tokens", {
            "prompt": 3120, "cached": 2800, "completion": 45, "calls": 1,
            "model": "deepseek/test"}))
        # Расход просим явно: без stream_options провайдер его не пришлёт.
        self.assertEqual(brain._client.bodies[0]["stream_options"],
                         {"include_usage": True})

    def test_deepseek_cache_field_is_understood(self):
        brain = _brain([[_chunk("Ок."), _spent(prompt=500, hit=400, completion=3,
                                               details=False)]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        list(brain.reply("ок"))
        self.assertEqual(events[-1][1]["cached"], 400)

    def test_provider_without_usage_still_answers_and_says_nothing(self):
        brain = _brain([[_chunk("Просто да, я и не умею иначе. "),
                         _chunk("Без расхода вовсе.")]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        said = list(brain.reply("скажи что-нибудь"))
        self.assertEqual(said, ["Просто да, я и не умею иначе.",
                                    "Без расхода вовсе."])
        self.assertEqual([k for k, _ in events if k == "tokens"], [])

    def test_provider_rejecting_stream_options_is_remembered(self):
        import httpx
        from openai import BadRequestError

        class _Picky:
            def __init__(self):
                self.bodies = []
                self.chat = NS(completions=NS(create=self._create))

            def _create(self, **body):
                self.bodies.append(body)
                if "stream_options" in body:
                    request = httpx.Request("POST", "https://api.example/v1")
                    raise BadRequestError(
                        "Error code: 400 - Unknown parameter: 'stream_options'",
                        response=httpx.Response(400, request=request), body=None)
                return iter([_chunk("Ок."), _spent(prompt=10, completion=1)])

        brain = _brain([])
        picky = _Picky()
        brain._client = picky
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        self.assertEqual(list(brain.reply("ок")), ["Ок."])
        # Отказ не сорвал ответ, а расход вернулся со второго запроса.
        self.assertEqual(events[-1][1]["prompt"], 10)
        self.assertIn("stream_options", picky.bodies[0])
        self.assertNotIn("stream_options", picky.bodies[1])
        self.assertIs(brain._usage_ok, False)
        # Следующий ответ сразу без stream_options — отказ не повторяем.
        list(brain.reply("и ещё"))
        self.assertNotIn("stream_options", picky.bodies[2])

    def test_two_tool_rounds_sum_into_one_event(self):
        search = [_chunk(calls=[_call(0, "c1", "web_search", '{"query": "курс"}')]),
                  _spent(prompt=800, cached=700, completion=10)]
        answer = [_chunk("Восемьдесят четыре рубля девяносто копеек. "),
                  _chunk("Как сам, кожаный?"),
                  _spent(prompt=1000, cached=900, completion=20)]
        brain = _brain([search, answer])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch.object(config, "WEB_SEARCH", True, create=True), \
                mock.patch.object(web, "run_tool", return_value='{"results": []}'):
            said = list(brain.reply("найди курс доллара"))
        self.assertEqual(said[-2:], ["Восемьдесят четыре рубля девяносто копеек.",
                                   "Как сам, кожаный?"])
        # Кругов два, событие одно — сумма по обоим.
        self.assertEqual(events[-1], ("tokens", {
            "prompt": 1800, "cached": 1600, "completion": 30, "calls": 2,
            "model": "deepseek/test"}))
        self.assertEqual(len(brain._client.bodies), 2)

    def test_repeated_usage_chunks_are_not_counted_twice(self):
        """usage — итог за круг, а не за кусок: повтор не удваивает."""
        brain = _brain([[_chunk("Слушаю тебя, кожаный, я весь внимание."),
                         _spent(prompt=100, cached=80, completion=5),
                         _spent(prompt=100, cached=80, completion=5)]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        list(brain.reply("слушай"))
        self.assertEqual(events[-1][1], {
            "prompt": 100, "cached": 80, "completion": 5, "calls": 1,
            "model": "deepseek/test"})

    def test_interrupted_answer_does_not_invent_spending(self):
        """Голос перебил на полуслове: расхода не было — значит и события нет."""
        def endless():
            yield _chunk("Начало длинного предложения, кожаный, я сейчас расскажу. ")
            while True:
                yield _chunk("и продолжаю очень длинным предложением, ")

        brain = _brain([endless()])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        stream = brain.reply("расскажи длинную историю")
        next(stream)   # первое предложение ушло в синтез
        stream.close()  # голос перебил
        self.assertEqual([k for k, _ in events if k == "tokens"], [])

class MemoryUsageTests(unittest.TestCase):
    """Выжимка памяти — тоже расход, и в журнале это видно отдельно."""

    def setUp(self):
        brain = object.__new__(Brain)
        brain.provider = "deepseek"
        brain._home = "deepseek"
        brain._home_at = 0.0
        brain._model = "test"
        brain._quiet = None
        brain._usage_ok = None
        brain._history = deque()
        brain._undigested = 0
        brain._reply_lock = threading.RLock()
        brain.did_ask = False
        answer = NS(choices=[NS(message=NS(content='{"ops": []}'))],
                    usage=NS(prompt_tokens=400, completion_tokens=20,
                             prompt_tokens_details=NS(cached_tokens=300)))
        brain._client = NS(chat=NS(completions=NS(create=lambda **b: answer)))
        self.brain = brain

    def _digest(self, say):
        self.brain._history = deque([
            {"role": "user", "content": say},
            {"role": "assistant", "content": "Понял, кожаный."},
        ])
        self.brain._undigested = 2
        self.brain.did_ask = False
        events = []
        self.brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch.object(memory, "numbered", return_value=""), \
                mock.patch.object(memory, "apply_changes", return_value={}):
            self.brain.digest()
        return [p for k, p in events if k == "tokens"]

    def test_digest_marks_its_tokens_as_memory(self):
        self.assertEqual(
            self._digest("Я купил кота, зовут Мурзик, и он очень серый, listrik на подоконнике."),
            [{"prompt": 400, "cached": 300, "completion": 20, "calls": 1,
              "model": "deepseek/test", "note": "память"}])

    def test_short_talk_costs_nothing(self):
        self.assertEqual(self._digest("привет"), [])


class UsageParsingTests(unittest.TestCase):
    def test_missing_fields_are_zero(self):
        self.assertEqual(read_usage(NS()), {"prompt": 0, "cached": 0,
                                            "completion": 0})

    def test_cached_tokens_win_over_cache_hit(self):
        usage = NS(prompt_tokens=10, completion_tokens=1,
                   prompt_tokens_details=NS(cached_tokens=7),
                   prompt_cache_hit_tokens=9)
        self.assertEqual(read_usage(usage)["cached"], 7)


class HistoryTrimTests(unittest.TestCase):
    """История режется пачкой: начало запроса держится на месте."""

    def setUp(self):
        self._saved = (config.HISTORY_TURNS, config.WEB_SEARCH,
                      config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"

    def tearDown(self):
        (config.HISTORY_TURNS, config.WEB_SEARCH,
         config.TTS_ENGINE) = self._saved

    @staticmethod
    def _fill(brain, turns, word="реплика"):
        for i in range(turns):
            brain._add_turn(f"{word} хозяина {i}", f"{word} её ответ {i}")

    def test_overflow_cuts_a_batch_not_one_turn(self):
        config.HISTORY_TURNS = 24
        brain = _brain([])
        self._fill(brain, 12)  # ровно 24 реплики
        self.assertEqual(len(brain._history), 24)
        self._fill(brain, 1)   # 26 — перебор
        self.assertEqual(len(brain._history), 24 - HISTORY_KEEP_BACK)
        # Дальше растёт снова до потолка и режется следующим ответом.
        self._fill(brain, 4)
        self.assertEqual(len(brain._history), 24)
        self._fill(brain, 1)
        self.assertEqual(len(brain._history), 24 - HISTORY_KEEP_BACK)

    def test_message_start_stays_whole_between_neighbours(self):
        config.HISTORY_TURNS = 24
        brain = _brain([[_chunk("Первый ответ."), _spent(prompt=1, completion=1)],
                        [_chunk("Второй ответ."), _spent(prompt=1, completion=1)]])
        brain._history = deque([
            {"role": "user", "content": "было начало разговора"},
            {"role": "assistant", "content": "и ответ на него"},
        ])
        list(brain.reply("давай раз"))
        first = brain._client.bodies[0]["messages"]
        list(brain.reply("и ещё раз"))
        second = brain._client.bodies[1]["messages"]
        # Хвост (время, умения, память) и сама реплика меняются, начало — нет:
        # ровно то, за что кеш префикса и платит вдвое дешевле.
        start = [first[0], first[1], first[2]]
        self.assertEqual(second[:3], start)
        self.assertEqual(first[:3], start)
        self.assertNotEqual(first[-2], second[-2])  # хвост со временем
        self.assertNotEqual(first[-1]["content"], second[-1]["content"])

    def test_undigested_never_points_outside_history(self):
        config.HISTORY_TURNS = 12
        brain = _brain([])
        self._fill(brain, 8)  # 16 реплик при потолке 12
        self.assertLessEqual(brain._undigested, len(brain._history))
        fresh = list(brain._history)[-brain._undigested:]
        self.assertEqual(fresh[-1]["content"], "реплика её ответ 7")
        self.assertNotIn("реплика хозяина 0", [t["content"] for t in fresh])

    def test_limit_below_back_leaves_at_least_four(self):
        config.HISTORY_TURNS = 6
        brain = _brain([])
        self._fill(brain, 4)  # 8 реплик при потолке 6
        self.assertEqual(len(brain._history), 4)


class TokenLabelTests(unittest.TestCase):
    """Подпись события в журнале показывает источник расхода."""

    def test_label_says_prompt_cached_and_completion(self):
        from ui.web_runtime import WebRuntime

        lines = WebRuntime._log_messages("tokens", {
            "prompt": 3120, "cached": 2800, "completion": 45, "calls": 1,
            "model": "openai/gpt-5.6-luna"})
        self.assertEqual(lines, ["токены: 3120 на входе (2800 из кеша), "
                                 "45 на выходе"])

    def test_memory_digest_is_marked(self):
        from ui.web_runtime import WebRuntime

        lines = WebRuntime._log_messages("tokens", {
            "prompt": 400, "cached": 0, "completion": 20, "calls": 1,
            "model": "deepseek/test", "note": "память"})
        self.assertIn("выжимка памяти", lines[0])
        self.assertIn("400 на входе", lines[0])


if __name__ == "__main__":
    unittest.main()
