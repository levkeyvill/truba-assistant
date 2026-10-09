"""Отчёт о проблеме для автора: запись падений, копия журнала, архив сбоев.

Пульт работает без консоли (`pythonw`), и всё, что Python печатает в stderr
при падении, пропадает. Поэтому при запуске пульта ошибки направляются в
файлы `data/errors.log` (необработанные исключения и `logging` от WARNING) и
`data/crash.log` (падение самого процесса через `faulthandler`).

Архив для автора собирается без разговоров: из `session.log` берутся только
строки про сбои и вехи запуска, а строки с речью («услышала», «ответила»,
«пропустил …») не берутся вовсе. Путь к папке пользователя в текстах
заменяется на `%USERPROFILE%`.
"""

import faulthandler
import logging
import os
import platform
import re
import sys
import threading
import time
import zipfile
from pathlib import Path

import config

ERRORS_LOG = config.DATA_DIR / "errors.log"
CRASH_LOG = config.DATA_DIR / "crash.log"
SESSION_LOG = config.DATA_DIR / "session.log"
# Журналы установки: основной и установки библиотек голосов.
INSTALL_LOGS = (config.DATA_DIR / "install.log",
                config.DATA_DIR / "voices_install.log")
# Файлы ошибок не растут бесконечно: больше — обрезаются до хвоста.
MAX_BYTES = 1_000_000
# Из журнала работы в архив — не больше стольких строк про сбои.
MAX_SESSION_LINES = 400
TELEGRAM = "https://t.me/levkeyvill"

# Строки журнала с речью человека или с его данными: в архив не идут, даже
# если в них есть слово «ошибка».
_РЕЧЬ = re.compile(
    r"^\[\d\d:\d\d:\d\d\] (услышал|ответил|ответ:|пропустил|сказал|заметка|"
    r"буфер обмена|команда голосом|проверка просьбы|файл найден|"
    r"файл не найден|разбор|поиск в интернете|прочитала страницу|"
    r"запросы для поиска|голос похож|напомина|таймер|документ|YouTube|"
    r"телефон попросил|телефон открыл заметк)", re.I)
_СБОЙ = re.compile(
    r"ошибк|не удалось|не вышло|не получил|не включил|не поднял|не загрузил|"
    r"не смогл|не отвеча|упал|сбой|отказ|traceback|error|exception|timeout|"
    r"не запустил|не открыл|недоступ", re.I)
# Вехи запуска — чтобы сбои читались по времени.
_ВЕХА = re.compile(r"^\[\d\d:\d\d:\d\d\] (———|веб-пульт запущен|голосовой режим|"
                   r"мозг поднят|модели подняты|обновление|Провайдер)")

_crash_file = None
_lock = threading.Lock()


def _скрыть_личное(текст: str) -> str:
    """Путь к папке пользователя и его имя в Windows — без имени."""
    дом = str(Path.home())
    if дом and len(дом) > 3:
        текст = текст.replace(дом, "%USERPROFILE%")
        текст = текст.replace(дом.replace("\\", "/"), "%USERPROFILE%")
    имя = os.environ.get("USERNAME", "")
    if len(имя) >= 3:
        # Только целое слово: «user» не должен задеть «Users» и «%USERPROFILE%».
        текст = re.sub(rf"(?<![\w%]){re.escape(имя)}(?![\w%])", "%USERNAME%",
                       текст, flags=re.I)
    return текст


def _обрезать(путь: Path) -> None:
    """Файл больше `MAX_BYTES` — оставить хвост."""
    try:
        if путь.stat().st_size <= MAX_BYTES:
            return
        данные = путь.read_bytes()[-MAX_BYTES // 2:]
        путь.write_bytes(данные)
    except OSError:
        pass


def write_error(заголовок: str, текст: str = "") -> None:
    """Дописать запись в `errors.log`. Сама никогда не падает."""
    try:
        with _lock:
            ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
            _обрезать(ERRORS_LOG)
            with ERRORS_LOG.open("a", encoding="utf-8") as файл:
                файл.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                           f"{заголовок}\n{текст.rstrip()}\n")
    except Exception:
        pass


def install() -> None:
    """Направить ошибки пульта в файлы. Вызывать один раз при запуске."""
    global _crash_file
    import traceback

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _обрезать(CRASH_LOG)
        _crash_file = CRASH_LOG.open("a", encoding="utf-8")
        _crash_file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} запуск "
                          f"{config.VERSION}\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        _crash_file = None

    прежний = sys.excepthook

    def в_файл(тип, ошибка, след):
        write_error("необработанная ошибка",
                    "".join(traceback.format_exception(тип, ошибка, след)))
        try:
            прежний(тип, ошибка, след)
        except Exception:
            pass

    sys.excepthook = в_файл

    def из_потока(args):
        if args.exc_type is SystemExit:
            return
        имя = getattr(args.thread, "name", "?")
        write_error(f"ошибка в потоке {имя}", "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))

    threading.excepthook = из_потока

    class _ВФайл(logging.Handler):
        def emit(self, запись):
            try:
                write_error(f"{запись.levelname} {запись.name}", self.format(запись))
            except Exception:
                pass

    обработчик = _ВФайл(level=logging.WARNING)
    обработчик.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(обработчик)


def _загрузки() -> Path:
    """Папка «Загрузки»; её нет — папка пользователя."""
    from core import folders

    try:
        путь = folders.path_of("downloads")
    except Exception:
        путь = None
    if путь is None or not Path(путь).is_dir():
        путь = Path.home()
    return Path(путь)


def reveal(путь: Path) -> None:
    """Показать файл в проводнике выделенным."""
    import subprocess

    subprocess.Popen(["explorer", f"/select,{Path(путь)}"])


def save_journal() -> Path:
    """Копия журнала работы в «Загрузки». Ответ — путь копии."""
    if not SESSION_LOG.is_file():
        raise FileNotFoundError("журнал пока пуст")
    цель = _загрузки() / f"Труба-журнал-{time.strftime('%Y-%m-%d_%H%M')}.txt"
    цель.write_bytes(SESSION_LOG.read_bytes())
    return цель


def session_errors() -> str:
    """Строки журнала про сбои и вехи запуска, без речи."""
    if not SESSION_LOG.is_file():
        return ""
    строки = []
    with SESSION_LOG.open(encoding="utf-8", errors="replace") as файл:
        for строка in файл:
            строка = строка.rstrip("\n")
            if _РЕЧЬ.match(строка):
                continue
            if _ВЕХА.match(строка) or _СБОЙ.search(строка):
                строки.append(строка)
    return _скрыть_личное("\n".join(строки[-MAX_SESSION_LINES:]))


def _сведения() -> str:
    """Версия, система и выбранные движки — без ключей и личного."""
    import config as c

    def поле(имя):
        return getattr(c, имя, "")

    return "\n".join([
        f"Труба {c.VERSION}",
        f"Windows: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
        f"голос: {поле('TTS_ENGINE')}, распознавание: {поле('STT_MODEL')}",
        f"режим слуха: {поле('LISTEN_MODE')}, вывод: {поле('OUTPUT')}",
        f"собрано: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ])


def build_report() -> Path:
    """Архив ошибок для автора в «Загрузках». Ответ — путь архива."""
    цель = _загрузки() / f"Труба-отчёт-{time.strftime('%Y-%m-%d_%H%M')}.zip"
    with zipfile.ZipFile(цель, "w", zipfile.ZIP_DEFLATED) as архив:
        архив.writestr("сведения.txt", _сведения())
        архив.writestr("журнал-сбои.txt", session_errors() or "(сбоев в журнале нет)")
        for путь in (ERRORS_LOG, CRASH_LOG, *INSTALL_LOGS):
            if путь.is_file():
                текст = путь.read_text(encoding="utf-8", errors="replace")
                архив.writestr(путь.name, _скрыть_личное(текст[-MAX_BYTES:]))
    return цель
