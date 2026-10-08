"""Один выполняемый ответ и одна последняя ожидающая реплика."""
from dataclasses import dataclass, field
import threading


@dataclass
class Turn:
    run: object
    cancelled: threading.Event = field(default_factory=threading.Event)


class LatestTurnRunner:
    def __init__(self, interrupt, on_error):
        self._condition = threading.Condition(threading.RLock())
        self._interrupt = interrupt
        self._on_error = on_error
        self._pending = None
        self._active = None
        self._closed = False
        self._worker = threading.Thread(target=self._work, name='truba-answer', daemon=True)
        self._worker.start()

    def submit(self, run):
        with self._condition:
            if self._closed:
                return
            # Отмену нельзя снять стартом следующего хода: у каждого свой Event.
            self.cancel()
            self._interrupt()
            self._pending = Turn(run)
            self._condition.notify()

    def cancel(self):
        with self._condition:
            for turn in (self._active, self._pending):
                if turn is not None:
                    turn.cancelled.set()
            self._pending = None

    def close(self, drain=False):
        with self._condition:
            self._closed = True
            if not drain:
                self.cancel()
                self._interrupt()
            self._condition.notify_all()
        self._worker.join(timeout=2)

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._pending is None:
                    return
                turn, self._pending = self._pending, None
                self._active = turn
            try:
                if not turn.cancelled.is_set():
                    turn.run(turn.cancelled)
            except Exception as exc:
                self._on_error(exc)
            finally:
                with self._condition:
                    if self._active is turn:
                        self._active = None
