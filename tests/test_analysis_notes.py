"""«Добавить разбор в заметки?» — она сама спрашивает и сама пишет.

Хозяин (02.10): «я сказал: открой документ, прочитай, сделай анализ. Она
делает анализ, говорит, а после сказанного спрашивает: добавить ли анализ
документа в заметки. Говорю да или нет — вот и всё».

Здесь проверяется весь путь по частям:
  - `Brain.last_turn_tools` — что вообще было вызвано в последнем ходу;
  - `VoiceLoop._analysis_source` — был ли ход разбором и откуда он;
  - `_ask_analysis_note` — вопрос, предложение и окно разговора (в том числе
    из `_answer`: после перебитого, остановленного и упавшего ответа вопроса нет);
  - `_analysis_turn` — разбор ответа «да/нет» на словах, без модели;
  - `notes.add_analysis` — что именно ляжет на диск (временная папка);
  - настройка `offer_analysis_note` — пульт и сервер;
  - сам выключатель в `pult.js` — пробой в node (tests/analysis_pult.mjs).

Ни микрофона, ни синтеза, ни динамика, ни облака, ни настоящих «Документов»:
всё собрано из заглушек, заметки пишутся во временную папку, `settings.json`
и пульт — подменами. Не здесь видно, как это звучит и сколько ждёт облако.
"""

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import notes, settings
from core.brain import Brain
from core.voice_loop import (
    ANALYSIS_ASK,
    ANALYSIS_FAILED,
    ANALYSIS_NO,
    ANALYSIS_SAVED,
    ANALYSIS_TTL,
    VoiceLoop,
)
from ui.web_runtime import WebRuntime

PULT = config.ROOT / "ui" / "web" / "pult.js"
ПРОБА = config.ROOT / "tests" / "analysis_pult.mjs"

# Разбор документа: путь с пробелом — как у настоящих «Документов» хозяина.
ДОКУМЕНТ = r"D:\Работа\Мои документы\отчёт за сентябрь.pdf"
БУФЕР_НАЧАЛО = "скопированный текст про погоду в москве на этой неделе"


# --- Мозг: что было вызвано в последнем ходу --------------------------------


def _chunk(content=None, calls=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


def _tool_call(name, args="{}", call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


class _Client:
    """Отдаёт заранее заготовленные потоки."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        return iter(self.streams.pop(0))


def _мозг(streams):
    """Настоящий `Brain` без облака, ключей и личности."""
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
    brain.last_queries = []
    brain.last_timing = {"rounds": []}
    brain._hedge_spare = None
    brain._hedge_model = None
    brain._warm = None
    return brain



# --- Мозг отдаёт наружу вызовы последнего ходу -----------------------------


class МозгОтдаётВызовыTests(unittest.TestCase):
    """`last_turn_tools`: имя, аргументы и разобранный ответ инструмента."""

    def _ход(self, brain, text="прочитай документ и разбери его"):
        # Судья «он правда просил?» спрашивает модель — она отвечает «да».
        # Этот файл проверяет `last_turn_tools`, а отказ судьи разбирает
        # tests/test_action_judge.py.
        with mock.patch.object(Brain, "_ask_plainly", lambda *a, **k: NS(
                choices=[NS(message=NS(content="да"))])):
            return list(brain.reply(text))

    def test_the_document_call_is_visible_with_its_path(self):
        # Голосовой цикл узнаёт «это был разбор документа» и возьмёт отсюда
        # путь и имя — без отдельного инструмента и без решения модели.
        цитата = "прочитай документ и разбери его"
        brain = _мозг([
            _tool_call("read_document", json.dumps(
                {"which": "downloads", "mode": "retell", "because": цитата},
                ensure_ascii=False)),
            [_chunk("Разбор документа.")],
        ])
        with mock.patch("core.hands.run_document", return_value=json.dumps(
                {"ok": True, "name": "отчёт за сентябрь.pdf",
                 "path": ДОКУМЕНТ, "chars": 8400, "text": "текст"},
                ensure_ascii=False)):
            self._ход(brain, цитата)
        self.assertEqual(len(brain.last_turn_tools), 1)
        вызов = brain.last_turn_tools[0]
        self.assertEqual(вызов["name"], "read_document")
        self.assertEqual(вызов["args"], {"which": "downloads", "mode": "retell",
                                         "because": цитата})
        # Ответ инструмента — разобранный словарь, а не строка: `ok` и `path`
        # смотрятся без второго `json.loads`.
        self.assertIsInstance(вызов["result"], dict)
        self.assertTrue(вызов["result"]["ok"])
        self.assertEqual(вызов["result"]["path"], ДОКУМЕНТ)

    def test_a_broken_answer_is_an_empty_dict_not_an_exception(self):
        # Инструмент ответил не-JSON: разбор фразы от этого падать не должен —
        # «не разбор» и «разбор» должны различаться тихо.
        цитата = "прочитай документ и разбери его"
        brain = _мозг([_tool_call("read_document", json.dumps(
            {"which": "downloads", "because": цитата}, ensure_ascii=False)),
            [_chunk("Что-то.")]])
        with mock.patch("core.hands.run_document", return_value="не json"):
            self._ход(brain, цитата)
        self.assertEqual(brain.last_turn_tools[0]["result"], {})

    def test_a_new_turn_clears_the_previous_calls(self):
        # Иначе вопрос про заметку прилетел бы к ходу, где ничего не разбирали:
        # «а что там с погодой» — обычный ответ, а разбор был в прошлый раз.
        цитата = "прочитай документ"
        brain = _мозг([
            _tool_call("read_document", json.dumps(
                {"which": "downloads", "because": цитата}, ensure_ascii=False)),
            [_chunk("Разбор.")],
            [_chunk("Обычный ответ, без инструментов.")],
        ])
        with mock.patch("core.hands.run_document", return_value=json.dumps(
                {"ok": True, "name": "отчёт.pdf", "path": ДОКУМЕНТ,
                 "text": "текст"}, ensure_ascii=False)):
            self._ход(brain, цитата)
        self.assertEqual(len(brain.last_turn_tools), 1)
        self._ход(brain, "а что там с погодой")
        self.assertEqual(brain.last_turn_tools, [])


# --- Источник разбора: какие ходы считаются разбором -------------------------


def _вызовы(имя, аргументы, ответ):
    """Один вызов инструмента в том виде, в каком его отдаёт мозг."""
    return [{"name": имя, "args": аргументы, "result": ответ}]


def _разбор_документа():
    return _вызовы("read_document", {"which": "downloads", "mode": "retell"},
                   {"ok": True, "name": "отчёт за сентябрь.pdf",
                    "path": ДОКУМЕНТ, "text": "текст"})


def _разбор_буфера():
    return _вызовы("read_clipboard", {"mode": "analyze"},
                   {"ok": True, "head": БУФЕР_НАЧАЛО, "chars": 8400})


class ИсточникРазбораTests(unittest.TestCase):
    """`_analysis_source`: путь и имя файла либо первые слова буфера."""

    def _цикл(self, вызовы):
        loop = object.__new__(VoiceLoop)
        loop._brain = NS(last_turn_tools=вызовы)
        return loop

    def test_the_document_gives_its_path_and_name(self):
        # Заголовок заметки «Разбор: отчёт за сентябрь.pdf» и ссылка в тексте
        # берутся отсюда: без пути в заметке было бы только имя файла.
        источник = self._цикл(_разбор_документа())._analysis_source()
        self.assertEqual(источник, {"kind": "document", "path": ДОКУМЕНТ,
                                   "name": "отчёт за сентябрь.pdf"})

    def test_the_name_is_taken_from_the_path_when_the_tool_forgot_it(self):
        # Инструмент мог не отдать имя: путь знает его всегда, а заметке нужно
        # и то и другое.
        вызовы = _вызовы("read_document", {"mode": "retell"},
                         {"ok": True, "path": ДОКУМЕНТ})
        self.assertEqual(self._цикл(вызовы)._analysis_source()["name"],
                         "отчёт за сентябрь.pdf")

    def test_the_clipboard_gives_its_first_words(self):
        # Документа нет — назвать разбор нечем, и в заголовке пишут, по какому
        # тексту он сделан.
        источник = self._цикл(_разбор_буфера())._analysis_source()
        self.assertEqual(источник, {"kind": "clipboard", "head": БУФЕР_НАЧАЛО})

    def test_the_default_mode_counts_too(self):
        # `mode` модель может не назвать: у документа это `retell`, иначе такой
        # ход был бы обычным чтением — и вопроса после него не было бы.
        вызовы = _вызовы("read_document", {"which": "downloads"},
                         {"ok": True, "name": "отчёт.pdf", "path": ДОКУМЕНТ})
        self.assertEqual(self._цикл(вызовы)._analysis_source()["kind"],
                         "document")

    def test_an_ordinary_turn_is_not_an_analysis(self):
        # «Открой папку загрузок» — инструмент позван, ответа `ok` нет, и
        # спрашивать после него не про что. Путь в неудачном ответе тоже может
        # быть: решение «это разбор» принимает флаг `ok`, а не наличие пути.
        for вызовы in ([], _вызовы("open_folder", {"which": "downloads"},
                                   {"ok": True}),
                       _вызовы("read_document", {"mode": "retell"},
                               {"ok": False, "why": "файла нет"}),
                       _вызовы("read_document", {"mode": "retell"},
                               {"ok": False, "path": ДОКУМЕНТ,
                                "name": "отчёт за сентябрь.pdf",
                                "why": "прочитать не удалось"}),
                       _вызовы("read_clipboard", {"mode": "analyze"},
                               {"ok": False, "head": БУФЕР_НАЧАЛО,
                                "why": "буфер пуст"}),
                       _вызовы("read_document", {"mode": "summarize"},
                               {"ok": True, "path": ДОКУМЕНТ}),
                       _вызовы("read_clipboard", {"mode": "translate"},
                               {"ok": True, "head": БУФЕР_НАЧАЛО}),
                       _вызовы("read_document", {"mode": "retell"},
                               {"ok": True, "path": ""})):
            with self.subTest(вызовы=вызовы):
                self.assertIsNone(self._цикл(вызовы)._analysis_source())

    def test_broken_calls_do_not_break_the_phrase(self):
        # Мозг отдаёт вызовы как есть; мусор в них — не повод ронять ход.
        for мусор in ([None, "стока", 5],
                      [{"name": None, "args": "не словарь",
                        "result": "не словарь"}],
                      [{"name": "read_document", "args": {"mode": "retell"},
                        "result": None}]):
            with self.subTest(мусор=мусор):
                self.assertIsNone(self._цикл(мусор)._analysis_source())

    def test_a_brain_without_calls_is_simply_not_an_analysis(self):
        # Цикл, собранный вручную (тесты, подмены), поля не имеет.
        loop = object.__new__(VoiceLoop)
        loop._brain = NS()
        self.assertIsNone(loop._analysis_source())


# --- Вопрос после разбора ----------------------------------------------------


class ВопросПослеРазбораTests(unittest.TestCase):
    """`_ask_analysis_note`: вопрос своим голосом и запомненное предложение."""

    def setUp(self):
        self._было = config.OFFER_ANALYSIS_NOTE
        config.OFFER_ANALYSIS_NOTE = True
        self.addCleanup(setattr, config, "OFFER_ANALYSIS_NOTE", self._было)

    def _цикл(self, вызовы):
        loop = object.__new__(VoiceLoop)
        loop._brain = NS(last_turn_tools=вызовы)
        loop._analysis = None
        loop.said = []
        loop.opened = 0
        loop._say_back = loop.said.append
        loop._open_conversation = lambda: setattr(
            loop, "opened", loop.opened + 1)
        return loop

    def _спросить(self, вызовы, spoken=("Разбор документа.",)):
        loop = self._цикл(вызовы)
        loop._ask_analysis_note(list(spoken))
        return loop

    def test_after_the_analysis_she_asks_with_her_own_voice(self):
        # Вопрос задаёт код, а не модель: у неё нет инструмента для записи
        # разбора, и «записать?» зависело бы от того, как она поняла фразу.
        loop = self._спросить(_разбор_документа())
        self.assertEqual(loop.said, [ANALYSIS_ASK])

    def test_the_offer_remembers_the_source_and_what_she_said(self):
        # В заметку должен лечь тот самый разбор, который хозяин слышал, а не
        # то, что модель вернула в потоке.
        loop = self._спросить(_разбор_документа(),
                              ["Первое предложение.", "Второе."])
        предложение = loop._analysis
        self.assertIsNotNone(предложение)
        self.assertEqual(предложение["source"],
                         {"kind": "document", "path": ДОКУМЕНТ,
                          "name": "отчёт за сентябрь.pdf"})
        self.assertEqual(предложение["text"],
                         "Первое предложение. Второе.")
        self.assertGreaterEqual(предложение["at"], 0.0)

    def test_the_clipboard_offer_remembers_the_first_words(self):
        loop = self._спросить(_разбор_буфера())
        self.assertEqual(loop._analysis["source"],
                         {"kind": "clipboard", "head": БУФЕР_НАЧАЛО})

    def test_the_window_stays_open_so_the_answer_needs_no_name(self):
        # Имя он сейчас вряд ли вспомнит: только что слушал разбор.
        loop = self._спросить(_разбор_документа())
        self.assertEqual(loop.opened, 1)

    def test_the_switched_off_setting_asks_nothing(self):
        config.OFFER_ANALYSIS_NOTE = False
        loop = self._спросить(_разбор_документа())
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_a_silence_is_not_an_analysis(self):
        # Всё молчание ушло в фон и на озвучку не легло — предлагать нечего.
        loop = self._спросить(_разбор_документа(), [])
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_an_ordinary_answer_asks_nothing(self):
        loop = self._спросить([])
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)


# --- Условия в `_answer`: когда вопроса быть не должно -----------------------


class _Тихо:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _Голос:
    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        return np.zeros(240, dtype=np.float32), 24000


class _Динамик:
    gapless = False

    def __init__(self):
        self.calls = 0
        self.idle = threading.Event()
        self.idle.set()
        # Кто-то прерывает её на середине: `_answer` обязан это увидеть.
        self.прервать = None

    def say(self, wave, rate, gap=True):
        self.calls += 1
        if self.прервать is not None:
            self.прервать()

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Отвечающий:
    """Мозг, который говорит заготовку и помнит вызовы последнего хода."""

    ended = False
    not_to_me = False
    searched = False

    def __init__(self, sentences, вызовы=None):
        self.sentences = list(sentences)
        self.last_turn_tools = list(вызовы or [])
        self.last_timing = {"rounds": [{"word": 0.0, "sentence": 0.0}]}

    def reply(self, text, **kwargs):
        yield from self.sentences


def _цикл(мозг, speaker=None):
    """Голосовой цикл из заглушек: ни сети, ни звука, ни облака."""
    loop = object.__new__(VoiceLoop)
    loop._brain = мозг
    loop._voice = _Голос()
    loop._ref = None
    loop._speaker = speaker or _Динамик()
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
    loop._analysis = None
    loop._voice_score = None
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._digest_later = lambda: None
    loop.said = []
    loop._say_back = loop.said.append
    loop._tell_phone = lambda state="", who="", text="", sound="": None
    return loop


class ВопросИзОтветаTests(unittest.TestCase):
    """`_answer`: вопрос только после целого, договорённого ответа."""

    def setUp(self):
        self._было = config.OFFER_ANALYSIS_NOTE
        config.OFFER_ANALYSIS_NOTE = True
        self.addCleanup(setattr, config, "OFFER_ANALYSIS_NOTE", self._было)

    def test_a_finished_analysis_ends_with_the_question(self):
        loop = _цикл(_Отвечающий(["Разбор документа."], _разбор_документа()))
        loop._answer("прочитай документ и разбери его")
        self.assertEqual(loop.said, [ANALYSIS_ASK])
        self.assertEqual(loop._analysis["text"], "Разбор документа.")

    def test_an_ordinary_answer_asks_nothing(self):
        loop = _цикл(_Отвечающий(["Погода в Москве +18."]))
        loop._answer("какая погода")
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_an_interrupted_analysis_is_not_offered(self):
        # Перебили на полуслове: половина разбора разбором не считается, и
        # предлагать её сохранить незачем.
        speaker = _Динамик()
        loop = _цикл(_Отвечающий(["Разбор доку", "мента."],
                                 _разбор_документа()), speaker)
        speaker.прервать = loop._interrupt.set
        loop._answer("прочитай документ")
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_a_stopped_analysis_is_not_offered(self):
        # «Хватит» — то же самое: она замолчала, и предлагать нечего.
        speaker = _Динамик()
        loop = _цикл(_Отвечающий(["Разбор доку", "мента."],
                                 _разбор_документа()), speaker)
        speaker.прервать = loop._stop.set
        loop._answer("прочитай документ")
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_a_broken_analysis_is_not_offered(self):
        # Ответ, кончившийся ошибкой, разбором не считается.
        class Сломанный(_Отвечающий):
            def reply(self, *args, **kwargs):
                raise RuntimeError("Error code: 403 - unsupported_country")
                yield

        loop = _цикл(Сломанный([], _разбор_документа()))
        loop._answer("прочитай документ")
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)

    def test_the_switched_off_setting_asks_nothing(self):
        config.OFFER_ANALYSIS_NOTE = False
        loop = _цикл(_Отвечающий(["Разбор документа."], _разбор_документа()))
        loop._answer("прочитай документ")
        self.assertEqual(loop.said, [])
        self.assertIsNone(loop._analysis)


# --- Ответ «да/нет» разбирается на словах -----------------------------------

ФРАЗА = np.zeros(8, dtype=np.float32)


class ОтветДаНетTests(unittest.TestCase):
    """`_analysis_turn`: короткий ответ на её собственный вопрос."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-analysis-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._папка = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(setattr, config, "NOTES_DIR", self._папка)
        self.loop = _цикл(_Отвечающий([]))
        self.loop.remembered = []
        self.loop._remember = (
            lambda said, answered: self.loop.remembered.append((said, answered)))

    def _предложить(self, at=None):
        self.loop._analysis = {
            "source": {"kind": "document", "path": ДОКУМЕНТ,
                       "name": "отчёт за сентябрь.pdf"},
            "text": "Разбор документа.",
            "at": time.monotonic() if at is None else at,
        }
        return self.loop._analysis

    def _запись(self):
        """Подмена записи: тест проверяет разбор фразы, а не диск."""
        return mock.patch("core.notes.add_analysis",
                          return_value=self.tmp / "Разборы" / "отчёт.md")

    def _свежий(self):
        """Новый цикл на каждую фразу: предложение живёт одну фразу."""
        # Списки у нового цикла свои, и `_say_back` смотрит именно на них.
        self.loop = _цикл(_Отвечающий([]))
        self.loop.remembered = []
        self.loop._remember = (
            lambda said, answered: self.loop.remembered.append((said, answered)))
        self._предложить()

    def test_yes_saves_the_note_without_the_model(self):
        # Второй ход такой же локальный, как у питания компьютера: модель не
        # слышала паузу между вопросом и ответом, и «не надо» всерьёз перепутала
        # бы с просьбой.
        self._предложить()
        with self._запись() as запись:
            self.assertTrue(self.loop._analysis_turn("да"))
        запись.assert_called_once_with(
            {"kind": "document", "path": ДОКУМЕНТ,
             "name": "отчёт за сентябрь.pdf"}, "Разбор документа.")
        self.assertEqual(self.loop.said, [ANALYSIS_SAVED])
        # В историю — что она записала, а не что ответил хозяин.
        self.assertEqual(self.loop.remembered, [("да", ANALYSIS_SAVED)])

    def test_every_short_yes_saves(self):
        # Слова из задания и «да, пожалуйста»: согласие — когда ВСЕ слова
        # ответа из списка.
        for фраза in ("да", "давай", "добавь", "ага", "конечно", "сохрани",
                      "запиши", "угу", "да, давай", "ага, запиши"):
            with self.subTest(фраза=фраза):
                self._свежий()
                with self._запись() as запись:
                    self.assertTrue(self.loop._analysis_turn(фраза), фраза)
                запись.assert_called_once()

    def test_the_journal_gets_the_topic_and_not_the_analysis(self):
        # Разбор — слова о чужом документе, и в журнале ему не место: там тема,
        # по которой запись найдётся.
        self._предложить()
        with mock.patch("core.notes.add_analysis",
                        return_value=self.tmp / "Разборы" / "отчёт за сентябрь.md"):
            self.loop._analysis_turn("да")
        события = [payload for вид, payload in self.loop.events
                   if вид == "analysis_saved"]
        self.assertEqual(события, ["отчёт за сентябрь"])
        self.assertEqual(
            WebRuntime._log_messages("analysis_saved", события[0]),
            ["разбор сохранён в заметки: отчёт за сентябрь"])
        self.assertNotIn("Разбор документа.", события[0])

    def test_no_says_so_and_writes_nothing(self):
        # Отказ узнаётся по первому слову: «не надо» и «не нужно» — это отказ,
        # а не просьба.
        for фраза in ("нет", "не надо", "не нужно", "неа", "отмена",
                      "нет, спасибо"):
            with self.subTest(фраза=фраза):
                self._свежий()
                with mock.patch("core.notes.add_analysis") as запись:
                    self.assertTrue(self.loop._analysis_turn(фраза), фраза)
                запись.assert_not_called()
                self.assertEqual(self.loop.said, [ANALYSIS_NO])

    def test_another_phrase_drops_the_offer_and_goes_on(self):
        # «А что там с погодой» — не ответ на её вопрос: фраза уходит в модель, а
        # предложение забыто, иначе забытое «да» сработало бы позже.
        self._предложить()
        with mock.patch("core.notes.add_analysis") as запись:
            self.assertFalse(self.loop._analysis_turn("а что там с погодой"))
        запись.assert_not_called()
        self.assertEqual(self.loop.said, [])
        self.assertIsNone(self.loop._analysis)

    def test_a_long_phrase_is_not_an_answer_at_all(self):
        # «Да, и заодно запусти браузер» ушло бы в модель вместе с записью
        # разбора: это уже не ответ на заданный вопрос. Во второй фразе все
        # слова из списка согласия — отличает именно длина, а не состав.
        for фраза in ("да, и заодно запусти браузер",
                      "да давай добавь ага угу"):
            with self.subTest(фраза=фраза):
                self._свежий()
                with mock.patch("core.notes.add_analysis") as запись:
                    self.assertFalse(self.loop._analysis_turn(фраза))
                запись.assert_not_called()
                self.assertIsNone(self.loop._analysis)

    def test_the_offer_lives_one_phrase_only(self):
        # Вторая фраза — уже не ответ: «да» после «а что там с погодой» молчит.
        self._предложить()
        with mock.patch("core.notes.add_analysis"):
            self.loop._analysis_turn("а что там с погодой")
            with self._запись() as запись:
                self.assertFalse(self.loop._analysis_turn("да"))
            запись.assert_not_called()

    def test_a_forgotten_yes_does_nothing_after_the_minute(self):
        # За минуту хозяин забыл, о чём его спрашивали: «да» уже не про это.
        self._предложить(at=time.monotonic() - ANALYSIS_TTL - 1.0)
        with mock.patch("core.notes.add_analysis") as запись:
            self.assertFalse(self.loop._analysis_turn("да"))
        запись.assert_not_called()
        self.assertIsNone(self.loop._analysis)

    def test_a_fresh_offer_still_works_after_the_minute(self):
        # Граница не перепутана: предложение, заданное только что, минуту ещё
        # ждёт ответа.
        self._предложить(at=time.monotonic() - ANALYSIS_TTL + 1.0)
        with self._запись() as запись:
            self.assertTrue(self.loop._analysis_turn("да"))
        запись.assert_called_once()

    def test_without_an_offer_the_phrase_is_ordinary(self):
        # «Да» без разбора ушло бы в облако и пропало бы там.
        self.loop._analysis = None
        with mock.patch("core.notes.add_analysis") as запись:
            self.assertFalse(self.loop._analysis_turn("да"))
        запись.assert_not_called()

    def test_a_failed_write_is_told_honestly(self):
        # Хозяин должен знать, что записи не будет, а не думать, что она есть.
        self._предложить()
        with mock.patch("core.notes.add_analysis",
                        side_effect=OSError("диск занят")):
            self.assertTrue(self.loop._analysis_turn("да"))
        self.assertEqual(self.loop.said, [ANALYSIS_FAILED])
        self.assertTrue(any(вид == "error" for вид, _ in self.loop.events))


# --- Второй ход стоит раньше модели и разбора команд -------------------------


class ВторойХодTests(unittest.TestCase):
    """`_turn_body`: «да» закрывает ход здесь и не доходит до облака."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-turn-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._папка = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(setattr, config, "NOTES_DIR", self._папка)
        self.loop = _цикл(_Отвечающий([]))
        self.loop.звонки = []
        self.loop._analysis = {
            "source": {"kind": "document", "path": ДОКУМЕНТ,
                       "name": "отчёт за сентябрь.pdf"},
            "text": "Разбор документа.", "at": time.monotonic()}
        self.loop._power_turn = lambda text: False
        self.loop._known_apps = lambda: []
        self.loop._run_command = lambda order, text: None
        self.loop._answer = lambda text, **kw: self.loop.звонки.append(text)

    def test_yes_is_answered_here_and_the_model_is_not_called(self):
        with mock.patch("core.notes.add_analysis",
                        return_value=self.tmp / "Разборы" / "отчёт.md"):
            self.loop._turn_body("да", ФРАЗА, 0.0, 0.0, True)
        self.assertEqual(self.loop.звонки, [])
        self.assertEqual(self.loop.said, [ANALYSIS_SAVED])

    def test_another_phrase_goes_on_as_usual(self):
        self.loop._turn_body("а что там с погодой", ФРАЗА, 0.0, 0.0, True)
        self.assertEqual(self.loop.звонки, ["а что там с погодой"])
        self.assertEqual(self.loop.said, [])
        self.assertIsNone(self.loop._analysis)


# --- Что ляжет на диск -------------------------------------------------------


class ЗаметкаРазбораTests(unittest.TestCase):
    """`notes.add_analysis` — во временной папке, как и остальные тесты."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-note-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._папка = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(setattr, config, "NOTES_DIR", self._папка)
        self.ССЫЛКА = ("Документ: [отчёт за сентябрь.pdf]"
                      "(<file:///D:/Работа/Мои документы/"
                      "отчёт за сентябрь.pdf>)")

    def test_the_document_note_names_the_file_and_links_to_it(self):
        путь = notes.add_analysis(
            {"kind": "document", "path": ДОКУМЕНТ,
             "name": "отчёт за сентябрь.pdf"}, "Разбор документа.")
        self.assertEqual(путь, self.tmp / "Разборы" / "отчёт за сентябрь.md")
        тело = путь.read_text(encoding="utf-8")
        self.assertIn("тема: отчёт за сентябрь", тело)
        self.assertIn("раздел: Разборы", тело)
        self.assertIn("— Разбор: отчёт за сентябрь.pdf", тело)
        # Ссылка первой строкой, путь с пробелами — в угловых скобках, а слэши
        # прямые: иначе Obsidian не откроет файл.
        self.assertIn(self.ССЫЛКА, тело)
        хвост = тело.split("— Разбор: отчёт за сентябрь.pdf", 1)[1]
        строки = [с.rstrip() for с in хвост.splitlines()]
        self.assertEqual([с for с in строки[1:6] if с],
                         [self.ССЫЛКА, "Разбор документа."])

    def test_the_clipboard_note_says_which_text_it_was(self):
        # Документа нет — в заголовке пишут, по какому тексту сделан разбор.
        путь = notes.add_analysis({"kind": "clipboard", "head": БУФЕР_НАЧАЛО},
                                  "Разбор скопированного.")
        self.assertEqual(путь, self.tmp / "Разборы" / "Скопированное.md")
        тело = путь.read_text(encoding="utf-8")
        self.assertIn(f"— Разбор скопированного: «{БУФЕР_НАЧАЛО}»", тело)
        self.assertNotIn("Документ:", тело)
        self.assertIn("Разбор скопированного.", тело)

    def test_the_section_appears_on_disk_by_itself(self):
        # Папка «Разборы» создаётся первой записью и сразу видна в пульте:
        # разделы читаются с диска, а не из списка в коде.
        self.assertNotIn("Разборы", notes.DEFAULT_SECTIONS)
        self.assertEqual(notes.sections(), [])
        notes.add_analysis({"kind": "clipboard", "head": БУФЕР_НАЧАЛО}, "Разбор.")
        разделы = notes.sections()
        self.assertEqual([р["name"] for р in разделы], ["Разборы"])
        self.assertEqual(разделы[0]["topics"][0]["name"], "Скопированное")

    def test_an_empty_analysis_is_not_written(self):
        # Молчание — не разбор, и пустую запись в заметках оставлять нечего.
        with self.assertRaises(ValueError):
            notes.add_analysis({"kind": "clipboard", "head": БУФЕР_НАЧАЛО}, "  ")
        self.assertEqual(notes.sections(), [])


# --- Настройка и выключатель в пульте ----------------------------------------


def _runtime() -> WebRuntime:
    """Пульт без живого окружения: только замки и заглушки вместо голоса."""
    runtime = object.__new__(WebRuntime)
    runtime._provider_test_lock = threading.Lock()
    runtime._lock = threading.Lock()
    runtime.server = mock.Mock()
    runtime.brain = None
    runtime.voice = NS(_brain=None, _close_conversation=lambda: None,
                       running=False)
    runtime._enroll = None
    runtime._jobs = {}
    runtime._audio_stale = False
    runtime._remember = lambda kind, payload: None
    return runtime


class НастройкаРазбораTests(unittest.TestCase):
    """`offer_analysis_note`: по умолчанию включена, мусор её не выключает."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-settings-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json",
            ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        # `apply_to_config` переносит в config и папку заметок — возвращаем.
        self._было = (config.NOTES_DIR, config.OFFER_ANALYSIS_NOTE)
        self.addCleanup(self._вернуть)
        self.runtime = _runtime()

    def _вернуть(self):
        config.NOTES_DIR, config.OFFER_ANALYSIS_NOTE = self._было

    def test_it_is_on_by_default(self):
        # Вопрос задаёт код, и без этой настройки она просто забывала бы о
        # разборах — молча, а хозяин ничего не сказал бы.
        self.assertTrue(config.OFFER_ANALYSIS_NOTE)
        self.assertIs(settings.DEFAULTS["offer_analysis_note"], True)

    def test_junk_keeps_it_on(self):
        # Правка руками или значение из будущей версии не должны молча выключить
        # вопрос: выключить его можно и в пульте.
        for мусор in ("нет", 0, None, [], "false"):
            with self.subTest(мусор=мусор):
                self.assertIs(settings.validate_offer_analysis_note(мусор), True)

    def test_junk_in_the_file_keeps_it_on(self):
        settings.SETTINGS_PATH.write_text(
            '{"offer_analysis_note": "нет"}', encoding="utf-8")
        self.assertIs(settings.load_settings()["offer_analysis_note"], True)
        settings.apply_to_config()
        self.assertTrue(config.OFFER_ANALYSIS_NOTE)

    def test_the_pult_saves_a_boolean_and_rejects_anything_else(self):
        self.assertTrue(self.runtime.save_settings(
            {"offer_analysis_note": False})["ok"])
        self.assertFalse(config.OFFER_ANALYSIS_NOTE)
        сохранено = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertIs(сохранено["offer_analysis_note"], False)
        ответ = self.runtime.save_settings({"offer_analysis_note": "нет"})
        self.assertFalse(ответ["ok"])
        self.assertEqual(ответ["errors"], ["offer_analysis_note: нужно true/false"])


class ПереключательВПультеTests(unittest.TestCase):
    """Сам выключатель: подпись на странице заметок и проба в node."""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node не найден")
        cls.скрипт = PULT.read_text(encoding="utf-8")

    def test_the_switch_is_where_the_notes_settings_are(self):
        # Подпись простыми словами, рядом с папкой заметок: хозяин ищет её там,
        # где уже ищет остальное про заметки.
        for кусок in ("Спрашивать, добавить ли разбор в заметки",
                      "заметкиЗагрузитьРазбор", "заметкиСпрашиватьРазбор",
                      "спроситьРазбор: спроситьРазбор",
                      "спроситьРазбор.addEventListener('change'"):
            with self.subTest(кусок=кусок):
                self.assertIn(кусок, self.скрипт)

    def test_the_switch_follows_the_setting_in_a_probe(self):
        # node режет функции из pult.js целиком и гоняет их в подставном
        # окружении: сервер и DOM — подмены, ни сети, ни диска.
        итог = subprocess.run(
            [self.node, str(ПРОБА)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(config.ROOT),
            timeout=120)
        self.assertEqual(итог.returncode, 0, итог.stdout + итог.stderr)
        self.assertIn("Analysis note switch OK", итог.stdout)


if __name__ == "__main__":
    unittest.main()
