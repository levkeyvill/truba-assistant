"""Проверка «просил ли» идёт одновременно: судьи круга и поиск файла.

Судья — отдельный запрос в облако (~1–1,5 с), поиск файла по дискам — 3–6 с.
Один не ждёт другого, а действие выполняется только после «да». Ни сети,
ни дисков: судья, поиск и открытие подменены.
"""

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from openai import APIConnectionError

import config
from core import files, hands, launcher
from test_fast_actions import APPS, _brain, _chunk, _rounds, _tool_call, _two_calls

ЖДАТЬ = 0.4
FIND_ARGS = '{"name": "план", "open": true, "because": "открой файл план"}'
SAID = "Труба, открой файл план."


def _judge(answer="да", delay=ЖДАТЬ, threads=None):
    """Подменённый запрос судьи из потока: отвечает через `delay` секунд."""
    def ask(*_args, **_kwargs):
        if threads is not None:
            threads.append(threading.current_thread())
        time.sleep(delay)
        if isinstance(answer, Exception):
            raise answer
        return NS(choices=[NS(message=NS(content=answer))], usage=None)
    return ask


def _slow_find(calls, delay=ЖДАТЬ):
    def find(words, drive="", *args, **kwargs):
        calls.append((words, drive))
        time.sleep(delay)
        return {"ok": True, "found": [Path("C:/Users/test/Desktop/план.txt")]}
    return find


def _tool_answer(brain):
    """Ответ инструмента, ушедший модели во втором круге."""
    return [m for m in brain._client.bodies[1]["messages"]
            if m.get("role") == "tool"][-1]["content"]


class JudgeParallelTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def _events(self, brain):
        events = []
        main = threading.current_thread()
        brain.on_event = lambda kind, payload: events.append(
            (kind, payload, threading.current_thread() is main))
        return events

    def test_поиск_файла_идёт_вместе_с_судьёй(self):
        brain = _brain([_tool_call(hands.FIND_NAME, FIND_ARGS),
                        [_chunk("Лишний второй круг.")]])
        brain._ask_plainly = _judge("да")
        поиски = []
        with mock.patch.object(files, "find", side_effect=_slow_find(поиски)), \
                mock.patch.object(files, "open_file", return_value={
                    "ok": True, "text": "Открыла «план.txt»."}) as открыть:
            начало = time.monotonic()
            said = list(brain.reply(SAID))
            прошло = time.monotonic() - начало
        # По очереди было бы 0,8 с и больше.
        self.assertLess(прошло, 0.7)
        self.assertEqual(len(поиски), 1)
        открыть.assert_called_once()
        self.assertEqual(said[-1], "Открыла «план.txt».")
        self.assertEqual(_rounds(brain), 1)

    def test_судья_сказал_нет_файл_не_открыт(self):
        brain = _brain([_tool_call(hands.FIND_NAME, FIND_ARGS),
                        [_chunk("Ладно, не открываю.")]])
        brain._ask_plainly = _judge("нет")
        events = self._events(brain)
        with mock.patch.object(files, "find", side_effect=_slow_find([])), \
                mock.patch.object(files, "open_file") as открыть:
            list(brain.reply(SAID))
        открыть.assert_not_called()
        self.assertNotIn("file_opened", [kind for kind, _, _ in events])
        ответ = _tool_answer(brain)
        self.assertEqual(ответ, hands.not_really_asked(hands.FIND_NAME))

    def test_судья_не_ответил_файл_не_открыт(self):
        brain = _brain([_tool_call(hands.FIND_NAME, FIND_ARGS),
                        [_chunk("Не вышло проверить.")]])
        brain._ask_plainly = _judge(RuntimeError("облако молчит"))
        with mock.patch.object(files, "find", side_effect=_slow_find([], 0.0)), \
                mock.patch.object(files, "open_file") as открыть:
            list(brain.reply(SAID))
        открыть.assert_not_called()
        ответ = _tool_answer(brain)
        self.assertEqual(ответ, hands.check_failed(hands.FIND_NAME))

    def test_обрыв_связи_у_судьи_не_вешает_ответ(self):
        # Смена провайдера берёт `_reply_lock`, а его держит `reply`. Из
        # потока судьи её нет: повтор проверки — в основном потоке.
        brain = _brain([_tool_call(hands.FIND_NAME, FIND_ARGS),
                        [_chunk("Лишний второй круг.")]])
        обрыв = APIConnectionError(request=mock.Mock())
        # Настоящий `_ask_plainly`: подменены только сам запрос и смена
        # провайдера, которая, как настоящая, берёт `_reply_lock`.
        del brain._ask_plainly
        основной = []

        def запрос(*_args, **_kwargs):
            if threading.current_thread() not in основной:
                raise обрыв  # поток судьи: связь оборвалась
            return NS(choices=[NS(message=NS(content="да"))], usage=None)

        def сменить(_exc):
            with brain._reply_lock:
                return False

        brain._ask_plainly_here = запрос
        brain._fall_back = сменить
        результат = []
        with mock.patch.object(files, "find", side_effect=_slow_find([], 0.0)), \
                mock.patch.object(files, "open_file", return_value={
                    "ok": True, "text": "Открыла «план.txt»."}):
            поток = threading.Thread(
                target=lambda: результат.extend(brain.reply(SAID)), daemon=True)
            основной.append(поток)
            поток.start()
            поток.join(5.0)
        self.assertFalse(поток.is_alive(), "ответ завис")
        self.assertEqual(результат[-1], "Открыла «план.txt».")

    def test_два_судьи_одного_круга_одновременно(self):
        brain = _brain([
            _two_calls((hands.NAME, '{"app": "youtube", "because": "открой YouTube"}'),
                       (hands.NAME, '{"app": "discord", "because": "открой Discord"}')),
            [_chunk("Готово.")],
        ])
        потоки = []
        brain._ask_plainly = _judge("да", threads=потоки)
        events = self._events(brain)
        with mock.patch.object(launcher, "launch", return_value=(True, "ок")):
            начало = time.monotonic()
            list(brain.reply("Открой YouTube и открой Discord."))
            прошло = time.monotonic() - начало
        self.assertLess(прошло, 0.7)
        проверки = [(payload["action"], в_основном)
                    for kind, payload, в_основном in events if kind == "action_check"]
        self.assertEqual(len(проверки), 2)
        # По порядку вызовов и из того же потока, что звал `reply`.
        self.assertIn("youtube", проверки[0][0].lower())
        self.assertIn("discord", проверки[1][0].lower())
        self.assertTrue(all(в_основном for _, в_основном in проверки))
        # А сами запросы — из потоков судей.
        self.assertTrue(all(поток is not threading.main_thread() for поток in потоки))

    def test_без_цитаты_судья_не_зовётся(self):
        brain = _brain([_tool_call(hands.FIND_NAME,
                                   '{"name": "план", "open": true, "because": "чего не было"}'),
                        [_chunk("Ладно.")]])
        спросили = []
        brain._ask_plainly = _judge("да", 0.0, threads=спросили)
        brain._ask_plainly = lambda *a, **k: спросили.append("основной")
        with mock.patch.object(files, "find") as искать:
            list(brain.reply(SAID))
        self.assertEqual(спросили, [])
        искать.assert_not_called()
        ответ = _tool_answer(brain)
        self.assertEqual(ответ, hands.not_asked(hands.FIND_NAME))

    def test_youtube_без_цитаты_и_без_названия_отказы_свои(self):
        # Нет цитаты — отказ «не просил»; цитата есть, а YouTube не назван —
        # свой отказ про YouTube.
        for because, ожидаем in (("чего не было", hands.not_asked(hands.YT_NAME)),
                                 ("включи ролик", hands.youtube_not_named())):
            with self.subTest(because=because):
                brain = _brain([_tool_call(hands.YT_NAME, json.dumps(
                    {"query": "котики", "because": because}, ensure_ascii=False)),
                    [_chunk("Ладно.")]])
                brain._ask_plainly = _judge("да", 0.0)
                list(brain.reply("Труба, включи ролик про котиков."))
                ответ = _tool_answer(brain)
                self.assertEqual(ответ, ожидаем)

    def test_run_find_file_без_готового_ищет_сама(self):
        поиски = []
        with mock.patch.object(files, "find", side_effect=_slow_find(поиски, 0.0)):
            ответ = json.loads(hands.run_find_file(
                '{"name": "план", "drive": "D"}'))
        self.assertEqual(поиски, [("план", "D")])
        self.assertTrue(ответ["ok"])
        self.assertEqual(ответ["name"], "план.txt")


if __name__ == "__main__":
    unittest.main()
