"""Мозг при заходе первой: без инструментов, с пометкой в истории.

Сети нет — провайдер подменён заготовленным потоком. Проверяется ровно
три вещи: инструментов нет вовсе, в историю ложится пометка о молчании
(а не подсказка), и снимок экрана в историю не попадает.
"""

import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import proactive
from core.brain import Brain, own_turns

SHOT = "data:image/jpeg;base64,КАРТИНКА"


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


class _Client:
    """Отдаёт заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(слова=("Ну что, сидим?",), снимок=None):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client([[_chunk(" ".join(слова))]])
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    # Настоящий `_keep_history` писал бы в живую data/history.json.
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


def _заход(brain=None, image=None):
    """Один заход первой: подсказка из модуля проактивности, снимок рядом."""
    brain = brain if brain is not None else _brain()
    said = list(brain.reply(
        proactive.prompt(30, look=image is not None),
        image=image, aloud=True, first=proactive.mark(30),
    ))
    return brain, said


def _тело(brain, index=0):
    return brain._client.bodies[index]


class ЗаходПервойTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.HISTORY_TURNS, config.WEB_SEARCH, config.HEDGE)
        config.HISTORY_TURNS = 24
        config.WEB_SEARCH = False
        config.HEDGE = False

    def tearDown(self):
        (config.HISTORY_TURNS, config.WEB_SEARCH, config.HEDGE) = self._saved

    # --- Инструменты ------------------------------------------------------

    def test_no_tools_at_all(self):
        # Заговорила из тишины — и вдруг запускает Discord. Не должно.
        brain, _ = _заход()
        self.assertNotIn("tools", _тело(brain))

    def test_not_even_an_empty_tools_list(self):
        # Пустой список — это всё равно поле `tools` в теле запроса, а его
        # некоторые провайдеры отвергают. Проверяем по-настоящему.
        brain, _ = _заход()
        self.assertTrue(_тело(brain))
        self.assertIsNone(_тело(brain).get("tools", None))

    def test_ordinary_answer_keeps_its_tools(self):
        # Проверка на то, что обычный ответ не разучился инструментам:
        # их набор и порядок трогать нельзя (на нём держится кеш).
        brain = _brain()
        list(brain.reply("открой Discord", aloud=True))
        self.assertIn("tools", _тело(brain))
        self.assertTrue(_тело(brain)["tools"])

    # --- История ----------------------------------------------------------

    def test_history_gets_the_note_not_the_nudge(self):
        brain, _ = _заход()
        ход = [t for t in brain._history if t["role"] == "user"]
        self.assertEqual(len(ход), 1)
        # Подсказка в историю не идёт: иначе в следующем запросе модель
        # читала бы свой же текст как реплику хозяина.
        self.assertNotIn("Заговори с ним", ход[0]["content"])
        self.assertIn("Труба заговорила первой", ход[0]["content"])
        self.assertTrue(ход[0]["first"])

    def test_history_has_no_voice_score(self):
        # Отбора по голосу у захода нет: сходство ставить не с чем, и
        # выдуманное значение выкинуло бы ход из памяти.
        brain, _ = _заход()
        ход = [t for t in brain._history if t["role"] == "user"][0]
        self.assertNotIn("voice", ход)

    def test_answer_lands_in_history_after_the_note(self):
        brain, сказанное = _заход()
        роли = [t["role"] for t in brain._history]
        self.assertEqual(роли, ["user", "assistant"])
        self.assertEqual(brain._history[1]["content"], " ".join(сказанное))

    def test_screenshot_never_lands_in_history(self):
        # Картинка дорогая, и тянуть её в каждый следующий запрос незачем.
        brain, _ = _заход(image=SHOT)
        for ход in brain._history:
            self.assertIsInstance(ход["content"], str)
            self.assertNotIn("base64", ход["content"])
        # В сам запрос снимок при этом уходит.
        self.assertIn(SHOT, str(_тело(brain)["messages"]))

    # --- Память о хозяине -------------------------------------------------

    def test_own_turns_drops_the_talk_and_the_answer(self):
        # В догадках о хозяине её собственный заход — это выдумка, тем
        # более по снимку экрана. Наружу не должно уйти ничего.
        brain, _ = _заход(image=SHOT)
        оставлено = own_turns(list(brain._history),
                              float(getattr(config, "OWNER_THRESHOLD", 0.0)))
        self.assertEqual(оставлено, [])

    def test_own_turns_still_keeps_real_phrases(self):
        # Проверка на то, что правило не съело обычный разговор.
        оборот = [
            {"role": "user", "content": "поставь напоминалку", "voice": 0.9},
            {"role": "assistant", "content": "поставила"},
        ]
        self.assertEqual(own_turns(оборот, 0.35), оборот)


if __name__ == "__main__":
    unittest.main()
