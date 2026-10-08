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
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import sounddevice as sd

import config
from core.speaker_echo import SpeakerEcho

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
# Секунда с небольшим выдерживает обычную паузу внутри мысли и добавляет
# к задержке ответа меньше полусекунды.
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

# --- Замер эха во время её речи --------------------------------------------
#
# Пока она говорит, микрофон слышит и её собственный голос из динамика
# телефона. Сравнивать его речь с порогом бесполезно: порог один на всех, а
# эхо у разных телефонов разное. Поэтому меряем фон — насколько громко
# слышно её во время паузы собеседника, — и перебиваем только то, что заметно
# выше этого фона.
#
# Сколько последних кусков держим и каким процентилем берём фон: 80-й, а не
# максимум, иначе один щелчок поднимет фон и следующее перебивание
# перестанет срабатывать.
ECHO_FLOOR_CHUNKS = 60
ECHO_FLOOR_PERCENTILE = 80
# Сколько её речи должно пройти, прежде чем фон вообще можно сравнивать.
# Раньше этого микрофон ничего не показал, и сравнивать не с чем.
ECHO_FLOOR_MIN = 0.5
# Ниже этой громкости (RMS) перебиванием не считаем ничего: это тишина
# комнаты, а не голос. Порог «во сколько раз громче фона» — config.BARGE_RATIO.
BARGE_MIN_RMS = 0.01
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


# --- Короткий замер уровня одного устройства ------------------------------
#
# Полоска шага 4 мастера должна показывать уровень **выбранного** микрофона
# сразу, когда голос ещё не запущен или держит другой. Работающий голос туда
# не годится: он слушает прошлый микрофон. Поэтому открываем нужное
# устройство на доли секунды, меряем и закрываем — это измерение, а не запись.
#
# Длина куска и их число — как у голосового потока (`VAD_HOP` на частоте
# устройства): иначе полоска мастера и полоска «Голоса» показывали бы разное.
LEVEL_CHUNK_SECONDS = 0.05


def peak_level(track, rate) -> float:
    """Насколько громко звучала запись — 0…1, та же шкала, что у полоски.

    Считаем максимум по кускам `LEVEL_CHUNK_SECONDS`, а не по всей записи
    целиком. Три секунды с паузами между словами дают низкий средний уровень,
    и обычная речь выглядела бы «тихой», хотя микрофон её прекрасно слышит.
    """
    трек = np.asarray(track)
    hop = max(1, int(float(rate) * LEVEL_CHUNK_SECONDS))
    лучший = 0.0
    for начало in range(0, len(трек), hop):
        лучший = max(лучший, voice_level(трек[начало:начало + hop]))
    return лучший


def record_own(device, channels: int, rate: int, frames: int, extra=None):
    """Записать `frames` кадров СВОИМ потоком: массив (кадры, каналы).

    Не `sd.rec`: он общий на весь процесс, как и `sd.play` у голоса в
    колонки (`core/audio_out.py::play_own`).
    Свой `InputStream` ничей звук не обрывает.
    """
    with sd.InputStream(device=device, channels=channels, samplerate=rate,
                        dtype="float32", extra_settings=extra) as stream:
        data, _overflowed = stream.read(int(frames))
    return data


def measure_level(device, channel: int = 0, seconds: float = 0.25) -> float:
    """Уровень 0…1 одного микрофона коротким замером.

    Шкала та же, что у работающего голоса (`voice_level`), поэтому обе
    полоски означают одно и то же. Устройство открывается на `seconds` и
    сразу закрывается; голос, модель и колонки это не трогает.
    """
    info = sd.query_devices(device)
    каналов = int(info["max_input_channels"])
    if каналов < 1:
        raise RuntimeError("устройство не принимает звук")
    # Просим `channel` и все, что до него: так же, как это делает `Listener`,
    # иначе Windows на двухвходовом устройстве возьмёт не тот канал.
    channels = min(каналов, channel + 1)
    rate, extra = _rate_for(device, info, channels)
    recorded = record_own(device, channels, rate, int(rate * seconds), extra)
    track = recorded[:, min(channel, channels - 1)]
    if not len(track):
        raise RuntimeError("микрофон не отдал звук")
    # Громкость — самая громкая часть замера, а не средняя по нему.
    return peak_level(track, rate)


def _rate_for(device, info, channels) -> tuple:
    """Частота, на которой это устройство вообще отдаст звук.

    Родная частота звуковой карты — как и в `Listener`. Если она не годится,
    ищем ту, что даёт Windows, и напоследок пробуем 16 кГц.
    """
    try:
        api = sd.query_hostapis(info["hostapi"])["name"]
    except Exception:
        api = ""
    extra = None
    if "wasapi" in api.lower() and hasattr(sd, "WasapiSettings"):
        extra = sd.WasapiSettings(auto_convert=True)
    for rate in (int(info.get("default_samplerate") or 0), 48000, 32000,
                 config.SAMPLE_RATE):
        if rate <= 0:
            continue
        try:
            sd.check_input_settings(device=device, channels=channels,
                                    samplerate=rate, dtype="float32",
                                    extra_settings=extra)
        except Exception:
            continue
        return rate, extra
    raise RuntimeError(f"микрофон не отдаёт звук (родная частота "
                       f"{int(info.get('default_samplerate') or 0)} Гц)")


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

    # Куда сообщать «его можно перебить прямо сейчас»: его громкость и фон её
    # собственного голоса, с которым его сравнили. Классом — по той же
    # причине, что и `on_voice`.
    on_barge: Callable[[float, float], None] | None = None

    # Замер последней её речи — пик микрофона на ней. Фон берётся отдельно,
    # методом `echo_measure`: он нужен один раз за реплику, в журнал.
    last_echo_peak: float = 0.0

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
            # автоконвертацией. Это поддерживает карты с другой родной частотой.
            self.rate, self._extra = self._pick_rate(info)
        self.decim = self.rate // config.SAMPLE_RATE
        self._vad = StreamingVad()
        self._stream = None
        self.speaker_echo = SpeakerEcho()
        # Порог громкости на время её речи. Ноль — слушаем всё подряд.
        self._barge_threshold = 0.0
        # Громкость последней попытки заговорить поверх неё. Нужна, чтобы
        # порог можно было подобрать по замерам.
        self.last_start_peak = 0.0
        self._analysis_lock = threading.RLock()
        self._reset_echo()

    # --- Замер эха и мгновенное перебивание ---------------------------------

    def _analysis_locked(self):
        """Сброс детектора и разбор звука используют один замок."""
        lock = getattr(self, "_analysis_lock", None)
        if lock is None:
            lock = self._analysis_lock = threading.RLock()
        return lock

    def _reset_echo(self) -> None:
        """Забыть замер её прошлой речи.

        Вызывается на каждой её реплике и когда микрофон перестаёт слушать
        громко: фон новой реплики начинается с нуля, иначе перебивание
        сравнивалось бы с эхом той, прошлой.
        """
        with self._analysis_locked():
            self._echo: deque[float] = deque(maxlen=ECHO_FLOOR_CHUNKS)
            self._echo_samples = 0
            self._barge_run: deque[float] = deque()
            self._barge_fired = False
            self.last_echo_peak = 0.0

    def echo_measure(self) -> tuple[float, float]:
        """Замер её последней речи: (фон, пик).

        Отдельный метод, а не поля: голосовой цикл спрашивает замер перед
        `stop_listening_loudly`, который замер обнуляет. По этим двум
        числам в журнале подбираются пороги.
        """
        with self._analysis_locked():
            return float(self._echo_floor()), float(self.last_echo_peak)

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
        with self._analysis_locked():
            self._barge_threshold = threshold
            # Новая реплика начинает новый замер эха.
            self._reset_echo()

    def stop_listening_loudly(self) -> None:
        with self._analysis_locked():
            self._barge_threshold = 0.0
            self._reset_echo()

    def unmute(self, keep_seconds: float = TAIL_KEPT) -> None:
        self._muted.clear()
        with self._analysis_locked():
            self._vad.reset()
            self._reset_echo()
        # Накопленное за время своей речи выбрасываем — но не всё.
        #
        # Оставляем последнюю секунду очереди: ответ может начаться сразу
        # после её речи, и полная очистка срежет начало фразы.
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

        # Громкость не отбрасывает куски: так сохраняется начало тихой фразы.
        # Порог решает только одно: можно ли НАЧАТЬ фразу, пока
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
        if config.SPEAKER_AEC:
            self.speaker_echo.start()
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
        echo = getattr(self, "speaker_echo", None)
        if echo is not None:
            echo.stop()
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

    def _barge(self, level: float, floor: float) -> None:
        """Говорит подписчику, что речь Трубы можно перебить прямо сейчас.

        Ошибку глотаем целично, как в `_voice`: перебивание — украшение,
        а микрофон от неё не зависит.
        """
        hook = getattr(self, "on_barge", None)
        if hook is None:
            return
        try:
            hook(float(level), float(floor))
        except Exception:
            pass

    def _echo_floor(self) -> float:
        """Фон её голоса: 80-й процентиль громкости за последние ~2 секунды.

        Процентиль, а не максимум: щелчок мыши поднял бы максимум, и после
        него перестало бы срабатывать всё подряд. Пусто — 0.0, сравнивать
        тогда не с чем, и сработать нечему.
        """
        if not self._echo:
            return 0.0
        ordered = sorted(self._echo)
        index = min(len(ordered) - 1,
                    int(ECHO_FLOOR_PERCENTILE / 100.0 * (len(ordered) - 1)))
        return float(ordered[index])

    def _echo_add(self, level: float, voiced: bool = True) -> None:
        """Кладёт кусок в фон её голоса.

        В счёт `ECHO_FLOOR_MIN` идёт только речь. Тишина тоже ложится в
        фон, но готовым его не делает: при чтении вслух замер начинается
        заново на каждом предложении, и пока её голос идёт из телефона до
        микрофона, слышна только тишина комнаты. Фон из одной тишины
        принял бы её первое слово за перебивание.
        """
        self._echo.append(float(level))
        if voiced:
            self._echo_samples += VAD_HOP

    def _watch_barge(self, level: float, prob: float) -> None:
        """Следит за микрофоном, пока она говорит.

        VAD назвал кусок речью — он идёт в окно кандидата на перебивание
        шириной `BARGE_MS`. Куски, которые из окна вытеснились, уходят в фон:
        пока кандидат не начался, в окне лежит её собственный голос из
        динамика, и только он. Так фон набирается даже когда она говорит без
        единой паузы, а его собственный «голос» фон себе поднять не может.

        Срабатывает один раз за её реплику: когда окно заполнено его речью
        (`prob ≥ SPEECH_ON`) и её медиана заметно выше фона.
        """
        if self._barge_fired:
            return

        # Пик запоминаем всегда: по нему журнал показывает, сколько микрофон
        # слышал на её речи, даже если перебивания так и не вышло.
        if level > self.last_echo_peak:
            self.last_echo_peak = float(level)

        # Окно нечётное: на чётном медиана усредняет две середины, и на
        # полуокне «эхо + его голос» получается ровно половина — ровно тот
        # случай, когда обрывать рано.
        need = max(1, int(float(config.BARGE_MS) / 1000.0 * config.SAMPLE_RATE
                          / VAD_HOP))
        need += 1 - need % 2

        if prob < SPEECH_ON:
            # Тишина посреди её речи: кандидат оборвался (щелчок, кашель), и
            # этот кусок — уже её голос, его в фон.
            self._barge_run.clear()
            self._echo_add(level, voiced=False)
            return

        self._barge_run.append(float(level))
        # Окно скользит, а не растёт: вытесненное уходит в фон.
        while len(self._barge_run) > need:
            self._echo_add(self._barge_run.popleft())
        if len(self._barge_run) < need:
            return

        median = float(np.median(self._barge_run))
        # Фон берём до проверки: он должен быть набран, иначе сравнивать
        # не с чем — первые полсекунды её речи уходят на разогрев.
        if self._echo_samples < int(ECHO_FLOOR_MIN * config.SAMPLE_RATE):
            return
        floor = self._echo_floor()
        # Порог перебивания из настроек (`_barge_threshold`) — это пик, а здесь
        # RMS: сравнивать их нельзя. Нижняя граница — почти тишина.
        if median >= max(BARGE_MIN_RMS, floor * float(config.BARGE_RATIO)):
            self._barge_fired = True
            self._barge(median, floor)

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

            echo = getattr(self, "speaker_echo", None)
            if echo is not None and echo.active:
                block = echo.clean(block)
            samples = np.concatenate([leftover, block]) if len(leftover) else block

            offset = 0
            while offset + VAD_HOP <= len(samples):
                chunk = samples[offset : offset + VAD_HOP]
                offset += VAD_HOP

                # Громкость этого куска — для полоски микрофона в пульте.
                # Считаем на каждом куске, но дёшево: 512 отсчётов по квадрату
                # раз в 32 мс, на поток звука это не слышно.
                self.last_level = voice_level(chunk)

                with self._analysis_locked():
                    prob = self._vad.probability(chunk)

                    # Пока она говорит — меряем её эхо и ждём его голоса. Идёт
                    # по каждому куску, а не по началу фразы: перебивание должно
                    # случиться, пока она ещё замолчала не успел.
                    if self._barge_threshold > 0:
                        # Линейная громкость (RMS), а не `voice_level`: та сжата
                        # корнем для подсветки, и «в 2.5 раза громче фона» на ней
                        # значило бы «в 6 раз» на деле.
                        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
                        self._watch_barge(rms, prob)

                if not speaking:
                    preroll = np.concatenate([preroll, chunk])[-preroll_len:]
                    if prob >= SPEECH_ON and self._may_start(preroll):
                        speaking = True
                        silence_run = 0
                        collected = [preroll.copy(), chunk]
                        length = sum(len(part) for part in collected)
                        preroll = np.zeros(0, dtype=np.float32)
                        self.heard_at = time.monotonic()
                        # О начале речи сообщаем сразу, без задержки подсветки.
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
