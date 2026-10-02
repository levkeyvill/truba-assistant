"""Модели качественных голосов: Higgs и ESpeech — что, сколько и как качать.

02.10: хозяин пожаловался на две вещи. «Установить качественные голоса» ставила
только библиотеки, а потом при первом включении Higgs молча тянул 9,3 ГБ — без
спроса («почему он качает 9 гигов, когда не было выбора его качать?») — и это
было прямо в загрузке голоса, где отменить нельзя было. Теперь скачивание
модели — отдельная работа пульта по выбору хозяина, а голос не ходит в сеть
никогда: нет модели — отказ словами (`core/voice_loop.py`).

Модуль один на обе модели. Описание — `MODELS`, проверка «уже скачано» — через
`voices_install.weights_installed` (тот же локальный кеш Hugging Face, без
сети), само скачивание — отдельным процессом, его можно погасить вместе с
потомками (`coordination/ГРАБЛИ.md`, раздел про `.venv\\Scripts\\python.exe`
uv: `kill()` гасит только обёртку, нужен `taskkill /T`).
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import config

# Обе модели: название для человека и сколько весит файл(ы) на диске.
# Размеры — по факту (Higgs 9,32 ГБ, ESpeech 2,7 ГБ), а не по памяти о
# видеопамяти: «4,3 ГБ» у Higgs — это то, сколько он занимает на карте после
# сжатия в FP8, отдельного файла такого размера нет.
MODELS = {
    "higgs": {"title": "Higgs", "bytes": 9.32e9},
    "espeech": {"title": "ESpeech", "bytes": 2.7e9},
}

# Запас сверх размера моделей: Hugging Face держит недокачанные куски и
# временные файлы, и без запасу диск кончается на 99 %.
ЗАПАС_БАЙТ = 1_000_000_000

# Куда пишется вывод процесса скачивания. При сбое хозяин присылает этот
# файл, и по нему видно, что ответил Hugging Face.
LOG_DIR = config.DATA_DIR

# Кто качается прямо сейчас. Голос спрашивает это, чтобы сказать «качается,
# N %», а не «не скачана» (он же не может знать, что делает пульт).
_текущий: dict = {}
_замок = threading.Lock()


def known(имя: str) -> bool:
    """Есть ли такая модель. Всё прочее — опечатка, а её качать нечем."""
    return имя in MODELS


def title(имя: str) -> str:
    return str((MODELS.get(имя) or {}).get("title") or имя)


def size_bytes(имя: str) -> int:
    return int((MODELS.get(имя) or {}).get("bytes") or 0)


def гб(байт) -> str:
    """Байты словами: `9.32e9` → «9,3 ГБ» (у нас запятая, не точка)."""
    try:
        return f"{float(байт) / 1e9:.1f}".replace(".", ",") + " ГБ"
    except (TypeError, ValueError):
        return "?"


def size_text(имя: str) -> str:
    return гб(size_bytes(имя))


def процент(готово, всего) -> int:
    """Сколько процентов скачано, 0…100."""
    try:
        всего = float(всего)
    except (TypeError, ValueError):
        return 0
    if всего <= 0:
        return 0
    return min(100, max(0, int(float(готово) * 100 / всего)))


# --- Что именно качать ------------------------------------------------------


def targets(имя: str) -> dict:
    """Репозиторий и файлы модели.

    Higgs — весь снимок репозитория (`snapshot_download`): загрузчик берёт оттуда
    и веса, и кодек, и конфиг. ESpeech — ровно два файла, остальное из её
    репозитория ей не нужно. Пути берутся у самих голосов, чтобы список не
    разошёлся с тем, что реально грузит синтез.
    """
    if имя == "higgs":
        from core import higgs_voice

        return {"repo": higgs_voice.REPO, "files": None}
    from core import tts

    return {"repo": tts.MODEL_REPO, "files": (tts.MODEL_FILE, tts.VOCAB_FILE)}


def _код(цели: dict) -> str:
    """Программа дочернего процесса: только скачать, ничего не печатать."""
    if цели["files"] is None:
        return ("import config\n"
                "from huggingface_hub import snapshot_download\n"
                f"snapshot_download({цели['repo']!r})\n")
    список = ", ".join(repr(имя) for имя in цели["files"])
    # ESpeech при загрузке берёт ещё вокодер Vocos и словарь ударений RUAccent
    # и без них полез бы в сеть при первом включении голоса. Качаем всё здесь
    # же: «скачано» должно значить «скачано всё» (02.10).
    return ("import config\n"
            "from huggingface_hub import hf_hub_download, snapshot_download\n"
            f"for _файл in ({список},):\n"
            f"    hf_hub_download({цели['repo']!r}, _файл)\n"
            "snapshot_download('charactr/vocos-mel-24khz')\n"
            "from ruaccent import RUAccent\n"
            "RUAccent().load(omograph_model_size='turbo3.1', use_dictionary=True,"
            " tiny_mode=False)\n")


# --- Проверка «уже скачано» -------------------------------------------------


def installed(имя: str) -> bool:
    """Модель лежит на диске целиком. Сети нет — только локальный кеш."""
    from core import voices_install

    try:
        return bool(voices_install.weights_installed().get(имя))
    except Exception:
        return False


def needed(модели) -> list:
    """Из списка — те, которых ещё нет. Порядок хозяина сохраняется."""
    return [имя for имя in dict.fromkeys(модели or [])
            if known(имя) and not installed(имя)]


# --- Место на диске ---------------------------------------------------------


def free_bytes():
    """Свободно на диске, где стоит Труба. Не определилось — `None`."""
    try:
        return shutil.disk_usage(str(config.ROOT)).free
    except Exception:
        return None


def disk_check(модели) -> dict:
    """Хватит ли места. Отказ — с цифрами: «свободно 5,0 ГБ, нужно 10,3 ГБ»."""
    нужно = sum(size_bytes(имя) for имя in модели if known(имя)) + ЗАПАС_БАЙТ
    свободно = free_bytes()
    if свободно is None:
        # Не определилось — это не «мало». Иначе на нестандартном компьютере
        # скачивание отказалось бы с выдуманной цифрой.
        return {"ok": True, "need": нужно, "free": None}
    if свободно < нужно:
        return {"ok": False, "need": нужно, "free": свободно,
                "error": (f"мало места на диске: свободно {гб(свободно)}, "
                          f"а моделям нужно {гб(нужно)} — освободи и повтори")}
    return {"ok": True, "need": нужно, "free": свободно}


# --- Слова для человека -----------------------------------------------------


def нужна_скачать(имя: str) -> str:
    """Отказ голоса: модели нет — значит сказать, где её взять."""
    return (f"Модель {title(имя)} не скачана — скачай её: Голос → Озвучивание → "
            f"Качественные голоса ({size_text(имя)})")


def качается(имя: str) -> str:
    """Отказ, когда пульт уже качает: с процентом, а не «не скачана»."""
    return (f"Модель {title(имя)} качается, {downloading(имя)} % — подожди, пульт "
            f"сам докачает ({size_text(имя)})")


def set_progress(модель: str, готово, всего) -> None:
    """Кто качается и насколько. Голос читает это же, поэтому замок."""
    with _замок:
        _текущий.update({"model": модель, "done": int(готово), "total": int(всего)})


def clear_progress(модель: str = "") -> None:
    """Скачали или отменили — состояние пустое, иначе голос будет верить в
    него и после перезапуска пульта."""
    with _замок:
        if модель and _текущий.get("model") != модель:
            return
        _текущий.clear()


def downloading(имя: str):
    """Процент текущей загрузки этой модели или `None` — не качается."""
    with _замок:
        if _текущий.get("model") != имя or not _текущий.get("total"):
            return None
        return процент(_текущий.get("done"), _текущий.get("total"))


# --- Само скачивание --------------------------------------------------------


def hf_dir() -> Path:
    """Папка кеша Hugging Face целиком.

    Считать надо именно её: кроме `hub` там лежит `xet`, и его куски — половина
    прироста. Старый счёт брал только `hub` и показывал «0,0 ГБ» при скачанных
    257 МБ.
    """
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home)
    свой = os.environ.get("HF_HUB_CACHE")
    if свой:
        return Path(свой).parent
    return Path(config.MODELS_DIR / "hf")


def cache_bytes() -> int:
    """Сколько байт сейчас в кеше, включая недокачанные куски."""
    всего = 0
    for корень, _папки, файлы in os.walk(hf_dir()):
        for имя in файлы:
            try:
                всего += os.stat(os.path.join(корень, имя)).st_size
            except OSError:
                pass
    return всего


def already_bytes(имя: str) -> int:
    """Сколько этой модели уже лежит недокачанным — для честной докачки.

    Прирост кеша начинается с нуля, а Hugging Face продолжает с того места,
    где оборвалось. Без этой поправки докачка показывала бы «0,3 из 9,3» при
    лежащих 2 ГБ и заканчивалась на «77 %». Готовые файлы не считаем: их
    качать не будут, и в прирост они не попадут.
    """
    try:
        репо = targets(имя)["repo"]
    except Exception:
        return 0
    папка = hf_dir() / "hub" / ("models--" + str(репо).replace("/", "--"))
    всего = 0
    for корень, _папки, файлы in os.walk(папка):
        for файл in файлы:
            if файл.endswith(".incomplete"):
                try:
                    всего += os.stat(os.path.join(корень, файл)).st_size
                except OSError:
                    pass
    return всего


def kill_tree(процесс) -> None:
    """Погасить процесс скачивания вместе с потомками.

    `python.exe` из `.venv` (uv) — обёртка, настоящий Python у неё дочерний:
    `kill()` гасил одну обёртку, а скачивание шло дальше (проверено 01.10).
    """
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(процесс.pid)],
                       capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    try:
        процесс.kill()
    except Exception:
        pass
    try:
        процесс.wait(timeout=10)
    except Exception:
        pass


def _ждём_остановки(stop, сколько: float) -> bool:
    """Ждём кусками: отмена не должна ждать весь `poll` (а это секунды)."""
    конец = time.monotonic() + max(0.0, float(сколько))
    while True:
        if stop is not None and stop.wait(0.2):
            return True
        if time.monotonic() >= конец:
            return False


def download(имя: str, stop=None, progress=None, poll: float = 3.0) -> bool:
    """Скачать модель отдельным процессом. False — отменили.

    `progress(скачано_байт)` зовётся раз в `poll` секунд. Обрыв сети и отказ
    Hugging Face не висят: процесс умер — `RuntimeError` с последними строками
    журнала, а следующий раз докачается с того же места.
    """
    if not known(имя):
        raise ValueError(f"такой модели нет: {имя}")
    код = _код(targets(имя))
    среда = dict(os.environ)
    среда["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    журнал = Path(LOG_DIR) / f"voice_download_{имя}.log"
    журнал.parent.mkdir(parents=True, exist_ok=True)
    было = cache_bytes()
    уже = already_bytes(имя)
    with журнал.open("w", encoding="utf-8") as поток:
        процесс = subprocess.Popen(
            [sys.executable, "-c", код], cwd=str(config.ROOT), env=среда,
            stdout=поток, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while процесс.poll() is None:
                if _ждём_остановки(stop, poll):
                    kill_tree(процесс)
                    return False
                if progress is not None:
                    try:
                        progress(уже + max(0, cache_bytes() - было))
                    except Exception:
                        pass
        except BaseException:
            if процесс.poll() is None:
                kill_tree(процесс)
            raise
    if процесс.returncode != 0:
        try:
            хвост = журнал.read_text(encoding="utf-8", errors="replace")
        except OSError:
            хвост = ""
        хвост = " ".join(хвост.strip().splitlines()[-2:])[-300:]
        raise RuntimeError(f"модель {title(имя)} не скачалась"
                           + (f": {хвост}" if хвост else "")
                           + " — в следующий раз докачается с того же места")
    return True
