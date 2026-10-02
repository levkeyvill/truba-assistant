r"""Перенос настроек: один файл, который уносит Трубу на другой компьютер.

Зачем. Человек удалил Трубу, поставил заново — и снова ходит за ключом, заново
рисует кнопки телефона, заново надиктовывает образцы голоса. Здесь живёт файл
переноса: кнопка «Экспорт» собирает его в `Документы\Труба\`, а «Импорт»
разбирает и применяет на новой установке.

Что переносится — части из `ТАБЛИЦА`: у каждой имя, подпись для человека и
список файлов (пути от `config.ROOT`, `data/` — это `config.DATA_DIR`).
Отпечаток установки (`data/install_id.txt`), журналы и кеши сюда не входят:
они принадлежат этой копии, а не хозяину.

Разбор файла строгий: только наш ZIP с `transfer.json`, только пути из
белого списка, `..` и абсолютные пути — отказ всего файла, а не «пропустим этот
файл». Подсовывать пути умеет всё, что скачано со стороны.
"""

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import config
from core import safe_files

# Формат файла переноса. Поднимаем, когда меняется состав `transfer.json`;
# файл с чужим форматом — не наш, и применять его нельзя.
FORMAT = 1
# Заголовок архива: по нему разбираем, что это перенос Трубы.
ЗАПИСЬ = "transfer.json"
# Потолок на распакованное содержимое и на число файлов. Образцы голоса и
# история разговора весят мало, а вот распакованная бомба — это уже не перенос.
MAX_BYTES = 300 * 1024 * 1024
MAX_FILES = 2000
# Папка переноса в «Документах» и папка копий «до переноса» внутри `data/`.
EXPORT_FOLDER = "Труба"
BACKUP_FOLDER = "перенос"
BACKUP_LABEL = "до-переноса"

# Части переноса: имя для запросов, подпись для человека, файлы. Часть без
# единого файла в архив не попадает и в ответе не называется.
ТАБЛИЦА = {
    "settings": ("Настройки", ("settings.json",)),
    "apps": ("Программы и кнопки телефона", ("apps.json",)),
    "persona": ("Характер", ("prompts/persona.md",)),
    "memory": ("Память о тебе и история разговора",
               ("data/memory.json", "data/history.json")),
    "reminders": ("Напоминания", ("data/reminders.json",)),
    "voices": ("Образцы голоса", ("data/voiceprint.npz",)),
    "phone": ("Привязка телефона", ("data/phone_key.txt",)),
    "keys": ("Ключи доступа к облаку", (".env",)),
}
# Папка с образцами голоса. Голос — это пара «звук + текст».
VOICE_DIR = "voice"


def размер(байт) -> str:
    """`1500000` → «1,5 МБ». У нас в интерфейсе запятая, не точка."""
    try:
        число = float(байт)
    except (TypeError, ValueError):
        return "?"
    for единица, предел in (("ГБ", 1e9), ("МБ", 1e6), ("КБ", 1e3)):
        if число >= предел:
            return f"{число / предел:.1f}".replace(".", ",") + f" {единица}"
    return f"{int(число)} Б"


def части() -> list[dict]:
    """Все части с подписью — и то, что есть на диске прямо сейчас.

    Пульту это нужно для галочек экспорта: часть, которой на диске нет,
    отмечать бессмысленно, а хозяину сказать об этом надо.
    """
    строки = []
    for имя, (подпись, _файлы) in ТАБЛИЦА.items():
        свои = [относительный for относительный in файлы_части(имя)
                if _путь(относительный).is_file()]
        вес = 0
        for относительный in свои:
            try:
                вес += _путь(относительный).stat().st_size
            except OSError:
                pass
        строки.append({"id": имя, "title": подпись, "files": len(свои),
                       "bytes": вес, "size": размер(вес), "ready": bool(свои)})
    return строки
def файлы_части(имя: str) -> list[str]:
    """Белый список файлов части — относительные пути через слэш.

    `apps` и `voices` дополняются тем, что лежит на диске (выбранные значки и
    пары «звук + текст»), остальные части неизменны.
    """
    if имя not in ТАБЛИЦА:
        return []
    свои = list(ТАБЛИЦА[имя][1])
    if имя == "apps":
        свои += _значки()
    elif имя == "voices":
        свои += _голоса()
    return свои


def _значки() -> list[str]:
    """Выбранные руками картинки кнопок: `data/icons/custom/*`.

    Остальное содержимое `data/icons/` — кеш, он соберётся заново.
    """
    папка = config.DATA_DIR / "icons" / "custom"
    свои = []
    try:
        for значок in sorted(папка.iterdir()):
            if значок.is_file():
                свои.append(f"data/icons/custom/{значок.name}")
    except OSError:
        pass
    return свои


def _голоса() -> list[str]:
    """Пары `voice/<имя>.wav` + `voice/<имя>.txt`, как их видит `voice_prep`.

    Голос без текста — половина пары: на другой машине он всё равно не зазвучит.
    """
    папка = config.ROOT / VOICE_DIR
    свои = []
    try:
        звуки = sorted(папка.glob("*.wav"))
    except OSError:
        звуки = []
    for звук in звуки:
        if звук.with_suffix(".txt").is_file():
            свои.append(f"{VOICE_DIR}/{звук.name}")
            свои.append(f"{VOICE_DIR}/{звук.with_suffix('.txt').name}")
    return свои


def _часть_файла(имя: str) -> str:
    """Какая часть владеет путём из архива. Пусто — файл не наш."""
    свои = {
        "settings.json": "settings",
        "apps.json": "apps",
        "prompts/persona.md": "persona",
        "data/memory.json": "memory",
        "data/history.json": "memory",
        "data/reminders.json": "reminders",
        "data/voiceprint.npz": "voices",
        "data/phone_key.txt": "phone",
        ".env": "keys",
    }
    if имя in свои:
        return свои[имя]
    куски = имя.split("/")
    if len(куски) == 4 and куски[:3] == ["data", "icons", "custom"]:
        return "apps"
    if len(куски) == 2 and куски[0] == VOICE_DIR \
            and куски[1].endswith((".wav", ".txt")):
        return "voices"
    return ""


# JSON-файлы переноса и вид, который у каждого должен быть: настройки, память и
# история — словари, список программ и напоминания — списки. Битый файл после
# переноса тихо заменялся бы пустым, а человек ничего бы не заметил.
ВИДЫ_JSON = {
    "settings.json": dict,
    "apps.json": list,
    "data/memory.json": dict,
    "data/history.json": dict,
    "data/reminders.json": list,
}
# Строка `.env` вида `ИМЯ=значение` — больше в файл переноса не берём.
ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Само имя переменной: так ключ выглядит в `.env.example` и в `key_env`.
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _путь(относительный: str) -> Path:
    """Где файл лежит на самом деле: `data/` — это `config.DATA_DIR`."""
    куски = относительный.split("/")
    if куски[0] == "data":
        return config.DATA_DIR.joinpath(*куски[1:])
    return config.ROOT.joinpath(*куски)


def свои_части(parts) -> list[str]:
    """Имена частей из запроса — только наши, в порядке таблицы.

    Пустой список — не ошибка сама по себе: выбранного может не оказаться на
    диске (свежая установка, удалённый голос). Чужое имя — ошибка сразу.
    """
    прошено = []
    for имя in parts if isinstance(parts, (list, tuple)) else []:
        имя = str(имя)
        if имя not in ТАБЛИЦА:
            raise ValueError(f"неизвестная часть переноса: {имя}")
        if имя not in прошено:
            прошено.append(имя)
    return [имя for имя in ТАБЛИЦА if имя in прошено]
def известные_ключи() -> set:
    """Имена, которые в `.env` переносить можно.

    Ключи провайдеров из `config.PROVIDERS` и настройки из `.env.example`
    (там `LLM_PROVIDER`). Чужое имя в `.env` — своя пометка или пароль от
    стороннего сервиса — в перенос не идёт.
    """
    имена = {spec.get("key_env", "") for spec in config.PROVIDERS.values()}
    try:
        строки = (config.ROOT / ".env.example").read_text(
            encoding="utf-8").splitlines()
    except OSError:
        строки = []
    for строка in строки:
        чистая = строка.strip()
        if not чистая or чистая.startswith("#") or "=" not in чистая:
            continue
        имя = чистая.split("=", 1)[0].strip()
        if ENV_NAME.match(имя):
            имена.add(имя)
    return {имя for имя in имена if имя}


def env_для_переноса(текст: str) -> str:
    """Из `.env` — только строки `ИМЯ=значение` известных имён."""
    известные = известные_ключи()
    строки = []
    for строка in текст.splitlines():
        чистая = строка.strip()
        if not ENV_LINE.match(чистая):
            continue
        if чистая.split("=", 1)[0].strip() in известные:
            строки.append(чистая)
    return "\n".join(строки) + ("\n" if строки else "")


def слить_env(из_файла: str, текущий: str) -> str:
    """Наложить ключи из файла переноса на текущий `.env`.

    Из файла берутся только известные ключи, а строки, которых в файле нет
    (свои пометки, пароли сторонних сервисов), остаются как были: перенос не
    имеет права вычеркнуть то, чего в нём не было.
    """
    известные = известные_ключи()
    новые = {}
    for строка in из_файла.splitlines():
        чистая = строка.strip()
        if not ENV_LINE.match(чистая):
            continue
        имя, _, значение = чистая.partition("=")
        if имя.strip() in известные:
            новые[имя.strip()] = значение.strip()
    строки = текущий.splitlines()
    где = {}
    for номер, строка in enumerate(строки):
        чистая = строка.strip()
        имя = чистая.split("=", 1)[0].strip() if ENV_LINE.match(чистая) else ""
        if имя in новые:
            где[имя] = номер
    for имя, значение in новые.items():
        if имя in где:
            строки[где[имя]] = f"{имя}={значение}"
        else:
            строки.append(f"{имя}={значение}")
    return "\n".join(строки) + ("\n" if строки else "")
# --- Экспорт ----------------------------------------------------------------


def export(parts, folder=None) -> dict:
    """Собрать файл переноса из выбранных частей.

    Папка по умолчанию — `Документы\Труба\`: хозяин найдёт её сам, без знания
    того, куда Windows положила программу. Отсутствующие файлы (свежая
    установка, удалённый голос) пропускаются молча, а в ответе — что вошло.
    """
    return _собрать(parts, folder, "перенос")


def _собрать(parts, folder, метка) -> dict:
    """Общий код экспорта и резервной копии «до переноса»."""
    from core import notes

    свои = свои_части(parts)
    if not свои:
        raise ValueError("не выбрано, что переносить")
    куда = Path(folder) if folder else notes.documents_dir() / EXPORT_FOLDER
    куда.mkdir(parents=True, exist_ok=True)
    цель = куда / f"Труба-{метка}-{datetime.now():%Y-%m-%d_%H%M}.zip"
    временный = цель.with_name(цель.name + ".part")

    # Что реально есть: путь из архива → файл на диске.
    собрано = {}
    for часть in свои:
        for относительный in файлы_части(часть):
            файл = _путь(относительный)
            if относительный not in собрано and файл.is_file():
                собрано[относительный] = файл
    if not собрано:
        raise ValueError("выбранного на этом компьютере нет — переносить нечего")
    вошли = []
    for относительный in собрано:
        часть = _часть_файла(относительный)
        if часть not in вошли:
            вошли.append(часть)

    манифест = {
        "format": FORMAT,
        "version": config.VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "parts": вошли,
        "files": {относительный: собрано[относительный].stat().st_size
                  for относительный in собрано},
    }
    try:
        with zipfile.ZipFile(временный, "w", zipfile.ZIP_DEFLATED) as коробка:
            for относительный, файл in собрано.items():
                if _часть_файла(относительный) == "keys":
                    # `.env` в архив идёт уже починенным: только известные ключи.
                    текст = env_для_переноса(файл.read_text(encoding="utf-8"))
                    коробка.writestr(относительный, текст)
                else:
                    коробка.write(файл, относительный)
            коробка.writestr(ЗАПИСЬ, json.dumps(манифест, ensure_ascii=False,
                                                indent=2))
        _заменить(временный, цель)
    except BaseException:
        try:
            временный.unlink()
        except OSError:
            pass
        raise
    return {"ok": True, "path": str(цель), "parts": вошли,
            "bytes": цель.stat().st_size}
# --- Разбор файла -----------------------------------------------------------


def inspect(blob) -> dict:
    """Что внутри файла переноса: части, размер, версия и дата.

    Ничего не пишет на диск: пульт показывает это хозяину до применения.
    """
    разобранное = _разобрать(blob)
    манифест = разобранное["манифест"]
    содержимое = разобранное["файлы"]
    свод = []
    for имя, (подпись, _файлы) in ТАБЛИЦА.items():
        свои = sorted(путь for путь in содержимое if _часть_файла(путь) == имя)
        if not свои:
            continue
        вес = sum(len(содержимое[путь]) for путь in свои)
        свод.append({"id": имя, "title": подпись, "files": len(свои),
                     "bytes": вес, "size": размер(вес)})
    if not свод:
        raise ValueError("в файле нет ни одной знакомой части переноса")
    return {
        "ok": True,
        "version": str(манифест.get("version", "")),
        "created": str(манифест.get("created", "")),
        "parts": свод,
        "files": len(содержимое),
        "bytes": sum(len(данные) for данные in содержимое.values()),
    }


def _разобрать(blob) -> dict:
    """Проверить архив целиком и достать содержимое. Бросает `ValueError`.

    Отказ здесь — отказ всего файла: подсунутый архив не должен ни одну свою
    часть протащить в настройки.
    """
    if not isinstance(blob, (bytes, bytearray)):
        raise ValueError("файл не передан")
    данные = bytes(blob)
    if not данные:
        raise ValueError("файл пустой")
    if len(данные) > MAX_BYTES:
        raise ValueError(f"файл больше {MAX_BYTES // (1024 * 1024)} МБ — "
                         "это не перенос Трубы")
    try:
        коробка = zipfile.ZipFile(io.BytesIO(данные))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ValueError("это не файл переноса Трубы: нужен наш ZIP с "
                         "transfer.json")

    with коробка:
        записи = [запись for запись in коробка.infolist() if not запись.is_dir()]
        if len(записи) > MAX_FILES:
            raise ValueError(f"в файле слишком много файлов: {len(записи)}")
        потолок = MAX_BYTES // (1024 * 1024)
        if sum(max(0, запись.file_size) for запись in записи) > MAX_BYTES:
            raise ValueError(f"в файле больше {потолок} МБ после распаковки — "
                             "это не перенос Трубы")
        имена = [запись.filename for запись in записи]
        if ЗАПИСЬ not in имена:
            raise ValueError("это не файл переноса Трубы: нет transfer.json")
        for запись in записи:
            имя = запись.filename
            if имя == ЗАПИСЬ:
                continue
            # Обратный слэш, двоеточие, абсолютный путь и `..` — отказ файла.
            if "\\" in имя or ":" in имя or имя.startswith("/") \
                    or ".." in PurePosixPath(имя).parts:
                raise ValueError(f"в файле недопустимое имя: {имя}")
            if not _часть_файла(имя):
                raise ValueError(f"в файле лишнее: {имя}")
        try:
            манифест = json.loads(коробка.read(ЗАПИСЬ).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError, zipfile.BadZipFile):
            raise ValueError("в файле не разобрался transfer.json")
        if not isinstance(манифест, dict) or манифест.get("format") != FORMAT:
            raise ValueError("это перенос, но формат не наш: обнови Трубу")

        содержимое = {}
        прочитано = 0
        for запись in записи:
            if запись.filename == ЗАПИСЬ:
                continue
            кусок = коробка.read(запись.filename)
            прочитано += len(кусок)
            if прочитано > MAX_BYTES:
                raise ValueError("в файле больше положенного после распаковки")
            содержимое[запись.filename] = кусок

    for имя, вид in ВИДЫ_JSON.items():
        if имя not in содержимое:
            continue
        try:
            разобранный = json.loads(содержимое[имя].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"в файле битый {имя}")
        if not isinstance(разобранный, вид):
            ждали = "словарь" if вид is dict else "список"
            raise ValueError(f"в файле не тот {имя}: ждали {ждали}")
    if ".env" in содержимое:
        try:
            содержимое[".env"].decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("в файле ключи не читаются")
    return {"манифест": манифест, "файлы": содержимое}
# --- Применение -------------------------------------------------------------


def apply(blob, parts) -> dict:
    """Применить выбранные части файла переноса к этой установке.

    Сначала копия текущего состояния тех же частей — «до переноса» в
    `data/перенос/`: человек удалил Трубу и перепутал, что принёс, и вернуть
    прошлое он должен иметь возможность.
    """
    разобранное = _разобрать(blob)
    содержимое = разобранное["файлы"]
    свои = свои_части(parts)
    if not свои:
        raise ValueError("не выбрано, что переносить")
    нужные = sorted(путь for путь in содержимое
                    if _часть_файла(путь) in свои)
    if not нужные:
        raise ValueError("в файле нет ни одной из выбранных частей")

    копия = ""
    try:
        копия = _собрать(свои, config.DATA_DIR / BACKUP_FOLDER,
                         BACKUP_LABEL)["path"]
    except (OSError, ValueError):
        # Копии может не оказаться (переносить нечего, диск занят) — это не
        # повод отказывать: хозяин сам выбрал, что принести.
        копия = ""

    записано = []
    for относительный in нужные:
        куда = _путь(относительный)
        if _часть_файла(относительный) == "keys":
            из_файла = содержимое[относительный].decode("utf-8")
            try:
                свой = куда.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                свой = ""
            safe_files.write_text(куда, слить_env(из_файла, свой))
        else:
            _записать(куда, содержимое[относительный])
        записано.append(относительный)

    if "settings.json" in записано:
        _проверить_settings(_путь("settings.json"))
    _закрыть_мастер(_путь("settings.json"))

    вошли = []
    for относительный in записано:
        часть = _часть_файла(относительный)
        if часть not in вошли:
            вошли.append(часть)
    return {"ok": True, "parts": вошли, "files": len(записано),
            "bytes": sum(len(содержимое[путь]) for путь in записано),
            "backup": копия}


def _записать(путь: Path, данные: bytes) -> None:
    """Записать файл целиком или не тронуть его вовсе (как `core/safe_files.py`)."""
    путь.parent.mkdir(parents=True, exist_ok=True)
    частичный = путь.with_name(путь.name + ".part")
    try:
        with open(частичный, "wb") as поток:
            поток.write(данные)
            поток.flush()
            os.fsync(поток.fileno())
        _заменить(частичный, путь)
    except BaseException:
        try:
            частичный.unlink()
        except OSError:
            pass
        raise


def _заменить(частичный: Path, цель: Path) -> None:
    """`os.replace` с повторами: антивирус держит только что записанный файл."""
    for попытка in range(safe_files.REPLACE_TRIES):
        try:
            os.replace(частичный, цель)
            return
        except PermissionError:
            if попытка == safe_files.REPLACE_TRIES - 1:
                raise
            time.sleep(safe_files.REPLACE_PAUSE)


def _проверить_settings(путь: Path) -> None:
    """Мягкая проверка, как при старте: пульт обязан уметь прочитать файл.

    `load_settings` сам уводит битый файл в сторону и подставляет значения по
    умолчанию, поэтому падать он не должен. А если вдруг — значит настройки из
    переноса писать нельзя, и хозяину надо сказать об этом прямо.
    """
    from core import settings

    было = settings.SETTINGS_PATH
    settings.SETTINGS_PATH = путь
    try:
        settings.load_settings()
    except Exception as exc:
        raise ValueError(f"настройки из файла не подошли: {exc}")
    finally:
        settings.SETTINGS_PATH = было


def _закрыть_мастер(путь: Path) -> None:
    """Мастер первого запуска после переноса не нужен: настройки уже настроены."""
    try:
        данные = json.loads(путь.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        данные = {}
    if not isinstance(данные, dict):
        данные = {}
    if данные.get("first_run_done") is True:
        return
    данные["first_run_done"] = True
    _записать(путь, json.dumps(данные, ensure_ascii=False,
                                indent=2).encode("utf-8"))


# --- Показать файл в проводнике ---------------------------------------------


def reveal(путь) -> dict:
    """Показать файл переноса в проводнике — только из папок переноса.

    Путь из запроса не должен открывать что угодно: показываем лишь то, что
    Труба сама положила в `Документы\Труба\` или в `data/перенос`.
    """
    from core import notes

    цель = Path(str(путь or "")).resolve()
    for папка in (notes.documents_dir() / EXPORT_FOLDER,
                  config.DATA_DIR / BACKUP_FOLDER):
        try:
            цель.relative_to(папка.resolve())
        except ValueError:
            continue
        if not цель.is_file():
            raise FileNotFoundError(f"файла нет: {цель.name}")
        import subprocess

        subprocess.Popen(["explorer", f"/select,{цель}"])
        return {"ok": True, "path": str(цель)}
    raise ValueError("показать можно только файл из папки «Труба» или из "
                     f"data\\{BACKUP_FOLDER}")
