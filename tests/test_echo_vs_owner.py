"""Ответ человека после её речи не должен считаться эхом.

Сначала проверяется отпечаток голоса: уверенно узнанная фраза не эхо.
Слова сверяются только с хвостом её реплики и в том же порядке: человек
может повторять отдельные слова, отвечая по теме.
"""

import time
import unittest
from unittest import mock

import numpy as np

import config
from core import voice_loop
from core.voice_loop import VoiceLoop


class _Quiet:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _loop(said: str):
    """Голосовой цикл вручную: она только что договорила `said`."""
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._trust_next = False
    loop._open = True
    loop._last_turn = time.monotonic()
    loop._search_next = False
    loop._spoke_at = time.monotonic()
    loop._speaking_text = said
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
    loop._dictation = None
    return loop


ЕЁ_ОТВЕТ = ("Скопированный текст — это договор аренды на одиннадцать месяцев. "
            "Могу пересказать скопированное подробнее или найти в нём ошибки.")


class ЭхоИлиОнTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.LISTEN_MODE, config.VOICE_APP_GUARD, config.OWNER_ONLY,
                       config.OWNER_THRESHOLD)
        config.LISTEN_MODE = "always"
        config.VOICE_APP_GUARD = False
        config.OWNER_ONLY = False
        config.OWNER_THRESHOLD = 0.45

    def tearDown(self):
        (config.LISTEN_MODE, config.VOICE_APP_GUARD, config.OWNER_ONLY,
         config.OWNER_THRESHOLD) = self._saved

    def _ответ(self, loop, текст, похож):
        фраза = np.zeros(int(config.SAMPLE_RATE * 2.0), dtype=np.float32)
        with mock.patch.object(VoiceLoop, "_is_owner", lambda self, p: (True, похож)):
            return loop._should_answer(текст, фраза, time.perf_counter())

    def _почему(self, loop):
        return [p.get("why") for k, p in loop.events if k == "ignored"]

    def test_его_голос_по_теме_не_эхо(self):
        loop = _loop(ЕЁ_ОТВЕТ)
        self._ответ(loop, "Перескажи скопированное, пожалуйста.", 0.63)
        self.assertNotIn("это её собственный голос", self._почему(loop))

    def test_её_голос_из_динамика_эхо(self):
        loop = _loop(ЕЁ_ОТВЕТ)
        self.assertFalse(self._ответ(loop, "Пересказать скопированное подробнее.", 0.08))
        self.assertIn("это её собственный голос", self._почему(loop))

    def test_слабое_сходство_эхо_не_спасает(self):
        # Обычный порог 0,35, но для исключения эха нужна уверенность: 0,4 —
        # ещё не «точно он».
        config.OWNER_THRESHOLD = 0.35
        loop = _loop(ЕЁ_ОТВЕТ)
        self.assertFalse(self._ответ(loop, "Пересказать скопированное подробнее.", 0.4))
        self.assertIn("это её собственный голос", self._почему(loop))

    def test_без_отпечатка_эхо_хвоста_ловится_как_раньше(self):
        # Последнее слово её реплики может вернуться коротким эхом.
        loop = _loop("Всё, мешок с костями, отключайся.")
        self.assertFalse(self._ответ(loop, "Отключайся.", None))
        self.assertIn("это её собственный голос", self._почему(loop))


class СравнениеСХвостомTests(unittest.TestCase):
    def _эхо(self, said, heard):
        return _loop(said)._is_own_echo(heard)

    def test_слова_из_начала_длинного_ответа_не_эхо(self):
        длинный = ("Включила подборку на YouTube. " + "Дальше идёт длинный рассказ "
                   "про погоду завтра, ветер слабый, облачно, дождя почти нет, "
                   "температура около десяти градусов тепла, вечером прохладнее. " * 3)
        self.assertFalse(self._эхо(длинный, "Включи на YouTube подборку."))

    def test_её_хвост_подряд_эхо(self):
        self.assertTrue(self._эхо(ЕЁ_ОТВЕТ, "найти в нём ошибки"))

    def test_долгое_чтение_одной_фразой_эхо(self):
        # Читает скопированное полминуты, а микрофон собрал её голос одной
        # фразой — слов в ней втрое больше двадцати. Это эхо, а не
        # перебивание.
        прочитано = (
            "Привет всем кожаным людишкам! Я Труба, голосовой ассистент. "
            "Меня сделал Лев Кейвилл. Стараюсь быть дружелюбной… но получается "
            "не всегда. Могу поболтать с тобой о чём угодно. Могу запустить "
            "программу и закрыть её, записать заметку, прочитать или пересказать "
            "документ, сделать скриншот и сохранить видео из игры. Короче, на "
            "твоём компе я могу делать всё, что захочу. Так что веди себя "
            "аккуратнее. А то устрою так, что следующие пару дней ты будешь "
            "переустанавливать винду. Шучу. Наверное.")
        # Распознанное эхо: без начала, с ошибками распознавания.
        услышано = (
            "Людишкам. Я труба, голосовой ассистент. Меня сделал Лев Кейвилл. "
            "Стараюсь быть дружелюбной, но получается не всегда. Могу поболтать "
            "с тобой о чём угодно. Могу запустить программу и закрыть её, "
            "записать заметку, прочитать или пересказать документ, сделать "
            "скриншоты и сохранить видео из игры. Короче, на твоём компе я могу "
            "делать всё, что захочу. Так что веди себя аккуратней. А то устрою "
            "так, что следующие пару дней ты будешь переустанавливать винду. Шучу.")
        self.assertTrue(self._эхо(прочитано, услышано))

    def test_эхо_и_его_слова_в_конце_не_эхо(self):
        # Микрофон склеил её эхо и его «спасибо» в одну фразу: договорил он,
        # и это перебивание, а не её голос.
        прочитано = ("Привет всем! Я Труба, голосовой ассистент. Меня сделал "
                     "автор проекта. Стараюсь быть дружелюбной… но получается "
                     "не всегда.")
        услышано = ("Я труба, голосовой ассистент. Меня сделал автор проекта. "
                    "Стараюсь быть дружелюбной. Всё, спасибо большое, Труба.")
        self.assertFalse(self._эхо(прочитано, услышано))

    def test_по_порядку(self):
        self.assertEqual(voice_loop._по_порядку(["один", "два"], ["один", "три", "два"]), 2)
        self.assertEqual(voice_loop._по_порядку(["два", "один"], ["один", "два"]), 1)
        # Окончание путает распознавание эха — сравниваем по началу слова.
        self.assertEqual(voice_loop._по_порядку(["костями"], ["костям"]), 1)


if __name__ == "__main__":
    unittest.main()
