r"""Тихий фон, пока Труба ищет в интернете.

Хозяин попросил «как у ChatGPT»: сказала «секунду, гляну» — и пошёл тихий
фон, пока модель ходит в сеть. Без него секунды ожидания не отличить от
зависания.

Звук — файл `web/search_hum.*`, спокойный эмбиент, который хозяин сделал
сам (29.09 синтез кодом он забраковал: «звуки хоррора», «страшные
высокие»). Файл едет с программой. Нет файла — нет и фона, молча.

При загрузке файл готовится: моно, конец плавно переходит в начало (круг
без щелчка на стыке) и громкость — тихая, под её голос. Файл может быть
любой: склеивать и приглушать его руками не нужно.

Звук идёт туда же, куда голос: в колонки — своим потоком sounddevice, в
телефон — коротким сообщением, и уже страница телефона крутит его по
кругу (`GET /search_hum.wav`). `sd.play`/`sd.stop` не используются: они
общие на процесс, ими говорит `core/audio_out.py`, и `sd.stop()` оборвал бы
её речь.
"""

import io
import threading
import time
import wave as wave_module

import numpy as np
import sounddevice as sd

import config

# Где искать звук: первый найденный. Любой формат, который читает soundfile.
SOUND_FILES = tuple(config.ROOT / "web" / f"search_hum{ext}"
                    for ext in (".ogg", ".mp3", ".wav", ".flac"))
# Пик после подготовки: фон не должен перебивать ни голос, ни музыку.
PEAK = 0.3
# И по средней громкости: эмбиент бывает ровным и плотным, и с одним пиком
# он всё равно звучал бы громко.
RMS = 0.07
# Сколько секунд конца плавно переходят в начало круга.
LOOP_FADE = 1.5

# Звук готовится один раз: и в колонки, и в телефон он один и тот же.
# False — ещё не искали; None — файла нет.
_cache = False


def prepare(wave: np.ndarray, rate: int) -> np.ndarray:
    """Моно, круг без стыка, тихая громкость."""
    wave = np.asarray(wave, dtype=np.float32)
    if wave.ndim > 1:
        wave = wave.mean(axis=1)
    wave = np.ascontiguousarray(wave, dtype=np.float32)
    fade = min(int(LOOP_FADE * rate), len(wave) // 4)
    if fade > 0:
        # Хвост уходит, а начало входит: последний звук круга — ровно тот,
        # что стоял перед его новым первым, и на стыке нечему щёлкнуть.
        вход = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        body = wave[fade:].copy()
        body[-fade:] = wave[-fade:] * (1.0 - вход) + wave[:fade] * вход
        wave = body
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    rms = float(np.sqrt(np.mean(wave ** 2))) if wave.size else 0.0
    if peak <= 0.0:
        return wave
    scale = min(PEAK / peak, RMS / rms if rms > 0 else PEAK / peak)
    return (wave * scale).astype(np.float32)


def _file():
    for path in SOUND_FILES:
        if path.is_file():
            return path
    return None


def sound():
    """Фон как (сигнал float32 моно, частота) или None — файла нет."""
    global _cache
    if _cache is False:
        _cache = None
        path = _file()
        if path is not None:
            try:
                import soundfile as sf

                wave, rate = sf.read(str(path), dtype="float32", always_2d=True)
                if wave.size:
                    _cache = (prepare(wave, int(rate)), int(rate))
            except Exception:
                _cache = None
    return _cache


def wav_bytes() -> bytes | None:
    """Тот же звук байтами WAV (16 бит, моно) — для телефона. None — нет."""
    found = sound()
    if found is None:
        return None
    wave, rate = found
    pcm = (np.clip(wave, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave_module.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class _Тишина:
    """Фон, которого нет: для цикла, собранного вручную (тесты, подмены)."""

    def start(self, speaker, to_phone: bool) -> None:
        pass

    def stop(self) -> None:
        pass


_ТИШИНА = _Тишина()


class SearchHum:
    """Фон на время ожидания ответа из интернета.

    `start` не включает звук сразу: фраза «секунду, гляну» ещё звучит, и фон
    начинается, когда она договорила. `stop` гасит его плавно. Оба можно
    звать сколько угодно раз.
    """

    # Сколько ждать конца фразы. Дольше незачем: если она и столько не
    # договорила, ответ скоро всё равно придёт.
    WAIT_PHRASE = 15.0
    FADE_IN = 0.8
    FADE_OUT = 0.4

    def __init__(self, send_phone):
        self._send_phone = send_phone
        self._lock = threading.Lock()
        # Отмена: пока фраза не договорила, звука не будет вовсе.
        self._stop = threading.Event()
        self._thread = None

    def start(self, speaker, to_phone: bool) -> None:
        """Фон пойдёт, когда фраза «секунду, гляну» договорит."""
        if not getattr(config, "SEARCH_SOUND", True) or sound() is None:
            return
        with self._lock:
            # Уже ждём или уже играет — второй раз начинать нечего.
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(speaker, to_phone, self._stop),
                daemon=True, name="search-hum")
            self._thread.start()

    def stop(self) -> None:
        """Гасит фон. Не ждёт его конца — зовётся между предложениями."""
        with self._lock:
            self._stop.set()

    # --- Поток ---------------------------------------------------------------

    def _run(self, speaker, to_phone, stop) -> None:
        try:
            try:
                speaker.wait(timeout=self.WAIT_PHRASE)
            except Exception:
                pass
            if stop.is_set():
                return
            if to_phone:
                self._on_phone(stop)
            else:
                self._on_speakers(speaker, stop)
        except Exception:
            pass

    def _on_phone(self, stop) -> None:
        """Телефон: «включи» и «выключи» шлёт этот же поток, по порядку.

        Выключение из `stop()` могло бы обогнать включение, и фон на
        телефоне остался бы играть.
        """
        from core.voice_loop import voice_gain

        if self._send_phone is None:
            return
        self._send_phone(True, voice_gain())
        stop.wait()
        self._send_phone(False, 0.0)

    def _on_speakers(self, speaker, stop) -> None:
        """Колонки: свой поток с колбэком, крутящим звук по кругу."""
        from core.voice_loop import voice_gain

        wave, rate = sound()
        n = len(wave)
        top = voice_gain()
        state = {"pos": 0, "level": 0.0, "quiet": False}

        def step(out, frames, _time, _status):
            block = wave[(state["pos"] + np.arange(frames)) % n]
            state["pos"] = int((state["pos"] + frames) % n)
            now = state["level"]
            # Громкость идёт прямо, а не по экспоненте: экспонента к нулю
            # тянется секундами, и поток закрывался бы на слышной громкости.
            if stop.is_set():
                end = max(0.0, now - top * frames / rate / self.FADE_OUT)
            else:
                end = min(top, now + top * frames / rate / self.FADE_IN)
            out[:, 0] = block * np.linspace(now, end, frames, endpoint=False, dtype=np.float32)
            state["level"] = end
            if stop.is_set() and end <= 0.0:
                state["quiet"] = True

        stream = sd.OutputStream(samplerate=rate, channels=1,
                                 device=getattr(speaker, "device", None), callback=step)
        stream.start()
        try:
            stop.wait()
            waited = 0.0
            while not state["quiet"] and waited < self.FADE_OUT + 0.5:
                time.sleep(0.02)
                waited += 0.02
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
