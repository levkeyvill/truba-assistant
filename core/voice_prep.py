r"""Подготовка образца голоса: обрезка, нормализация, распознавание текста.

Образец нужен синтезатору, чтобы скопировать тембр. Текст образца
распознаём сами — вводить его руками незачем, GigaAM всё равно точнее.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

import config

MAX_SECONDS = 12.0
# F5-TTS работает с 24 кГц.
TTS_RATE = 24000

# Целевая длина образца. Короче — модели мало данных о тембре, длиннее —
# меньше остаётся бюджета на саму реплику и грубее расчёт длительности.
TARGET_SECONDS = 9.0
MIN_SECONDS = 6.0


def snap_to_pause(
    audio: np.ndarray, rate: int, position: int, search: float = 1.2, forward: bool = False
) -> int:
    """Двигает точку реза к ближайшей паузе между словами.

    Детектор речи делит запись по заметным паузам, и если человек говорит
    без остановки, весь кусок для него неразделим. Резать его по границе
    окна нельзя — слово оборвётся на середине. Ищем поблизости место, где
    тише всего: между словами всегда есть провал громкости.
    """
    window = int(search * rate)
    if window < 2:
        return position

    lo = max(0, position if forward else position - window)
    hi = min(len(audio), position + window if forward else position)
    if hi - lo < 2:
        return position

    chunk = np.abs(audio[lo:hi])
    # Огибающая по 20 мс: убирает дрожь внутри звука, оставляет провалы.
    step = max(int(0.02 * rate), 1)
    usable = len(chunk) // step * step
    if usable < step * 2:
        return position

    envelope = chunk[:usable].reshape(-1, step).mean(axis=1)
    quietest = int(np.argmin(envelope)) * step

    return lo + quietest


def find_best_fragment(audio: np.ndarray, rate: int) -> tuple[int, int]:
    """Ищет в записи самый пригодный кусок: сплошную речь без длинных пауз.

    Брать начало файла нельзя — там обычно заставка, музыка или раскачка
    диктора. Ищем детектором речи и выбираем окно, где говорят плотнее всего,
    а границы ставим по краям фраз, чтобы не резать посреди слова.
    """
    import onnx_asr

    work_rate = config.SAMPLE_RATE
    mono = resample(audio, rate, work_rate).astype(np.float32)

    vad = onnx_asr.load_vad(config.VAD_MODEL)
    segments = list(
        next(
            vad.segment_batch(
                mono[None, :], np.array([len(mono)], dtype=np.int64), work_rate
            )
        )
    )

    if not segments:
        # Речь не нашлась — берём середину, она обычно содержательнее начала.
        middle = max(0, len(audio) // 2 - int(TARGET_SECONDS * rate / 2))
        return middle, middle + int(TARGET_SECONDS * rate)

    target = int(TARGET_SECONDS * work_rate)
    scale = rate / work_rate

    if len(mono) <= target:
        return 0, len(audio)

    # Маска речи с шагом 10 мс: дальше ищем по ней окно, а не перебираем
    # сегменты — иначе кусок речи длиннее окна отбрасывался бы целиком.
    step = max(work_rate // 100, 1)
    mask = np.zeros(len(mono) // step + 2, dtype=np.float32)
    for seg_start, seg_end in segments:
        mask[seg_start // step : seg_end // step] = 1.0

    window = max(target // step, 1)
    if len(mask) <= window:
        return int(segments[0][0] * scale), int(segments[-1][1] * scale)

    # Скользящая сумма: где речи больше всего, там и лучший образец.
    cumulative = np.concatenate([[0.0], np.cumsum(mask)])
    density = cumulative[window:] - cumulative[:-window]
    start = int(np.argmax(density)) * step
    end = start + target

    # Подравниваем границы по краям фраз, чтобы не резать посреди слова.
    inside = [s for s in segments if s[1] > start and s[0] < end]
    if inside:
        start = max(start, inside[0][0])
        last_end = inside[-1][1]
        if last_end <= end:
            # Речь закончилась внутри окна — режем точно по её краю.
            end = last_end
        else:
            # Речь продолжается за окном: детектор пауз здесь бессилен,
            # ищем тихое место между словами, иначе оборвём слог.
            end = snap_to_pause(mono, work_rate, end)
            start = snap_to_pause(mono, work_rate, start, forward=True)

    limit = int(MAX_SECONDS * work_rate)
    if end - start > limit:
        end = snap_to_pause(mono, work_rate, start + limit)

    if end - start < int(MIN_SECONDS * work_rate):
        end = min(start + target, len(mono))

    return int(start * scale), int(end * scale)


def voices_dir() -> Path:
    path = config.ROOT / "voice"
    path.mkdir(exist_ok=True)
    return path


def available() -> list[str]:
    """Имена готовых голосов — те, у кого есть и звук, и текст."""
    found = []
    for wav in sorted(voices_dir().glob("*.wav")):
        if wav.with_suffix(".txt").exists():
            found.append(wav.stem)
    return found


def read_text(name: str) -> str:
    txt = voices_dir() / f"{name}.txt"
    return txt.read_text(encoding="utf-8").strip() if txt.exists() else ""


def duration(name: str) -> float:
    """Длительность образца в секундах, 0 если его нет."""
    import soundfile as sf

    wav = voices_dir() / f"{name}.wav"
    if not wav.exists():
        return 0.0
    info = sf.info(str(wav))
    return info.frames / info.samplerate


def delete(name: str) -> None:
    """Убирает голос: и звук, и текст."""
    for suffix in (".wav", ".txt"):
        path = voices_dir() / f"{name}{suffix}"
        if path.exists():
            path.unlink()


def resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return audio
    if src % dst == 0:
        factor = src // dst
        trimmed = audio[: len(audio) // factor * factor]
        return trimmed.reshape(-1, factor).mean(axis=1)
    length = int(len(audio) * dst / src)
    return np.interp(
        np.linspace(0, len(audio) - 1, length), np.arange(len(audio)), audio
    ).astype(np.float32)


def prepare(
    source: Path,
    name: str | None = None,
    start: float | None = None,
    seconds: float = MAX_SECONDS,
) -> tuple[str, str]:
    """Готовит образец и распознаёт его текст.

    Если начало не задано, сам находит в записи лучший кусок — можно
    скармливать файлы любой длины, хоть часовые.

    Возвращает (имя голоса, распознанный текст).
    """
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"Нет файла {source}")

    name = name or source.stem
    # Пробелы и точки в именах ломают подстановку в пути — чистим.
    name = "".join(c for c in name if c.isalnum() or c in "-_").strip("-_")
    if not name:
        raise ValueError("Из имени файла не получилось собрать название голоса")

    try:
        data, rate = sf.read(str(source), dtype="float32", always_2d=True)
    except Exception as exc:
        raise RuntimeError(
            f"Не смог прочитать {source.name}: {exc}. "
            "Если это экзотический формат, пересохрани в обычный wav."
        ) from exc

    audio = data.mean(axis=1)

    if start is None:
        begin, finish = find_best_fragment(audio, rate)
        audio = audio[begin : min(finish, begin + int(MAX_SECONDS * rate))]
    else:
        begin = int(start * rate)
        length = int(min(seconds, MAX_SECONDS) * rate)
        audio = audio[begin : begin + length]

    if len(audio) == 0:
        raise ValueError("После обрезки ничего не осталось — сдвинь начало")

    peak = float(np.abs(audio).max())
    if peak < 0.01:
        raise ValueError("В этом куске тишина — сдвинь начало")

    # Тихий образец даёт вялый и шипящий синтез.
    audio = audio / peak * 0.95

    wav_path = voices_dir() / f"{name}.wav"
    sf.write(wav_path, resample(audio, rate, TTS_RATE), TTS_RATE)

    import onnx_asr

    model = onnx_asr.load_model(config.STT_MODEL, quantization=config.STT_QUANTIZATION)
    text = model.recognize(
        resample(audio, rate, config.SAMPLE_RATE), sample_rate=config.SAMPLE_RATE
    ).strip()

    (voices_dir() / f"{name}.txt").write_text(text, encoding="utf-8")
    return name, text
