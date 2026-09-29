r"""Долговременная память: что она знает о хозяине между запусками.

Раньше памяти было ровно две штуки, и обе неживые: `prompts/about_user.md`,
который правится руками, и два десятка последних реплик в оперативке —
они умирали вместе с процессом. Человек рассказывал про себя, а назавтра
она об этом не помнила.

Здесь два разных хранилища, и путать их нельзя:

    data/history.json   последние реплики разговора, дословно.
                        Нужна, чтобы после перезапуска разговор
                        продолжился, а не начался с чистого листа.

    data/memory.json    факты о хозяине, выжимка. Живёт долго,
                        подмешивается в каждый запрос.

Факты выписывает сама модель отдельным дешёвым запросом, когда разговор
закончился. Человек их видит и правит кнопкой в пульте — иначе в памяти
однажды заведётся чушь, и выковырять её будет нечем.

Второй заход (25 сентября): память умеет не только добавлять.

    add      новый факт
    update   факт изменился или сливается с похожим: «взял 5070 Ti» →
             «продал 5070 Ti, теперь 4070»; «играет в Тарков» и «любит
             Escape from Tarkov» — одна строка, а не две
    remove   факт больше не верен

У факта есть важность 1–3: когда фактов больше, чем влезает в промпт,
туда идут важные, а не последние. Забытое и заменённое не стирается, а
уходит в архив `forgotten` того же файла — модель может ошибиться, и
тогда старое можно вернуть. За один разбор она забывает не больше
MAX_REMOVALS фактов: страховка от того, что однажды она сочтёт
устаревшим всё сразу.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime

import config
from core import safe_files

HISTORY_PATH = config.DATA_DIR / "history.json"
MEMORY_PATH = config.DATA_DIR / "memory.json"

# Сколько фактов уходит в промпт. Больше — дороже каждый запрос и выше
# шанс, что модель утонет в мелочах вместо того, чтобы говорить.
FACTS_IN_PROMPT = 60
# Длиннее этого факт не факт, а пересказ разговора.
MAX_FACT_CHARS = 160
# Важность: 3 — главное о нём, 2 — полезное, 1 — мелочь.
DEFAULT_WEIGHT = 2
# Сколько фактов можно забыть за один разбор разговора.
MAX_REMOVALS = 3
# Сколько забытого держим в архиве.
ARCHIVE_LIMIT = 200

# Один замок на всё, что читает память и записывает её обратно: разбор
# разговора (отдельным потоком, их может быть два подряд), ручная правка в
# пульте и «Забыть всё». Без него одна запись молча затирала другую (29.09,
# проверка кода). RLock — `apply_changes` зовёт `save_facts` под тем же замком.
_LOCK = threading.RLock()


# --- Факты ---------------------------------------------------------------


def _key(text: str) -> str:
    """Ключ для сравнения фактов: без знаков, регистра и ё."""
    plain = re.sub(r"[^а-яa-z0-9\s]+", " ", text.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", plain).strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def _clean(text) -> str:
    return " ".join(str(text or "").split())[:MAX_FACT_CHARS].strip(" -–—•*")


def _weight(value, default: int = DEFAULT_WEIGHT) -> int:
    try:
        return min(3, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def _load() -> dict:
    if not MEMORY_PATH.exists():
        return {}
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Битый файл — в сторону: иначе первый же разбор разговора записал бы
        # поверх пустую память, и старые факты пропали бы насовсем.
        safe_files.quarantine(MEMORY_PATH)
        return {}
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def load_facts() -> list[dict]:
    facts = _load().get("facts", [])
    return [f for f in facts if isinstance(f, dict) and f.get("text")]


def load_forgotten() -> list[dict]:
    items = _load().get("forgotten", [])
    return [f for f in items if isinstance(f, dict) and f.get("text")]


def save_facts(facts: list[dict], forgotten: list[dict] | None = None) -> None:
    """Пишет факты. Архив забытого сохраняется, если не передан новый."""
    with _LOCK:
        if forgotten is None:
            forgotten = load_forgotten()
        safe_files.write_text(MEMORY_PATH, json.dumps(
            {
                "updated": datetime.now().isoformat(timespec="seconds"),
                "facts": facts,
                "forgotten": forgotten[-ARCHIVE_LIMIT:],
            },
            ensure_ascii=False,
            indent=2,
        ))


def add_facts(texts: list[str]) -> list[str]:
    """Добавляет новые факты. Возвращает те, что легли впервые."""
    changes = apply_changes([{"op": "add", "text": t} for t in texts])
    return changes.get("added", [])


def numbered(facts: list[dict] | None = None) -> str:
    """Память с номерами — для модели, чтобы она могла сослаться на строку.

    `facts` — снимок, который потом уйдёт в `apply_changes(…, seen=…)`: по
    нему номера модели и переводятся обратно в факты.
    """
    facts = load_facts() if facts is None else facts
    return "\n".join(
        f"{i}. {f['text']} [важность {_weight(f.get('weight'))}]"
        for i, f in enumerate(facts, 1)
    )


def _renumber(ops: list, seen: list[str], facts: list[dict]) -> list:
    """Номера модели — к нынешней памяти.

    Модель видела снимок `seen` (тексты по порядку), а пока она думала,
    память могли поправить в пульте или записать соседним разбором: номер 3
    тогда указывал бы на чужой факт. Поэтому номер → текст из снимка → тот
    же факт в нынешней памяти. Такого уже нет — правка пропускается.
    """
    где = {}
    for index, fact in enumerate(facts):
        где.setdefault(_key(fact["text"]), index)
    out = []
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict) or "id" not in op:
            out.append(op)
            continue
        try:
            было = seen[int(op.get("id")) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        сейчас = где.get(_key(было))
        if сейчас is None:
            continue
        out.append({**op, "id": сейчас + 1})
    return out


def apply_changes(ops: list[dict], seen: list[str] | None = None) -> dict:
    """Применяет правки модели к памяти. Возвращает, что изменилось.

    Номера — из `numbered()` на момент разбора: 1 — первая строка. Если
    передан снимок `seen`, номера сверяются по нему (см. `_renumber`).
    Непонятные и повторные правки молча пропускаются: память дороже,
    чем аккуратность ответа модели.
    """
    with _LOCK:
        facts = load_facts()
        if seen is not None:
            ops = _renumber(ops, seen, facts)
        return _apply(ops, facts)


def _apply(ops: list[dict], facts: list[dict]) -> dict:
    forgotten = load_forgotten()
    keys = {_key(f["text"]) for f in facts}
    touched: set[int] = set()
    drop: set[int] = set()
    result: dict[str, list] = {"added": [], "updated": [], "removed": []}

    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op", "")).lower()
        index = None
        if kind in ("update", "remove"):
            try:
                index = int(op.get("id")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= index < len(facts) or index in touched:
                continue

        if kind == "add":
            text = _clean(op.get("text"))
            key = _key(text)
            if len(text) < 4 or not key or key in keys:
                continue
            keys.add(key)
            facts.append({"text": text, "added": _now(), "weight": _weight(op.get("weight"))})
            result["added"].append(text)

        elif kind == "update":
            old = facts[index]
            text = _clean(op.get("text")) or old["text"]
            weight = _weight(op.get("weight"), _weight(old.get("weight")))
            key = _key(text)
            touched.add(index)
            if key != _key(old["text"]) and key in keys:
                # Такой факт уже есть отдельной строкой — значит, это слияние:
                # старую строку в архив, новую не дублируем.
                forgotten.append({**old, "forgotten": _now(), "why": f"слита с «{text}»"})
                drop.add(index)
                keys.discard(_key(old["text"]))
                result["removed"].append(old["text"])
                continue
            if key == _key(old["text"]) and weight == _weight(old.get("weight")):
                continue
            if key != _key(old["text"]):
                forgotten.append({**old, "forgotten": _now(), "why": f"заменена на «{text}»"})
                keys.discard(_key(old["text"]))
                keys.add(key)
                result["updated"].append([old["text"], text])
            facts[index] = {**old, "text": text, "weight": weight, "changed": _now()}

        elif kind == "remove":
            if len(result["removed"]) >= MAX_REMOVALS:
                continue
            old = facts[index]
            touched.add(index)
            drop.add(index)
            why = _clean(op.get("why")) or "больше не верно"
            forgotten.append({**old, "forgotten": _now(), "why": why})
            keys.discard(_key(old["text"]))
            result["removed"].append(old["text"])

    changed = {k: v for k, v in result.items() if v}
    # Смена одной только важности — тоже правка, её надо записать.
    if changed or touched - drop:
        save_facts([f for i, f in enumerate(facts) if i not in drop], forgotten)
    return changed


def as_text() -> str:
    """Память одним куском — для правки руками в пульте."""
    return "\n".join(f["text"] for f in load_facts())


def _lines(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip(" -–—•*\t")
        if line:
            out.append(line)
    return out


def from_text(text: str, base: str | None = None) -> None:
    """Принимает поправленный человеком список. Пустые строки выкидывает.

    Даты и важность у уцелевших строк сохраняем: человек правит одну
    строчку, а не переписывает память заново. Стёртое рукой — это его
    решение, в архив не кладём.

    `base` — текст памяти, каким человек его открыл. Факты, которые разбор
    разговора дописал уже после этого, он не видел и стереть не мог — они
    остаются (29.09: иначе правка давно открытой страницы молча стирала
    свежее).
    """
    with _LOCK:
        current = load_facts()
        was = {_key(f["text"]): f for f in current}
        facts = []
        written = set()
        for line in _lines(text):
            key = _key(line)
            old = was.get(key)
            facts.append(
                old
                if old
                else {"text": line[:MAX_FACT_CHARS], "added": _now(), "weight": DEFAULT_WEIGHT}
            )
            written.add(key)
        if base is not None:
            seen = {_key(line) for line in _lines(base)}
            for fact in current:
                key = _key(fact["text"])
                if key not in seen and key not in written:
                    facts.append(fact)
                    written.add(key)
        save_facts(facts)


def as_prompt() -> str:
    """Кусок системного промпта с тем, что она помнит.

    Влезает всё — идёт всё. Не влезает — важные вперёд, при равной
    важности свежие; порядок строк в промпте остаётся прежним.
    """
    facts = load_facts()
    if not facts:
        return ""
    if len(facts) > FACTS_IN_PROMPT:
        ranked = sorted(
            range(len(facts)),
            key=lambda i: (_weight(facts[i].get("weight")), i),
            reverse=True,
        )
        keep = set(ranked[:FACTS_IN_PROMPT])
        facts = [f for i, f in enumerate(facts) if i in keep]
    lines = ["# Что ты помнишь о нём", ""]
    lines += [f"- {f['text']}" for f in facts]
    lines.append("")
    lines.append(
        "Это ты знаешь из прошлых разговоров. Пользуйся этим как своей "
        "памятью — но не зачитывай список вслух и не хвастайся тем, "
        "что помнишь."
    )
    return "\n".join(lines)


def forget_all() -> None:
    """«Забыть всё» — по кнопке хозяина, вместе с архивом."""
    save_facts([], [])


# --- История разговора ---------------------------------------------------


def load_history(limit: int) -> list[dict]:
    """Последние реплики прошлого разговора."""
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    turns = [
        turn
        for turn in data.get("turns", [])
        if isinstance(turn, dict)
        and turn.get("role") in ("user", "assistant")
        and isinstance(turn.get("content"), str)
    ]
    return turns[-limit:] if limit else turns


def save_history(turns) -> None:
    # Пишется после каждого ответа — чаще всех файлов, и оборванная запись
    # тут вероятнее всего.
    safe_files.write_text(HISTORY_PATH, json.dumps(
        {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "turns": list(turns),
        },
        ensure_ascii=False,
        indent=2,
    ))


def clear_history() -> None:
    save_history([])
