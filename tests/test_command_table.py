"""Таблица мгновенных команд проверяет сама себя.

`core/commands.COMMANDS` — единственный источник и для разбора, и для страницы
«Команды» в пульте. Поэтому каждый её `examples` обязан разбираться в свою
команду с верным аргументом: разъехались таблица и разбор — падает здесь.

Список «не команды» проверяет вторую половину грамматики: прошедшее время,
вопросы и длинные разговорные фразы уходят модели, а не выполняются.

Здесь же — два стыка таблицы с остальным кодом: цитата `because` доходит от
вызова модели до `hands.asked_for`, а команда `search` уходит в модель с
требованием искать. Ни сети, ни звука, ни облака: подменён только мозг и
вызовы `_answer`/`_run_command`.
"""

import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import commands, hands
from core.brain import Brain
from core.voice_loop import VoiceLoop

APPS = [
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"},
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
    {"id": "telegram", "title": "Telegram", "kind": "app", "path": "Telegram.exe"},
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe"},
]


# --- Подставной мозг --------------------------------------------------------


def _чан(content=None, calls=None):
    """Один кусок потока ответов модели."""
    delta = NS(content=content, tool_calls=calls, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _вызов(name, args):
    """Поток одного круга: модель зовёт инструмент."""
    call = NS(index=0, id="c1", function=NS(name=name, arguments=args))
    return [_чан(calls=[call])]


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _мозг(streams):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
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
    brain.actions = {}
    brain.last_sources = []
    return brain


# --- Подставной голосовой цикл ---------------------------------------------


# Полсекунды тишины: `_turn_body` меряет фразу, чтобы положить в событие.
_ФРАЗА = np.zeros(int(config.SAMPLE_RATE * 0.5), dtype=np.float32)


def _цикл():
    """Цикл, у которого видно только _turn_body: кто и с каким флагом позван."""
    loop = object.__new__(VoiceLoop)
    loop.ran = []
    loop.answers = []
    loop._search_next = False
    loop._dictation = None
    loop._interrupt = threading.Event()
    loop._known_apps = lambda: APPS
    loop._voice_score = None
    loop._emit = lambda kind, payload: None
    loop._open_conversation = lambda: None
    loop._run_command = lambda order, text: loop.ran.append(order.action)
    loop._answer = lambda text, **kw: loop.answers.append((text, kw.get("search", False)))
    return loop


class TableTests(unittest.TestCase):
    def test_every_example_is_understood_as_its_own_command(self):
        for spec in commands.COMMANDS:
            self.assertTrue(spec.examples, spec.id)
            for пример in spec.examples:
                with self.subTest(команда=spec.id, фраза=пример):
                    order = commands.understand(пример, APPS)
                    self.assertIsNotNone(order, пример)
                    self.assertEqual(order.action, spec.id, пример)

    def test_arguments_of_the_examples(self):
        for пример, ждём in (
            ("найди в интернете курс доллара", ("search", "курс доллара")),
            ("поищи что будет с рублем", ("search", "что будет с рублем")),
            ("открой канал Корзинка", ("youtube", "корзинка")),
            ("найди на ютубе обзор 5070", ("youtube", "обзор 5070")),
            ("закрой дискорд", ("close", "discord")),
            ("открой ютуб", ("launch", "youtube")),
        ):
            with self.subTest(фраза=пример):
                order = commands.understand(пример, APPS)
                self.assertEqual((order.action, order.target), ждём, пример)

    def test_kinds_of_the_youtube_examples(self):
        self.assertEqual(
            commands.understand("открой канал Корзинка", APPS).kind, "channel")
        self.assertEqual(
            commands.understand("найди на ютубе обзор 5070", APPS).kind, "search")
        self.assertEqual(
            commands.understand("включи на ютубе ремонт видеокарты", APPS).kind,
            "video")

    def test_the_table_describes_itself_for_the_pult(self):
        # По этим же полям потом соберётся страница «Команды» в пульте.
        for spec in commands.COMMANDS:
            with self.subTest(команда=spec.id):
                self.assertTrue(spec.title, spec.id)
                self.assertTrue(spec.forms, spec.id)
                self.assertTrue(spec.does, spec.id)
                self.assertIsInstance(spec.arg, str, spec.id)

    def test_ids_are_the_ones_voice_loop_knows(self):
        self.assertEqual(
            {spec.id for spec in commands.COMMANDS},
            {"screenshot", "moment", "layout", "media", "pc_volume", "volume",
             "launch", "close", "youtube", "note", "search", "folder"},
        )


class NotCommandsTests(unittest.TestCase):
    def test_questions_and_the_past_tense_go_to_the_model(self):
        for фраза in ("ты сделала скриншот?",
                      "я вчера сделал скриншот",
                      "сделал заметку",
                      "ты опять открыла ютуб, да?",
                      "можешь, пожалуйста, открыть канал Корзинка "
                      "и включить последний ролик?",
                      "где мои заметки",
                      "прочитай заметку"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_turning_on_a_program_or_on_nothing(self):
        self.assertEqual(
            commands.understand("включи Discord", APPS).action, "launch")
        # «Свет» программой из списка не называется — пусть модель.
        self.assertIsNone(commands.understand("включи свет", APPS))

    def test_a_long_phrase_is_not_an_instant_command(self):
        длинная = ("запиши заметку и ещё раз скажи то же самое медленнее "
                   "и ещё раз и ещё раз и ещё раз и ещё раз")
        self.assertGreater(len(длинная.split()), commands.MAX_WORDS)
        self.assertIsNone(commands.understand(длинная, APPS))


class SearchTests(unittest.TestCase):
    def test_search_keeps_what_to_look_for(self):
        order = commands.understand("найди в интернете курс доллара", APPS)
        self.assertEqual((order.action, order.target), ("search", "курс доллара"))
        # Ответа у поиска нет: ответит модель по найденному.
        self.assertEqual(order.reply, "")

    def test_search_without_a_query_is_not_a_command(self):
        self.assertIsNone(commands.understand("поищи", APPS))
        self.assertIsNone(commands.understand("найди в интернете", APPS))

    def test_a_search_asked_as_a_question_is_still_a_search(self):
        # 29.09: «найди в интернете что такое квантовый компьютер?» уходило
        # в разговор, и модель думала, искать ли, — лишние секунды.
        order = commands.understand(
            "найди в интернете, что такое квантовый компьютер?", APPS)
        self.assertEqual((order.action, order.target),
                         ("search", "что такое квантовый компьютер"))

    def test_a_long_search_is_still_a_search(self):
        длинная = ("найди в инете сколько стоит билет на поезд из Москвы "
                   "в Питер на эти выходные и есть ли места в купе")
        self.assertGreater(len(длинная.split()), commands.MAX_WORDS)
        self.assertEqual(commands.understand(длинная, APPS).action, "search")

    def test_a_question_is_never_another_command(self):
        # Поиску «?» не мешает, остальным командам — по-прежнему мешает.
        self.assertIsNone(commands.understand("открой ютуб?", APPS))
        self.assertIsNone(commands.understand("сделай скриншот?", APPS))


# --- Цитата доходит от модели до проверки -----------------------------------


class BecauseTests(unittest.TestCase):
    """`brain._reply_rounds` обязан отдать `asked_for` цитату из вызова.

    Без неё любое действие проходило бы проверку по пустому `because`, а с
    выдуманной цитатой — отвергалось бы зря. Проверяем сам передаваемый
    аргумент: сам `hands.asked_for` разбирает цитаты в test_asked_for.py.
    """

    def test_the_quote_from_the_call_reaches_asked_for(self):
        seen = []

        def spy(name, said, apps=None, because=""):
            seen.append((name, said, because))
            return False

        brain = _мозг([_вызов(hands.SHOT_NAME,
                              '{"because": "сделать скриншот"}'),
                       [_чан(content="Готово.")]])
        with mock.patch.object(hands, "asked_for", spy):
            list(brain.reply("Слушай, а ты можешь сделать скриншот?"))
        self.assertEqual(seen, [(hands.SHOT_NAME,
                                 "Слушай, а ты можешь сделать скриншот?",
                                 "сделать скриншот")])

    def test_a_call_without_the_quote_comes_through_empty(self):
        seen = []

        def spy(name, said, apps=None, because=""):
            seen.append(because)
            return False

        brain = _мозг([_вызов(hands.SHOT_NAME, "{}"), [_чан(content="Готово.")]])
        with mock.patch.object(hands, "asked_for", spy):
            list(brain.reply("сделай скриншот"))
        self.assertEqual(seen, [""])


# --- «Найти» голосом идёт в модель с требованием искать ----------------------


class SearchThroughTheModelTests(unittest.TestCase):
    """Команда `search` — это не действие, а запрос в интернет.

    Так же работает кнопка «Найти» на телефоне (`_search_next`): ответом будет
    разговор по найденному, а не короткое подтверждение.
    """

    def test_search_goes_to_the_model_with_the_search_flag(self):
        loop = _цикл()
        loop._turn_body("найди в интернете курс доллара", _ФРАЗА, 0.0, 0.0, False)
        self.assertEqual(loop.ran, [])  # _run_command не звался
        self.assertEqual(loop.answers, [("найди в интернете курс доллара", True)])

    def test_another_command_still_runs_by_itself(self):
        loop = _цикл()
        loop._turn_body("сделай скриншот", _ФРАЗА, 0.0, 0.0, False)
        self.assertEqual(loop.ran, ["screenshot"])
        self.assertEqual(loop.answers, [])


if __name__ == "__main__":
    unittest.main()