"""Шумодав колонок: кадры, запасной путь и настройка без живого звука."""

import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core import settings
from core.audio_in import Listener, VAD_HOP
from core.speaker_echo import BUFFER_FRAMES, FRAME, SpeakerEcho
from ui.web_runtime import WebRuntime


class _Canceller:
    calls = []

    def __init__(self, **kwargs):
        self.options = kwargs

    def process(self, near, far):
        self.calls.append((near.copy(), far.copy()))
        return near


class _Audio:
    device = {"index": 3, "defaultSampleRate": 48000, "maxInputChannels": 2}

    def get_default_wasapi_loopback(self):
        return self.device

    def open(self, **kwargs):
        return _Stream()

    def terminate(self):
        pass


class _Stream:
    """Петля как у PortAudio: закрыть её посреди `read` — падение процесса.

    Здесь вместо падения такой случай записывается в `violations`.
    `silent` — колонки молчат: данных нет, и `read` ждал бы вечно.
    """

    def __init__(self, silent=False):
        self.stop = threading.Event()
        self.silent = silent
        self.reading = 0
        self.closed = 0
        self.violations = []

    def get_read_available(self):
        return 0 if self.silent else 10_000

    def read(self, count, exception_on_overflow=False):
        if self.silent:
            self.violations.append("read в тишине ждал бы вечно")
        self.reading += 1
        try:
            self.stop.wait(0.05)
            return np.zeros(count * 2, dtype=np.int16).tobytes()
        finally:
            self.reading -= 1

    def stop_stream(self):
        if self.reading:
            self.violations.append("stop_stream посреди read")
        self.stop.set()

    def close(self):
        if self.reading:
            self.violations.append("close посреди read")
        self.closed += 1


def _packages(device=None):
    class Audio(_Audio):
        pass

    if device is not None:
        Audio.device = device
    return mock.patch.dict(sys.modules, {
        "pyaudiowpatch": types.SimpleNamespace(PyAudio=Audio, paInt16=8),
        "pywebrtc_audio": types.SimpleNamespace(EchoCanceller=_Canceller),
    })


class SpeakerEchoTests(unittest.TestCase):
    def setUp(self):
        _Canceller.calls = []

    def test_frames_remainder_and_far_silence(self):
        echo = SpeakerEcho()
        echo.active = True
        echo._aec = _Canceller(sample_rate=16000, num_channels=1,
                               stream_delay_ms=80)
        echo._far.extend(np.full(FRAME, 1234, dtype=np.int16))
        first = echo.clean(np.full(200, 0.25, dtype=np.float32))
        second = echo.clean(np.full(120, 0.5, dtype=np.float32))
        self.assertEqual((len(first), len(second)), (160, 160))
        self.assertEqual(len(_Canceller.calls), 2)
        np.testing.assert_array_equal(_Canceller.calls[0][1], 1234)
        np.testing.assert_array_equal(_Canceller.calls[1][1], 0)
        np.testing.assert_array_equal(_Canceller.calls[1][0][:40], 8192)
        np.testing.assert_array_equal(_Canceller.calls[1][0][40:], 16384)

    def _started(self, silent=False):
        потоки = []

        class Audio(_Audio):
            def open(self, **kwargs):
                поток = _Stream(silent=silent)
                потоки.append(поток)
                return поток

        packages = mock.patch.dict(sys.modules, {
            "pyaudiowpatch": types.SimpleNamespace(PyAudio=Audio, paInt16=8),
            "pywebrtc_audio": types.SimpleNamespace(EchoCanceller=_Canceller),
        })
        packages.start()
        self.addCleanup(packages.stop)
        echo = SpeakerEcho()
        echo.start()
        self.assertTrue(echo.active, echo.why_off)
        return echo, потоки[0]

    def test_stop_from_two_threads_never_closes_mid_read(self):
        # «Выключить» останавливает слушатель из двух потоков сразу, а поток
        # петли в этот миг читает: закрытие посреди чтения роняло процесс.
        echo, поток = self._started()
        time.sleep(0.2)
        второй = threading.Thread(target=echo.stop)
        второй.start()
        echo.stop()
        второй.join(3)
        time.sleep(0.1)
        self.assertEqual(поток.violations, [])
        self.assertEqual(поток.closed, 1)

    def test_stop_in_silence_is_quick(self):
        # Колонки молчат: данных нет, и ждать в `read` нельзя — остановка
        # иначе висела бы, а закрытие из другого потока роняло процесс.
        echo, поток = self._started(silent=True)
        time.sleep(0.2)
        начало = time.monotonic()
        echo.stop()
        self.assertLess(time.monotonic() - начало, 1.0)
        self.assertEqual(поток.violations, [])
        self.assertEqual(поток.closed, 1)

    def test_start_uses_default_loopback(self):
        with _packages():
            echo = SpeakerEcho()
            echo.start()
            self.assertTrue(echo.active, echo.why_off)
            self.assertEqual(echo._aec.options,
                             {"sample_rate": 16000, "num_channels": 1,
                              "stream_delay_ms": 80})
            echo.stop()

    def test_ring_discards_old_playback(self):
        echo = SpeakerEcho()
        echo._far.extend(range(FRAME * (BUFFER_FRAMES + 2)))
        self.assertEqual(len(echo._far), FRAME * BUFFER_FRAMES)
        self.assertEqual(echo._far[0], FRAME * 2)

    def test_loopback_is_decimated_to_mono_16k(self):
        echo = SpeakerEcho()
        mono = np.tile(np.array([300, 600, 900], dtype=np.float32), FRAME)
        far = echo._resample(mono, 48000)
        self.assertEqual(len(far), FRAME)
        np.testing.assert_array_equal(far, 600)

    def test_missing_package_or_device_keeps_direct_microphone(self):
        with mock.patch.dict(sys.modules, {"pyaudiowpatch": None}):
            echo = SpeakerEcho()
            echo.start()
            self.assertFalse(echo.active)
            self.assertIn("нет пакета", echo.why_off)
        with _packages(device={}):
            echo = SpeakerEcho()
            echo.start()
            self.assertFalse(echo.active)
            self.assertIn("нет устройства", echo.why_off)


class _Queue(queue.Queue):
    def __init__(self, stop):
        super().__init__()
        self.stop = stop

    def get(self, *args, **kwargs):
        if self.empty():
            self.stop.set()
            raise queue.Empty
        return super().get(*args, **kwargs)


class _Vad:
    def __init__(self):
        self.chunks = []

    def probability(self, chunk):
        self.chunks.append(chunk.copy())
        return 0.0


class ListenerTests(unittest.TestCase):
    def _listener(self, echo):
        listener = object.__new__(Listener)
        listener._stop = threading.Event()
        listener._queue = _Queue(listener._stop)
        listener._queue.put(np.ones(VAD_HOP, dtype=np.float32))
        listener._vad = _Vad()
        listener._barge_threshold = 0
        listener.speaker_echo = echo
        return listener

    def test_active_echo_reaches_vad_and_level(self):
        echo = mock.Mock(active=True)
        echo.clean.return_value = np.zeros(VAD_HOP, dtype=np.float32)
        listener = self._listener(echo)
        list(listener.phrases())
        echo.clean.assert_called_once()
        np.testing.assert_array_equal(listener._vad.chunks[0], 0)
        self.assertEqual(listener.last_level, 0.0)

    def test_inactive_echo_is_not_called(self):
        echo = mock.Mock(active=False)
        listener = self._listener(echo)
        list(listener.phrases())
        echo.clean.assert_not_called()
        np.testing.assert_array_equal(listener._vad.chunks[0], 1)


class GuardAndSettingsTests(unittest.TestCase):
    def test_journal_has_one_status_line(self):
        self.assertEqual(WebRuntime._log_messages("speaker_aec", {"active": True}),
                         ["шумоподавление включено"])
        self.assertEqual(WebRuntime._log_messages(
            "speaker_aec", {"active": False, "reason": "нет пакета"}),
            ["шумоподавление выключено: нет пакета"])

    def test_discord_guard_depends_on_active_echo(self):
        from test_voice_guard import _discord, _loop, _phrase

        with mock.patch.object(config, "VOICE_APP_GUARD", True), \
                mock.patch.object(config, "LISTEN_MODE", "always"):
            loop = _loop(_discord(0.8))
            loop._listener.speaker_echo = types.SimpleNamespace(active=True)
            self.assertTrue(loop._should_answer("обычная фраза", _phrase()))
            loop._listener.speaker_echo.active = False
            self.assertFalse(loop._should_answer("обычная фраза", _phrase()))
            self.assertEqual(loop.events[-1][1]["why"], "говорили в Discord")

    def test_setting_persists_and_reaches_config(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(settings, "SETTINGS_PATH", Path(folder) / "settings.json"), \
                mock.patch.object(settings, "ENV_PATH", Path(folder) / ".env"), \
                mock.patch.object(config, "SPEAKER_AEC", True):
            runtime = object.__new__(WebRuntime)
            runtime.settings_snapshot = lambda: {}
            result = runtime.save_settings({"speaker_aec": False})
            self.assertTrue(result["ok"], result)
            self.assertFalse(settings.load_settings()["speaker_aec"])
            self.assertFalse(config.SPEAKER_AEC)


if __name__ == "__main__":
    unittest.main()
