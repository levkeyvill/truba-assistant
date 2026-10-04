r"""Скачать модели заранее, до первого запуска пульта.

Запуск — из корня Трубы тем же python, что и пульт:
    .venv\Scripts\python.exe tools\prefetch_models.py

Что качает: модель распознавания речи, VAD, модель отпечатка голоса и голос
Silero. Ровно то, что пульт просит при первом включении голоса, — просто здесь
хозяин видит прогресс, а не ждёт молчаливую загрузку.

Каждая модель обёрнута в свой try: упавшая одна не отменяет остальные (сеть
может оборваться на третьей минуте, а VAD маленький и мог бы скачаться).
Код возврата 1, если хоть одна не скачалась, — установщик на это ругается.

Тяжёлое (onnx_asr, torch, silero, Pillow) импортируется внутри функций: файл
должен разбираться и импортироваться без сети и без библиотек — на этом стоят
тесты. config импортируется наверху, он ставит HF_HOME на models/hf, откуда
модели и берутся дальше.
"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402  — нужен до всего: ставит HF_HOME на models/hf

# Значок ярлыков — тот же файл, что у окна пульта (core/app_icon.py).
ICO_PATH = config.DATA_DIR / "truba.ico"


def _скачать_распознавание() -> None:
    """Модель GigaAM. Та же, что грузит core/voice_loop.py."""
    import onnx_asr

    onnx_asr.load_model(config.STT_MODEL, quantization=config.STT_QUANTIZATION)


def _скачать_vad() -> None:
    """VAD — он отрезает тишину. Маленький, но без него голос не заработает."""
    import onnx_asr

    onnx_asr.load_vad(config.VAD_MODEL)


def _скачать_отпечаток() -> None:
    """Модель распознавания хозяина. core/speaker.py, Voiceprint.load()."""
    from core.speaker import Voiceprint

    Voiceprint().load()


def _скачать_голос() -> None:
    """Голос Silero — основной, на процессоре. core/silero_voice.py."""
    from core.silero_voice import SileroVoice

    SileroVoice().load()


def _нарисовать_значок() -> None:
    """Рисуем значок ярлыков: янтарное кольцо на тёмном круге, как в шапке
    пульта (ui/web/pult.css, .значок). Уже нарисованный не трогаем — вдруг
    хозяин поменял картинку на свою."""
    if ICO_PATH.exists():
        return

    # Рисунок один на ярлыки и окно пульта — он живёт в core/app_icon.py.
    from core import app_icon

    app_icon.draw(ICO_PATH)


# Что скачиваем: подпись для хозяина и функция. Подпись печатается до
# загрузки, поэтому должна говорить, сколько ждать.
ЗАДАЧИ = (
    ("Скачиваю распознавание речи (~225 МБ)…", _скачать_распознавание),
    ("Скачиваю детектор речи (~2 МБ)…", _скачать_vad),
    ("Скачиваю модель отпечатка голоса (~90 МБ)…", _скачать_отпечаток),
    ("Скачиваю голос Silero (~140 МБ)…", _скачать_голос),
    ("Рисую значок ярлыков…", _нарисовать_значок),
)


def main() -> int:
    print(f"Куда качаю: {config.MODELS_DIR}", flush=True)
    print("Модель Higgs (~9 ГБ) не качаю — она нужна только для этого голоса.", flush=True)

    упало = []
    for подпись, работа in ЗАДАЧИ:
        print("", flush=True)
        print(подпись, flush=True)
        try:
            работа()
        except Exception:
            # Одна неудача не отменяет остальные: показываем, что именно, и
            # идём дальше. Пусть качается всё, что ещё может.
            traceback.print_exc()
            упало.append(подпись)
            print(f"НЕ ПОЛУЧИЛОСЬ: {подпись}", flush=True)
        else:
            print("...готово", flush=True)

    print("", flush=True)
    if упало:
        print("Не скачалось:", flush=True)
        for подпись in упало:
            print(f"  {подпись}", flush=True)
        print("Запусти установщик ещё раз — он скачает остальное.", flush=True)
        return 1

    print("Все модели на месте.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
