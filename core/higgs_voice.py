r"""Синтез речи через Higgs TTS 3 (Boson AI, вышел 4 июня 2026) — проба.

Зачем: хозяину не нравится, как читают и Silero, и ESpeech. Higgs — большая
говорящая модель на 4 миллиарда параметров: сто с лишним языков, русский
в группе лучшего качества, голос клонирует по образцу, английские слова
читает по-английски, а не выбрасывает.

Официально она запускается только сервером SGLang под Linux. Здесь тот же
алгоритм, повторённый на обычном PyTorch по исходникам SGLang-Omni
(`sglang_omni/models/higgs_tts`, Apache-2.0):

    хребет      Qwen3 на 36 слоёв — из transformers, как есть
    звук        восемь кодовых книг по 1026 значений, каждая следующая
                сдвинута на шаг (delay pattern), 25 кадров в секунду
    вход        эмбеддинги восьми книг складываются в один вектор
    выход       одна общая матрица на все книги, та же, что на входе
    кодек       Higgs Audio v2 из transformers, веса лежат в том же файле

Затравка: ``<|tts|> <|ref_text|> текст образца <|ref_audio|> [коды образца]
<|text|> что сказать <|audio|>``. Без ``<|tts|>`` она бегло несёт чушь —
так предупреждают сами авторы.

Цена в видеопамяти. В bf16 — 9.3 ГБ, и 25 сентября это повесило комп
намертво: Valorant (5.8 ГБ) плюс Higgs упёрлись в 16 ГБ карты. Поэтому по
умолчанию (PRECISION = "fp8"):

    матрицы слоёв      FP8 с масштабом на строку       6930 → 3465 МБ
    таблица слов       читается строками прямо из файла  742 → 0
    разбор образца     кодировщик кодека поднимается    680 → 0
                       на процессоре только для нового
                       образца, коды ложатся на диск
    на видеокарте      декодер кодека, кеш внимания, звуковая голова

Обычная память тоже на счету. Первая версия читала веса через safetensors
(safe_open): он отображает в память весь файл, 9.3 ГБ, и любой тензор из
него — даже маленький — держит всё отображение живым. Таблица слов на
процессоре так и держала: 25 сентября пульт занимал 19 ГБ оперативки при
Valorant. Теперь файл читается по одному тензору в свою память (`_read`),
а таблица слов — `np.memmap` только на свой кусок файла: в память
попадают лишь строки слов текущей фразы.

Умножение FP8 — torch._scaled_mm с одним масштабом на матрицу (построчный
cuBLAS на этой карте не умеет); масштаб на строку весов и на строку
активаций применяется после умножения, это почти бесплатно. Замерено на
5070 Ti для матрицы 2560×9728: bf16 77 мкс, FP8 42 мкс.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from pathlib import Path

import numpy as np

import config
from core.speech_text import for_speech
from core.tempo import Stretcher, stretch

REPO = "bosonai/higgs-tts-3-4b"
SAMPLE_RATE = 24_000
FRAMES_PER_SECOND = 25

# Служебные значения внутри словаря кодовой книги (не текстового словаря):
# «звук ещё не начался» для сдвинутых книг и «звук кончился».
BOC_ID = 1024
EOC_ID = 1025
# Место под коды образца в затравке. Там не текст, а звук: при сборке
# входа на эти позиции подставляются эмбеддинги кодов.
PLACEHOLDER = -100

CODEC_PREFIX = "tied.embedding.modality_embeddings.0.model."
AUDIO_EMBEDDING = "tied.embedding.modality_embeddings.0.embedding.weight"
TEXT_EMBEDDING = "tied.embedding.text_embedding.weight"
# Чем кодек собирает звук из кодов — только это живёт на видеокарте.
DECODER_PARTS = ("quantizer", "fc2", "acoustic_decoder")
# Коды образцов голоса: разбор образца стоит поднятия кодировщика (0.7 ГБ,
# секунды), а нужен один раз на файл.
REF_CODES = config.ROOT / "data" / "higgs_refs"
# Конфиг кодека в самих весах не лежит — он взят байт в байт из
# bosonai/higgs-audio-v2-tokenizer, как это делает SGLang-Omni.
CODEC_CONFIG = Path(__file__).with_name("higgs_codec.json")

# Образец длиннее не нужен: голос схватывается за 10–20 секунд, а каждая
# секунда образца — это 25 лишних позиций в каждой реплике.
MAX_REFERENCE_SECONDS = 20.0

# Длина статического кеша: затравка (образец до 20 с — 507 строк, плюс
# тексты) и сама реплика. 2560 позиций — это около 70 секунд речи за раз
# и 380 МБ видеопамяти. Реплики режутся по предложениям, так что с запасом.
MAX_POSITIONS = 2560

# Потоковый вывод: первый кусок — 12 кадров (0.48 с речи), дальше по 20.
# Свёрточному кодеку на краю куска нужны соседи. Замерено 25 сентября на
# склейке кусков против цельной записи: решает запас справа — 4 кадра
# дают 34 дБ (местами слышно), 8 кадров — 53 дБ (неслышно); запас слева
# больше 8 ничего не добавляет. Цена правого запаса — 70 мс до первого звука.
STREAM_FIRST = 12
STREAM_NEXT = 20
STREAM_OVERLAP = 8
STREAM_HOLDBACK = 8
# Бережный режим (config.HIGGS_GENTLE): сколько секунд звука держать готовыми
# впереди проигрывания. Модель пишет 60 кадров в секунду, звучат 25 — без
# тормоза видеокарта все эти секунды занята на 100%, и игра рядом теряет
# кадры. С запасом в полторы секунды шаги идут со скоростью звука, первый
# кусок — как и раньше, на полной скорости. При скорости речи выше единицы
# звук короче, поэтому и запас делится на speed — см. stream().
STREAM_LEAD = 1.5
# Часы и сон — через модуль, чтобы тест мог подставить свои.
_clock = time.perf_counter
_sleep = time.sleep

# Точность хребта: "fp8" — вдвое меньше видеопамяти и быстрее (по
# умолчанию), "bf16" — как в оригинале, эталон для сверки.
PRECISION = "fp8"
FP8_MAX = 448.0

# Рекомендация авторов для клонирования голоса.
TEMPERATURE = 0.8
TOP_K = 50


_DTYPES = {"BF16": "bfloat16", "F16": "float16", "F32": "float32", "F64": "float64",
           "I64": "int64", "I32": "int32", "I16": "int16", "I8": "int8",
           "U8": "uint8", "BOOL": "bool"}


def _index(path: Path) -> tuple[dict, int]:
    """Оглавление safetensors: имя → dtype, форма, смещения; и где начинаются данные."""
    with open(path, "rb") as f:
        size = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(size))
    header.pop("__metadata__", None)
    return header, 8 + size


def _in_file_order(index: dict):
    return sorted(index.items(), key=lambda item: item[1]["data_offsets"][0])


def _read(f, base: int, info: dict):
    """Один тензор из открытого файла весов — в собственную память."""
    import torch

    begin, end = info["data_offsets"]
    dtype = getattr(torch, _DTYPES[info["dtype"]])
    if end == begin:
        return torch.empty(info["shape"], dtype=dtype)
    f.seek(base + begin)
    buffer = bytearray(end - begin)
    f.readinto(buffer)
    return torch.frombuffer(buffer, dtype=dtype).reshape(info["shape"])


def _pack(weight, device, fp8: bool):
    """Матрица слоя на видеокарту: FP8 с масштабом на строку или bf16."""
    import torch

    w = weight.to(device)
    if not fp8:
        return w.to(torch.bfloat16)
    scale = w.float().abs().amax(dim=1).clamp_(min=1e-12).div_(FP8_MAX)
    w8 = (w.float() / scale[:, None]).to(torch.float8_e4m3fn)
    del w
    return w8, scale


def _weights_folder() -> Path:
    """Папка с весами: сначала с диска, в сеть — только если их ещё нет.

    Просто snapshot_download каждый раз сверяется с Hugging Face, и когда
    VPN барахлит, включение голоса висит минутами (25 сентября).
    """
    from huggingface_hub import snapshot_download

    try:
        return Path(snapshot_download(REPO, local_files_only=True))
    except Exception:
        return Path(snapshot_download(REPO))


def apply_delay(codes_tn):
    """[T, N] сырые коды → [T + N - 1, N] со сдвигом: книга c опаздывает на c шагов."""
    import torch

    frames, books = codes_tn.shape
    out = torch.full((frames + books - 1, books), EOC_ID, dtype=codes_tn.dtype)
    for book in range(books):
        out[:book, book] = BOC_ID
        out[book : book + frames, book] = codes_tn[:, book]
    return out


def remove_delay(delayed_ln):
    """Обратно: [L, N] со сдвигом → [L - N + 1, N]. Служебные значения — в ноль."""
    import torch

    length, books = delayed_ln.shape
    frames = length - (books - 1)
    if frames <= 0:
        return delayed_ln.new_zeros((0, books))
    out = torch.empty((frames, books), dtype=delayed_ln.dtype)
    for book in range(books):
        out[:, book] = delayed_ln[book : book + frames, book]
    return torch.where(out >= BOC_ID, torch.zeros_like(out), out)


class _Sampler:
    """Пошаговый автомат выборки: сдвиг книг на старте и доигрывание в конце.

    Первые N шагов книга c ещё не началась и получает BOC. Когда нулевая
    книга выдала EOC, остальным нужно ещё N-2 шага, чтобы доиграть свой
    хвост, — после этого генерация закончена. Повторяет `sampler.step`
    из SGLang-Omni.
    """

    def __init__(self, books: int):
        self.books = books
        self.delay = 0
        self.countdown: int | None = None
        self.done = False

    def step(self, logits_nv, temperature: float, top_k: int | None):
        import torch

        if temperature <= 1e-5:
            codes = logits_nv.argmax(dim=-1)
        else:
            logits = logits_nv / temperature
            if top_k:
                kth = logits.topk(min(top_k, logits.size(-1)), dim=-1).values[:, -1:]
                logits = torch.where(logits < kth, float("-inf"), logits)
            codes = logits.softmax(dim=-1).multinomial(1).squeeze(-1)

        if self.delay < self.books:
            codes[self.delay + 1 :] = BOC_ID
            self.delay += 1
        elif self.countdown is not None:
            self.countdown -= 1
            if self.countdown <= 0:
                self.done = True
        elif int(codes[0]) == EOC_ID:
            if self.books <= 2:
                self.done = True
            else:
                self.countdown = self.books - 2
        return codes


class _FastBody:
    """Тот же Qwen3, но без обвязки transformers — ради CUDA graph.

    Через transformers один шаг генерации стоил около 50 мс, хотя сама
    видеокарта успевает его за 10: всё остальное — сотни мелких запусков
    из Python на каждый из 36 слоёв. Шаг одинаковой формы, поэтому его
    можно записать один раз графом и дальше проигрывать целиком.

    Веса приходят уже на видеокарте: матрицы слоёв — через `_pack` (FP8
    или bf16), по одной прямо при чтении файла, чтобы ни на процессоре,
    ни на карте не держать 8 ГБ bf16 даже на миг. Кеш ключей и значений
    статический, на `max_len` позиций: граф требует неизменных адресов.
    """

    BIG = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")

    def __init__(self, cfg, layers: list[dict], norm, audio_embedding, books: int,
                 vocab: int, max_len: int):
        import torch

        self.device = audio_embedding.device
        self.unit = torch.ones((), dtype=torch.float32, device=self.device)
        self.heads = cfg.num_attention_heads
        self.kv_heads = cfg.num_key_value_heads
        self.head_dim = cfg.head_dim
        self.groups = self.heads // self.kv_heads
        self.scale = self.head_dim**-0.5
        self.hidden = cfg.hidden_size
        self.eps = cfg.rms_norm_eps
        self.books = books
        self.vocab = vocab
        self.max_len = max_len

        self.layers = [
            (
                layer["input_layernorm.weight"],
                layer["self_attn.q_proj.weight"],
                layer["self_attn.k_proj.weight"],
                layer["self_attn.v_proj.weight"],
                layer["self_attn.o_proj.weight"],
                layer["self_attn.q_norm.weight"],
                layer["self_attn.k_norm.weight"],
                layer["post_attention_layernorm.weight"],
                layer["mlp.gate_proj.weight"],
                layer["mlp.up_proj.weight"],
                layer["mlp.down_proj.weight"],
            )
            for layer in layers
        ]
        self.norm = norm
        self.audio = audio_embedding
        dtype = audio_embedding.dtype

        shape = (len(self.layers), 1, self.kv_heads, max_len, self.head_dim)
        self.k = torch.zeros(shape, dtype=dtype, device=self.device)
        self.v = torch.zeros(shape, dtype=dtype, device=self.device)

        # Повороты как в transformers: считаются в fp32, в модель идут в bf16.
        theta = cfg.rope_parameters["rope_theta"]
        inv_freq = 1.0 / (
            theta ** (torch.arange(0, self.head_dim, 2, dtype=torch.float32) / self.head_dim)
        )
        freqs = torch.outer(torch.arange(max_len, dtype=torch.float32), inv_freq)
        angles = torch.cat((freqs, freqs), dim=-1).to(self.device)
        self.cos = angles.cos().to(dtype)
        self.sin = angles.sin().to(dtype)
        self.positions = torch.arange(max_len, device=self.device)
        self.neg_inf = torch.tensor(float("-inf"), device=self.device)
        self.offsets = torch.arange(books, device=self.device) * vocab

        # Постоянные входы и выход графа.
        self.in_codes = torch.zeros(books, dtype=torch.long, device=self.device)
        self.in_pos = torch.zeros(1, dtype=torch.long, device=self.device)
        self.out_logits = torch.zeros(1, books * vocab, dtype=dtype, device=self.device)
        self.graph = None

    def _lin(self, x, w):
        """x [..., K] @ Wᵀ. FP8: активации — по строкам, масштабы после умножения."""
        import torch
        import torch.nn.functional as F

        if not isinstance(w, tuple):
            return F.linear(x, w)
        w8, w_scale = w
        lead = x.shape[:-1]
        x2 = x.reshape(-1, x.shape[-1]).float()
        x_scale = x2.abs().amax(dim=1, keepdim=True).clamp_(min=1e-12).div_(FP8_MAX)
        x8 = (x2 / x_scale).to(torch.float8_e4m3fn)
        out = torch._scaled_mm(x8, w8.t(), scale_a=self.unit, scale_b=self.unit,
                               out_dtype=torch.float32)
        return (out * x_scale * w_scale).to(x.dtype).reshape(*lead, -1)

    def _rms(self, x, weight):
        import torch

        xf = x.float()
        xf = xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + self.eps)
        return weight * xf.to(x.dtype)

    def _rope(self, x, cos, sin):
        import torch

        half = self.head_dim // 2
        rotated = torch.cat((-x[..., half:], x[..., :half]), dim=-1)
        return x * cos + rotated * sin

    def embed_codes(self, codes_ln):
        """[L, N] коды → [L, D]: эмбеддинги восьми книг складываются."""
        import torch.nn.functional as F

        return F.embedding(codes_ln + self.offsets, self.audio).sum(dim=-2)

    def forward(self, x, positions, decode: bool):
        """x [1, T, D] → [1, T, D]. Пишет ключи и значения в кеш по positions."""
        import torch
        import torch.nn.functional as F

        T = x.shape[1]
        cos = self.cos[positions]
        sin = self.sin[positions]
        mask = None
        if decode:
            mask = (self.positions <= positions).view(1, 1, 1, self.max_len)

        for i, (ln1, wq, wk, wv, wo, qn, kn, ln2, wg, wu, wd) in enumerate(self.layers):
            h = self._rms(x, ln1)
            q = self._rms(self._lin(h, wq).view(1, T, self.heads, self.head_dim), qn)
            k = self._rms(self._lin(h, wk).view(1, T, self.kv_heads, self.head_dim), kn)
            v = self._lin(h, wv).view(1, T, self.kv_heads, self.head_dim).transpose(1, 2)
            q = self._rope(q.transpose(1, 2), cos, sin)
            k = self._rope(k.transpose(1, 2), cos, sin)
            self.k[i].index_copy_(2, positions, k)
            self.v[i].index_copy_(2, positions, v)
            if decode:
                # Своё внимание вместо scaled_dot_product_attention: с маской
                # и группами голов оно на Windows уходит в самый медленный
                # путь — 0.8 мс на слой, больше половины всего шага. Здесь
                # четыре головы запроса делят одну голову ключей, и это
                # просто два умножения матриц.
                qg = q.view(1, self.kv_heads, self.groups, self.head_dim)
                scores = torch.matmul(qg, self.k[i].transpose(-1, -2)).float() * self.scale
                scores = torch.where(mask, scores, self.neg_inf)
                probs = scores.softmax(dim=-1).to(q.dtype)
                att = torch.matmul(probs, self.v[i]).view(1, self.heads, 1, self.head_dim)
            else:
                att = F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=True)
            x = x + self._lin(att.transpose(1, 2).reshape(1, T, -1), wo)
            h = self._rms(x, ln2)
            x = x + self._lin(F.silu(self._lin(h, wg)) * self._lin(h, wu), wd)
        return self._rms(x, self.norm)

    def logits(self, hidden_d):
        """[D] → [N, V] в fp32 для выборки."""
        import torch.nn.functional as F

        return F.linear(hidden_d, self.audio).view(self.books, self.vocab).float()

    def prefill(self, embeds_td):
        """Затравка целиком, с нулевой позиции. Возвращает логиты первого шага."""
        import torch

        T = embeds_td.shape[0]
        if T >= self.max_len:
            raise ValueError(f"затравка длиннее кеша: {T} ≥ {self.max_len}")
        hidden = self.forward(embeds_td.unsqueeze(0), self.positions[:T], decode=False)
        return self.logits(hidden[0, -1])

    def _step_body(self):
        x = self.embed_codes(self.in_codes.view(1, self.books)).view(1, 1, self.hidden)
        hidden = self.forward(x, self.in_pos, decode=True)
        import torch.nn.functional as F

        self.out_logits.copy_(F.linear(hidden[:, -1], self.audio))

    def capture(self) -> None:
        """Записывает шаг генерации графом. Разогрев — на отдельном потоке."""
        import torch

        stream = torch.cuda.Stream(device=self.device)
        stream.wait_stream(torch.cuda.current_stream(self.device))
        with torch.cuda.stream(stream):
            for _ in range(3):
                self._step_body()
        torch.cuda.current_stream(self.device).wait_stream(stream)
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph):
            self._step_body()
        torch.cuda.synchronize(self.device)
        self.graph = graph

    def step(self, codes_n, position: int):
        """Один шаг: коды прошлого кадра на позиции position → логиты [N, V]."""
        self.in_codes.copy_(codes_n)
        self.in_pos.fill_(position)
        if self.graph is not None:
            self.graph.replay()
        else:
            self._step_body()
        return self.out_logits.view(self.books, self.vocab).float()


class HiggsVoice:
    """Голос ассистента на Higgs TTS 3. Держит модель в видеопамяти."""

    def __init__(self, device: str = "cuda", precision: str | None = None):
        self.device = device
        self.precision = precision or PRECISION
        # Повторно входимый: load() зовётся и изнутри синтеза, под тем же замком.
        self._lock = threading.RLock()
        self._body = None  # transformers-хребет — только эталон для сверки
        self._fast: _FastBody | None = None
        self._embed = None  # таблица слов: np.memmap на кусок файла весов
        self._weights: Path | None = None
        self._audio_embedding = None
        self._decoder = None  # декодер кодека на видеокарте — синтез
        self._tokens = None
        self._ids: dict[str, int] = {}
        self._books = 8
        self._vocab = 1026
        # Коды образца со сдвигом — считаются один раз на файл.
        self._ref_cache: dict[tuple[str, float], tuple] = {}

    # --- Загрузка ----------------------------------------------------------

    def load(self, reference: bool = False) -> None:
        """Грузит модель и кодек. Первый раз качает 9.3 ГБ.

        reference=True — ещё и хребет transformers в bf16, эталон для сверки
        (fast=False). В обычной работе не нужен.
        """
        if self._fast is not None and (self._body is not None or not reference):
            return
        with self._lock:
            if self._fast is not None and (self._body is not None or not reference):
                return
            self._load(reference)

    def _load(self, reference: bool = False) -> None:
        import torch
        from tokenizers import Tokenizer
        from transformers import (
            HiggsAudioV2TokenizerConfig,
            HiggsAudioV2TokenizerModel,
            Qwen3Config,
            Qwen3Model,
        )
        from transformers.models.qwen3.modeling_qwen3 import Qwen3RotaryEmbedding

        # FP8 (`torch._scaled_mm`) есть только у RTX 40 и 50 серии (compute
        # capability 8.9 и выше). На RTX 30 и старше умножение падает с
        # невнятной ошибкой CUDA — да ещё после скачивания 9 ГБ весов. Говорим
        # сразу и словами (29.09, проверка выпуска: у автора 5070 Ti, у людей —
        # любые карты).
        if self.precision == "fp8" and str(self.device).startswith("cuda"):
            if not torch.cuda.is_available():
                raise RuntimeError("Higgs нужна видеокарта NVIDIA — выбери голос Silero")
            if tuple(torch.cuda.get_device_capability(self.device)) < (8, 9):
                raise RuntimeError(
                    "Higgs работает только на видеокартах RTX 40 и 50 серии — "
                    "на этой выбери ESpeech или Silero")

        folder = _weights_folder()
        raw = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        audio_cfg = raw["audio_encoder_config"]
        self._books = int(audio_cfg["num_codebooks"])
        self._vocab = int(audio_cfg["vocab_size"])

        text_cfg = {
            k: v
            for k, v in raw["text_config"].items()
            if not k.startswith("_") and k != "architectures"
        }
        body_cfg = Qwen3Config(**text_cfg)
        body_cfg._attn_implementation = "sdpa"
        fp8 = self.precision == "fp8"

        # Файл читается по одному тензору, и каждый сразу уходит на
        # видеокарту в нужной точности (см. шапку модуля: safe_open держал
        # в оперативке все 9.3 ГБ).
        weights = folder / "model.safetensors"
        index, base = _index(weights)
        layers: list[dict] = [{} for _ in range(body_cfg.num_hidden_layers)]
        state: dict[str, torch.Tensor] = {}  # эталонный хребет, только reference
        decoder_state: dict[str, torch.Tensor] = {}
        audio_embedding = norm = None
        with open(weights, "rb") as f:
            for name, info in _in_file_order(index):
                if name.startswith(CODEC_PREFIX):
                    rest = name[len(CODEC_PREFIX) :]
                    if rest.split(".", 1)[0] in DECODER_PARTS:
                        decoder_state[rest] = _read(f, base, info)
                elif name == AUDIO_EMBEDDING:
                    audio_embedding = _read(f, base, info).to(self.device, dtype=torch.bfloat16)
                elif name == TEXT_EMBEDDING:
                    if reference:
                        state["embed_tokens.weight"] = _read(f, base, info).to(self.device)
                elif name.startswith("body."):
                    rest = name[len("body.") :]
                    tensor = _read(f, base, info)
                    if reference:
                        tensor = tensor.to(self.device)
                        state[rest] = tensor
                    if rest.startswith("layers."):
                        number, key = rest[len("layers.") :].split(".", 1)
                        big = key.split(".")[-2] in _FastBody.BIG
                        layers[int(number)][key] = _pack(tensor, self.device, fp8 and big)
                    elif rest == "norm.weight":
                        norm = tensor.to(self.device, dtype=torch.bfloat16)
                    del tensor
        if audio_embedding is None or norm is None:
            raise RuntimeError("Higgs: в весах нет эмбеддингов звука или последней нормы")

        # Таблица слов: 151 тысяча строк, а фразе нужно сотня-другая.
        # Отображаем только её кусок файла и только на чтение — строки
        # подтягиваются с диска по мере надобности и не держат память.
        info = index.get(TEXT_EMBEDDING)
        if info is None or info["dtype"] != "BF16":
            raise RuntimeError("Higgs: в весах нет таблицы слов в bf16")
        embed = np.memmap(
            weights, dtype=np.uint16, mode="r",
            offset=base + info["data_offsets"][0], shape=tuple(info["shape"]),
        )

        body = None
        if reference:
            # Эталон: хребет transformers целиком в bf16 на видеокарте.
            with torch.device("meta"):
                body = Qwen3Model(body_cfg)
            missing, unexpected = body.load_state_dict(state, strict=False, assign=True)
            if missing or unexpected:
                raise RuntimeError(
                    f"Higgs: веса хребта не сошлись — нет {missing[:5]}, лишние {unexpected[:5]}"
                )
            # Таблица поворотов не хранится в весах, а на каркасе без памяти
            # её нет вовсе — строим заново уже на видеокарте.
            body.rotary_emb = Qwen3RotaryEmbedding(config=body_cfg).to(self.device)
            body.eval()
        del state

        # Кодек держим в fp32: его транспонированные свёртки в bf16
        # неустойчивы — предупреждение авторов SGLang-Omni. Каркас без
        # памяти, на видеокарту — только части, которыми собирается звук
        # (85 МБ). Все их буферы лежат в весах, так что strict=True ловит
        # любую недостачу, а не оставляет мусор после to_empty.
        codec_cfg = HiggsAudioV2TokenizerConfig.from_json_file(str(CODEC_CONFIG))
        with torch.device("meta"):
            codec = HiggsAudioV2TokenizerModel(codec_cfg)
        decoder = torch.nn.Module()
        for part in DECODER_PARTS:
            module = getattr(codec, part).to_empty(device=self.device)
            prefix = part + "."
            module.load_state_dict(
                {k[len(prefix) :]: v for k, v in decoder_state.items() if k.startswith(prefix)},
                strict=True,
            )
            module.float().eval()
            for p in module.parameters():
                p.requires_grad_(False)
            setattr(decoder, part, module)
        del codec, decoder_state

        tokens = Tokenizer.from_file(str(folder / "tokenizer.json"))
        for name in ("<|tts|>", "<|ref_text|>", "<|ref_audio|>", "<|text|>", "<|audio|>"):
            token = tokens.token_to_id(name)
            if token is None:
                raise RuntimeError(f"Higgs: в словаре нет {name}")
            self._ids[name] = token

        self._tokens = tokens
        self._weights = weights
        self._embed = embed
        self._audio_embedding = audio_embedding
        self._decoder = decoder
        fast = _FastBody(
            body_cfg, layers, norm, audio_embedding, self._books, self._vocab, MAX_POSITIONS,
        )
        del layers
        with torch.inference_mode():
            fast.capture()
        self._body = body
        self._fast = fast

    # --- Входы -------------------------------------------------------------

    def _embed_codes(self, codes_ln):
        """[L, N] коды → [L, D]: эмбеддинги восьми книг складываются."""
        import torch
        import torch.nn.functional as F

        offsets = torch.arange(self._books, device=codes_ln.device) * self._vocab
        return F.embedding(codes_ln + offsets, self._audio_embedding).sum(dim=-2)

    def _embed_text(self, ids_cpu):
        """Строки таблицы слов для id → [T, D] bf16 на процессоре."""
        import torch

        rows = np.ascontiguousarray(self._embed[ids_cpu.numpy()])
        return torch.from_numpy(rows).view(torch.bfloat16)

    def _reference_codes(self, ref) -> tuple:
        """Коды образца со сдвигом и его текст. Кешируются в памяти и на диске."""
        import torch

        path = Path(ref.audio)
        stat = path.stat()
        key = (str(path), stat.st_mtime)
        cached = self._ref_cache.get(key)
        if cached is not None:
            return cached

        saved = REF_CODES / f"{path.stem}@{stat.st_mtime_ns}_{stat.st_size}.pt"
        codes_tn = None
        if saved.exists():
            try:
                codes_tn = torch.load(saved, weights_only=True).to(torch.long)
            except Exception:
                codes_tn = None
        if codes_tn is None:
            codes_tn = self._encode_reference(path)
            try:
                REF_CODES.mkdir(parents=True, exist_ok=True)
                for old in REF_CODES.glob("*.pt"):
                    if old.name.rsplit("@", 1)[0] == path.stem and old != saved:
                        old.unlink()
                torch.save(codes_tn.to(torch.int16), saved)
            except OSError:
                pass
        if codes_tn.shape[1] != self._books:
            raise RuntimeError(
                f"Higgs: кодек выдал {codes_tn.shape[1]} книг вместо {self._books}"
            )
        result = (apply_delay(codes_tn), (ref.text or "").strip())
        self._ref_cache[key] = result
        return result

    def _encode_reference(self, path: Path):
        """Разбор образца кодировщиком кодека — на процессоре, и сразу долой.

        Кодировщик (HuBERT и прочее) весит 0.7 ГБ, а нужен раз на файл
        образца: держать его постоянно — отнимать память у игр.
        """
        import soundfile as sf
        import torch
        import torch.nn.functional as F
        import torchaudio
        from transformers import HiggsAudioV2TokenizerConfig, HiggsAudioV2TokenizerModel

        codec = HiggsAudioV2TokenizerModel(
            HiggsAudioV2TokenizerConfig.from_json_file(str(CODEC_CONFIG))
        ).float().eval()
        index, base = _index(self._weights)
        state = {}
        with open(self._weights, "rb") as f:
            for name, info in _in_file_order(index):
                if name.startswith(CODEC_PREFIX):
                    state[name[len(CODEC_PREFIX) :]] = _read(f, base, info)
        missing, _ = codec.load_state_dict(state, strict=False)
        if len(missing) > len(state) // 2:
            raise RuntimeError(
                f"Higgs: кодек не загрузился — не хватает {len(missing)} тензоров"
            )
        del state

        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        wave = torch.from_numpy(data.mean(axis=1).copy())
        if rate != SAMPLE_RATE:
            wave = torchaudio.functional.resample(wave, rate, SAMPLE_RATE)
        wave = wave[: int(MAX_REFERENCE_SECONDS * SAMPLE_RATE)]
        # Кодек падает на обрывке короче секунды — добиваем тишиной.
        if wave.numel() < SAMPLE_RATE:
            wave = F.pad(wave, (0, SAMPLE_RATE - wave.numel()))

        with torch.inference_mode():
            codes = codec.encode(wave.view(1, 1, -1)).audio_codes
        return codes[0].transpose(0, 1).to(torch.long).cpu()

    def _prompt(self, text: str, ref_rows: int, ref_text: str) -> list[int]:
        ids = [self._ids["<|tts|>"]]
        if ref_rows and ref_text:
            ids.append(self._ids["<|ref_text|>"])
            ids += self._tokens.encode(ref_text, add_special_tokens=False).ids
        if ref_rows:
            ids.append(self._ids["<|ref_audio|>"])
            ids += [PLACEHOLDER] * ref_rows
        ids.append(self._ids["<|text|>"])
        ids += self._tokens.encode(text, add_special_tokens=False).ids
        ids.append(self._ids["<|audio|>"])
        return ids

    # --- Синтез ------------------------------------------------------------

    def _rows(
        self,
        text: str,
        ref=None,
        temperature: float = TEMPERATURE,
        top_k: int | None = TOP_K,
        max_frames: int | None = None,
        fast: bool | None = None,
    ):
        """Строки кодов со сдвигом по одной, по мере генерации.

        Отдаёт пары (строка [N] на видеокарте, закончила ли сама). Следующий
        шаг ставится в очередь видеокарты до того, как строка уйдёт наружу:
        пока снаружи разбирают звук, модель уже считает дальше.

        fast=False — медленный путь через transformers, для сверки.
        """
        import torch

        self.load()
        ref_delayed, ref_text = (None, "")
        if ref is not None:
            ref_delayed, ref_text = self._reference_codes(ref)
        ref_rows = 0 if ref_delayed is None else ref_delayed.shape[0]

        ids = self._prompt(text, ref_rows, ref_text)
        if max_frames is None:
            # Потолок против бесконечного бормотания: русская речь идёт
            # примерно 15 знаков в секунду, берём с большим запасом.
            max_frames = int(FRAMES_PER_SECOND * (3 + len(text) * 0.15))

        body = self._body
        fast = self._fast if fast is None else (self._fast if fast else None)
        if fast is None and body is None:
            raise RuntimeError("эталонный путь: грузи с load(reference=True)")
        sampler = _Sampler(self._books)
        cache = None
        with torch.inference_mode():
            ids_cpu = torch.tensor(ids)
            spots_cpu = ids_cpu == PLACEHOLDER
            safe = torch.where(spots_cpu, torch.zeros_like(ids_cpu), ids_cpu)
            embeds = self._embed_text(safe).to(self.device)
            spots = spots_cpu.to(self.device)
            if ref_rows:
                embeds[spots] = self._embed_codes(ref_delayed.to(self.device)).to(embeds.dtype)
            if fast is not None:
                # Потолок кадров ещё и по размеру кеша: дальше писать некуда.
                limit = min(max_frames + self._books, fast.max_len - len(ids) - 1)
                logits = fast.prefill(embeds)
            else:
                # Медленный путь через transformers — эталон для сверки.
                limit = max_frames + self._books
                out = body(inputs_embeds=embeds.unsqueeze(0), use_cache=True)
                cache = out.past_key_values
                logits = self._head(out.last_hidden_state[0, -1])

        position = len(ids)
        for _ in range(limit):
            with torch.inference_mode():
                codes = sampler.step(logits, temperature, top_k)
                if not sampler.done:
                    if fast is not None:
                        logits = fast.step(codes, position)
                    else:
                        step_in = self._embed_codes(codes.unsqueeze(0)).unsqueeze(0)
                        out = body(inputs_embeds=step_in, past_key_values=cache, use_cache=True)
                        cache = out.past_key_values
                        logits = self._head(out.last_hidden_state[0, -1])
                    position += 1
            yield codes, sampler.done
            if sampler.done:
                break

    def _head(self, hidden_d):
        import torch.nn.functional as F

        return F.linear(hidden_d, self._audio_embedding).view(self._books, self._vocab).float()

    def codes_for(
        self,
        text: str,
        ref=None,
        temperature: float = TEMPERATURE,
        top_k: int | None = TOP_K,
        max_frames: int | None = None,
        seed: int | None = None,
        fast: bool | None = None,
    ):
        """Текст → ([T, N] коды звука без сдвига, закончила ли сама)."""
        import torch

        if seed is not None:
            torch.manual_seed(seed)
        rows = []
        done = False
        for codes, done in self._rows(text, ref, temperature, top_k, max_frames, fast):
            rows.append(codes)
        return remove_delay(torch.stack(rows).cpu()), done

    def _window(self, rows: list, start: int, stop: int, context: int) -> np.ndarray:
        """Звук кадров [start, stop) из строк со сдвигом.

        Кодек — свёрточный: на краю куска ему не хватает соседей, и шов
        слышен щелчком. Поэтому декодируем с запасом — OVERLAP кадров слева
        и до `context` справа — и отрезаем лишнее.
        """
        import torch

        left = max(0, start - STREAM_OVERLAP)
        delayed = torch.stack(rows[left : context + self._books - 1]).cpu()
        audio = self.decode(remove_delay(delayed))
        spf = SAMPLE_RATE // FRAMES_PER_SECOND
        head = (start - left) * spf
        if stop >= context:
            return audio[head:]
        return audio[head : (stop - left) * spf]

    def decode(self, codes_tn) -> np.ndarray:
        """[T, N] коды → звук 24 кГц."""
        import torch

        if codes_tn.shape[0] == 0:
            return np.zeros(0, dtype=np.float32)
        with torch.inference_mode():
            # То же, что HiggsAudioV2TokenizerModel.decode, только частями,
            # которые лежат на видеокарте.
            codes = codes_tn.transpose(0, 1).unsqueeze(0).to(self.device).transpose(0, 1)
            quantized = self._decoder.quantizer.decode(codes)
            acoustic = self._decoder.fc2(quantized.transpose(1, 2)).transpose(1, 2)
            audio = self._decoder.acoustic_decoder(acoustic)
        return audio.squeeze().float().cpu().numpy().astype(np.float32)

    @staticmethod
    def _spoken(text: str, speed: float) -> str:
        """Текст в том виде, в каком он уйдёт в модель.

        Цифры переводятся в слова заранее: модель их читает, но падеж и
        род угадывает хуже нашего разборщика. Латиницу оставляем — её
        она читает сама и по-английски.

        Скорость сюда намеренно не попадает: раньше от неё зависела метка
        `<|prosody:speed_*|>`, но толку от неё хозяин не увидел — 26 сентября
        при 1.2 всё равно говорило медленно. Теперь темп задаёт растяжение
        звука (`core.tempo`), и двойной эффект не нужен. Параметр оставлен
        ради общего вида с ESpeech.
        """
        return for_speech(text, keep_latin=True).strip()

    def say(
        self,
        text: str,
        ref=None,
        nfe_step: int | None = None,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """Синтезирует реплику целиком. Возвращает (сигнал, частота).

        nfe_step принят ради общего вида с ESpeech — Higgs он не нужен.
        """
        spoken = self._spoken(text, speed)
        if not spoken:
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE
        with self._lock:
            codes, _ = self.codes_for(spoken, ref)
            wave = self.decode(codes)
        return stretch(wave, SAMPLE_RATE, speed), SAMPLE_RATE

    def stream(self, text: str, ref=None, speed: float = 1.0, gentle: bool | None = None):
        """Та же реплика кусками (сигнал, частота) — звук идёт, пока
        предложение ещё пишется.

        Модель выдаёт 60 кадров в секунду, а звучат они по 25: первый кусок
        готов через четверть секунды. Дальше в бережном режиме (gentle, по
        умолчанию config.HIGGS_GENTLE) синтез держится лишь на STREAM_LEAD
        секунд впереди звука, иначе — уходит вперёд на полной скорости.
        Последние STREAM_HOLDBACK кадров придерживаются до следующего куска —
        им нужен правый сосед, иначе шов щёлкнет.

        Скорость (`core.tempo`) растягивает уже готовый звук, одним
        растягивателем на всё предложение: куски идут в динамик подряд, и
        растягивать каждый отдельно означало бы рвать фразу на швах. Из-за
        этого же пересчитан запас в бережном режиме: секунда сгенерированного
        звука звучит 1/speed секунды, поэтому в счёт опережения идёт
        `emitted / (FRAMES_PER_SECOND * speed)`. Без этого при speed > 1
        тормоз был бы рассчитан на чужой темп: куски начали бы отставать от
        звука, и на телефоне пошли бы дыры.

        Генератор держит замок синтеза: бросил на полпути — закрой его
        (`close()`), иначе замок освободится только со сборкой мусора.
        """
        spoken = self._spoken(text, speed)
        if not spoken:
            return
        if gentle is None:
            gentle = bool(getattr(config, "HIGGS_GENTLE", True))
        with self._lock:
            rows = []
            emitted = 0
            behind = self._books - 1
            started = None
            tempo = Stretcher(SAMPLE_RATE, speed)
            for codes, _ in self._rows(spoken, ref):
                rows.append(codes)
                ready = len(rows) - behind - STREAM_HOLDBACK
                need = STREAM_FIRST if emitted == 0 else STREAM_NEXT
                if ready - emitted >= need:
                    part = tempo.push(
                        self._window(rows, emitted, ready, len(rows) - behind)
                    )
                    emitted = ready
                    if len(part):
                        yield part, SAMPLE_RATE
                    if started is None and len(part):
                        # Отсюда звук и пошёл: отсчёт запаса — от этого мига.
                        started = _clock()
                if gentle and started is not None:
                    ahead = (emitted / FRAMES_PER_SECOND / tempo.speed
                             - (_clock() - started))
                    if ahead > STREAM_LEAD:
                        _sleep(ahead - STREAM_LEAD)
            total = len(rows) - behind
            if total > emitted:
                part = tempo.push(self._window(rows, emitted, total, total))
                emitted = total
                if len(part):
                    yield part, SAMPLE_RATE
            # Хвост растяжения: последние окна ещё не доделаны.
            tail = tempo.flush()
            if len(tail):
                yield tail, SAMPLE_RATE

    def accent(self, text: str) -> str:
        """Ударения она ставит сама — показываем то, что уйдёт в модель."""
        return for_speech(text, keep_latin=True)

    @property
    def loaded(self) -> bool:
        return self._fast is not None

    def unload(self) -> None:
        """Отдаёт видеопамять. Следующий load() поднимет всё заново (~10 с).

        Видеокарта принадлежит играм: держать 4 ГБ, пока голос выключен,
        нельзя.
        """
        with self._lock:
            if self._fast is None and self._body is None:
                return
            self._fast = None
            self._body = None
            self._decoder = None
            self._embed = None
            self._audio_embedding = None
            self._ref_cache.clear()
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def vram_used_mb(self) -> float:
        import torch

        if not torch.cuda.is_available():
            return 0.0
        return torch.cuda.memory_allocated() / 1024 / 1024


_shared: HiggsVoice | None = None
_shared_lock = threading.Lock()


def shared() -> HiggsVoice:
    """Один экземпляр на процесс: вторая копия — ещё 4 ГБ у игр."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = HiggsVoice()
        return _shared


def release() -> None:
    """Выгрузить общий экземпляр, если он поднят."""
    with _shared_lock:
        voice = _shared
    if voice is not None:
        voice.unload()
