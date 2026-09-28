r"""Скорость речи без изменения высоты голоса — растяжение времени (WSOLA).

Зачем: у Higgs нет ни скорости, ни метки темпа, а ползунок «Скорость речи» у
хозяина есть. 26 сентября он поднял его до 1.2, а она всё так же говорила
медленно — модель числа не понимает. Значит, темп задаём мы, уже на готовом
звуке.

Метод — WSOLA (waveform similarity overlap-add), а не phase vocoder: тот на
речи «металлит» и подчёркивает согласные, а здесь нужен тот же голос, только
бодрее.

Как устроено (окно 25 мс, шаг вдвое меньше, поиск ±10 мс — для 24 кГц это
600 отсчётов, 300 и ±240):

    исходник режется окнами, окна накладываются вдвое;
    выходная позиция окна — кратна шагу;
    входная (номер) растёт на шаг, умноженный на скорость: при 1.2 мы берём
    более крупные куски исходника на тот же выход, и речь звучит быстрее;
    перед склейкой каждого окна ищем в исходнике сдвиг в пределах ±10 мс,
    при котором кусок лучше всего коррелирует с тем, чем прошлый кусок
    продолжился бы на самом деле. Высота тона не трогается: волну не
    пересчитывают, а переставляют как есть;
    окна склеиваются с окном Ханна, у которого две половины в сумме дают
    ровно единицу, — амплитуда не гуляет.

Поток (`Stretcher`) нужен потому, что куски от Higgs идут по несколько сотен
миллисекунд, и на стыке кусков не должно быть ни щелчка, ни провала.
Состояние между кусками: остаток исходника (нужен и поиску, и эталону
следующего окна), номер взятого куска, позиция в исходнике, позиция склейки и
недоклеенный хвост выхода. Ничего не сбрасывается — куски, поданные по
одному, дают тот же звук, что и целиком.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# Окно 25 мс, шаг склейки — половина окна (перекрытие 50%), поиск сдвига ±10 мс.
FRAME_SECONDS = 0.025
SEARCH_SECONDS = 0.010

MIN_SPEED = 0.5
MAX_SPEED = 2.0
# Столько «единицы» считаем за единицу: ползунок приходит с шагом 0.05, и
# растягивать на 1% бессмысленно — только лишний хрип на стыках окон.
NEUTRAL = 0.02


def _speed(speed) -> float:
    """Скорость в допустимых границах; мусор и None — обычная единица."""
    try:
        value = float(speed)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(value):
        return 1.0
    return float(min(MAX_SPEED, max(MIN_SPEED, value)))


def _window(frame: int) -> np.ndarray:
    """Окно Ханна: две половины в сумме дают единицу — склейка без провалов."""
    n = np.arange(frame, dtype=np.float32)
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * n / frame)).astype(np.float32)


class Stretcher:
    """Растяжение времени потоком: куски на входе, звук на выходе.

    Состояние между кусками — в атрибутах с подчёркиванием; все они нужны
    именно для того, чтобы куски, поданные по одному, звучали как один
    цельный кусок (см. модуль).
    """

    def __init__(self, rate: int = 24_000, speed: float = 1.0):
        self.rate = int(rate)
        self.speed = _speed(speed)
        # При скорости «единица» не делаем ничего: незачем хрипеть на стыках.
        self.neutral = abs(self.speed - 1.0) <= NEUTRAL
        # Окно чётное, чтобы половинки Ханна складывались в единицу без остатка.
        self.hop = max(2, int(round(self.rate * FRAME_SECONDS / 2.0)))
        self.frame = self.hop * 2
        self.search = max(1, int(round(self.rate * SEARCH_SECONDS)))
        # Шаг по исходнику: чем больше скорость, тем больше исходника на тот
        # же кусок звука.
        self._step = self.hop * self.speed
        window = _window(self.frame)
        # У первого окна слева нет предыдущего, склеивать не с чем: там окно
        # открыто с единицы, иначе начало фразы тихнуло бы на 12 мс.
        first = np.ones(self.frame, dtype=np.float32)
        first[self.hop:] = window[self.hop:]
        self._win = window
        self._win_first = first
        self._in = np.zeros(0, dtype=np.float32)  # исходник, ещё не разобранный
        self._base = 0   # номер отсчёта _in[0] во всём потоке
        self._total = 0  # сколько отсчётов исходника пришло за всё время
        self._at = 0.0   # где искать следующее окно (в исходнике)
        self._was = None  # откуда взяли прошлое окно — эталон для поиска
        self._out = np.zeros(0, dtype=np.float32)   # недоклеенный выход
        self._weight = np.zeros(0, dtype=np.float32)  # сумма окон на нём же
        self._first = 0  # номер отсчёта _out[0] в результате
        self._at_out = 0  # куда клеим следующее окно (в результате)
        self._emitted = 0  # сколько отсчётов уже отдано в push()
        self._queue: list[np.ndarray] = []
        self._closed = False

    # --- Поток -------------------------------------------------------------

    def push(self, chunk) -> np.ndarray:
        """Очередной кусок исходника → кусок растянутого звука (может быть пуст).

        Пустой кусок и кусок короче окна — не поломка: они просто ждут в
        буфере, пока наберётся следующий.
        """
        if self._closed:
            return np.zeros(0, dtype=np.float32)
        data = np.asarray(chunk, dtype=np.float32)
        if self.neutral:
            return data
        if data.size:
            if self._in.size:
                self._in = np.concatenate([self._in, data])
            else:
                self._in = data
            self._total += data.size
            self._run(False)
        return self._collect()

    def flush(self) -> np.ndarray:
        """Исходник кончился — досылаем остаток, включая недоклеенный хвост."""
        if self._closed:
            return np.zeros(0, dtype=np.float32)
        if self.neutral:
            self._closed = True
            return np.zeros(0, dtype=np.float32)
        self._run(True)
        self._take(self._first + self._out.size)
        self._closed = True
        out = self._collect()
        # Последнее окно никем не перекрыто справа, и в нём до конца исходника
        # добавлено до целого окна тишины. Обрезаем этот хвост до честной
        # длины: «1.2» должно значить ровно 1/1.2, а не 1/1.2 плюс четверть
        # окна. Лишнего тут не больше окна (25 мс) и оно в самом конце фразы,
        # где речи и так нет. Считаем от всего выданного, а не от хвоста:
        # в потоке к этому моменту почти всё уже отдано кусками.
        excess = self._emitted + out.size - int(round(self._total / self.speed))
        return out[:out.size - excess] if excess > 0 else out

    # --- Внутри ------------------------------------------------------------

    def _run(self, final: bool) -> None:
        """Клеим окна, пока хватает исходника.

        В потоке ждём следующих кусков: без данных справа от текущей позиции
        поиск сдвига ответил бы иначе, чем ответил бы на следующем куске, и на
        стыке кусков вылез бы щелчок. Задержка — 35 мс, на слух мёртвая.
        """
        while True:
            at = int(round(self._at))
            if at >= self._total:
                return
            # Эталон следующего окна — то, чем прошлое продолжилось бы само.
            ref = at - self.search if self._was is None else self._was + self.hop
            need = max(at + self.search, ref) + self.frame
            if not final and need > self._total:
                return
            start = self._pick(at, ref)
            self._place(start)
            self._was = start
            self._at = at + self._step
            # Дальше исходник левее этой отметки не понадобится: поиску сдвига
            # нужно (at + step) ± search, эталону — start + hop.
            nxt = int(round(self._at))
            self._trim(min(nxt - self.search, start + self.hop))

    def _pick(self, at: int, ref: int) -> int:
        """Откуда взять окно: как можно ближе к нужному, но похожее на прошлое."""
        last = self._total - self.frame
        if last < self._base:
            return self._base  # хвост короче окна — склеим что есть
        low = max(self._base, at - self.search)
        high = min(at + self.search, last)
        if self._was is None or high < low:
            return min(max(at, self._base), last)
        wanted = self._piece(ref, self.frame)
        off = low - self._base
        region = self._in[off: off + (high - low) + self.frame]
        # Все сдвиги разом: сдвиг k — это кусок region[k : k + frame].
        view = sliding_window_view(region, self.frame)
        corr = view @ wanted
        energy = np.einsum("ij,ij->i", view, view, dtype=np.float64)
        scale = np.sqrt(energy) * float(np.linalg.norm(wanted))
        return low + int(np.argmax(corr / np.maximum(scale, 1e-9)))

    def _piece(self, start: int, count: int) -> np.ndarray:
        """count отсчётов исходника с позиции start; за концом — тишина."""
        off = start - self._base
        piece = self._in[off: off + count]
        if piece.size < count:
            piece = np.concatenate(
                [piece, np.zeros(count - piece.size, dtype=np.float32)]
            )
        return piece

    def _place(self, start: int) -> None:
        """Накопить окно в выход и наклеить его с окном Ханна."""
        at = self._at_out - self._first
        need = at + self.frame
        if need > self._out.size:
            pad = need - self._out.size
            self._out = np.concatenate([self._out, np.zeros(pad, dtype=np.float32)])
            self._weight = np.concatenate(
                [self._weight, np.zeros(pad, dtype=np.float32)]
            )
        window = self._win_first if self._was is None else self._win
        self._out[at: need] += self._piece(start, self.frame) * window
        self._weight[at: need] += window
        self._at_out += self.hop
        # Всё левее _at_out покрыто окнами целиком и больше не изменится.
        self._take(self._at_out)

    def _take(self, until: int) -> None:
        """Передать готовые отсчёты результата, разделив их на сумму окон.

        На краях (первое окно, последнее окно) сумма окон меньше единицы, и
        без деления фраза начиналась бы и кончалась бы тише середины.
        """
        count = min(until - self._first, self._out.size)
        if count <= 0:
            return
        weight = self._weight[:count]
        self._queue.append(
            np.divide(
                self._out[:count], weight,
                out=np.zeros(count, dtype=np.float32), where=weight > 1e-6,
            )
        )
        self._out = self._out[count:]
        self._weight = self._weight[count:]
        self._first += count

    def _trim(self, keep: int) -> None:
        """Выбросить из буфера исходник, который больше ни на что не нужен."""
        if keep > self._base:
            self._in = self._in[keep - self._base:]
            self._base = keep

    def _collect(self) -> np.ndarray:
        if not self._queue:
            return np.zeros(0, dtype=np.float32)
        parts = self._queue
        self._queue = []
        out = parts[0] if len(parts) == 1 else np.concatenate(parts)
        self._emitted += out.size
        return out


def stretch(wave, rate: int = 24_000, speed: float = 1.0) -> np.ndarray:
    """Растянуть весь сигнал разом. Моно, на выходе float32.

    speed — во сколько раз быстрее звучит: 1.2 — на 20% короче, а тон тот же
    самый. Диапазон 0.5–2.0, около единицы сигнал не трогаем вовсе.
    """
    value = _speed(speed)
    if abs(value - 1.0) <= NEUTRAL:
        return wave
    data = np.asarray(wave, dtype=np.float32)
    if data.size == 0:
        return data
    stretcher = Stretcher(rate, value)
    head = stretcher.push(data)
    tail = stretcher.flush()
    if not head.size:
        return tail
    return head if not tail.size else np.concatenate([head, tail])
