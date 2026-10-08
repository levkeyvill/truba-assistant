"""Огибающая уже подготовленного звука; микрофон и устройства не открывает."""
from collections import deque
import threading
import time
import numpy as np


class PlaybackMeter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._segments = deque()
        self._end = 0.0

    def append(self, wave, rate, queued=True):
        data = np.asarray(wave, dtype=np.float32)
        if not len(data) or rate <= 0:
            return
        if data.ndim > 1:
            data = data.mean(axis=1)
        hop = max(1, round(rate * .04))
        levels = [float(np.sqrt(np.mean(data[i:i+hop] ** 2))) for i in range(0, len(data), hop)]
        with self._lock:
            now = self._clock()
            start = max(now, self._end) if queued else now
            end = start + len(data) / rate
            self._segments.append((start, end, hop / rate, levels))
            self._end = end

    @property
    def level(self):
        with self._lock:
            now = self._clock()
            while self._segments and self._segments[0][1] <= now:
                self._segments.popleft()
            if not self._segments or now < self._segments[0][0]:
                return 0.0
            start, end, step, levels = self._segments[0]
            return levels[min(len(levels) - 1, int((now - start) / step))]

    def reset(self):
        with self._lock:
            self._segments.clear()
            self._end = 0.0
