"""Установка библиотек качественных голосов из пульта.

Пока `torch` не загружен, установка идёт в фоне без окна. Если он уже в
памяти, пульт перезапускается без голоса и продолжает установку в новом
процессе (`WebRuntime.voices_libs_start`). Перед запуском проверяются
видеокарта, драйвер, место и папка установки. Вывод установщика пишется
в `data/voices_install.log` для диагностики ошибок.
"""

import importlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config

# Журнал установки — рядом с остальным журналом, в `data`.
LOG_PATH = config.DATA_DIR / "voices_install.log"

# Предел времени установки не даёт фоновому процессу ждать сеть бесконечно.
TIMEOUT = 60 * 60

# Версии — как в `install.ps1` и в `requirements-voices.txt`.
TORCH = "torch==2.10.0"
TORCHAUDIO = "torchaudio==2.10.0"
# Индекс NVIDIA: по нему torch ставится с CUDA, а не сборка для процессора.
TORCH_INDEX = "https://download.pytorch.org/whl/"

# Место для torch с CUDA и библиотек; модели проверяются отдельно
# (`core/voice_models.disk_check`).
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
    """Проверить условия установки и вернуть причину отказа при необходимости.

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


# --- Что уже скачано ---------------------------------------------------------
#
# Библиотеки и веса моделей устанавливаются отдельно (`core/voice_models.py`).
# Наличие весов проверяется в локальном кеше Hugging Face без сети.


def _скачан_файл(репо: str, имя: str | None = None,
                  нужны: tuple[str, ...] = ()) -> bool:
    """Есть ли нужные файлы модели в локальном кеше, без обращения к сети."""
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except Exception:
        return False
    try:
        if имя is None:
            папка = Path(snapshot_download(репо, local_files_only=True))
            return all((папка / файл).is_file() for файл in нужны)
        else:
            return Path(hf_hub_download(
                repo_id=репо, filename=имя, local_files_only=True)).is_file()
    except Exception:
        return False


def weights_installed() -> dict:
    """Какие веса качественных голосов уже лежат на диске.

    Ключи совпадают с настройкой `tts_engine`; состояние весов показывается
    отдельно от состояния библиотек.
    """
    веса = {"espeech": False, "higgs": False}
    try:
        from core import tts

        веса["espeech"] = (
            _скачан_файл(tts.MODEL_REPO, tts.MODEL_FILE)
            and _скачан_файл(tts.MODEL_REPO, tts.VOCAB_FILE)
        )
    except Exception:
        pass
    try:
        from core import higgs_voice

        веса["higgs"] = _скачан_файл(
            higgs_voice.REPO, нужны=("config.json", "model.safetensors", "tokenizer.json")
        )
    except Exception:
        pass
    return веса


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

    Возвращается список команд для проверки и записи в журнал перед стартом.
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


# --- Кеш uv и окружение -------------------------------------------------------

# Кеш uv лежит в папке проекта, как у `install.ps1`: удаление проекта удаляет
# и кеш. По приросту кеша показывается ход установки.
CACHE_REL = (".cache", "uv")

# Два шага установки и их названия для человека. Порядок тот же, что у
# команд: сначала torch с CUDA, потом остальные библиотеки голосов.
STEP_TITLES = ("torch с CUDA", "библиотеки голосов")

# Оценка распакованного кеша по замеру: torch и torchaudio с CUDA — 2,6 ГиБ,
# остальные пакеты — 1,7 ГиБ. Сетевую загрузку чужого процесса `io_counters`
# не видит; из-за приблизительного размера прогресс до завершения не выше 99 %.
STEP_BYTES = (2_800_000_000, 1_800_000_000)


def кеш(root=None) -> Path:
    """Папка кеша uv в папке Трубы."""
    return _корень(root).joinpath(*CACHE_REL)


def cache_bytes(root=None) -> int:
    """Сколько байт в кеше uv. Не определилось — ноль, а не отказ установки."""
    всего = 0
    for корень, _папки, файлы in os.walk(кеш(root)):
        for имя in файлы:
            try:
                всего += os.stat(os.path.join(корень, имя)).st_size
            except OSError:
                pass
    return всего


def uv_есть(root=None) -> bool:
    """Нашёлся ли `uv` в папке Трубы.

    Если `.tools` отсутствует, пульт предлагает установщик в отдельном окне.
    """
    return _uv(_корень(root)) is not None


def среда(root=None) -> dict:
    """Окружение для uv: кеш в папке Трубы и русский вывод в журнал."""
    окружение = dict(os.environ)
    окружение["UV_CACHE_DIR"] = str(кеш(root))
    окружение["PYTHONIOENCODING"] = "utf-8"
    return окружение


def torch_в_процессе() -> bool:
    """Загружен ли уже torch в этом процессе пульта.

    Загруженный torch запирает файлы в Windows. Для их замены пульт
    перезапускается без голоса.
    """
    return "torch" in sys.modules


# --- Запрет загрузки torch на время установки -------------------------------

ПОКА_СТАВЛЮ = ("torch", "torchaudio")


class _ЗапретTorch:
    """Пока uv меняет torch на диске, грузить его в пульт нельзя.

    Голос, «Послушать голос», проверка звука — любой из них загрузил бы старый
    torch посреди замены: Windows заперла бы его файлы, uv упал бы на полпути,
    и сломанным остался бы даже обычный Silero. Поэтому на время установки
    любой `import torch` получает понятный отказ, а не полупакет. Одно место
    на все пути к torch, а не проверка у каждой кнопки.
    """

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ПОКА_СТАВЛЮ:
            raise ImportError("ставлю библиотеки голосов — голос заработает "
                              "после установки")
        return None


@contextmanager
def запрет_torch():
    запрет = _ЗапретTorch()
    sys.meta_path.insert(0, запрет)
    try:
        yield
    finally:
        try:
            sys.meta_path.remove(запрет)
        except ValueError:
            pass
        # uv положил новые пакеты, пока пульт работал: без сброса кешей поиск
        # модулей мог бы их не увидеть до перезапуска.
        importlib.invalidate_caches()


# --- Флаг «поставить после перезапуска» -------------------------------------

# Пульт, который не может поставить torch сам (он уже в памяти), оставляет
# флаг и перезапускается. Новый пульт читает флаг, голос в этот раз не
# включает и ставит библиотеки сам.
PENDING_PATH = config.DATA_DIR / "voices_install_pending.json"


def write_pending(models=None) -> bool:
    """Оставить флаг «поставить после перезапуска». Не записался — `False`."""
    try:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_PATH.write_text(json.dumps(
            {"models": list(models or []),
             "at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def read_pending() -> dict:
    """Что записано в флаге. Нет файла или он битый — пустой словарь."""
    try:
        данные = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return данные if isinstance(данные, dict) else {}


def clear_pending() -> None:
    """Флаг отработал или отменился — убрать, чтобы не ставить снова."""
    try:
        PENDING_PATH.unlink()
    except OSError:
        pass


# --- Запуск в фоне -----------------------------------------------------------


def _записать(журнал: Path, текст: str) -> None:
    """Дописать строку в журнал. Диск не дал — установку это не отменяет."""
    try:
        журнал.parent.mkdir(parents=True, exist_ok=True)
        with журнал.open("a", encoding="utf-8") as поток:
            поток.write(_строка(текст) + "\n")
    except OSError:
        pass


def _запустить(команда: list, root: Path, журнал: Path, stop=None,
               on_progress=None, было: int = 0) -> dict:
    """Выполнить команду установки отдельным процессом без окна.

    Возвращает успех, отмену или код ошибки. Вывод пишет в журнал для
    диагностики ошибки.
    """
    from core import voice_models

    _записать(журнал, "запускаю: " + " ".join(команда))
    try:
        журнал.parent.mkdir(parents=True, exist_ok=True)
        with журнал.open("a", encoding="utf-8") as поток:
            процесс = subprocess.Popen(
                команда, cwd=str(root), stdout=поток,
                stderr=subprocess.STDOUT, env=среда(root),
                creationflags=_окно_нет())
            try:
                while процесс.poll() is None:
                    # Отмена не должна ждать весь `poll` (у `taskkill` это
                    # секунды), поэтому ждём кусочками по две.
                    if voice_models._ждём_остановки(stop, 2.0):
                        voice_models.kill_tree(процесс)
                        _записать(журнал, "остановил хозяин")
                        return {"ok": False, "cancelled": True}
                    if on_progress is not None:
                        try:
                            on_progress(max(0, cache_bytes(root) - было))
                        except Exception:
                            pass
            except BaseException:
                if процесс.poll() is None:
                    voice_models.kill_tree(процесс)
                raise
    except (OSError, subprocess.SubprocessError) as exc:
        _записать(журнал, f"не получилось запустить: {exc}")
        return {"ok": False, "error": f"не получилось запустить: {exc}"}
    if процесс.returncode != 0:
        _записать(журнал, f"команда вернула {процесс.returncode}")
        return {"ok": False, "returncode": процесс.returncode}
    return {"ok": True}


def install(root=None, on_step=None, on_progress=None, stop=None) -> dict:
    """Ставит качественные голоса в фоне, шагами, с отменой.

    `on_step` сообщает о шаге, `on_progress` — о приросте кеша uv,
    `stop` отменяет процесс вместе с деревом (`core/voice_models.kill_tree`).

    Перезапуск — не дело этого модуля: если torch уже был в процессе, пульт
    перезапускается ДО установки (`WebRuntime.voices_libs_start`), и сюда мы
    попадаем уже в процессе, где torch не загружен. Поэтому `restart` в
    успешном ответе — `False`.
    """
    root = _корень(root)
    шаг = on_step if callable(on_step) else (lambda **_п: None)
    журнал = Path(LOG_PATH)
    начало = time.monotonic()
    # Для ответа нужен понятный путь к журналу установки.
    где = "data\\" + журнал.name

    try:
        проверка = check(root)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not проверка.get("ok"):
        отказ = проверка.get("error") or "качественные голоса поставить нельзя"
        шаг(step=0, steps=0, title="", text=отказ)
        _записать(журнал, f"качественные голоса: {отказ}")
        return {"ok": False, "error": отказ}

    индекс = str(проверка.get("cuda_index") or "cu130")
    try:
        список = команды(root, индекс)
    except Exception as exc:
        отказ = f"{type(exc).__name__}: {exc}"
        шаг(step=0, steps=0, title="", text=отказ)
        _записать(журнал, f"качественные голоса: {отказ}")
        return {"ok": False, "error": отказ}

    всего = len(список)
    _записать(журнал, f"качественные голоса: начинаю, сборка torch {индекс}, "
                      f"{всего} шага")
    # Прирост кеша uv считаем от начала установки, а не от начала шага: иначе
    # на втором шаге полоса поехала бы назад, будто ничего не скачано.
    было = cache_bytes(root)

    def прогресс(байт) -> None:
        """В полосу пульта — прирост байт с начала установки."""
        if callable(on_progress):
            on_progress(max(0, int(байт)))

    with запрет_torch():
        for номер, команда in enumerate(список, 1):
            title = (STEP_TITLES[номер - 1] if номер <= len(STEP_TITLES)
                     else f"шаг {номер}")
            шаг(step=номер, steps=всего, title=title,
                text=f"ставлю {title} — шаг {номер} из {всего}")
            итог = _запустить(команда, root, журнал, stop=stop,
                              on_progress=прогресс, было=было)
            if итог.get("ok"):
                continue
            if итог.get("cancelled"):
                _записать(журнал, f"качественные голоса: остановил хозяин "
                                  f"(шаг {номер} из {всего})")
                return {"ok": False, "cancelled": True}
            отказ = (f"качественные голоса не поставились: шаг {номер} из "
                     f"{всего} — {title}, подробности в {где}")
            шаг(step=номер, steps=всего, title=title, text=отказ)
            _записать(журнал, отказ)
            return {"ok": False, "error": отказ}

    минут = (time.monotonic() - начало) / 60
    _записать(журнал, f"качественные голоса поставлены за {минут:.0f} мин")
    шаг(step=всего, steps=всего, title=STEP_TITLES[-1],
        text=f"библиотеки голосов поставлены за {минут:.0f} мин — "
             f"скачиваю выбранные модели")
    return {"ok": True, "restart": False, "cuda_index": индекс,
            "minutes": round(минут, 1)}


# --- Запуск отдельным окном ---------------------------------------------------
#
# Отдельное окно установщика используется, если `uv` в папке не найден.
# При закрытии пульта оно продолжает работу и освобождает файлы torch.

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
