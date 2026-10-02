"""Размышления в тексте ответа не идут в речь (02.10, test11, MiniMax).

MiniMax кладёт размышления не полем `reasoning_content`, а прямо в ответ:
«<think> The user is asking… </think> А как тебе надо?». Мозг этого не знал,
и она прочитала вслух свои английские мысли. Теперь `<think>…</think>`
вырезается из потока на лету (метка может прийти разорванной между кусками) и
из разовых ответов (память, заметки, запрос поиска).
"""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

from core import notes
from core.brain import Brain, без_размышлений


def _кусок(текст=None, finish=None):
    delta = NS(content=текст, tool_calls=None, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=None)


def _речь(*куски):
    said, reasoning = [], []
    поток = [_кусок(к) for к in куски] + [_кусок(None, finish="stop")]
    вслух = list(Brain._sentences(iter(поток), {}, said, reasoning, {}))
    return вслух, said, "".join(reasoning)


class РазмышленияВПотокеTests(unittest.TestCase):
    def test_мысли_целиком_не_звучат(self):
        вслух, said, мысли = _речь(
            "<think> The user is asking why I act this way. Keep it short. </think>",
            " А как тебе надо? Ты третий раз подряд говоришь «привет» и молчишь.")
        текст = " ".join(вслух)
        self.assertNotIn("think", текст)
        self.assertNotIn("The user", текст)
        self.assertIn("А как тебе надо?", текст)
        self.assertEqual(" ".join(said), текст, "в историю — то же, что вслух")
        self.assertIn("The user is asking", мысли)

    def test_метка_разорвана_между_кусками(self):
        вслух, _, мысли = _речь(
            "<thi", "nk>I should respond in char", "acter.</th", "ink>  Ну, что «О»? ",
            "Спроси уже что-нибудь, а то я тут сижу.")
        текст = " ".join(вслух)
        self.assertNotIn("<", текст)
        self.assertNotIn("character", текст)
        self.assertIn("Ну, что «О»?", текст)
        self.assertIn("respond in character", мысли)

    def test_похожее_на_метку_остаётся_текстом(self):
        вслух, _, _ = _речь("Если число <th", "ree, то меньше трёх, и это понятно.")
        self.assertIn("<three, то меньше трёх", " ".join(вслух))

    def test_незакрытые_мысли_в_речь_не_уходят(self):
        вслух, _, мысли = _речь("Сначала ответ, он нормальный и длинный. ",
                                "<think> unfinished thought about")
        self.assertEqual(вслух, ["Сначала ответ, он нормальный и длинный."])
        self.assertIn("unfinished", мысли)


class РазовыеОтветыTests(unittest.TestCase):
    def test_без_размышлений(self):
        self.assertEqual(без_размышлений("<think>x\ny</think>\n{\"a\": 1}"), '{"a": 1}')
        self.assertEqual(без_размышлений("{\"a\": 1}"), '{"a": 1}')
        self.assertEqual(без_размышлений("<think> оборвалось на потолке"), "")
        self.assertEqual(без_размышлений(None), "")

    def test_заметки_разбирают_ответ_без_мыслей(self):
        ответ = NS(choices=[NS(message=NS(content='<think>hmm</think>{"topic": "Идеи"}'))])
        мозг = mock.Mock()
        мозг._ask_plainly.return_value = ответ
        self.assertEqual(notes._ask(мозг, "вопрос"), '{"topic": "Идеи"}')


if __name__ == "__main__":
    unittest.main()
