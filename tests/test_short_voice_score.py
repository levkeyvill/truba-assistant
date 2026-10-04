"""Короткие фразы отбором по голосу не судятся, но сходство пишется в журнал.

Отпечаток подменён: модель не грузится, микрофона нет.
"""

import contextlib
import unittest

import numpy as np

import config
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime


class _Отпечаток:
    known = True

    def similarity(self, wave, rate, min_seconds=1.5):
        if len(wave) < min_seconds * rate:
            return None
        return 0.31


def _цикл():
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._owner_print = lambda: _Отпечаток()
    loop._print_locked = contextlib.nullcontext
    return loop


class КороткаяФраза(unittest.TestCase):
    def test_короткая_проходит_а_сходство_в_журнале(self):
        loop = _цикл()
        фраза = np.zeros(int(config.SAMPLE_RATE * 1.0), dtype=np.float32)
        self.assertEqual(loop._is_owner(фраза), (True, None))
        оценки = [p for k, p in loop.events if k == "voice_score"]
        self.assertEqual(len(оценки), 1)
        self.assertTrue(оценки[0]["short"])
        self.assertAlmostEqual(оценки[0]["score"], 0.31)

    def test_совсем_короткая_не_считается(self):
        loop = _цикл()
        фраза = np.zeros(int(config.SAMPLE_RATE * 0.4), dtype=np.float32)
        self.assertEqual(loop._is_owner(фраза), (True, None))
        self.assertEqual([k for k, _ in loop.events if k == "voice_score"], [])

    def test_строка_журнала_говорит_что_не_судим(self):
        строки = WebRuntime._log_messages("voice_score",
                                          {"score": 0.31, "seconds": 1.0, "short": True})
        self.assertEqual(строки, ["голос похож на 0.31 (1.0 с речи, коротко — не судим)"])


if __name__ == "__main__":
    unittest.main()
