r"""Таймеры и напоминания: где они лежат, когда звонят и кто звонит.

Хозяин говорит «напомни через двадцать минут вытащить пиццу» — и **время
понимает модель**, а не код. В каждом запросе у неё есть часы (системное
сообщение), и в инструмент она передаёт готовые секунды или готовый момент.
Здесь поэтому нет ни одного разборщика «через полчаса» и ни одной попытки
угадать: этот модуль только хранит, проверяет и будит.

Одна запись — одна строка в `data/reminders.json`:

    {"id": "r1", "due": "2026-09-30T17:00:00+05:00",
     "created": "2026-09-30T16:40:00+05:00", "text": "вытащить пиццу",
     "say": "", "kind": "reminder", "minutes": 0}

`say` — фраза, которую она произнесёт, когда позовёт; пишет модель в своём
характере. Пустая строка — говорит `phrase`: «Напоминаю: …», а у таймера
«Таймер на пять минут вышел.».

Время везде с часовым поясом: часы компьютера — UTC+5, а не Москва (см.
ГРАБЛИ про часы), и `datetime.fromisoformat` без пояса терял бы это молча.

Путь берётся переменной модуля `PATH`, а не константой, зашитой в коде:
тесты подменяют её на временную папку (см. `tests/test_data_guard.py`), и
живая `data/` остаётся нетронутой.

`Alarm` — поток-будильник. Файл он **не держит открытым**: раз в секунду
смотрит только время изменения и читает диск, когда файл изменился (после
`add` и `cancel`). В простое это ноль чтений — ровно тот же довод, что в
ГРАБЛИ про onnxruntime, крутящий четыре ядра вхолостую.
"""

import json
import threading
from datetime import datetime, timedelta

import config

PATH = config.DATA_DIR / "reminders.json"

# Больше двадцати активных — это уже не напоминания, а забывчивость
# хозяина: список в пульте всё равно не покажут. Отказ честнее лишнего.
MAX_ACTIVE = 20
# Дальше месяца вперёд не ставим: «напомни через три месяца» — это не
# напоминание, а обещание, которое всё равно забудут.
MAX_DAYS = 30
# Виды записи. Разница только в словах при срабатывании.
KIND_TIMER = "timer"
KIND_REMINDER = "reminder"
KINDS = (KIND_TIMER, KIND_REMINDER)

# Насколько опоздание считается «пока меня не было» — с извинительной
# пометкой. Просто опоздавший таймер (компьютер был зависшим на полминуты)
# говорится как обычно, без неё.
MISSED_AFTER = 300.0

# Записи пишутся из двух мест: модель зовёт инструмент, а будильник в эту
# секунду забирает сработавшее. RLock, а не Lock: `Alarm.check` зовёт
# `_write` под тем же замком.
_LOCK = threading.RLock()
# Последний выданный номер id. Живёт только в процессе: с перезапуском
# нумерация продолжится с того, что уже лежит в файле (см. `_next_id`).
_LAST_ID = 0


def local_now() -> datetime:
    """Местное время с часовым поясом — то, в котором живёт хозяин."""
    return datetime.now().astimezone()


def parse(value) -> datetime:
    """Момент из строки ISO или из `datetime`. Без пояса — местный.

    Модель присылает местное время без пояса («в пять вечера» → `at`), и
    молча считать его чужим нельзя: пояс у часов компьютера один, у
    телефона другой, а напоминание «в семь утра» должно настать там, где
    хозяин.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("время не названо")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"не поняла время «{text}»") from None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment



def _read() -> list[dict]:
    """Все записи из файла. Битый и несуществующий файл — пустой список."""
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [one for one in data if isinstance(one, dict)]


def _write(items: list[dict]) -> None:
    """Запись через временный файл: оборванная запись не должна выглядеть
    как настоящее напоминание (как в `core/notes.py`)."""
    path = PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        partial.write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        partial.replace(path)
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _stamp():
    """Время изменения файла или None — для дешёвой проверки будильника."""
    try:
        return PATH.stat().st_mtime
    except OSError:
        return None


def _next_id(items: list[dict]) -> str:
    """Свободный короткий id: `r1`, `r2`…

    Номер никогда не идёт назад за жизнь процесса, даже если запись с
    большим id отменили: в пульте (часть B) такая строка может ещё висеть на
    экране, и повторный `r1` на другом напоминании отменил бы не то.
    """
    global _LAST_ID
    taken = set()
    for one in items:
        key = str(one.get("id", ""))
        if key.startswith("r") and key[1:].isdigit():
            taken.add(int(key[1:]))
    number = max(taken | {_LAST_ID}) + 1
    _LAST_ID = number
    return f"r{number}"


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def minutes_said(n: int, unit=("минуту", "минуты", "минут")) -> str:
    """«пять минут», «одну минуту», «двадцать одну минуту» — после «на».

    `for_speech` читает «1 минута» в именительном («одна минута»), а после
    «таймер на» нужен винительный.
    """
    from core import speech_text

    words = speech_text.cardinal(int(n), "f").split()
    if words and words[-1] == "одна":
        words[-1] = "одну"
    return " ".join(words) + " " + speech_text.plural(int(n), unit)


def seconds_said(n: int) -> str:
    return minutes_said(n, ("секунду", "секунды", "секунд"))


def when_said(due, now=None) -> str:
    """Когда сработает, словами: «в семнадцать часов ровно», «завтра в …»,
    «третьего октября в …» — день называем, если не сегодня."""
    from core import speech_text

    moment = parse(due)
    current = parse(now) if now is not None else local_now()
    time_words = speech_text.for_speech(moment.strftime("%H:%M"))
    days = (moment.date() - current.date()).days
    if days == 0:
        return f"в {time_words}"
    if days == 1:
        return f"завтра в {time_words}"
    if days == 2:
        return f"послезавтра в {time_words}"
    return f"{moment.day} {MONTHS[moment.month - 1]} в {time_words}"


def phrase(record: dict) -> str:
    """Фраза при срабатывании: своя из `say`, иначе собранная здесь.

    Свою фразу пишет модель — она звучит её голосом и в её характере.
    Запасная нужна, когда она пустая: молчать на «напомни через час» было
    бы хуже, чем сказать «Напоминаю: …» без её слов.
    """
    said = str(record.get("say") or "").strip()
    if said:
        return said
    if str(record.get("kind") or "") == KIND_TIMER:
        minutes = int(record.get("minutes") or 0)
        if minutes > 0:
            return f"Таймер на {minutes_said(minutes)} вышел."
        return "Таймер вышел."
    text = str(record.get("text") or "").strip()
    return f"Напоминаю: {text}." if text else "Напоминаю."


def add(due, text: str = "", say: str = "", kind: str = KIND_REMINDER,
        minutes: int = 0, now=None) -> dict:
    """Новое напоминание или таймер. Ответ — сама запись.

    `now` — подмена времени для тестов; в жизни это местное время с поясом.

    Отказ бросает `ValueError` с русским текстом: инструмент модели ловит
    его и отдаёт строкой `{"error": …}`, а та перескажет хозяину своими
    словами. Молча проглотить отказ нельзя — он должен дойти до человека
    («это время уже прошло»), иначе он будет ждать напоминания, которого
    не будет.
    """
    moment = parse(due)
    current = parse(now) if now is not None else local_now()
    if moment <= current:
        raise ValueError("это время уже прошло — скажи, на когда поставить")
    if moment > current + timedelta(days=MAX_DAYS):
        raise ValueError(
            f"дальше чем на {MAX_DAYS} дней не ставлю — так далеко я не помню")
    kind = str(kind or KIND_REMINDER)
    if kind not in KINDS:
        kind = KIND_REMINDER
    with _LOCK:
        items = _read()
        if len(items) >= MAX_ACTIVE:
            raise ValueError(
                f"уже стоит {MAX_ACTIVE} — сначала отмени что-нибудь")
        record = {
            "id": _next_id(items),
            "due": moment.isoformat(timespec="seconds"),
            "created": current.isoformat(timespec="seconds"),
            "text": str(text or "").strip(),
            "say": str(say or "").strip(),
            "kind": kind,
            "minutes": int(minutes or 0),
        }
        items.append(record)
        _write(items)
    return record


def cancel(what: str) -> list[dict]:
    """Отменяет по id или всё сразу. Ответ — что отменили.

    `what` — id записи или строка `all`. Отменённого в ответе нет, потому
    что сработать ему уже не когда: при следующем `pending` его не будет.
    """
    key = str(what or "").strip()
    with _LOCK:
        items = _read()
        if key in ("all", "все", "*"):
            gone = list(items)
            left: list[dict] = []
        else:
            gone = [one for one in items if str(one.get("id", "")) == key]
            left = [one for one in items if str(one.get("id", "")) != key]
        if gone:
            _write(left)
    return gone


def pending(now=None) -> list[dict]:
    """Все активные записи по времени: раньше — раньше."""
    items = _read()
    try:
        return sorted(items, key=lambda one: parse(one.get("due")))
    except ValueError:
        return sorted(items, key=lambda one: str(one.get("due", "")))


def due_now(now) -> list[dict]:
    """Что пора звать: время пришло, а запись ещё не сработала."""
    current = parse(now) if now is not None else local_now()
    out = []
    for one in _read():
        try:
            if parse(one.get("due")) <= current:
                out.append(one)
        except ValueError:
            continue  # мусор в due — не повод будить хозяина
    return out


class Alarm:
    """Будильник: раз в секунду смотрит, что пора, и зовёт `on_fire`.

    Живёт в пульте, а не в голосовом цикле (как `core/replay.py::Guard`):
    напоминание должно сработать и при выключенном голосе.

    `on_fire(запись, опоздание_в_секундах)` зовётся уже **после** того, как
    запись убрана из файла: повторно она не сработает, даже если пульт
    упадёт в ту же секунду. Сама `on_fire` наружу исключений не роняет —
    будильник от неё не должен умирать.

    Пропущенное за время, пока пульт был выключен, срабатывает один раз с
    опозданием в `late_seconds`; вызывающий сам различает «опоздала на
    полминуты» и «пока меня не было» (см. `MISSED_AFTER`). Копиться оно не
    может: всё сработавшее уходит из файла тем же проходом.
    """

    TICK = 1.0

    def __init__(self, on_fire, tick: float | None = None, now=local_now) -> None:
        self._on_fire = on_fire
        self._tick = float(tick) if tick else self.TICK
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Кеш прочитанного и время изменения файла: пока файл не трогали,
        # диск не читается вовсе.
        self._items: list[dict] | None = None
        self._stamp = None

    # --- Управление -------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="reminders-alarm")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.check()
            except Exception:
                continue  # будильник переживает любой сбой: иначе он умрёт навсегда

    # --- Чтение -----------------------------------------------------------

    def _read_changed(self) -> list[dict]:
        """Читает файл, только если он изменился с прошлого раза."""
        current = _stamp()
        if self._items is not None and current == self._stamp:
            return self._items
        self._stamp = current
        self._items = _read()
        return self._items

    def _forget(self) -> None:
        """Забыть кеш: следующая проверка пойдёт за файлом с диска."""
        self._items = None
        self._stamp = None

    # --- Срабатывание -----------------------------------------------------

    def check(self, now=None) -> list[tuple]:
        """Один проход: что пора — сработать. Ответ — список пар
        `(запись, опоздание)`.

        `now` — подмена времени для тестов. Проверяется время прихода, а не
        время срабатывания: сработавшее уходит из файла до вызова `on_fire`
        и потому второй раз не придёт.
        """
        moment = parse(now) if now is not None else self._now()
        with _LOCK:
            items = self._read_changed()
            due = sorted((one for one in items if _пора(one, moment)),
                         key=lambda one: str(one.get("due", "")))
            if not due:
                return []
            fired = {str(one.get("id", "")) for one in due}
            rest = [one for one in items if str(one.get("id", "")) not in fired]
            try:
                _write(rest)
            except OSError:
                # Файл не записать — помечаем ушедшим в этом кеше: иначе
                # следующая проверка позовёт их снова, и хозяин услышит одно
                # и то же дважды.
                self._items = rest
                self._stamp = _stamp()
        self._forget()
        out = []
        for record in due:
            try:
                late = max(0.0, (moment - parse(record.get("due"))).total_seconds())
            except ValueError:
                late = 0.0
            out.append((record, late))
            try:
                self._on_fire(record, late)
            except Exception:
                continue
        return out


def _пора(record: dict, moment: datetime) -> bool:
    """Время этой записи пришло. Мусор в `due` — не повод будить хозяина."""
    try:
        return parse(record.get("due")) <= moment
    except ValueError:
        return False

