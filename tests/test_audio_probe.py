"""Проба микрофона мастером: голос снимает и возвращает сервер.

Раньше это делал пульт через `/api/voice/toggle`, и голос оставался выключенным,
если хозяин ушёл со шага или закрыл вкладку. Теперь атомарная операция живёт на
сервере, поэтому проверяем её тут: запись и голос — подмены, ни микрофона, ни
модели, ни сети не нужно.
"""

import threading
import types
import unittest

from ui.web_runtime import WebRuntime


class ПробаЗвукаНаСервереTests(unittest.TestCase):
    def setUp(self):
        self.события = []
        self.ошибкаЗаписи = None
        self.ошибкаСтарта = None
        self.rt = object.__new__(WebRuntime)
        self.rt.voice = types.SimpleNamespace(
            running=True,
            stop=lambda: (self.события.append("stop"), setattr(self.rt.voice, "running", False)),
            start=lambda: self._старт(),
        )
        self.rt._warm_stop = lambda: self.события.append("warm_stop")
        self.rt._warm_start = lambda: self.события.append("warm_start")
        self.rt._audio_record_once = self._записать
        self.rt._audio_probe_lock = threading.Lock()
        self.rt._level_capture_lock = threading.Lock()
        self.rt._audio_stale = False

    def _сбросить_звук(self):
        self.rt._audio_stale = False
        self.события.append("drop_audio")

    def _старт(self):
        if self.ошибкаСтарта is not None:
            raise self.ошибкаСтарта
        self.события.append("start")
        self.rt.voice.running = True

    def _записать(self, голос_остановлен=False):
        # Флаг от пробы: голос она снимает сама, поэтому «занято» здесь
        # означает только живой поток (см. `test_mic_record_listen`).
        self.события.append(("record", голос_остановлен))
        if self.ошибкаЗаписи is not None:
            raise self.ошибкаЗаписи
        return {"ok": True, "seconds": 3.0}

    def test_выключенный_голос_не_трогаем(self):
        self.rt.voice.running = False
        ответ = self.rt.audio_test(True)
        self.assertTrue(ответ["ok"])
        self.assertEqual(self.события, [("record", False)])
        self.assertFalse(self.rt.voice.running)

    def test_включённый_голос_возвращается(self):
        ответ = self.rt.audio_test(True)
        self.assertTrue(ответ["ok"])
        self.assertEqual(self.события,
                         ["stop", "warm_stop", ("record", True), "start", "warm_start"])
        self.assertTrue(self.rt.voice.running)

    def test_запись_сорвалась_голос_всё_равно_поднят(self):
        self.ошибкаЗаписи = RuntimeError("микрофон молчит")
        with self.assertRaises(RuntimeError):
            self.rt.audio_test(True)
        self.assertIn("start", self.события)
        self.assertTrue(self.rt.voice.running)

    def test_голос_не_поднялся_возвращаем_честный_отказ(self):
        self.ошибкаСтарта = RuntimeError("модель не грузится")
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertFalse(ответ.get("voice_restored", False))
        self.assertIn("модель не грузится", ответ["error"])

    def test_ошибки_записи_и_возврата_видны_вместе(self):
        self.ошибкаЗаписи = RuntimeError("устройство пропало")
        self.ошибкаСтарта = RuntimeError("модель не грузится")
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertFalse(ответ["voice_restored"])
        self.assertIn("устройство пропало", ответ["error"])
        self.assertIn("модель не грузится", ответ["error"])

    def test_две_пробы_одновременно_не_идут(self):
        self.rt._audio_probe_lock.acquire()
        try:
            ответ = self.rt.audio_test(True)
        finally:
            self.rt._audio_probe_lock.release()
        self.assertFalse(ответ["ok"])
        self.assertIn("уже идёт", ответ["error"])
        self.assertEqual(self.события, [])
        self.assertTrue(self.rt.voice.running, "чужая проба голос не трогает")

    def test_сменённый_микрофон_не_поднимается_старым(self):
        # Мастер сохранил новый микрофон при работающем голосе: `_audio_stale`
        # поднят, и возвращать надо уже свежий звук, а не старый.
        self.rt._audio_stale = True
        self.rt._drop_audio = self._сбросить_звук
        ответ = self.rt.audio_test(True)
        self.assertTrue(ответ["ok"])
        self.assertEqual(self.события,
                         ["stop", "warm_stop", ("record", True), "drop_audio",
                          "start", "warm_start"])
        self.assertFalse(self.rt._audio_stale, "флаг сброшен")
        self.assertTrue(self.rt.voice.running)

    def test_сбой_остановки_голос_всё_равно_поднят(self):
        # `stop` погасил голос наполовину и упал: восстановление обязано
        # произойти, а проба — честно отклониться.
        def стоп():
            self.события.append("stop")
            self.rt.voice.running = False
            raise RuntimeError("движок не освободил микрофон")
        self.rt.voice.stop = стоп
        self.rt._drop_audio = self._сбросить_звук
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertTrue(ответ["voice_restored"])
        self.assertNotIn("record", self.события, "проба не начиналась")
        self.assertEqual(self.события, ["stop", "start", "warm_start"])
        self.assertTrue(self.rt.voice.running)
        self.assertIn("не остановился", ответ["error"])

    def test_сбой_остановки_и_возврата_просим_включить_сам(self):
        self.ошибкаСтарта = RuntimeError("модель не грузится")

        def стоп():
            self.события.append("stop")
            self.rt.voice.running = False
            raise RuntimeError("движок не освободил микрофон")
        self.rt.voice.stop = стоп
        self.rt._drop_audio = self._сбросить_звук
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertFalse(ответ["voice_restored"])
        self.assertIn("включи его сам", ответ["error"])

    def test_уже_работающий_голос_не_стартует_второй_раз(self):
        def стоп():
            self.события.append("stop")
            # Голос не погас — сбой где-то дальше по цепочке.
            self.rt.voice.running = True
            raise RuntimeError("слой не отпустили")
        self.rt.voice.stop = стоп
        self.rt._drop_audio = self._сбросить_звук
        self.rt._audio_stale = True
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertNotIn("start", self.события, "второй раз не стартуем")
        self.assertNotIn("drop_audio", self.события, "живой голос не сбрасываем")
        self.assertTrue(self.rt._audio_stale, "новый звук применится при настоящем рестарте")
        self.assertTrue(self.rt.voice.running)

    def test_обычная_форма_голос_не_трогает(self):
        # Без параметра проба не включается: кнопка в настройках ведёт себя
        # как раньше и просит выключить голос сама. Настоящий
        # `_audio_record_once` вместо подмены из setUp.
        del self.rt._audio_record_once
        self.rt._voice_busy = lambda: True
        ответ = WebRuntime.audio_test(self.rt)
        self.assertFalse(ответ["ok"])
        self.assertIn("выключи голос", ответ["error"])
        self.assertEqual(self.события, [])
        self.assertTrue(self.rt.voice.running, "форма голос не гасит")


if __name__ == "__main__":
    unittest.main()
