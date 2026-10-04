"""Руки: запуск программ моделью — по белому списку и без вранья.

Ни сети, ни настоящих программ: `launcher.launch` подменяется, а список
берётся из подменённого `apps.json`.
"""

import json
import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import abilities, hands, launcher, web
from core.brain import Brain

APPS = [
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"},
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
]


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
    # Проверка «он правда просил?» (core/hands.JUDGED) спрашивает модель перед
    # запуском. Здесь она отвечает «да»: этот файл проверяет сам запуск, а
    # отказ модели разбирает tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain


def _tool_call(name, args, call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


def _names(brain, index=0):
    return [t["function"]["name"] for t in brain._client.bodies[index].get("tools", [])]


class LaunchToolTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        # Память о недавних действиях общая на процесс: без сброса второй
        # тест получит «Уже сделала» вместо запуска.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_model_launches_and_needs_no_second_round(self):
        # Готовое подтверждение запуска экономит 2–5 с второго круга
        # модели по замеру.
        brain = _brain([_tool_call("launch_app",
                                  '{"app": "youtube", "because": "запусти его"}'),
                        [_chunk("Держи, YouTube открыт.")]])
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")) as start:
            said = list(brain.reply("Ну, у тебя же есть кнопка запустить Chat GPT. "
                                    "Вот запусти его, пожалуйста."))
        # Программа запущена по белому списку, а не тем, о чём говорила модель.
        start.assert_called_once_with("youtube")
        self.assertEqual(len(brain._client.bodies), 1)
        # Сказано с названием программы, а не с её id.
        self.assertEqual(len(said), 1)
        self.assertIn("YouTube", said[0])
        # В историю идёт сказанное, служебного ответа инструмента там нет.
        self.assertEqual(brain._history[-1]["content"], said[0])
        self.assertNotIn("запущено", brain._history[-1]["content"])

    def test_unknown_app_is_refused_before_launch(self):
        brain = _brain([_tool_call("launch_app",
                                  '{"app": "notepad", "because": "открой, пожалуйста, блокнот"}'),
                        [_chunk("Блокнота у меня нет, я его не умею.")]])
        with mock.patch.object(launcher, "launch") as start:
            said = list(brain.reply("Слушай, открой, пожалуйста, блокнот."))
        start.assert_not_called()
        answer = brain._client.bodies[1]["messages"][-1]
        self.assertEqual(answer["role"], "tool")
        self.assertIn("error", json.loads(answer["content"]))
        self.assertEqual(said, ["Блокнота у меня нет, я его не умею."])

    def test_failed_launch_reaches_the_model_with_the_reason(self):
        brain = _brain([_tool_call("launch_app",
                                  '{"app": "discord", "because": "Открой Discord"}'),
                        [_chunk("Не вышло запустить Discord, файла нет.")]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch.object(launcher, "launch",
                               return_value=(False, "Discord: файл не найден")):
            said = list(brain.reply("Открой Discord, только он у меня не открывается."))
        answer = brain._client.bodies[1]["messages"][-1]["content"]
        self.assertIn("не вышло", answer)
        self.assertIn("файл не найден", answer)
        self.assertEqual(said, ["Не вышло запустить Discord, файла нет."])
        # В журнал пульта — то же событие, что у голосовой команды. Проверку
        # «он правда просил?» (action_check) отбрасываем: о ней свой файл.
        self.assertEqual([e for e in events if e[0] != "action_check"],
                         [("launch_failed", "Discord: файл не найден")])

    def test_success_is_journalled(self):
        brain = _brain([_tool_call("launch_app",
                                  '{"app": "youtube", "because": "запусти, пожалуйста, YouTube"}'),
                        [_chunk("Открыла.")]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")):
            list(brain.reply("запусти, пожалуйста, YouTube"))
        self.assertEqual([e for e in events if e[0] != "action_check"],
                         [("launched", "YouTube")])

    def test_tool_is_given_even_on_small_talk(self):
        # Инструмент выдаётся всегда: распознавание речи коверкает слова
        # («Шот», «посмотрю» вместо «попью»), и ворота по глаголам промахивались.
        brain = _brain([[_chunk("Нормально.")]])
        list(brain.reply("как дела?"))
        self.assertIn(hands.NAME, _names(brain))

    def test_tool_on_a_long_phrase_the_local_parser_misses(self):
        # Ровно та фраза из журнала: короткий разбор её не берёт, и модель
        # без инструмента отвечала «готово, ютуб открыт».
        brain = _brain([[_chunk("Готово.")]])
        list(brain.reply("Ладно, всё. Проверка прошла. Слушай, запусти, пожалуйста, YouTube."))
        self.assertIn(hands.NAME, _names(brain))

    def test_tool_is_given_in_chat_too(self):
        brain = _brain([[_chunk("Открываю.")]])
        list(brain.reply("открой, пожалуйста, Discord"))
        self.assertIn(hands.NAME, _names(brain))

    def test_no_tool_without_programs(self):
        with mock.patch.object(launcher, "read_list", return_value=[]):
            brain = _brain([[_chunk("А что запустить-то.")]])
            list(brain.reply("запусти, пожалуйста, YouTube"))
        self.assertNotIn(hands.NAME, _names(brain))

    def test_description_lists_titles_for_the_model(self):
        spec = hands.tool(APPS)["function"]
        self.assertEqual(
            spec["parameters"]["properties"]["app"]["enum"], ["youtube", "discord"])
        for title in ("YouTube", "Discord"):
            self.assertIn(title, spec["description"])

    def test_web_tools_still_work_alongside(self):
        brain = _brain([_tool_call("web_search", '{"query": "курс доллара"}'),
                        [_chunk("Восемьдесят четыре.")]])
        config.WEB_SEARCH = True
        with mock.patch.object(web, "run_tool", return_value='{"results": []}') as tool:
            said = list(brain.reply("запусти ютуб и глянь курс доллара"))
        tool.assert_called_once()
        self.assertIn(hands.NAME, _names(brain))
        self.assertIn("web_search", _names(brain))
        self.assertEqual(said[-1], "Восемьдесят четыре.")


class GuardTests(unittest.TestCase):
    def setUp(self):
        # Память о недавних действиях общая на процесс: без сброса тест про
        # ошибку запуска получит «Уже сделала» от предыдущего.
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def test_broken_arguments_do_not_raise(self):
        with mock.patch.object(launcher, "launch") as start:
            self.assertIn("error", json.loads(hands.run_tool("{не json", None, APPS)))
        start.assert_not_called()

    def test_only_ids_from_the_list_are_launched(self):
        with mock.patch.object(launcher, "launch") as start:
            answer = json.loads(hands.run_tool('{"app": "calc.exe"}', None, APPS))
        start.assert_not_called()
        self.assertIn("нет такой программы", answer["error"])

    def test_path_from_the_model_is_not_taken(self):
        # Ни путей, ни команд от модели: в списке только id.
        with mock.patch.object(launcher, "launch") as start:
            for args in ('{"app": "C:\\\\Windows\\\\notepad.exe"}',
                         '{"app": "powershell -enc AAAA"}'):
                self.assertIn("error", json.loads(hands.run_tool(args, None, APPS)))
        start.assert_not_called()

    def test_launch_itself_may_raise(self):
        with mock.patch.object(launcher, "launch", side_effect=OSError("занято")):
            answer = json.loads(hands.run_tool('{"app": "youtube"}', None, APPS))
        self.assertIn("не вышло", answer["error"])
        self.assertIn("занято", answer["error"])


class NoLyingTests(unittest.TestCase):
    def test_rule_is_in_the_system_prompt(self):
        text = abilities.describe(APPS)
        self.assertIn(abilities.NO_LYING, text)
        self.assertIn("Никогда не говори, что сделала", text)
        self.assertIn("launch_app", text)

    def test_rule_survives_without_programs_and_without_web(self):
        # Инструмента может не быть — правило обязано работать и тогда.
        text = abilities.describe([], web=False)
        self.assertIn(abilities.NO_LYING, text)
        self.assertIn("не можешь", text)

    def test_brain_puts_the_rule_into_the_messages(self):
        saved = config.WEB_SEARCH
        config.WEB_SEARCH = True
        try:
            with mock.patch.object(launcher, "read_list", return_value=APPS):
                brain = _brain([[_chunk("Ок.")]])
                # Настоящий сборщик умений, а не заглушка из _brain().
                brain._abilities = Brain._abilities.__get__(brain)
                messages = brain._messages("как дела?", None)
        finally:
            config.WEB_SEARCH = saved
        self.assertIn(abilities.NO_LYING, messages[0]["content"])
        self.assertNotIn(abilities.NO_LYING, messages[-2]["content"])


if __name__ == "__main__":
    unittest.main()
