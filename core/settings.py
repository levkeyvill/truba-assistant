r"""Чтение и запись настроек: ключи в .env, параметры модели в settings.json.

Ключи держим в .env отдельно от остальных настроек, чтобы случайно не
утащить их в репозиторий вместе с конфигом.
"""

from __future__ import annotations

import json
import os

import config
from core import proactive

ENV_PATH = config.ROOT / ".env"
SETTINGS_PATH = config.ROOT / "settings.json"
PERSONA_PATH = config.ROOT / "prompts" / "persona.md"

# --- Нижние кнопки телефона ------------------------------------------------
#
# Кнопок всегда четыре: телефон стоит горизонтально, и полоса внизу рассчитана
# на четыре. Виды ячеек:
#   builtin — то, что было с самого начала (снимок экрана, момент, поиск,
#     заметка); телефон ведёт себя с ними ровно как раньше, сам ничего не
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

DEFAULTS = {
    "temperature": config.TEMPERATURE,
    "max_tokens": config.MAX_TOKENS,
    "history_turns": config.HISTORY_TURNS,
    "web_search": config.WEB_SEARCH,
    "web_search_mode": config.WEB_SEARCH_MODE,
    "web_search_budget": config.WEB_SEARCH_BUDGET,
    # Страховка от заминок облака. Выключена по умолчанию: при заминке
    # платим за оба запроса.
    "hedge": config.HEDGE,
    "replay_guard": config.REPLAY_GUARD,
    "voice_autostart": config.VOICE_AUTOSTART,
    "update_check": config.UPDATE_CHECK,
    "higgs_gentle": config.HIGGS_GENTLE,
    "models": {name: spec["model"] for name, spec in config.PROVIDERS.items()},
    # Адрес локального сервера ответов. По умолчанию — Ollama; хозяин может
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
    # Насколько громко надо говорить, чтобы пробиться сквозь её речь.
    # Подбирается замером голоса во вкладке «Проверка»: заводские 0.12
    # оказались выше, чем говорит человек, и перебить её было нельзя.
    "barge_in_level": config.BARGE_IN_LEVEL,
    "require_name_when_noisy": config.REQUIRE_NAME_WHEN_NOISY,
    "voice_app_guard": config.VOICE_APP_GUARD,
    "owner_only": config.OWNER_ONLY,
    "owner_threshold": config.OWNER_THRESHOLD,
    "output": config.OUTPUT,
    # Откуда говорим в колонки: подстрока имени устройства вывода.
    # Пусто — системное по умолчанию.
    "speaker_name": config.SPEAKER_NAME,
    # Откуда нас слушают: подстрока имени микрофона (пусто — системный) и
    # какой вход брать у многоканальной карты. У автора это звуковая карта
    # со вторым входом, у другого человека — обычный системный микрофон.
    "mic_name": config.MIC_NAME,
    "mic_channel": config.MIC_CHANNEL,
    "listen_mode": config.LISTEN_MODE,
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
    # Сетка программ на телефоне. Читает их web/index.html по сообщению
    # `apps`, поэтому телефон ничего не знает и не проверяет сам.
    "phone_cols": config.PHONE_COLS,
    "phone_rows": config.PHONE_ROWS,
    "phone_icon_style": config.PHONE_ICON_STYLE,
    "phone_labels": config.PHONE_LABELS,
    # Нижние кнопки телефона. Хозяин 28 сентября захотел менять их на свои
    # («смена сцены в OBS», «замутить себя в Discord»), поэтому здесь ровно
    # четыре ячейки, а что делает каждая — выбирается в пульте. Кривая ячейка
    # молча становится ячейкой по умолчанию на своём месте.
    "phone_actions": list(DEFAULT_PHONE_ACTIONS),
    # Город для погоды. Пусто — погоды нет: телефон её не показывает и в сеть
    # никто не ходит. Выбирается в пульте поиском по геокодеру Open-Meteo.
    "weather_city": config.WEATHER_CITY,
    "weather_lat": config.WEATHER_LAT,
    "weather_lon": config.WEATHER_LON,
    # Мастер первого запуска показывается, пока это не сделано. У кого папка
    # Трубы уже была, мастер не нужен — см. `load_settings`.
    "first_run_done": False,
    # Тема оформления пульта и телефона. Меняет только пульт («Настройки →
    # Система»): на телефоне лишних кнопок быть не должно, поэтому страница
    # телефона берёт тему сообщением от сервера и помнит её в localStorage.
    "theme": config.THEME,
}

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
# Колонок всегда четыре: хозяин 28 сентября попросил «4 программы в ширину,
# рядов 2 или 3 — и всё». Ключ `phone_cols` в `settings.json` остаётся (старые
# файлы и `config.PHONE_COLS`), но значение в нём уже ничего не решает.
PHONE_COLS = 4
PHONE_ROWS_MIN = 2
PHONE_ROWS_MAX = 3
PHONE_ICON_STYLES = ("plate", "round", "bare")


def validate_phone_cols(value) -> int:
    """Колонок всегда четыре. Что бы ни прислали — телефон рисует 4.

    Старое «auto» и числа 3…6 остались в живых `settings.json`; если бы мы
    им верили, добавление девятой программы снова сделало бы пять колонок, и
    макет в пульте врал бы хозяину.
    """
    return PHONE_COLS


def validate_phone_rows(value) -> int:
    """Рядов сетки: 2 или 3. Всё прочее (в том числе 1) — два ряда."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return config.PHONE_ROWS
    if PHONE_ROWS_MIN <= number <= PHONE_ROWS_MAX:
        return number
    return PHONE_ROWS_MIN


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
    убрала бы хозяину все четыре кнопки, и телефон остался бы с пустым низом.
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
    # не ищем: apps.json хозяин может переписать руками в любой момент, и
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
# Проверки мягкие, как у сетки телефона: хозяин присылает значение из пульта,
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
# Проверки мягкие, как у сетки телефона: хозяин присылает значение из пульта,
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
    # Адрес локального сервера. Проверяем ещё раз: settings.json хозяин
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
    config.WEB_SEARCH_MODE = values["web_search_mode"]
    config.WEB_SEARCH_BUDGET = float(values["web_search_budget"])
    config.HEDGE = bool(values["hedge"])
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
    config.REQUIRE_NAME_WHEN_NOISY = values["require_name_when_noisy"]
    config.VOICE_APP_GUARD = bool(values["voice_app_guard"])
    config.OWNER_ONLY = values["owner_only"]
    config.OWNER_THRESHOLD = values["owner_threshold"]
    config.OUTPUT = values["output"]
    # Звуковые устройства. Имя ищется по подстроке, а пустое имя — это
    # системное устройство по умолчанию (см. `config.find_input_device`).
    config.SPEAKER_NAME = validate_device_name(values.get("speaker_name"))
    config.MIC_NAME = validate_device_name(values.get("mic_name"))
    config.MIC_CHANNEL = validate_mic_channel(values.get("mic_channel"))
    config.LISTEN_MODE = values["listen_mode"]
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
        except (json.JSONDecodeError, OSError):
            сохранённые = None
        if isinstance(сохранённые, dict):
            data.update(сохранённые)
    # Ключа в файле нет — значит, файл писала версия без мастера, и человек
    # не новичок: показывать ему мастер незачем. Файла нет вовсе — свежая
    # установка, мастер нужен.
    if "first_run_done" not in (сохранённые or {}):
        data["first_run_done"] = bool(файл_был)

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
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    Журнал и Панель берут её отсюда, поэтому формулировка одна: хозяин
    должен видеть, что с Трубой эта модель не проверялась.
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

    Пустая строка — не ошибка: значит, локальный сервер не настроен и хозяин
    ещё не выбрал его в пульте. Остальное — ошибка у поля `local_url`.
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
    PERSONA_PATH.parent.mkdir(exist_ok=True)
    PERSONA_PATH.write_text(text, encoding="utf-8")
