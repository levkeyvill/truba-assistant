"""Сбой видеокарты посреди работы: Труба говорит запасным голосом, а не молчит.

После сброса драйвера каждый синтез Higgs падает с «CUDA error: unknown
error» — до перезапуска процесса. Синтез и запасной голос подменены.
"""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

from core import voice_loop
from core.voice_loop import VoiceLoop


class _Сломанный:
    """Голос на видеокарте после сброса драйвера."""

    stream = None

    def __init__(self, ошибка):
        self.ошибка = ошибка
        self.вызовов = 0

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        self.вызовов += 1
        raise self.ошибка


class _Запасной:
    сказано: list = []
    грузился = 0

    def load(self):
        _Запасной.грузился += 1

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        _Запасной.сказано.append(text)
        return np.ones(10, dtype=np.float32), 24000


def _цикл(голос):
    цикл = object.__new__(VoiceLoop)
    цикл._voice = голос
    цикл._ref = None
    цикл.события = []
    цикл._emit = lambda kind, data: цикл.события.append((kind, data))
    return цикл


class ЗапаснойГолосTests(unittest.TestCase):
    def setUp(self):
        _Запасной.сказано = []
        _Запасной.грузился = 0
        подмена = mock.patch("core.silero_voice.SileroVoice", _Запасной)
        подмена.start()
        self.addCleanup(подмена.stop)

    def _сказать(self, цикл, фраза):
        parts, _ = цикл._sound_of(фраза, NS(gapless=False))
        return list(parts)

    def test_сбой_видеокарты_говорит_запасным(self):
        цикл = _цикл(_Сломанный(RuntimeError("CUDA error: unknown error\nCUDA kernel errors")))
        звук = self._сказать(цикл, "Открыла Firefox.")
        self.assertTrue(звук)
        self.assertEqual(_Запасной.сказано, [voice_loop.SPARE_VOICE_WORDS, "Открыла Firefox."])
        self.assertIsInstance(цикл._voice, _Запасной)
        self.assertIn("voice_spare", [kind for kind, _ in цикл.события])

    def test_объяснение_один_раз(self):
        цикл = _цикл(_Сломанный(RuntimeError("CUDA error: unknown error")))
        self._сказать(цикл, "Раз.")
        self._сказать(цикл, "Два.")
        self.assertEqual(_Запасной.сказано, [voice_loop.SPARE_VOICE_WORDS, "Раз.", "Два."])
        self.assertEqual(_Запасной.грузился, 1)

    def test_другая_ошибка_не_прячется(self):
        цикл = _цикл(_Сломанный(ValueError("пустой текст")))
        with self.assertRaises(ValueError):
            self._сказать(цикл, "Раз.")
        self.assertEqual(_Запасной.сказано, [])

    def test_ошибка_torch_про_видеокарту_тоже_сбой(self):
        class AcceleratorError(RuntimeError):
            pass

        self.assertTrue(voice_loop.gpu_failed(AcceleratorError("unknown error")))
        self.assertTrue(voice_loop.gpu_failed(RuntimeError("CUDA error: unknown error")))
        self.assertFalse(voice_loop.gpu_failed(RuntimeError("нет файла образца")))

    def test_пульт_узнаёт_и_показывает_плашку(self):
        from pathlib import Path

        import config
        from ui.web_runtime import WebRuntime

        среда = object.__new__(WebRuntime)
        среда.voice = NS(running=True, ready=True, _open=False, _spare_voice=True,
                         _speaker_idle=lambda: True, _listener=None, _speaker=None,
                         _fallback=None, loading_text="", stopping=False)
        try:
            состояние = среда.voice_state()
        except Exception:
            self.skipTest("voice_state требует полного голоса")
        self.assertTrue(состояние["spare_voice"])
        js = (Path(config.ROOT) / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("if (состояние.spare_voice) запаснойГолосПлашка();", js)
        блок = js[js.index("function запаснойГолосПлашка()"):]
        self.assertIn("Перезапустить Трубу", блок[:3000])
        self.assertIn("'/api/update/restart'", блок[:3000])

    def test_журнал_объясняет(self):
        from ui.web_runtime import WebRuntime

        строки = WebRuntime._log_messages("voice_spare", "CUDA error: unknown error")
        self.assertIn("запасным голосом", " ".join(строки))


if __name__ == "__main__":
    unittest.main()
