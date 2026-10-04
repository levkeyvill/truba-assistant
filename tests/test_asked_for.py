"""Действие на компе — только если о нём просили в этой фразе.

`because` должен дословно входить в текущую реплику, чтобы действие не
повторялось по прошлой просьбе или выдуманной цитате.
"""

import json
import unittest

from core import hands

APPS = [{"id": "youtube", "title": "YouTube"}, {"id": "discord", "title": "Discord"}]


class AskedForTests(unittest.TestCase):
    def test_a_quote_from_the_phrase_is_a_request(self):
        for имя, фраза, цитата in (
            (hands.NAME, "Открой, пожалуйста, Ютуб.", "Открой, пожалуйста, Ютуб."),
            (hands.NAME, "ютуб давай", "ютуб"),
            (hands.CLOSE_NAME, "Труба, закрой дискорд", "закрой дискорд"),
            (hands.SHOT_NAME, "Ну, сделай скрин, пожалуйста.", "сделай скрин"),
            (hands.MOMENT_NAME, "сохрани клип", "сохрани клип"),
            (hands.LOOK_NAME, "глянь, что у меня на экране", "что у меня на экране"),
        ):
            with self.subTest(фраза=фраза):
                self.assertTrue(hands.asked_for(имя, фраза, APPS, because=цитата))

    def test_an_invented_quote_is_not_a_request(self):
        # «Скриншота» в реплике нет, а «сделать скрин» модель вставила от себя.
        self.assertFalse(hands.asked_for(
            hands.SHOT_NAME, "Ну, привет", APPS, because="сделать скриншот"))
        # Слова из прошлой реплики в текущую не входят.
        self.assertFalse(hands.asked_for(
            hands.CLOSE_NAME, "а кстати, что нового?", APPS,
            because="закрой дискорд"))

    def test_an_empty_quote_is_not_a_request(self):
        for цитата in ("", "   ", "ну", "а"):
            with self.subTest(цитата=цитата):
                self.assertFalse(hands.asked_for(
                    hands.NAME, "Открой, пожалуйста, Ютуб.", APPS, because=цитата))

    def test_case_and_punctuation_do_not_matter(self):
        self.assertTrue(hands.asked_for(
            hands.SHOT_NAME, "Ну, сделай скрин, пожалуйста!", APPS,
            because="Сделай скрин, пожалуйста"))
        # «ё» приводится к «е» с обеих сторон.
        self.assertTrue(hands.asked_for(
            hands.NOTE_NAME, "запиши мысль про ёлку", APPS, because="про ёлку"))
        # Знаки препинания внутри цитаты — просто пробелы.
        self.assertTrue(hands.asked_for(
            hands.CLOSE_NAME, "выруби, пожалуйста, Discord!", APPS,
            because="выруби, пожалуйста, Discord"))

    def test_no_request_in_this_phrase(self):
        self.assertFalse(hands.asked_for(
            hands.NAME, "М-м, кстати, знаешь, что ещё надо?", APPS,
            because="открыть ютуб"))
        self.assertFalse(hands.asked_for(
            hands.LOOK_NAME, "Попью что ли?", APPS, because="посмотреть на экран"))

    def test_refusal_is_an_error_for_the_model(self):
        error = json.loads(hands.not_asked(hands.NAME))["error"]
        self.assertEqual(error,
                         "Это действие сейчас не нужно: в этой реплике он о нём не просит. "
                         "Ответь на его последнюю реплику по смыслу, как в обычном "
                         "разговоре, и не упоминай это действие и проверку.")

    def test_web_tools_are_not_guarded(self):
        self.assertTrue(hands.asked_for("web_search", "что угодно", APPS))


if __name__ == "__main__":
    unittest.main()
