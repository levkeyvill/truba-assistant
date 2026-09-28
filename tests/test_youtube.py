"""YouTube по запросу: ролик, канал, выдача — без сети и без браузера.

Сеть в `core/youtube.py` живёт только в `_page` (страница поиска YouTube),
`_videos` и `_text` (`ddgs`). `_page` подменён на весь модуль (`setUpModule`),
остальные — в каждом тесте, поэтому ни один запрос наружу не уходит.
Настоящий браузер тоже не открывается: `launcher.open_url` заменён, а само
`youtube.open` проверяется на отказ чужому адресу.

Не здесь проверяется то, что видно только на живой машине: сколько на самом
деле секунд отвечает `ddgs` (замерено 27 сентября — 1.4 с на ролик и до 14 с
на первый запрос по каналу) и что браузер покажет на открытой странице.
"""

import json
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import commands, hands, launcher, youtube
from core.brain import Brain
from core.voice_loop import VoiceLoop

APPS = [
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"},
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
]
VIDEO = {"title": "Ремонт видеокарты своими руками",
         "content": "https://www.youtube.com/watch?v=abc123XYZ"}

# Метка «поиск упал» в списке результатов: подмена должна не вернуть пусто,
# а поднять ошибку — так ведёт себя настоящая сеть.
_RAISE = object()

# Страница поиска YouTube — основной путь, но в тестах ниже по умолчанию она
# «ничего не дала», и работает запасной `ddgs`. Свои страницы — в PageTests.
_NO_PAGE = mock.patch.object(youtube, "_page", lambda query, channels=False: None)


def setUpModule():
    _NO_PAGE.start()


def tearDownModule():
    _NO_PAGE.stop()


# Память о недавних открытиях и действиях — общая у всего процесса, а тесты
# идут один за другим. Без сброса «тот же ролик второй раз не открывается»
# ловит следующий тест, а не тот, который это проверяет.
class _Clean(unittest.TestCase):
    def setUp(self):
        youtube.forget_opened()
        hands.forget_done()

    def tearDown(self):
        youtube.forget_opened()
        hands.forget_done()


class _Patch:
    """Включает подмены на время теста и снимает их после."""

    def __init__(self, *patches):
        self._patches = patches

    def __enter__(self):
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *rest):
        for patch in self._patches:
            patch.stop()


def _net(video=None, channel=None, opened=None):
    """Подменяет сеть и открытие. Ни того, ни другого в тестах нет.

    Единственное место, где живёт `ddgs`, — `youtube._videos` и `youtube._text`,
    поэтому подменяются они: наружу не уходит ни один запрос.
    """
    def do_videos(query):
        if video is _RAISE:
            raise OSError("сеть легла")
        return list(video or [])

    def do_text(query):
        if channel is _RAISE:
            raise OSError("сети нет")
        return list(channel or [])

    def do_open(url):
        if opened is not None:
            opened.append(url)
        return True, "YouTube"

    return _Patch(mock.patch.object(youtube, "_videos", do_videos),
                  mock.patch.object(youtube, "_text", do_text),
                  mock.patch.object(youtube, "open", do_open))


# --- Поиск ролика ----------------------------------------------------------


class FindVideoTests(unittest.TestCase):
    def test_first_watch_link_is_taken(self):
        with _net([VIDEO]):
            self.assertEqual(
                youtube.find_video("ремонт видеокарты"),
                ("https://www.youtube.com/watch?v=abc123XYZ",
                 "Ремонт видеокарты своими руками"))

    def test_short_link_is_taken_too(self):
        # `youtu.be` — тот же ролик, а не мусор в выдаче.
        with _net([{"title": "Клип", "content": "https://youtu.be/QWERTY123"}]):
            url, title = youtube.find_video("клип")
        self.assertEqual(url, "https://youtu.be/QWERTY123")
        self.assertEqual(title, "Клип")

    def test_no_watch_link_means_none(self):
        # В выдаче попалось всё, кроме ролика: открывать нечего.
        with _net([{"title": "Плейлист", "content": "https://www.youtube.com/playlist?list=PL1"},
                   {"title": "Канал", "content": "https://www.youtube.com/@Frost"}]):
            self.assertIsNone(youtube.find_video("ремонт видеокарты"))

    def test_network_failure_means_none(self):
        with _net(_RAISE):
            self.assertIsNone(youtube.find_video("ремонт видеокарты"))

    def test_timeout_means_none_and_the_stuck_thread_is_a_daemon(self):
        # Сеть зависла намертво: ждать её шесть секунд хозяин не станет, а
        # зависший поток программу при выходе удерживать не должен.
        def hung(query):
            import time

            time.sleep(30)
            return []

        with mock.patch.object(youtube, "_videos", hung):
            self.assertIsNone(youtube.find_video("ремонт видеокарты", timeout=0.7))
        for thread in threading.enumerate():
            # Главный поток демоном быть не может — речь про зависший поиск.
            if thread is threading.main_thread():
                continue
            self.assertTrue(thread.daemon, thread)

    def test_empty_query_never_goes_to_the_network(self):
        with mock.patch.object(youtube, "_videos") as search:
            self.assertIsNone(youtube.find_video("  "))
        search.assert_not_called()


# --- Поиск канала ----------------------------------------------------------


class FindChannelTests(unittest.TestCase):
    MIXED = [
        {"title": "Ролик", "href": "https://www.youtube.com/watch?v=abc"},
        {"title": "Плейлист", "href": "https://www.youtube.com/playlist?list=PL1"},
        {"title": "Музыка", "href": "https://music.youtube.com/watch?v=xyz"},
        {"title": "Клип", "href": "https://www.youtube.com/shorts/abc"},
        {"title": "Linus Tech Tips",
         "href": "https://www.youtube.com/LinusTechTips/about"},
    ]

    def test_channel_is_picked_out_of_a_mixed_list_and_about_is_cut(self):
        with _net(channel=self.MIXED):
            self.assertEqual(
                youtube.find_channel("Linus Tech Tips"),
                ("https://www.youtube.com/LinusTechTips", "Linus Tech Tips"))

    def test_new_style_and_id_addresses_are_taken(self):
        for raw, expected in (
            ("https://www.youtube.com/@FrostPlus", "https://www.youtube.com/@FrostPlus"),
            ("https://www.youtube.com/channel/UCabc123/videos",
             "https://www.youtube.com/channel/UCabc123"),
            ("https://www.youtube.com/c/Veritasium", "https://www.youtube.com/c/Veritasium"),
            ("https://www.youtube.com/user/someone", "https://www.youtube.com/user/someone"),
        ):
            with self.subTest(raw=raw), _net(channel=[{"title": "К", "href": raw}]):
                url, _title = youtube.find_channel("кто-то")
            self.assertEqual(url, expected)

    def test_nothing_but_videos_means_none(self):
        with _net(channel=self.MIXED[:4]):
            self.assertIsNone(youtube.find_channel("Linus Tech Tips"))

    def test_network_failure_means_none(self):
        with _net(channel=_RAISE):
            self.assertIsNone(youtube.find_channel("Linus Tech Tips"))

    def test_query_carries_site_youtube_com(self):
        # Именно этот вид запроса приводит к каналам, а не к роликам.
        asked = []
        with mock.patch.object(youtube, "_text", lambda q: asked.append(q) or []):
            youtube.find_channel("Linus Tech Tips")
        self.assertEqual(asked, ["Linus Tech Tips site:youtube.com"])


# --- Страница поиска самого YouTube ---------------------------------------
# Кусок `ytInitialData` в том виде, в каком он лежит на странице 27 сентября:
# ролики и каналы сидят глубоко внутри, рядом — полки и реклама без videoId.
PAGE_VIDEOS = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
    "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
        {"adSlotRenderer": {"videoRenderer": {"title": {"runs": [{"text": "Реклама"}]}}}},
        {"videoRenderer": {"videoId": "07gwV2al4R4", "title": {"runs": [
            {"text": "Как поменять термопасту"}, {"text": " на видеокарте"}]}}},
        {"videoRenderer": {"videoId": "second", "title": {"runs": [{"text": "Второй"}]}}},
    ]}}]}}}}}
PAGE_CHANNELS = {"contents": [{"itemSectionRenderer": {"contents": [
    {"channelRenderer": {"channelId": "UCXuqSBlHAE6Xw-yeJA0Tunw",
                         "title": {"simpleText": "Linus Tech Tips"},
                         "navigationEndpoint": {"browseEndpoint": {
                             "canonicalBaseUrl": "/@LinusTechTips"}}}},
]}}]}


def _page_gives(data, asked=None):
    def page(query, channels=False):
        if asked is not None:
            asked.append((query, channels))
        if data is _RAISE:
            raise OSError("youtube не ответил")
        return data
    return mock.patch.object(youtube, "_page", page)


class PageTests(unittest.TestCase):
    def test_first_real_video_is_taken_and_ddgs_is_not_asked(self):
        with _page_gives(PAGE_VIDEOS), mock.patch.object(youtube, "_videos") as ddg:
            self.assertEqual(
                youtube.find_video("как заменить термопасту"),
                ("https://www.youtube.com/watch?v=07gwV2al4R4",
                 "Как поменять термопасту на видеокарте"))
        ddg.assert_not_called()

    def test_channel_takes_its_handle_address(self):
        asked = []
        with _page_gives(PAGE_CHANNELS, asked), mock.patch.object(youtube, "_text") as ddg:
            self.assertEqual(
                youtube.find_channel("линус тех типс"),
                ("https://www.youtube.com/@LinusTechTips", "Linus Tech Tips"))
        ddg.assert_not_called()
        # Канал ищется с фильтром «только каналы».
        self.assertEqual(asked, [("линус тех типс", True)])

    def test_channel_without_handle_falls_back_to_its_id(self):
        data = {"channelRenderer": {"channelId": "UCabc", "title": {"simpleText": "Фрост"}}}
        with _page_gives(data):
            self.assertEqual(youtube.find_channel("фрост"),
                             ("https://www.youtube.com/channel/UCabc", "Фрост"))

    def test_page_failure_falls_back_to_ddgs(self):
        with _page_gives(_RAISE), _net([VIDEO]):
            self.assertEqual(youtube.find_video("ремонт видеокарты")[0],
                             "https://www.youtube.com/watch?v=abc123XYZ")

    def test_page_without_data_falls_back_to_ddgs(self):
        with _page_gives(None), _net(channel=[{"title": "Фрост",
                                               "href": "https://www.youtube.com/@Frost"}]):
            self.assertEqual(youtube.find_channel("фрост")[0], "https://www.youtube.com/@Frost")

    def test_hung_page_spends_the_whole_limit_and_ddgs_is_skipped(self):
        # Голос не должен молчать два предела подряд: страница съела время —
        # запасной поиск уже не зовётся, откроется выдача.
        def hung(query, channels=False):
            time.sleep(30)

        with mock.patch.object(youtube, "_page", hung),                 mock.patch.object(youtube, "_videos") as ddg:
            started = time.monotonic()
            self.assertIsNone(youtube.find_video("ремонт", timeout=0.1))
        self.assertLess(time.monotonic() - started, 1)
        ddg.assert_not_called()

    def test_initial_data_is_cut_out_of_the_html(self):
        html = ('<script>var ytInitialData = {"a": {"videoRenderer": {"videoId": "x1"}}};'
                '</script><script>var other = {};</script>')
        found = youtube._INITIAL.search(html)
        self.assertEqual(json.loads(found.group(1)),
                         {"a": {"videoRenderer": {"videoId": "x1"}}})


# --- Страница поиска и открытие -------------------------------------------


class SearchUrlTests(unittest.TestCase):
    def test_russian_query_is_encoded(self):
        url = youtube.search_url("ремонт видеокарты")
        self.assertTrue(
            url.startswith("https://www.youtube.com/results?search_query="), url)
        self.assertIn("%D1%80%D0%B5%D0%BC%D0%BE%D0%BD%D1%82", url)
        # Русского текста в адресе быть не должно — только «+» и проценты.
        self.assertNotIn("ремонт", url)

    def test_channels_filter_is_added_only_when_asked(self):
        self.assertNotIn("&sp=", youtube.search_url("фрост"))
        self.assertTrue(
            youtube.search_url("фрост", channels=True).endswith("&sp=EgIQAg%253D%253D"))

    def test_space_becomes_plus(self):
        self.assertIn("video+card", youtube.search_url("video card"))


class OpenTests(unittest.TestCase):
    def test_a_foreign_address_is_refused_and_never_opened(self):
        for url in ("https://google.com/watch?v=abc", "https://youtube.com.evil.ru/watch",
                    "http://www.youtube.com/watch?v=abc", "file:///C:/x.exe",
                    "javascript:alert(1)", ""):
            with self.subTest(url=url), mock.patch.object(
                    launcher, "open_url") as opened:
                ok, what = youtube.open(url)
            self.assertFalse(ok)
            self.assertIn("не открываю", what)
            opened.assert_not_called()

    def test_youtube_address_goes_through_launcher(self):
        with mock.patch.object(launcher, "open_url",
                               return_value=(True, "YouTube")) as opened:
            ok, what = youtube.open("https://www.youtube.com/watch?v=abc")
        self.assertTrue(ok)
        opened.assert_called_once_with("youtube",
                                       "https://www.youtube.com/watch?v=abc")

    def test_browser_failure_is_reported_not_swallowed(self):
        with mock.patch.object(launcher, "open_url", side_effect=OSError("занято")):
            ok, what = youtube.open("https://www.youtube.com/watch?v=abc")
        self.assertFalse(ok)
        self.assertIn("занято", what)


# --- Что делаем и что говорим ---------------------------------------------


class ActTests(_Clean):
    def test_a_found_video_is_opened_and_announced(self):
        opened = []
        with _net([VIDEO], opened=opened):
            what = youtube.act("ремонт видеокарты", "video")
        self.assertTrue(what["ok"])
        self.assertEqual(what["url"], "https://www.youtube.com/watch?v=abc123XYZ")
        self.assertEqual(opened, ["https://www.youtube.com/watch?v=abc123XYZ"])
        self.assertEqual(what["text"], "Включила: Ремонт видеокарты своими руками")

    def test_a_found_channel_is_announced_without_a_prefix(self):
        with _net(channel=[{"title": "Frost", "href": "https://www.youtube.com/@Frost"}]):
            what = youtube.act("фрост", "channel")
        self.assertTrue(what["ok"])
        self.assertEqual(what["text"], "Открыла канал Frost")

    def test_search_kind_opens_the_results_page_right_away(self):
        opened = []
        with _net(opened=opened):
            what = youtube.act("ремонт видеокарты", "search")
        self.assertTrue(what["ok"])
        self.assertEqual(opened, [youtube.search_url("ремонт видеокарты")])
        self.assertEqual(what["text"], "Открыла поиск на YouTube: ремонт видеокарты")
        self.assertEqual(what["kind"], "search")

    def test_nothing_found_falls_back_to_search_and_says_so(self):
        # Главный случай: ролика нет. Молчать или выдумать нельзя — открывается
        # выдача, и в ответе прямо сказано, что нашлась только она.
        opened = []
        with _net([], opened=opened):
            what = youtube.act("как заменить термопасту", "video")
        self.assertTrue(what["ok"])
        self.assertEqual(what["kind"], "search")
        self.assertEqual(opened, [youtube.search_url("как заменить термопасту")])
        self.assertIn("только поиск", what["text"])
        self.assertIn("как заменить термопасту", what["text"])

    def test_a_broken_network_still_opens_search(self):
        with _net(_RAISE, channel=_RAISE):
            what = youtube.act("ремонт видеокарты", "video")
        self.assertTrue(what["ok"])
        self.assertIn("только поиск", what["text"])

    def test_a_missing_channel_says_about_a_channel(self):
        with _net(channel=[]):
            what = youtube.act("фрост", "channel")
        self.assertIn("Канал не нашла", what["text"])

    def test_an_unknown_kind_is_treated_as_a_video(self):
        with _net([VIDEO]):
            what = youtube.act("ремонт видеокарты", "ерунда")
        self.assertEqual(what["kind"], "video")

    def test_an_empty_query_does_not_open_anything(self):
        with mock.patch.object(youtube, "open") as opened:
            what = youtube.act("  ", "video")
        self.assertFalse(what["ok"])
        opened.assert_not_called()

    def test_a_long_title_is_cut_by_word(self):
        # Название читается вслух, а бывает на полстраницы. Режется по слову и
        # без многоточия: 27.09 хозяин услышал «…ESCAPE FROM…» и решил, что
        # она не договорила.
        long_title = ("Как я покупал видеокарту RTX пять тысяч семьдесят и что "
                      "из этого вышло в итоге, рассказываю честно и подробно")
        with _net([{"title": long_title, "content": "https://www.youtube.com/watch?v=a"}]):
            what = youtube.act("видеокарта", "video")
        self.assertNotIn("…", what["text"])
        self.assertLessEqual(len(what["text"]), 150)
        # Слова не разрезаны: произнесённое название — начало целых слов.
        said = what["text"].split("Включила: ", 1)[1]
        for word in said.split():
            self.assertIn(word, long_title)


# --- Короткие голосовые команды -------------------------------------------


class CommandTests(unittest.TestCase):
    def test_video_request_carries_the_query(self):
        order = commands.understand("включи на ютубе как заменить термопасту", APPS)
        self.assertIsNotNone(order)
        self.assertEqual((order.action, order.target, order.kind),
                         ("youtube", "как заменить термопасту", "video"))

    def test_the_verb_may_come_before_or_after_the_phrase(self):
        # Форма — начало фразы. «Поставь на ютубе…», «на ютубе включи…» и
        # прочие разговорные порядки слов в таблицу не входят: фраза уходит
        # модели, и смысл («включи ролик про …») понимает она.
        for фраза in ("на ютубе включи ремонт видеокарты",
                      "обзор 5070 на ютубе включи",
                      "поставь на ютубе рецепт борща"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS))
        # А повелительное начало — узнаётся.
        for фраза in ("включи на ютубе ремонт видеокарты",
                      "найди на ютубе ремонт видеокарты"):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual(order.action, "youtube")

    def test_channel_is_understood_with_and_without_the_word_youtube(self):
        # Хвост «на ютубе» после названия канала — это не часть запроса:
        # канал ищется по названию без него.
        for фраза in ("открой канал линус тех типс",
                      "открой канал линус тех типс на ютубе"):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertEqual((order.action, order.target, order.kind),
                                 ("youtube", "линус тех типс", "channel"))

    def test_find_on_youtube_means_the_results_page(self):
        # «Найди» — это выдача: выбирать ролик из неё нечего.
        order = commands.understand("найди на ютубе обзор 5070", APPS)
        self.assertEqual((order.action, order.target, order.kind),
                         ("youtube", "обзор 5070", "search"))

    def test_plain_youtube_still_launches_the_button(self):
        # Старое поведение не сломано: «открой ютуб» — это кнопка из apps.json,
        # а не поиск ролика.
        order = commands.understand("открой ютуб", APPS)
        self.assertEqual((order.action, order.target), ("launch", "youtube"))

    def test_a_saved_clip_is_still_a_moment(self):
        # «Клип» есть у обоих инструментов: «сохрани клип» — это момент.
        order = commands.understand("сохрани клип", APPS)
        self.assertEqual(order.action, "moment")

    def test_talk_about_a_video_is_not_a_command(self):
        for фраза in ("спасибо, ролик был классный", "я говорил: включи на ютубе борщ"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS))

    def test_youtube_without_a_query_is_not_a_search(self):
        # «Включи на ютубе» без запроса — это обычный запуск главной.
        order = commands.understand("включи на ютубе", APPS)
        self.assertEqual((order.action, order.target), ("launch", "youtube"))


# --- Инструмент модели -----------------------------------------------------


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


class _Client:
    """Отдаёт заготовленные потоки и помнит тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams):
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
    # Проверка «он правда просил?» спрашивает модель перед открытием YouTube.
    # Здесь она отвечает «да»: этот файл проверяет сам ролик, а отказ судьи
    # разбирает tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain


def _yt_call(args, call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, hands.YT_NAME, args)])]


class ToolTests(_Clean):
    def setUp(self):
        super().setUp()
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_a_video_is_announced_by_the_tool_text_without_a_second_round(self):
        # Тот же короткий путь, что у запуска программы: второй круг в облако
        # уходит только ради слова «включила», а оно уже готово в `text`.
        brain = _brain([_yt_call('{"query": "ремонт видеокарты", "because": "включи на ютубе ролик"}'),
                        [_chunk("Держи, включила.")]])
        with _net([VIDEO]):
            said = list(brain.reply("включи на ютубе ролик про ремонт видеокарты"))
        self.assertEqual(len(brain._client.bodies), 1)
        self.assertEqual(said, ["Включила: Ремонт видеокарты своими руками"])
        # В историю идёт сказанное, служебные поля ответа — нет.
        self.assertEqual(brain._history[-1]["content"], said[0])

    def test_a_channel_request_is_answered_by_the_channel_text(self):
        brain = _brain([_yt_call('{"query": "фрост", "kind": "channel", "because": "открой на ютубе канал фрост"}'),
                        [_chunk("Готово.")]])
        with _net(channel=[{"title": "Frost", "href": "https://www.youtube.com/@Frost"}]):
            # Со словом «ютуб»: без него модели YouTube не открыть (правило хозяина 27.09).
            said = list(brain.reply("открой на ютубе канал фрост"))
        self.assertEqual(said, ["Открыла канал Frost"])
        self.assertEqual(len(brain._client.bodies), 1)

    def test_nothing_found_answers_with_search_and_says_so(self):
        brain = _brain([_yt_call('{"query": "как заменить термопасту", "because": "включи на ютубе ролик"}'),
                        [_chunk("Готово.")]])
        with _net([]):
            said = list(brain.reply("включи на ютубе ролик про замену термопасты"))
        self.assertEqual(len(said), 1)
        self.assertIn("только поиск", said[0])

    def test_a_broken_call_never_opens_anything(self):
        with _net([VIDEO]):
            broken = json.loads(hands.run_youtube("{не json"))
            empty = json.loads(hands.run_youtube('{"query": "  "}'))
        self.assertIn("error", broken)
        self.assertIn("error", empty)
        self.assertIn("не сказано", empty["error"])

    def test_a_failed_open_does_not_confirm(self):
        # Инструмент ответил ошибкой — подтверждать нечем, и хозяин должен
        # услышать честное «не вышло», а не «включила».
        with _net([VIDEO]), mock.patch.object(
                youtube, "open", return_value=(False, "не открываю такой адрес: x")):
            answer = json.loads(hands.run_youtube('{"query": "ремонт видеокарты"}'))
        self.assertIn("error", answer)
        self.assertIn("не вышло", answer["error"])

    def test_the_journal_says_what_was_opened(self):
        events = []
        with _net([VIDEO]):
            hands.run_youtube('{"query": "ремонт видеокарты"}',
                              lambda kind, payload: events.append((kind, payload)))
        self.assertEqual(
            events, [("youtube", "включила «Ремонт видеокарты своими руками» "
                                 "(запрос: ремонт видеокарты)")])

    def test_the_journal_says_search_without_a_video(self):
        events = []
        with _net([]):
            hands.run_youtube('{"query": "ремонт видеокарты"}',
                              lambda kind, payload: events.append((kind, payload)))
        self.assertEqual(events, [("youtube", "открыла поиск «ремонт видеокарты»")])

    def test_the_journal_line_reads_in_the_web_pult(self):
        from ui.web_runtime import WebRuntime

        self.assertEqual(
            WebRuntime._log_messages("youtube", "включила «Ремонт видеокарты» "
                                                 "(запрос: ремонт видеокарты)"),
            ["YouTube: включила «Ремонт видеокарты» (запрос: ремонт видеокарты)"])

    def test_the_tool_is_offered_even_without_programs(self):
        # Ролики запускаются голосом, а не кнопкой, и набор инструментов не
        # должен зависеть от того, что лежит в apps.json.
        with mock.patch.object(launcher, "read_list", return_value=[]):
            brain = _brain([[_chunk("Ок.")]])
            list(brain.reply("как дела?"))
        names = [t["function"]["name"] for t in brain._client.bodies[0]["tools"]]
        self.assertIn(hands.YT_NAME, names)

    def test_the_tool_explains_its_kinds_in_russian(self):
        spec = hands.YT_TOOL["function"]
        self.assertEqual(spec["name"], hands.YT_NAME)
        self.assertEqual(spec["parameters"]["properties"]["kind"]["enum"],
                         ["video", "channel", "search"])
        # `because` обязателен у каждого действия: без цитаты из последней
        # реплики хозяина ролик не откроется.
        self.assertIn("because", spec["parameters"]["required"])
        for слово in ("ролик", "канал", "YouTube", "хозяин"):
            self.assertIn(слово, spec["description"])

    def test_a_video_is_only_opened_when_it_was_asked_for(self):
        # Повтор прошлой просьбы: в этой фразе про YouTube не говорят, и цитаты
        # из неё взять неоткуда — модель получает `not_asked`.
        self.assertFalse(hands.asked_for(hands.YT_NAME, "а кстати, что нового?", APPS,
                                         because="включи ролик"))
        for фраза in ("включи ролик про борщ", "открой канал фрост",
                      "найди видео про кота"):
            with self.subTest(фраза=фраза):
                self.assertTrue(hands.asked_for(hands.YT_NAME, фраза, APPS,
                                                because=фраза))
        self.assertIn("error", json.loads(hands.not_asked(hands.YT_NAME)))


# --- Голосовой цикл: тот же путь без облака --------------------------------


def _loop():
    """Голосовой цикл из заглушек: ни сети, ни звука, ни синтеза, ни облака.

    Ответы и события копятся в списках, которые навешены на сам цикл, — так же,
    как их снимают тесты голосового цикла в `test_fast_actions.py`.
    """
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop.said = []
    loop.remembered = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._say_back = loop.said.append
    loop._remember = lambda text, answered: loop.remembered.append((text, answered))
    loop._answer = lambda *args, **kwargs: None
    return loop


class VoiceLoopTests(_Clean):
    def _run(self, фраза):
        order = commands.understand(фраза, APPS)
        self.assertIsNotNone(order, фраза)
        loop = _loop()
        self.assertTrue(loop._run_command(order, фраза))
        return loop

    def test_a_voice_request_is_answered_after_opening(self):
        with _net([VIDEO]):
            loop = self._run("включи на ютубе ремонт видеокарты")
        # Ответ приходит после того, как открылось: заранее говорить нечего.
        self.assertEqual(loop.said, ["Включила: Ремонт видеокарты своими руками"])
        self.assertEqual(loop.remembered,
                         [("включи на ютубе ремонт видеокарты", loop.said[0])])
        self.assertEqual(loop.events, [
            ("youtube", "включила «Ремонт видеокарты своими руками» "
                        "(запрос: ремонт видеокарты)")])

    def test_a_channel_voice_request_says_a_channel(self):
        with _net(channel=[{"title": "Linus Tech Tips",
                            "href": "https://www.youtube.com/@LinusTechTips"}]):
            loop = self._run("открой канал линус тех типс")
        self.assertEqual(loop.said, ["Открыла канал Linus Tech Tips"])

    def test_search_request_just_opens_the_results(self):
        with _net():
            loop = self._run("найди на ютубе обзор 5070")
        self.assertEqual(loop.said, ["Открыла поиск на YouTube: обзор 5070"])
        self.assertEqual(loop.events, [("youtube", "открыла поиск «обзор 5070»")])

    def test_a_nothing_found_request_says_it_out_loud(self):
        with _net([]):
            loop = self._run("включи на ютубе ремонт видеокарты")
        self.assertIn("только поиск", loop.said[0])

    def test_a_failed_open_does_not_claim_success(self):
        with _net([VIDEO]), mock.patch.object(
                youtube, "open", return_value=(False, "не открываю такой адрес")):
            loop = self._run("включи на ютубе ремонт видеокарты")
        self.assertEqual(loop.said, ["Не получилось."])
        self.assertNotIn("Включила", str(loop.remembered))

    def test_plain_youtube_still_launches_the_button(self):
        loop = _loop()
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")):
            order = commands.understand("открой ютуб", APPS)
            loop._run_command(order, "открой ютуб")
        self.assertEqual(loop.said, ["Запускаю YouTube."])


if __name__ == "__main__":
    unittest.main()






