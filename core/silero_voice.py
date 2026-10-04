r"""Синтез речи через Silero v5 — основной движок.

Заметно быстрее ESpeech (RTF около 0.03 против 0.40), работает на процессоре
и не занимает видеокарту. Сам расставляет ударения и букву «ё», включая
омографы, поэтому RUAccent ему не нужен.

Голос не клонируется — есть пять готовых. Если понадобится конкретный тембр,
в проекте остаётся ESpeech (core/tts.py).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import numpy as np

import config
from core.speech_text import for_speech, speakable

SAMPLE_RATE = 48000
# Больше этого Silero за раз не синтезирует — длинный текст режем сами.
MAX_CHARS = 900


# --- Скачивание модели --------------------------------------------------
# Сам silero качает список моделей и модель через torch.hub.download_url_to_file
# без срока ожидания: при обрыве соединения загрузка может зависнуть.
# Здесь то же самое, но со сроком и повторами, и в те же места, где silero
# их потом ищет: список — `latest_silero_models.yml` в рабочей папке, модель —
# `silero/model/<файл>` внутри установленного пакета.

MODELS_LIST_URL = "https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml"
DOWNLOAD_ATTEMPTS = 3
BUNDLED_MODEL = "v5_5_ru"
# Официальная модель Silero v5.5. В полном ZIP
# она лежит в models/silero; исходники на GitHub по-прежнему без модели.
BUNDLED_MODEL_SHA256 = "50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437"
# Сколько ждём следующего куска данных. Не всей загрузки: 140 МБ на медленном
# интернете идут минутами, а вот тишина в минуту — это уже обрыв.
READ_TIMEOUT = 60.0


def _download(url: str, target, attempts: int = DOWNLOAD_ATTEMPTS) -> None:
    """Скачать файл с повторами: сначала в `.part`, потом переименовать."""
    import os

    import httpx

    part = str(target) + ".part"
    last = None
    for _ in range(attempts):
        try:
            with httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=20.0),
                              follow_redirects=True) as client:
                with client.stream("GET", url) as answer:
                    answer.raise_for_status()
                    with open(part, "wb") as out:
                        for chunk in answer.iter_bytes():
                            out.write(chunk)
            os.replace(part, target)
            return
        except Exception as exc:  # обрыв, таймаут, 5xx — пробуем ещё раз
            last = exc
            try:
                os.remove(part)
            except OSError:
                pass
    raise RuntimeError(f"не скачалось {url}: {last}")


def _install_bundled_model(model_name: str, model_dir: Path) -> bool:
    """Взять официальный голос из полного ZIP, если он в нём есть."""
    if model_name != BUNDLED_MODEL:
        return False
    bundled = config.MODELS_DIR / "silero" / f"{model_name}.pt"
    if not bundled.is_file():
        return False
    digest = hashlib.sha256()
    with bundled.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != BUNDLED_MODEL_SHA256:
        raise RuntimeError("Файл голоса Silero в архиве повреждён. Распакуй архив заново.")
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / f"{model_name}.pt"
    part = target.with_suffix(".pt.part")
    try:
        shutil.copyfile(bundled, part)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return True


def _models_list():
    """Путь к списку моделей silero — там же, где его ищет сам silero."""
    import os

    import silero.silero as package

    inside = os.path.join(os.path.dirname(package.__file__), "..", "..", "models.yml")
    if os.path.exists(inside):
        return inside
    local = "latest_silero_models.yml"
    if not os.path.exists(local):
        _download(MODELS_LIST_URL, local)
    return local


def ensure_model(model_name: str, language: str = "ru") -> None:
    """Скачать модель заранее, если её ещё нет. Уже есть — ничего не делает.

    Не вышло разобрать список или найти модель в нём — молча отдаём работу
    самому silero: пусть скачает, как умеет (лучше медленно, чем никак).
    """
    import os

    # У полного установочного ZIP модель уже внутри. Пользователь не должен
    # зависеть от доступности отдельного сервера Silero на первом запуске.
    if language == "ru" and model_name == BUNDLED_MODEL:
        import silero.silero as package

        bundled_dir = Path(package.__file__).parent / "model"
        if (bundled_dir / f"{model_name}.pt").is_file():
            return
        if _install_bundled_model(model_name, bundled_dir):
            return

    try:
        from omegaconf import OmegaConf

        import silero.silero as package

        models = OmegaConf.load(_models_list())
        url = models.tts_models[language][model_name].latest.package
    except Exception:
        return
    model_dir = os.path.join(os.path.dirname(package.__file__), "model")
    os.makedirs(model_dir, exist_ok=True)
    path = os.path.join(model_dir, os.path.basename(url))
    if not os.path.isfile(path):
        _download(url, path)


class SileroVoice:
    """Голос ассистента. Держит модель в памяти между репликами."""

    def __init__(self, speaker: str | None = None, model: str | None = None):
        self.speaker = speaker or config.SILERO_SPEAKER
        self.model_name = model or config.SILERO_MODEL
        self._model = None

    @property
    def speakers(self) -> list[str]:
        if self._model is None:
            return []
        return [v for v in getattr(self._model, "speakers", []) if v != "random"]

    def load(self) -> None:
        """Грузит модель. Первый раз качает около 140 МБ."""
        if self._model is not None:
            return

        import torch
        from silero import silero_tts

        # Модель качаем со сроком и повторами: torch.hub без срока ожидания
        # может зависнуть при обрыве загрузки.
        ensure_model(self.model_name)
        model, _ = silero_tts(language="ru", speaker=self.model_name)
        # Специально на процессоре: видеокарта нужна играм, а выигрыша
        # от неё здесь почти нет — синтез и так в тридцать раз быстрее речи.
        model.to(torch.device("cpu"))
        self._model = model

    def say(
        self,
        text: str,
        ref=None,
        nfe_step: int | None = None,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """Синтезирует реплику. Возвращает (сигнал, частота дискретизации).

        Аргументы ref и nfe_step приняты ради общего вида с ESpeech —
        Silero они не нужны.
        """
        self.load()

        # Цифры и латиницу Silero не читает вовсе, а на куске без кириллицы
        # падает с пустым ValueError — и реплика пропадает молча. Поэтому
        # текст сначала переводится в то, как его произносят.
        pieces = [chunk for chunk in _split(for_speech(text)) if speakable(chunk)]
        if not pieces:
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE

        parts = []
        failed = []
        for piece in pieces:
            try:
                audio = self._model.apply_tts(
                    text=piece,
                    speaker=self.speaker,
                    sample_rate=SAMPLE_RATE,
                    put_accent=True,
                    put_yo=True,
                )
            except Exception as exc:
                # Один неудачный кусок не должен уносить всю реплику.
                failed.append(f"{type(exc).__name__}: {exc}")
                continue
            parts.append(audio.numpy().astype(np.float32))

        if not parts:
            raise RuntimeError(
                "Silero не смог произнести ничего из реплики: "
                + "; ".join(failed or ["пусто"])
            )

        wave = parts[0] if len(parts) == 1 else np.concatenate(parts)

        if speed and abs(speed - 1.0) > 0.01:
            wave = _restretch(wave, speed)

        return wave, SAMPLE_RATE

    def accent(self, text: str) -> str:
        """Ударения Silero ставит внутри себя — показываем хотя бы чтение."""
        return for_speech(text)

    def vram_used_mb(self) -> float:
        """Модель живёт на процессоре, видеопамять не занимает."""
        return 0.0


def _split(text: str) -> list[str]:
    """Режет длинный текст по границам предложений.

    Silero не синтезирует бесконечно длинные строки, а реплики иногда
    приходят целым абзацем.
    """
    text = (text or "").strip()
    if len(text) <= MAX_CHARS:
        return [text]

    chunks: list[str] = []
    current = ""
    for sentence in text.replace("!", "!|").replace("?", "?|").replace(".", ".|").split("|"):
        if len(current) + len(sentence) <= MAX_CHARS:
            current += sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _restretch(wave: np.ndarray, speed: float) -> np.ndarray:
    """Меняет темп речи простым пересчётом длины.

    Вместе с темпом немного едет тон — для небольших отклонений это
    незаметно, а городить полноценный алгоритм ради пары процентов не стоит.
    """
    length = int(len(wave) / speed)
    if length < 2:
        return wave
    return np.interp(
        np.linspace(0, len(wave) - 1, length), np.arange(len(wave)), wave
    ).astype(np.float32)
