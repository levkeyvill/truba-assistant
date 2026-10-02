"""Колонки через WASAPI: голос на 24 кГц не должен молчать (02.10, test11).

Хозяин выбрал внешнюю звуковую карту «Динамики (…)», а пульт нашёл её через
WASAPI (48 кГц). Higgs говорит на 24 кГц, `sd.play` без пересчёта частоты
падал с «Invalid sample rate», ошибка глоталась молча — и в журнале стояло
«ответил голосом», а в колонках была тишина. Silero на 48 кГц звучал, поэтому
раньше этого не видели. Проверено на живом устройстве тишиной: без пересчёта
— ошибка, с `auto_convert` — играет.
"""

import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core import audio_out

ROOT = Path(config.ROOT)


def _устройство(api: str):
    """Подмена устройства: «Динамики» нужного вида, без настоящего звука."""
    return (mock.patch.object(audio_out.sd, "query_devices",
                              return_value={"hostapi": 7, "name": "Динамики"}),
            mock.patch.object(audio_out.sd, "query_hostapis",
                              return_value={"name": api}))


class КолонкиWasapiTests(unittest.TestCase):
    def _сыграть(self, api: str, play=None):
        вызовы = []
        play = play or (lambda *a, **kw: вызовы.append(kw))
        а, б = _устройство(api)
        with а, б, mock.patch.object(audio_out.sd, "play", play), \
                mock.patch.object(audio_out.sd, "wait", lambda: None):
            колонки = audio_out.Speaker(device=27)
            try:
                колонки.say(np.zeros(240, dtype=np.float32), 24000, gap=False)
                self.assertTrue(колонки.wait(5), "очередь не доиграла")
                time.sleep(0.05)
            finally:
                колонки.close()
        return колонки, вызовы

    def test_wasapi_играет_с_пересчётом_частоты(self):
        _, вызовы = self._сыграть("Windows WASAPI")
        self.assertEqual(len(вызовы), 1)
        self.assertIsNotNone(вызовы[0].get("extra_settings"),
                             "без пересчёта WASAPI не берёт 24 кГц")

    def test_mme_без_лишних_настроек(self):
        _, вызовы = self._сыграть("MME")
        self.assertIsNone(вызовы[0].get("extra_settings"))

    def test_ошибка_колонок_не_глотается(self):
        def отказ(*a, **kw):
            raise RuntimeError("Invalid sample rate")

        колонки, _ = self._сыграть("Windows WASAPI", play=отказ)
        self.assertIn("Invalid sample rate", колонки.last_error)

    def test_голосовой_цикл_пишет_про_тихие_колонки(self):
        код = (ROOT / "core" / "voice_loop.py").read_text(encoding="utf-8")
        self.assertIn('getattr(speaker, "last_error", "")', код)
        self.assertIn("колонки не сыграли", код)

    def test_фон_поиска_тоже_с_пересчётом(self):
        код = (ROOT / "core" / "search_hum.py").read_text(encoding="utf-8")
        self.assertIn("extra_settings=wasapi_extra(", код)


if __name__ == "__main__":
    unittest.main()
