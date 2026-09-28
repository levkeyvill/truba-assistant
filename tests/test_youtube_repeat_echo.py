"""Ролик не открывается второй раз, обрывок её речи не считается хозяином.

Живой случай 27.09, 11:23–11:24: ролик открылся трижды за минуту, название
обрезалось до «…ESCAPE FROM…», и на «Дави» (0.4 с) и «Это.» (0.5 с) — концы её
же речи из динамика телефона — она отвечала всерьёз.

Ни сети, ни браузера, ни звука: YouTube подменён на уровне `_videos`/`_text`/
`open`, время — на `time.monotonic`, микрофон и голос — на пустые объекты.
Данные на диске — временные папки (как в tests/test_data_guard.py).
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import ducking, hands, voice_loop, youtube
from core.voice_loop import VoiceLoop

# Из журнала 27.09 — ровно то, что она произнесла капсом.
CAPS_TITLE = ("ИМБОВАЯ ФИЧА ТУРНИРА, КОТОРАЯ ДОВЕЛА РЕКРЕНТА "
              "IN ESCAPE FROM TARKOV: ARENA")
CALM_TITLE = ("Имбовая фича турнира, которая довела рекрента "
              "In Escape From Tarkov: Arena")
VIDEO = {"title": CALM_TITLE, "content": "https://www.youtube.com/watch?v=abc123XYZ"}


def _net(opened=None, video=None):
    """Сеть и открытие YouTube — подменами. Наружу не уходит ничего.

    `video` — список роликов на любой запрос, либо словарь «запрос → ролики»,
    когда нужно, чтобы разные запросы давали разные адреса.
    """
    def do_videos(query):
        if isinstance(video, dict):
            return list(video.get(query, []))
        return list(video if video is not None else [VIDEO])

    def do_text(query):
        return []

    def do_open(url):
        if opened is not None:
            opened.append(url)
        return True, "YouTube"

    patcher = mock.patch.multiple(
        youtube,
        _videos=do_videos,
        _text=do_text,
        _page=lambda query, channels=False: None,
        open=do_open,
    )
    patcher.start()
    return patcher


class _RepeatGuard(unittest.TestCase):
    """Чистая память о недавних действиях и открытиях."""

    def setUp(self):
        youtube.forget_opened()
        hands.forget_done()
        self.addCleanup(youtube.forget_opened)
        self.addCleanup(hands.forget_done)


# --- Название целиком и обычным регистром ----------------------------------


class TitleTests(_RepeatGuard):
    def test_a_caps_title_is_said_in_normal_case(self):
        self.assertEqual(youtube._calm(CAPS_TITLE), CALM_TITLE)

    def test_a_normal_title_is_left_alone(self):
        # «Wi-Fi или Ethernet: что выбрать» — заглавных мало, вид не трогаем.
        self.assertEqual(youtube._calm("Wi-Fi или Ethernet: что выбрать"),
                         "Wi-Fi или Ethernet: что выбрать")

    def test_the_whole_title_is_said_without_the_dots(self):
        opened = []
        patcher = _net(opened)
        self.addCleanup(patcher.stop)
        what = youtube.act("имбовая фича", "video")
        self.assertEqual(what["text"], "Включила: " + CALM_TITLE)
        self.assertNotIn("…", what["text"])
        self.assertEqual(opened, ["https://www.youtube.com/watch?v=abc123XYZ"])

    def test_a_very_long_title_is_cut_by_word_and_without_the_dots(self):
        long_title = ("Как я покупал видеокарту RTX пять тысяч семьдесят и что "
                      "из этого вышло в итоге, рассказываю честно и подробно, "
                      "без утайки и без рекламы, только опыт и немного нервов")
        patcher = _net(video=[{"title": long_title,
                              "content": "https://www.youtube.com/watch?v=long"}])
        self.addCleanup(patcher.stop)
        what = youtube.act("видеокарта", "video")
        self.assertNotIn("…", what["text"])
        self.assertLessEqual(len(what["text"]), 150)
        said = what["text"].split("Включила: ", 1)[1]
        for word in said.split():
            self.assertIn(word, long_title, "слово разрезано пополам")

    def test_the_model_gets_the_whole_title_too(self):
        # `confirm` говорит вслух `text` из ответа инструмента — тот самый.
        patcher = _net()
        self.addCleanup(patcher.stop)
        answer = json.loads(hands.run_youtube('{"query": "имбовая фича"}'))
        self.assertEqual(answer["text"], "Включила: " + CALM_TITLE)
        self.assertEqual(hands.confirm(
            [{"name": hands.YT_NAME, "args": '{"query": "имбовая фича"}'}],
            [{"id": "youtube", "title": "YouTube"}],
            [json.dumps(answer, ensure_ascii=False)],
        ), "Включила: " + CALM_TITLE)



# --- Тот же ролик второй раз не открывается --------------------------------


class RepeatTests(_RepeatGuard):
    def test_the_same_video_twice_is_not_opened_again(self):
        opened = []
        patcher = _net(opened)
        self.addCleanup(patcher.stop)
        first = youtube.act("имбовая фича", "video")
        second = youtube.act("имбовая фича", "video")
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["repeat"])
        self.assertEqual(second["text"], "Он уже открыт.")
        self.assertEqual(opened, ["https://www.youtube.com/watch?v=abc123XYZ"],
                         "адрес открыт один раз")
        # Модели нужно понимание, а не только «уже открыт».
        self.assertIn("уже открыт", second["note"])
        self.assertIn("воспроизведение", second["note"])

    def test_after_the_window_the_video_opens_again(self):
        opened = []
        patcher = _net(opened)
        self.addCleanup(patcher.stop)
        with mock.patch.object(youtube.time, "monotonic", return_value=1000.0):
            youtube.act("имбовая фича", "video")
        later = 1000.0 + config.YT_REPEAT_SECONDS + 1
        with mock.patch.object(youtube.time, "monotonic", return_value=later):
            again = youtube.act("имбовая фича", "video")
        self.assertNotIn("repeat", again)
        self.assertEqual(len(opened), 2)

    def test_a_direct_request_opens_it_right_away(self):
        opened = []
        patcher = _net(opened)
        self.addCleanup(patcher.stop)
        youtube.act("имбовая фича", "video")
        again = youtube.act("имбовая фича", "video", again=True)
        self.assertNotIn("repeat", again)
        self.assertEqual(len(opened), 2)

    def test_the_tool_passes_again_and_speaks_about_the_repeat(self):
        patcher = _net()
        self.addCleanup(patcher.stop)
        hands.run_youtube('{"query": "имбовая фича"}')
        answer = json.loads(hands.run_youtube('{"query": "имбовая фича"}'))
        self.assertTrue(answer["repeat"])
        self.assertEqual(answer["text"], "Он уже открыт.")
        self.assertIn("уже открыт", answer["note"])
        # А вслух — то же, а не «Включила: …»: ролик не включали.
        self.assertEqual(hands.confirm(
            [{"name": hands.YT_NAME, "args": '{"query": "имбовая фича"}'}],
            [{"id": "youtube", "title": "YouTube"}],
            [json.dumps(answer, ensure_ascii=False)],
        ), "Он уже открыт.")

    def test_the_tool_opens_when_asked_again(self):
        opened = []
        patcher = _net(opened)
        self.addCleanup(patcher.stop)
        hands.run_youtube('{"query": "имбовая фича"}')
        hands.run_youtube('{"query": "имбовая фича", "again": true}')
        self.assertEqual(len(opened), 2)

    def test_the_repeat_guard_does_not_hide_youtube_from_the_model(self):
        # У YouTube своя защита по адресу (core/youtube.py); общая по запросу
        # молчала бы на «тот же ролик, но другой запрос». Здесь ролик ДРУГОЙ —
        # и защита по адресу его пропускает, а по запросу спрятала бы.
        other = {"title": "Ремонт видеокарты",
                 "content": "https://www.youtube.com/watch?v=repair1"}
        patcher = _net(video={"имбовая фича": [VIDEO], "ремонт видеокарты": [other]})
        self.addCleanup(patcher.stop)
        hands.run_youtube('{"query": "имбовая фича"}')
        answer = json.loads(hands.run_youtube('{"query": "ремонт видеокарты"}'))
        self.assertNotIn("repeat", answer)



# --- Жалоба — не просьба ----------------------------------------------------


class AskedForTests(unittest.TestCase):
    def test_a_complaint_about_the_title_is_not_a_request(self):
        # 27.09, 11:24:16 — ровно на эту фразу модель открыла ролик в третий раз.
        # Слова «ролик» в реплике есть, а просьбы открыть — нет: цитата
        # «открыть ролик» в неё не входит.
        self.assertFalse(hands.asked_for(
            hands.YT_NAME, "Ага, и ты ещё не договариваешь название YouTube-ролика, да?",
            because="открыть ролик"))

    def test_a_plain_request_is_a_request(self):
        self.assertTrue(hands.asked_for(
            hands.YT_NAME, "открой канал Корзинка", because="открой канал Корзинка"))

    def test_a_polite_question_is_a_request(self):
        # Вопрос с «можешь» — тоже просьба, если модель процитировала её слова.
        self.assertTrue(hands.asked_for(
            hands.YT_NAME, "можешь включить последний ролик Корзинки?",
            because="включить последний ролик Корзинки"))


# --- Хвост её речи ----------------------------------------------------------


class _Quiet:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _loop(spoke_at=None):
    """Голосовой цикл вручную: без микрофона, динамика, мозга и отпечатка."""
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._trust_next = False
    loop._open = True
    loop._last_turn = time.monotonic()
    loop._search_next = False
    loop._spoke_at = 0.0 if spoke_at is None else spoke_at
    loop._speaking_text = ""
    loop._brain = None
    loop._server = None
    loop._speaker = None
    loop._fallback = None
    loop._listener = _Quiet()
    loop._ducker = _Quiet()
    loop._voice_meter = None
    loop._voice_score = None
    loop._voiceprint = _Quiet()
    loop._digest_later = lambda: None
    return loop


def _phrase(seconds):
    return np.zeros(int(config.SAMPLE_RATE * seconds), dtype=np.float32)



class EchoTailTests(unittest.TestCase):
    """Короткий обрывок сразу после её речи — это её же голос, а не хозяин."""

    def setUp(self):
        from core import audio_in

        self._saved = (config.LISTEN_MODE, config.VOICE_APP_GUARD,
                       config.ECHO_FRAGMENT_SECONDS, config.ECHO_FRAGMENT_GAP)
        config.LISTEN_MODE = "always"
        config.VOICE_APP_GUARD = False
        self.silence = audio_in.SILENCE_TO_END

    def tearDown(self):
        (config.LISTEN_MODE, config.VOICE_APP_GUARD,
         config.ECHO_FRAGMENT_SECONDS,
         config.ECHO_FRAGMENT_GAP) = self._saved

    def _check(self, seconds, gap):
        """Фраза в `seconds`, начавшаяся через `gap` после конца её речи.

        Часы сведены: `heard_at` — `perf_counter`, `_spoke_at` — `monotonic`,
        и проверка идёт с заменой обоих на одно и то же время.
        """
        loop = _loop()
        heard_at = 1000.0
        start = heard_at - seconds - self.silence
        loop._spoke_at = start - gap
        with mock.patch("core.voice_loop.time.monotonic", return_value=heard_at), \
                mock.patch("core.voice_loop.time.perf_counter", return_value=heard_at):
            return loop._should_answer("Это.", _phrase(seconds), heard_at)

    def _why(self, loop):
        return [p for k, p in loop.events if k == "ignored"]

    def test_a_short_burst_right_after_her_voice_is_skipped(self):
        # Живой случай 27.09, 11:23:34: «Дави», 0.4 с, сразу после её речи.
        self.assertFalse(self._check(0.4, 0.2))

    def test_the_reason_says_it_is_her_own_tail(self):
        loop = _loop()
        heard_at = 1000.0
        loop._spoke_at = heard_at - 0.4 - self.silence - 0.2
        with mock.patch("core.voice_loop.time.monotonic", return_value=heard_at), \
                mock.patch("core.voice_loop.time.perf_counter", return_value=heard_at):
            self.assertFalse(loop._should_answer("Это.", _phrase(0.4), heard_at))
        self.assertEqual(self._why(loop),
                         [{"text": "Это.", "why": "похоже на хвост её речи"}])

    def test_a_burst_a_second_and_a_half_later_goes_further(self):
        # Живое «Да» через полторы секунды после её вопроса — не хвост.
        self.assertTrue(self._check(0.5, 1.5))

    def test_a_long_phrase_right_after_her_voice_is_not_caught(self):
        # Правило только про короткие обрывки: длинная фраза — это человек.
        self.assertTrue(self._check(2.0, 0.0))

    def test_a_phrase_that_started_while_she_was_talking_is_skipped(self):
        # Начало фразы — до конца её речи: хвост, тем более.
        self.assertFalse(self._check(0.4, -0.1))

    def test_before_her_first_word_nothing_is_applied(self):
        # `_spoke_at == 0` — она ещё не говорила, отсекать нечего.
        loop = _loop(spoke_at=0.0)
        self.assertTrue(loop._should_answer("Это.", _phrase(0.4), 1000.0))
        self.assertEqual(self._why(loop), [])

    def test_without_a_phrase_or_time_nothing_is_applied(self):
        loop = _loop(spoke_at=1000.0)
        self.assertTrue(loop._should_answer("Это.", None, 1000.0))


# --- Защита от повторов всех действий модели -------------------------------


class ActionRepeatTests(unittest.TestCase):
    def setUp(self):
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        # Счётчик вместо настоящего действия: видно, сколько раз позвали.
        self.calls = []
        self.actions = {name: (lambda key=name: (self.calls.append(key),
                                                f"сделано, {key}")[1])
                        for name in ("screenshot", "moment", "look")}

    def test_a_second_screenshot_within_fifteen_seconds_is_not_taken(self):
        first = hands.run_action(hands.SHOT_NAME, self.actions)
        self.assertNotIn("error", first)
        second = json.loads(hands.run_action(hands.SHOT_NAME, self.actions))
        self.assertTrue(second["repeat"])
        self.assertEqual(second["text"], hands.REPEAT_SAID)
        self.assertIn("уже сделано", second["note"])
        self.assertEqual(self.calls, ["screenshot"], "второй раз не снимали")

    def test_a_screenshot_after_the_window_is_taken(self):
        hands.run_action(hands.SHOT_NAME, self.actions)
        with mock.patch.object(hands.time, "monotonic",
                               return_value=time.monotonic() + 20):
            again = hands.run_action(hands.SHOT_NAME, self.actions)
        self.assertNotIn("repeat", again)
        self.assertEqual(self.calls, ["screenshot", "screenshot"])

    def test_a_voice_command_takes_two_screenshots(self):
        # Голосовая команда — его явная просьба, и память о действиях модели
        # её не касается: тут считаются только вызовы `run_action`.
        order = NS(action="screenshot", target="", kind="", reply="Скриншот сделан.")
        loop = _loop()
        loop._do_screenshot = lambda: "готово"
        loop._say_back = lambda words: loop.events.append(("said", words))
        loop._remember = lambda said, answered: None
        self.assertTrue(loop._run_command(order, "сделай скриншот"))
        self.assertTrue(loop._run_command(order, "сделай скриншот"))
        self.assertEqual([p for k, p in loop.events if k == "said"],
                         ["Скриншот сделан.", "Скриншот сделан."])

    def test_the_same_note_is_not_written_twice(self):
        from core import notes

        tmp = Path(tempfile.mkdtemp(prefix="truba-repeat-"))
        saved = config.NOTES_DIR
        config.NOTES_DIR = str(tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", saved))
        args = json.dumps({"text": "Мысль про трубу."}, ensure_ascii=False)
        first = json.loads(hands.run_note(args))
        self.assertTrue(first["ok"])
        second = json.loads(hands.run_note(args))
        self.assertTrue(second["repeat"])
        self.assertEqual(second["text"], hands.REPEAT_SAID)
        self.assertEqual(len(notes.read("Разное", "Входящие")["entries"]), 1)

    def test_a_close_repeat_is_not_closed_again(self):
        from core import launcher

        apps = [{"id": "discord", "title": "Discord"}]
        with mock.patch.object(launcher, "close",
                               return_value=(True, "Discord закрыта")) as shut:
            hands.run_close('{"app": "discord"}', None, apps)
            again = json.loads(hands.run_close('{"app": "discord"}', None, apps))
        self.assertTrue(again["repeat"])
        self.assertEqual(shut.call_count, 1)
        self.assertEqual(again["text"], hands.REPEAT_SAID)

    def test_confirm_says_the_repeat_and_not_the_usual_phrase(self):
        # «Готово, снимок на телефоне» здесь врало бы: снимка не было.
        answer = json.dumps({"ok": True, "repeat": True,
                             "text": hands.REPEAT_SAID}, ensure_ascii=False)
        self.assertEqual(hands.confirm([{"name": hands.SHOT_NAME, "args": "{}"}],
                                        [], [answer]), hands.REPEAT_SAID)

    def test_confirm_keeps_its_own_words_without_a_repeat(self):
        # Вариантов у снимка два, и при одном вызове выбор случайный.
        self.assertIn(hands.confirm([{"name": hands.SHOT_NAME, "args": "{}"}],
                                     [], None),
                      hands.CONFIRM[hands.SHOT_NAME])


# --- Звук браузера — как голос Discord -------------------------------------


class VoiceAppsTests(unittest.TestCase):
    def test_browsers_and_players_are_in_the_list(self):
        for name in ("firefox.exe", "chrome.exe", "msedge.exe", "browser.exe",
                     "opera.exe", "vlc.exe", "mpc-hc64.exe", "PotPlayerMini64.exe"):
            self.assertIn(name, config.VOICE_APPS, name)
        self.assertIn("Discord.exe", config.VOICE_APPS)

    def test_the_pult_webview_is_not_in_the_list(self):
        # «Послушать пробу» играет в пульте: его собственный голос нельзя
        # пропускать как чужой.
        self.assertNotIn("msedgewebview2.exe", config.VOICE_APPS)

    def test_the_meter_finds_a_browser_regardless_of_case(self):
        meter = ducking.VoiceAppMeter(apps=config.VOICE_APPS, window=2.0)
        meter._available = True

        class _Process:
            def __init__(self, pid, name):
                self.pid = pid
                self.name = name

        class _Meter:
            def __init__(self, value):
                self.value = value

            def GetPeakValue(self):
                return self.value

        class _Ctl:
            def __init__(self, value):
                self.value = value

            def QueryInterface(self, iface):
                return _Meter(self.value)

        class _Session:
            def __init__(self, process, value):
                self.Process = process
                self._ctl = _Ctl(value)

        sessions = [
            _Session(_Process(1, "Firefox.EXE"), 0.5),        # регистр не важен
            _Session(_Process(2, "msedgewebview2.exe"), 0.9),  # пульт — не считаем
            _Session(_Process(meter._pid, "firefox.exe"), 0.99),  # себя не считаем
            _Session(None, 0.5),
        ]
        with mock.patch("pycaw.pycaw.AudioUtilities.GetAllSessions",
                        return_value=sessions), \
                mock.patch("pycaw.pycaw.IAudioMeterInformation", create=True):
            self.assertEqual(meter._session_peak(), (0.5, "firefox.exe"))


if __name__ == "__main__":
    unittest.main()
