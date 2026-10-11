r"""Следы прежних падений пульта: снимки памяти Windows и режим FTH.

Когда процесс падает, Windows кладёт снимок его памяти в
`%LOCALAPPDATA%\CrashDumps` — если на компьютере есть ключ `LocalDumps`
(его создают, например, программы NVIDIA, и тогда снимки пишутся для
всех программ). Снимок Трубы весит около 800 МБ: в него попадают данные
всех загруженных библиотек, а torch и CUDA огромные. Хранится до десяти
таких — до 8 ГБ.

После нескольких падений подряд Windows вдобавок навсегда включает для
python режим отказоустойчивой кучи (Fault Tolerant Heap, FTH): память
работает медленнее, а новые ошибки с ней прячутся. Сам он не снимается.

Свои следы — снимки процесса, в котором были загружены файлы этой
установки, и FTH у её python. Чужие снимки и программы не трогаются.
Убирает их кнопка на странице «Логи»; снять FTH можно только с правами
администратора — Windows спросит разрешение.
"""

import ctypes
import os
import struct
import sys
import tempfile
from pathlib import Path

import config

LAYERS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
FTH_STATE_KEY = r"SOFTWARE\Microsoft\FTH\State"
FTH = "FaultTolerantHeap"
# Список модулей в снимке — поток 4 (ModuleListStream).
_MODULES = 4
_MODULE_SIZE = 108
# Сколько ждать, пока Windows снимет режим (окно разрешения висит и это время).
ELEVATED_WAIT_MS = 120_000


def dumps_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "CrashDumps"


def dump_modules(путь: Path) -> list:
    """Пути модулей из снимка: читается только заголовок и их список."""
    with open(путь, "rb") as файл:
        заголовок = файл.read(32)
        if заголовок[:4] != b"MDMP":
            return []
        потоков, каталог = struct.unpack_from("<II", заголовок, 8)
        файл.seek(каталог)
        записи = файл.read(потоков * 12)
        for i in range(потоков):
            тип, _размер, rva = struct.unpack_from("<III", записи, i * 12)
            if тип == _MODULES:
                break
        else:
            return []
        файл.seek(rva)
        (сколько,) = struct.unpack("<I", файл.read(4))
        модули = файл.read(сколько * _MODULE_SIZE)
        пути = []
        for i in range(сколько):
            (имя_rva,) = struct.unpack_from("<I", модули, i * _MODULE_SIZE + 20)
            файл.seek(имя_rva)
            (длина,) = struct.unpack("<I", файл.read(4))
            пути.append(файл.read(длина).decode("utf-16-le", "replace"))
        return пути


def _своё(путь: str, корень: str) -> bool:
    return os.path.normcase(os.path.normpath(путь)).startswith(корень)


def own_dumps(корень: Path | None = None) -> list:
    """Снимки упавших python, в которых были файлы этой установки."""
    папка = dumps_dir()
    if not папка.is_dir():
        return []
    корень_н = os.path.normcase(os.path.normpath(str(корень or config.ROOT))) + os.sep
    свои = []
    for снимок in папка.glob("python*.exe.*.dmp"):
        try:
            if any(_своё(м, корень_н) for м in dump_modules(снимок)):
                свои.append(снимок)
        except (OSError, struct.error):
            continue
    return sorted(свои)


def interpreters() -> list:
    """python этой установки: им Windows и вешает режим совместимости.

    Пульт запускается настоящим pythonw, но из окружения `.venv` путь
    настоящего лежит в `sys._base_executable` — смотрим оба.
    """
    папки = []
    for exe in (sys.executable, getattr(sys, "_base_executable", "")):
        if exe and Path(exe).parent not in папки:
            папки.append(Path(exe).parent)
    return [str(папка / имя) for папка in папки for имя in ("pythonw.exe", "python.exe")]


def _значения(корень, ключ: str) -> dict:
    import winreg

    try:
        with winreg.OpenKey(корень, ключ) as к:
            значения = {}
            i = 0
            while True:
                try:
                    имя, данные, _тип = winreg.EnumValue(к, i)
                except OSError:
                    return значения
                значения[имя] = данные
                i += 1
    except OSError:
        return {}


def fth_marks() -> list:
    """Где на python этой установки висит FTH: [(раздел, путь, флаги)]."""
    import winreg

    наши = {os.path.normcase(п): п for п in interpreters()}
    найдено = []
    for раздел, корень in (("HKLM", winreg.HKEY_LOCAL_MACHINE),
                           ("HKCU", winreg.HKEY_CURRENT_USER)):
        for имя, данные in _значения(корень, LAYERS_KEY).items():
            if (os.path.normcase(имя) in наши and isinstance(данные, str)
                    and FTH in данные.split()):
                найдено.append((раздел, имя, данные))
    return найдено


def find() -> dict:
    """Что осталось: число снимков, их вес и пути с FTH."""
    снимки = own_dumps()
    вес = 0
    for снимок in снимки:
        try:
            вес += снимок.stat().st_size
        except OSError:
            pass
    try:
        fth = [путь for _раздел, путь, _флаги in fth_marks()]
    except Exception:
        fth = []
    return {"dumps": len(снимки), "bytes": вес, "fth": fth}


def _без_fth(флаги: str) -> str:
    """Флаги совместимости без FTH; пусто — значение можно удалить целиком."""
    остаток = [ф for ф in флаги.split() if ф != FTH]
    return " ".join(остаток) if any(ф not in ("~", "$") for ф in остаток) else ""


def _reg_строка(текст: str) -> str:
    return '"' + текст.replace("\\", "\\\\").replace('"', '\\"') + '"'


def reg_text(отметки: list) -> str:
    """Файл .reg: снять FTH с этих python в HKLM и забыть их прежние падения."""
    строки = ["Windows Registry Editor Version 5.00", "",
              f"[HKEY_LOCAL_MACHINE\\{LAYERS_KEY}]"]
    for путь, флаги in отметки:
        остаток = _без_fth(флаги)
        строки.append(f"{_reg_строка(путь)}={_reg_строка(остаток) if остаток else '-'}")
    строки += ["", f"[HKEY_LOCAL_MACHINE\\{FTH_STATE_KEY}]"]
    for путь in sorted({путь for путь, _флаги in отметки}):
        строки.append(f"{_reg_строка(путь)}=-")
    строки.append("")
    return "\r\n".join(строки)


def _снять_у_себя(отметки: list) -> None:
    """FTH в разделе пользователя снимается без прав администратора."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LAYERS_KEY, 0,
                        winreg.KEY_SET_VALUE) as ключ:
        for путь, флаги in отметки:
            остаток = _без_fth(флаги)
            if остаток:
                winreg.SetValueEx(ключ, путь, 0, winreg.REG_SZ, остаток)
            else:
                winreg.DeleteValue(ключ, путь)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_ulong),
                ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_uint32), ("hIconOrMonitor", ctypes.c_void_p),
                ("hProcess", ctypes.c_void_p)]


def run_elevated(программа: str, параметры: str) -> bool:
    """Запустить с правами администратора и дождаться. Отказ в окне — False."""
    сведения = _ShellExecuteInfo()
    сведения.cbSize = ctypes.sizeof(сведения)
    сведения.fMask = 0x40 | 0x100  # NOCLOSEPROCESS | NOASYNC
    сведения.lpVerb = "runas"
    сведения.lpFile = программа
    сведения.lpParameters = параметры
    сведения.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(сведения)):
        return False
    if сведения.hProcess:
        ядро = ctypes.windll.kernel32
        ядро.WaitForSingleObject(ctypes.c_void_p(сведения.hProcess), ELEVATED_WAIT_MS)
        ядро.CloseHandle(ctypes.c_void_p(сведения.hProcess))
    return True


def clean() -> dict:
    """Удалить свои снимки и снять FTH. Ответ — что вышло, что осталось."""
    удалено = освобождено = 0
    не_удалось = []
    for снимок in own_dumps():
        try:
            размер = снимок.stat().st_size
            снимок.unlink()
            удалено += 1
            освобождено += размер
        except OSError:
            не_удалось.append(снимок.name)

    fth_снят = None
    отметки = fth_marks()
    if отметки:
        у_себя = [(путь, флаги) for р, путь, флаги in отметки if р == "HKCU"]
        у_системы = [(путь, флаги) for р, путь, флаги in отметки if р == "HKLM"]
        if у_себя:
            try:
                _снять_у_себя(у_себя)
            except OSError:
                pass
        if у_системы:
            # write_bytes, не write_text: текстовый режим превратил бы \r\n в \r\r\n.
            файл = Path(tempfile.gettempdir()) / "truba-fth-reset.reg"
            файл.write_bytes(reg_text(у_системы).encode("utf-16"))
            try:
                run_elevated("reg.exe", f'import "{файл}"')
            finally:
                try:
                    файл.unlink()
                except OSError:
                    pass
        fth_снят = not fth_marks()
    return {"removed": удалено, "freed": освобождено, "failed": не_удалось,
            "fth_cleared": fth_снят, "left": find()}
