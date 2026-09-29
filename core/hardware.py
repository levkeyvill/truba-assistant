"""Железо компьютера и совет, что ему по силам.

Смысл модуля один: мастер первого запуска (часть 2) и раздел «Железо»
должны говорить человеку не «ставь torch с CUDA», а «RTX 5070 Ti, 16 ГБ —
потянет качественный голос Higgs». Поэтому здесь два слоя:

  - `detect()` — что есть: винда, процессор, память, место, видеокарты, и
    стоят ли в `.venv` библиотеки качественных голосов;
  - `recommend()` — что из этого следует словами и значениями.

Правила, которые здесь нельзя нарушать:

  - **Никаких исключений наружу.** Мастер спрашивает про любой компьютер,
    и «не определилось» — это `None` в поле, а не `Traceback`.
  - **torch не импортируется.** Он весит под гигабайт и с CUDA грузится
    секунд двадцать; версия читается из метаданных пакета.
  - **Сети нет.** Всё, что о видеокарте, спрашивается у `nvidia-smi`
    локально, с таймаутом и без окна консоли.
"""

import importlib.metadata
import importlib.util
import platform
import shutil
import subprocess
from datetime import datetime

import config

# Сколько ждём `nvidia-smi`. Он запускается раз в минуту, и зависший драйвер
# не должен подвешивать ни мастер, ни вкладку пульта.
SMI_TIMEOUT = 5.0
# Что спрашиваем у видеокарты: имя, мегабайты видеопамяти, драйвер, «вычисления».
SMI_FIELDS = "name,memory.total,driver_version,compute_cap"
SMI_ARGS = ["nvidia-smi", f"--query-gpu={SMI_FIELDS}",
            "--format=csv,noheader,nounits"]

# С какой сборкой torch работает драйвер. Ниже — пороги NVIDIA: начиная с
# этих версий драйвер тянет соответствующую ветку CUDA. Драйвер старее
# последней из них качественные голоса не запустит вовсе.
CUDA_BRANCHES = (
    (580, "cu130"),
    (570, "cu128"),
    (560, "cu126"),
)


def _окно_нет() -> int:
    """Флаг «не показывать окно консоли». На другой системе — ноль."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _версия_драйвера(номер: str) -> float:
    """`616.56` → 616.56. Мусор — 0.0, и совет будет «драйвер не распознан»."""
    текст = str(номер or "").strip().rstrip(".")
    if not текст:
        return 0.0
    # В строке бывает хвост вида `531.14 (Family)` — берём только начало.
    кусок = текст.split("(")[0].strip()
    try:
        return float(кусок)
    except ValueError:
        return 0.0


def cuda_index(driver: str):
    """Какая сборка torch подходит драйверу. None — драйвер слишком старый.

    Проверка идёт по числам: `579.99` — это ещё 570-я ветка, а `580.0` — уже
    580-я, и на границе легко ошибиться, если сравнивать как строки.
    """
    version = _версия_драйвера(driver)
    if version <= 0:
        return None
    for порог, индекс in CUDA_BRANCHES:
        if version >= порог:
            return индекс
    return None


# --- Определение ------------------------------------------------------------


def _windows() -> dict:
    """Версия и сборка Windows. Не получилось — пустые строки."""
    try:
        release, version, build, _ = platform.win32_ver()
    except Exception:
        release = version = build = ""
    return {
        "release": str(release or ""),
        "version": str(version or ""),
        "build": str(build or ""),
    }


def _cpu_name() -> str:
    """Название процессора из реестра. Пусто — не определилось."""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        return str(value or "").strip()
    except Exception:
        return ""


def _cpu() -> dict:
    """Процессор: название, ядра и потоки."""
    name = _cpu_name()
    cores = threads = None
    try:
        import psutil

        cores = psutil.cpu_count(logical=False)
        threads = psutil.cpu_count(logical=True)
    except Exception:
        pass
    return {
        "name": name,
        "cores": int(cores) if cores else None,
        "threads": int(threads) if threads else None,
    }


def _ram_gb():
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        return None


def _disk_free_gb():
    """Свободное место на диске, где стоит Труба — туда же ставят голоса."""
    try:
        return round(shutil.disk_usage(config.ROOT).free / 1024**3, 1)
    except Exception:
        return None


def _число(значение):
    """Число из строки nvidia-smi. Мусор — None, поле остаётся пустым."""
    текст = str(значение or "").strip()
    if not текст:
        return None
    try:
        return float(текст)
    except ValueError:
        return None


def gpus() -> list[dict]:
    """Видеокарты NVIDIA по `nvidia-smi`. Нет программы или драйвера — [].

    Список разбирается вручную, а не библиотекой: `nvidia-ml-py` уже стоит
    в базовой установции ради `core/sysinfo.py`, но там нужен только процент
    загрузки, а строка от `nvidia-smi` отдаёт всё сразу — имя, мегабайты,
    драйвер и версию вычислений.
    """
    try:
        итог = subprocess.run(SMI_ARGS, capture_output=True, text=True,
                             timeout=SMI_TIMEOUT, creationflags=_окно_нет())
    except (OSError, subprocess.SubprocessError):
        # Нет nvidia-smi, не запустился или завис — карт не видно.
        return []
    if getattr(итог, "returncode", 1) != 0:
        return []

    карты = []
    for строка in str(getattr(итог, "stdout", "") or "").splitlines():
        строка = строка.strip()
        if not строка:
            continue
        часть = [p.strip() for p in строка.split(",")]
        if len(часть) < 4:
            continue
        мегабайты = _число(часть[1])
        карты.append({
            "name": часть[0],
            "vram_gb": round(мегабайты / 1024, 1) if мегабайты else None,
            "driver": часть[2],
            # У карт постарше Turing `nvidia-smi` пишет `[N/A]` — это None,
            # и совет по ним опирается на память, а не на вычисления.
            "compute_cap": _число(часть[3]),
        })
    return карты


def torch_cuda() -> bool:
    """Стоит ли в `.venv` torch с CUDA. Версия — из метаданных, без импорта.

    Импорт torch занял бы секунды и полгигабайта памяти, а версия сборки
    записана прямо в имени пакета: `2.10.0+cu130`.
    """
    try:
        version = importlib.metadata.version("torch")
    except Exception:
        return False
    return "+cu" in str(version or "").lower()


def voices_installed() -> bool:
    """Стоят ли библиотеки качественных голосов.

    Проверяем по наличию пакетов, а не по импорту: `f5_tts` при первом
    импорте тянет за собой половину torch, а мастеру нужно знать только
    «поставлено или ещё нет».
    """
    for имя in ("transformers", "f5_tts", "vocos"):
        try:
            if importlib.util.find_spec(имя) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def detect() -> dict:
    """Всё, что можно узнать о компьютере. Ни одного исключения наружу."""
    return {
        "windows": _windows(),
        "cpu": _cpu(),
        "ram_gb": _ram_gb(),
        "disk_free_gb": _disk_free_gb(),
        "gpus": gpus(),
        "torch_cuda": torch_cuda(),
        "voices_installed": voices_installed(),
        "at": datetime.now().isoformat(timespec="seconds"),
    }


# --- Что ему по силам -------------------------------------------------------

# Пороги подобраны по живому железу хозяина (27.09) и по тому, сколько эти
# модели занимают: Higgs в FP8 — около 4 ГБ видеопамяти, ESpeech заметно
# меньше. С запасом, чтобы не советовать то, что потом зависнет.
HIGGS_COMPUTE_CAP = 8.9
HIGGS_VRAM_GB = 8
ESPEECH_VRAM_GB = 4
# Полная точность распознавания требует процессора и памяти.
STT_FULL_THREADS = 12
STT_FULL_RAM_GB = 16
# Пороги для предупреждений.
MIN_DISK_GB = 15
MIN_RAM_GB = 8


def _карта(hw: dict) -> dict:
    """Первая найденная карта NVIDIA — по ней и выносим вердикт."""
    for одна in (hw or {}).get("gpus") or []:
        if isinstance(одна, dict) and одна.get("name"):
            return одна
    return {}


def _короткая_карта(карта: dict) -> str:
    """`NVIDIA GeForce RTX 5070 Ti` → `RTX 5070 Ti`, для фразы человеку."""
    имя = str(карта.get("name") or "видеокарта")
    for лишнее in ("NVIDIA GeForce ", "NVIDIA ", "GeForce "):
        if имя.startswith(лишнее):
            return имя[len(лишнее):]
    return имя


def _гб(число) -> str:
    """`15.9` → «16 ГБ»: округляем, как это говорят, и без «.0»."""
    try:
        return f"{round(float(число))} ГБ"
    except (TypeError, ValueError):
        return "?"


def _короткая_версия(драйвер: str) -> str:
    """`531.14` → «531»: в фразе человеку хватает первых цифр."""
    version = _версия_драйвера(драйвер)
    return str(int(version)) if version > 0 else "?"


def _голос(hw: dict) -> tuple:
    """Какой голос тянет железо и почему — словами для человека."""
    карта = _карта(hw)
    if not карта:
        return "silero", "видеокарты NVIDIA нет — голос Silero на процессоре"

    драйвер = str(карта.get("driver") or "")
    if cuda_index(драйвер) is None:
        return "silero", (f"драйвер {_короткая_версия(драйвер)} старый — "
                          f"обнови драйвер NVIDIA, тогда будет качественный голос")

    имя = _короткая_карта(карта)
    память = _гб(карта.get("vram_gb"))
    вычисления = _число(карта.get("compute_cap")) or 0.0
    видеопамять = _число(карта.get("vram_gb")) or 0.0
    if вычисления >= HIGGS_COMPUTE_CAP and видеопамять >= HIGGS_VRAM_GB:
        return "higgs", f"{имя}, {память} — потянет качественный голос Higgs"
    if видеопамять >= ESPEECH_VRAM_GB:
        return "espeech", f"{имя}, {память} — потянет качественный голос ESpeech"
    return "silero", (f"{имя}, {память} — видеопамяти мало для качественного "
                      f"голоса, останется Silero на процессоре")


def _stt(hw: dict) -> tuple:
    """Полная точность распознавания или сжатая — и почему."""
    cpu = (hw or {}).get("cpu") or {}
    потоки = _число(cpu.get("threads")) or 0.0
    память = _число((hw or {}).get("ram_gb")) or 0.0
    if потоки >= STT_FULL_THREADS and память >= STT_FULL_RAM_GB:
        return "none", (f"{int(потоки)} потоков и {_гб(память)} памяти — "
                        f"пойдёт полная точность распознавания")
    return "int8", (f"{int(потоки)} потоков, {_гб(память)} памяти — "
                    f"распознавание лучше сжать (int8)")


def warnings(hw: dict) -> list:
    """Что может помешать. Список пуст, когда всё в порядке."""
    подсказки = []
    место = _число((hw or {}).get("disk_free_gb"))
    if место is not None and место < MIN_DISK_GB:
        # Место — с десятыми, как строкой выше в «Твоём компьютере»: округлённые
        # «14 ГБ» под «13.5 ГБ» выглядели как два разных замера (29.09).
        свободно = f"{место:.1f}".rstrip("0").rstrip(".")
        подсказки.append(f"мало места на диске ({свободно} ГБ) — качественным "
                         f"голосам нужно от {_гб(MIN_DISK_GB)}")
    память = _число((hw or {}).get("ram_gb"))
    if память is not None and память < MIN_RAM_GB:
        подсказки.append(f"мало памяти ({_гб(память)}) — для Трубы желательно "
                         f"от {_гб(MIN_RAM_GB)}")
    windows = (hw or {}).get("windows") or {}
    сборка = str(windows.get("build") or "")
    if сборка.isdigit() and int(сборка) < 19041:
        подсказки.append("Windows старше 10 версии 2004 — обнови до 10 или 11")
    return подсказки


def recommend(hw: dict) -> dict:
    """Совет по железу: что выбрать и почему — словами и значениями.

    `hw` берётся аргументом, а не определяется заново: компьютер уже
    опрошен один раз, и второй `nvidia-smi` мастеру не нужен.
    """
    голос, почему_голос = _голос(hw or {})
    точность, почему_stt = _stt(hw or {})
    return {
        "voice": голос,
        "voice_why": почему_голос,
        "stt_quantization": точность,
        "stt_why": почему_stt,
        "warnings": warnings(hw or {}),
    }
