r"""Обновление Трубы с GitHub: проверка, скачивание, установка, откат.

Правила, которые здесь нельзя нарушать:

  - **Личные данные не трогаем.** Обновляется только код (белый
    список ниже). `data/`, `settings.json`, `.env`, `apps.json`, `models/`,
    `voice/`, `prompts/` и прочее в него не входят даже тогда, когда лежит
    в архиве: это пользовательские настройки и данные.
  - **Рабочая папка разработчика не обновляется.** Там, где лежит
    `coordination/`, стоит свежая работа, и установка затёрла бы её
    опубликованной старой. Проверка обновлений при этом работает.
  - **Перед заменой — копия.** Если новое не запускается, файлы
    возвращаются на место, а пульт сообщает об откате.
  - **Никаких исключений наружу.** Любой обрыв — понятная строка словами
    для человека, а не `Traceback` в журнале.

Все функции берут `root` — папку, где стоит проект. По умолчанию это
`config.ROOT`; в тестах используется временная папка.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import config

# --- Куда и как ходим ------------------------------------------------------

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Truba-updater",
}
# Скачивать разрешено только отсюда. Адрес приходит из ответа GitHub, но
# подсунуть его может не только сам GitHub: redirect на посторонний сайт —
# это уже не обновление, а чужая раздача.
ALLOWED_HOSTS = ("https://api.github.com/", "https://codeload.github.com/")

# Предел ожидания проверки версии нужен для ответа пульта.
CHECK_TIMEOUT = 8.0
# Потолок архива. Код весит мегабайты, триста — это уже не наш выпуск.
MAX_ARCHIVE = 300 * 1024 * 1024
# Заметки к выпуску длиннее этого пульту показывать незачем.
NOTES_LIMIT = 4000
# Сколько бэкапов держим: три последних достаточно, а диск они не съедают.
KEEP_BACKUPS = 3
# Список файлов, которых до обновления не было, — внутри бэкапа. Откат их
# убирает, чтобы после отката не оставался код неудачного выпуска.
ADDED_ENTRY = "__добавлены__.json"
# Какие файлы поставил прошлый выпуск. Файл, который из нового выпуска
# убрали, по нему находится и удаляется; всё, чего в списке нет (своё у
# человека), не трогается никогда.
MANIFEST = "release_files.json"
# Папка с бэкапами и журналом pip — внутри `data`, то есть рядом с личным,
# но в стороне от кода.
UPDATES_DIR = "updates"

# --- Что обновляем ---------------------------------------------------------

# Папки с кодом целиком.
CODE_DIRS = {"core", "ui", "web", "tools", "tests", "licenses"}
# Отдельные файлы в корне.
# `apps.default.json` — запасной список программ для свежей установки, и без
# него у человека, обновившего Трубу, телефон снова покажет пустой экран.
# `THIRD_PARTY.md` и `licenses/` — лицензии моделей: Higgs требует
# прикладывать их к коду, который её скачивает.
CODE_NAMES = {".env.example", "README.md", "LICENSE", "THIRD_PARTY.md", "ROADMAP.md",
              "apps.default.json"}
CODE_SUFFIXES = {".py", ".vbs", ".bat"}
# Папки, в которые не заходим никогда.
SKIP_DIRS = {".venv", "models", "vendor", "prompts", "data", "скрины",
             "coordination", "__pycache__", ".git", ".idea", ".vscode"}
# Файлы в корне, которых не должно быть в обновлении.
SKIP_FILES = {"settings.json", ".env", "apps.json", "latest_silero_models.yml"}

# Всё, что не перечислено выше, из архива просто игнорируется. Лишние локальные
# файлы не удаляются: они могут принадлежать пользователю.


def _разрешён(relative: str) -> bool:
    """Идёт ли файл из архива в установку."""
    части = PurePosixPath(relative).parts
    if not части or not части[0]:
        return False
    if части[0] in SKIP_DIRS or части[0] in SKIP_FILES:
        return False
    # `coordination` и `__pycache__` запрещены на любом уровне.
    if "coordination" in части or "__pycache__" in части:
        return False
    if len(части) == 1:
        имя = части[0]
        расширение = Path(имя).suffix.lower()
        if расширение in CODE_SUFFIXES:
            return True
        if расширение == ".txt" and имя.lower().startswith("requirements"):
            return True
        return имя in CODE_NAMES
    return части[0] in CODE_DIRS

def parse_version(text) -> tuple | None:
    """«v1.2.3», «1.2.3», «1.2» → тройка чисел. Мусор — None.

    Третья часть необязательна: `1.2` — это `1.2.0`, а не ошибка.
    """
    if not isinstance(text, str):
        return None
    чистая = text.strip()
    if чистая[:1] in ("v", "V"):
        чистая = чистая[1:]
    части = чистая.split(".")
    if not 1 < len(части) <= 3:
        return None
    числа = []
    for кусок in части:
        кусок = кусок.strip()
        if not кусок.isdigit():
            return None
        числа.append(int(кусок))
    while len(числа) < 3:
        числа.append(0)
    return tuple(числа)


def _ошибка(текст: str) -> dict:
    return {"ok": False, "state": "error", "error": текст, "current": config.VERSION}


def check(timeout: float = CHECK_TIMEOUT) -> dict:
    """Спрашивает GitHub о последнем выпуске. Всегда возвращает словарь.

    Состояния: `no_repo` (репозиторий ещё не опубликован — GitHub отдал
    404), `newer`, `latest`, `ahead` (локальная версия новее опубликованной,
    так бывает у разработчика) и `error`.
    """
    url = f"{API}/repos/{config.UPDATE_REPO}/releases/latest"
    import httpx

    try:
        with httpx.Client(timeout=timeout) as клиент:
            ответ = клиент.get(url, headers=HEADERS)
    except Exception:
        # Отказ сети, таймаут и VPN дают один вид ответа пульту.
        return _ошибка("нет связи с GitHub")

    код = getattr(ответ, "status_code", 0)
    if код == 404:
        # Репозиторий ещё не опубликован. Это не поломка, а «пока рано».
        return {"ok": True, "state": "no_repo", "current": config.VERSION}
    if код != 200:
        return _ошибка("нет связи с GitHub")

    try:
        данные = ответ.json()
    except Exception:
        return _ошибка("нет связи с GitHub")
    if not isinstance(данные, dict):
        return _ошибка("нет связи с GitHub")

    тег = данные.get("tag_name")
    последняя = parse_version(тег if isinstance(тег, str) else "")
    if последняя is None:
        return {"ok": False, "state": "error",
                "error": "непонятный номер версии на GitHub",
                "current": config.VERSION}

    здесь = parse_version(config.VERSION) or (0, 0, 0)
    if последняя > здесь:
        состояние = "newer"
    elif последняя == здесь:
        состояние = "latest"
    else:
        состояние = "ahead"

    заметки = данные.get("body")
    заметки = str(заметки) if isinstance(заметки, str) else ""
    имя = данные.get("name")
    published = данные.get("published_at")
    zip_url = данные.get("zipball_url")
    return {
        "ok": True,
        "state": состояние,
        "current": config.VERSION,
        "latest": ".".join(str(часть) for часть in последняя),
        "title": str(имя) if isinstance(имя, str) else "",
        "notes": заметки[:NOTES_LIMIT],
        "published": str(published) if isinstance(published, str) else "",
        "zip": str(zip_url) if isinstance(zip_url, str) else "",
    }


# --- Установка -------------------------------------------------------------


def _адрес_свой(url: str) -> bool:
    return str(url).startswith(ALLOWED_HOSTS)


def _скачать(url: str, куда: Path, timeout: float = 300.0) -> None:
    """Скачать архив выпуска, следуя редиректам, с потолком по размеру."""
    if not _адрес_свой(url):
        raise ValueError("непонятный адрес архива — установка отменена")
    import httpx

    всего = 0
    with httpx.Client(timeout=timeout, follow_redirects=True) as клиент:
        with клиент.stream("GET", url, headers=HEADERS) as ответ:
            ответ.raise_for_status()
            # Редирект мог увести не туда: адрес после него проверяем тоже.
            if not _адрес_свой(str(getattr(ответ, "url", url))):
                raise ValueError("непонятный адрес архива — установка отменена")
            with куда.open("wb") as поток:
                for кусок in ответ.iter_bytes():
                    всего += len(кусок)
                    if всего > MAX_ARCHIVE:
                        raise ValueError(
                            "архив больше 300 МБ — столько код не весит")
                    поток.write(кусок)
    if not всего:
        raise ValueError("архив пустой — установка отменена")


def _проверить_имя(имя: str) -> None:
    """Имя из архива: без `..`, без абсолютного пути, без `:`.

    Такой архив — отказ целиком, а не «пропустим этот файл»: подсовывать
    пути умеет всё, что скачано со стороны.
    """
    текст = str(имя).replace("\\", "/")
    if not текст or текст.startswith("/"):
        raise ValueError("в архиве недопустимое имя — установка отменена")
    if ":" in текст:
        raise ValueError("в архиве недопустимое имя — установка отменена")
    if ".." in PurePosixPath(текст).parts:
        raise ValueError("в архиве недопустимое имя — установка отменена")


def _распаковать(архив: Path, папка: Path) -> Path:
    """Распаковать и снять верхнюю папку GitHub (`владелец-имя-abc123/`)."""
    with zipfile.ZipFile(архив) as коробка:
        for имя in коробка.namelist():
            _проверить_имя(имя)
        коробка.extractall(папка)
    содержимое = list(папка.iterdir())
    if len(содержимое) == 1 and содержимое[0].is_dir():
        return содержимое[0]
    return папка


def _отобрать(основа: Path) -> dict:
    """Файлы выпуска, которые меняем: путь относительно проекта → файл.

    Обход через `os.walk` с обрезкой запрещённых папок: `rglob` залез бы
    внутрь `.venv` и `models`, если бы они вдруг попали в архив.
    """
    выбранные: dict = {}
    for folder, dirs, names in os.walk(основа):
        dirs[:] = [имя for имя in dirs if имя not in SKIP_DIRS]
        here = Path(folder)
        for имя in sorted(names):
            путь = here / имя
            if not путь.is_file():
                continue
            relative = путь.relative_to(основа).as_posix()
            if _разрешён(relative):
                выбранные[relative] = путь
    return выбранные


def _папка_обновлений(root: Path) -> Path:
    папка = root / "data" / UPDATES_DIR
    папка.mkdir(parents=True, exist_ok=True)
    return папка


def _бэкапы(root: Path) -> list:
    """Бэкапы, свежий первым: имя начинается с даты, по ней и порядок."""
    папка = root / "data" / UPDATES_DIR
    if not папка.is_dir():
        return []
    found = [p for p in папка.iterdir()
             if p.is_file() and p.name.startswith("backup_")
             and p.name.endswith(".zip")]
    return sorted(found, key=lambda p: p.name, reverse=True)


def _сделать_бэкап(root: Path, файлы, добавленные=()) -> Path:
    """Копия всего, что сейчас будет заменено или удалено.

    `добавленные` — файлы, которых до обновления не было: копировать нечего,
    но откат должен знать, что их надо убрать.
    """
    папка = _папка_обновлений(root)
    метка = datetime.now().strftime("%Y-%m-%d_%H%M")
    куда = папка / f"backup_{config.VERSION}_{метка}.zip"
    with zipfile.ZipFile(куда, "w", zipfile.ZIP_DEFLATED) as коробка:
        for relative in sorted(файлы):
            текущий = root / relative
            if текущий.is_file():
                коробка.write(текущий, relative)
        коробка.writestr(ADDED_ENTRY, json.dumps(sorted(добавленные), ensure_ascii=False))
    # Держим последние три. Уборка — только после того, как копия собралась.
    for лишний in _бэкапы(root)[KEEP_BACKUPS:]:
        try:
            лишний.unlink()
        except OSError:
            continue
    return куда


def rollback(backup: Path, root: Path | None = None) -> bool:
    """Вернуть файлы из бэкапа."""
    root = Path(root) if root is not None else config.ROOT
    backup = Path(backup)
    if not backup.is_file():
        return False
    try:
        with zipfile.ZipFile(backup) as коробка:
            имена = коробка.namelist()
            if ADDED_ENTRY in имена:
                try:
                    добавленные = json.loads(коробка.read(ADDED_ENTRY).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    добавленные = []
                _убрать(root, добавленные if isinstance(добавленные, list) else [])
            for имя in имена:
                if имя == ADDED_ENTRY:
                    continue
                _проверить_имя(имя)
                куда = root / имя
                куда.parent.mkdir(parents=True, exist_ok=True)
                with коробка.open(имя) as внутри, куда.open("wb") as поток:
                    shutil.copyfileobj(внутри, поток)
    except (OSError, zipfile.BadZipFile, ValueError):
        return False
    return True


def _применить(root: Path, файлы: dict) -> None:
    for relative, источник in sorted(файлы.items()):
        куда = root / relative
        куда.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(источник, куда)


def _убрать(root: Path, relatives) -> None:
    """Удаляет файлы кода по списку. Только из белого списка и только внутри
    папки Трубы — чужое и личное этим путём не удалить."""
    корень = root.resolve()
    for relative in relatives:
        relative = str(relative)
        try:
            _проверить_имя(relative)
        except ValueError:
            continue
        if not _разрешён(relative):
            continue
        путь = root / relative
        try:
            if путь.resolve().is_relative_to(корень) and путь.is_file():
                путь.unlink()
        except OSError:
            continue


def _манифест(root: Path) -> set | None:
    """Файлы, которые поставил прошлый выпуск. None — списка ещё нет."""
    путь = root / "data" / UPDATES_DIR / MANIFEST
    try:
        данные = json.loads(путь.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {str(x) for x in данные} if isinstance(данные, list) else None


def _записать_манифест(root: Path, relatives) -> None:
    try:
        (_папка_обновлений(root) / MANIFEST).write_text(
            json.dumps(sorted(relatives), ensure_ascii=False, indent=0), encoding="utf-8")
    except OSError:
        pass


def _прочитать(путь: Path):
    try:
        return путь.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _python(root: Path) -> Path:
    """Python окружения проекта; если его нет — тот, на котором работаем."""
    внутри = root / ".venv" / "Scripts" / "python.exe"
    return внутри if внутри.is_file() else Path(sys.executable)


def _окно_нет() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)



def _pip_install(root: Path) -> bool:
    """Поставить библиотеки из requirements.txt.

    Отдельная функция, чтобы её подменяли тесты: настоящий pip в тестах
    ходить в сеть не должен.
    """
    внутри = root / ".venv" / "Scripts" / "python.exe"
    if not внутри.is_file():
        return False
    журнал = _папка_обновлений(root) / "pip.log"
    try:
        with журнал.open("w", encoding="utf-8") as поток:
            итог = subprocess.run(
                [str(внутри), "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=str(root), stdout=поток, stderr=subprocess.STDOUT,
                timeout=1800, creationflags=_окно_нет())
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(итог, "returncode", 1) == 0


def _smoke(root: Path) -> bool:
    """Запускается ли новый код тем же Python, что и текущий пульт."""
    try:
        итог = subprocess.run(
            [str(_python(root)), "-c",
             "import config, core.brain, ui.web_runtime"],
            cwd=str(root), capture_output=True, timeout=300,
            creationflags=_окно_нет())
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(итог, "returncode", 1) == 0


def install(release: dict, root: Path | None = None, on_step=None) -> dict:
    """Поставить выпуск, который вернул `check` (состояние `newer`).

    `on_step("текст шага")` — если передан, пульт показывает текущий шаг.
    Возвращает словарь: успех — `{"ok": True, "from", "to",
    "restart": True}`, неудача — `{"ok": False, "error": …}`, а после
    отката ещё и `"rolled_back": True`.
    """
    root = Path(root) if root is not None else config.ROOT
    шаг = on_step if callable(on_step) else (lambda текст: None)

    # Рабочая папка разработки: обновлять её нельзя, иначе свежая работа
    # затёрлась бы опубликованной старой.
    if (root / "coordination").is_dir():
        return {"ok": False,
                "error": "это рабочая папка разработки — её обновлять нельзя"}

    url = str((release or {}).get("zip") or "")
    if not url:
        return {"ok": False, "error": "нечего ставить — нет адреса архива"}
    куда = str((release or {}).get("latest") or "?")
    откуда = config.VERSION

    with tempfile.TemporaryDirectory(prefix="truba-update-") as временная:
        временная = Path(временная)
        архив = временная / "update.zip"
        шаг("качаю обновление")
        try:
            _скачать(url, архив)
        except Exception as exc:
            return {"ok": False, "error": f"не скачалось: {exc}"}

        шаг("распаковываю")
        try:
            основа = _распаковать(архив, временная / "release")
        except (zipfile.BadZipFile, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"архив не распаковался: {exc}"}

        файлы = _отобрать(основа)
        if not файлы:
            return {"ok": False, "error": "в выпуске нет ни одного нашего файла"}

        # Прежние требования читаем ДО замены: с ними сравниваем новые.
        старые = _прочитать(root / "requirements.txt")

        # Новые файлы — чтобы откат их убрал; лишние — файлы прошлого выпуска,
        # которых в новом нет. Список прошлого выпуска есть только после
        # первого обновления: до него лишним не считается ничего.
        прошлый = _манифест(root) or set()
        добавленные = sorted(relative for relative in файлы
                             if not (root / relative).exists())
        лишние = sorted(relative for relative in прошлый - set(файлы)
                        if _разрешён(relative) and (root / relative).is_file())

        шаг("делаю копию прежних файлов")
        try:
            бэкап = _сделать_бэкап(root, list(файлы) + лишние, добавленные)
        except Exception as exc:
            return {"ok": False, "error": f"копия не собралась: {exc}"}

        шаг("ставлю новые файлы")
        try:
            _применить(root, файлы)
            _убрать(root, лишние)
        except Exception as exc:
            rollback(бэкап, root)
            return {"ok": False, "error": f"файлы не заменились: {exc}",
                    "rolled_back": True}

        новые = _прочитать(root / "requirements.txt")
        if новые is not None and новые != старые:
            if (root / ".venv" / "Scripts" / "python.exe").is_file():
                шаг("ставлю библиотеки")
                if not _pip_install(root):
                    rollback(бэкап, root)
                    return {"ok": False, "error": "библиотеки не поставились",
                            "rolled_back": True}
            else:
                шаг("библиотеки не проверены")

        шаг("проверяю запуск")
        if not _smoke(root):
            rollback(бэкап, root)
            return {"ok": False,
                    "error": "новый код не запускается — вернула прежний",
                    "rolled_back": True}

    # Запуск прошёл — теперь это список файлов этого выпуска: по нему
    # следующее обновление найдёт, что из выпуска убрали.
    _записать_манифест(root, файлы)
    try:
        (_папка_обновлений(root) / "last.json").write_text(json.dumps(
            {"from": откуда, "to": куда,
             "when": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    return {"ok": True, "from": откуда, "to": куда, "restart": True}


# --- Перезапуск ------------------------------------------------------------

# Сколько ждём освобождения порта, прежде чем поднимать пульт заново. Ровно
# столько же, сколько ждёт `ui/window.py` при запуске.
WAIT_PORT = 60.0


def _код_ждуна(root: Path) -> str:
    """Короткий код для отдельного процесса: дождаться порта и запустить.

    Пульт сейчас закроется, и ждать освобождения порта станет некому —
    поэтому ждёт отдельный процесс, которому закрытие не мешает.

    Порт — общий привычный (`core/instance.py`), такой же, как у всех копий.
    """
    from core import instance

    return (
        "import socket,subprocess,time\n"
        f"край=time.monotonic()+{WAIT_PORT}\n"
        "while time.monotonic()<край:\n"
        f"    s=socket.socket(); s.settimeout(0.5)\n"
        f"    занят=s.connect_ex(('127.0.0.1',{int(instance.порт())}))==0\n"
        "    s.close()\n"
        "    if not занят: break\n"
        "    time.sleep(0.5)\n"
        f"subprocess.Popen(['wscript', {str(root / 'Труба.vbs')!r}],"
        " creationflags=0x08000000)\n"
    )


def restart_later(root: Path | None = None) -> bool:
    """Запустить пульт заново, когда текущий освободит порт.

    Отдельный процесс, не дочерний и без окна.
    """
    root = Path(root) if root is not None else config.ROOT
    if not (root / "Труба.vbs").is_file():
        return False
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = Path(sys.executable)
    флаги = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | _окно_нет())
    try:
        subprocess.Popen(
            [str(pythonw), "-c", _код_ждуна(root)],
            cwd=str(root), close_fds=True, creationflags=флаги)
    except OSError:
        return False
    return True


# Проверка при старте пульта идёт не сразу: сначала пульт должен открыться.
START_DELAY = 20.0


def check_at_start(after: float = START_DELAY, on_result=None) -> dict:
    """Проверка обновлений при запуске пульта.

    Ждёт `after` секунд, чтобы пульт успел открыться, и отдаёт результат
    в `on_result` — в нём пульт его запоминает и пишет в журнал, если
    есть что сказать. Проверка ровно одна: повторный запрос в GitHub
    ничего не добавит и задержит ответ пульта.
    """
    time.sleep(max(0.0, float(after)))
    итог = check()
    if callable(on_result):
        on_result(итог)
    return итог

