r"""Мгновенный повтор NVIDIA: включён ли он и как включить его обратно.

NVIDIA может автоматически выключить повтор при обнаружении защищённого
содержимого и не включить его обратно. Признак — `Auto-Disable` в журнале
`CaptureCore.log`; окно, скрытое от записи экрана, тоже может вызвать это.

Состояние читаем из того же журнала: при каждой смене служба пишет
«DVR Active = true/false». Включаем сочетанием DVRToggle (Alt+Shift+F10).
Это переключатель, поэтому
жать можно только когда точно известно, что выключено: иначе выключим.

Выключенное руками не трогаем: сторож включает обратно, только если
последнее выключение автоматическое — перед ним в журнале «Auto-Disable».
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config

LOG = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    / "NVIDIA Corporation" / "ShadowPlay" / "CaptureCore.log"
)
# Когда журнал разрастается, NVIDIA переименовывает его в .old.
OLD = LOG.with_suffix(".old")

STATE = re.compile(rb"DVR Active = (true|false)")
AUTO_OFF = re.compile(rb"Auto-Disable IR Session|IsDVRDisableRequired YES")
BLOCKER = re.compile(rb"Protected Content is running by PID: (\d+) App:([^\r\n]*)")
# Защищённое содержимое, из-за которого выключили, видно за несколько
# строк до «DVR Active = false» — хватает небольшого окна.
LOOKBACK = 12_000

# Журнал NVIDIA регулярно сменяется; строки о состоянии могут уйти
# вместе со старым файлом. Последнее известное состояние
# держим у себя: пока повтор не менялся, новых строк о нём и не будет.
MEMO = config.DATA_DIR / "replay_state.json"

# Заводское сочетание NVIDIA «включить/выключить повтор».
DEFAULT_TOGGLE = [0x12, 0x10, 0x79]  # Alt+Shift+F10
# Процесс Codex, который прячет окно от записи, — когда журнал не назвал
# номер процесса. Номер известен — ждём только его (см. `blocker_running`).
BLOCKER_NAMES = ("codex-computer-use",)


@dataclass
class Status:
    on: bool | None  # None — журнала нет или в нём ни слова о повторе
    auto_off: bool = False  # автоматическое выключение NVIDIA
    blocker_pid: int | None = None
    blocker_app: str = ""

    @property
    def text(self) -> str:
        if self.on is None:
            return "не знаю — журнал NVIDIA не читается"
        if self.on:
            return "включён"
        if self.auto_off:
            # После заголовка ответа не добавляем второе двоеточие.
            return f"выключен из-за {self.blocker_app or 'защищённого экрана'}"
        return "выключен вручную"


def _app_name(raw: bytes) -> str:
    path = raw.decode("utf-8", errors="replace").strip()
    if "\\Codex\\" in path or "cua_node" in path:
        return "Codex (управление компьютером)"
    return Path(path.replace("\\", "/")).name or path[-60:]


def _read(path: Path) -> bytes:
    try:
        with path.open("rb") as file:
            return file.read()
    except OSError:
        return b""


def _recall(memo: Path | None) -> Status | None:
    if memo is None:
        return None
    try:
        data = json.loads(memo.read_text(encoding="utf-8"))
        return Status(on=bool(data["on"]), auto_off=bool(data.get("auto_off")),
                      blocker_pid=data.get("blocker_pid"), blocker_app=data.get("blocker_app", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _keep(found: Status, memo: Path | None) -> Status:
    if memo is not None and _recall(memo) != found:
        try:
            memo.parent.mkdir(parents=True, exist_ok=True)
            memo.write_text(json.dumps({
                "on": found.on, "auto_off": found.auto_off,
                "blocker_pid": found.blocker_pid, "blocker_app": found.blocker_app,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return found


def status(log: Path = LOG, old: Path = OLD, memo: Path | None = MEMO) -> Status:
    """Текущее состояние по последней записи «DVR Active» в журнале."""
    for path in (log, old):
        data = _read(path)
        found = None
        for found in STATE.finditer(data):
            pass
        if found is None:
            continue
        on = found.group(1) == b"true"
        if on:
            return _keep(Status(on=True), memo)
        before = data[max(0, found.start() - LOOKBACK): found.start()]
        auto = bool(AUTO_OFF.search(before))
        pid, app = None, ""
        blocker = None
        for blocker in BLOCKER.finditer(before):
            pass
        if blocker is not None:
            pid, app = int(blocker.group(1)), _app_name(blocker.group(2))
        return _keep(Status(on=False, auto_off=auto, blocker_pid=pid, blocker_app=app), memo)
    return _recall(memo) or Status(on=None)


def blocker_running(pid_hint: int | None = None) -> bool:
    """Жив ли ещё тот, кто прячет окно от записи.

    Если журнал назвал процесс — ждём только его. Иначе фоновый процесс,
    который живёт дольше блокировавшего, мог бы задержать восстановление.
    При повторном отключении сторож применит `Guard.BACKOFF`.
    """
    import psutil

    if pid_hint:
        if not psutil.pid_exists(pid_hint):
            return False
        try:
            exe = psutil.Process(pid_hint).exe() or ""
        except (psutil.Error, OSError):
            return False
        # Номер процесса Windows отдаёт заново — чужой с тем же номером не в счёт.
        return "cua_node" in exe or "Codex" in exe
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if any(b in name for b in BLOCKER_NAMES):
            return True
    return False


def turn_on(wait: float = 8.0) -> bool:
    """Включает повтор и проверяет по журналу, что включился.

    Жмёт переключатель, только если журнал говорит «выключен»: вслепую
    это же сочетание повтор выключает.
    """
    from core import hotkeys

    now = status()
    if now.on is not False:
        return now.on is True
    hotkeys.press(hotkeys.combo("DVRToggle") or list(DEFAULT_TOGGLE))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if status().on:
            return True
    return False


class ReplayOff(RuntimeError):
    """Повтор выключен — сохранить момент нечем."""


def require_on() -> None:
    """Для кнопки «момент»: молча нажать сочетание при выключенном повторе
    значит сделать вид, что сохранили. Неизвестно — не мешаем."""
    if status().on is False:
        raise ReplayOff("мгновенный повтор был выключен — момент не сохранился")


Notify = Callable[[str, dict], None]


class Guard:
    """Сторож: раз в PERIOD секунд смотрит журнал и возвращает повтор.

    Живёт в пульте: повтор нужен и при выключенном голосе.
    См. coordination/ГРАБЛИ.md, раздел про обработчики в пульте.
    """

    PERIOD = 20.0
    # Не вышло включить — пробуем реже, чтобы не жать клавиши без конца.
    BACKOFF = (60.0, 300.0, 900.0, 1800.0)

    def __init__(self, notify: Notify):
        self._notify = notify
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._next_try = 0.0
        self._waiting_for: str | None = None
        self.last = Status(on=None)

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="replay-guard")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.PERIOD):
            try:
                self.check()
            except Exception as exc:  # сторож не должен падать насовсем
                self._notify("replay_failed", {"error": f"{type(exc).__name__}: {exc}"})

    def check(self) -> Status:
        now = status()
        self.last = now
        if now.on is not False or not getattr(config, "REPLAY_GUARD", True):
            self._waiting_for = None
            if now.on:
                self._failures = 0
            return now
        if not now.auto_off:
            return now  # ручное выключение не отменяем
        if blocker_running(now.blocker_pid):
            if self._waiting_for != now.blocker_app:
                self._waiting_for = now.blocker_app
                self._notify("replay_waiting", {"app": now.blocker_app})
            return now
        if time.monotonic() < self._next_try:
            return now
        self._waiting_for = None
        if turn_on():
            self._failures = 0
            self._next_try = 0.0
            self.last = status()
            self._notify("replay_restored", {"app": now.blocker_app})
        else:
            delay = self.BACKOFF[min(self._failures, len(self.BACKOFF) - 1)]
            self._failures += 1
            self._next_try = time.monotonic() + delay
            self._notify("replay_failed", {"error": "NVIDIA не включила повтор",
                                           "retry": int(delay)})
        return self.last
