"""Карточка поиска на телефоне: после любого поиска и с касанием (30.09).

Хозяин: «хотелось бы, чтобы она после поиска показывала на телефоне, что
нашла, и можно было тыкнуть на её запрос, чтобы в браузере открылся этот
запрос». Проверяем четыре уровня:

- **мозг**: после похода в интернет у него есть `searched` и `last_queries` —
  запросы, что реально ушли в поисковик (их пишет модель сама), а не слова
  хозяина;
- **голосовой цикл**: карточка уходит и после кнопки «Найти», и после того,
  как она искала сама, и **не** уходит после ответа без поиска;
- **сервер**: `open_search` строит адрес в поисковике (кириллица
  кодируется), `open_source` берёт адрес из **своего** последнего списка по
  номеру и отказывает всему, что не из списка;
- **страница телефона**: строки карточки шлют `open_search`/`open_source`,
  касание карточки её не закрывает, таймер 60 с.

Ни сети, ни микрофона, ни **настоящих вкладок**: `launcher.open_web`
подменён, `web.run_tool` подменён, страница только читается и разбирается
`node --check` (как в `tests/test_phone_access.py`).
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
from urllib.parse import quote_plus

import numpy as np

import config
from core import launcher, web
from core.brain import Brain
from core.phone import PhoneServer
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

ROOT = Path(config.ROOT)

FOUND = json.dumps({
    "query": "курс доллара",
    "results": [{"title": "Курс ЦБ", "url": "https://cbr.ru/rates",
                 "snippet": "92 рубля"}],
}, ensure_ascii=False)


# --- Подставное облако ----------------------------------------------------


def _чан(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _вызов(имя, аргументы="{}", call_id="c1"):
    """Один круг ответа: модель зовёт инструмент."""
    return [_чан(calls=[NS(index=0, id=call_id,
                           function=NS(name=имя, arguments=аргументы))])]


def _реплика(текст):
    """Один круг ответа: модель говорит (слова приходят куском)."""
    return [_чан(текст)]


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
    """Настоящий `Brain` без облака, без сети и без записи истории."""
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
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {}
    brain.last_sources = []
    brain.searched = False
    brain.last_queries = []
    return brain



# --- Мозг помнит, что искали ----------------------------------------------


class МозгЗапоминаетПоискTests(unittest.TestCase):
    """Ответ, в котором она ходила в интернет, помечен, и известен запрос."""

    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = True
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))
        self._поиск = mock.patch.object(web, "run_tool", return_value=FOUND)
        self._поиск.start()
        self.addCleanup(self._поиск.stop)

    def _поиск_модели(self):
        return _вызов("web_search",
                      json.dumps({"query": "курс доллара"}, ensure_ascii=False))

    def test_the_models_own_search_is_remembered(self):
        # Модель сама позвала `web_search` — это тоже поиск, и телефон должен
        # получить карточку (раньше получал только кнопку «Найти»).
        brain = _мозг(self._поиск_модели(), _реплика("Девяносто два рубля."))
        list(brain.reply("сколько стоит доллар"))
        self.assertTrue(brain.searched)
        self.assertEqual(brain.last_queries, ["курс доллара"])
        self.assertEqual(brain.last_sources[0]["host"], "cbr.ru")

    def test_an_answer_without_a_search_is_not_marked(self):
        brain = _мозг(_реплика("Нормально."))
        list(brain.reply("как дела?"))
        self.assertFalse(brain.searched)
        self.assertEqual(brain.last_queries, [])
        self.assertEqual(brain.last_sources, [])

    def test_the_next_answer_forgets_the_previous_search(self):
        # Иначе карточка пришла бы на «спасибо» — с чужими источниками.
        brain = _мозг(self._поиск_модели(), _реплика("Девяносто два рубля."),
                      _реплика("Пожалуйста."))
        list(brain.reply("сколько стоит доллар"))
        list(brain.reply("спасибо"))
        self.assertFalse(brain.searched)
        self.assertEqual(brain.last_queries, [])
        self.assertEqual(brain.last_sources, [])

    def test_the_fast_search_of_the_button_remembers_its_queries(self):
        # Путь кнопки «Найти»: запросы пишет модель в `_search_queries`, и
        # именно они должны уйти в карточку, а не фраза хозяина.
        brain = _мозг(_реплика("Девяносто два рубля."))
        brain._search_queries = lambda text, query: ["курс доллара 30 сентября"]
        list(brain.reply("курс доллара", search=True))
        self.assertTrue(brain.searched)
        self.assertEqual(brain.last_queries, ["курс доллара 30 сентября"])


# --- Голосовой цикл --------------------------------------------------------


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


class _МозгЦикла:
    """Мозг-заглушка: говорит заготовленное и помнит, что искал."""

    ended = False
    not_to_me = False

    def __init__(self, sentences=(), searched=False, last_queries=(),
                 last_sources=()):
        self.sentences = list(sentences)
        self.searched = searched
        self.last_queries = list(last_queries)
        self.last_sources = list(last_sources)
        self.calls = []

    def reply(self, text, **kwargs):
        self.calls.append((text, kwargs))
        self.last_timing = {"rounds": [{"sent": 0.0, "word": 0.0,
                                         "sentence": 0.0}]}
        yield from self.sentences


class _Телефон:
    def __init__(self):
        self.results = []

    def send_search_result(self, query, answer, sources=None, asked=""):
        self.results.append({"query": query, "answer": answer,
                             "sources": list(sources or []), "asked": asked})

    def send_search_fail(self, text):
        pass


def _цикл(мозг, телефон=None):
    """Голосовой цикл из одних заглушек: ни сети, ни звука, ни облака."""
    loop = object.__new__(VoiceLoop)
    loop._brain = мозг
    loop._voice = _Голос()
    loop._ref = None
    loop._speaker = _Динамик()
    loop._fallback = None
    loop._server = телефон
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
    loop._tell_phone = lambda *args, **kwargs: None
    loop._digest_later = lambda: None
    return loop


class КарточкаУходитTests(unittest.TestCase):
    """Когда карточка приходит на телефон, а когда нет."""

    def setUp(self):
        self._saved = config.OUTPUT
        config.OUTPUT = "speakers"
        self.addCleanup(lambda: setattr(config, "OUTPUT", self._saved))

    def test_after_the_search_button(self):
        телефон = _Телефон()
        loop = _цикл(_МозгЦикла(["Девяносто два."]), телефон)
        loop._answer("курс доллара", search=True)
        self.assertEqual(len(телефон.results), 1)
        self.assertEqual(телефон.results[0]["answer"], "Девяносто два.")

    def test_after_a_search_of_her_own(self):
        # Без кнопки: она сама сходила в интернет инструментом — карточка
        # всё равно нужна (30.09, «после любого поиска»).
        телефон = _Телефон()
        мозг = _МозгЦикла(["Девяносто два."], searched=True,
                           last_queries=["курс доллара"],
                           last_sources=[{"title": "Курс ЦБ", "host": "cbr.ru",
                                          "url": "https://cbr.ru/rates"}])
        loop = _цикл(мозг, телефон)
        loop._answer("ну сколько там доллар")
        self.assertEqual(len(телефон.results), 1)
        карточка = телефон.results[0]
        self.assertEqual(карточка["query"], "курс доллара")
        self.assertEqual(карточка["asked"], "ну сколько там доллар")
        self.assertEqual(карточка["sources"][0]["host"], "cbr.ru")

    def test_not_after_an_answer_without_a_search(self):
        телефон = _Телефон()
        loop = _цикл(_МозгЦикла(["Нормально."]), телефон)
        loop._answer("как дела?")
        self.assertEqual(телефон.results, [])

    def test_no_phone_means_no_card_and_no_crash(self):
        # Сервера нет (телефон не подключался) — молча, но без ошибки.
        loop = _цикл(_МозгЦикла(["Нормально."], searched=True))
        loop._answer("как дела?")



# --- Действия телефона: открыть на компьютере ------------------------------


def _среда(источники=()):
    """Пульт без голоса: нужны браузер, тосты и журнал."""
    runtime = object.__new__(WebRuntime)
    runtime._lock = threading.Lock()
    # Потоки не нужны: `_bg` выполняет сразу, иначе тест проверял бы
    # планировщик, а не выполнение.
    runtime._bg = lambda func, *args: func(*args)
    runtime._ids = iter(range(1, 100))
    runtime._overview = {}
    runtime.тосты = []
    runtime.звуки = []
    runtime.строки = []
    runtime.log_message = runtime.строки.append
    runtime.server = NS(
        last_sources=list(источники),
        send_toast=lambda text, ok=True: runtime.тосты.append((text, ok)),
        send_sound=lambda name: runtime.звуки.append(name),
    )
    return runtime


class ОткрытьПоискTests(unittest.TestCase):
    """`open_search`: запрос из карточки → адрес в поисковике."""

    def setUp(self):
        подмена = mock.patch.object(launcher, "open_web",
                                    return_value=(True, "браузер"))
        self.браузер = подмена.start()
        self.addCleanup(подмена.stop)

    def _адрес(self, запрос):
        среда = _среда()
        среда.handle_event("open_search", {"query": запрос})
        self.assertEqual(len(self.браузер.call_args_list), 1)
        return self.браузер.call_args_list[0][0][0]

    def test_the_query_goes_to_the_search_engine(self):
        адрес = self._адрес("курс доллара")
        self.assertTrue(адрес.startswith("https://www.google.com/search?q="))
        self.assertIn(quote_plus("курс доллара"), адрес)

    def test_cyrillic_is_encoded(self):
        # Кириллица в адресе браузер не поймёт, а `%`-коды открывают ровно
        # тот же поиск.
        адрес = self._адрес("курс доллара")
        self.assertNotIn(" ", адрес)
        self.assertNotIn("курс", адрес)
        self.assertIn("%D0%BA%D1%83%D1%80%D1%81", адрес)

    def test_the_search_engine_is_taken_from_the_settings(self):
        saved = config.SEARCH_OPEN_URL
        config.SEARCH_OPEN_URL = "https://yandex.ru/search/?text={q}"
        self.addCleanup(lambda: setattr(config, "SEARCH_OPEN_URL", saved))
        self.assertTrue(self._адрес("курс").startswith(
            "https://yandex.ru/search/?text="))

    def test_the_phone_is_told_it_opened_on_the_computer(self):
        среда = _среда()
        среда.handle_event("open_search", {"query": "курс доллара"})
        self.assertEqual(среда.тосты[-1], ("Открыла на компе", True))
        self.assertIn("поиск открыт в браузере: курс доллара", среда.строки)

    def test_an_empty_query_is_refused(self):
        среда = _среда()
        среда.handle_event("open_search", {"query": "   "})
        self.браузер.assert_not_called()
        self.assertEqual(среда.тосты[-1][1], False)
        self.assertIn("fail", среда.звуки)

    def test_a_broken_search_engine_url_is_refused(self):
        saved = config.SEARCH_OPEN_URL
        config.SEARCH_OPEN_URL = "file:///C:/Windows/System32"
        self.addCleanup(lambda: setattr(config, "SEARCH_OPEN_URL", saved))
        среда = _среда()
        среда.handle_event("open_search", {"query": "курс доллара"})
        self.браузер.assert_not_called()
        self.assertEqual(среда.тосты[-1][1], False)

    def test_garbage_instead_of_a_query_is_refused(self):
        среда = _среда()
        среда.handle_event("open_search", "не словарь")
        self.браузер.assert_not_called()
        self.assertEqual(среда.тосты[-1][1], False)




class ОткрытьИсточникTests(unittest.TestCase):
    """`open_source`: номер из последнего списка, а не адрес с телефона."""

    ИСТОЧНИКИ = [
        {"title": "Курс ЦБ", "host": "cbr.ru", "url": "https://cbr.ru/rates"},
        {"title": "Курсы на сегодня", "host": "cbr.ru",
         "url": "https://cbr.ru/today"},
    ]

    def setUp(self):
        подмена = mock.patch.object(launcher, "open_web",
                                    return_value=(True, "браузер"))
        self.браузер = подмена.start()
        self.addCleanup(подмена.stop)

    def _открыт(self, payload, источники=None):
        среда = _среда(self.ИСТОЧНИКИ if источники is None else источники)
        среда.handle_event("open_source", payload)
        return среда

    def test_the_number_of_the_row_opens_its_own_address(self):
        среда = self._открыт({"index": 1})
        self.браузер.assert_called_once_with("https://cbr.ru/today")
        self.assertEqual(среда.тосты[-1], ("Открыла на компе", True))
        self.assertIn("источник открыт: cbr.ru", среда.строки)

    def test_a_number_outside_the_list_is_refused(self):
        for номер in (2, 99, -1):
            with self.subTest(номер=номер):
                self.браузер.reset_mock()
                среда = self._открыт({"index": номер})
                self.браузер.assert_not_called()
                self.assertEqual(среда.тосты[-1][1], False)

    def test_garbage_instead_of_a_number_is_refused(self):
        for значение in ("один", None, 1.5):
            with self.subTest(значение=значение):
                self.браузер.reset_mock()
                среда = self._открыт({"index": значение})
                self.браузер.assert_not_called()
                self.assertEqual(среда.тосты[-1][1], False)

    def test_an_empty_list_has_nothing_to_open(self):
        self.браузер.reset_mock()
        среда = self._открыт({"index": 0}, источники=[])
        self.браузер.assert_not_called()
        self.assertEqual(среда.тосты[-1][1], False)

    def test_the_phone_cannot_name_its_own_address(self):
        # Ключ `url` в сообщении сервер не читает: адрес только из своего
        # списка, по номеру. Иначе страница с телефона заставила бы комп
        # открыть что угодно.
        среда = self._открыт({"index": 0, "url": "https://чужой.example/x"})
        self.браузер.assert_called_once_with("https://cbr.ru/rates")
        self.assertIn("источник открыт: cbr.ru", среда.строки)

    def test_a_list_entry_that_is_not_a_dict_is_refused(self):
        self.браузер.reset_mock()
        среда = self._открыт({"index": 0}, источники=["https://cbr.ru/rates"])
        self.браузер.assert_not_called()
        self.assertEqual(среда.тосты[-1][1], False)


class БраузерTests(unittest.TestCase):
    """`launcher.open_web` — тот же путь, что у закладок, и только http(s)."""

    def test_the_default_browser_is_opened(self):
        with mock.patch.object(launcher, "os") as os_:
            ok, what = launcher.open_web("https://cbr.ru/rates")
        self.assertTrue(ok)
        self.assertEqual(what, "браузер")
        os_.startfile.assert_called_once_with("https://cbr.ru/rates")

    def test_only_http_addresses_are_opened(self):
        for адрес in ("file:///C:/Windows/System32", "javascript:alert(1)",
                      "ftp://example.com/x", "", None, 5):
            with self.subTest(адрес=адрес):
                with mock.patch.object(launcher, "os") as os_:
                    ok, what = launcher.open_web(адрес)
                self.assertFalse(ok)
                self.assertIn("не открываю", what)
                os_.startfile.assert_not_called()




# --- Страница телефона ------------------------------------------------------


def _скрипты(страница):
    return re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", страница, re.S)


class СтраницаКарточкиTests(unittest.TestCase):
    """Скрипт страницы телефона: строки карточки шлят, а не просто светятся."""

    @classmethod
    def setUpClass(cls):
        cls.страница = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def _код(self, имя, длина=2000):
        начало = self.страница.find("function " + имя + "(")
        self.assertGreaterEqual(начало, 0, "функция %s не найдена" % имя)
        return self.страница[начало:начало + длина]

    def test_the_script_still_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        скрипты = _скрипты(self.страница)
        self.assertTrue(скрипты)
        for номер, код in enumerate(скрипты):
            with tempfile.TemporaryDirectory() as папка:
                файл = Path(папка) / "page.js"
                файл.write_text(код, encoding="utf-8")
                итог = subprocess.run([node, "--check", str(файл)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(итог.returncode, 0,
                             f"скрипт {номер}: {итог.stderr[:400]}")

    def test_the_query_row_asks_the_computer_to_open_the_search(self):
        self.assertIn("send({ type: 'open_search', query: findQuery })",
                      self.страница)

    def test_the_source_rows_send_only_their_number(self):
        блок = self._код("showSearch")
        self.assertIn("send({ type: 'open_source', index: номер })", блок)
        # Адрес с телефона не отправляем вовсе: только номер.
        self.assertNotIn("open_source', url", блок)
        self.assertNotIn("source.url", блок)

    def test_the_card_hangs_a_minute_and_a_touch_extends_it(self):
        self.assertIn("const FIND_HIDE_MS = 60000;", self.страница)
        # Каждое касание строки перевзводит таймер, а не гасит карточку.
        self.assertGreaterEqual(self.страница.count("armFind();"), 3)

    def test_a_touch_of_the_card_does_not_close_it(self):
        # Закрывает только пустое место вокруг карточки и крестик.
        self.assertIn("findBox.classList.remove('on')", self._код("closeFind"))
        self.assertIn("if (event.target.closest('.card')) return;", self.страница)
        # И ряд запроса, и источник останавливают событие — иначе касание
        # строки открыло бы и тут же закрыло карточку.
        self.assertGreaterEqual(self.страница.count("event.stopPropagation();"), 3)

    def test_the_rows_click_loudly_like_other_buttons(self):
        # Щелчок под палец вешается одним обработчиком на всю страницу
        # (`document.addEventListener('pointerdown', …)`) — строки карточки
        # должны быть в его списке. Ищем именно его: `'pointerdown'` есть и
        # у других кнопок (удержание пункта меню).
        начало = self.страница.find("document.addEventListener(\n  'pointerdown'")
        self.assertGreaterEqual(начало, 0, "обработчик щелчка не найден")
        блок = self.страница[начало:начало + 900]
        for имя in ("#find .qrow", "#find .srow", "#find .x"):
            self.assertIn(имя, блок)
        self.assertIn("playSound('tap')", блок)

    def test_the_query_row_says_where_the_tap_leads(self):
        # Подпись «открыть поиск на компе ↗» — иначе касание выглядит
        # как чтение строки, а оно открывает браузер на компьютере.
        self.assertIn("открыть поиск на компе ↗", self.страница)
        self.assertIn('<div class="x">✕</div>', self.страница)

    def test_a_source_shows_its_title_and_its_site(self):
        блок = self._код("showSearch")
        self.assertIn("title.textContent", блок)
        self.assertIn("host.textContent", блок)




# --- Карточка доезжает до телефона ----------------------------------------


class ТелефонОтдаётКарточкуTests(unittest.TestCase):
    """`PhoneServer.send_search_result` помнит источники и несёт `asked`."""

    def setUp(self):
        self.сообщения = []
        self.сервер = object.__new__(PhoneServer)
        self.сервер.last_sources = []
        # Настоящий `_broadcast` звал бы сокеты и поток; тут собираем то,
        # что ушло бы на телефон, без поднятия сервера.
        self.сервер._broadcast = self._собирать

    def _собирать(self, sender, targets=None):
        sender(NS(send_text=self.сообщения.append))

    def _карточка(self):
        return [json.loads(текст) for текст in self.сообщения]

    def test_the_message_carries_the_query_the_words_and_the_sources(self):
        self.сервер.send_search_result(
            "курс доллара", "Девяносто два",
            [{"title": "Курс ЦБ", "host": "cbr.ru", "url": "https://cbr.ru/rates"}],
            asked="сколько доллар")
        self.assertEqual(len(self._карточка()), 1)
        сообщение = self._карточка()[0]
        self.assertEqual(сообщение["type"], "search_result")
        self.assertEqual(сообщение["query"], "курс доллара")
        self.assertEqual(сообщение["asked"], "сколько доллар")
        self.assertEqual(сообщение["sources"][0]["host"], "cbr.ru")

    def test_without_words_the_query_is_shown_itself(self):
        self.сервер.send_search_result("курс доллара", "Девяносто два", [])
        self.assertEqual(self._карточка()[0]["asked"], "курс доллара")

    def test_the_last_list_of_sources_is_kept_for_the_open_source(self):
        # По нему телефон потом открывает источник **по номеру**: свой
        # список, а не то, что пришло с телефона.
        источники = [{"title": "Курс ЦБ", "host": "cbr.ru",
                      "url": "https://cbr.ru/rates"}]
        self.сервер.send_search_result("курс доллара", "Девяносто два", источники)
        self.assertEqual(self.сервер.last_sources, источники)


if __name__ == "__main__":
    unittest.main()



