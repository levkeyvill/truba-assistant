"""WASAPI: речь на 24 кГц требует пересчёта для устройства на 48 кГц.

Без `auto_convert` Windows может ответить «Invalid sample rate».
Устройство и воспроизведение подменены, настоящий звук не запускается.
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


class _Поток:
    """Подменённый поток колонок: запоминает параметры, звука нет."""

    вызовы: list = []

    def __init__(self, **kw):
        _Поток.вызовы.append(kw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data):
        pass

    def abort(self):
        pass


class КолонкиWasapiTests(unittest.TestCase):
    def _сыграть(self, api: str, play=None):
        _Поток.вызовы = []
        вызовы = _Поток.вызовы
        а, б = _устройство(api)
        with а, б, mock.patch.object(audio_out.sd, "OutputStream", play or _Поток):
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
