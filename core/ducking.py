r"""Приглушение чужого звука на время речи Трубы.

Когда играет музыка или видео, ассистента не слышно. Windows умеет
регулировать громкость каждого приложения отдельно — этим и пользуемся:
на время реплики прижимаем всех, кроме себя, потом возвращаем как было.

Громкость возвращается даже при сбое: если этого не сделать, у человека
останется навсегда тихий браузер, и он не поймёт почему.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque


class Ducker:
    """Приглушает остальные приложения, пока Труба говорит."""

    def __init__(self, level: float = 0.25, enabled: bool = True):
        self.level = level
        self.enabled = enabled
        self._saved: dict[int, float] = {}
        self._lock = threading.Lock()
        self._active = False
        self._pid = os.getpid()
        self._available = self._check()

    @staticmethod
    def _check() -> bool:
        try:
            from pycaw.pycaw import AudioUtilities  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _sessions(self):
        from pycaw.pycaw import AudioUtilities

        for session in AudioUtilities.GetAllSessions():
            if session.Process is None:
                continue
            # Себя не трогаем, иначе приглушим собственную речь.
            if session.Process.pid == self._pid:
                continue
            yield session

    def system_peak(self) -> float:
        """Громкость того, что играет на компе прямо сейчас, от 0 до 1.

        Нужна, чтобы понять, стоит ли доверять микрофону: если из колонок
        грохочет видео, распознанная фраза скорее всего оттуда, а не от человека.
        """
        if not self._available:
            return 0.0

        try:
            from pycaw.pycaw import IAudioMeterInformation
        except Exception:
            return 0.0

        peak = 0.0
        for session in self._sessions():
            try:
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                peak = max(peak, float(meter.GetPeakValue()))
            except Exception:
                continue
        return peak

    def duck(self) -> None:
        """Прижимает громкость всех чужих приложений."""
        if not (self.enabled and self._available):
            return

        with self._lock:
            if self._active:
                return
            self._active = True
            self._saved.clear()

            try:
                from pycaw.pycaw import ISimpleAudioVolume

                for session in self._sessions():
                    try:
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        current = volume.GetMasterVolume()
                        # Уже тихие не трогаем — вернём громче, чем было.
                        if current <= self.level:
                            continue
                        self._saved[session.Process.pid] = current
                        volume.SetMasterVolume(self.level, None)
                    except Exception:
                        continue
            except Exception:
                self._active = False

    def restore(self) -> None:
        """Возвращает громкость. Безопасно звать повторно."""
        if not (self.enabled and self._available):
            return

        with self._lock:
            if not self._active:
                return

            try:
                from pycaw.pycaw import ISimpleAudioVolume

                for session in self._sessions():
                    pid = session.Process.pid
                    if pid not in self._saved:
                        continue
                    try:
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume.SetMasterVolume(self._saved[pid], None)
                    except Exception:
                        continue
            except Exception:
                pass
            finally:
                self._saved.clear()
                self._active = False

    def __enter__(self):
        self.duck()
        return self

    def __exit__(self, *exc):
        self.restore()
        return False


class VoiceAppMeter:
    """Кто из голосовых программ звучал из колонок — по времени, а не сейчас.

    `Ducker.system_peak` берёт громкость всех программ и в один момент: к
    концу фразы Discord уже может смолкнуть, и причина пропуска теряется.
    Здесь отдельный поток читает пик у сессий из `config.VOICE_APPS` раз в
    100 мс и помнит последние полминуты, поэтому для любой фразы известно,
    звучала ли программа, пока он говорил.

    Нужен он ровно для разговора по сети: отбор по голосу хозяина там не
    спасает (сходство у друзей и у хозяина одинаковое), а окно разговора на
    три минуты впускает в себя чужую речь.

    Без pycaw или при ошибке COM поток молча не работает, доля всегда 0 —
    лучше лишний раз ответить, чем замереть навсегда.
    """

    def __init__(self, apps=None, interval: float = 0.1, window: float = 30.0):
        if apps is None:
            import config

            apps = config.VOICE_APPS
        self.apps = tuple(str(name).lower() for name in apps)
        self.interval = float(interval)
        self.window = float(window)
        self._buf: deque[tuple[float, float, str]] = deque(maxlen=int(self.window / self.interval) + 4)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._available = self._check()
        self._pid = os.getpid()

    @staticmethod
    def _check() -> bool:
        try:
            from pycaw.pycaw import AudioUtilities  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- Запись -----------------------------------------------------------

    def add(self, when: float, peak: float, name: str = "") -> None:
        """Кладёт замер в буфер.

        Время — `time.perf_counter`, ровно как `heard_at` у фразы в
        voice_loop: смешивать часы нельзя, иначе промежуток выйдет пустым.
        """
        with self._lock:
            self._buf.append((float(when), float(peak), str(name or "")))
            # Время режет буфер надёжнее, чем длина: поток мог простоять.
            edge = float(when) - self.window
            while self._buf and self._buf[0][0] < edge:
                self._buf.popleft()

    def _rows(self) -> list[tuple[float, float, str]]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    # --- Чтение -----------------------------------------------------------

    def share_during(self, start: float, end: float, level: float) -> float:
        """Доля замеров в промежутке, где программа звучала громче level.

        Считается от числа замеров, а не от времени: пока поток жив, они
        идут ровно. Пустой буфер — ноль, как и тишина.
        """
        if end <= start:
            return 0.0
        rows = [row for row in self._rows() if start <= row[0] <= end]
        if not rows:
            return 0.0
        loud = sum(1 for _, peak, _ in rows if peak > level)
        return loud / len(rows)

    def loudest_app(self, start: float, end: float, level: float) -> str:
        """Имя самой громкой программы в промежутке, без .exe. '' — тишина."""
        best = ""
        best_peak = level
        for when, peak, name in self._rows():
            if not (start <= when <= end) or peak <= best_peak:
                continue
            best_peak = peak
            best = name
        if best.lower().endswith(".exe"):
            best = best[:-4]
        return best

    # --- Поток ------------------------------------------------------------

    # Как часто заново искать сессии Discord/Telegram. Перебирать все звуковые
    # сессии Windows с именами процессов 10 раз в секунду стоило ~9 % ядра
    # (замер 28.09); громкость у уже найденных читается дёшево.
    SESSION_REFRESH = 2.0
    _meters: list | None = None
    _meters_at = 0.0

    def _voice_meters(self) -> list:
        """Измерители громкости голосовых программ: [(meter, имя)].

        Список обновляется раз в `SESSION_REFRESH` секунд — новая программа
        (запустил Discord) попадает в него не позже чем через две секунды.
        """
        now = time.monotonic()
        if self._meters is not None and now - self._meters_at < self.SESSION_REFRESH:
            return self._meters
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        found = []
        for session in list(AudioUtilities.GetAllSessions()):
            try:
                if session.Process is None or session.Process.pid == self._pid:
                    continue
                # У psutil.Process имя — метод, а не поле. Первая версия брала
                # поле и получала «<bound method …>»: Discord не совпадал
                # никогда, а тесты на подставных процессах это не ловили.
                name_of = getattr(session.Process, "name", "")
                title = str(name_of() if callable(name_of) else name_of).lower()
                if title not in self.apps:
                    continue
                found.append((session._ctl.QueryInterface(IAudioMeterInformation), title))
            except Exception:
                continue
        self._meters = found
        self._meters_at = now
        return found

    def _session_peak(self) -> tuple[float, str]:
        """Громче всего из голосовых программ и кто это. (0.0, '') — тишина."""
        if not self._available or not self.apps:
            return 0.0, ""

        try:
            meters = self._voice_meters()
        except Exception:
            return 0.0, ""

        peak = 0.0
        name = ""
        for meter, title in meters:
            try:
                value = float(meter.GetPeakValue())
            except Exception:
                # Программу закрыли — сессия умерла: список перечитаем сразу.
                self._meters = None
                continue
            if value > peak:
                peak = value
                name = title
        return peak, name

    def _run(self) -> None:
        # COM в своём потоке надо поднять руками: pycaw через comtypes
        # сам этого не делает, и без CoInitialize первый же GetAllSessions
        # падает, а с ним и весь замер.
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass
        while not self._stop.wait(self.interval):
            try:
                peak, name = self._session_peak()
            except Exception:
                continue
            self.add(time.perf_counter(), peak, name)

    def start(self) -> None:
        if not self._available or self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice-apps")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
