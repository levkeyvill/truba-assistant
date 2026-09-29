"""Интернет-инструменты модели: цикл вызовов, филлер, запреты — без сети."""

import json
import os
import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import brain as brain_module
from core import web
from core.brain import FILLERS, TOOL_ROUNDS, Brain


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


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
    return brain


SEARCH = [
    _chunk(reasoning="надо искать"),
    _chunk(calls=[_call(0, "c1", "web_search", '{"query": "курс')]),
    _chunk(calls=[_call(0, None, None, ' доллара"}')]),
]
ANSWER = [_chunk("Курс доллара сегодня восемьдесят четыре рубля девяносто копеек. "),
          _chunk("Такие дела.")]


class ToolLoopTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = True
        config.TTS_ENGINE = "silero"

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_search_then_answer_with_filler(self):
        brain = _brain([SEARCH, ANSWER])
        with mock.patch.object(web, "run_tool", return_value='{"results": []}') as tool:
            said = list(brain.reply("какой курс доллара?"))
        self.assertIn(said[0], FILLERS)
        self.assertEqual(said[1:], ["Курс доллара сегодня восемьдесят четыре рубля девяносто копеек.",
                                    "Такие дела."])
        tool.assert_called_once()
        self.assertEqual(tool.call_args[0][:2], ("web_search", '{"query": "курс доллара"}'))
        second = brain._client.bodies[1]["messages"]
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "web_search")
        # DeepSeek в режиме размышлений требует их вернуть вместе с вызовом.
        self.assertEqual(second[-2]["reasoning_content"], "надо искать")
        self.assertEqual(second[-1]["role"], "tool")
        # В историю — только сказанное по делу, без филлера и результатов поиска.
        self.assertEqual(brain._history[-1]["content"],
                         "Курс доллара сегодня восемьдесят четыре рубля девяносто копеек. Такие дела.")

    def test_small_talk_goes_without_search(self):
        brain = _brain([[_chunk("Нормально, кожаный, лежу и смотрю на тебя.")]])
        with mock.patch.object(web, "run_tool") as tool:
            said = list(brain.reply("как дела?"))
        self.assertEqual(said, ["Нормально, кожаный, лежу и смотрю на тебя."])
        tool.assert_not_called()
        # Инструменты действий есть всегда (см. hands.LOCAL), интернета — нет.
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertNotIn("web_search", names)

    def test_last_round_has_no_tools_and_markup_is_not_spoken(self):
        leaked = [_chunk("Короче, нашла кое-что интересное про патч. <｜｜DSML｜｜ calls>"),
                  _chunk("<｜｜DSML｜｜ invoke name=\"web_search\">")]
        brain = _brain([SEARCH] * TOOL_ROUNDS + [leaked])
        with mock.patch.object(web, "run_tool", return_value="{}"):
            said = list(brain.reply("что нового?"))
        last = brain._client.bodies[-1]
        self.assertNotIn("tools", last)
        self.assertIn("Хватит искать", last["messages"][-1]["content"])
        self.assertFalse(any("DSML" in s or "<｜" in s for s in said))
        self.assertEqual(said[-1], "Короче, нашла кое-что интересное про патч.")

    def test_time_budget_forces_answer(self):
        brain = _brain([SEARCH, ANSWER])
        with mock.patch.object(config, "WEB_SEARCH_BUDGET", -1.0, create=True), \
                mock.patch.object(web, "run_tool", return_value="{}"):
            said = list(brain.reply("что нового?"))
        # После первого же поиска время вышло: второй запрос — без инструментов.
        self.assertNotIn("tools", brain._client.bodies[1])
        self.assertEqual(said[-1], "Такие дела.")

    def test_end_conversation_only_aloud(self):
        end = [_chunk("Ладно, молчу."), _chunk(calls=[_call(0, "e1", "end_conversation", "{}")])]
        brain = _brain([end])
        with mock.patch.object(web, "run_tool") as tool:
            said = list(brain.reply("мы уже поговорили", aloud=True))
        self.assertTrue(brain.ended)
        self.assertEqual(said, ["Ладно, молчу."])
        tool.assert_not_called()
        self.assertEqual(len(brain._client.bodies), 1)
        names = [t["function"]["name"] for t in brain._client.bodies[0]["tools"]]
        self.assertIn("end_conversation", names)
        # В чате закончить разговор нельзя — инструмента нет.
        chat = _brain([[_chunk("Ок.")]])
        list(chat.reply("пока"))
        names = [t["function"]["name"] for t in chat._client.bodies[0].get("tools", [])]
        self.assertNotIn("end_conversation", names)
        self.assertFalse(chat.ended)

    def test_silent_end_stays_out_of_history(self):
        # 25 сентября записи «молча закончила» подряд приучили её молчать
        # и на «Труба, привет».
        brain = _brain([[_chunk(calls=[_call(0, "e1", "end_conversation", "{}")])]])
        self.assertEqual(list(brain.reply("ок, всё", aloud=True)), [])
        self.assertTrue(brain.ended)
        self.assertEqual(len(brain._history), 0)

    def test_cannot_end_when_called_by_name(self):
        brain = _brain([[_chunk("Привет, кожаный.")]])
        list(brain.reply("Труба, привет!", aloud=True, can_end=False))
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertNotIn("end_conversation", names)
        self.assertFalse(brain.ended)

    def test_openai_gets_reasoning_none_with_tools(self):
        brain = _brain([[_chunk("Ок.")]], provider="openai")
        list(brain.reply("привет, что нового?"))
        body = brain._client.bodies[0]
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertIn("max_completion_tokens", body)

    def test_no_search_without_question_or_request(self):
        # 25 сентября после вопроса про Японию она искала то же на каждую
        # реплику, даже на «ну ты и косячница».
        for text in ("Ну ты и косячница, блядь, конечно.",
                     "Ага, понятно. Слушай, короче, и друг хотел у тебя спросить ещё пару вопросов.",
                     "Спасибо, всё понятно", "Как дела?", "Ты меня слышишь, нет?",
                     "Right Games. Запускай, пожалуйста.", "Сколько тебе лет?",
                     "Какой у тебя любимый цвет?"):
            brain = _brain([[_chunk("Ну бывает.")]])
            list(brain.reply(text, aloud=True))
            names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
            self.assertNotIn("web_search", names, text)
        for text in ("Найди мне курс доллара", "Расскажи что нового в валоранте",
                     "Какая погода в Токио?", "Сколько стоит 5090?", "Когда выйдет GTA 6?",
                     "Глянь в интернете, кто выиграл вчера", "поищи рецепт борща"):
            brain = _brain([[_chunk("Ок.")]])
            list(brain.reply(text))
            names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
            self.assertIn("web_search", names, text)

    def test_questions_about_her_stay_offline(self):
        # 25 сентября на «что новенького у тебя, какие обновления?» она
        # пошла искать новости про нейросети.
        for text in ("Как сегодня день прошёл? Что-то новенькое у тебя появилось? Какие-то обновления?",
                     "Что у тебя нового?", "Какие новости с тобой?"):
            brain = _brain([[_chunk("Да так, сижу.")]])
            list(brain.reply(text))
            names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
            self.assertNotIn("web_search", names, text)
        # Явная просьба — всегда в интернет.
        brain = _brain([[_chunk("Ок.")]])
        list(brain.reply("Найди, что нового у тебя в прошивке"))
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertIn("web_search", names)

    def test_no_silent_end_on_a_question(self):
        # «Ну всё, окей… пойду за пивом. Как думаешь, вариант?» — она молча
        # закрыла разговор, зацепившись за начало.
        brain = _brain([[_chunk("Вариант норм.")]])
        list(brain.reply("Ну всё, окей, понятно. Пойду за пивом. Как ты думаешь, вариант?", aloud=True))
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertNotIn("end_conversation", names)
        brain = _brain([[_chunk("Давай.")]])
        list(brain.reply("Ну всё, окей, пойду за пивом.", aloud=True))
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertIn("end_conversation", names)

    def _names(self, brain, index=0):
        return [t["function"]["name"] for t in brain._client.bodies[index].get("tools", [])]

    def test_search_is_remembered_so_she_does_not_recheck(self):
        # Без пометки она видела свой курс без источника и либо лезла
        # перепроверять, либо каялась «назвала без проверки».
        brain = _brain([SEARCH, ANSWER, [_chunk("Около девяноста семи.")]])
        with mock.patch.object(web, "run_tool", return_value='{"results": []}'):
            list(brain.reply("какой курс доллара?"))
            self.assertEqual(brain._history[-1]["searched"], ["курс доллара"])
            list(brain.reply("А в евро?"))
        sent = brain._client.bodies[2]["messages"]
        notes = [m["content"] for m in sent if m["role"] == "system" and "по поиску" in m["content"]]
        self.assertEqual(len(notes), 1)
        self.assertIn("курс доллара", notes[0])
        # «А в евро?» сразу после поиска — уточнение, ему интернет можно.
        self.assertIn("web_search", self._names(brain, 2))

    def test_follow_up_needs_a_fresh_search_and_a_question(self):
        brain = _brain([[_chunk("Не знаю.")]])
        list(brain.reply("А в евро?"))
        self.assertNotIn("web_search", self._names(brain))
        brain = _brain([SEARCH, ANSWER, [_chunk("Сама такая.")]])
        with mock.patch.object(web, "run_tool", return_value="{}"):
            list(brain.reply("какой курс доллара?"))
            list(brain.reply("Ну ты и косячница"))
        self.assertNotIn("web_search", self._names(brain, 2))

    def test_dead_search_is_not_retried(self):
        # Все поисковики отказали — сразу отвечать, а не искать снова (было 20 с тишины).
        brain = _brain([SEARCH, ANSWER])
        with mock.patch.object(web, "run_tool", return_value='{"error": "поисковики не ответили"}'):
            list(brain.reply("какой курс доллара?"))
        self.assertNotIn("tools", brain._client.bodies[1])
        self.assertIn("Хватит искать", brain._client.bodies[1]["messages"][-1]["content"])
        # Не нашла — и помечать «ответ по поиску» нечего.
        self.assertNotIn("searched", brain._history[-1])

    def test_end_only_when_something_sounds_like_goodbye(self):
        brain = _brain([[_chunk("Запомнила.")]])
        list(brain.reply("Запомни, что я завтра иду к стоматологу.", aloud=True))
        self.assertNotIn("end_conversation", self._names(brain))
        for text in ("Ок, спасибо", "Всё, пока", "Хватит, отдыхай", "Ладно, забей"):
            brain = _brain([[_chunk("Ага.")]])
            list(brain.reply(text, aloud=True))
            self.assertIn("end_conversation", self._names(brain), text)

    def test_clock_has_time_zone(self):
        from core.brain import _now_line

        self.assertRegex(_now_line(), r"\(UTC[+-]\d")
        self.assertIn("по разнице поясов", _now_line())

    def test_search_off_sends_no_web_tools(self):
        config.WEB_SEARCH = False
        brain = _brain([[_chunk("Ок.")]], provider="openai")
        list(brain.reply("привет"))
        names = [t["function"]["name"] for t in brain._client.bodies[0].get("tools", [])]
        self.assertNotIn("web_search", names)
        self.assertNotIn("read_page", names)
        # Инструменты действий остаются: они не про интернет.
        self.assertIn("launch_app", names)


def _region_block():
    import httpx
    from openai import PermissionDeniedError

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return PermissionDeniedError(
        "Error code: 403 - {'error': {'code': 'unsupported_country_region_territory', "
        "'message': 'Country, region, or territory not supported'}}",
        response=httpx.Response(403, request=request), body=None)


class _Blocked:
    """OpenAI без VPN: на любой запрос — 403 по стране."""

    def __init__(self):
        self.calls = 0
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.calls += 1
        raise _region_block()


class SpareProviderTests(unittest.TestCase):
    """25 сентября VPN отвалился посреди игры — она молчала на всё."""

    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_region_block_answers_through_spare_and_comes_back(self):
        brain = _brain([], provider="openai")
        blocked = _Blocked()
        brain._client = blocked
        spare = _Client([[_chunk("Я тут, кожаный.")], [_chunk("Опять я.")]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        # Оба ключа подменены: иначе возврат к своему брал живой из .env.
        with mock.patch("openai.OpenAI", return_value=spare), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test",
                                             "OPENAI_API_KEY": "sk-test"}):
            said = list(brain.reply("ты тут?"))
            self.assertEqual(said, ["Я тут, кожаный."])
            self.assertEqual(brain.provider, "deepseek")
            self.assertEqual(events[0], ("provider_fallback",
                                         {"from": "openai", "to": "deepseek", "why": "region"}))
            # Пока не прошло HOME_RETRY — сразу к запасному, без лишнего отказа.
            list(brain.reply("а сейчас?"))
            self.assertEqual(blocked.calls, 1)
            # Прошло — снова пробуем свой.
            brain._home_at = 0.0
            home = _Client([[_chunk("Вернулась.")]])
            with mock.patch("openai.OpenAI", return_value=home):
                self.assertEqual(list(brain.reply("ну как?")), ["Вернулась."])
        self.assertEqual(brain.provider, "openai")
        self.assertEqual(events[-1][0], "provider_back")

    def test_connection_lost_mid_search_goes_to_spare_with_findings(self):
        import httpx
        from openai import APIConnectionError

        brain = _brain([], provider="openai")
        first = _Client([SEARCH])
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        calls = {"n": 0}

        def create(**body):
            calls["n"] += 1
            if calls["n"] == 1:
                return first._create(**body)
            raise APIConnectionError(request=request)

        brain._client = NS(chat=NS(completions=NS(create=create)))
        spare = _Client([ANSWER])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch("openai.OpenAI", return_value=spare), \
                mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}), \
                mock.patch.object(web, "run_tool", return_value='{"results": ["84,9"]}'):
            said = list(brain.reply("какой курс доллара?"))
        self.assertEqual(said[-1], "Такие дела.")
        self.assertIn(("provider_fallback", {"from": "openai", "to": "deepseek", "why": "connection"}),
                      events)
        # Запасному чужие вызовы не отдаём — только найденное текстом.
        sent = spare.bodies[0]["messages"]
        self.assertFalse(any(m.get("role") == "tool" or m.get("tool_calls") for m in sent))
        self.assertIn("84,9", sent[-1]["content"])

    def test_other_errors_are_not_masked(self):
        from openai import AuthenticationError
        import httpx

        brain = _brain([], provider="openai")
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        wrong_key = AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
        brain._client = NS(chat=NS(completions=NS(create=mock.Mock(side_effect=wrong_key))))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            with self.assertRaises(AuthenticationError):
                list(brain.reply("привет"))
        self.assertEqual(brain.provider, "openai")

    def test_no_spare_key_raises_original(self):
        brain = _brain([], provider="openai")
        brain._client = _Blocked()
        keys = {name: "" for name in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "MINIMAX_API_KEY")}
        with mock.patch.dict(os.environ, keys):
            with self.assertRaisesRegex(Exception, "unsupported_country"):
                list(brain.reply("привет"))


class SearchModeTests(unittest.TestCase):
    FREE = ([{"title": "t", "url": "https://a", "snippet": "s"}], "yahoo")
    PAID = ([{"title": "Сводка поиска", "url": "https://b", "snippet": "курс"}], "openai")

    def test_modes(self):
        with mock.patch.object(web, "search_free", return_value=self.FREE) as free, \
                mock.patch.object(web, "search_paid", return_value=self.PAID) as paid:
            self.assertEqual(web.search("курс", mode="free")[1], "yahoo")
            self.assertEqual(web.search("курс", mode="paid")[1], "openai")
            self.assertEqual(web.search("курс", mode="auto")[1], "yahoo")
            self.assertEqual(paid.call_count, 1)
            self.assertEqual(free.call_count, 2)

    def test_auto_falls_back_to_paid_only_when_free_fails(self):
        with mock.patch.object(web, "search_free", side_effect=RuntimeError("поисковики не ответили")), \
                mock.patch.object(web, "search_paid", return_value=self.PAID) as paid:
            self.assertEqual(web.search("курс", mode="auto")[1], "openai")
            with self.assertRaises(RuntimeError):
                web.search("курс", mode="free")
            self.assertEqual(paid.call_count, 1)

    def test_paid_parses_summary_and_sources(self):
        note = NS(type="url_citation", url="https://cbr.ru/x", title="Банк России")
        answer = NS(output_text="Курс — **84,91** рубля. ([cbr.ru](https://cbr.ru/x)) См. [сайт ЦБ](https://cbr.ru).",
                    output=[NS(content=[NS(annotations=[note, note])])])
        client = NS(responses=NS(create=mock.Mock(return_value=answer)))
        with mock.patch("openai.OpenAI", return_value=client), \
                mock.patch("core.settings.get_api_key", return_value="sk-test"):
            results, backend = web.search_paid("курс доллара")
        self.assertEqual(backend, "openai")
        self.assertEqual(results[0]["snippet"], "Курс — 84,91 рубля. См. сайт ЦБ.")
        self.assertEqual([r["url"] for r in results], ["https://cbr.ru/x", "https://cbr.ru/x"])
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["tools"][0]["type"], "web_search")
        self.assertEqual(kwargs["model"], web.PAID_MODEL)

    def test_paid_without_key_explains(self):
        with mock.patch("core.settings.get_api_key", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "ключ OpenAI"):
                web.search_paid("курс")


class WebGuardTests(unittest.TestCase):
    def test_local_addresses_are_refused(self):
        for url in ("http://127.0.0.1:8765/api/settings", "http://localhost/",
                    "http://192.168.1.50:8765/", "file:///C:/Windows/win.ini",
                    "http://[::1]/"):
            answer = json.loads(web.run_tool("read_page", json.dumps({"url": url})))
            self.assertIn("error", answer, url)

    def test_bad_arguments_do_not_raise(self):
        self.assertIn("error", json.loads(web.run_tool("web_search", "{не json")))
        self.assertIn("error", json.loads(web.run_tool("rm_rf", "{}")))


if __name__ == "__main__":
    unittest.main()
