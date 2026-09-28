r"""Уши: захват с микрофона и нарезка речи на фразы.

Поток с микрофона режется детектором речи Silero: пока человек говорит —
копим, замолчал — отдаём фразу целиком на распознавание.

Микрофон пишется на родной частоте звуковой карты и пересчитывается в 16 кГц
уже здесь: WASAPI на внешних картах отказывается отдавать чужую частоту.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import sounddevice as sd

import config

# Шаг детектора речи при 16 кГц — он обучен именно на таких кусках.
VAD_HOP = 512
VAD_CONTEXT = 64

# Речь началась, когда уверенность выше; закончилась — когда ниже.
# Разные пороги не дают детектору дёргаться на границе.
SPEECH_ON = 0.55
SPEECH_OFF = 0.35

# Сколько тишины считать концом фразы. Меньше — перебивает на паузах,
# больше — тормозит ответ.
#
# Было 0.7, и этого не хватало: человек делает паузу посреди мысли —
# подбирает слово, набирает воздух, — а она уже считает фразу законченной
# и лезет отвечать. Его слова: «я не успеваю договорить, она меня уже
# перебивает». Секунда с небольшим переживает обычную паузу в речи и
# добавляет к ответу меньше полусекунды.
SILENCE_TO_END = 1.15
# Короче этого — щелчок, стук по столу, кашель. Не считаем за речь.
MIN_PHRASE = 0.35
# Запас перед началом речи: детектор срабатывает с задержкой,
# без него съедается первый слог.
PREROLL = 0.35
# Сколько звука оставлять в очереди, когда она договорила. Человек часто
# отвечает встык, и без этого запаса начало его фразы пропадало.
TAIL_KEPT = 1.0

# Как часто сообщать телефону, что речь идёт. Десять раз в секунду — глаз
# не отличает, а сообщений за фразу получается немного: по WebSocket столько
# лишних сообщений только жгли бы связь с телефоном.
VOICE_EVERY = 0.1
# Громкость, при которой рамка считается «громко». Дальше она не растёт:
# иначе тихий голос и крик выглядели бы одинаково.
VOICE_FULL = 0.08


def voice_level(chunk: np.ndarray) -> float:
    """Громкость куска 0…1 для подсветки телефона.

    Кривая не линейная: тихий голос виден так же, как громкий, иначе
    телефон молчал бы в самый нужный момент.
    """
    if not len(chunk):
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
    return min(1.0, (rms / VOICE_FULL) ** 0.5)


class StreamingVad:
    """Silero VAD в потоковом режиме: кадр за кадром, с сохранением состояния."""

    def __init__(self):
        import onnx_asr

        from core import onnx_opts

        # Переиспользуем загрузчик onnx-asr, но работаем с сессией напрямую:
        # его собственный интерфейс рассчитан на готовые записи, а не на поток.
        # Один поток и без кручения: зовут каждые 32 мс, и с настройками по
        # умолчанию четыре потока крутились вхолостую всё время (onnx_opts).
        self._session = onnx_asr.load_vad(
            config.VAD_MODEL, sess_options=onnx_opts.quiet(threads=1))._model
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(VAD_CONTEXT, dtype=np.float32)

    def probability(self, chunk: np.ndarray) -> float:
        """Вероятность того, что в куске из 512 отсчётов есть речь."""
        frame = np.concatenate([self._context, chunk])[None, :].astype(np.float32)

        output, new_state = self._session.run(
            ["output", "stateN"],
            {"input": frame, "state": self._state, "sr": [config.SAMPLE_RATE]},
        )
        self._state = new_state
        self._context = chunk[-VAD_CONTEXT:]
        return float(output[0, 0])


@dataclass
class Listener:
    """Слушает микрофон и отдаёт законченные фразы."""

    device: int | None = None
    # None — из настроек в момент запуска: значение класса Python считает
    # один раз при загрузке модуля, и смена входа в пульте не доходила бы.
    channel: int | None = None

    # Когда в микрофоне последний раз звучала речь (`time.monotonic`).
    # Сторож тишины диктовки отсчитывает паузу отсюда, а не от конца
    # законченной фразы: пока он говорит, тишины нет. Классом, а не в
    # `__init__`: подмена в тестах не должна падать из-за отсутствия поля.
    heard_at: float = 0.0

    # Куда сообщать «речь идёт» с её громкостью: телефон по этому рисует
    # свечение по краям экрана. Классом, а не в `__init__` — по той же
    # причине: тесты собирают слушателя вручную, поля не будет, и падать
    # не должно.
    on_voice: Callable[[bool, float], None] | None = None

    # Громкость последнего куска, который услышали: 0…1. Её же рисует полоска
    # микрофона в пульте. Считается попутно с тем, что уже меряется для
    # подсветки телефона, поэтому в потоке звука лишней работы не появляется.
    # Классом, а не в `__post_init__` — по той же причине, что и `on_voice`.
    last_level: float = 0.0

    _queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=200))
    _muted: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        if self.device is None:
            if config.MIC_DEVICE is not None:
                self.device = config.MIC_DEVICE
            else:
                self.device, _ = config.find_input_device()
        if self.device is None:
            raise RuntimeError("Микрофон не найден. Запусти diagnose_mic.py")

        if self.channel is None:
            self.channel = int(getattr(config, "MIC_CHANNEL", 0) or 0)
        info = sd.query_devices(self.device)
        self.name = info["name"]
        self.rate = int(info["default_samplerate"])
        self.channels = min(info["max_input_channels"], self.channel + 1)
        self._extra = None

        if self.rate % config.SAMPLE_RATE:
            # У многих гарнитур и встроенных микрофонов по умолчанию
            # 44 100 Гц — нацело на 16 000 не делится. Просим у Windows
            # кратную частоту: MME пересчитает сама, WASAPI — с
            # автоконвертацией. Без этого голос работал только на звуковой
            # карте автора (48 000).
            self.rate, self._extra = self._pick_rate(info)
        self.decim = self.rate // config.SAMPLE_RATE
        self._vad = StreamingVad()
        self._stream = None
        # Порог громкости на время её речи. Ноль — слушаем всё подряд.
        self._barge_threshold = 0.0
        # Громкость последней попытки заговорить поверх неё. Нужна, чтобы
        # порог подбирался по живым замерам, а не назначался на глаз —
        # ровно на этом уже обожглись с отбором по голосу.
        self.last_start_peak = 0.0

    # --- Заглушка на время своей речи -------------------------------------

    def mute(self) -> None:
        """Глушит микрофон, пока Труба говорит, иначе он услышит сам себя."""
        self._muted.set()

    def listen_while_speaking(self, threshold: float = 0.12) -> None:
        """Продолжает слушать, но только громкую речь.

        Пока Труба говорит, микрофон обычно глухой — и перебить её голосом
        невозможно, приходится ждать конца реплики. Здесь вместо полной
        глухоты поднимается порог: тихое эхо её собственного голоса не
        проходит, а обращённая к ней фраза — проходит.
        """
        self._muted.clear()
        self._barge_threshold = threshold

    def stop_listening_loudly(self) -> None:
        self._barge_threshold = 0.0

    def unmute(self, keep_seconds: float = TAIL_KEPT) -> None:
        self._muted.clear()
        self._vad.reset()
        # Накопленное за время своей речи выбрасываем — но не всё.
        #
        # Раньше чистилась вся очередь, и это стирало начало фразы: человек
        # отвечает сразу, как она договорила, а первые полсекунды уже
        # выброшены. До распознавания доезжал огрызок, и приходилось
        # повторять. Оставляем последнюю секунду — там его голос, а не эхо.
        keep = int(max(0.0, keep_seconds) * config.SAMPLE_RATE)
        tail: list[np.ndarray] = []
        kept = 0
        while not self._queue.empty():
            try:
                block = self._queue.get_nowait()
            except queue.Empty:
                break
            tail.append(block)
            kept += len(block)
            while tail and kept - len(tail[0]) >= keep:
                kept -= len(tail.pop(0))
        for block in tail:
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                break

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    # --- Захват -----------------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:
        if self._muted.is_set():
            return

        # Берём нужный канал и сразу прореживаем до 16 кГц: усреднение
        # по группе гасит наложение частот не хуже простого фильтра.
        mono = indata[:, min(self.channel, indata.shape[1] - 1)]

        # Громкость здесь больше ничего не режет. Раньше куски тише порога
        # выбрасывались прямо тут — и фраза, начатая вполголоса, теряла
        # начало: человек говорил, а до распознавания доезжал хвост.
        # Теперь порог решает только одно: можно ли НАЧАТЬ фразу, пока
        # Труба говорит. Решается это в phrases(), где виден весь поток.

        usable = len(mono) // self.decim * self.decim
        if usable == 0:
            return
        down = mono[:usable].reshape(-1, self.decim).mean(axis=1)
        try:
            self._queue.put_nowait(down.astype(np.float32))
        except queue.Full:
            pass

    def _pick_rate(self, info) -> tuple:
        """Частота, кратная 16 кГц, которую микрофон согласен отдать."""
        api = sd.query_hostapis(info["hostapi"])["name"]
        extra = None
        if "wasapi" in api.lower() and hasattr(sd, "WasapiSettings"):
            extra = sd.WasapiSettings(auto_convert=True)
        for rate in (48000, 32000, config.SAMPLE_RATE):
            try:
                sd.check_input_settings(
                    device=self.device, channels=self.channels,
                    samplerate=rate, dtype="float32", extra_settings=extra)
            except Exception:
                continue
            return rate, extra
        raise RuntimeError(
            f"Микрофон «{info['name']}» не отдаёт звук с частотой, кратной "
            f"{config.SAMPLE_RATE} Гц (у него {int(info['default_samplerate'])})"
        )

    def start(self) -> None:
        blocksize = VAD_HOP * self.decim
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.rate,
            channels=self.channels,
            dtype="float32",
            blocksize=blocksize,
            callback=self._callback,
            extra_settings=getattr(self, "_extra", None),
        )
        self._stream.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # --- Нарезка на фразы -------------------------------------------------

    def _voice(self, on: bool, level: float = 0.0) -> None:
        """Говорит подписчику, что микрофон слышит речь.

        Ошибку глотаем целиком: подсветка — украшение, а микрофон важнее.
        Оборванное соединение с телефоном не должно оборвать нарезку фраз.
        """
        hook = getattr(self, "on_voice", None)
        if hook is None:
            return
        try:
            hook(bool(on), float(level))
        except Exception:
            pass

    def _may_start(self, preroll: np.ndarray) -> bool:
        """Можно ли начинать фразу прямо сейчас.

        Пока Труба молчит — всегда. Пока говорит — только если звук
        заметно громче порога: иначе она услышит сама себя из динамика
        телефона и перебьёт себя же на полуслове.

        Громкость берём по разбегу окна, а не по одному куску: короткий
        всплеск даёт и щелчок мыши.
        """
        if self._barge_threshold <= 0 or not len(preroll):
            return True

        peak = float(np.abs(preroll).max())
        self.last_start_peak = peak
        return peak >= self._barge_threshold

    def phrases(self):
        """Генератор законченных фраз. Блокируется, пока человек молчит."""
        preroll_len = int(PREROLL * config.SAMPLE_RATE)
        silence_needed = int(SILENCE_TO_END * config.SAMPLE_RATE)
        min_len = int(MIN_PHRASE * config.SAMPLE_RATE)
        # Границы, за которыми длинную речь режем до распознавания: иначе
        # GigaAM падает на куске длиннее ~54 с и валит весь голос.
        soft_max = int(float(config.PHRASE_SOFT_MAX) * config.SAMPLE_RATE)
        hard_max = int(float(config.PHRASE_HARD_MAX) * config.SAMPLE_RATE)
        soft_silence = int(float(config.PHRASE_SOFT_SILENCE) * config.SAMPLE_RATE)

        preroll = np.zeros(0, dtype=np.float32)
        collected: list[np.ndarray] = []
        speaking = False
        silence_run = 0
        # Сколько отсчётов в текущей фразе: по этому числу решаем, не слишком
        # ли она длинная для распознавания.
        length = 0
        # Когда подсветке последний раз говорили, что речь идёт (`monotonic`).
        # Между сообщениями она просто ждёт, иначе телефон получал бы по
        # тридцать сообщений в секунду на каждом куске.
        last_voice = 0.0

        leftover = np.zeros(0, dtype=np.float32)

        while not self._stop.is_set():
            try:
                block = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            samples = np.concatenate([leftover, block]) if len(leftover) else block

            offset = 0
            while offset + VAD_HOP <= len(samples):
                chunk = samples[offset : offset + VAD_HOP]
                offset += VAD_HOP

                # Громкость этого куска — для полоски микрофона в пульте.
                # Считаем на каждом куске, но дёшево: 512 отсчётов по квадрату
                # раз в 32 мс, на поток звука это не слышно.
                self.last_level = voice_level(chunk)

                prob = self._vad.probability(chunk)

                if not speaking:
                    preroll = np.concatenate([preroll, chunk])[-preroll_len:]
                    if prob >= SPEECH_ON and self._may_start(preroll):
                        speaking = True
                        silence_run = 0
                        collected = [preroll.copy(), chunk]
                        length = sum(len(part) for part in collected)
                        preroll = np.zeros(0, dtype=np.float32)
                        self.heard_at = time.monotonic()
                        # Начало речи — всегда сообщаем: хозяин ждёт ответа
                        # именно в этот момент, промедление читается как «не
                        # услышала».
                        last_voice = self.heard_at
                        self._voice(True, voice_level(chunk))
                else:
                    collected.append(chunk)
                    length += len(chunk)
                    if prob >= SPEECH_OFF:
                        # Речь идёт прямо сейчас: сторож диктовки ждёт тишины
                        # отсюда, а не от конца законченной фразы.
                        self.heard_at = time.monotonic()
                        silence_run = 0
                        # Внутри фразы — не чаще десяти раз в секунду: рамка
                        # телефона дышит по громкости, а не по каждому куску.
                        if self.heard_at - last_voice >= VOICE_EVERY:
                            last_voice = self.heard_at
                            self._voice(True, voice_level(chunk))
                    else:
                        silence_run += VAD_HOP
                        # Длинная фраза кончается на короткой паузе: человек
                        # читает монолог с остановками, и каждая из них — не
                        # конец мысли.
                        needed = soft_silence if length > soft_max else silence_needed
                        if silence_run >= needed:
                            phrase = np.concatenate(collected)
                            speaking = False
                            collected = []
                            length = 0
                            # Хвост тишины в распознавание не отдаём.
                            phrase = phrase[: len(phrase) - silence_run + VAD_HOP]
                            # Фраза закончилась — и отданная, и выброшенная
                            # как слишком короткая: рамка должна погаснуть в
                            # обоих случаях, иначе телефон будет светиться
                            # после щелчка мышью.
                            self._voice(False, 0.0)
                            if len(phrase) >= min_len:
                                yield phrase

                if speaking and length > hard_max:
                    # Пауз не было совсем: отдаём фразу как есть и сразу
                    # собираем следующую, речь не прерывая. Иначе распознавание
                    # получит кусок, на котором оно падает. Обрезаем ровно по
                    # пределу, а остаток текущего куска — начало следующей фразы.
                    # Про «речь кончилась» здесь не сообщаем: человек говорит,
                    # рамка должна гореть дальше, а не мигать на стыке.
                    phrase = np.concatenate(collected)
                    leftover_of_chunk = phrase[hard_max:]
                    phrase = phrase[:hard_max]
                    collected = [leftover_of_chunk]
                    length = len(leftover_of_chunk)
                    silence_run = 0
                    if len(phrase) >= min_len:
                        yield phrase

            leftover = samples[offset:]

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
