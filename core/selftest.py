r"""Замеры: слышно ли, быстро ли, можно ли перебить.

Повод конкретный. В прошлый раз хозяин не смог перебить Трубу голосом,
и выяснилось это только на живой проверке — то есть его временем. Здесь
всё, что можно измерить без него, меряется заранее.

Главный замер — про перебивание. Пока она говорит, микрофон не глухой,
но пропускает только громкое: порог `BARGE_IN_LEVEL`. Если голос хозяина
до этого порога не дотягивает, перебить её невозможно в принципе, и
никакие настройки разговора этого не исправят.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

import config


@dataclass
class Result:
    """Один замер: что мерили, что вышло, годится ли."""

    name: str
    value: str
    ok: bool = True
    note: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, name, value, ok=True, note="") -> None:
        self.results.append(Result(name, value, ok, note))

    @property
    def good(self) -> bool:
        return all(r.ok for r in self.results)

    def as_text(self) -> str:
        lines = []
        for r in self.results:
            mark = "+" if r.ok else "!"
            lines.append(f"[{mark}] {r.name}: {r.value}")
            if r.note:
                lines.append(f"      {r.note}")
        return "\n".join(lines)


# --- Микрофон ------------------------------------------------------------


def microphone(report: Report, seconds: float = 1.5) -> float:
    """Находит микрофон и меряет, насколько тихо в комнате.

    Возвращает уровень шума — от него потом считается, отличим ли голос.
    """
    import sounddevice as sd

    index, described = config.find_input_device()
    if index is None:
        report.add("Микрофон", "не найден", ok=False,
                   note="Проверь, включён ли микрофон")
        return 0.0

    report.add("Микрофон", described)

    info = sd.query_devices(index)
    rate = int(info["default_samplerate"])
    channels = min(info["max_input_channels"], config.MIC_CHANNEL + 1)

    recorded = sd.rec(
        int(rate * seconds), samplerate=rate, channels=channels,
        dtype="float32", device=index, blocking=True,
    )
    track = recorded[:, min(config.MIC_CHANNEL, channels - 1)]

    noise = float(np.abs(track).max())
    report.add(
        "Тишина в комнате",
        f"пик {noise:.4f}",
        ok=noise < 0.05,
        note="" if noise < 0.05 else "Шумно: микрофон будет цепляться за фон",
    )
    return noise


def voice_level(seconds: float = 4.0) -> dict:
    """Меряет, насколько громко человек говорит. Нужен живой голос.

    Считаем не только пик: одиночный щелчок даёт высокий пик при тихой
    речи. Берём ещё и уровень, ниже которого девять десятых времени —
    он честнее описывает обычную громкость.
    """
    import sounddevice as sd

    index, _ = config.find_input_device()
    if index is None:
        return {"ok": False, "why": "микрофон не найден"}

    info = sd.query_devices(index)
    rate = int(info["default_samplerate"])
    channels = min(info["max_input_channels"], config.MIC_CHANNEL + 1)

    recorded = sd.rec(
        int(rate * seconds), samplerate=rate, channels=channels,
        dtype="float32", device=index, blocking=True,
    )
    track = np.abs(recorded[:, min(config.MIC_CHANNEL, channels - 1)])

    peak = float(track.max())
    loud = float(np.percentile(track, 99))
    typical = float(np.percentile(track, 90))

    threshold = config.BARGE_IN_LEVEL
    # Перебить получится, если громкие места речи уверенно выше порога.
    can_interrupt = loud >= threshold * 1.2

    return {
        "ok": True,
        "peak": peak,
        "loud": loud,
        "typical": typical,
        "threshold": threshold,
        "can_interrupt": can_interrupt,
        # Порог с запасом вниз: перебивать должно получаться без крика.
        "suggested": round(max(0.02, loud * 0.55), 3),
    }


def record(seconds: float) -> tuple[np.ndarray, int]:
    """Пишет микрофон заданное время и отдаёт нужный канал."""
    recorder = Recorder()
    recorder.start()
    try:
        recorder.wait(seconds)
    finally:
        return recorder.stop()


class Recorder:
    """Запись с микрофона, которую можно оборвать по кнопке.

    Раньше здесь был `sd.rec` с ожиданием: он держит общий поток внутри
    sounddevice, оборвать его нечем, и второй запуск подряд натыкался
    на незакрытый первый. Свой поток закрывается явно и всегда.
    """

    def __init__(self):
        self._blocks: list[np.ndarray] = []
        self._stream = None
        self.rate = config.SAMPLE_RATE
        self._done = threading.Event()

    def start(self) -> None:
        import sounddevice as sd

        index, _ = config.find_input_device()
        if index is None:
            raise RuntimeError("микрофон не найден — проверь, включён ли микрофон")

        info = sd.query_devices(index)
        self.rate = int(info["default_samplerate"])
        channels = min(info["max_input_channels"], config.MIC_CHANNEL + 1)
        track = min(config.MIC_CHANNEL, channels - 1)

        self._blocks = []
        self._done.clear()

        def take(indata, frames, time_info, status):
            self._blocks.append(indata[:, track].copy())

        self._stream = sd.InputStream(
            device=index, samplerate=self.rate, channels=channels,
            dtype="float32", callback=take,
        )
        self._stream.start()

    @property
    def seconds(self) -> float:
        return sum(len(b) for b in self._blocks) / self.rate

    def wait(self, limit: float) -> None:
        """Ждёт, пока не наберётся столько секунд или не попросят закончить."""
        while self.seconds < limit and not self._done.is_set():
            time.sleep(0.1)

    def finish(self) -> None:
        """Попросить закончить раньше времени."""
        self._done.set()

    def stop(self) -> tuple[np.ndarray, int]:
        """Закрывает поток и отдаёт записанное. Звать можно повторно."""
        self._done.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if not self._blocks:
            return np.zeros(0, dtype=np.float32), self.rate
        return np.concatenate(self._blocks), self.rate


def split_speech(
    track: np.ndarray, rate: int, piece: float = 4.0, quiet: float = 0.02
) -> list[np.ndarray]:
    """Режет запись на куски и выбрасывает те, где человек молчал.

    Отпечаток считается по кускам и усредняется. Куски, где он набирал
    воздух или ждал, только портят среднее — их убираем по громкости.
    """
    step = int(piece * rate)
    pieces = []
    for start in range(0, len(track) - step // 2, step):
        chunk = track[start : start + step]
        if len(chunk) < step // 2:
            continue
        # Не пик, а уровень девяноста процентов: одиночный щелчок
        # не должен выдавать тишину за речь.
        if float(np.percentile(np.abs(chunk), 90)) < quiet:
            continue
        pieces.append(chunk)
    return pieces


# --- Скорости ------------------------------------------------------------


def recognition(report: Report) -> None:
    """Сколько занимает распознавание секунды речи."""
    import onnx_asr

    started = time.perf_counter()
    model = onnx_asr.load_model(config.STT_MODEL, quantization=config.STT_QUANTIZATION)
    load = time.perf_counter() - started

    sample = np.zeros(config.SAMPLE_RATE * 3, dtype=np.float32)
    model.recognize(sample, sample_rate=config.SAMPLE_RATE)  # прогрев

    started = time.perf_counter()
    model.recognize(sample, sample_rate=config.SAMPLE_RATE)
    took = time.perf_counter() - started

    report.add("Загрузка распознавания", f"{load:.1f} с")
    report.add(
        "Распознать 3 секунды",
        f"{took:.2f} с",
        ok=took < 1.0,
        note="" if took < 1.0 else "Медленно: ответ будет заметно отставать",
    )


def synthesis(report: Report) -> None:
    """Сколько занимает синтез обычной фразы."""
    PHRASE = "Ну чё, проверяем, как быстро я успеваю ответить."

    started = time.perf_counter()
    from core.voice_loop import make_voice

    voice = make_voice()
    voice.load()
    load = time.perf_counter() - started

    try:
        voice.say("Прогрев.", None, nfe_step=config.TTS_NFE)

        started = time.perf_counter()
        wave, rate = voice.say(
            PHRASE, None, nfe_step=config.TTS_NFE, speed=config.TTS_SPEED
        )
        took = time.perf_counter() - started
        length = len(wave) / rate
    finally:
        # Проверка идёт при выключенном голосе — видеопамять после замера
        # отдаём обратно (Higgs держит 4 ГБ, ESpeech 3).
        unload = getattr(voice, "unload", None)
        if unload is not None:
            unload()

    report.add("Загрузка синтеза", f"{load:.1f} с")
    report.add(
        "Синтез фразы",
        f"{took:.2f} с на {length:.1f} с речи",
        ok=took < length,
        note="" if took < length else "Синтез медленнее самой речи — будут паузы",
    )


def thinking(report: Report) -> None:
    """Сколько модель думает до первого слова."""
    from core import settings
    from core.brain import Brain

    try:
        brain = Brain(provider=settings.get_provider())
    except Exception as exc:
        report.add("Облако", f"{exc}", ok=False)
        return

    started = time.perf_counter()
    first = None
    pieces = 0
    try:
        for _ in brain.reply("Скажи одно короткое предложение про погоду."):
            if first is None:
                first = time.perf_counter() - started
            pieces += 1
    except Exception as exc:
        report.add("Облако", f"{type(exc).__name__}: {exc}", ok=False)
        return

    total = time.perf_counter() - started
    report.add(
        "Первое слово от модели",
        f"{first:.2f} с" if first else "не ответила",
        ok=bool(first and first < 3.0),
        note="" if first and first < 3.0 else "Долго: проверь связь и провайдера",
    )
    report.add("Ответ целиком", f"{total:.2f} с, кусков {pieces}")


def commands_work(report: Report) -> None:
    """Разбираются ли голосовые команды."""
    from core import commands, launcher

    apps = launcher.read_list()
    проверки = [
        ("сделай скриншот", "screenshot"),
        ("сфоткай экран", "screenshot"),
        ("сохрани момент", "moment"),
        ("клипани", "moment"),
        ("посмотри что на экране", "look"),
        ("запусти дискорд", "launch"),
        ("открой фаерфокс", "launch"),
        ("закрой дискорд", "close"),
        ("выключи телеграм", "close"),
        ("как дела", None),
    ]

    промахи = []
    for фраза, ждём in проверки:
        got = commands.understand(фраза, apps)
        есть = got.action if got else None
        if есть != ждём:
            промахи.append(f"{фраза!r} -> {есть}")

    report.add(
        "Голосовые команды",
        f"{len(проверки) - len(промахи)} из {len(проверки)}",
        ok=not промахи,
        note="; ".join(промахи),
    )


def run_all(report: Report | None = None, with_cloud: bool = True) -> Report:
    """Всё, что можно померить без живого человека."""
    report = report or Report()

    microphone(report)
    commands_work(report)
    recognition(report)
    synthesis(report)
    if with_cloud:
        thinking(report)

    return report
