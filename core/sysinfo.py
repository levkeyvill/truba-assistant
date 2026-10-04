r"""Нагрузка компьютера для экрана телефона.

Телефон стоит рядом с монитором и постоянно в поле зрения — на нём удобно
держать загрузку железа, не занимая места на основном экране и не мешая игре
оверлеем.

Все замеры дешёвые: опрос раз в пару секунд не отнимает ничего заметного.
"""

from __future__ import annotations

import threading
import time

_nvml_ready = False
_nvml_handle = None
_lock = threading.Lock()


def _init_nvml():
    """Готовит доступ к видеокарте. Без неё остальное всё равно работает."""
    global _nvml_ready, _nvml_handle
    if _nvml_ready:
        return _nvml_handle

    try:
        import pynvml

        pynvml.nvmlInit()
        _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        _nvml_handle = None
    _nvml_ready = True
    return _nvml_handle


def _gpu() -> dict:
    handle = _init_nvml()
    if handle is None:
        return {}

    try:
        import pynvml

        used = pynvml.nvmlDeviceGetMemoryInfo(handle)
        rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
        data = {
            "gpu_load": int(rates.gpu),
            "gpu_mem_used": round(used.used / 1024**3, 1),
            "gpu_mem_total": round(used.total / 1024**3, 1),
        }
        try:
            data["gpu_temp"] = int(
                pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )
        except Exception:
            pass
        return data
    except Exception:
        return {}


def _cpu_temp() -> int | None:
    """Температура процессора. Windows её отдаёт не всегда — это нормально."""
    try:
        import psutil

        readings = psutil.sensors_temperatures()
    except Exception:
        return None

    for key in ("coretemp", "k10temp", "acpitz"):
        entries = readings.get(key) if readings else None
        if entries:
            return int(entries[0].current)
    return None


def snapshot() -> dict:
    """Текущее состояние железа одним словарём."""
    import psutil

    with _lock:
        # interval=None берёт нагрузку с прошлого вызова — не блокирует поток.
        cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()

        data = {
            "cpu": int(cpu),
            "ram": int(memory.percent),
            "ram_used": round(memory.used / 1024**3, 1),
            "ram_total": round(memory.total / 1024**3, 1),
            "time": time.strftime("%H:%M"),
        }

        temp = _cpu_temp()
        if temp is not None:
            data["cpu_temp"] = temp

        data.update(_gpu())
        return data


class Monitor:
    """Периодически шлёт состояние железа на телефон.

    Раз в 5 секунд, а не в 2: каждое сообщение будит Wi-Fi телефона и
    перерисовывает полоски, расходуя заряд телефона. Полоски нагрузки
    при этом остаются живыми.
    """

    def __init__(self, server, period: float = 5.0):
        self.server = server
        self.period = period
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Первый вызов psutil всегда возвращает ноль — прогреваем.
        snapshot()
        while not self._stop.wait(self.period):
            if not self.server.connected:
                continue
            try:
                self.server.send_system(snapshot())
            except Exception:
                continue
