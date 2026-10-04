r"""Модели распознавания речи, из которых выбирает хозяин.

Список один на всё: и форма «Голос → Распознавание», и проверка значения в
core/settings.py. Сама модель грузится через `onnx_asr` (core/voice_loop.py) —
здесь только то, из-за чего хозяин выбирает: как она называется, что умеет и
сколько примерно занимает. Первый запуск качает модель с Hugging Face, поэтому
размер подписан рядом с названием.
"""

# По умолчанию — та, что стояла в config.py до появления настроек.
DEFAULT = "gigaam-v3-e2e-rnnt"

MODELS = (
    {
        "id": "gigaam-v3-e2e-rnnt",
        "title": "Точная, с пунктуацией",
        "note": "Русскую речь понимает лучше всех и расставляет знаки препинания. "
                "Разбирает фразу целиком, поэтому чуть медленнее.",
        "size": "около 230 МБ",
    },
    {
        "id": "gigaam-v3-e2e-ctc",
        "title": "Быстрая, с пунктуацией",
        "note": "Тот же русский, но распознаёт быстрее: берём, когда важна "
                "отзывчивость, а не последняя запятая.",
        "size": "около 180 МБ",
    },
    {
        "id": "gigaam-multilingual-ctc",
        "title": "Несколько языков (пробная)",
        "note": "Для речи вперемешку с английским; русский может быть хуже.",
        "size": "около 600 МБ",
    },
)

# Сжатие весов. "none" в config означает None — полная точность.
QUANTIZATIONS = (
    {"id": "int8", "title": "Сжатая — быстрее",
     "note": "Модель вчетверо меньше и заметно быстрее на процессоре"},
    {"id": "none", "title": "Полная — точнее, но медленнее",
     "note": "Веса целиком: точнее, но фраза распознаётся дольше"},
)

IDS = tuple(model["id"] for model in MODELS)
QUANT_IDS = tuple(item["id"] for item in QUANTIZATIONS)


def valid_model(value) -> str:
    """Имя модели из списка. Мусор — значение по умолчанию, а не ошибка."""
    if isinstance(value, str) and value.strip() in IDS:
        return value.strip()
    return DEFAULT


def valid_quantization(value) -> str:
    """Сжатие: "int8" или "none". Всё прочее — как было."""
    if isinstance(value, str) and value.strip() in QUANT_IDS:
        return value.strip()
    return "int8"


def quantization_for_config(value):
    """Что положить в `config.STT_QUANTIZATION`: "none" — это None."""
    return None if valid_quantization(value) == "none" else "int8"


def quantization_name(value) -> str:
    """Обратное к `quantization_for_config`: None в config — это "none".

    Пульт и `VoiceLoop.stt_state` говорят о сжатии одним словом из
    QUANT_IDS, а в config оно живёт как "int8" или None.
    """
    return "none" if value in (None, "none") else "int8"


def find_model(value) -> dict:
    """Описание модели по имени; для чужого — описание модели по умолчанию."""
    for model in MODELS:
        if model["id"] == valid_model(value):
            return dict(model)
    return dict(MODELS[0])


def models_payload() -> list:
    """Список для снимка настроек: пульт рисует им поле выбора."""
    return [dict(model) for model in MODELS]


def quantizations_payload() -> list:
    """То же для переключателя точности."""
    return [dict(item) for item in QUANTIZATIONS]
