r"""Кто эта установка: её отпечаток и запрет второй копии.

Решение хозяина от 01.10: папки установки могут быть сколько угодно и
живут независимо (у каждой своя `data/`), но **работать может только одна
Труба на компьютере**. Пока открыт чужой пульт, запуск из другой папки
объясняет это и ничего не поднимает; повторный запуск своей копии
показывает своё же окно. Поэтому соседних портов больше нет: все копии
берут привычный `config.PHONE_PORT` (8765), а QR, адрес телефона, Origin и
ожидание перезапуска смотрят на него же — новое правило брандмауэра не
нужно.

Два механизма:

- **Отпечаток установки** (`data/install_id.txt`). Пульт отдаёт его на
  `GET /api/install` (только с этого компьютера, `client_is_local`).
  Сверка идёт сравнением строк, а не «сервер ответил — значит мой»: чужой
  пульт на том же порту отвечает так же. Нужен для повторного щелчка по
  своему ярлыку: так пульт узнаёт «это мой собственный процесс» и
  показывает его окно, не заводя второго. Отпечаток — не секрет (пароль
  телефона лежит отдельно, в `phone_key.txt`).
- **Общий именованный mutex Windows** (`Global\Levkeyvill.Truba.Pult`).
  Имя общее для всех папок, поэтому две копии, стартующие одновременно,
  не заведут два пульта: вторая не получит mutex и откажется. Освобождает
  его сама ОС при завершении процесса — постоянного файла или настройки,
  которые остались бы мешать после удаления папки, тут нет. На других
  системах (тесты, Linux) — тот же запрет через блокировку файла во
  временной папке.

Старые версии (0.9.6) mutex не знают, но порт у них тот же: занятый
`config.PHONE_PORT` проверяется отдельно, и вторая копия всё равно не
запустится.

**Формат файла — простой ASCII без BOM.** Читателей несколько: Python
(`read_text('utf-8')`), PowerShell и VBScript (FSO.OpenTextFile без
кодировки читает ANSI). BOM от `Set-Content -Encoding UTF8` в Windows
PowerShell 5.1 ломает именно VBScript, поэтому пишем ASCII, а читаем с
`utf-8-sig`: старый BOM, успевший появиться, не должен ломать запуск.
"""

import hmac
import os
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path

import config

# Отпечаток этой установки. Каждая копия Трубы — отдельная папка со своей
# `data/`, поэтому файл не общий и настройки другой копии не
# перезаписываются. Тесты подменяют путь (tests/test_data_guard.py).
ID_FILE = config.DATA_DIR / "install_id.txt"

ID_MIN = 16
# Имя mutex общее для всех папок: «Global\» делает его видимым и из других
# сессий. Тесты подменяют имя, чтобы не держать настоящий.
MUTEX_NAME = "Global\\Levkeyvill.Truba.Pult"
# CreateMutexW отдаёт этот код, когда mutex уже кто-то держит.
ERROR_ALREADY_EXISTS = 183
# Только этот компьютер. Отпечаток установки не должен уезжать в сеть.
HOST = "127.0.0.1"
# Сколько ждём ответа чужого (или своего) пульта на проверку личности.
TIMEOUT = 1.0
# Свой порт у всех копий один: соседние порты хозяин 01.10 отменил.


def порт() -> int:
    """Порт пульта этой установки. Свой у всех копий, как и прежде."""
    return int(config.PHONE_PORT)


# --- Отпечаток ------------------------------------------------------------


class ОшибкаУстановки(OSError):
    """Папка установки непригодна: файлы не пишутся или не читаются."""


def install_id() -> str:
    """Отпечаток этой установки; нет файла — заводим новый.

    Отпечаток обязан переживать и повторный вызов, и перезапуск пульта, иначе
    каждая проверка «мой ли это пульт?» получала бы новый ответ и две копии
    переставали бы узнавать друг друга. Поэтому ошибка записи — не «пропустить
    и забыть» (тогда отпечаток менялся бы при каждом вызове), а явный отказ с
    причиной: непригодная папка не должна притворяться рабочей.
    """
    ключ = _прочитать(ID_FILE, default="").strip()
    if len(ключ) >= ID_MIN:
        return ключ
    ключ = secrets.token_urlsafe(12)
    _записать(ID_FILE, ключ)
    return ключ


def _прочитать(путь: Path, default: str = "") -> str:
    """Прочитать файл установки. BOM от старой версии установщика — не помеха."""
    try:
        return путь.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return default


def _записать(путь: Path, текст: str) -> None:
    """Записать файл установки простым ASCII без BOM (см. модульный docstring).

    Молчать здесь нельзя: вызывающий код решил бы, что файл записан, и
    установка сошла бы за рабочую при негодном `data/`.
    """
    try:
        путь.parent.mkdir(parents=True, exist_ok=True)
        путь.write_text(текст, encoding="ascii")
    except (OSError, UnicodeEncodeError) as ошибка:
        raise ОшибкаУстановки(
            f"Не получилось записать {путь.name} в папку установки: {ошибка}. "
            f"Проверь, что папка {путь.parent} существует и доступна для записи "
            "(нет ли защиты от записи или антивируса), и запусти пульт снова."
        ) from ошибка


# --- Запрет второй копии ---------------------------------------------------


class ОшибкаЗапрета(OSError):
    """Общий запрет взять не удалось (и не потому, что его кто-то занял)."""


class Замок:
    """Занятый общий mutex. Держится, пока жив объект, отпускается при выходе.

    Хранить надо именно объект: `CloseHandle` на mutex освобождает его, а
    упавший дескриптор (нет GC) оставил бы запрет на весь сеанс Windows.

    `владелец` — True, когда mutex создан с `bInitialOwner=True` и мы его
    держим. Тогда порядок освобождения по документации Microsoft строго
    такой: сначала `ReleaseMutex` (снять владение), потом `CloseHandle`
    (закрыть дескриптор).
    """

    def __init__(self, handle=None, файл=None, владелец: bool = False) -> None:
        self._handle = handle
        self._файл = файл
        self._владелец = bool(владелец)

    def отпустить(self) -> None:
        if self._handle is not None:
            import ctypes

            kernel32 = _kernel32()
            # Дескрипторы 64-битные: без явного c_void_p ctypes усечёт
            # HANDLE до int, и CloseHandle закроет не тот дескриптор.
            if self._владелец:
                kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
            self._handle = None
            self._владелец = False
        if self._файл is not None:
            try:
                self._файл.seek(0)
                if os.name == "nt":
                    _разблокировать_windows(self._файл)
                else:
                    _разблокировать_unix(self._файл)
            except OSError:
                pass
            self._файл.close()
            self._файл = None

    def __enter__(self) -> "Замок":
        return self

    def __exit__(self, *_args) -> None:
        self.отпустить()


def _kernel32():
    """kernel32 с объявленными типами 64-битных дескрипторов.

    `HANDLE` на x64 — 64 бита. Без `argtypes/restype` ctypes считает его
    `c_int` и усекает верхние разряды: `CloseHandle` закрыл бы чужой
    дескриптор, а проверка `if not handle` работала бы с обрезком.
    Объявляем один раз на все вызовы (ctypes просто присвоит атрибуты).
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int,
                                      ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _занять_mutex() -> Замок | None:
    """Взять общий mutex, если он свободен.

    Имя общее у всех папок, поэтому гонка двух новых копий разрешается
    здесь: вторая получает `ERROR_ALREADY_EXISTS` и отказывается, не
    поднимая пульт. Освобождает mutex сама ОС, когда процесс уходит, —
    постоянного следа на диске не остаётся.

    None — mutex держит кто-то (или он был занят и остался занят).
    `ОшибкаЗапрета` — mutex создать не удалось вовсе: это не «вторая копия»,
    и молчать или продолжать без защиты тут нельзя.
    """
    kernel32 = _kernel32()
    handle = kernel32.CreateMutexW(None, 1, MUTEX_NAME)
    if not handle:
        # Это не «кто-то занял» — запрет просто не работает. Продолжать
        # без него значило бы поднять второй пульт рядом с первым.
        raise ОшибкаЗапрета(
            f"Не получилось создать общий запрет на пульт (Windows mutex "
            f"{MUTEX_NAME}, код {kernel32.GetLastError()}). Пульт этой копии "
            "не запущен. Убедись, что текущему пользователю разрешено "
            "создавать объекты синхронизации, и запусти пульт снова."
        )
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return Замок(handle, владелец=True)


def _путь_замка(имя: str) -> Path:
    """Где лежит файл-замок для не-Windows. Тесты подменяют путь."""
    import tempfile

    return Path(tempfile.gettempdir()) / f"truba-pult-{имя}.lock"


def _занять_файл(имя: str) -> Замок | None:
    """Тот же запрет без Windows: блокировка файла во временной папке.

    Нужен тестам и Unix-системам. `msvcrt` тут не импортируется: на
    Windows модуля нет, и «поддержка» была бы выдуманной. Файл лежит в
    `tempfile.gettempdir()` (не в папке установки хозяина) и удаляется
    вместе с папкой, поэтому удаление Трубы ничего не оставляет.
    """
    путь = _путь_замка(имя)
    try:
        файл = open(путь, "a+b")
        if файл.read(1) == b"":
            файл.seek(0)
            файл.write(b"0")
            файл.flush()
        файл.seek(0)
        _заблокировать(файл)
    except OSError:
        try:
            файл.close()
        except Exception:
            pass
        return None
    return Замок(файл=файл)


def _заблокировать(файл) -> None:
    """Захватить файл без ожидания: msvcrt на Windows, fcntl на Unix."""
    if os.name == "nt":
        _заблокировать_windows(файл)
    else:
        _заблокировать_unix(файл)


def _заблокировать_windows(файл) -> None:
    import msvcrt  # есть только на Windows — потому и отдельная функция

    msvcrt.locking(файл.fileno(), msvcrt.LK_NBLCK, 1)


def _разблокировать_windows(файл) -> None:
    import msvcrt

    msvcrt.locking(файл.fileno(), msvcrt.LK_UNLCK, 1)


def _заблокировать_unix(файл) -> None:
    import fcntl  # есть только на Unix — потому и отдельная функция

    fcntl.flock(файл.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _разблокировать_unix(файл) -> None:
    import fcntl

    fcntl.flock(файл.fileno(), fcntl.LOCK_UN)


def занять(имя: str = "global", windows: bool | None = None) -> Замок | None:
    """Занять общий запрет на пульт. None — пульт уже кто-то держит.

    Имя нужно только тестам: у настоящего пульта оно всегда одно на все
    папки. `windows` — тоже для тестов: на этой машине настоящий mutex
    держит живая Труба хозяина, и трогать его в тестах нельзя.
    """
    if os.name == "nt" if windows is None else windows:
        return _занять_mutex()
    return _занять_файл(имя)


# --- Порт ------------------------------------------------------------------


def port_busy(порт: int) -> bool:
    """Кто-то слушает этот порт на этом компьютере."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.3)
    try:
        return probe.connect_ex((HOST, int(порт))) == 0
    finally:
        probe.close()


def our_copy(порт: int) -> bool:
    """На этом порту отвечает пульт именно этой установки.

    Сравнение строк, а не «ответил — значит мой»: на занятом порте может
    стоять другая копия Трубы, и её ответ не должен выдавать себя за наш.
    """
    if not port_busy(порт):
        return False
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{int(порт)}/api/install", timeout=TIMEOUT
        ) as ответ:
            if ответ.status != 200:
                return False
            удалённый = ответ.read(200).decode("utf-8", "replace").strip()
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return bool(удалённый) and hmac.compare_digest(
        удалённый.encode("utf-8"), install_id().encode("utf-8")
    )


def кто_на_порте(номер: int | None = None) -> str:
    """Кто слушает общий порт: «свой», «другая копия», «чужая программа»
    или «свободно».

    Различать эти случаи хозяину нужно: занятый порт чужой копией — это
    «закрой её и повтори запуск», а занятый порт посторонней программой —
    «закрой эту программу». Старая версия (0.9.6) не знает `/api/install`,
    но её `/api/runtime` доступен локально: по нему видно, что на порту
    именно Труба, просто отпечаток неизвестен.

    Аргумент называется `номер`, а не `порт`: иначе он затеняет функцию
    `порт()`, и вызов без аргумента падал бы с TypeError.
    """
    номер = порт() if номер is None else int(номер)
    if not port_busy(номер):
        return "свободно"
    if our_copy(номер):
        return "свой"
    if _это_пульт_трубы(номер):
        return "другая копия"
    return "чужая программа"


def _это_пульт_трубы(порт: int) -> bool:
    """Отвечает ли на порту что-то похожее на пульт Трубы (любой версии)."""
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{int(порт)}/api/runtime", timeout=TIMEOUT
        ) as ответ:
            return ответ.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False
