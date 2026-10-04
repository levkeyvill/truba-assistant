r"""Состояние кнопок-переключателей в подменю программы.

Узнать, включён ли микрофон в Discord, нельзя: его RPC закрыт, и телефон
ничего не спрашивает у самой программы. Поэтому состояние — наш собственный
счёт нажатий, с честными поправками:

* **счёт ведётся в памяти процесса**, на диск ничего не пишется: переживать
  перезапуск пульта нечего, хозяин после него и так видит всё включённым;
* **перезапуск программы сбрасывает состояние**. В момент переключения
  запоминается время старта её процесса (`psutil`, имя — как при закрытии,
  `launcher.process_name`). Если при следующем чтении процесса нет или время
  старта другое — программа перезапустилась, и всё у неё снова включено;
* **`implies`** — честная поправка «на глаз». У Discord выключенный звук
  выключает и микрофон, но состояние микрофона от этого не меняется: поле
  означает «пока этот пункт включён, связанный показывается включённым», и
  больше ничего. Настоящий `implies` знает только сама программа.

Состояние одно на оба направления: телефон рисует его, а длинное нажатие
может его поправить — на случай, когда хозяин переключил мышкой в самом
Discord и подсветка разошлась.
"""

import threading

# (программа, ключ пункта) → «выключено» (для микрофона true = заглушен).
_state: dict[tuple[str, str], bool] = {}
# Программа → время старта её процесса в момент последнего переключения.
_starts: dict[str, float | None] = {}

# Переключение идёт из фонового потока пульта, а чтение — из потока сервера,
# поэтому замок нужен. Именно RLock, а не Lock: `flip` держит его и зовёт
# `_check_restart`, а та — `forget`; на обычном Lock здесь пульт зависает
# насмерть. Обход состоит из двух-трёх вложенных вызовов — это норма.
_lock = threading.RLock()


def forget(app_id: str | None = None) -> None:
    """Сбрасывает состояние: одну программу или всё (так чистят тесты)."""
    with _lock:
        if app_id is None:
            _state.clear()
            _starts.clear()
            return
        for key in [k for k in _state if k[0] == app_id]:
            del _state[key]
        _starts.pop(app_id, None)


def own(app_id: str, key: str) -> bool:
    """Своё состояние пункта, без поправок на `implies`."""
    with _lock:
        return bool(_state.get((app_id, key), False))


def _process_start(item) -> float | None:
    """Когда начался процесс программы. None — её сейчас нет.

    Имя процесса берётся то же, что и при закрытии: из `apps.json`, и больше
    ниоткуда — иначе сброс состояния зависел бы от того, как программа
    названа в системе, а не от того, как она описана у хозяина.
    """
    from core import launcher

    name = launcher.process_name(item or {})
    if not name:
        return None
    try:
        started = [proc.create_time() for proc in launcher._running(name)]
    except Exception:
        # Процессы мы не обязаны видеть: нет psutil, нет прав — состояние
        # просто останется таким, каким было.
        return None
    return min(started) if started else None


def _check_restart(app_id: str, item) -> None:
    """Программа перезапустилась — всё у неё снова включено."""
    with _lock:
        if app_id not in _starts:
            return
        remembered = _starts[app_id]
    current = _process_start(item)
    if current is not None and current == remembered:
        return
    forget(app_id)


def flip(app_id: str, item, key: str) -> bool:
    """Переворачивает состояние пункта. Возвращает новое."""
    with _lock:
        _check_restart(app_id, item)
        if app_id not in _starts:
            _starts[app_id] = _process_start(item)
        new = not _state.get((app_id, key), False)
        _state[(app_id, key)] = new
        return new


def states(app_id: str, item, entries: list[dict]) -> dict[str, bool]:
    """Что показать телефону: `{ключ: включено}` по переключателям меню.

    Пустой словарь — у программы нет переключателей, и телефону нечего
    обновлять. `on` означает «выключено»: у микрофона и звука это то, что
    хозяин и хочет видеть.
    """
    if not any(entry.get("toggle") for entry in entries):
        return {}
    _check_restart(app_id, item)

    with _lock:
        own_states = {
            f"{entry['kind']}:{entry['id']}": bool(
                _state.get((app_id, f"{entry['kind']}:{entry['id']}"), False)
            )
            for entry in entries
            if entry.get("toggle")
        }
    shown = dict(own_states)

    # `implies` смотрим только на свои состояния: показ связанного пункта не
    # должен превращаться в его собственное состояние и тянуть за собой
    # третий пункт.
    by_id = {entry.get("id"): entry for entry in entries}
    for entry in entries:
        if not entry.get("toggle"):
            continue
        if not own_states.get(f"{entry['kind']}:{entry['id']}"):
            continue
        other = by_id.get(entry.get("implies") or "")
        if not other or not other.get("toggle"):
            continue
        shown[f"{other['kind']}:{other['id']}"] = True
    return shown
