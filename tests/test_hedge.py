"""Страховка от заминок облака и тёплое соединение — без сети.

Замер: у разных провайдеров встречаются задержки около 6 с
до первого слова. Здесь проверяем, что при HEDGE_AFTER секунд молчания тот
же вопрос уходит запасному, отвечает кто пришёл первым, а поток
проигравшего закрывается. И что подогрев шлёт `models.list` раз в
интервал, пока голос включён, и останавливается вместе с ним.
"""

import os
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import brain as brain_module
from core import web
from core.brain import Brain, KeepWarm


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


class _Slow:
    """Поток, который думает перед первым куском. Спит — и это видно."""

    def __init__(self, chunks, delay=0.0):
        self.chunks = list(chunks)
        self.delay = delay
        self.closed = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.delay:
            time.sleep(self.delay)
            self.delay = 0.0
        if not self.chunks:
            raise StopIteration
        return self.chunks.pop(0)

    def close(self):
        self.closed += 1


def _spent(prompt=0, cached=0, completion=0):
    """Кусок потока с расходом: приходит последним, с пустым choices."""
    usage = NS(prompt_tokens=prompt, completion_tokens=completion,
               prompt_tokens_details=NS(cached_tokens=cached))
    return NS(choices=[], usage=usage)


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.calls = 0
        # Что действительно отдали: по нему видно, чей поток закрыли.
        self.opened: list = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.calls += 1
        self.bodies.append(body)
        stream = self.streams.pop(0)
        # Настоящий поток умеет close(); поддельный список — нет.
        if not hasattr(stream, "close"):
            stream = iter(stream)
        self.opened.append(stream)
        return stream


def _brain(streams, provider="openai"):
    brain = object.__new__(Brain)
    brain.provider = provider
    brain._home = provider
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(streams)
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
    brain._hedge_spare = None
    brain._hedge_model = None
    brain._warm = None
    return brain


ANSWER = [_chunk("Курс доллара сегодня восемьдесят четыре рубля девяносто копеек. "),
          _chunk("Такие дела.")]
SPARE_ANSWER = [_chunk("Отвечает запасной, восемьдесят четыре девяносто. "),
                _chunk("И это точно.")]


def _said(chunks):
    """Что прозвучит из этих кусков: текст без хвостовых пробелов."""
    return [c.choices[0].delta.content.strip() for c in chunks]


class HedgeTests(unittest.TestCase):
    """Страховка: галочка «Страховка от заминок облака»."""

    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE, config.HEDGE,
                       config.HEDGE_AFTER)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        config.HEDGE = True
        config.HEDGE_AFTER = 0.2
        self.env = mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        (config.WEB_SEARCH, config.TTS_ENGINE, config.HEDGE,
         config.HEDGE_AFTER) = self._saved

    def test_fast_answer_never_touches_the_spare(self):
        brain = _brain([[_chunk("Привет, кожаный.")]])
        spare = _Client([])
        with mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("привет"))
        self.assertEqual(said, ["Привет, кожаный."])
        self.assertEqual(spare.calls, 0)

    def test_silent_main_answers_through_spare_and_is_closed(self):
        # Основной молчит дольше HEDGE_AFTER: отвечает запасной, а поток
        # основного закрывается — его ответ выбрасывается.
        main = _Slow(ANSWER, delay=5.0)
        brain = _brain([main])
        spare = _Client([list(SPARE_ANSWER) + [_spent(prompt=10, cached=8,
                                                      completion=4)]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("какой курс доллара?"))
        self.assertEqual(said, _said(SPARE_ANSWER))
        self.assertEqual(spare.calls, 1)
        self.assertGreaterEqual(main.closed, 1)
        # Срабатывание страховки отражается в журнале.
        hedge = [e for e in events if e[0] == "hedge"]
        self.assertEqual(len(hedge), 1)
        self.assertEqual(hedge[0][1]["from"], "openai")
        self.assertEqual(hedge[0][1]["to"], "deepseek")
        self.assertEqual(hedge[0][1]["winner"], "spare")
        # Победителя называет и расход.
        tokens = [e for e in events if e[0] == "tokens"]
        self.assertTrue(tokens)
        self.assertEqual(tokens[-1][1]["model"], "deepseek/deepseek-flash")

    def test_spare_parameters_are_its_own(self):
        # Запасной получает max_tokens, а не max_completion_tokens, и без
        # reasoning_effort: это поле знает только прямой OpenAI.
        brain = _brain([_Slow([], delay=5.0)])
        spare = _Client([list(ANSWER)])
        with mock.patch("openai.OpenAI", return_value=spare):
            list(brain.reply("привет"))
        body = spare.bodies[0]
        self.assertIn("max_tokens", body)
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["stream_options"], {"include_usage": True})

    def test_slow_both_times_main_still_wins_and_spare_is_closed(self):
        # Оба медленные, но основной успел раньше: отвечает он, поток
        # запасного закрыт.
        main = _Slow(ANSWER + [_spent(prompt=20, cached=15, completion=6)],
                     delay=0.35)
        brain = _brain([main])
        spare = _Client([_Slow(list(SPARE_ANSWER), delay=5.0)])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("какой курс доллара?"))
        self.assertEqual(said, _said(ANSWER))
        self.assertEqual(spare.calls, 1)
        self.assertEqual(spare.opened[0].closed, 1)
        hedge = [e for e in events if e[0] == "hedge"]
        self.assertEqual(len(hedge), 1)
        self.assertEqual(hedge[0][1]["winner"], "main")
        # Расход называет того, кто реально ответил.
        tokens = [e for e in events if e[0] == "tokens"]
        self.assertEqual(tokens[-1][1]["model"], "openai/test")

    def test_no_hedge_after_tools(self):
        # Во втором круге история с вызовами инструментов собрана под
        # основного провайдера: страховки нет.
        search = [_chunk(calls=[_call(0, "c1", "web_search", '{"query": "курс"}')])]
        brain = _brain([search, list(ANSWER)])
        spare = _Client([])
        with mock.patch.object(web, "run_tool", return_value='{"results": []}'), \
                mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("найди курс доллара"))
        self.assertIn("Такие дела.", said)
        self.assertEqual(spare.calls, 0)
        # Второй круг ушёл основному, а не запасному.
        self.assertEqual(brain._client.bodies[1]["messages"][-1]["role"], "tool")

    def test_hedge_winner_tool_calls_go_to_main_as_text(self):
        # Ответила страховка, и модель зовёт инструмент: основной её вызовы
        # принять может не всегда, поэтому на следующем круге отдаём
        # найденное текстом — как при отказе провайдера.
        search = [_chunk(calls=[_call(0, "c1", "web_search", '{"query": "курс"}')])]
        # Основной в первом круге молчит, запасной отвечает и зовёт поиск.
        brain = _brain([_Slow(list(ANSWER), delay=5.0), list(ANSWER)])
        spare = _Client([list(SPARE_ANSWER) + search])
        with mock.patch.object(web, "run_tool", return_value='{"results": ["84,9"]}'), \
                mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("найди курс доллара"))
        self.assertIn("восемьдесят четыре девяносто", " ".join(said).lower())
        # Второй круг ушёл основному (он же и остался текущим).
        self.assertEqual(brain._client.calls, 2)
        sent = brain._client.bodies[1]["messages"]
        # Вызовы запасного основному не отдаём — только найденное текстом.
        self.assertFalse(any(m.get("role") == "tool" or m.get("tool_calls")
                             for m in sent))
        self.assertIn("84,9", " ".join(str(m.get("content") or "") for m in sent))

    def test_without_spare_key_only_main(self):
        brain = _brain([list(ANSWER)])
        keys = {name: "" for name in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
                                      "MINIMAX_API_KEY")}
        with mock.patch.dict(os.environ, keys, clear=False), \
                mock.patch("openai.OpenAI", return_value=_Client([])):
            said = list(brain.reply("какой курс доллара?"))
        self.assertIn("Такие дела.", said)
        self.assertEqual(brain._client.bodies[0]["model"], "test")

    def test_hedge_off_means_no_spare_request(self):
        config.HEDGE = False
        brain = _brain([_Slow(ANSWER, delay=0.4)])
        spare = _Client([])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("какой курс доллара?"))
        # Основной «думает» 0.4 с, но страховка выключена — ждём его.
        self.assertEqual(spare.calls, 0)
        self.assertEqual([e for e in events if e[0] == "hedge"], [])
        self.assertEqual(said, _said(ANSWER))

    def test_main_region_block_still_falls_back_whole(self):
        # Основной не пускает из страны: это не заминка, а отказ, и страховка
        # его не подменяет — уходим на запасного целиком, как и без неё.
        import httpx
        from openai import PermissionDeniedError

        class _Blocked:
            def __init__(self):
                self.chat = NS(completions=NS(create=self._create))

            def _create(self, **body):
                request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
                raise PermissionDeniedError(
                    "Error code: 403 - unsupported_country_region_territory",
                    response=httpx.Response(403, request=request), body=None)

        brain = _brain([])
        brain._client = _Blocked()
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        spare = _Client([list(ANSWER)])
        with mock.patch("openai.OpenAI", return_value=spare):
            said = list(brain.reply("какой курс доллара?"))
        self.assertIn("Такие дела.", said)
        self.assertEqual(brain.provider, "deepseek")
        self.assertIn(("provider_fallback",
                       {"from": "openai", "to": "deepseek", "why": "region"}),
                      events)
        # Страховка не сработала: отказ был сразу, ждать было нечего.
        self.assertEqual([e for e in events if e[0] == "hedge"], [])

    def test_defaults(self):
        # Без включённой настройки второй платный запрос не отправляется.
        from core import settings

        self.assertEqual(brain_module.HEDGE_AFTER, 2.5)
        self.assertIn("hedge", settings.DEFAULTS)
        self.assertFalse(settings.DEFAULTS["hedge"])


class _Models:
    """Поддельный models.list: считает запросы и умеет «не отвечать»."""

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def list(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("сети нет")
        return NS(data=[])


class KeepWarmTests(unittest.TestCase):
    """Подогрев: GET /models раз в интервал, пока голос включён."""

    def test_pings_repeatedly_and_stops(self):
        models = _Models()
        warm = KeepWarm(NS(models=models), every=0.02)
        warm.start()
        try:
            time.sleep(0.25)
            self.assertGreaterEqual(models.calls, 3)
        finally:
            warm.stop()
        self.assertIsNone(warm._thread)
        # Остановился вместе с голосом: счётчик больше не растёт.
        after_stop = models.calls
        time.sleep(0.12)
        self.assertEqual(models.calls, after_stop)

    def test_errors_are_swallowed(self):
        models = _Models(fail=True)
        warm = KeepWarm(NS(models=models), every=0.02)
        warm.start()
        try:
            time.sleep(0.15)
        finally:
            warm.stop()
        # Подогрев не должен ничего ломать и не считается успехом.
        self.assertGreater(models.calls, 0)
        self.assertEqual(warm.pings, 0)

    def test_starts_with_voice_and_stops_with_it(self):
        brain = _brain([_chunk("Ок.")])
        models = _Models()
        brain._client.models = models
        brain.warm_start(every=0.02)
        self.assertIsInstance(brain._warm, KeepWarm)
        try:
            time.sleep(0.15)
            self.assertGreater(models.calls, 0)
        finally:
            brain.warm_stop()
        self.assertIsNone(brain._warm)
        after_stop = models.calls
        time.sleep(0.1)
        self.assertEqual(models.calls, after_stop)

    def test_switch_warms_the_new_provider(self):
        brain = _brain([_chunk("Ок.")])
        warm = KeepWarm(NS(models=_Models()), every=0.02)
        brain._warm = warm
        other = _Models()
        with mock.patch.object(brain_module, "_client", return_value=NS(models=other)):
            brain._switch("deepseek")
        # Греется тот, кто реально отвечает.
        self.assertIs(warm._client, brain._client)
        self.assertIs(brain._client.models, other)

    def test_default_interval(self):
        self.assertEqual(brain_module.WARM_EVERY, 45.0)


if __name__ == "__main__":
    unittest.main()
