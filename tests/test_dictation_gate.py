"""Во время диктовки его речь идёт в заметку мимо фильтра разговора.

28.09, 11:56. Хозяин диктовал одну фразу 30 с: окно разговора за это время
истекло, на фоне играл YouTube, и фильтр выкинул фразу — «играет звук,
разговор остыл», а следом «всё» и «заканчивай» — «не позвали по имени».
Диктовка кончилась только по тишине и записала одно первое предложение,
а телефон всё это время светил янтарём «диктую».

Эхо её голоса и чужой голос — проверяются и в диктовке: это стоит выше.
"""

import unittest
from unittest import mock

import config

try:  # discover -s tests кладёт папку тестов в путь, запуск по имени — нет
    from test_voice_guard import _loop, _phrase
except ImportError:
    from tests.test_voice_guard import _loop, _phrase


class _Шумно:
    """В комнате играет звук: YouTube в колонках."""

    def system_peak(self):
        return 1.0

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class ДиктовкаМимоФильтраTests(unittest.TestCase):
    def setUp(self):
        было = (config.LISTEN_MODE, config.REQUIRE_NAME_WHEN_NOISY, config.VOICE_APP_GUARD)
        self.addCleanup(self._вернуть, было)
        config.LISTEN_MODE = "name"
        config.REQUIRE_NAME_WHEN_NOISY = True
        config.VOICE_APP_GUARD = False

    def _вернуть(self, было):
        config.LISTEN_MODE, config.REQUIRE_NAME_WHEN_NOISY, config.VOICE_APP_GUARD = было

    def _цикл(self, диктовка):
        # Окно разговора закрыто и остыло, в комнате шумно — как было 28.09.
        loop = _loop(open_conversation=False)
        loop._ducker = _Шумно()
        # Разговор закрыт: `_silence` у закрытого — 0, а фильтр скажет
        # «не позвали по имени», как на вторую половину 28.09.
        loop._dictation = диктовка
        loop._open_conversation = mock.Mock()
        return loop

    def test_a_long_phrase_in_dictation_goes_to_the_note(self):
        loop = self._цикл(["надо будет подготовить тебя к публикации"])
        self.assertTrue(loop._should_answer(
            "убрать все привязанные к моей личности данные", _phrase(30.0)))
        loop._open_conversation.assert_called()

    def test_all_done_in_dictation_is_heard_without_the_name(self):
        loop = self._цикл(["мысль"])
        self.assertTrue(loop._should_answer("Всё, заканчивай.", _phrase(1.5)))

    def test_without_dictation_the_filter_works_as_before(self):
        loop = self._цикл(None)
        self.assertFalse(loop._should_answer("убрать личные данные", _phrase(3.0)))
        причины = [p["why"] for k, p in loop.events if k == "ignored"]
        self.assertTrue(причины)

    def test_her_own_echo_is_still_not_dictated(self):
        loop = self._цикл(["мысль"])
        loop._is_echo_tail = lambda phrase, heard_at: True
        self.assertFalse(loop._should_answer("Записываю.", _phrase(0.5)))


if __name__ == "__main__":
    unittest.main()
