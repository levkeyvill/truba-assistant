r"""Сводка состояния для карточек наверху пульта.

Одно место, где собирается всё, что человек должен видеть, не открывая
настройки: чем слушает, чем говорит, чем думает, где телефон, что помнит
и как себя чувствует железо.

Карточки показывают и подключение, и задержки: по ним видны пропущенные
фразы и время ответа.
"""

from __future__ import annotations

import config

# Как называется режим слуха по-человечески.
MODES = {
    "always": "слышит всё",
    "name": "по имени",
    "off": "не слушает",
}


def everything(server=None) -> dict:
    """Всё состояние разом. Ничего не роняет: карточка с ошибкой лучше
    пустого пульта."""
    return {
        "слух": _hearing(),
        "голос": _voice(),
        "мозг": _brain(),
        "телефон": _phone(server),
        "память": _memory(),
        "железо": _hardware(),
        "повтор": _replay(server),
        "возможности": _возможности(),
    }


def _возможности() -> dict:
    """Что Труба умеет и включено ли это. Панель рисует из этого блока две
    строки-переключателя: «Поиск в интернете» и «Сама заговаривает».

    Только из config — без чтения файлов: Панель перерисовывается каждые
    две секунды, и лишние чтения тут ни к чему. Значения с этого среза
    Панель же и возвращает одним запросом, поэтому настройки применяются
    без перезапуска.
    """
    return {
        "поиск": bool(getattr(config, "WEB_SEARCH", False)),
        "поиск_где": getattr(config, "WEB_SEARCH_MODE", "free"),
        "первой": getattr(config, "PROACTIVE", "never"),
        "первой_последняя": getattr(config, "PROACTIVE_LAST", "sometimes"),
    }


def _replay(server) -> dict:
    """Мгновенный повтор NVIDIA — из сторожа пульта, журнал он читает сам.

    Сюда не читаем журнал заново: Панель спрашивает раз в пару секунд, а
    журнал NVIDIA — это мегабайты.
    """
    guard = getattr(getattr(server, "runtime", None), "replay_guard", None)
    last = getattr(guard, "last", None)
    on = getattr(last, "on", None)
    return {
        "вкл": on,
        "текст": last.text if last is not None else "не знаю",
        "следить": bool(getattr(config, "REPLAY_GUARD", True)),
    }


def _hearing() -> dict:
    return {
        "главное": MODES.get(config.LISTEN_MODE, config.LISTEN_MODE),
        "внизу": f"окно разговора {int(config.FOLLOW_UP_WINDOW)} с",
        "хорошо": config.LISTEN_MODE != "off",
    }


def _voice() -> dict:
    if config.TTS_ENGINE == "silero":
        главное = "Готовый голос"
        внизу = f"Silero · {config.SILERO_SPEAKER} · без видеопамяти"
        return {"главное": главное, "внизу": внизу, "хорошо": True}
    # Голос по образцу: назван движок и назван образец. Пустой образец — не
    # повод написать «всё хорошо»: так выглядело бы, будто она уже звучит.
    главное = "Голос по образцу"
    образец = (config.VOICE_NAME or "").strip() or "образец не выбран"
    движок = "Higgs" if config.TTS_ENGINE == "higgs" else "ESpeech"
    внизу = f"{движок} · {образец} · на видеокарте, пока голос включён"
    if not (config.VOICE_NAME or "").strip():
        return {"главное": главное, "внизу": внизу, "хорошо": False}
    return {"главное": главное, "внизу": внизу, "хорошо": True}


def _brain() -> dict:
    try:
        from core import settings

        provider = settings.get_provider()
        model = config.PROVIDERS.get(provider, {}).get("model", "?")
        # Ключ спрашиваем у settings: локальному серверу он не нужен, и
        # карточка не должна врать, что мозг «без ключа».
        есть_ключ = settings.has_key(provider)
        # Пометка под именем модели: локальная с Трубой не проверялась.
        # Одна короткая приписка, карточку не переделываем.
        локальный = settings.is_local(provider)
    except Exception:
        provider, model, есть_ключ, локальный = "?", "?", False, False

    внизу = model if есть_ключ else "нет ключа"
    if локальный and есть_ключ:
        внизу = f"{model} · локальная, не проверялась"
    return {
        "главное": provider,
        "внизу": внизу,
        "хорошо": есть_ключ,
    }


def _phone(server) -> dict:
    куда = "звук в телефон" if config.OUTPUT == "phone" else "звук в колонки"
    if server is None:
        return {"главное": "сервер не поднят", "внизу": куда, "хорошо": False}
    # Заряд телефона присылает он сам, раз в минуту. Показываем его здесь:
    # хозяин у компа, а телефон с зарядом 15% он видит не всегда.
    заряд = _заряд_телефона(server)
    if not server.connected:
        внизу = "не на связи"
    elif not getattr(server, "audio_ready", False):
        # Связь есть, а звука нет: страница подключается сразу, но браузер
        # не даёт запустить AudioContext без касания. Хозяин у компа об этом
        # иначе не узнает — и будет думать, что его не слышат.
        внизу = "на связи · звук выключен, коснись телефона"
    else:
        # «Куда звук» оставляем: по нему видно, говорит она в телефон или
        # в колонки комнаты. Заряд — следом.
        внизу = f"{куда} · {заряд}" if заряд else куда
    return {
        "главное": server.url.replace("http://", ""),
        "внизу": внизу,
        "хорошо": bool(server.connected),
    }


def _заряд_телефона(server) -> str:
    """`82% ⚡` последним, что прислал телефон. Пусто, если не прислал."""
    runtime = getattr(server, "runtime", None)
    note = getattr(runtime, "battery_note", None)
    if not callable(note):
        return ""
    try:
        return str(note() or "").strip()[:20]
    except Exception:
        return ""


def _memory() -> dict:
    try:
        from core import memory

        факты = memory.load_facts()
        реплики = len(memory.load_history(0))
    except Exception:
        return {"главное": "не читается", "внизу": "", "хорошо": False}

    if not факты:
        главное = "пока пусто"
    else:
        главное = f"{len(факты)} {_сколько(len(факты), ('факт', 'факта', 'фактов'))}"
    return {
        "главное": главное,
        "внизу": f"{реплики} реплик в разговоре",
        "хорошо": True,
    }


def _hardware() -> dict:
    try:
        from core import sysinfo

        снимок = sysinfo.snapshot()
    except Exception:
        return {"главное": "не читается", "внизу": "", "хорошо": False}

    цп = _число(снимок.get("cpu")) or 0
    видео = _число(снимок.get("gpu_load")) or 0
    занято = _число(снимок.get("gpu_mem_used")) or 0
    всего = _число(снимок.get("gpu_mem_total")) or 0
    итог = {
        "главное": f"процессор {цп:.0f}%, видеокарта {видео:.0f}%",
        "внизу": f"видеопамять {занято:.1f} из {всего:.1f} ГБ",
        "хорошо": цп < 90,
    }

    # Структурные метрики для широкой секции "Компьютер" в Панели.
    # Только реальные данные из sysinfo.snapshot(), ничего не выдумываем.
    for ключ in (
        "cpu",
        "cpu_temp",
        "ram",
        "ram_used",
        "ram_total",
        "gpu_load",
        "gpu_mem_used",
        "gpu_mem_total",
        "gpu_temp",
    ):
        значение = _число(снимок.get(ключ))
        if значение is not None:
            итог[ключ] = значение
    время = снимок.get("time")
    if isinstance(время, str) and время.strip():
        итог["time"] = время.strip()
    return итог


def _число(значение) -> float | None:
    try:
        if значение is None or isinstance(значение, bool):
            return None
        число = float(значение)
    except (TypeError, ValueError):
        return None
    if число != число or число in (float("inf"), float("-inf")):
        return None
    return число


def _сколько(n: int, формы: tuple[str, str, str]) -> str:
    from core.speech_text import plural

    return plural(n, формы)
