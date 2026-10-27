"""Облачный голос: синтез речи на сервере сервиса, а не на этом компьютере.

Подходит любой сервис с OpenAI-совместимым `POST {адрес}/audio/speech`:
OpenRouter, OpenAI или свой адрес. Звук просим сырым (`pcm`, 16 бит, моно):
его не надо распаковывать, и он приходит потоком. Сервис, который pcm не
отдаёт, просим `wav`.

Интернет и чужой сервер ненадёжны, поэтому рядом всегда Silero на
процессоре: сервис не ответил — фразу говорит Silero, а облако какое-то
время не трогаем (`COOLDOWN_*`), чтобы каждая фраза не ждала отказа заново.
"""

import io
import re
import threading
import time
import wave

import httpx
import numpy as np

import config
from core import settings
from core.speech_text import for_speech

CONNECT_TIMEOUT = 5.0
# Между кусками звука: сервис, замолчавший дольше, считаем отвалившимся.
READ_TIMEOUT = 15.0
# Столько секунд звука в одном куске потока: меньше — больше щелей на стыках,
# больше — дольше ждать первый звук.
STREAM_SECONDS = 0.4
# Сколько не ходить в облако после отказа, по его причине.
COOLDOWN_NET = 60.0       # нет связи, тайм-аут, сбой сервера
COOLDOWN_LIMIT = 600.0    # кончились деньги или дневной лимит
COOLDOWN_SETUP = 1800.0   # ключ не тот, модели нет, не тот формат звука
FORMATS = ("pcm", "wav")
# Каталог голосовых моделей OpenRouter открыт без ключа.
OPENROUTER_CATALOG = "https://openrouter.ai/api/v1/models?output_modalities=speech"
OPENAI_MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
                 "onyx", "sage", "shimmer", "verse")
PREVIEW_PHRASE = "Ну чё, как тебе такой голос? Нормально звучу или поменяем?"


class CloudVoiceError(RuntimeError):
    """Отказ облака: текст для журнала и сколько облако не трогать."""

    def __init__(self, text: str, cooldown: float):
        super().__init__(text)
        self.cooldown = cooldown


def _http_error(status: int, detail: str) -> CloudVoiceError:
    """Ответ сервиса с ошибкой — понятными словами и с паузой по причине."""
    detail = re.sub(r"\s+", " ", detail).strip()[:200]
    if status in (401, 403):
        return CloudVoiceError(f"ключ не подошёл ({status})", COOLDOWN_SETUP)
    if status == 402:
        return CloudVoiceError("на счёте сервиса кончились деньги (402)", COOLDOWN_LIMIT)
    if status == 429:
        return CloudVoiceError("лимит запросов сервиса (429)", COOLDOWN_LIMIT)
    if status in (400, 404, 422):
        return CloudVoiceError(f"сервис не принял запрос ({status}): {detail}", COOLDOWN_SETUP)
    return CloudVoiceError(f"сервис ответил ошибкой {status}: {detail}", COOLDOWN_NET)


def _rate_from(content_type: str) -> int:
    """Частота из заголовка вида `audio/pcm;rate=44100`; нет — 0."""
    found = re.search(r"rate=(\d{4,6})", content_type or "")
    return int(found.group(1)) if found else 0


def _pcm(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _wav(data: bytes) -> tuple:
    """Целый wav в (сигнал, частота); многоканальный сводится в моно."""
    try:
        with wave.open(io.BytesIO(data)) as файл:
            if файл.getsampwidth() != 2:
                raise CloudVoiceError("сервис отдал wav не 16 бит", COOLDOWN_SETUP)
            каналы = файл.getnchannels()
            звук = _pcm(файл.readframes(файл.getnframes()))
            if каналы > 1:
                звук = звук[:len(звук) // каналы * каналы].reshape(-1, каналы).mean(axis=1)
            return звук.astype(np.float32), файл.getframerate()
    except (wave.Error, EOFError) as exc:
        raise CloudVoiceError(f"сервис отдал битый wav: {exc}", COOLDOWN_SETUP) from exc


def _minutes(seconds: float) -> str:
    минут = max(1, round(seconds / 60))
    return f"{минут} мин"


class CloudVoice:
    """Голос через облачный сервис, с Silero на случай отказа."""

    def __init__(self, service: str | None = None, model: str | None = None,
                 voice: str | None = None, url: str | None = None,
                 key: str | None = None, spare: bool = True):
        self.service = settings.validate_cloud_voice_service(
            service if service is not None else config.CLOUD_VOICE_SERVICE)
        spec = config.CLOUD_VOICE_SERVICES[self.service]
        self.model = (model if model is not None else config.CLOUD_VOICE_MODEL) or spec["model"]
        голос = voice if voice is not None else config.CLOUD_VOICE_VOICE
        # Голос по умолчанию — голос модели по умолчанию; у другой модели
        # свои голоса, и чужое имя она не примет. У OpenAI голоса общие.
        if not голос and (self.model == spec["model"] or self.service == "openai"):
            голос = spec["voice"]
        self.voice = голос or ""
        адрес = spec["base_url"] or (url if url is not None else config.CLOUD_VOICE_URL)
        self.base_url = str(адрес or "").rstrip("/")
        self.default_rate = int(spec["rate"])
        # Ключ берётся при `load`: его могли вписать уже после создания.
        self._key = key
        # Журнал: «не ответил — говорю Silero» и «снова отвечает».
        self.on_note = None
        self._spare_wanted = spare
        self._spare = None
        self._spare_error = None
        self._spare_ready = threading.Event()
        self._spare_lock = threading.Lock()
        self._down_until = 0.0
        self._failing = False
        self._format = None
        self._client = None

    # --- Подготовка ---------------------------------------------------------

    def load(self) -> None:
        """Проверить, что всё вписано, и поднять запасной Silero в фоне."""
        if not self.base_url:
            raise RuntimeError("Облачный голос: впиши адрес сервиса — "
                               "«Голос → Озвучивание»")
        if not self.model:
            raise RuntimeError("Облачный голос: впиши модель — «Голос → Озвучивание»")
        if self._key is None:
            self._key = settings.cloud_voice_key(self.service)
        if not self._key and self.service != "custom":
            raise RuntimeError("Облачный голос: нет ключа — впиши его в "
                               "«Голос → Озвучивание» или у мозга")
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT))
        if self._spare_wanted and self._spare is None and not self._spare_ready.is_set():
            threading.Thread(target=self._load_spare, name="truba-spare-voice",
                             daemon=True).start()

    def _load_spare(self) -> None:
        with self._spare_lock:
            if self._spare is None and self._spare_error is None:
                try:
                    from core.silero_voice import SileroVoice

                    запасной = SileroVoice()
                    запасной.load()
                    self._spare = запасной
                except Exception as exc:
                    self._spare_error = exc
            self._spare_ready.set()

    def _headers(self) -> dict:
        заголовки = {"Content-Type": "application/json"}
        if self._key and not self._key.isascii():
            # Ключи бывают только латиницей: русская буква или кавычка
            # «ёлочкой» — след неудачной вставки, заголовок с ней не уйдёт.
            raise CloudVoiceError("в ключе посторонние символы — вставь его заново",
                                  COOLDOWN_SETUP)
        if self._key:
            заголовки["Authorization"] = f"Bearer {self._key}"
        if self.service == "openrouter":
            заголовки["X-Title"] = "Truba"
        return заголовки

    # --- Синтез -------------------------------------------------------------

    def say(self, text: str, ref=None, nfe_step: int | None = None,
            speed: float = 1.0) -> tuple:
        """Реплика целиком: (сигнал, частота). `ref` и `nfe_step` не нужны."""
        готовый = self._prepare(text)
        if not готовый:
            return np.zeros(0, dtype=np.float32), self.default_rate
        if self._cloud_ready():
            try:
                куски = list(self._fetch(готовый))
                звук = np.concatenate([к for к, _ in куски]) if куски else np.zeros(0, np.float32)
                частота = куски[0][1] if куски else self.default_rate
                if not len(звук):
                    raise CloudVoiceError("сервис вернул пустой звук", COOLDOWN_NET)
                self._recovered()
                return self._tempo(звук, частота, speed), частота
            except CloudVoiceError as exc:
                self._failed(exc)
        return self._spare_say(text, speed)

    def stream(self, text: str, ref=None, speed: float = 1.0):
        """Реплика кусками по мере прихода звука — для телефона, играющего встык."""
        готовый = self._prepare(text)
        if not готовый:
            return
        if not self._cloud_ready():
            yield self._spare_say(text, speed)
            return
        from core.tempo import NEUTRAL, Stretcher

        растяжка = None
        отдано = False
        try:
            for кусок, частота in self._fetch(готовый):
                if abs(float(speed or 1.0) - 1.0) > NEUTRAL:
                    if растяжка is None:
                        растяжка = (Stretcher(частота, speed), частота)
                    кусок = растяжка[0].push(кусок)
                if len(кусок):
                    отдано = True
                    yield кусок, частота
            if растяжка is not None:
                хвост = растяжка[0].flush()
                if len(хвост):
                    yield хвост, растяжка[1]
            if not отдано:
                raise CloudVoiceError("сервис вернул пустой звук", COOLDOWN_NET)
            self._recovered()
        except CloudVoiceError as exc:
            self._failed(exc)
            if not отдано:
                yield self._spare_say(text, speed)

    def _prepare(self, text: str) -> str:
        # Числа и значки — словами, латиницу облачная модель читает сама.
        готовый = for_speech(text or "", keep_latin=True).strip()
        return готовый if re.search(r"\w", готовый) else ""

    def _fetch(self, text: str):
        """Звук сервиса кусками (сигнал, частота). Отказ — `CloudVoiceError`."""
        if self._client is None:
            self.load()
        форматы = (self._format,) if self._format else FORMATS
        for номер, формат in enumerate(форматы):
            тело = {"model": self.model, "input": text, "response_format": формат}
            if self.voice:
                тело["voice"] = self.voice
            try:
                with self._client.stream("POST", f"{self.base_url}/audio/speech",
                                         json=тело, headers=self._headers()) as ответ:
                    if ответ.status_code >= 400:
                        подробно = ответ.read().decode("utf-8", "replace")
                        последний = номер == len(форматы) - 1
                        # Не тот формат — пробуем следующий, а не сдаёмся.
                        if (ответ.status_code in (400, 415, 422) and not последний
                                and "format" in подробно.lower()):
                            continue
                        raise _http_error(ответ.status_code, подробно)
                    self._format = формат
                    вид = ответ.headers.get("content-type", "").lower()
                    if формат == "wav" or "wav" in вид:
                        yield _wav(ответ.read())
                        return
                    if any(слово in вид for слово in ("mpeg", "mp3", "ogg", "opus", "aac")):
                        raise CloudVoiceError(f"сервис отдал сжатый звук ({вид}), "
                                              "а нужен pcm или wav", COOLDOWN_SETUP)
                    частота = _rate_from(вид) or self.default_rate
                    шаг = max(2, int(частота * STREAM_SECONDS) * 2)
                    запас = b""
                    for кусок in ответ.iter_bytes():
                        запас += кусок
                        while len(запас) >= шаг:
                            yield _pcm(запас[:шаг]), частота
                            запас = запас[шаг:]
                    if len(запас) >= 2:
                        yield _pcm(запас[:len(запас) // 2 * 2]), частота
                    return
            except httpx.TimeoutException as exc:
                raise CloudVoiceError("сервис не ответил вовремя", COOLDOWN_NET) from exc
            except httpx.HTTPError as exc:
                raise CloudVoiceError(f"нет связи с сервисом ({type(exc).__name__})",
                                      COOLDOWN_NET) from exc
        raise CloudVoiceError("сервис не принял ни pcm, ни wav", COOLDOWN_SETUP)

    @staticmethod
    def _tempo(звук: np.ndarray, частота: int, speed: float) -> np.ndarray:
        from core.tempo import NEUTRAL, Stretcher

        if abs(float(speed or 1.0) - 1.0) <= NEUTRAL:
            return звук
        растяжка = Stretcher(частота, speed)
        return np.concatenate([растяжка.push(звук), растяжка.flush()]).astype(np.float32)

    # --- Отказы и запасной голос ----------------------------------------------

    def _cloud_ready(self) -> bool:
        return time.monotonic() >= self._down_until

    def _failed(self, exc: CloudVoiceError) -> None:
        self._down_until = time.monotonic() + exc.cooldown
        if self._spare_wanted and not self._failing:
            self._note(f"не ответил — {exc}; говорю Silero, снова попробую "
                       f"через {_minutes(exc.cooldown)}")
        self._failing = True
        if not self._spare_wanted:
            raise RuntimeError(f"Облачный голос: {exc}") from exc

    def _recovered(self) -> None:
        if self._failing:
            self._failing = False
            self._note("снова отвечает")

    def _note(self, text: str) -> None:
        hook = self.on_note
        if hook is not None:
            try:
                hook(text)
            except Exception:
                pass

    def _spare_say(self, text: str, speed: float) -> tuple:
        if not self._spare_wanted:
            raise RuntimeError("Облачный голос не ответил")
        if not self._spare_ready.is_set():
            self._load_spare()
        if self._spare is None:
            raise RuntimeError(f"облачный голос не ответил, а Silero не поднялся: "
                               f"{self._spare_error}")
        return self._spare.say(text, speed=speed)

    def accent(self, text: str) -> str:
        return for_speech(text, keep_latin=True)

    def vram_used_mb(self) -> float:
        """Синтез на сервере сервиса, видеопамять не занимает."""
        return 0.0


# --- Каталог для пульта -------------------------------------------------------


def catalog(service: str, url: str = "", key: str = "") -> dict:
    """Модели и голоса сервиса для подсказок в пульте.

    {"models": [{"id", "name", "free", "voices"}], "voices": [...]}. Каталог —
    только подсказка: вписать можно любую модель и любой голос.
    """
    service = settings.validate_cloud_voice_service(service)
    if service == "openai":
        return {"models": [{"id": м, "name": м, "free": False, "voices": list(OPENAI_VOICES)}
                           for м in OPENAI_MODELS],
                "voices": list(OPENAI_VOICES)}
    if service == "openrouter":
        ответ = httpx.get(OPENROUTER_CATALOG, timeout=15.0)
        ответ.raise_for_status()
        модели = []
        for модель in ответ.json().get("data", []):
            цена = модель.get("pricing") or {}
            модели.append({
                "id": str(модель.get("id", "")),
                "name": str(модель.get("name", "")),
                "free": str(модель.get("id", "")).endswith(":free")
                or (str(цена.get("prompt")) in ("0", "0.0")
                    and str(цена.get("completion")) in ("0", "0.0")),
                "voices": [str(г) for г in (модель.get("supported_voices") or [])],
            })
        модели.sort(key=lambda м: (not м["free"], м["id"]))
        голоса = sorted({г for м in модели for г in м["voices"]})
        return {"models": [м for м in модели if м["id"]], "voices": голоса}
    адрес = str(url or config.CLOUD_VOICE_URL or "").rstrip("/")
    if not адрес:
        return {"models": [], "voices": []}
    заголовки = {"Authorization": f"Bearer {key}"} if key else {}
    ответ = httpx.get(f"{адрес}/models", headers=заголовки, timeout=10.0)
    ответ.raise_for_status()
    модели = [{"id": str(м.get("id", "")), "name": str(м.get("id", "")), "free": False,
               "voices": []} for м in ответ.json().get("data", []) if м.get("id")]
    return {"models": модели, "voices": []}
