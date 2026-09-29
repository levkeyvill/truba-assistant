"""Закрытие программ и поиск кнопкой: без микрофона, сети и настоящих окон.

Закрытие проверяется на подменённых процессах и окнах: ни одна настоящая
программа в тестах не трогается — иначе прогон тестов закрыл бы хозяину
Discord. Источники для телефона собираются из подменённого `web.run_tool`.

Не здесь и не в этом файле проверяется: настоящий WM_CLOSE на настоящем
окне (это видно только на живой машине) и то, что psutil на этой Windows
видит имя процесса так же, как наш код.
"""

import json
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import audio_in, commands, hands, launcher, web
from core import brain as brain_module
from core.brain import MAX_SOURCES, Brain
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime

APPS = [
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe"},
    {"id": "discord", "title": "Discord", "kind": "app",
     "path": "%LOCALAPPDATA%\\Discord\\Update.exe",
     "args": ["--processStart", "Discord.exe"]},
    {"id": "telegram", "title": "Telegram", "kind": "app",
     "path": "%APPDATA%\\Telegram Desktop\\Telegram.exe"},
    {"id": "youtube", "title": "YouTube", "kind": "url",
     "url": "https://youtube.com"},
    {"id": "claude", "title": "Claude", "kind": "store",
     "app_id": "Claude_pzs8sxrjxfjjc!Claude"},
]


# --- Подмены вместо настоящих процессов и окон ---------------------------


class FakeProc:
    """Процесс, который живёт ровно столько, сколько скажут.

    `lives` — сколько раз ещё отвечает «жив». Уходит от WM_CLOSE (0), от
    terminate (1), либо не уходит вовсе (None — и close обязан это сказать).
    """

    def __init__(self, name, pid=4242, lives=1):
        self.info = {"name": name}
        self.pid = pid
        self.left = lives
        self.terminated = 0
        self.killed = 0

    def is_running(self):
        return self.left is None or self.left > 0

    def status(self):
        return "sleeping" if self.is_running() else "zombie"

    def terminate(self):
        self.terminated += 1
        if self.left is not None:
            self.left -= 1

    def kill(self):
        self.killed += 1
        if self.left is not None:
            self.left = 0


def _close_with(procs, apps=APPS):
    """Прогоняет `launcher.close` на подменённых процессах и окнах.

    Возвращает (вызов, список отправленных WM_CLOSE). Ни psutil, ни ctypes,
    ни пятисекундное ожидание тут не работают: важна лишь последовательность
    «вежливо, потом terminate, потом kill» и то, какие pids пошли в окна.
    """
    closed_windows = []

    def post(pids):
        closed_windows.append(set(pids))
        # Окно закрылось — приложение честно уходит. Так ведёт себя Firefox.
        for proc in procs:
            if proc.left == 0:
                proc.left = -1
        return len(pids)

    def run(app_id):
        with mock.patch.object(launcher, "read_list", return_value=apps), \
                mock.patch.object(launcher, "_running",
                                  return_value=list(procs)), \
                mock.patch.object(launcher, "_close_windows", side_effect=post), \
                mock.patch.object(launcher, "CLOSE_WAIT", 0.0), \
                mock.patch.object(launcher, "KILL_WAIT", 0.0):
            return launcher.close(app_id)

    return run, closed_windows


# --- Голосовая команда ----------------------------------------------------


class CloseCommandTests(unittest.TestCase):
    def test_close_is_understood(self):
        for фраза, title in (("закрой дискорд", "Discord"),
                             ("выключи телеграм", "Telegram"),
                             ("закрой фаерфокс", "Firefox"),
                             ("выруби дискорд", "Discord")):
            got = commands.understand(фраза, APPS)
            self.assertIsNotNone(got, фраза)
            self.assertEqual(got.action, "close", фраза)
            self.assertEqual(got.target,
                             next(a["id"] for a in APPS if a["title"] == title))
            self.assertEqual(got.reply, f"Закрываю {title}.")

    def test_without_a_program_it_is_not_a_command(self):
        for фраза in ("закрой разговор", "выключи голос", "выключи звук",
                      "выключи свет", "закрой дверь"):
            self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_guards_still_work(self):
        # Пересказ, кавычки и длинная фраза — как у всех остальных команд.
        self.assertIsNone(commands.understand('я говорю: «закрой дискорд»', APPS))
        self.assertIsNone(commands.understand(
            "спасибо, ты уже закрыла дискорд и теперь можно идти спать", APPS))
        long = "закрой дискорд и фаерфокс и телеграм и ещё пятнадцать программ " \
               "которые я уже не помню как называются"
        self.assertIsNone(commands.understand(long, APPS))

    def test_launch_still_works_alongside(self):
        got = commands.understand("запусти дискорд", APPS)
        self.assertEqual(got.action, "launch")
        self.assertIsNone(commands.understand("как дела", APPS))


# --- Закрытие программы ---------------------------------------------------


class ProcessNameTests(unittest.TestCase):
    def test_plain_exe(self):
        self.assertEqual(
            launcher.process_name({"kind": "app", "path": "firefox.exe"}),
            "firefox.exe")

    def test_launcher_arg_wins_over_the_launcher_itself(self):
        # У Discord в path Update.exe, а работает Discord.exe.
        item = {"kind": "app", "path": "%LOCALAPPDATA%\\Discord\\Update.exe",
                "args": ["--processStart", "Discord.exe"]}
        self.assertEqual(launcher.process_name(item), "Discord.exe")

    def test_explicit_field_wins(self):
        item = {"kind": "app", "path": "Happ.exe", "process": "Happ.exe.1"}
        self.assertEqual(launcher.process_name(item), "Happ.exe.1")

    def test_nothing_to_close(self):
        self.assertIsNone(launcher.process_name({"kind": "app"}))


class CloseTests(unittest.TestCase):
    def test_not_running_is_a_success(self):
        run, windows = _close_with([])
        ok, what = run("discord")
        self.assertTrue(ok)
        self.assertIn("не запущен", what)
        self.assertEqual(windows, [])

    def test_wm_close_was_enough(self):
        # Процесс уходит сам после WM_CLOSE — как Firefox.
        proc = FakeProc("firefox.exe", lives=0)
        run, windows = _close_with([proc])
        ok, what = run("firefox")
        self.assertTrue(ok)
        self.assertEqual(windows, [{proc.pid}])
        self.assertEqual(proc.terminated, 0)
        self.assertEqual(proc.killed, 0)

    def test_stubborn_process_is_terminated(self):
        # Discord по крестику прячется в трей: WM_CLOSE не берёт.
        proc = FakeProc("Discord.exe", lives=1)
        run, _windows = _close_with([proc])
        ok, what = run("discord")
        self.assertTrue(ok)
        self.assertEqual(proc.terminated, 1)
        self.assertEqual(proc.killed, 0)

    def test_unkillable_process_is_a_failure(self):
        proc = FakeProc("Telegram.exe", lives=None)
        run, _windows = _close_with([proc])
        ok, what = run("telegram")
        self.assertFalse(ok)
        self.assertIn("не закрылась", what)
        self.assertEqual(proc.killed, 1)

    def test_url_is_refused(self):
        run, _windows = _close_with([])
        ok, what = run("youtube")
        self.assertFalse(ok)
        self.assertIn("вкладка в браузере", what)

    def test_store_without_process_is_refused(self):
        run, _windows = _close_with([])
        ok, what = run("claude")
        self.assertFalse(ok)
        self.assertIn("не знаю, как её закрыть", what)

    def test_store_with_process_is_looked_for(self):
        apps = APPS + [dict(APPS[4], id="store2", process="Spotify.exe")]
        proc = FakeProc("Spotify.exe")
        run, _windows = _close_with([proc], apps)
        ok, _what = run("store2")
        self.assertTrue(ok)

    def test_claude_is_protected_even_with_process(self):
        # В Claude работает помощник, который пишет этот код: закрыть его
        # голосом значит оборвать правку на полуслове.
        apps = APPS + [dict(APPS[4], id="claude2", process="Claude.exe")]
        run, _windows = _close_with([FakeProc("Claude.exe")], apps)
        ok, what = run("claude2")
        self.assertFalse(ok)

    def test_program_outside_the_list_is_refused(self):
        run, _windows = _close_with([FakeProc("calc.exe")])
        ok, what = run("calc")
        self.assertFalse(ok)
        self.assertIn("нет такой кнопки", what)

    def test_the_pult_itself_is_never_a_target(self):
        apps = APPS + [{"id": "pult", "title": "Пульт", "kind": "app",
                        "path": "pythonw.exe"}]
        proc = FakeProc("pythonw.exe")
        run, _windows = _close_with([proc], apps)
        ok, what = run("pult")
        self.assertFalse(ok)
        self.assertIn("пульт", what)
        self.assertEqual(proc.terminated, 0)


# --- Инструмент модели ----------------------------------------------------


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает запросы."""

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
    brain.actions = {}
    brain.last_sources = []
    # Проверка «он правда просил?» спрашивает модель перед закрытием программы.
    # Здесь она отвечает «да»: этот файл проверяет само закрытие, а отказ судьи
    # разбирает tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain


def _tool_call(name, args, call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


def _names(brain, index=0):
    return [t["function"]["name"]
            for t in brain._client.bodies[index].get("tools", [])]


class CloseToolTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        # Память о недавних действиях общая на процесс: без сброса тест про
        # «закрыть» получит «Уже сделала» от предыдущего.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_comes_right_after_launch_and_always(self):
        brain = _brain([[_chunk("Ок.")]])
        list(brain.reply("как дела?"))
        names = _names(brain)
        self.assertEqual(names[0], hands.NAME)
        self.assertEqual(names[1], hands.CLOSE_NAME)
        # Тот же набор на болтовне и на просьбе — на этом держится кеш.
        other = _brain([[_chunk("Ок.")]])
        list(other.reply("а ты не могла бы закрыть дискорд"))
        self.assertEqual(_names(other), names)

    def test_model_closes_and_needs_no_second_round(self):
        brain = _brain([_tool_call("close_app",
                                  '{"app": "discord", "because": "выруби, пожалуйста, Discord"}'),
                        [_chunk("Готово, закрыла Discord.")]])
        with mock.patch.object(launcher, "close",
                               return_value=(True, "Discord закрыта")) as shut:
            said = list(brain.reply("Ну, выруби, пожалуйста, Discord."))
        shut.assert_called_once_with("discord")
        # Закрылось — второй круг в облако не нужен, говорим сами (26 сентября).
        self.assertEqual(len(brain._client.bodies), 1)
        # Вариант подтверждения выбирается случайно, поэтому сверяем со всем
        # набором: с одной строкой тест ловил бы только один из трёх.
        self.assertEqual(len(said), 1)
        self.assertIn(said[0], [f.format(title="Discord")
                                for f in hands.CONFIRM[hands.CLOSE_NAME]])
        self.assertEqual(brain._history[-1]["content"], said[0])

    def test_failure_comes_back_as_an_error(self):
        brain = _brain([_tool_call("close_app",
                                  '{"app": "claude", "because": "закрой claude"}'),
                        [_chunk("Не вышло, не знаю как.")]])
        events = []
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        with mock.patch.object(launcher, "close", return_value=(False, "не знаю")):
            list(brain.reply("закрой claude"))
        self.assertEqual([e for e in events if e[0] != "action_check"],
                         [("close_failed", "не знаю")])

    def test_only_ids_from_the_list(self):
        with mock.patch.object(launcher, "close") as shut:
            answer = json.loads(hands.run_close('{"app": "calc.exe"}', None, APPS))
        shut.assert_not_called()
        self.assertIn("нет такой программы", answer["error"])

    def test_same_enum_as_launch(self):
        launch = hands.tool(APPS)["function"]["parameters"]
        close = hands.close_tool(APPS)["function"]["parameters"]
        self.assertEqual(launch["properties"]["app"]["enum"],
                         close["properties"]["app"]["enum"])


# --- Поиск кнопкой на телефоне -------------------------------------------


class _FakeVoice:
    """Голосовой цикл, которому ничего не надо: только флаги."""

    def __init__(self, running=True, ready=True):
        self.running = running
        self.ready = ready
        self._trust_next = False
        self._search_next = False
        self._armed_at = 0.0
        self.opened = 0
        self.stopped = 0

    def _speaker_idle(self):
        return True

    def shut_up(self):
        self.stopped += 1

    def _open_conversation(self):
        self.opened += 1

    def arm_button(self, search=False):
        """Настоящий `VoiceLoop.arm_button` — флаги те же."""
        if not self._speaker_idle():
            self.shut_up()
        self._armed_at = time.monotonic()
        self._trust_next = True
        if search:
            self._search_next = True
        self._open_conversation()


class _FakePhone:
    def __init__(self):
        self.results = []
        self.failed = []

    def send_search_result(self, query, answer, sources=None):
        self.results.append((query, answer, list(sources or [])))

    def send_search_fail(self, text):
        self.failed.append(text)


def _runtime(voice=None):
    rt = object.__new__(WebRuntime)
    rt.voice = voice if voice is not None else _FakeVoice()
    rt.server = _FakePhone()
    rt._remember = lambda kind, payload: None
    return rt


# Момент, вокруг которого считаются часы в тестах кнопки: подменены и
# `monotonic`, и `perf_counter`, поэтому `sleep` здесь не нужен вовсе.
MOMENT = 10_000.0


class _Speaker:
    """Динамик, который или молчит, или говорит — как в момент нажатия."""

    def __init__(self, busy=False):
        self.idle = threading.Event()
        if not busy:
            self.idle.set()


def _armable(busy=False):
    """Настоящий `VoiceLoop` с одной только кнопкой.

    Динамик и разговор подменены, остальное — как в жизни: `arm_button`
    здесь настоящий, а не заглушка.
    """
    loop = object.__new__(VoiceLoop)
    loop._speaker = _Speaker(busy)
    loop._fallback = None
    loop._trust_next = False
    loop._search_next = False
    loop._armed_at = 0.0
    loop._open = False
    loop._opened_at = 0.0
    loop._last_turn = 0.0
    loop._last_talk = 0.0
    loop._first_pending = False
    loop._spoke_at = -1000.0
    loop._speaking_text = ""
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._tell_phone = lambda *args, **kwargs: None
    loop.shut_up = lambda: loop.events.append(("shut_up", {}))
    return loop


def _armed(busy=False):
    """Тот же цикл, но кнопка уже нажата и ждёт фразу хозяина."""
    loop = _armable(busy)
    loop._trust_next = True
    loop._search_next = True
    loop._armed_at = MOMENT
    return loop


def _phrase(seconds=3.0):
    return np.zeros(int(config.SAMPLE_RATE * seconds), dtype=np.float32)


def _heard_at(start, seconds=3.0):
    """`heard_at` для фразы, речь в которой началась в `start` (`monotonic`).

    Как у настоящего микрофона: запись начинается с предзаписи (PREROLL) —
    за PREROLL до первого слова, в длину фразы она входит; `heard_at` —
    конец записи плюс тишина конца фразы.
    """
    return start - audio_in.PREROLL + seconds + audio_in.SILENCE_TO_END


def _frozen_clocks():
    """Часы, стоящие на `MOMENT`: ждать не надо, а `_started_before_button`
    сам переводит `heard_at` из `perf_counter` в `monotonic`."""
    return mock.patch.multiple(
        "core.voice_loop.time",
        monotonic=mock.Mock(return_value=MOMENT),
        perf_counter=mock.Mock(return_value=MOMENT),
    )


class SearchButtonTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = True
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))

    def test_button_marks_the_next_phrase_as_a_query(self):
        rt = _runtime()
        rt._search()
        self.assertTrue(rt.voice._search_next)
        self.assertTrue(rt.voice._trust_next)
        self.assertEqual(rt.voice.opened, 1)
        self.assertEqual(rt.server.failed, [])

    def test_arm_button_raises_everything_the_phone_waits_for(self):
        loop = _armable()
        loop.arm_button(search=True)
        self.assertTrue(loop._trust_next)
        self.assertTrue(loop._search_next)
        self.assertGreater(loop._armed_at, 0.0)
        # Окно разговора открыто: кнопка работает и в режиме "не слушает".
        self.assertTrue(loop._open)

    def test_the_button_first_silences_her(self):
        # Чаще всего жмут именно чтобы прервать поток.
        loop = _armable(busy=True)
        loop.arm_button()
        self.assertIn(("shut_up", {}), loop.events)

    def test_a_plain_button_keeps_the_search_it_did_not_ask_for(self):
        # Удержание круга посреди забытого запроса его не отменяет.
        loop = _armable()
        loop._search_next = True
        loop.arm_button()
        self.assertTrue(loop._trust_next)
        self.assertTrue(loop._search_next)

    def test_a_phrase_started_before_the_button_is_not_a_query(self):
        # 28.09, 21:25: хозяин говорил сам с собой, нажал «найти» посреди
        # фразы — и она, закончившись через пару секунд, ушла в модель как
        # поисковый запрос.
        loop = _armed()
        with _frozen_clocks():
            heard = loop._should_answer("Плюс можно вот сейчас нажать",
                                       _phrase(), _heard_at(MOMENT - 3.0))
        self.assertFalse(heard)
        self.assertEqual([p for k, p in loop.events if k == "ignored"],
                         [{"text": "Плюс можно вот сейчас нажать",
                           "why": "начата до кнопки"}])
        # Флаги живы: кнопка ждёт следующую фразу, а не теряется.
        self.assertTrue(loop._trust_next)
        self.assertTrue(loop._search_next)

    def test_a_word_right_after_the_button_is_taken(self):
        # Нажал и сразу заговорил: 0.1 с после кнопки. С вычтенной
        # предзаписью такая фраза считалась начатой до кнопки и пропадала.
        loop = _armed()
        with _frozen_clocks():
            heard = loop._should_answer("курс доллара", _phrase(),
                                       _heard_at(MOMENT + 0.1))
        self.assertTrue(heard)

    def test_a_word_just_before_the_button_is_forgiven(self):
        # Заговорил на 0.3 с раньше, чем палец дошёл до кнопки, — его.
        loop = _armed()
        with _frozen_clocks():
            heard = loop._should_answer("курс доллара", _phrase(),
                                       _heard_at(MOMENT - 0.3))
        self.assertTrue(heard)

    def test_a_phrase_started_after_the_button_is_taken(self):
        loop = _armed()
        with _frozen_clocks():
            heard = loop._should_answer("курс доллара", _phrase(),
                                       _heard_at(MOMENT + 1.0))
        self.assertTrue(heard)
        self.assertEqual([p for k, p in loop.events if k == "ignored"], [])
        self.assertFalse(loop._trust_next)

    def test_flag_is_taken_once(self):
        # Ровно одна фраза: иначе «как дела» через минуту ушло бы в поиск.
        loop = object.__new__(VoiceLoop)
        loop._search_next = True
        search = loop._search_next
        loop._search_next = False
        self.assertTrue(search)
        self.assertFalse(loop._search_next)

    def test_flag_dies_with_the_conversation_window(self):
        loop = object.__new__(VoiceLoop)
        loop._open = True
        loop._last_turn = 5.0
        loop._search_next = True
        loop._emit = lambda *args: None
        loop._tell_phone = lambda *args, **kwargs: None
        loop._digest_later = lambda: None
        loop._brain = None
        loop._close_conversation()
        self.assertFalse(loop._search_next)
        self.assertFalse(loop._open)

    def test_voice_off(self):
        rt = _runtime(_FakeVoice(running=False))
        rt._search()
        self.assertEqual(rt.server.failed, ["голос выключен"])
        self.assertFalse(rt.voice._search_next)

    def test_web_off(self):
        config.WEB_SEARCH = False
        rt = _runtime()
        rt._search()
        self.assertEqual(rt.server.failed, ["интернет выключен в настройках"])
        self.assertFalse(rt.voice._search_next)

    def test_search_result_goes_to_the_phone(self):
        phone = _FakePhone()
        loop = object.__new__(VoiceLoop)
        loop._server = phone
        loop._external_server = None
        loop._brain = NS(last_sources=[{"title": "Курс", "host": "cbr.ru",
                                        "url": "https://cbr.ru/x"}])
        loop._send_search("курс доллара", "Девяносто два")
        query, answer, sources = phone.results[0]
        self.assertEqual((query, answer), ("курс доллара", "Девяносто два"))
        self.assertEqual(sources[0]["host"], "cbr.ru")

    def test_no_brain_means_no_sources_but_still_an_answer(self):
        phone = _FakePhone()
        loop = object.__new__(VoiceLoop)
        loop._server = phone
        loop._external_server = None
        loop._brain = None
        loop._send_search("что угодно", "Ответ")
        self.assertEqual(phone.results[0][2], [])


class SearchModeTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = True
        config.TTS_ENGINE = "silero"
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    # --- Быстрый поиск (29.09): ищем сами, модель отвечает одним кругом ---

    FOUND = '{"query": "курс доллара", "results": [{"title": "Курс ЦБ", "url": "https://cbr.ru/rates", "snippet": "92 рубля"}]}'

    def test_the_button_turn_searches_before_asking_the_model(self):
        # Раньше первый поход к модели нужен был только, чтобы она придумала
        # запрос: +1.5–4 с на пустом месте. Теперь поиск — до модели.
        brain = _brain([[_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool", return_value=self.FOUND) as поиск:
            said = list(brain.reply("сколько стоит доллар", search=True))
        self.assertEqual(поиск.call_args[0][0], "web_search")
        self.assertIn('"query": "сколько стоит доллар"', поиск.call_args[0][1])
        # Один запрос к модели, без инструментов и без требования их звать.
        self.assertEqual(len(brain._client.bodies), 1)
        self.assertEqual(_names(brain), [])
        self.assertNotIn("tool_choice", brain._client.bodies[0])
        последнее = brain._client.bodies[0]["messages"][-1]["content"]
        self.assertIn("92 рубля", последнее)
        self.assertIn("Ты уже поискала в интернете", последнее)
        # Сначала «секунду, гляну», потом ответ.
        self.assertIn(said[0], brain_module.FILLERS)
        self.assertEqual(said[-1], "Девяносто два рубля.")

    def test_the_command_gives_the_query_without_its_verb(self):
        brain = _brain([[_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool", return_value=self.FOUND) as поиск:
            list(brain.reply("найди в инете курс доллара", search=True))
        self.assertIn('"query": "курс доллара"', поиск.call_args[0][1])

    def test_found_text_stays_out_of_the_history(self):
        brain = _brain([[_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool", return_value=self.FOUND):
            list(brain.reply("сколько стоит доллар", search=True))
        реплика = [t for t in brain._history if t["role"] == "user"][-1]
        self.assertEqual(реплика["content"], "сколько стоит доллар")
        ответ = [t for t in brain._history if t["role"] == "assistant"][-1]
        self.assertEqual(ответ.get("searched"), ["сколько стоит доллар"])

    # --- Поисковики не ответили — сразу честный ответ, без второй попытки ---

    FAILED = '{"error": "поисковики не ответили"}'

    def test_a_failed_search_is_told_at_once_without_a_second_try(self):
        # 29.09 вживую: быстрый поиск упал, модель искала ещё раз, упало и
        # это — 26 секунд тишины вместо 10.
        brain = _brain([[_chunk("Интернет сейчас не отвечает.")]])
        with mock.patch.object(web, "run_tool", return_value=self.FAILED) as поиск:
            said = list(brain.reply("сколько стоит доллар", search=True))
        self.assertEqual(поиск.call_count, 1)
        self.assertEqual(len(brain._client.bodies), 1)
        self.assertEqual(_names(brain), [])
        self.assertNotIn("tool_choice", brain._client.bodies[0])
        последнее = brain._client.bodies[0]["messages"][-1]["content"]
        self.assertIn("поисковики сейчас не ответили", последнее)
        self.assertEqual(said[-1], "Интернет сейчас не отвечает.")
        # Не нашла — значит, и «уже искала» в истории не пишем.
        ответ = [t for t in brain._history if t["role"] == "assistant"][-1]
        self.assertNotIn("searched", ответ)

    # --- Поиск со снимком экрана — прежний путь через инструмент -----------

    IMAGE = "data:image/png;base64,iVBORw0KGgo="

    def test_the_button_turn_gives_the_internet_and_nothing_else(self):
        # 28.09, 21:26: на фразу с кнопки она сняла экран и описала его.
        brain = _brain([[_chunk("Привет.")]])
        list(brain.reply("А что я на поиск нажимаю", search=True, image=self.IMAGE))
        self.assertEqual(_names(brain), ["web_search", "read_page"])

    def test_the_first_turn_demands_the_search(self):
        # Иначе модель отвечает болтовнёй, ни разу не поискав.
        brain = _brain([_tool_call("web_search", '{"query": "курс доллара"}'),
                        [_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool", return_value='{"results": []}'):
            list(brain.reply("сколько стоит доллар", search=True, image=self.IMAGE))
        self.assertEqual(brain._client.bodies[0].get("tool_choice"), "required")
        self.assertNotIn("tool_choice", brain._client.bodies[1])

    def test_an_ordinary_turn_never_asks_for_a_tool(self):
        # Слова «найди» открывают интернет и сами по себе, но требовать
        # вызова инструмента вправе только кнопка.
        brain = _brain([_tool_call("web_search", '{"query": "курс доллара"}'),
                        [_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool", return_value='{"results": []}'):
            list(brain.reply("найди курс доллара"))
        self.assertIn("web_search", _names(brain))
        self.assertNotIn("tool_choice", brain._client.bodies[0])
        self.assertNotIn("tool_choice", brain._client.bodies[1])

    def test_a_provider_refusing_tool_choice_loses_only_that(self):
        # Такой отказ не должен выключить инструменты навсегда: без
        # `tool_choice` модель просто не обязана искать, но может.
        import httpx
        from openai import BadRequestError

        class _Picky(_Client):
            def _create(self, **body):
                self.bodies.append(body)
                if "tool_choice" in body:
                    request = httpx.Request("POST", "https://api.example/v1")
                    raise BadRequestError(
                        "Error code: 400 - Unsupported parameter: 'tool_choice'",
                        response=httpx.Response(400, request=request), body=None)
                return iter(self.streams.pop(0))

        brain = _brain([_tool_call("web_search", '{"query": "курс доллара"}'),
                        [_chunk("Девяносто два рубля.")]])
        picky = _Picky(brain._client.streams)
        brain._client = picky
        with mock.patch.object(web, "run_tool", return_value='{"results": []}'):
            said = list(brain.reply("сколько стоит доллар", search=True,
                                    image=self.IMAGE))
        self.assertEqual(picky.bodies[0].get("tool_choice"), "required")
        self.assertNotIn("tool_choice", picky.bodies[1])
        self.assertEqual([t["function"]["name"] for t in picky.bodies[1]["tools"]],
                         ["web_search", "read_page"])
        self.assertIsNot(brain._tools_ok, False)
        self.assertEqual(said[-1], "Девяносто два рубля.")

    def test_without_the_flag_the_same_phrase_gets_no_web(self):
        brain = _brain([[_chunk("Привет.")]])
        list(brain.reply("ну привет"))
        self.assertNotIn("web_search", _names(brain))

    def test_prompt_says_it_is_a_query(self):
        # Подсказка «это запрос для поиска» нужна прежнему пути, где искать
        # будет сама модель (поиск со снимком).
        brain = _brain([[_chunk("Ок.")]])
        list(brain.reply("сколько стоит доллар", search=True, image=self.IMAGE))
        system = "\n".join(
            str(m["content"]) for m in brain._client.bodies[0]["messages"]
            if m["role"] == "system"
        )
        self.assertIn("кнопку поиска", system)

    def test_sources_come_from_the_real_tool_result(self):
        found = {"query": "курс доллара", "results": [
            {"title": "Курс ЦБ", "url": "https://cbr.ru/rates", "snippet": "доллар"},
            {"title": "Курсы ЦБ на сегодня", "url": "https://cbr.ru/rates2",
             "snippet": "доллар"},
        ]}
        brain = _brain([_tool_call("web_search", '{"query": "курс доллара"}'),
                        [_chunk("Девяносто два рубля.")]])
        with mock.patch.object(web, "run_tool",
                               return_value=json.dumps(found, ensure_ascii=False)):
            list(brain.reply("курс доллара", search=True))
        self.assertEqual(
            brain.last_sources,
            [{"title": "Курс ЦБ", "host": "cbr.ru", "url": "https://cbr.ru/rates"},
             {"title": "Курсы ЦБ на сегодня", "host": "cbr.ru",
              "url": "https://cbr.ru/rates2"}],
        )

    def test_at_most_three_sources(self):
        found = {"query": "q", "results": [
            {"title": f"Результат {n}", "url": f"https://site{n}.ru/page",
             "snippet": ""} for n in range(6)
        ]}
        brain = _brain([_tool_call("web_search", '{"query": "q"}'),
                        [_chunk("Готово.")]])
        with mock.patch.object(web, "run_tool",
                               return_value=json.dumps(found, ensure_ascii=False)):
            list(brain.reply("что-нибудь", search=True))
        self.assertEqual(len(brain.last_sources), MAX_SOURCES)
        self.assertEqual([s["host"] for s in brain.last_sources],
                         ["site0.ru", "site1.ru", "site2.ru"])

    def test_read_page_becomes_a_source_too(self):
        page = {"url": "https://cbr.ru/rates", "text": "текст страницы"}
        brain = _brain([_tool_call("read_page", '{"url": "https://cbr.ru/rates"}'),
                        [_chunk("Девяносто два.")]])
        with mock.patch.object(web, "run_tool",
                               return_value=json.dumps(page, ensure_ascii=False)):
            list(brain.reply("курс", search=True))
        self.assertEqual([s["host"] for s in brain.last_sources], ["cbr.ru"])

    def test_failed_search_brings_no_sources(self):
        brain = _brain([_tool_call("web_search", '{"query": "q"}'),
                        [_chunk("Ничего не нашла.")]])
        with mock.patch.object(web, "run_tool", return_value='{"error": "пусто"}'):
            list(brain.reply("что-нибудь", search=True))
        self.assertEqual(brain.last_sources, [])

    def test_sources_are_cleared_between_answers(self):
        brain = _brain([_tool_call("web_search", '{"query": "q"}'),
                        [_chunk("Готово.")]])
        found = {"query": "q", "results": [
            {"title": "Сайт", "url": "https://site.ru/page", "snippet": ""}]}
        with mock.patch.object(web, "run_tool",
                               return_value=json.dumps(found, ensure_ascii=False)):
            list(brain.reply("первый", search=True))
        self.assertEqual(len(brain.last_sources), 1)
        brain._client.streams.append([_chunk("И без поиска.")])
        list(brain.reply("просто разговор"))
        self.assertEqual(brain.last_sources, [])


if __name__ == "__main__":
    unittest.main()
