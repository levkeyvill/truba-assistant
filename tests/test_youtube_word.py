"""YouTube — только когда хозяин сам сказал «ютуб».

27.09 хозяин: «можно же сделать просто: я говорю „найди на ютубе“ или
„открой на ютубе“, остальное всё — в поиск в интернет». До этого модель
«искала всё на YouTube»: на вопрос «что за мем про кошечку и трубу?» открыла
выдачу YouTube. Это его договорённость — одно слово, которое он сам говорит,
а не заплатка на формулировку.

Распознавание чаще пишет «YouTube» латиницей, чем «ютуб»: таблица команд и
проверка модели понимают оба написания.
"""

import json
import unittest
from unittest import mock

from core import commands, hands
try:  # discover -s tests кладёт папку тестов в путь, запуск по имени — нет
    from test_action_judge import APPS, Обстановка, _brain, _tool_call, _chunk, _судья
except ImportError:
    from tests.test_action_judge import (APPS, Обстановка, _brain, _tool_call, _chunk,
                                         _судья)


def _ютуб(запрос="мем про кошечку и трубу", вид="search", цитата="что за мем"):
    return _brain([_tool_call(hands.YT_NAME, json.dumps(
                      {"query": запрос, "kind": вид, "because": цитата},
                      ensure_ascii=False)),
                   [_chunk("Поняла.")]])


class ТаблицаКоманд(unittest.TestCase):
    def понять(self, фраза):
        команда = commands.understand(фраза, APPS)
        return (команда.action, команда.target, команда.kind) if команда else None

    def test_latin_youtube_from_recognition(self):
        self.assertEqual(self.понять("Открой на YouTube канал Джо Спин"),
                         ("youtube", "джо спин", "channel"))
        self.assertEqual(self.понять("YouTube борщ"), ("youtube", "борщ", "video"))

    def test_open_and_look_for_on_youtube(self):
        self.assertEqual(self.понять("открой на ютубе Мэддисона"),
                         ("youtube", "мэддисона", "video"))
        self.assertEqual(self.понять("поищи на ютубе борщ"),
                         ("youtube", "борщ", "search"))
        self.assertEqual(self.понять("найди на ютубе обзор 5070"),
                         ("youtube", "обзор 5070", "search"))

    def test_without_the_word_it_is_not_youtube(self):
        self.assertIsNone(self.понять("найди аниме Канасуба"))
        self.assertEqual(self.понять("открой ютуб")[:2], ("launch", "youtube"))


class СловоЮтуб(unittest.TestCase):
    def test_any_spelling(self):
        for фраза in ("найди на ютубе обзор", "вчера на YouTube смотрел",
                      "Ютубчик глянь", "на ютьюбе видел", "you tube открой"):
            with self.subTest(фраза=фраза):
                self.assertTrue(hands.names_youtube(фраза))

    def test_no_word(self):
        for фраза in ("что за мем про кошечку и трубу?", "найди аниме Канасуба",
                      "", None):
            with self.subTest(фраза=фраза):
                self.assertFalse(hands.names_youtube(фраза))


class МодельБезСлова(Обстановка):
    def test_no_word_no_youtube_and_no_judge(self):
        brain = _ютуб()
        спросили = _судья(brain, "да")
        with mock.patch.object(hands, "run_youtube") as ютуб:
            list(brain.reply("Труба, слушай, а что за мем про кошечку и трубу?"))
        ютуб.assert_not_called()
        # Слова нет — облако не спрашиваем: и так ясно.
        self.assertEqual(спросили, [])
        ответ = json.loads(brain._client.bodies[-1]["messages"][-1]["content"])
        self.assertIn("ютуб", ответ["error"])
        self.assertIn("интернет", ответ["error"])

    def test_with_the_word_the_judge_decides(self):
        brain = _ютуб("обзор Канасубы", "search", "найди на ютубе обзор Канасубы")
        спросили = _судья(brain, "да")
        with mock.patch.object(hands, "run_youtube",
                               return_value='{"ok": true, "text": "Открыла"}') as ютуб:
            list(brain.reply("Найди на ютубе обзор Канасубы"))
        ютуб.assert_called_once()
        self.assertEqual(len(спросили), 1)
        self.assertIn("показать поиск на YouTube: обзор Канасубы", спросили[0][0])


if __name__ == "__main__":
    unittest.main()
