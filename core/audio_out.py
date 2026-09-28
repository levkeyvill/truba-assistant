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


class Speaker:
    # Куски здесь играются по одному через sd.play — между ними щель.
    # Поэтому поток по кускам сюда не шлём, только целые предложения.
    gapless = False

    def __init__(self, device: int | None = None, gap: float = 0.0):
        self.device = device
        # Пауза после каждого предложения. Встык речь звучит тараторящей,
        # и в неё невозможно вклиниться, чтобы перебить.
        self.gap = gap
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        # Взведено, когда всё проиграно и очередь пуста.
        self.idle = threading.Event()
        self.idle.set()

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def say(self, wave: np.ndarray, sample_rate: int, gap: bool = True) -> None:
        """Ставит кусок речи в очередь на проигрывание.

        gap=False — без паузы после: кусок не последний в предложении.
        """
        self.idle.clear()
        self._queue.put((wave, sample_rate, gap))

    def pause(self, seconds: float, sample_rate: int = 24000) -> None:
        """Тишина в очередь — пауза после предложения, присланного кусками."""
        if seconds > 0:
            self.say(np.zeros(int(seconds * sample_rate), dtype=np.float32), sample_rate, gap=False)

    def interrupt(self) -> None:
        """Обрывает речь и чистит очередь — когда Трубу перебили."""
        self._interrupt.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        sd.stop()

    def wait(self, timeout: float | None = None) -> bool:
        """Ждёт, пока всё проиграется."""
        return self.idle.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        self.interrupt()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                wave, sample_rate, gap = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._queue.empty():
                    self.idle.set()
                continue

            if self._interrupt.is_set():
                self._queue.task_done()
                continue

            try:
                if gap and self.gap > 0:
                    silence = np.zeros(int(self.gap * sample_rate), dtype=wave.dtype)
                    wave = np.concatenate([wave, silence])
                sd.play(wave, sample_rate, device=self.device)
                sd.wait()
            except Exception:
                pass
            finally:
                self._queue.task_done()

            if self._queue.empty():
                self._interrupt.clear()
                self.idle.set()
