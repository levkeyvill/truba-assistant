"""Вычитание звука колонок из потока микрофона."""

import threading
from collections import deque

import numpy as np

import config


FRAME = config.SAMPLE_RATE // 100
# Запас петли — полсекунды: микрофон обрабатывается из очереди и может
# отставать, а пара «колонки — микрофон» должна оставаться в одном времени.
# С запасом в несколько кадров отставший микрофон получал бы слишком свежую
# петлю, и вычитать было бы нечего.
BUFFER_FRAMES = 50


class SpeakerEcho:
    """Петля WASAPI и AEC с кадрами по 10 мс."""

    def __init__(self):
        self.active = False
        self.why_off = "не запущен"
        self._aec = None
        self._audio = None
        self._stream = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._far = deque(maxlen=FRAME * BUFFER_FRAMES)
        self._near = np.zeros(0, dtype=np.float32)
        self._source_tail = np.zeros(0, dtype=np.float32)
        self._source_count = 0
        self._next_out = 0.0

    def start(self) -> None:
        """Открывает петлю по умолчанию; при отказе оставляет микрофон прямым."""
        if self.active:
            return
        with self._lock:
            self._far.clear()
        self._near = np.zeros(0, dtype=np.float32)
        self._source_tail = np.zeros(0, dtype=np.float32)
        self._source_count = 0
        self._next_out = 0.0
        try:
            import pyaudiowpatch as pyaudio
            import pywebrtc_audio
        except ImportError as exc:
            self.why_off = f"нет пакета {exc.name or exc}"
            return

        try:
            self._audio = pyaudio.PyAudio()
            try:
                device = self._audio.get_default_wasapi_loopback()
            except OSError as exc:
                self.why_off = f"нет устройства петли колонок: {exc}"
                self.stop()
                return
            if not device:
                self.why_off = "нет устройства петли колонок"
                self.stop()
                return
            rate = int(device["defaultSampleRate"])
            channels = int(device["maxInputChannels"])
            if rate <= 0 or channels <= 0:
                raise ValueError("неверные параметры петли колонок")
            self._aec = pywebrtc_audio.EchoCanceller(
                sample_rate=config.SAMPLE_RATE, num_channels=1,
                stream_delay_ms=80)
            self._stream = self._audio.open(
                format=pyaudio.paInt16, channels=channels, rate=rate,
                input=True, input_device_index=device["index"],
                frames_per_buffer=max(1, rate // 100))
            self._stop.clear()
            self.active = True
            self.why_off = ""
            self._thread = threading.Thread(
                target=self._capture, args=(rate, channels), daemon=True)
            self._thread.start()
        except Exception as exc:
            self.why_off = f"ошибка петли колонок: {exc}"
            self.stop()

    def _resample(self, mono: np.ndarray, rate: int) -> np.ndarray:
        """Приводит очередной кусок петли к частоте микрофона без разрыва."""
        if rate % config.SAMPLE_RATE == 0:
            step = rate // config.SAMPLE_RATE
            samples = np.concatenate((self._source_tail, mono))
            usable = len(samples) // step * step
            self._source_tail = samples[usable:]
            return samples[:usable].reshape(-1, step).mean(axis=1)

        samples = np.concatenate((self._source_tail, mono))
        start = self._source_count - len(self._source_tail)
        end = self._source_count + len(mono) - 1
        positions = np.arange(self._next_out, end + 1e-6,
                              rate / config.SAMPLE_RATE)
        result = np.interp(positions, np.arange(start, end + 1), samples)
        self._next_out += len(positions) * rate / config.SAMPLE_RATE
        self._source_count += len(mono)
        self._source_tail = samples[-1:]
        return result.astype(np.float32)

    def _capture(self, rate: int, channels: int) -> None:
        """Складывает моно петли в ограниченный буфер."""
        try:
            while not self._stop.is_set():
                raw = self._stream.read(max(1, rate // 100),
                                        exception_on_overflow=False)
                samples = np.frombuffer(raw, dtype=np.int16)
                if not len(samples):
                    continue
                mono = samples[:len(samples) // channels * channels].reshape(
                    -1, channels).astype(np.float32).mean(axis=1)
                far = self._resample(mono, rate)
                with self._lock:
                    self._far.extend(far)
        except Exception as exc:
            if not self._stop.is_set():
                self.why_off = f"ошибка петли колонок: {exc}"
                self.active = False

    def clean(self, block: np.ndarray) -> np.ndarray:
        """Передаёт полные кадры AEC, сохраняя остаток до следующего блока."""
        if not self.active:
            return block
        samples = np.concatenate((self._near, np.asarray(block, dtype=np.float32)))
        full = len(samples) // FRAME * FRAME
        self._near = samples[full:]
        cleaned = []
        try:
            for offset in range(0, full, FRAME):
                near = np.clip(samples[offset:offset + FRAME] * 32768,
                               -32768, 32767).astype(np.int16)
                with self._lock:
                    if len(self._far) >= FRAME:
                        far = np.fromiter((self._far.popleft() for _ in range(FRAME)),
                                          dtype=np.float32, count=FRAME)
                    else:
                        self._far.clear()
                        far = np.zeros(FRAME, dtype=np.float32)
                far = np.clip(far, -32768, 32767).astype(np.int16)
                result = self._aec.process(near, far)
                cleaned.append(np.asarray(result, dtype=np.int16).astype(np.float32)
                               / 32768)
        except Exception as exc:
            self.why_off = f"ошибка шумодава: {exc}"
            self.active = False
            return samples
        return np.concatenate(cleaned) if cleaned else np.zeros(0, dtype=np.float32)

    def stop(self) -> None:
        """Останавливает петлю и освобождает устройство."""
        self._stop.set()
        self.active = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        audio, self._audio = self._audio, None
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass
        self._aec = None
