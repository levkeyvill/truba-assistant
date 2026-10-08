r"""Рот: проигрывание синтезированной речи.

Работает очередью в отдельном потоке — пока звучит первое предложение,
следующее уже синтезируется. Иначе между фразами висели бы паузы на
время работы синтезатора.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd


def wasapi_extra(device: int | None):
    """Пересчёт частоты для WASAPI, иначе `None`.

    WASAPI в общем режиме берёт только частоту микшера Windows (обычно
    48 кГц). Higgs и ESpeech говорят на 24 кГц, запись микрофона бывает на
    44,1 кГц — без пересчёта Windows отвечает «Invalid sample rate».
    """
    try:
        info = sd.query_devices(device if device is not None else sd.default.device[1])
        api = sd.query_hostapis(info["hostapi"])["name"]
        if "wasapi" in api.lower() and hasattr(sd, "WasapiSettings"):
            return sd.WasapiSettings(auto_convert=True)
    except Exception:
        pass
    return None


def play_own(wave, sample_rate: int, device: int | None = None) -> None:
    """Проиграть звук СВОИМ потоком и дождаться конца.

    `sd.play`/`sd.rec`/`sd.wait`/`sd.stop` — одни на весь процесс: новый
    вызов из другого потока обрывает предыдущий прямо посреди работы.
    Ими пользуется только `Speaker` ниже; остальное открывает свой
    `OutputStream`, который не прерывает чужие потоки.
    """
    data = np.asarray(wave, dtype=np.float32)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    extra = wasapi_extra(device)
    with sd.OutputStream(device=device, channels=data.shape[1],
                         samplerate=int(sample_rate), dtype="float32",
                         extra_settings=extra) as stream:
        stream.write(data)


class Speaker:
    # Куски здесь играются по одному через sd.play — между ними щель.
    # Поэтому поток по кускам сюда не шлём, только целые предложения.
    gapless = False
    accepts_cancel = True

    def __init__(self, device: int | None = None, gap: float = 0.0):
        self.device = device
        # Пересчёт частоты для WASAPI — один раз на устройство (см. `wasapi_extra`).
        self._extra = wasapi_extra(device)
        # Последняя ошибка проигрывания нужна для честной записи в журнале.
        self.last_error: str = ""
        # Пауза после каждого предложения. Встык речь звучит тараторящей,
        # и в неё невозможно вклиниться, чтобы перебить.
        self.gap = gap
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._play_lock = threading.RLock()
        self._generation = 0
        from core.playback_meter import PlaybackMeter
        self.meter = PlaybackMeter()
        # Взведено, когда всё проиграно и очередь пуста.
        self.idle = threading.Event()
        self.idle.set()

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def say(self, wave: np.ndarray, sample_rate: int, gap: bool = True, cancel=None) -> None:
        """Ставит кусок речи в очередь на проигрывание.

        gap=False — без паузы после: кусок не последний в предложении.
        """
        with self._play_lock:
            if cancel is not None and cancel.is_set():
                return
            self.idle.clear()
            self._queue.put((self._generation, wave, sample_rate, gap))

    def pause(self, seconds: float, sample_rate: int = 24000) -> None:
        """Тишина в очередь — пауза после предложения, присланного кусками."""
        if seconds > 0:
            self.say(np.zeros(int(seconds * sample_rate), dtype=np.float32), sample_rate, gap=False)

    def interrupt(self) -> None:
        """Обрывает речь и чистит очередь — когда Трубу перебили."""
        with self._play_lock:
            self._generation += 1
            self.meter.reset()
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
            sd.stop()
            self.idle.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Ждёт, пока всё проиграется."""
        return self.idle.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        self.interrupt()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                generation, wave, sample_rate, gap = self._queue.get(timeout=0.2)
            except queue.Empty:
                with self._play_lock:
                    if self._queue.empty():
                        self.idle.set()
                continue

            try:
                if gap and self.gap > 0:
                    silence = np.zeros(int(self.gap * sample_rate), dtype=wave.dtype)
                    wave = np.concatenate([wave, silence])
                with self._play_lock:
                    if generation != self._generation:
                        continue
                    self.meter.append(wave, sample_rate, queued=False)
                    sd.play(wave, sample_rate, device=self.device,
                            extra_settings=self._extra)
                sd.wait()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._queue.task_done()

            with self._play_lock:
                if self._queue.empty():
                    self.meter.reset()
                    self.idle.set()
