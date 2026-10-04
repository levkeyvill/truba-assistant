r"""Проактивность: когда Труба заговаривает сама.

Отдельный модуль без звука и без сети, потому что решать тут нечего: все
входы приходят явными числами, и каждое «нельзя» проверяется тестом.

Логика в трёх слоях:

* `Schedule` — когда в следующий раз: база по частоте, разброс и отступ
  за отсутствие ответа;
* `should_speak()` — можно ли сейчас: явные входы и причина отказа;
* `prompt()` / `mark()` — текст для модели и пометка для истории,
  `idle_seconds()` — человек за компом или отошёл.

Частота приходит настройкой и меняется на ходу, поэтому всё читается
из неё на каждый заход: голос перезапускать не нужно.
"""

import ctypes
import random
from ctypes import wintypes

# Частоты и база — минуты тишины до захода. «никогда» захода не даёт.
FREQUENCIES = {
    "never": None,
    "rare": 60.0,
    "sometimes": 25.0,
    "often": 10.0,
}
# Разброс срока: база × случайное число. Без него выходило бы «как по
# часам», и второй заход приходил бы минута в минуту.
SPREAD = (0.7, 1.4)
# Насколько растёт срок, если на заход не ответили: 1 → 2 → 4. Дальше не
# растёт: после трёх молчаний она заговорила бы раз в сутки.
MAX_BACKOFF = 4
# После двух минут без ввода считаем, что человек отошёл.
OWNER_AWAKE_LIMIT = 120.0
# Сколько секунд после последнего звука из Discord и Telegram заход не
# делаем: идёт созвон, лезть в разговор друзей нельзя.
CALL_GUARD = 60.0
# Вероятность глянуть на экран в этот раз.
LOOK_CHANCE = 0.4
# Как часто сторож будит и спрашивает, пора ли (секунды).
TICK = 15.0

NUDGE = (
    "Хозяин молчит {minutes} мин, сидит за компом. Заговори с ним первой — "
    "одна-две короткие живые фразы, по-своему: вспомни тему прошлого "
    "разговора, спроси, как дела и чем занят, поделись мыслью по его "
    "интересам из памяти или подколи. Не спрашивай, чем помочь. "
    "Не повторяй свои прошлые заходы."
)
NUDGE_SCREEN = (
    " Вот его экран прямо сейчас: прокомментируй или подколи по "
    "увиденному. Если там личное — переписка, банк, пароли, почта — "
    "экран не описывай, заговори о другом."
)
# Пометка в истории вместо его реплики: её он не говорил.
FIRST_MARK = "(хозяин молчал {minutes} мин — Труба заговорила первой)"


# --- Срок -------------------------------------------------------------------

def is_frequency(name) -> bool:
    """Такой частоты вообще бывает (для проверки настроек)."""
    return name in FREQUENCIES


def base_seconds(frequency) -> float | None:
    """База в секундах по частоте. None — не заговаривать никогда."""
    minutes = FREQUENCIES.get(frequency, None) if frequency else None
    return None if minutes is None else minutes * 60.0



class Schedule:
    """Когда в следующий раз заговорить: разброс и отступ за молчание.

    Срок хранится, пока `reschedule` не назначит следующий. Разговор сам
    по себе срок не двигает — это делает голосовой цикл, иначе проверка
    «пора ли» и назначение разошлись бы местами.
    """

    def __init__(self, rnd: random.Random | None = None):
        # Случайность через свой экземпляр: в тестах подменяется на
        # заготовку, а не через модульный `random`.
        self.rnd = rnd if rnd is not None else random.Random()
        # Отступ за молчание: 1, 2 или 4.
        self.backoff = 1
        # Когда назначен срок (time.monotonic). 0.0 — ещё не назначен.
        self.due_at = 0.0

    def due(self, frequency, now: float) -> bool:
        """Наступил ли срок при этой частоте."""
        if base_seconds(frequency) is None:
            return False
        if not self.due_at:
            return True
        return now >= self.due_at

    def next_interval(self, frequency) -> float:
        """Следующий срок: база × разброс × отступ."""
        base = base_seconds(frequency)
        if base is None:
            return 0.0
        return base * self.rnd.uniform(*SPREAD) * self.backoff

    def reschedule(self, frequency, now: float) -> float:
        """Назначает следующий срок от `now`. Отдаёт, через сколько."""
        span = self.next_interval(frequency)
        self.due_at = now + span if span else 0.0
        return span

    def missed(self) -> None:
        """Заход остался без ответа: следующий срок вдвое дальше."""
        self.backoff = min(self.backoff * 2, MAX_BACKOFF)

    def answered(self) -> None:
        """Ответил — отступ сбрасывается."""
        self.backoff = 1


# --- Присутствие за компьютером -------------------------------------------

def idle_seconds() -> float:
    """Сколько секунд никто не трогал мышь и клавиатуру.

    Windows знает это сама (`GetLastInputInfo`): счётчик общий на всю
    сессию и сбрасывается на любом вводе, включая прокрутку. Ошибка —
    ноль: при неизвестном вводе лучше промолчать, чем заговорить над
    спящим.
    """
    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(_LastInputInfo)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        # Оба счётчика — миллисекунды со включения Windows в 32 битах, и
        # раз в 49.7 суток они обнуляются. Поэтому 32-битный GetTickCount и
        # разность по модулю 2**32, а не GetTickCount64: без restype тот
        # вдобавок обрезается до знакового int, и через 25 суток без
        # перезагрузки отсутствие ввода выглядело бы постоянным.
        tick = ctypes.windll.kernel32.GetTickCount
        tick.restype = wintypes.DWORD
        now = tick()
    except Exception:
        return 0.0
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


# --- Решение ---------------------------------------------------------------

def should_speak(
    frequency,
    voice_ready: bool,
    listen_mode: str,
    in_conversation: bool,
    speaking: bool,
    echoing: bool,
    cloud_down: bool,
    owner_idle: float,
    call_sounds: bool,
    silence: float,
    now: float,
    due_at: float,
    interval: float,
) -> str:
    """Можно ли сейчас заговорить. '' — можно, иначе причина отказа.

    Порядок — от верхнего условия к нижнему: сначала выключенное, затем
    занятое, потом «человек тут», и только потом сроки. Отдаётся первая
    нарушенная причина, поэтому в журнале видно именно то, что остановило.

    Сроков тут два, и они разные. `due_at` — назначенный момент с учётом
    разброса: когда сторож вправе спросить. `interval` — честный срок по
    частоте и отступу: тишина с последнего события разговора должна быть
    не меньше него. Разброс умеет выдать и 0.7, то есть разбудить раньше
    срока, — вот его и держит вторая проверка.
    """
    if base_seconds(frequency) is None:
        return "выключено"
    if not voice_ready:
        return "голос не готов"
    if listen_mode == "off":
        return "слух заглушен"
    if in_conversation:
        return "разговор идёт"
    if speaking or echoing:
        return "говорит"
    if cloud_down:
        return "облако лежит"
    if owner_idle >= OWNER_AWAKE_LIMIT:
        return "хозяин отошёл"
    if call_sounds:
        return "идёт созвон"
    if now < due_at:
        return "срок не пришёл"
    if silence < interval:
        # Тишины с последнего события разговора не набралось: она
        # заговорила бы в паузу, которую сама же и создала.
        return "тишина короче срока"
    return ""


# --- Тексты ----------------------------------------------------------------

def prompt(minutes: int, look: bool) -> str:
    """Подсказка для модели. Не его реплика: в промпт идёт как просьба,
    а в историю кладётся пометка из `mark`."""
    return NUDGE.format(minutes=max(1, int(minutes))) + (NUDGE_SCREEN if look else "")


def mark(minutes: int) -> str:
    """Пометка, которая ложится в историю вместо его реплики."""
    return FIRST_MARK.format(minutes=max(1, int(minutes)))
