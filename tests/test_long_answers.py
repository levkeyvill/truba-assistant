"""Длинные ответы: упёрся в потолок токенов — договорила сама.

Потолок ответа — `config.MAX_TOKENS`; обрезанный ответ приходит с
`finish_reason == "length"`. Продолжение запрашивается до озвучивания,
чтобы синтез не останавливался посреди фразы.

Проверяем на подменах потока (сети нет): хвост не озвучивается отдельно,
продолжение запрошено с уже сказанным и прямой просьбой, фраза договаривается
целиком, в историю ответ ложится одним куском, и не больше трёх запросов.
Плюс потолки знаков: на разбор модели и на чтение вслух — разные.
"""

import json
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import clipboard, documents, hands
from core.brain import Brain, CONTINUE_ASK, CONTINUE_LAST, CONTINUE_ROUNDS


def _chunk(content=None, calls=None, finish=None, reasoning=None):
    """Кусок потока. `finish` — причина в последнем куске, как у провайдера."""
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=None)


def _spent(prompt=0, completion=0, cached=None):
    usage = NS(prompt_tokens=prompt, completion_tokens=completion,
               prompt_tokens_details=NS(cached_tokens=cached or 0))
    return NS(choices=[], usage=usage)


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


class _Client:
    """Отдаёт заготовленные потоки и запоминает тела запросов."""

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
    brain._hedge_spare = None
    brain._hedge_model = None
    return brain


def _события(brain):
    """События мозга списком — журнал их потом разбирает сам."""
    events = []
    brain.on_event = lambda kind, payload: events.append((kind, payload))
    return events


# Тексты короче MIN_CHUNK не режутся на отдельные предложения, поэтому все
# куски тут длиннее — иначе тест проверял бы не то.
НАЧАЛО = "Разбираю твой документ по пунктам, кожаный, и вот что выходит. "
ХВОСТ = "Второй пункт говорит про то, что ты и сам"
class ПродолжениеОтвета(unittest.TestCase):
    """`_sentences` знает, чем кончился поток, и мозг договаривает."""

    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE, config.MAX_TOKENS)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        config.MAX_TOKENS = 800

    def tearDown(self):
        (config.WEB_SEARCH, config.TTS_ENGINE,
         config.MAX_TOKENS) = self._saved

    def test_обычный_ответ_ничего_не_продолжает(self):
        """`finish_reason == "stop"` — обычный конец, продолжения не было."""
        brain = _brain([[_chunk(НАЧАЛО),
                         _chunk("Всё, вот так примерно.", finish="stop"),
                         _spent(prompt=100, cached=80, completion=30)]])
        events = _события(brain)
        said = list(brain.reply("разбери документ"))
        self.assertEqual(said, ["Разбираю твой документ по пунктам, кожаный, "
                                "и вот что выходит.",
                                "Всё, вот так примерно."])
        # Один запрос — и ни одного события о продолжении.
        self.assertEqual(len(brain._client.bodies), 1)
        self.assertEqual([k for k, _ in events if k == "continue"], [])

    def test_хвост_не_озвучен_а_продолжение_запрошено(self):
        """Обрыв по потолку: хвост молчит, модель продолжает с него же."""
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length"),
             _spent(prompt=100, cached=80, completion=800)],
            [_chunk("уже знаешь, и это как раз то, что тебя и беспокоит."),
             _spent(prompt=200, cached=180, completion=60)],
        ])
        events = _события(brain)
        said = list(brain.reply("разбери документ"))

        # Хвост отдельно вслух не ушёл — фраза договорилась целиком.
        self.assertEqual(said[0], "Разбираю твой документ по пунктам, кожаный, "
                                 "и вот что выходит.")
        self.assertNotIn(ХВОСТ, said)
        self.assertEqual(said[1],
                         ХВОСТ + " уже знаешь, и это как раз то, что тебя "
                                 "и беспокоит.")

        # Продолжение — второй запрос: те же сообщения, уже сказанное
        # (включая хвост) и прямая просьба, без инструментов.
        self.assertEqual(len(brain._client.bodies), 2)
        продолжение = brain._client.bodies[1]["messages"]
        assistant = [m for m in продолжение if m["role"] == "assistant"][-1]
        self.assertIn(ХВОСТ, assistant["content"])
        self.assertIn("и вот что выходит.", assistant["content"])
        self.assertEqual(продолжение[-1]["role"], "system")
        self.assertEqual(продолжение[-1]["content"], CONTINUE_ASK)
        self.assertNotIn("tools", brain._client.bodies[1])

        # В историю — весь ответ одним сообщением, как и без продолжений.
        ход = [t for t in brain._history if t["role"] == "assistant"]
        self.assertEqual(len(ход), 1)
        self.assertIn(ХВОСТ, ход[0]["content"])
        self.assertIn("и беспокоит.", ход[0]["content"])

    def test_продолжение_у_openai_без_размышлений(self):
        """Продолжение OpenAI не должно расходовать лимит на размышления."""
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length")],
            [_chunk("уже знаешь, и это как раз то, что тебя и беспокоит.",
                    finish="stop")],
        ], provider="openai")
        with mock.patch.object(config, "HEDGE", False, create=True):
            list(brain.reply("разбери документ"))
        self.assertEqual(len(brain._client.bodies), 2)
        self.assertEqual(brain._client.bodies[1].get("reasoning_effort"), "none")
        self.assertNotIn("tools", brain._client.bodies[1])

    def test_без_состояния_хвост_по_потолку_не_теряется(self):
        """Кто не просит состояние потока, тот не продолжает — и хвост,
        поэтому хвост звучит: молча выбросить его означало бы потерю ответа."""
        said: list = []
        поток = iter([_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length")])
        вслух = list(Brain._sentences(поток, {}, said, [], {}))
        self.assertEqual(вслух[-1], ХВОСТ)
        self.assertIn(ХВОСТ, said)

    def test_событие_журнала_без_текста_ответа(self):
        """Событие показывает причину продолжения без текста ответа."""
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length")],
            [_chunk("Договорила наконец-то, вот и всё.")],
        ])
        events = _события(brain)
        list(brain.reply("разбери документ"))
        продолжение = [p for k, p in events if k == "continue"]
        self.assertEqual(продолжение, [{"limit": 800, "step": 1,
                                        "of": CONTINUE_ROUNDS}])
        # В журнале достаточно причины продолжения без текста ответа.
        self.assertNotIn(ХВОСТ, json.dumps(продолжение, ensure_ascii=False))

    def test_строка_журнала_о_продолжении(self):
        """Событие превращается в строку журнала, а не в тишину."""
        from ui.web_runtime import WebRuntime

        строки = WebRuntime._log_messages(
            "continue", {"limit": 800, "step": 1, "of": 2})
        self.assertEqual(строки, ["ответ упёрся в потолок 800 токенов — "
                                  "продолжаю (1 из 2)"])

    def test_два_продолжения_и_третий_обрыв(self):
        """Три обрыва подряд — три запроса, потом честная последняя фраза.

        Второе продолжение заканчивается законченной фразой (плюс новый
        недоговорённый хвост), а третье упирается в потолок снова — так
        видно, что промежуточный кусок всё-таки прозвучал.
        """
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length")],
            [_chunk("уже знаешь, и это как раз то, что тебя и беспокоит. "
                    "А дальше пошёл второй кусок, который опять оборвался",
                    finish="length")],
            [_chunk("А это уже третий кусок, и он тоже не влез в потолок.",
                    finish="length")],
        ])
        events = _события(brain)
        said = list(brain.reply("разбери документ"))

        # Три запроса: первый круг и два продолжения. Четвёртого нет.
        self.assertEqual(len(brain._client.bodies), 3)
        # Первая фраза и первый хвост склеились в одну.
        self.assertEqual(said[1],
                         ХВОСТ + " уже знаешь, и это как раз то, что тебя "
                                 "и беспокоит.")
        # Обрыв третьего куска озвучен словами, а не молчанием.
        self.assertEqual(said[-1], CONTINUE_LAST)
        self.assertEqual(CONTINUE_LAST,
                         "Дальше не влезло — скажи «продолжай», договорю.")
        # Продолжений ровно две, и каждое помечено в журнале.
        шаги = [p["step"] for k, p in events if k == "continue"]
        self.assertEqual(шаги, [1, 2])
        # В историю — весь сказанный ответ одним куском.
        ход = [t for t in brain._history if t["role"] == "assistant"]
        self.assertEqual(len(ход), 1)
        self.assertIn(CONTINUE_LAST, ход[0]["content"])

    def test_продолжение_суммирует_расход(self):
        """Два запроса — два круга расхода, а не один на весь ответ."""
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk(ХВОСТ, finish="length"),
             _spent(prompt=100, cached=90, completion=800)],
            [_chunk("Хвост договорила до конца, и теперь всё.", finish="stop"),
             _spent(prompt=150, cached=140, completion=40)],
        ])
        events = _события(brain)
        list(brain.reply("разбери документ"))
        tokens = [p for k, p in events if k == "tokens"][-1]
        self.assertEqual(tokens["prompt"], 250)
        self.assertEqual(tokens["cached"], 230)
        self.assertEqual(tokens["completion"], 840)
        self.assertEqual(tokens["calls"], 2)

    def test_обрыв_на_вызове_инструмента_не_продолжается(self):
        """Есть вызов инструмента — модель ещё думает, что собирается делать."""
        brain = _brain([
            [_chunk(НАЧАЛО),
             _chunk(calls=[_call(0, "c1", "web_search",
                                 '{"query": "разбор документа"}')],
                    finish="length"),
             _spent(prompt=90, completion=700)],
            [_chunk("Нашла, вот и разбор по пунктам.", finish="stop")],
        ])
        events = _события(brain)
        with mock.patch.object(config, "WEB_SEARCH", True, create=True), \
                mock.patch("core.web.run_tool",
                           return_value='{"results": []}'):
            said = list(brain.reply("найди и разбери документ"))

        # Продолжения не было: вместо него — обычный круг с инструментом.
        self.assertEqual([k for k, _ in events if k == "continue"], [])
        self.assertNotIn(ХВОСТ, " ".join(said))
        self.assertIn("Нашла, вот и разбор по пунктам.", said)

    def test_помеченный_разметкой_поток_не_ломает_разбор(self):
        """Разметка вызова — не речь: уходящий кусок не должен ронять цикл."""
        brain = _brain([
            [_chunk(НАЧАЛО), _chunk("<｜tool▁call▁begin▁"),
             _chunk(calls=[_call(0, "c1", "web_search", '{"query": "текст"}')],
                    finish="tool_calls")],
            [_chunk("Готово, вот тебе и разбор по пунктам.", finish="stop")],
        ])
        with mock.patch.object(config, "WEB_SEARCH", True, create=True), \
                mock.patch("core.web.run_tool",
                           return_value='{"results": []}'):
            said = list(brain.reply("разбери документ"))
        self.assertTrue(any("и вот что выходит." in one for one in said), said)
        self.assertFalse(any("<｜" in one for one in said), said)


class ПотолкиЗнаков(unittest.TestCase):
    """На разбор модели знаков больше, чем на чтение вслух."""

    def test_у_документов_два_потолка(self):
        self.assertEqual(documents.TEXT_LIMIT, 12000)
        self.assertEqual(documents.MODEL_TEXT_LIMIT, 30000)
        self.assertGreater(documents.MODEL_TEXT_LIMIT, documents.TEXT_LIMIT)

    def test_у_буфера_два_потолка(self):
        self.assertEqual(clipboard.MAX_CHARS, 12000)
        self.assertEqual(clipboard.MODEL_CHARS, 30000)
        self.assertGreater(clipboard.MODEL_CHARS, clipboard.MAX_CHARS)

    def test_разбор_документа_берёт_больший_потолок(self):
        """`retell` отдаёт модели 30 000, а чтение вслух — по-старому."""
        taken = []

        def _read(path, limit=documents.TEXT_LIMIT):
            taken.append(limit)
            return {"ok": True, "name": "отчёт.pdf", "text": "Текст.",
                    "chars": 10, "pages": 1, "cut": False}

        with mock.patch.object(documents, "pick",
                               return_value={"ok": True, "path": "r.pdf"}), \
                mock.patch.object(documents, "read_text", _read):
            hands.run_document(json.dumps(
                {"which": "by_name", "name": "отчёт", "because": "разбери"},
                ensure_ascii=False))
            hands.run_document(json.dumps(
                {"which": "by_name", "name": "отчёт", "mode": "aloud",
                 "because": "прочитай вслух"}, ensure_ascii=False),
                actions={"read_aloud": lambda text, name, resume:
                         (True, name)})
        self.assertEqual(taken, [documents.MODEL_TEXT_LIMIT,
                                 documents.TEXT_LIMIT])

    def test_буфер_на_перевод_и_разбор_берёт_больший_потолок(self):
        """`translate`/`analyze` — 30 000, чтение вслух — 12 000."""
        taken = []

        def _text(limit=clipboard.MAX_CHARS):
            taken.append(limit)
            return {"ok": True, "text": "Текст.", "chars": 6, "cut": False}

        with mock.patch.object(clipboard, "text", _text):
            for режим in ("translate", "analyze"):
                hands.run_clipboard(json.dumps(
                    {"mode": режим, "because": "переведи"}, ensure_ascii=False))
            hands.run_clipboard(json.dumps(
                {"mode": "read", "because": "прочитай"}, ensure_ascii=False),
                actions={"read_aloud": lambda text, name, resume:
                         (True, name)})
        self.assertEqual(taken, [clipboard.MODEL_CHARS, clipboard.MODEL_CHARS,
                                 clipboard.MAX_CHARS])

    def test_обрезка_честная_на_обоих_потолках(self):
        """Длинный текст обрезается и помечается `cut` — как было.

        Настоящее чтение — на временном файле: папки `data/` в копии нет,
        и создавать её нельзя.
        """
        длинный = "абзац. " * 6000
        with tempfile.TemporaryDirectory() as папка:
            путь = Path(папка) / "длинный.txt"
            путь.write_text(длинный, encoding="utf-8")
            для_модели = documents.read_text(путь, documents.MODEL_TEXT_LIMIT)
            вслух = documents.read_text(путь, documents.TEXT_LIMIT)
        self.assertTrue(для_модели["cut"])
        self.assertTrue(вслух["cut"])
        self.assertLessEqual(len(для_модели["text"]), documents.MODEL_TEXT_LIMIT)
        self.assertLessEqual(len(вслух["text"]), documents.TEXT_LIMIT)
        self.assertGreater(для_модели["chars"], documents.MODEL_TEXT_LIMIT)
