r"""Синтез речи: ESpeech-TTS (F5-TTS, дообученная под русский) + RUAccent.

Модель клонирует голос по референсному образцу, поэтому нужен файл с голосом
и текст, который в нём произнесён. Образец — не длиннее 12 секунд.

RUAccent расставляет ударения: без него модель путает "за́мок" и "замо́к".
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.speech_text import for_speech


@contextlib.contextmanager
def _quiet():
    """Глушит болтовню f5-tts.

    Библиотека печатает в stdout текст каждой реплики, содержимое образца и
    «Download Vocos from huggingface» — хотя файл давно в кеше. В консоли
    это выглядит так, будто что-то постоянно скачивается.
    """
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        yield

MODEL_CFG = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
MODEL_REPO = "ESpeech/ESpeech-TTS-1_RL-V2"
MODEL_FILE = "espeech_tts_rlv2.pt"
VOCAB_FILE = "vocab.txt"


def _patch_torchaudio_load() -> None:
    """Заменяет torchaudio.load на чтение через soundfile.

    В torchaudio 2.10 загрузка идёт через torchcodec, который требует
    совпадения версий с torch и наличия ffmpeg — иначе падает на загрузке DLL.
    Нам нужно читать только локальные образцы голоса, с этим справится soundfile.
    """
    import soundfile as sf
    import torch
    import torchaudio

    if getattr(torchaudio.load, "_patched_by_us", False):
        return

    def load(filepath, *args, **kwargs):
        data, sample_rate = sf.read(str(filepath), dtype="float32", always_2d=True)
        # torchaudio отдаёт форму [каналы, отсчёты], soundfile — наоборот.
        return torch.from_numpy(data.T.copy()), sample_rate

    load._patched_by_us = True
    torchaudio.load = load


def _patch_ruaccent_sessions(accentizer) -> None:
    """Приводит входы ONNX-моделей RUAccent в соответствие с тем, что они ждут.

    Модели внутри RUAccent экспортированы в разное время и расходятся по
    набору входов: одни падают на лишнем token_type_ids, другие требуют его
    обязательно. Свежий transformers отдаёт его не всегда, поэтому лишнее
    отбрасываем, а недостающее подставляем нулями — это нейтральное значение
    для одиночной последовательности.

    Патчим сессии, а не чужой пакет, чтобы правка пережила переустановку.
    """
    for attr in (
        "omograph_model",
        "accent_model",
        "yo_homograph_model",
        "stress_usage_model",
    ):
        session = getattr(getattr(accentizer, attr, None), "session", None)
        if session is None:
            continue

        expected = {i.name for i in session.get_inputs()}
        original = session.run

        def run(output_names, input_feed, run_options=None, _orig=original, _ok=expected):
            feed = {k: v for k, v in input_feed.items() if k in _ok}

            for name in _ok - feed.keys():
                reference = feed.get("input_ids")
                if reference is None:
                    continue
                if name in ("token_type_ids", "attention_mask"):
                    filler = np.zeros_like(reference) if name == "token_type_ids" else np.ones_like(reference)
                    feed[name] = filler

            return _orig(output_names, feed, run_options)

        session.run = run


@dataclass
class Reference:
    """Образец голоса: путь к аудио и текст, который в нём звучит."""

    audio: Path
    text: str


class Voice:
    """Голос ассистента. Держит модель в памяти между репликами."""

    def __init__(self, device: str | None = None, keep_on_gpu: bool = True):
        import torch

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        # Выгрузка модели на CPU после каждой фразы экономит ~2 ГБ VRAM,
        # но добавляет секунды задержки. Для живого разговора держим на GPU.
        self.keep_on_gpu = keep_on_gpu and device == "cuda"

        self._model = None
        self._vocoder = None
        self._accentizer = None
        self._ref_cache: dict[Path, tuple] = {}

    def load(self) -> None:
        """Грузит модель, вокодер и словарь ударений.

        Файлы берутся только с диска: скачивает их пульт по выбору хозяина
        (`core/voice_models.py`), а голос не ходит в сеть никогда (02.10).
        """
        from huggingface_hub import hf_hub_download
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT
        from ruaccent import RUAccent

        if self._model is not None:
            return

        from core import voice_models

        _patch_torchaudio_load()

        try:
            ckpt = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE,
                                   local_files_only=True)
            vocab = hf_hub_download(repo_id=MODEL_REPO, filename=VOCAB_FILE,
                                    local_files_only=True)
        except Exception as exc:
            raise RuntimeError(voice_models.нужна_скачать("espeech")) from exc

        with _quiet():
            self._model = load_model(DiT, MODEL_CFG, ckpt, vocab_file=vocab)
            self._vocoder = load_vocoder()

        self._accentizer = RUAccent()
        self._accentizer.load(
            omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False
        )
        _patch_ruaccent_sessions(self._accentizer)

        if self.keep_on_gpu:
            self._model.to(self.device)
            self._vocoder.to(self.device)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> None:
        """Отдаёт видеопамять (около 3 ГБ), когда голос выключают.

        Видеокарта принадлежит играм. Следующий load() поднимет всё заново.
        """
        if self._model is None:
            return
        self._model = None
        self._vocoder = None
        self._accentizer = None
        self._ref_cache.clear()
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def accent(self, text: str) -> str:
        """Расставляет ударения. Если в тексте уже есть '+', оставляет как есть."""
        if not text or not text.strip():
            return text
        if "+" in text:
            return text
        return self._accentizer.process_all(text)

    def _prepare_ref(self, ref: Reference):
        """Предобработка образца кешируется — она не зависит от реплики.

        Возвращает (путь к обработанному аудио, текст, его длительность).
        Длительность берём уже после обработки: она вырезает паузы, и
        исходная продолжительность файла перестаёт соответствовать тексту.
        """
        if ref.audio not in self._ref_cache:
            import soundfile as sf
            from f5_tts.infer.utils_infer import preprocess_ref_audio_text

            with _quiet():
                audio, text = preprocess_ref_audio_text(
                    str(ref.audio), self.accent(ref.text), show_info=lambda *a, **k: None
                )
            info = sf.info(audio)
            self._ref_cache[ref.audio] = (audio, text, info.frames / info.samplerate)
        return self._ref_cache[ref.audio]

    def say(
        self,
        text: str,
        ref: Reference,
        nfe_step: int = 32,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """Синтезирует реплику. Возвращает (сигнал, частота дискретизации).

        nfe_step — компромисс качество/скорость: 48 чище, 16 заметно быстрее.
        """
        from f5_tts.infer.utils_infer import infer_process

        self.load()

        ref_audio, ref_text, ref_seconds = self._prepare_ref(ref)
        # Цифры и сокращения — словами, как для Silero. Без этого замерено
        # 25 сентября: «SSD на 240 Гб» звучало как «с езды на дзецб»,
        # «18:42» — как «эт Сучи»: F5 цифр не знает, а латиницу читает
        # по буквам наугад.
        gen_text = self.accent(for_speech(text))
        if not gen_text.strip():
            return np.zeros(0, dtype=np.float32), 24000

        if not self.keep_on_gpu and self.device == "cuda":
            self._model.to(self.device)
            self._vocoder.to(self.device)

        try:
            with _quiet():
                wave, sample_rate, _ = infer_process(
                    ref_audio,
                    ref_text,
                    gen_text,
                    self._model,
                    self._vocoder,
                    cross_fade_duration=0.15,
                    nfe_step=nfe_step,
                    speed=speed,
                    fix_duration=self._duration_for(
                        ref_text, gen_text, ref_seconds, speed
                    ),
                    show_info=lambda *a, **k: None,
                    progress=None,
                )
        finally:
            if not self.keep_on_gpu and self.device == "cuda":
                import torch

                self._model.to("cpu")
                self._vocoder.to("cpu")
                torch.cuda.empty_cache()

        return trim_tail(wave, sample_rate), sample_rate

    @staticmethod
    def _duration_for(
        ref_text: str, gen_text: str, ref_seconds: float, speed: float
    ) -> float:
        """Сколько секунд отвести под реплику.

        Модель укладывает речь ровно в заданное окно: мало — обрежет конец,
        много — растянет слово или домыслит лишнее. Поэтому нужна точная
        величина, а не максимальная.

        Встроенная оценка линейна по длине текста в байтах и не учитывает
        постоянные издержки — вдох в начале, затухание в конце, паузу перед
        точкой. Они одинаковы для любой фразы, поэтому короткие реплики
        страдают в разы, а длинные почти нет. Добавка подобрана на слух:
        для «Ага» линейная оценка давала 0.22 с при нужных 0.6–1.0 с.
        """
        ref_bytes = max(len(ref_text.encode("utf-8")), 1)
        gen_bytes = len(gen_text.encode("utf-8"))
        speed = max(speed, 0.1)

        expected = ref_seconds / ref_bytes * gen_bytes / speed
        overhead = 0.55 / speed
        return ref_seconds + expected + overhead


def trim_tail(wave: np.ndarray, sample_rate: int, keep: float = 0.12) -> np.ndarray:
    """Срезает тишину в конце — она остаётся от запаса по длительности."""
    if wave.size == 0:
        return wave

    # Порог относительно самой реплики: абсолютный не годится, громкость плавает.
    threshold = max(float(np.abs(wave).max()) * 0.02, 1e-4)
    loud = np.flatnonzero(np.abs(wave) > threshold)
    if loud.size == 0:
        return wave

    end = min(len(wave), loud[-1] + int(keep * sample_rate))
    return wave[:end]

    def vram_used_mb(self) -> float:
        """Сколько видеопамяти занято процессом, МБ."""
        import torch

        if self.device != "cuda":
            return 0.0
        return torch.cuda.memory_allocated() / 1024 / 1024
