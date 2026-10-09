r"""Чтение и запись настроек: ключи в .env, параметры модели в settings.json.

Ключи держим в .env отдельно от остальных настроек, чтобы случайно не
утащить их в репозиторий вместе с конфигом.
"""

from __future__ import annotations

import json
import os

import config
from core import proactive, safe_files

ENV_PATH = config.ROOT / ".env"
SETTINGS_PATH = config.ROOT / "settings.json"
PERSONA_PATH = config.ROOT / "prompts" / "persona.md"

# --- Нижние кнопки телефона ------------------------------------------------
#
# Кнопок всегда четыре: телефон стоит горизонтально, и полоса внизу рассчитана
# на четыре. Виды ячеек:
#   builtin — снимок экрана, момент, поиск и заметка; телефон сам ничего не
#     выполняет, а просит сервер;
#   hotkey — своё сочетание клавиш (смена сцены в OBS и подобное);
#   app — открыть программу из apps.json;
#   menu — пункт подменю программы (Discord → «Микрофон»); выполняется тем же
#     `launcher.run_menu`, поэтому подсветка переключателя работает как у
#     пункта подменю;
#   none — кнопки нет: остальные сдвигаются вправо.
DEFAULT_PHONE_ACTIONS = (
    {"kind": "builtin", "id": "screenshot"},
    {"kind": "builtin", "id": "moment"},
    {"kind": "builtin", "id": "search"},
    {"kind": "builtin", "id": "note_start"},
)
PHONE_ACTION_KINDS = ("builtin", "hotkey", "app", "menu", "none")
BUILTIN_ACTION_IDS = ("screenshot", "moment", "search", "note_start")
# Подпись под значком нижней кнопки: короткая, телефон ею и называется вслух.
BUILTIN_ACTION_TITLES = {
    "screenshot": "экран",
    "moment": "момент",
    "search": "найти",
    "note_start": "заметка",
}
BUILTIN_ACTION_ICONS = {
    "screenshot": "camera",
    "moment": "replay",
    "search": "search",
    "note_start": "note",
}
# Значки нижних кнопок — тот же набор, что у пунктов подменю
# (`ПРОГ_ЗНАЧКИ_ПУНКТ` в пульте, `ICONS` на телефоне). Незнакомое имя молча
# становится клавиатурой: кнопка остаётся, а значок не пропадает.
PHONE_ACTION_ICONS = (
    "mic", "headphones", "keyboard", "link", "camera", "replay", "browser", "play",
)
PHONE_ACTION_FALLBACK_ICON = "keyboard"
# Название под сочетанием на клавиатуре: длиннее на телефоне не прочитать.
PHONE_ACTION_TITLE_MAX = 24

# --- Мастер первого запуска -------------------------------------------------
#
# Шесть шагов, номера 1…6. Номер лежит в settings.json этой установки, чтобы
# после закрытия пульта мастер открылся там же, где человек остановился.
# По умолчанию — первый шаг: свежая установка начинается с начала, а если в
# файле значение чужое (правка руками, версия из будущего), открываемся с
# первого шага, а не падаем.
WIZARD_STEP_MIN = 1
WIZARD_STEP_MAX = 6
WIZARD_STEP_DEFAULT = WIZARD_STEP_MIN


def validate_wizard_step(value) -> int:
    """Номер шага мастера: целое 1…6. Всё прочее — первый шаг."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return WIZARD_STEP_DEFAULT
    if isinstance(value, float) and value != int(value):
        return WIZARD_STEP_DEFAULT
    шаг = int(value)
    if WIZARD_STEP_MIN <= шаг <= WIZARD_STEP_MAX:
        return шаг
    return WIZARD_STEP_DEFAULT


DEFAULTS = {
    "temperature": config.TEMPERATURE,
    "max_tokens": config.MAX_TOKENS,
    "history_turns": config.HISTORY_TURNS,
    "web_search": config.WEB_SEARCH,
    "search_sound": config.SEARCH_SOUND,
    "web_search_mode": config.WEB_SEARCH_MODE,
    "web_search_budget": config.WEB_SEARCH_BUDGET,
    # Страховка от заминок облака. Выключена по умолчанию: при заминке
    # платим за оба запроса.
    "hedge": config.HEDGE,
    "reasoning": config.REASONING,
    "replay_guard": config.REPLAY_GUARD,
    "voice_autostart": config.VOICE_AUTOSTART,
    "update_check": config.UPDATE_CHECK,
    "higgs_gentle": config.HIGGS_GENTLE,
    "models": {name: spec["model"] for name, spec in config.PROVIDERS.items()},
    # Адрес локального сервера ответов. По умолчанию — Ollama; можно
    # вписать LM Studio или llama.cpp (кнопки-подсказки в пульте).
    "local_url": config.PROVIDERS[config.LOCAL_PROVIDER]["base_url"],
    "tts_engine": config.TTS_ENGINE,
    # Готовый характер — от него быстрые фразы (core/personas.py).
    "persona_preset": config.PERSONA_PRESET,
    "silero_speaker": config.SILERO_SPEAKER,
    "silero_model": config.SILERO_MODEL,
    # Какая модель разбирает речь и насколько сжатая («Голос → Распознавание»).
    # Список моделей с названиями — core/stt_models.py.
    "stt_model": config.STT_MODEL,
    "stt_quantization": config.STT_QUANTIZATION,
    "voice_name": config.VOICE_NAME,
    "tts_speed": config.TTS_SPEED,
    "tts_nfe": config.TTS_NFE,
    "tts_gap": config.TTS_GAP,
    "voice_volume": config.VOICE_VOLUME,
    "duck_level": config.DUCK_LEVEL,
    # Порог начала речи поверх ответа настраивается по замеру во вкладке
    # «Проверка».
    "barge_in_level": config.BARGE_IN_LEVEL,
    # Замолкать при начале речи поверх ответа, не дожидаясь распознавания.
    "barge_instant": config.BARGE_INSTANT,
    "require_name_when_noisy": config.REQUIRE_NAME_WHEN_NOISY,
    "voice_app_guard": config.VOICE_APP_GUARD,
    "speaker_aec": config.SPEAKER_AEC,
    "owner_only": config.OWNER_ONLY,
    "owner_threshold": config.OWNER_THRESHOLD,
    "output": config.OUTPUT,
    # Откуда говорим в колонки: подстрока имени устройства вывода.
    # Пусто — системное по умолчанию.
    "speaker_name": config.SPEAKER_NAME,
    # Откуда нас слушают: подстрока имени микрофона (пусто — системный) и
    # какой вход брать у многоканальной карты.
    "mic_name": config.MIC_NAME,
    "mic_channel": config.MIC_CHANNEL,
    "listen_mode": config.LISTEN_MODE,
    # `listen_mode_on` (режим, которым слух включается обратно из «не
    # слушает») здесь нет намеренно: пока его не выбирали, это текущий
    # режим, см. `listen_mode_on()`.
    # Сколько она слушает без имени после последней реплики.
    "follow_up_window": config.FOLLOW_UP_WINDOW,
    # Заговаривать первой: никогда / редко / иногда / часто (core/proactive.py).
    "proactive": config.PROACTIVE,
    # Какая частота была последней включённой. Панель включает заходы
    # обратно к ней (config.PROACTIVE_LAST), поэтому «никогда» тут быть
    # не может: включать нечего.
    "proactive_last": config.PROACTIVE_LAST,
    # Иногда при таком заходе глянуть на экран.
    "proactive_look": config.PROACTIVE_LOOK,
    # Папка заметок. Пусто — «Документы\Заметки Трубы» (core/notes.py).
    "notes_dir": config.NOTES_DIR,
    # Спрашивать ли после разбора, записать его в заметки. По умолчанию да:
    # вопрос задаёт код (core/voice_loop.py::_ask_analysis_note), модель о
    # нём не знает, и без настройки она просто забывала бы о разборах.
    "offer_analysis_note": config.OFFER_ANALYSIS_NOTE,
    # Свои фразы при включении голоса (по одной на строку в пульте). Пусто —
    # фразы выбранного характера (`core/personas.py`, «ready»).
    "ready_phrases": list(config.READY_PHRASES),
    # Сетка программ на телефоне. Читает их web/index.html по сообщению
    # `apps`, поэтому телефон ничего не знает и не проверяет сам.
    "phone_cols": config.PHONE_COLS,
    "phone_rows": config.PHONE_ROWS,
    "phone_icon_style": config.PHONE_ICON_STYLE,
    "phone_labels": config.PHONE_LABELS,
    # Нижние кнопки телефона: четыре ячейки с действиями из пульта.
    # Ошибочная ячейка заменяется значением по умолчанию на своём месте.
    "phone_actions": list(DEFAULT_PHONE_ACTIONS),
    # Город для погоды. Пусто — погоды нет: телефон её не показывает и в сеть
    # никто не ходит. Выбирается в пульте поиском по геокодеру Open-Meteo.
    "weather_city": config.WEATHER_CITY,
    "weather_lat": config.WEATHER_LAT,
    "weather_lon": config.WEATHER_LON,
    # Мастер первого запуска показывается, пока это не сделано. У кого папка
    # Трубы уже была, мастер не нужен — см. `load_settings`.
    "first_run_done": False,
    # На каком шаге мастер первого запуска бросил человека в прошлый раз.
    # Шаг живёт в данных этой установки: закрыл пульт на пятом шаге (после QR) —
    # в следующий раз мастер открылся на нём же, а не на первом. Проверка
    # мягкая (см. `validate_wizard_step`): чужое значение в settings.json — это
    # первый шаг, а не отказ пульта запускаться.
    "wizard_step": WIZARD_STEP_DEFAULT,
    # Тема оформления пульта и телефона. Меняет только пульт («Настройки →
    # Система»): на телефоне лишних кнопок быть не должно, поэтому страница
    # телефона берёт тему сообщением от сервера и помнит её в localStorage.
    "theme": config.THEME,
    # Выбранные для скачивания модели качественных голосов. Список из
    # `higgs`/`espeech`; пустой — не выбрал ничего, и пульт не качает сам
    # ничего. Помнит именно выбор: кнопка «Установить качественные голоса»
    # без установки библиотек докачивает модели сама, а после перезапуска
    # пульта недокачанное дополняется.
    "voices_wanted": [],
}

# --- Качественные голоса: выбранные модели ---------------------------------
#
# Здесь только имена и проверка. Что именно модель весит и как её качать —
# в `core/voice_models.py`; список имён держим одинаковым с ним (сторожит
# тест). Проверка мягкая, как у соседних ключей: чужое имя молча исчезает,
# а не ломает запуск пульта.

VOICES_WANTED_MODELS = ("espeech", "higgs")


def validate_voices_wanted(value) -> list:
    """Список выбранных моделей. Некорректное значение даёт пустой список."""
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for имя in value:
        if not isinstance(имя, str):
            continue
        имя = имя.strip()
        if имя in VOICES_WANTED_MODELS and имя not in out:
            out.append(имя)
    return out


def voices_wanted() -> list:
    """Выбранные модели в фиксированном порядке."""
    выбрано = set(validate_voices_wanted(load_settings().get("voices_wanted")))
    return [имя for имя in VOICES_WANTED_MODELS if имя in выбрано]


def save_voices_wanted(модели) -> list:
    """Сохранить выбор и вернуть проверенный список моделей."""
    чистое = validate_voices_wanted(модели)
    save_settings({"voices_wanted": чистое})
    return чистое


# --- Тема оформления -------------------------------------------------------
#
# Всего две темы. Проверка мягкая, как у сетки телефона: чужое значение в
# settings.json (правка руками, версия из будущего) — это тёмная тема, а не
# отказ пульта запускаться.
THEMES = ("dark", "light")


def validate_theme(value) -> str:
    """Тема оформления: `dark` или `light`. Всё прочее — тёмная."""
    if isinstance(value, str) and value.strip() in THEMES:
        return value.strip()
    return config.THEME

# --- Сетка программ на телефоне -------------------------------------------
#
# Колонок всегда четыре: под это число рассчитаны телефон и макет пульта.
# Ключ `phone_cols` в `settings.json` остаётся (старые файлы и
# `config.PHONE_COLS`), но значение в нём уже ничего не решает.
PHONE_COLS = 4
# Рядов сетки один или два: третий ряд не помещается на телефоне.
# Сохранённое «3» и любая опечатка молча
# становятся двумя рядами: телефон не должен рисовать сетку, которой в
# пульте не выбрать, а пульт не должен ругаться пустым экраном.
PHONE_ROWS = 2
PHONE_ROWS_MIN = 1
PHONE_ROWS_MAX = 2
PHONE_ICON_STYLES = ("plate", "round", "bare")


def validate_phone_cols(value) -> int:
    """Колонок всегда четыре. Что бы ни прислали — телефон рисует 4.

    Старое «auto» и числа 3…6 остались в живых `settings.json`; если бы мы
    им верили, добавление девятой программы снова сделало бы пять колонок, и
    макет в пульте не совпал бы с телефоном.
    """
    return PHONE_COLS


def validate_phone_rows(value) -> int:
    """Рядов сетки: один или два.

    Сохранённое «3» и любая опечатка молча
    становятся двумя рядами: телефон не должен рисовать сетку, которой в
    пульте не выбрать, а пустым экраном пульт ругаться не должен.

    Рядов у сетки не бывает полтора: `1.5` — это опечатка, а не выбор, и
    `int()` от неё молча дал бы один ряд. Дробь потому мусор, как и всё
    остальное; целое `1.0` — обычная запись единицы, её принимаем.
    """
    if isinstance(value, bool):
        return PHONE_ROWS
    if isinstance(value, str):
        value = value.strip()
    try:
        число = int(value)
    except (TypeError, ValueError):
        return PHONE_ROWS
    if isinstance(value, float) and value != число:
        return PHONE_ROWS
    if PHONE_ROWS_MIN <= число <= PHONE_ROWS_MAX:
        return число
    return PHONE_ROWS


def validate_phone_icon_style(value) -> str:
    """Вид плитки: plate / round / bare."""
    if isinstance(value, str) and value.strip() in PHONE_ICON_STYLES:
        return value.strip()
    return config.PHONE_ICON_STYLE


def validate_phone_labels(value) -> bool:
    """Подпись под значком — только настоящий булев ключ."""
    if isinstance(value, bool):
        return value
    return bool(config.PHONE_LABELS)


READY_PHRASES_MAX = 20
READY_PHRASE_CHARS = 80


def validate_ready_phrases(value) -> list[str]:
    """Свои фразы при включении голоса: строки, без пустых, не больше 20.

    Короткие: это одна реплика «я на связи», а не речь — длиннее 80 знаков
    обрезаем по слову. Мусор (не список, не строки) — пустой список, то есть
    фразы выбранного характера, а не отказ сохранять остальное.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        фраза = " ".join(item.split())
        if not фраза:
            continue
        if len(фраза) > READY_PHRASE_CHARS:
            фраза = фраза[:READY_PHRASE_CHARS].rsplit(" ", 1)[0] or фраза[:READY_PHRASE_CHARS]
        if фраза not in out:
            out.append(фраза)
        if len(out) >= READY_PHRASES_MAX:
            break
    return out


def validate_offer_analysis_note(value) -> bool:
    """Спрашивать ли про разбор в заметки — только настоящий булев ключ.

    Мусор из settings.json (правка руками, значение из будущей версии) — это
    «спрашивать»: выключить этот вопрос можно и в пульте, а вот молча
    отключать вопрос из-за опечатки нельзя.
    """
    if isinstance(value, bool):
        return value
    return bool(config.OFFER_ANALYSIS_NOTE)


def default_phone_action(slot: int) -> dict:
    """Ячейка по умолчанию для места `slot` — нынешние четыре кнопки."""
    try:
        номер = int(slot)
    except (TypeError, ValueError):
        номер = 0
    if not 0 <= номер < len(DEFAULT_PHONE_ACTIONS):
        номер = 0
    return dict(DEFAULT_PHONE_ACTIONS[номер])


def validate_phone_actions(value) -> list:
    """Четыре нижние кнопки телефона — по одной ячейке на место.

    Проверка мягкая, как у сетки: кривая ячейка становится ячейкой по
    умолчанию **на своём месте**, а не отказом. Иначе одна опечатка в пульте
    убрала бы все четыре кнопки, и низ телефона остался бы пустым.
    """
    if not isinstance(value, list):
        return [dict(one) for one in DEFAULT_PHONE_ACTIONS]
    out = []
    for slot in range(len(DEFAULT_PHONE_ACTIONS)):
        cell = value[slot] if slot < len(value) else None
        out.append(_validate_action_cell(cell) or default_phone_action(slot))
    return out


def _validate_action_cell(cell) -> dict | None:
    """Одна ячейка нижней кнопки. None — она кривая, звать будет нечего."""
    if not isinstance(cell, dict):
        return None
    kind = cell.get("kind")
    if kind not in PHONE_ACTION_KINDS:
        return None
    if kind == "none":
        return {"kind": "none"}

    if kind == "builtin":
        one = cell.get("id")
        if one not in BUILTIN_ACTION_IDS:
            return None
        return {"kind": "builtin", "id": one}

    if kind == "hotkey":
        title = str(cell.get("title") or "").strip()
        keys = str(cell.get("keys") or "").strip()
        if not title or not keys:
            return None
        title = title[:PHONE_ACTION_TITLE_MAX]
        # Разбор тот же, что у пункта подменю `hotkey`: неразобранное
        # сочетание нажать нечем, поэтому такая ячейка — как ячейка по умолчанию.
        from core import hotkeys

        try:
            hotkeys.parse(keys)
        except ValueError:
            return None
        icon = str(cell.get("icon") or "").strip()
        if icon not in PHONE_ACTION_ICONS:
            icon = PHONE_ACTION_FALLBACK_ICON
        return {"kind": "hotkey", "title": title, "icon": icon, "keys": keys}

    # `app` и `menu` — обе ссылаются на запись apps.json. Саму программу здесь
    # не ищем: apps.json можно переписать руками в любой момент, и
    # потерять кнопку из-за этого нельзя. Нажатие разберётся само
    # (`web_runtime._action` скажет «такой программы больше нет»).
    app = str(cell.get("app") or "").strip()[:80]
    if not app:
        return None
    if kind == "app":
        return {"kind": "app", "app": app}
    item = str(cell.get("item") or "").strip()[:80]
    if not item:
        return None
    return {"kind": "menu", "app": app, "item": item}


# --- Звук: микрофон и колонки ----------------------------------------------
#
# Проверки мягкие, как у сетки телефона: значение приходит из пульта,
# и лишний пробел или опечатка не должны оставить его без голоса.

# Имя устройства ищем по подстроке, поэтому длиннее 100 знаков смысла нет.
NAME_LIMIT = 100


def validate_device_name(value) -> str:
    """Подстрока имени устройства. Пусто — системное по умолчанию."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:NAME_LIMIT]


def validate_mic_channel(value) -> int:
    """Вход микрофона: 0 или 1. Всё прочее — первый вход."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return config.MIC_CHANNEL
    return number if number in (0, 1) else config.MIC_CHANNEL


LISTEN_ON_MODES = ("always", "name")


def listen_mode_on(values: dict) -> str:
    """Режим, которым слух включается из «не слушает».

    Сохранённый `listen_mode_on`; без него — текущий режим, если слух
    включён; иначе «по имени».
    """
    сохранён = values.get("listen_mode_on")
    if сохранён in LISTEN_ON_MODES:
        return сохранён
    сейчас = values.get("listen_mode")
    return сейчас if сейчас in LISTEN_ON_MODES else "name"


def validate_weather_city(value) -> str:
    """Название города для подписи. Пусто — погоды нет."""
    return validate_device_name(value) if isinstance(value, str) else ""


def validate_coordinate(value):
    """Координата — число в разумных пределах. Мусор — None, и погода молчит.

    Оставлено как обёртка широты: долгота у неё своя (см. `validate_lon`).
    """
    return validate_lat(value)


def validate_lat(value):
    """Широта: от −90 до 90. Мусор — None, и погода молчит."""
    return _координата(value, 90.0)


def validate_lon(value):
    """Долгота: от −180 до 180. Мусор — None, и погода молчит.

    Отдельная проверка не для красоты: долгота Владивостока 131.9 — это
    131.9, а не «минус 90, значит мусор». Общая проверка на ±90 превращала
    её в None, и погода молча выключалась, хотя город в пульте выбран.
    """
    return _координата(value, 180.0)


def _координата(value, предел: float):
    try:
        число = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or value is None or value == "":
        return None
    if число != число or not (-предел <= число <= предел):
        return None
    return число


def weather_ready() -> bool:
    """Настроен ли город. Пока нет — в сеть никто не ходит."""
    город = str(getattr(config, "WEATHER_CITY", "") or "").strip()
    lat = validate_lat(getattr(config, "WEATHER_LAT", None))
    lon = validate_lon(getattr(config, "WEATHER_LON", None))
    return bool(город and lat is not None and lon is not None)


def grid_layout() -> dict:
    """Вид сетки для сообщения телефону: всё, что он знает о раскладке."""
    return {
        "cols": validate_phone_cols(config.PHONE_COLS),
        "rows": validate_phone_rows(config.PHONE_ROWS),
        "style": validate_phone_icon_style(config.PHONE_ICON_STYLE),
        "labels": validate_phone_labels(config.PHONE_LABELS),
    }


def phone_actions() -> list:
    """Нижние кнопки телефона из сохранённых настроек — всегда четыре ячейки.

    Отдельной функции-получателя нет ради простоты: настройка приходит из
    settings.json, поэтому здесь её читаем и сразу проверяем. `config` для
    этого ключа не нужен — телефон всё равно получает готовый список
    сообщением.
    """
    return validate_phone_actions(load_settings().get("phone_actions"))


# --- Модель распознавания речи --------------------------------------------
#
# Проверки мягкие, как у сетки телефона: значение приходит из пульта,
# и опечатка в названии модели не должна оставлять голос без слуха.


def validate_stt_model(value) -> str:
    """Имя модели из списка. Неизвестное — модель по умолчанию."""
    from core import stt_models

    return stt_models.valid_model(value)


def validate_stt_quantization(value) -> str:
    """Сжатие модели: "int8" или "none". Всё прочее — как было."""
    from core import stt_models

    return stt_models.valid_quantization(value)


def stt_quantization_for_config(value):
    """Что положить в `config.STT_QUANTIZATION`: "none" — это None."""
    from core import stt_models

    return stt_models.quantization_for_config(validate_stt_quantization(value))


def apply_to_config() -> dict:
    """Переносит сохранённые настройки в config, откуда их читает весь код."""
    values = load_settings()
    for name, spec in config.PROVIDERS.items():
        selected = values.get("models", {}).get(name)
        if isinstance(selected, str) and selected.strip():
            spec["model"] = selected.strip()
    # Адрес локального сервера. Проверяем ещё раз: settings.json
    # может править руками, а пустой или схемы-без адрес оставил бы мозг
    # без внятной ошибки. Пустое — не поломка: значит, локальный сервер
    # просто не настроен.
    try:
        local_url = validate_local_url(values.get("local_url"))
    except ValueError:
        local_url = ""  # мусор руками — молча оставляем прошлый адрес
    if local_url:
        config.PROVIDERS[config.LOCAL_PROVIDER]["base_url"] = local_url
    config.TEMPERATURE = values["temperature"]
    config.MAX_TOKENS = values["max_tokens"]
    config.HISTORY_TURNS = values["history_turns"]
    config.WEB_SEARCH = bool(values["web_search"])
    config.SEARCH_SOUND = bool(values["search_sound"])
    config.WEB_SEARCH_MODE = values["web_search_mode"]
    config.WEB_SEARCH_BUDGET = float(values["web_search_budget"])
    config.HEDGE = bool(values["hedge"])
    config.REASONING = bool(values.get("reasoning", False))
    config.REPLAY_GUARD = bool(values["replay_guard"])
    config.VOICE_AUTOSTART = bool(values["voice_autostart"])
    config.UPDATE_CHECK = bool(values["update_check"])
    config.HIGGS_GENTLE = bool(values["higgs_gentle"])
    config.TTS_ENGINE = values["tts_engine"]
    config.SILERO_SPEAKER = values["silero_speaker"]
    config.SILERO_MODEL = values["silero_model"]
    # Модель распознавания и её сжатие. "none" — это None в config: полная
    # точность. Саму модель поднимает голосовой цикл (core/voice_loop.py),
    # и при смене он грузит её заново — settings.json сам по себе не молчит.
    config.STT_MODEL = validate_stt_model(values.get("stt_model"))
    config.STT_QUANTIZATION = stt_quantization_for_config(
        values.get("stt_quantization"))
    config.VOICE_NAME = values["voice_name"]
    config.TTS_SPEED = values["tts_speed"]
    config.TTS_NFE = int(values["tts_nfe"])
    config.TTS_GAP = values["tts_gap"]
    config.VOICE_VOLUME = int(values["voice_volume"])
    config.DUCK_LEVEL = values["duck_level"]
    config.BARGE_IN_LEVEL = values["barge_in_level"]
    config.BARGE_INSTANT = bool(values["barge_instant"])
    config.REQUIRE_NAME_WHEN_NOISY = values["require_name_when_noisy"]
    config.VOICE_APP_GUARD = bool(values["voice_app_guard"])
    config.SPEAKER_AEC = bool(values["speaker_aec"])
    config.OWNER_ONLY = values["owner_only"]
    config.OWNER_THRESHOLD = values["owner_threshold"]
    config.OUTPUT = values["output"]
    # Звуковые устройства. Имя ищется по подстроке, а пустое имя — это
    # системное устройство по умолчанию (см. `config.find_input_device`).
    config.SPEAKER_NAME = validate_device_name(values.get("speaker_name"))
    config.MIC_NAME = validate_device_name(values.get("mic_name"))
    config.MIC_CHANNEL = validate_mic_channel(values.get("mic_channel"))
    config.LISTEN_MODE = values["listen_mode"]
    config.LISTEN_MODE_ON = listen_mode_on(values)
    config.FOLLOW_UP_WINDOW = float(values["follow_up_window"])
    # Частота захода первой: значение проверяется при сохранении, а здесь
    # подстраховка на случай правки settings.json руками.
    frequency = values["proactive"]
    if not proactive.is_frequency(frequency):
        frequency = config.PROACTIVE
    config.PROACTIVE = frequency
    # Последняя включённая частота. Панель включает заходы обратно к ней,
    # поэтому «никогда» или мусор руками здесь не годятся — оставляем
    # промежуточный вариант, иначе включить было бы нечем.
    last = values["proactive_last"]
    if last == "never" or not proactive.is_frequency(last):
        last = "sometimes"
    config.PROACTIVE_LAST = last
    config.PROACTIVE_LOOK = bool(values["proactive_look"])
    # Папка заметок. Читается на каждый вызов из config, поэтому сменяется
    # сразу после сохранения, без перезапуска голоса.
    config.NOTES_DIR = str(values["notes_dir"] or "").strip()
    # Вопрос про разбор в заметки. Читается на каждый разбор, поэтому смена
    # видна сразу, без перезапуска голоса.
    config.OFFER_ANALYSIS_NOTE = validate_offer_analysis_note(
        values.get("offer_analysis_note"))
    # Свои фразы при включении голоса; пусто — фразы выбранного характера.
    config.READY_PHRASES = validate_ready_phrases(values.get("ready_phrases"))
    # Город для погоды. Пока он не выбран, `core/weather.py` молчит и в сеть
    # не ходит — иначе Труба спрашивала бы погоду в чужом городе.
    config.WEATHER_CITY = validate_weather_city(values.get("weather_city"))
    config.WEATHER_LAT = validate_lat(values.get("weather_lat"))
    config.WEATHER_LON = validate_lon(values.get("weather_lon"))
    # Сетка программ телефона. Телефон берёт её из сообщения, а config —
    # отсюда: значит, после сохранения настроек новый вид уходит на экран
    # без перезагрузки страницы.
    config.PHONE_COLS = validate_phone_cols(values["phone_cols"])
    config.PHONE_ROWS = validate_phone_rows(values["phone_rows"])
    config.PHONE_ICON_STYLE = validate_phone_icon_style(values["phone_icon_style"])
    config.PHONE_LABELS = validate_phone_labels(values["phone_labels"])
    from core import personas

    config.PERSONA_PRESET = personas.valid(values.get("persona_preset"))
    # Тема оформления. В config она нужна окну пульта: фон окна до загрузки
    # страницы берётся отсюда (см. `config.фон_окна`).
    config.THEME = validate_theme(values.get("theme"))
    return values


# --- Параметры модели ----------------------------------------------------


def load_settings() -> dict:
    data = dict(DEFAULTS)
    # Файл уже был до появления мастера первого запуска — значит, человек
    # не новичок, и мастер ему показывать нечего. Отдельной строкой, а не
    # значением по умолчанию: файл есть — «уже делал», файла нет — «свежая
    # установка, покажи мастер».
    файл_был = SETTINGS_PATH.exists()
    сохранённые = None
    if файл_был:
        try:
            сохранённые = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Битый файл — в сторону, а не под затирание первой же записью:
            # из него ещё можно вытащить настройки руками.
            safe_files.quarantine(SETTINGS_PATH)
            сохранённые = None
        except OSError:
            сохранённые = None
        if isinstance(сохранённые, dict):
            data.update(сохранённые)
            # Прежний потолок по умолчанию поднимается при обновлении один
            # раз; выбранный руками после этого остаётся как есть.
            if (сохранённые.get("max_tokens") == config.OLD_MAX_TOKENS
                    and not сохранённые.get("max_tokens_raised")):
                data["max_tokens"] = config.MAX_TOKENS
    data["max_tokens_raised"] = True
    # Ключа в файле нет — значит, файл писала версия без мастера, и человек
    # не новичок: показывать ему мастер незачем. Файла нет вовсе — свежая
    # установка, мастер нужен.
    if "first_run_done" not in (сохранённые or {}):
        data["first_run_done"] = bool(файл_был)
    # Шаг мастера нормализуем при чтении: правка руками не должна открывать
    # пульт с несуществующего шага.
    data["wizard_step"] = validate_wizard_step(data.get("wizard_step"))
    # Выбранные модели качественных голосов — так же: правка руками не должна
    # заставить пульт качать то, чего нет.
    data["voices_wanted"] = validate_voices_wanted(data.get("voices_wanted"))
    # Тот же ключ — вопрос про разбор в заметки: мусор руками не должен ни
    # сломать запуск пульта, ни молча выключить вопрос.
    data["offer_analysis_note"] = validate_offer_analysis_note(
        data.get("offer_analysis_note"))
    data["ready_phrases"] = validate_ready_phrases(data.get("ready_phrases"))

    saved_models = data.get("models")
    data["models"] = {
        name: (saved_models.get(name) if isinstance(saved_models, dict)
               and isinstance(saved_models.get(name), str)
               and saved_models.get(name).strip() else spec["model"])
        for name, spec in config.PROVIDERS.items()
    }
    return data


def save_settings(values: dict) -> None:
    data = load_settings()
    data.update(values)
    # Ряды сетки нормализуем и здесь: `phone_rows` может прийти из старого
    # пульта или из прежнего settings.json, а в файле должно лежать то, что
    # телефон умеет рисовать. Остальные настройки пишем как прислали.
    if "phone_rows" in data:
        data["phone_rows"] = validate_phone_rows(data["phone_rows"])
    safe_files.write_text(SETTINGS_PATH, json.dumps(data, ensure_ascii=False, indent=2))


# --- Ключи и провайдер ---------------------------------------------------


def _read_env() -> dict[str, str]:
    """Разбирает .env в словарь, сохраняя только строки вида КЛЮЧ=значение."""
    values = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env(values: dict[str, str]) -> None:
    lines = [
        "# Ключи провайдеров. Этот файл не попадает в репозиторий.",
        "",
        f"LLM_PROVIDER={values.get('LLM_PROVIDER', 'deepseek')}",
        "",
    ]
    for name, spec in config.PROVIDERS.items():
        env_name = spec["key_env"]
        lines.append(f"{env_name}={values.get(env_name, '')}")
    # Здесь ключи: оборванная запись оставила бы человека без ключа.
    safe_files.write_text(ENV_PATH, "\n".join(lines) + "\n")


def get_provider() -> str:
    return _read_env().get("LLM_PROVIDER", config.LLM_PROVIDER)


def set_provider(name: str) -> None:
    values = _read_env()
    values["LLM_PROVIDER"] = name
    _write_env(values)
    os.environ["LLM_PROVIDER"] = name


def get_api_key(provider: str) -> str:
    env_name = config.PROVIDERS[provider]["key_env"]
    return _read_env().get(env_name, "")


def is_local(provider: str) -> bool:
    """Локальный ли сервер. У него нет ни ключа, ни цены, ни облачных правил."""
    spec = config.PROVIDERS.get(provider) or {}
    return bool(spec.get("local"))


def key_for(provider: str) -> str:
    """Ключ, с которым реально ходим. Пустая строка — ходить нечем.

    Единственное место, где решается, обязателен ключ или нет: облаку он
    обязателен, локальному серверу не нужен вовсе, но OpenAI-клиент требует
    непустой `api_key` — поэтому подставляем заглушку (`config.LOCAL_KEY_STUB`).
    Всё остальное (мозг, пульт, проверка связи, список моделей) берёт ключ
    отсюда и не знает, откуда он взялся.
    """
    if provider not in config.PROVIDERS:
        return ""
    env_name = config.PROVIDERS[provider]["key_env"]
    # Сначала через `get_api_key` — тот же путь, что у пульта и у тестов
    # (они подменяют именно его); потом окружение процесса.
    key = get_api_key(provider) or os.environ.get(env_name, "")
    key = (key or "").strip()
    if key:
        return key
    return config.LOCAL_KEY_STUB if is_local(provider) else ""


def local_notice(provider: str, model: str = "", base_url: str = "") -> str:
    """Честная пометка о локальном мозге. Для облака — пустая строка.

    Журнал и Панель берут её отсюда, чтобы одинаково показывать, что
    совместимость локальной модели с Трубой не проверялась.
    """
    if not is_local(provider):
        return ""
    spec = config.PROVIDERS.get(provider) or {}
    имя = (model or spec.get("model") or "?").strip() or "?"
    адрес = (base_url or spec.get("base_url") or "?").strip() or "?"
    return f"локальная модель {имя} на {адрес} — с Трубой не проверялась"


def has_key(provider: str) -> bool:
    """Есть ли чем ходить. Для `local` — всегда: заглушка не требует ключа."""
    return bool(key_for(provider))


# --- Адрес локального сервера ---------------------------------------------

# Схемы, которые понимает OpenAI-клиент. Всё остальное — опечатка, а не адрес.
LOCAL_URL_SCHEMES = ("http://", "https://")


def validate_local_url(value) -> str:
    """Адрес локального сервера строкой без хвостовых слэшей.

    Пустая строка означает, что локальный сервер ещё не настроен.
    Остальное — ошибка у поля `local_url`.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("адрес строкой")
    text = value.strip()
    if not text:
        return ""
    if len(text) > 200:
        raise ValueError("адрес до 200 знаков")
    low = text.lower()
    if not low.startswith(LOCAL_URL_SCHEMES):
        raise ValueError("адрес должен начинаться с http:// или https://")
    if any(ch.isspace() for ch in text):
        raise ValueError("в адресе не должно быть пробелов")
    # Хост: то, что между схемой и первым слэшем после неё.
    остаток = text.split("://", 1)[1]
    хост = остаток.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not хост:
        raise ValueError("в адресе нет адреса сервера")
    return text.rstrip("/")


def local_url() -> str:
    """Адрес, по которому сейчас ходим (из config, а не из settings.json)."""
    return config.PROVIDERS[config.LOCAL_PROVIDER]["base_url"]


def set_api_key(provider: str, key: str) -> None:
    env_name = config.PROVIDERS[provider]["key_env"]
    values = _read_env()
    values[env_name] = key.strip()
    _write_env(values)
    # Обновляем и текущий процесс, чтобы не требовать перезапуска.
    os.environ[env_name] = key.strip()


# --- Характер ------------------------------------------------------------


def load_persona() -> str:
    """Текст характера. Своего ещё нет (свежая установка) — выбранный готовый:
    иначе в «Характере» стояло бы пустое поле, а мозг — тот же готовый текст."""
    if PERSONA_PATH.exists():
        return PERSONA_PATH.read_text(encoding="utf-8")
    from core import personas

    return personas.text(getattr(config, "PERSONA_PRESET", personas.DEFAULT))


def save_persona(text: str) -> None:
    safe_files.write_text(PERSONA_PATH, text)
