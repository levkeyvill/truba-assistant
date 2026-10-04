r"""Отпечаток голоса хозяина: отличить его от колонок и от чужих людей.

Зачем. Микрофон слышит всё в комнате разом — ютуб, дискорд, её собственную
речь и живых людей рядом. Отличить по громкости нельзя, по словам нельзя.
А вот по голосу — можно: у каждого человека свой тембр, и модель сводит
его в набор из 256 чисел. Похожие голоса дают похожие наборы.

Отпечаток считается на процессоре для уже выделенной фразы.
"""

from __future__ import annotations

import numpy as np

import config

# Модель отпечатков. Та же библиотека, что крутит GigaAM и Silero VAD,
# так что ставить ничего не нужно.
MODEL = "wespeaker/wespeaker-voxceleb-resnet34-LM"

PRINT_FILE = config.DATA_DIR / "voiceprint.npz"

# Короткие реплики дают ненадёжный отпечаток, поэтому фразы короче
# полутора секунд проходят без проверки голоса.
MIN_SECONDS = 1.5
# Короткие фразы не судим, но сходство для них пишем в журнал: по живым
# цифрам видно, можно ли отсекать и их. Короче этого не считаем вовсе.
SHORT_SECONDS = 0.6

# Текст для записи образца. Читается вслух примерно полминуты.
#
# Он не случайный: тут собраны шипящие, свистящие, мягкие и твёрдые,
# раскатистое «р», гласные во всех позициях и числа. Чем разнообразнее
# звуки, тем полнее отпечаток и тем реже она потом будет путать.
# Читать надо обычным голосом, с обычного места — как разговариваешь
# с ней каждый день, а не как диктор новостей.
ENROLL_TEXT = """Ну что, давай знакомиться как следует.

За окном опять зарядил дождь, я сижу перед двумя мониторами
с чашкой остывшего чая, и мне лень куда-то идти.

Жёлтый шмель жужжит над шершавой щепкой, а рыжий кот дрыхнет
на широком подоконнике. Шестьдесят три, двести сорок восемь.

Запусти дискорд, сохрани момент, покажи что там на экране —
вот это я и буду тебе говорить каждый день."""


class Voiceprint:
    """Считает отпечатки и сравнивает их с записанным образцом."""

    def __init__(self):
        self._model = None
        self._own: np.ndarray | None = None
        self._loaded_own = False

    # --- Модель -----------------------------------------------------------

    def load(self) -> None:
        """Поднимает модель. Первый раз скачивает, дальше из кеша."""
        if self._model is not None:
            return
        from onnx_asr.loader import Manager

        from core import onnx_opts

        # Без кручения потоков — как у детектора речи и распознавания.
        self._model = Manager(sess_options=onnx_opts.quiet()).create_se(MODEL)

    @property
    def ready(self) -> bool:
        return self._model is not None

    # --- Отпечатки --------------------------------------------------------

    def embed(self, wave: np.ndarray, rate: int = config.SAMPLE_RATE,
              min_seconds: float = MIN_SECONDS) -> np.ndarray | None:
        """Отпечаток куска речи. None — кусок короче `min_seconds`.

        Вектор возвращается единичной длины: тогда сравнение сводится
        к скалярному произведению, и ни делить, ни нормировать больше
        нигде не приходится.
        """
        if wave is None or len(wave) < min_seconds * rate:
            return None

        self.load()

        if rate != config.SAMPLE_RATE:
            if rate % config.SAMPLE_RATE:
                raise ValueError(f"частота {rate} не делится на {config.SAMPLE_RATE}")
            step = rate // config.SAMPLE_RATE
            usable = len(wave) // step * step
            wave = wave[:usable].reshape(-1, step).mean(axis=1)

        vector = np.asarray(
            self._model.embedding(
                wave.astype(np.float32), sample_rate=config.SAMPLE_RATE
            )
        ).ravel()

        length = float(np.linalg.norm(vector))
        return vector / length if length else None

    # --- Образец хозяина --------------------------------------------------

    def enroll(self, pieces: list[np.ndarray], rate: int = config.SAMPLE_RATE) -> int:
        """Запоминает голос по нескольким кускам. Возвращает, сколько взял.

        Усредняем несколько отпечатков: в одной фразе человек может
        говорить нетипично — простыть, зевнуть, отвернуться от микрофона.
        Среднее по кускам описывает его устойчивее.
        """
        vectors = []
        for piece in pieces:
            vector = self.embed(piece, rate)
            if vector is not None:
                vectors.append(vector)

        if not vectors:
            raise RuntimeError("не из чего считать отпечаток — речи не нашлось")

        mean = np.mean(vectors, axis=0)
        mean = mean / np.linalg.norm(mean)

        PRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            PRINT_FILE,
            own=mean,
            parts=np.array(vectors, dtype=np.float32),
            model=MODEL,
        )
        self._own = mean
        self._loaded_own = True
        return len(vectors)

    def own(self) -> np.ndarray | None:
        """Записанный образец хозяина. None — его ещё не записывали."""
        if self._loaded_own:
            return self._own

        self._loaded_own = True
        self._own = None
        if PRINT_FILE.exists():
            try:
                data = np.load(PRINT_FILE)
                # Образец, снятый другой моделью, сравнивать не с чем.
                if str(data.get("model", MODEL)) == MODEL:
                    self._own = data["own"]
            except Exception:
                self._own = None
        return self._own

    @property
    def known(self) -> bool:
        return self.own() is not None

    def forget(self) -> None:
        PRINT_FILE.unlink(missing_ok=True)
        self._own = None
        self._loaded_own = True

    # --- Сравнение --------------------------------------------------------

    def similarity(self, wave: np.ndarray, rate: int = config.SAMPLE_RATE,
                   min_seconds: float = MIN_SECONDS) -> float | None:
        """Насколько это похоже на хозяина: от -1 до 1.

        None — сравнить не с чем: образца нет или кусок короче `min_seconds`.
        """
        own = self.own()
        if own is None:
            return None
        vector = self.embed(wave, rate, min_seconds)
        if vector is None:
            return None
        return float(vector @ own)

    def spread(self) -> dict | None:
        """Насколько разошлись куски самого образца.

        Это нижняя граница того, что можно ждать от живой фразы: если уж
        его собственные куски между собой на 0.7, то порог выше ставить
        бессмысленно — он начнёт отвергать сам себя.
        """
        if not PRINT_FILE.exists():
            return None
        try:
            parts = np.load(PRINT_FILE)["parts"]
        except Exception:
            return None
        if len(parts) < 2:
            return None

        pairs = [
            float(parts[i] @ parts[j])
            for i in range(len(parts))
            for j in range(i + 1, len(parts))
        ]
        return {
            "parts": len(parts),
            "mean": float(np.mean(pairs)),
            "worst": float(np.min(pairs)),
        }
