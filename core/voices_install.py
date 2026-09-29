"""Качественные голоса кнопкой из пульта: то же, что `install.ps1 -Voices`.

Разница одна — установщик запускают руками и с окном консоли, а отсюда
установка идёт из работающего пульта, в фоне и без окна. Поэтому:

  - **Проверки до старта.** Видеокарты NVIDIA нет, драйвер старый или места
    мало — отказ словами, ничего не качаем.
  - **Рабочая папка разработки — отказ.** Там уже стоит torch с CUDA, и
    переставить его поверх работающей копии Трубы нельзя.
  - **torch уже загружен в процесс.** Даже если библиотеки встали, текущая
    копия в памяти останется прежней: нужен перезапуск пульта, о котором
    и говорим в ответе.
  - **Никаких окон.** Иначе хозяину на пульт прилетел бы чёрный прямоугольник
    с надписью и полосой прогресса.

Всё, что печатает pip, пишется в `data/voices_install.log`: после неудачи
хозяин пришлёт этот файл, и по нему видно, на чём установка встала.
"""

import subprocess
import time
from datetime import datetime
from pathlib import Path

import config

# Журнал установки — рядом с остальным журналом, в `data`.
LOG_PATH = config.DATA_DIR / "voices_install.log"

# Сколько идёт установка. Больше часа не бывает: сеть обрывается, а ждать
# бесконечно нельзя — хозяин ушёл, а пульт всё держит поток.
TIMEOUT = 60 * 60

# Версии — как в `install.ps1` и в `requirements-voices.txt`.
TORCH = "torch==2.10.0"
TORCHAUDIO = "torchaudio==2.10.0"
# Индекс NVIDIA: по нему torch ставится с CUDA, а не сборка для процессора.
TORCH_INDEX = "https://download.pytorch.org/whl/"

# Сколько свободного места нужно качественным голосам. Модель Higgs (~9 ГБ)
# качается сама, когда её выберут, а библиотеки и torch с CUDA — сейчас.
MIN_DISK_GB = 15

VOICES_REQUIREMENTS = "requirements-voices.txt"


def _окно_нет() -> int:
    """Флаг «не показывать окно консоли». На другой системе — ноль."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _строка(текст: str) -> str:
    """Строка журнала: время и текст, как в установщике."""
    return f"{datetime.now().strftime('%H:%M:%S')}  {текст}"


# --- Проверки до старта ------------------------------------------------------


def _корень(root=None) -> Path:
    return Path(root) if root is not None else config.ROOT


def check(root=None) -> dict:
    """Можно ли ставить: что нашлось, и если нет — почему словно.

    Отдельная функция, а не только часть `install`, потому что пульт должен
    показать отказ сразу по кнопке, не запуская ничего.
    """
    root = _корень(root)
    from core import hardware

    if (root / "coordination").is_dir():
        return {"ok": False,
                "error": "в рабочей папке ставь голоса руками — "
                         "здесь код в работе, его трогать нельзя"}

    hw = hardware.detect()
    карты = hw.get("gpus") or []
    if not карты:
        return {"ok": False,
                "error": "видеокарты NVIDIA нет — качественные голоса на ней "
                         "только, а обычный Silero уже работает"}

    драйвер = str(карты[0].get("driver") or "")
    индекс = hardware.cuda_index(драйвер)
    if индекс is None:
        return {"ok": False,
                "error": f"драйвер видеокарты {драйвер or '—'} старый — "
                         f"обнови его, и тогда можно качественный голос"}

    место = hw.get("disk_free_gb")
    if место is not None and float(место) < MIN_DISK_GB:
        # `reason` — для пульта: про место «Твой компьютер» уже сказал
        # красной строкой (`hardware.warnings`), второй раз не повторяет.
        return {"ok": False, "reason": "disk",
                "error": f"мало места на диске ({место} ГБ) — качественным "
                         f"голосам нужно от {MIN_DISK_GB} ГБ"}

    return {"ok": True, "cuda_index": индекс, "driver": драйвер,
            "gpus": карты, "disk_free_gb": место}


# --- Команды ----------------------------------------------------------------


def _python(root: Path) -> Path:
    """Python окружения Трубы."""
    return root / ".venv" / "Scripts" / "python.exe"


def _uv(root: Path):
    """`uv` из `.tools` или None. Установщик кладёт его именно туда."""
    путь = root / ".tools" / "uv.exe"
    return путь if путь.is_file() else None


def команды(root=None, cuda_index: str = "cu130") -> list:
    """Команды установки — ровно те, что у `install.ps1 -Voices`.

    Список, а не запуск: так его можно и проверить, и показать в журнале
    перед стартом. Хозяину, у которого всё встало, будет с чем сверяться.
    """
    root = _корень(root)
    index = f"{TORCH_INDEX}{cuda_index}"
    uv = _uv(root)
    python = _python(root)
    # Индексы библиотек одинаковые в обоих случаях: часть пакетов лежит на
    # PyPI, часть — в сборке NVIDIA, и pip должен видеть оба.
    библиотеки = ["--extra-index-url", index]

    if uv is not None:
        префикс = [str(uv), "pip", "install", "--python", str(python)]
        # uv умеет переставить только torch одной опцией, как в установщике.
        переустановить = ["--reinstall-package", "torch"]
        # Выбор между индексами — только у uv; pip его флага не знает.
        библиотеки = библиотеки + ["--index-strategy", "unsafe-best-match"]
    else:
        префикс = [str(python), "-m", "pip", "install"]
        # У pip своей опции «переставить только torch» нет, а ставить всё
        # заново нельзя: базовая Труба на этом же torch и работает. Поэтому
        # переустанавливаем ровно torch и torchaudio, без зависимостей.
        переустановить = ["--force-reinstall", "--no-deps"]

    return [
        префикс + [TORCH, TORCHAUDIO, "--index-url", index] + переустановить,
        префикс + ["-r", VOICES_REQUIREMENTS] + библиотеки,
    ]


# --- Запуск -----------------------------------------------------------------


def _записать(журнал: Path, текст: str) -> None:
    """Дописать строку в журнал. Диск не дал — установку это не отменяет."""
    try:
        журнал.parent.mkdir(parents=True, exist_ok=True)
        with журнал.open("a", encoding="utf-8") as поток:
            поток.write(_строка(текст) + "\n")
    except OSError:
        pass


def _запустить(команда: list, root: Path, журнал: Path) -> bool:
    """Одна команда установки. Её вывод целиком уходит в журнал."""
    _записать(журнал, "запускаю: " + " ".join(команда))
    try:
        with журнал.open("a", encoding="utf-8") as поток:
            итог = subprocess.run(
                команда, cwd=str(root), stdout=поток,
                stderr=subprocess.STDOUT, timeout=TIMEOUT,
                creationflags=_окно_нет())
    except (OSError, subprocess.SubprocessError) as exc:
        _записать(журнал, f"не получилось запустить: {exc}")
        return False
    if getattr(итог, "returncode", 1) == 0:
        return True
    _записать(журнал, f"команда вернула {getattr(итог, 'returncode', '?')}")
    return False


def install(root=None, on_step=None) -> dict:
    """Ставит качественные голоса. Шаги видны хозяину через `on_step`.

    Возвращает `{"ok": True, "restart": True}` — torch уже загружен в процессе,
    поэтому пульт надо перезапустить, — либо `{"ok": False, "error": ...}`.
    """
    root = _корень(root)
    шаг = on_step if callable(on_step) else (lambda текст: None)
    журнал = Path(LOG_PATH)
    начало = time.monotonic()

    try:
        проверка = check(root)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not проверка.get("ok"):
        отказ = проверка.get("error") or "качественные голоса поставить нельзя"
        шаг(отказ)
        _записать(журнал, f"качественные голоса: {отказ}")
        return {"ok": False, "error": отказ}

    индекс = str(проверка.get("cuda_index") or "cu130")
    _записать(журнал, f"качественные голоса: начинаю, сборка torch {индекс}")
    шаг(f"ставлю torch с CUDA ({индекс}) — качается 3–4 ГБ, это 5–15 минут")

    try:
        список = команды(root, индекс)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    шаг("ставлю библиотеки качественных голосов (1–2 ГБ, 5–15 минут)")
    for номер, команда in enumerate(список, 1):
        if not _запустить(команда, root, журнал):
            отказ = (f"качественные голоса не поставились: шаг {номер} "
                     f"из {len(список)} — подробности в {журнал.name}")
            шаг(отказ)
            _записать(журнал, отказ)
            return {"ok": False, "error": отказ}

    минут = (time.monotonic() - начало) / 60
    _записать(журнал, "качественные голоса поставлены — перезапусти пульт")
    шаг(f"готово за {минут:.0f} мин — перезапусти пульт, чтобы голоса подхватились")
    return {"ok": True, "restart": True, "cuda_index": индекс,
            "minutes": round(минут, 1)}


# --- Запуск отдельным окном ---------------------------------------------------
#
# 28.09: ставить torch с CUDA изнутри работающего пульта нельзя — пульт сам
# держит torch загруженным (голос Silero), а Windows не даёт заменить файлы
# работающей библиотеки: установка упала бы на полпути, «доступ запрещён».
# Поэтому кнопка в пульте запускает проверенный установщик
# («Установить качественные голоса.bat» → tools/install.ps1 -Voices) в своём
# окне, а пульт закрывается и освобождает файлы. Установщик в конце сам
# открывает пульт снова (порт к тому времени давно свободен).
# `install()` выше остаётся для проверок и для папки без пульта.

BAT = "Установить качественные голоса.bat"


def launch_external(root=None) -> dict:
    """Открыть установщик качественных голосов отдельным окном."""
    root = _корень(root)
    проверка = check(root)
    if not проверка.get("ok"):
        return проверка
    bat = root / BAT
    if not bat.is_file():
        return {"ok": False, "error": f"нет файла «{BAT}» в папке Трубы"}
    try:
        # `start` — новое окно консоли, чтобы человек видел шаги; не дочерний
        # процесс: пульт сейчас закроется, а установка должна идти дальше.
        # `-Yes`: пульт уже спросил «Начать?», второй вопрос в окне лишний.
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "Труба — качественные голоса", str(bat), "-Yes"],
            cwd=str(root),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except OSError as exc:
        return {"ok": False, "error": f"установщик не запустился: {exc}"}
    return {"ok": True, "started": True, "close": True,
            "cuda_index": проверка.get("cuda_index")}
