r"""Мозг: облачная модель через OpenAI-совместимый протокол.

Провайдер задаётся в .env одной строкой — DeepSeek, OpenRouter, MiniMax и
OpenAI говорят на одном протоколе, поэтому смена стоит ровно ничего.

Ответ отдаётся потоком и режется по предложениям: первое предложение уходит
в синтез, пока модель ещё дописывает остальное. Это главный способ убрать
паузу из разговора.
"""

from __future__ import annotations

import os
import random
import re
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Iterator

import config

# Режем на предложениях, но не на сокращениях и не на инициалах.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Минимальная длина куска, который есть смысл отправлять в синтез отдельно.
MIN_CHUNK = 25
# Сколько раз за ответ модель может сходить в интернет: поиск, чтение
# страницы, уточняющий поиск. Дальше — отвечает тем, что нашла.
TOOL_ROUNDS = 3
# И сколько секунд на всё это (по умолчанию; хозяин меняет в Настройках —
# config.WEB_SEARCH_BUDGET): голосом после «секунду, гляну» больше не
# ждут. Замерено 25 сентября: DeepSeek на вопрос про новости Таркова
# сходил в поиск трижды и заговорил через 24 с. Вышло время — отвечает тем,
# что уже нашла.
TOOL_BUDGET = 12.0
# Так начинается разметка вызова инструмента у DeepSeek, когда она
# прорывается в обычный текст. Вслух это не произносится.
TOOL_MARKUP = ("<｜", "｜DSML｜", "<|tool", "<tool_call>")
# Инструмент «закончить разговор» — только для голоса. Прощания бывают
# любыми: «стоп, ладно, забей, пофиг», «мы же уже поговорили», «ок». Список
# фраз в voice_loop ловит очевидное мгновенно, а всё остальное понимает
# сама модель по смыслу — 25 сентября она отвечала «всё, замолкаю», а окно
# разговора оставалось открытым ещё три минуты и ловило чужое.
END_NAME = "end_conversation"
# На сколько реплик история укорачивается разом, когда упёрлась в потолок.
# По одной нельзя: каждая новая реплика сдвигала бы начало запроса, и кеш
# префикса ловил бы один только характер. Пачкой начало стоит на месте
# несколько ответов подряд — с этого и дешевле входные токены.
HISTORY_KEEP_BACK = 8
END_TOOL = {
    "type": "function",
    "function": {
        "name": END_NAME,
        "description": (
            "Закончить разговор: перестать слушать его без обращения по имени. "
            "Вызывай, когда он даёт понять, что разговор окончен или просит "
            "отстать: прощается, благодарит в конце, говорит «хватит», «стоп», "
            "«ок, всё», «забей», «пофиг», «не слушай меня», «мы уже поговорили», "
            "«отдыхай». Попрощайся в ответ парой слов («Давай, пока!», «Не за что!») — "
            "молчание не отличить от «не услышала». Не "
            "вызывай, если он просто просит отвечать короче или меняет тему. "
            "Никогда не вызывай на приветствие, вопрос к тебе или обращение по "
            "имени — это начало разговора, на него надо ответить, даже если "
            "раньше ты замолкала."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
# Инструмент «это не ко мне» — только когда фраза прошла в ворота имени.
# 28.09 в 23:22 хозяин говорил о программе «Труба» (он её автор), а не с ней:
# регулярка имени слово нашла, а смысл не поняла, и она извинилась, что влезла,
# а потом выжимка памяти записала его слова как факт о нём. Смысл решает
# модель, шаблоны фраз тут не ворот — она сама решает, к ней ли обратились.
NOT_TO_ME_NAME = "not_to_me"
NOT_TO_ME_TOOL = {
    "type": "function",
    "function": {
        "name": NOT_TO_ME_NAME,
        "description": (
            "Имя «Труба» прозвучало, но говорят не с тобой: о программе «Труба», "
            "о тебе в третьем лице или с другим человеком. Тогда вызови этот "
            "инструмент и ничего не говори. Если к тебе обратились, спросили "
            "тебя или ты сомневаешься — отвечай как обычно и этот инструмент не "
            "зови. Например: «я доделываю трубу», «моя труба жрёт память» — "
            "просто упоминание, а «Труба, глянь…», «слышь, труба» — к тебе."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
# Что сказать, пока ищем, если сама она молчит: иначе секунды тишины.
FILLERS = ("Секунду, гляну.", "Щас посмотрю.", "Погоди, гляну в интернете.")
# Быстрый поиск: вопрос человека — в запросы для поисковика (как у
# Perplexica и Open WebUI). 29.09 «нейросети, которые недавно вышли, топ-5»
# как есть приносило рейтинги 2025 года; модель пишет «новые нейросети
# сентябрь 2026». Замер: 1.4–2.9 с.
REWRITE_ASK = (
    "Сегодня {today}. Недавний разговор:\n{talk}\n\n"
    "Человек попросил голосом найти в интернете: «{text}».\n"
    "Составь {n} коротких поисковых запроса ключевыми словами, не предложениями: "
    "вместе они должны найти ответ. Местоимения замени тем, о чём шла речь. "
    "Если спрашивают про новое, недавнее или «сейчас» — добавь месяц и год. "
    'Ответ строго JSON: {{"queries": ["…", "…"]}}'
)
REWRITE_TOKENS = 120
# Дольше не ждём: ищем по фразе как есть.
REWRITE_TIMEOUT = 4.0
# Кнопка поиска на телефоне. Человек не сказал вслух «найди» — он нажал
# кнопку, поэтому ворот по словам (ASKS, FRESH) тут не сработал бы никогда.
# Слова нужны сильные: 28.09 в 21:25 на фразу с кнопки она ответила
# болтовнёй без поиска, а в 21:26 — сняла экран и описала его.
SEARCH_ASK = (
    "Хозяин нажал кнопку поиска. Его фраза — это то, что надо найти, а не "
    "тема для разговора: запрос для поисковика составляй сама, коротко и по "
    "его словам. Сразу вызови web_search, потом ответь коротко по "
    "найденному. Не комментируй саму фразу и не переспрашивай, что искать."
)
# Интернет даётся, только когда о нём явно просят или спрашивают о том, что
# меняется. 25 сентября gpt-6-luna, раз сходив в поиск, искала потом на всё
# подряд: после «сколько времени в Японии» — на «друг хочет спросить» и на
# «ну ты и косячница», а на «Right Games, запускай» полезла искать лаунчер.
# Первая попытка пускала в интернет по любому «?» — простых вопросов это не
# спасало. Теперь на реплику без этих слов инструментов поиска у неё нет;
# чтобы поискала — «найди», «поищи», «загугли», «в интернете».
ASKS = re.compile(
    r"\b(найди|найти|поищи|поискать|поиск|погугли|загугли|гугл|интернет|инет|узнай)",
    re.IGNORECASE,
)
FRESH = re.compile(
    r"\b(что нового|новост|курс|погод|прогноз|цен[аыуе]|сколько\s+стоит|почём|стоимост|сч[её]т|"
    r"выиграл|победил|результат|расписани|выш(ел|ла|ли|ло)|выйдет|релиз|патч|"
    r"обновлени)",
    re.IGNORECASE,
)
# «Что у тебя новенького, какие обновления?» — это о ней, а не о мире.
# 25 сентября на такой вопрос она пошла искать новости про нейросети.
# Слова о свежем при «у тебя», «твои», «с тобой» интернет не открывают;
# явная просьба («найди», «в интернете») — открывает всегда.
ABOUT_HER = re.compile(r"\bу тебя\b|\bс тобой\b|\bтво(й|я|ё|е|и|их|ими|ем|ей|ю)\b",
                       re.IGNORECASE)


# Короткое уточнение сразу после поиска: «А в евро?», «А завтра?». Без
# ключевых слов, но ему нужен интернет. Только следующей репликой за
# поиском, не позже FOLLOW_UP_MINUTES.
FOLLOW_UP = re.compile(r"^\s*[аи]\b", re.IGNORECASE)
FOLLOW_UP_MINUTES = 5
# Закончить разговор — только если в реплике есть что-то прощальное.
# 25 сентября на «Запомни, что я завтра иду к стоматологу» она молча
# закрыла разговор, приняв просьбу за точку.
BYE = re.compile(
    r"\b(спасибо|спс|пока|всё|все|хватит|стоп|забей|отдыхай|отстань|не слушай|"
    r"поговорили|ок|окей|пофиг|проехали|до связи|спокойной|бывай|давай|"
    r"свободна|отбой|молчи|замолчи|тихо|потом)\b",
    re.IGNORECASE,
)
# Длиннее — это уже разговор, а не прощание.
BYE_WORDS = 12


def wants_web(text: str) -> bool:
    """Давать ли интернет на эту реплику (см. ASKS, FRESH, ABOUT_HER)."""
    text = text or ""
    if ASKS.search(text):
        return True
    return bool(FRESH.search(text)) and not ABOUT_HER.search(text)


# Напоминание о длине — в хвост, ближе всего к вопросу. Правило «Про длину
# ответа» в характере стоит в самом начале длинного промпта, и DeepSeek его
# теряет: 25 сентября на прогоне из 44 реплик у него 13 ответов по 4–6
# предложений (20–30 с речи) на «что посоветуешь», «расскажи анекдот».
ALOUD = (
    "Сейчас разговор голосом. По умолчанию — одна-две короткие фразы; "
    "длиннее, только если он сам просит объяснить или рассказать подробно."
)
# Клиент облака: соединение ждём 4 с и пробуем ещё раз один, а не два. По
# умолчанию это 5 с × 3 — при отвалившемся VPN 25 сентября она молчала 17 с,
# прежде чем сдаться. Дальше — запасной провайдер (_fall_back).
CONNECT_TIMEOUT = 4.0
RETRIES = 1
# Сколько живёт соединение после последнего запроса. По умолчанию httpx
# закрывает его через 5 с простоя, а в разговоре паузы между репликами
# длиннее: замер 26 сентября через VPN хозяина дал провал до 6.4 с на
# холодном соединении и 1.0–2.0 с на тёплом. Держим соединение живым.
KEEPALIVE = 300.0
# Потолок для выжимки фактов. Модель рассуждающая: размышления тратят те же
# токены, что и ответ, и на скупом потолке ответ приходит пустым.
DIGEST_TOKENS = 1500

# Просьба не думать вслух. Замерено на deepseek-flash 21 сентября 2026:
# выжимка фактов из восьми реплик стоила 3425 токенов, из них 3292 —
# размышления. С этой просьбой — 24 токена и тот же ответ.
#
# Поле не общее для всех провайдеров, поэтому применяется с оглядкой:
# отказался — спрашиваем как раньше и больше не пробуем.
NO_THINKING = {"thinking": {"type": "disabled"}}


def completion_limits(provider: str, limit: int, temperature: float | None = None) -> dict:
    """Параметры Chat Completions с учётом прямого OpenAI API."""
    if provider == "openai":
        # Reasoning-модели OpenAI не принимают max_tokens и нередко
        # отвергают пользовательскую температуру.
        return {"max_completion_tokens": limit}
    result = {"max_tokens": limit}
    if temperature is not None:
        result["temperature"] = temperature
    return result

DAYS = (
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
)
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _now_line() -> str:
    """Который час — словами, как сказал бы человек.

    Модель не знает времени вовсе: у неё нет часов, а в промпте его не было.
    На вопрос «сколько времени» она честно выдумывала. Строка идёт
    отдельным сообщением в самом конце, чтобы не ломать кеш промпта:
    характер и история остаются неизменными, меняется только хвост.
    """
    from datetime import datetime

    now = datetime.now().astimezone()
    minutes = int(now.utcoffset().total_seconds() // 60)
    zone = f"UTC{'+' if minutes >= 0 else '-'}{abs(minutes) // 60}"
    if minutes % 60:
        zone += f":{abs(minutes) % 60:02d}"
    return (
        f"Сейчас {now:%H:%M} ({zone}), {DAYS[now.weekday()]}, "
        f"{now.day} {MONTHS[now.month - 1]} {now.year} года. "
        "Часы у тебя есть — если спрашивают время или дату, отвечай по этой строке. "
        "Время в других странах считай сама по разнице поясов, в интернете не ищи."
    )


def _args_of(call: dict) -> dict:
    """Аргументы вызова инструмента. Битый JSON — пустые аргументы."""
    import json

    try:
        args = json.loads(call.get("args") or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _query_of(call: dict) -> str:
    """Что искала или читала — для пометки в истории."""
    args = _args_of(call)
    return str(args.get("query") or args.get("url") or "")[:160]


# Сколько источников показываем телефону: три — и список помещается на экран,
# и хватает, чтобы понять, откуда ответ.
MAX_SOURCES = 3


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").strip()
    except ValueError:
        return ""


def _search_wait() -> float:
    """Сколько быстрый поиск ждёт поисковик — страховка от зависания.

    Сроки у самих поисков свои; этот — чуть больше самого долгого пути в
    выбранном режиме. 29.09 он был один на всех, 17 с, и платный поиск
    (до 25 с) обрывался раньше, чем успевал ответить.
    """
    from core import web

    # Бесплатно: запросы от модели, поисковики, чтение страниц — подряд.
    free = REWRITE_TIMEOUT + web.SEARCH_DEADLINE + web.READ_DEADLINE + 2.0
    mode = getattr(config, "WEB_SEARCH_MODE", "free")
    if mode == "paid":
        return web.PAID_TIMEOUT + 3.0
    if mode == "auto":
        return free + web.PAID_TIMEOUT
    return free


def _attach_found(messages: list[dict], query: str, result: str, ok: bool = True) -> None:
    """Итог быстрого поиска — к реплике человека, только в этот запрос.

    Сообщение копируется, а не правится на месте: в истории реплика должна
    остаться как сказана, без найденного (оно не копится в каждом следующем
    запросе — как и при обычном поиске).

    ok=False — поисковики не ответили. Второй попытки нет: 29.09 вживую
    они падали оба раза подряд, и ответ ждал 26 секунд вместо 10.
    """
    if not messages or messages[-1].get("role") != "user":
        return
    last = dict(messages[-1])
    if not isinstance(last.get("content"), str):
        return
    if ok:
        last["content"] += (
            f"\n\n[Ты уже поискала в интернете по запросу «{query}». Найдено "
            f"(results — выдача, pages — тексты верхних страниц):]\n{result}\n"
            "[Ответь по найденному коротко и своими словами, для голоса: без ссылок "
            "и без списков. Если ответа в найденном нет — так и скажи.]"
        )
    else:
        last["content"] += (
            f"\n\n[Ты попробовала поискать в интернете «{query}», но поисковики "
            "сейчас не ответили. Скажи об этом одной фразой. Если знаешь ответ "
            "сама — коротко ответь и предупреди, что это по памяти, а не из "
            "интернета; свежих цифр и новостей по памяти не называй.]"
        )
    messages[-1] = last


def _collect_sources(name: str, result: str, into: list) -> None:
    """Кладёт в `into` то, что реально пришло из интернета: {title, host, url}.

    Только из успешных вызовов и только с адресом: источник без ссылки —
    это не источник, а пересказ. Из поиска берём верхние результаты, из
    прочитанной страницы — её саму. Дубли убираем по адресу.
    """
    if len(into) >= MAX_SOURCES or not result or result.startswith('{"error"'):
        return
    import json

    try:
        data = json.loads(result)
    except ValueError:
        return
    if not isinstance(data, dict):
        return

    found: list[dict] = []
    if name == "web_search":
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            found.append({
                "title": str(item.get("title", "")).strip()[:200],
                "host": _host_of(str(item.get("url", "")).strip()),
                "url": str(item.get("url", "")).strip(),
            })
    elif name == "read_page":
        url = str(data.get("url", "")).strip()
        if url:
            found.append({"title": url[:200], "host": _host_of(url), "url": url})

    for source in found:
        if len(into) >= MAX_SOURCES:
            return
        if not source["url"] or not source["host"]:
            continue
        if any(old["url"] == source["url"] for old in into):
            continue
        into.append(source)


def _found_note(queries: list) -> str:
    listed = "; ".join(f"«{q}»" for q in queries[:3])
    return (
        f"(Служебно, вслух не говорить: ответ выше ты дала по поиску в интернете — {listed}. "
        "Он не выдуман. Перепроверять его без просьбы не нужно.)"
    )


def _at_of(turn: dict) -> datetime | None:
    """Время реплики истории. None — его нет или оно не разбирается."""
    at = turn.get("at")
    if not isinstance(at, str) or not at:
        return None
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def _new_talk_note(when: datetime) -> str:
    """Пометка «здесь начинается новый разговор» — по времени реплики.

    Хозяин 27.09: «ей надо разделять инфу, иначе зацикливается на том, что уже
    не надо». Ночной рассказ про диктовку лежал в истории, и утром модель
    повторяла его слова вместо дела. Пометка говорит прямо: всё выше — прошлые
    разговоры, к нынешней просьбе они не относятся.
    """
    return (
        f"— Дальше новый разговор ({when:%d.%m, %H:%M}). Всё выше — прошлые "
        "разговоры: к нынешней просьбе они не относятся, не продолжай их и "
        "не повторяй оттуда просьбы и объяснения, пока хозяин сам о них не "
        "заговорит. —"
    )


def read_usage(usage) -> dict:
    """Сколько токенов стоил один запрос, по тому, что прислал провайдер.

    Попадание в кеш OpenAI пишет в `prompt_tokens_details.cached_tokens`,
    DeepSeek — в `prompt_cache_hit_tokens` (в `prompt_tokens_details` у него
    для совместимости то же самое). Нет поля — 0: лучше показать ноль, чем
    не показать расход вовсе.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None)
    if cached is None:
        cached = getattr(usage, "prompt_cache_hit_tokens", None)
    return {
        "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
        "cached": int(cached or 0),
        "completion": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def _client(provider: str):
    import httpx
    from openai import OpenAI

    from core import settings

    spec = config.PROVIDERS[provider]
    # Свой httpx-клиент — только ради keepalive. Свой же таймаут задаём и
    # ему: OpenAI берёт timeout клиента, а не переданный в конструкторе.
    http = httpx.Client(
        limits=httpx.Limits(keepalive_expiry=KEEPALIVE),
        timeout=httpx.Timeout(90.0, connect=CONNECT_TIMEOUT),
    )
    return OpenAI(
        # Ключ берём через settings.key_for: у локального сервера его нет,
        # а клиент непустой api_key требует — отдаём заглушку.
        api_key=settings.key_for(provider),
        base_url=spec["base_url"],
        timeout=httpx.Timeout(90.0, connect=CONNECT_TIMEOUT),
        max_retries=RETRIES,
        http_client=http,
    )


# OpenAI не пускает из России: без VPN на каждый запрос 403
# unsupported_country_region_territory. 25 сентября VPN отвалился на
# несколько минут посреди игры, и она молча не отвечала ни на что. Теперь на
# такой отказ отвечает запасной провайдер, у которого есть ключ, а основной
# пробуется снова через HOME_RETRY секунд.
REGION_BLOCK = ("unsupported_country", "country, region, or territory")
SPARE_ORDER = ("deepseek", "openrouter", "minimax", "openai")
HOME_RETRY = 600.0

# Страховка от заминок облака. Замерено 26 сентября через VPN хозяина: у
# всех провайдеров случаются случайные провалы по ~6 с до первого слова
# (gpt-6-luna 6.8/6.3/1.7, gpt-5.6 0.97/1.1/6.1, deepseek 1.4/6.4/1.3).
# Тёплое соединение (_client, KEEPALIVE) от части провалов спасает, но не
# от всех. Если за HEDGE_AFTER секунд основной не прислал НИ ОДНОГО куска
# (ни текста, ни вызова инструмента, ни размышлений) — тот же вопрос уходит
# запасному, отвечает тот, кто пришёл первым.
#
# Выключено по умолчанию: при заминке платим за оба запроса, а хозяин на
# лишние траты не согласен. Галочка в Настройках → Ответы.
HEDGE_AFTER = 2.5

# Конец потока от провайдера: дальше кусков не будет.
_END = object()


def _close_stream(stream) -> None:
    """Закрыть поток, не разбирая исключений: он уже не нужен."""
    closer = getattr(stream, "close", None)
    if closer is None:
        return
    try:
        closer()
    except Exception:
        pass


# Как часто подогреваем соединение, пока голос включён. Замер 26 сентября:
# httpx по умолчанию рвёт соединение через 5 с простоя, а паузы в разговоре
# длиннее. Раз в 45 с шлём бесплатный GET {base_url}/models — запрос
# дешёвый, а соединение после него тёплое.
WARM_EVERY = 45.0


class KeepWarm:
    """Держит соединение с облаком тёплым, пока включён голос.

    Отдельный поток, свой таймер: ни в потоке окна, ни в голосовом цикле
    сетевых запросов быть не должно. Ошибки молча игнорируем — подогрев
    не должен ничего ломать, он только убирает заминку. Хозяин может
    позвонить в другой провайдер (`client`) — берём текущий на каждый
    запрос, чтобы грелось то, что реально отвечает.
    """

    def __init__(self, client, every: float = WARM_EVERY):
        self.every = every
        self._client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.pings = 0

    def set_client(self, client) -> None:
        self._client = client

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="keep-warm")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.every + 1.0)

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            try:
                self._client.models.list()
                self.pings += 1
            except Exception:
                # Молча: подогрев не ломает голос, а чинить связь он не умеет.
                pass


class _Hedge:
    """Один вопрос — двум провайдерам сразу; отвечает кто быстрее.

    Основной запрос уходит сразу. Если за `after` секунд не пришло ни
    одного куска, тот же вопрос уходит запасному (`open_spare`). Кто
    прислал кусок первым, тот и отвечает: поток проигравшего закрывается,
    его ответ выбрасывается целиком. Деньги за проигравший запрос уже
    потрачены — об этом пишем в журнал (событие `hedge`).

    Оба чтения идут в своих потоках: `next()` по потоку блокирует, иначе
    «молчит облако или нет» было бы нечем мерить. Наружу объект отдаёт
    куски победителя как обычный итератор — дальше с ним работает
    `_sentences` без всяких знаний о страховке.

    `on_error("main", exc, hedge)` — основной отказал; если он вернул True,
    его запрос уходит заново (см. `retry`), иначе ошибка уходит наверх.
    `on_winner(имя, сработала_ли_страховка)` — пришёл первый кусок.
    """

    def __init__(self, open_main, open_spare, after, on_error=None, on_winner=None):
        self._open_main = open_main
        self._open_spare = open_spare
        self._after = after
        self._on_error = on_error
        self._on_winner = on_winner
        self._queue: Queue = Queue()
        self._streams: dict[str, object] = {}
        self._started: set[str] = set()
        self._done: set[str] = set()
        self._winner: str | None = None
        self._spare_started = False
        # Страховка реально сработала: запасной запрос ушёл. Отличается от
        # `_spare_started` после `retry`, где запасной просто отменён.
        self._fired = False
        self._error: Exception | None = None
        self._started.add("main")
        threading.Thread(target=self._read, args=("main", open_main),
                         daemon=True, name="hedge-main").start()

    def __iter__(self):
        return self

    def retry(self, open_main) -> None:
        """Основной отказал: его запрос заново, и уже без страховки.

        Страховку выключаем: провайдер сменился (см. `_fall_back`), а
        запасным теперь будет тот, кто только что отказал. Событие в
        журнал при этом не пишется: запасной запрос так и не ушёл.
        """
        self._open_main = open_main
        self._after = None
        self._spare_started = True
        self._fired = False
        self._started.add("main")
        threading.Thread(target=self._read, args=("main", open_main),
                         daemon=True, name="hedge-main").start()

    def _start_spare(self) -> None:
        if self._spare_started:
            return
        self._spare_started = True
        self._fired = True
        self._started.add("spare")
        threading.Thread(target=self._read, args=("spare", self._open_spare),
                         daemon=True, name="hedge-spare").start()

    def _read(self, who: str, opener) -> None:
        try:
            stream = opener()
            self._streams[who] = stream
        except Exception as exc:
            self._queue.put((who, exc))
            return
        try:
            for chunk in stream:
                self._queue.put((who, chunk))
        except Exception as exc:
            self._queue.put((who, exc))
            return
        self._queue.put((who, _END))

    def _alive(self) -> bool:
        """Есть ли ещё кто-то, кто может ответить."""
        return any(name not in self._done for name in self._started)

    def _win(self, who: str) -> None:
        self._winner = who
        for name, stream in self._streams.items():
            if name != who:
                _close_stream(stream)
        if self._on_winner is not None:
            self._on_winner(who, self._fired)

    def __next__(self):
        while True:
            try:
                who, item = self._queue.get(
                    timeout=self._after if self._winner is None else None
                )
            except Empty:
                # Основной молчит дольше отведённого — страхуемся.
                self._start_spare()
                continue
            if isinstance(item, Exception):
                if (who == "main" and self._winner is None
                        and not self._spare_started):
                    # Основной отказал: уходим на запасного целиком, его
                    # запрос уходит заново — этот «main» больше не умерший.
                    if self._on_error is not None and self._on_error(who, item, self):
                        continue
                    self._error = item
                # Отказ запасного или проигравшего: вычёркиваем его и ждём
                # того, кто ещё может ответить.
                self._done.add(who)
                if self._winner is None and not self._alive():
                    raise self._error or item
                continue
            if item is _END:
                self._done.add(who)
                if who == self._winner or (self._winner is None and not self._alive()):
                    raise StopIteration
                continue
            if self._winner is None:
                self._win(who)
            if who != self._winner:
                # Ответ проигравшего выбрасываем: он уже оплачен.
                continue
            return item


def read_persona(path) -> str:
    """Текст характера из файла; файла нет — выбранный готовый характер.

    Свежая установка: своего текста ещё нет (он появляется, когда характер
    сохраняют в пульте). 28.09 без файла мозг не создавался вовсе, и Труба
    молчала бы — в пробную установку файл попал случайно.
    """
    try:
        return open(path, encoding="utf-8").read().strip()
    except FileNotFoundError:
        from core import personas

        return personas.text(getattr(config, "PERSONA_PRESET", personas.DEFAULT)).strip()


@dataclass
class Brain:
    provider: str = field(default_factory=lambda: config.LLM_PROVIDER)
    persona_path: str = None

    def __post_init__(self) -> None:
        self._reply_lock = threading.RLock()
        if self.provider not in config.PROVIDERS:
            known = ", ".join(config.PROVIDERS)
            raise ValueError(f"Неизвестный провайдер '{self.provider}'. Есть: {known}")

        spec = config.PROVIDERS[self.provider]
        from core import settings

        # Ключ обязателен всегда, кроме локального сервера: у него ключа нет
        # вовсе, вместо него уходит заглушка (см. settings.key_for).
        if not settings.key_for(self.provider):
            raise RuntimeError(
                f"Нет ключа {spec['key_env']}. Положи его в .env рядом с config.py"
            )
        if not str(spec.get("model") or "").strip():
            if settings.is_local(self.provider):
                raise RuntimeError(
                    "Локальная модель не выбрана: впиши её в Настройки → "
                    "Ответы и подключение"
                )
            raise RuntimeError(
                f"Не выбрана модель для {self.provider}. Впиши её в Настройки → Ответы"
            )

        self._client = _client(self.provider)
        self._model = spec["model"]
        # Свой провайдер, к которому возвращаемся после запасного.
        self._home = self.provider
        self._home_at = 0.0
        # Без maxlen: очередь сама по себе выталкивала бы по одной реплике,
        # и начало запроса сдвигалось бы на каждом ответе — кеш префикса
        # тогда держал только характер. Режем пачкой, см. _trim_history.
        self._history: deque[dict] = deque()

        # Разговор переживает перезапуск: без этого каждое утро начиналось
        # с чистого листа, хотя вчера полчаса говорили.
        from core import memory

        if config.HISTORY_TURNS > 0:
            for turn in memory.load_history(config.HISTORY_TURNS):
                self._history.append(turn)
        # Сколько реплик добавлено с запуска и ещё не разобрано на факты.
        # Считать по длине истории нельзя: она ограничена (HISTORY_TURNS),
        # и когда заполнилась, новые реплики вытесняют старые, а длина стоит
        # на месте. Так и было до 25 сентября: выжимка брала «всё после 24-й
        # реплики» из истории длиной 24 — то есть ничего, и память молча
        # перестала учиться 23 сентября. Поднятое с диска считаем разобранным.
        self._undigested = 0
        # Спрашивала ли модель о памяти в последней выжимке. Нужен, чтобы
        # отличить «спросила, нового нет» от «было нечего спрашивать»:
        # возвращаются оба раза пустым словарём (см. digest).
        self.did_ask = False
        # Понимает ли провайдер просьбу не думать вслух. None — не пробовали.
        self._quiet: bool | None = None
        # Принимает ли провайдер инструменты (интернет). None — не пробовали.
        self._tools_ok: bool | None = None
        # Этот ответ — ход с кнопки поиска на телефоне: первый круг обязан
        # что-то найти, иначе модель отвечает болтовнёй (28.09, 21:25).
        # Ставится на время ответа, снимается в конце (см. `_reply_unlocked`).
        self._must_search = False
        # Понимает ли провайдер `tool_choice`. False — отказался: убираем
        # параметр и больше не шлём, но инструменты оставляем (иначе такой
        # отказ выключил бы интернет навсегда).
        self._choice_ok = True
        # Принимает ли провайдер stream_options с расходом токенов. None —
        # не пробовали. Отказавшийся провайдер идёт без него и больше не
        # тратит попытку на отказ.
        self._usage_ok: bool | None = None
        # Куда сообщать о поисках: пульт кладёт это в журнал.
        self.on_event = None
        # Кто ответил со страховкой (см. `_hedge`): None — никто. Имя
        # запасного нужно, чтобы на следующем круге отдать его вызовы
        # инструментов основному текстом, и чтобы событие `tokens`
        # назвало модель, которая реально ответила.
        self._hedge_spare: str | None = None
        self._hedge_model: str | None = None
        # Прогрев соединения, пока включён голос (см. KeepWarm).
        self._warm: KeepWarm | None = None
        # Действия на компе: снимок экрана, клип, взгляд на экран. Заполняет
        # тот, кто создаёт мозг (ui/web_runtime.py, VoiceLoop) — brain.py про
        # голосовой цикл не знает и знать не должен. Чего здесь нет, того
        # инструмента модель не получает.
        self.actions: dict[str, object] = {}
        # Попросила ли модель закончить разговор в последнем ответе.
        self.ended = False
        # Решила ли модель в последнем ответе, что имя прозвучало, а говорили
        # не с ней. Тогда она молчит, и голосовой цикл закрывает окно.
        self.not_to_me = False
        # Источники последнего ответа — что реально пришло из интернета.
        # Их показывает кнопка поиска на телефоне.
        self.last_sources: list[dict] = []
        # Ходила ли она в этом ответе в интернет. По этому признаку телефон
        # показывает карточку поиска после **любого** такого ответа, а не
        # только после кнопки «Найти» (30.09, хозяин: «после любого поиска,
        # с касанием»).
        self.searched: bool = False
        # Запросы, которые реально ушли в поисковик: их пишет модель сама
        # (`web_queries`), и карточка на телефоне должна открыть в браузере
        # именно этот запрос, а не слова хозяина.
        self.last_queries: list[str] = []
        # Когда в этом ответе уходили в модель и когда от неё пришло первое
        # слово и первое готовое предложение — по каждому кругу инструментов.
        # Считает только perf_counter, голосовой цикл берёт это для замера
        # ответа в журнал (ui/web_runtime.py::_log_messages).
        self.last_timing: dict = {"rounds": []}

        self._persona = read_persona(
            self.persona_path or (config.ROOT / "prompts" / "persona.md"))

        # Что она знает о человеке. Лежит отдельно от характера, чтобы можно
        # было править факты, не трогая манеру речи.
        about = config.ROOT / "prompts" / "about_user.md"
        if about.exists():
            self._persona += (
                "\n\n# Что ты знаешь о хозяине\n\n"
                + about.read_text(encoding="utf-8").strip()
            )

    @property
    def model(self) -> str:
        return f"{self.provider}/{self._model}"

    # --- Тёплое соединение -----------------------------------------------

    def warm_start(self, every: float = WARM_EVERY) -> None:
        """Пока голос включён, греем соединение (см. KeepWarm).

        Отдельный поток внутри: голосовой цикл о нём не знает, как и
        мозг не знает про голос.
        """
        self._warm = KeepWarm(self._client, every)
        self._warm.start()

    def warm_stop(self) -> None:
        warm, self._warm = self._warm, None
        if warm is not None:
            warm.stop()

    # --- Запасной провайдер ----------------------------------------------

    def _switch(self, provider: str) -> None:
        spec = config.PROVIDERS[provider]
        self._client = _client(provider)
        self._model = spec["model"]
        self.provider = provider
        # Что провайдер понимает, выясняется заново.
        self._quiet = None
        self._tools_ok = None
        self._usage_ok = None
        self._choice_ok = True
        # Прогреваем уже того, кто отвечает (см. KeepWarm).
        warm = getattr(self, "_warm", None)
        if warm is not None:
            warm.set_client(self._client)

    def _spare(self) -> str | None:
        """Запасной провайдер: только облако с ключом.

        Локальный сервер запасным не бывает никогда — даже если ключ ему
        подставили. Облако молча уходить на чужой локальный сервер не
        должно: хозяин выбирал облако, и платит за него. Обратное верно —
        сам `local` как основной при обрыве связи уходит на облачный
        запасной по общим правилам (см. `_fall_back`).
        """
        from core import settings

        for name in SPARE_ORDER:
            spec = config.PROVIDERS.get(name)
            if name == self.provider or not spec or settings.is_local(name):
                continue
            if os.environ.get(spec["key_env"]):
                return name
        return None

    def _fall_back(self, exc: Exception) -> bool:
        """Основной не пускает из этой страны или не отвечает — на запасной.

        True — перешли, запрос можно повторить. Прочие ошибки не трогаем:
        кончились деньги или неверный ключ запасной не вылечит тихо.
        """
        from openai import APIConnectionError

        region = any(mark in str(exc).lower() for mark in REGION_BLOCK)
        if not region and not isinstance(exc, APIConnectionError):
            return False
        with self._reply_lock:
            spare = self._spare()
            if spare is None:
                return False
            was = self.provider
            self._switch(spare)
            self._home_at = time.monotonic() + HOME_RETRY
        self._tell("provider_fallback", {"from": was, "to": spare,
                                         "why": "region" if region else "connection"})
        return True

    @staticmethod
    def _flatten_tool_rounds(messages: list[dict]) -> None:
        """Поиски прежнего провайдера — одной заметкой с найденным.

        Чужие вызовы инструментов запасной может не принять: DeepSeek в
        режиме размышлений требует к ним свои размышления. Найденное
        остаётся текстом, сами вызовы убираются.
        """
        found = [m["content"] for m in messages if m.get("role") == "tool" and m.get("content")]
        if not found:
            return
        messages[:] = [m for m in messages if m.get("role") != "tool" and not m.get("tool_calls")]
        messages.append({
            "role": "system",
            "content": "Уже найдено в интернете по этому вопросу:\n\n" + "\n\n".join(found),
        })

    def _maybe_home(self) -> None:
        """Пора снова попробовать свой провайдер: VPN мог вернуться."""
        if self.provider == self._home or time.monotonic() < self._home_at:
            return
        with self._reply_lock:
            if self.provider == self._home:
                return
            was = self.provider
            self._switch(self._home)
        self._tell("provider_back", {"from": was, "to": self._home})

    def _tell(self, kind: str, payload) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def _messages(
        self, user_text: str, context: str | None, image: str | None = None,
        aloud: bool = False, search: bool = False,
    ) -> list[dict]:
        """Системный промпт идёт первым и неизменным — так он попадает в кеш."""
        system = self._persona
        if context:
            system += f"\n\n## Что происходит прямо сейчас\n\n{context}"
        if search:
            system += f"\n\n{SEARCH_ASK}"

        if image:
            # Картинка кладётся рядом с текстом в одном сообщении. Модель
            # принимает её как обычный data-URL, отдельной ручки не надо.
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image}},
            ]
        else:
            content = user_text

        # Часы и список умений идут последними, отдельным сообщением.
        # В характер их класть нельзя: характер неизменен и потому попадает
        # в кеш, а время меняется каждую минуту и кнопки — по мере правки.
        tail = [_now_line(), self._abilities()]
        if aloud:
            tail.append(ALOUD)
        remembered = self._memory()
        if remembered:
            tail.append(remembered)
        dated = [turn.get("at") for turn in self._history if isinstance(turn.get("at"), str)]
        if dated:
            try:
                last = datetime.fromisoformat(dated[-1])
                minutes = max(0, int((datetime.now() - last).total_seconds() // 60))
                tail.append(
                    f"Последняя сохранённая реплика разговора была {last:%d.%m.%Y в %H:%M} "
                    f"({minutes} мин назад). Не считай прошлые планы человека текущими."
                )
            except ValueError:
                pass

        past: list[dict] = []
        # Пометка нового разговора — по времени самих реплик, поэтому
        # одинаковая история всегда даёт одинаковый запрос и кеш начала
        # запроса не ломается. Реплики без `at` — без пометки: время неизвестно.
        gap = timedelta(minutes=float(getattr(config, "HISTORY_GAP_MINUTES", 40) or 40))
        previous_at: datetime | None = None
        for turn in self._history:
            when = _at_of(turn)
            if when is not None and previous_at is not None and when - previous_at > gap:
                past.append({"role": "system", "content": _new_talk_note(when)})
            if when is not None:
                previous_at = when
            past.append({"role": turn["role"], "content": turn["content"]})
            if turn.get("searched"):
                past.append({"role": "system", "content": _found_note(turn["searched"])})
        return [
            {"role": "system", "content": system},
            *past,
            {"role": "system", "content": "\n\n".join(tail)},
            {"role": "user", "content": content},
        ]

    def _abilities(self) -> str:
        """Что она умеет руками. Список берётся из живого `apps.json`."""
        from core import abilities, launcher, web

        try:
            apps = launcher.read_list()
        except Exception:
            apps = []
        text = abilities.describe(apps, web=self._tools_on())
        if self._tools_on():
            text += "\n\n" + web.HINT
        return text

    def _tools_on(self) -> bool:
        """Отдавать ли модели интернет. Отказался провайдер — больше не пробуем."""
        return bool(getattr(config, "WEB_SEARCH", False)) and self._tools_ok is not False

    def _wants_tool_choice(self, messages: list[dict]) -> bool:
        """Требовать ли от модели вызов инструмента в этом круге.

        Только ход с кнопки поиска на телефоне и только первый круг: как
        только в истории появились вызовы инструментов (`role == "tool"`),
        модель и сама знает, что делать, а `tool_choice` там только мешает.
        Провайдеру, который параметр отверг, больше не шлём его — но отказ
        от `tool_choice` выключать инструменты не должен (см.
        `_open_stream_here`).
        """
        if not getattr(self, "_must_search", False):
            return False
        if getattr(self, "_choice_ok", True) is False:
            return False
        return not any(m.get("role") == "tool" for m in messages)

    def _tool_list(self, aloud: bool, user_text: str = "", search: bool = False,
                   named: bool = False) -> list[dict]:
        """Инструменты для этого ответа: действия, интернет, конец разговора.

        Действия — всегда, одним и тем же набором и в одном порядке, первыми
        в списке. Угадывать по глаголам нельзя: распознавание речи коверкает
        слова («Шот», «посмотрю» вместо «попью»), и ворота промахивались мимо
        просьбы. Одинаковый набор нужен ещё и для кеша OpenAI: запрос с тем
        же началом стоит дешевле. Чего в `actions` нет — того инструмента нет.

        Интернет — только если о нём просят или спрашивают о свежем
        (`wants_web`). Исключение одно: `search` — кнопка поиска на телефоне.
        Там человек ничего не сказал вслух про интернет, но нажал именно
        кнопку поиска: ворот по словам тут не было бы вовсе.
        Закончить разговор — не на реплику с вопросом: 25 сентября на
        «ну всё, окей… пойду за пивом. Как думаешь, вариант?» она молча
        закрыла разговор, зацепившись за начало.

        Ход с кнопки поиска — только интернет, и только он: 28.09 в 21:26 на
        фразу с кнопки она сняла экран и описала его вместо поиска. Всё
        остальное (запуск программ, снимок, заметки, конец разговора) в этом
        ходу не нужно и только отвлекает.

        «Это не ко мне» — только когда фраза прошла в ворота имени (`named`),
        и в самый конец набора: начало списка и кеш от этого не меняются. Без
        имени инструмента нет, на ход с кнопки поиска — тоже: там человек
        сказал, чего хочет, и молчать незачем.
        """
        from core import hands, web

        if self._tools_ok is False:
            return []
        if search and self._tools_on():
            return list(web.TOOLS)
        text = user_text or ""
        follow_up = "?" in text and FOLLOW_UP.match(text) and self._searched_just_now()
        tools: list[dict] = []
        apps = hands.read_list()
        if apps:
            tools.append(hands.tool(apps))
            # Сразу за запуском и всегда: порядок набора не меняется, на нём
            # держится кеш запроса.
            tools.append(hands.close_tool(apps))
        # YouTube — тоже всегда и тоже сразу после close_app, независимо от
        # того, что в apps.json: ролики с каналами запускать не кнопкой.
        tools.append(hands.YT_TOOL)
        # Заметки — сразу после YouTube и тоже всегда: в разговоре он просит
        # «запиши это в заметки», и спрашивать уточнение в круге дороже, чем
        # дать оба инструмента. Порядок набора не меняется — на нём держится
        # кеш запроса.
        tools.append(hands.NOTE_TOOL)
        tools.append(hands.READ_TOOL)
        # Диктовка — сразу за чтением и только если голос её умеет. Без
        # этого инструмента модель на «сделаешь заметку небольшую?» отвечала
        # словами «диктуй», а записывать было некому: 27.09 так пропала минута
        # диктовки целиком. Порядок набора не меняется — на нём кеш запроса.
        if callable((getattr(self, "actions", None) or {}).get("dictation")):
            tools.append(hands.DICTATE_TOOL)
        # Раскладка, музыка и звук компьютера — сразу за диктовкой, тоже
        # всегда и тем же набором: их не от чего включать, они не зависят ни от
        # apps.json, ни от настроек голоса. Порядок набора не меняется — на
        # нём держится кеш запроса.
        tools.extend(hands.PC_TOOLS)
        # Стандартные папки — сразу за раскладкой, музыкой и звуком и тоже
        # всегда: они есть у каждого, и «открывай загрузки» не должно уходить
        # в облако. Порядок набора не меняется — на нём держится кеш.
        tools.append(hands.FOLDER_TOOL)
        # Напоминания и таймеры — сразу за папками, тоже всегда и одним
        # блоком: «напомни через двадцать минут» не должно уходить в облако
        # на уточнение, а набор их не зависит ни от apps.json, ни от
        # настроек голоса. Порядок набора не меняется — на нём держится кеш.
        tools.extend(hands.REMINDER_TOOLS)
        # Документы — сразу за напоминаниями, тоже всегда и тем же набором:
        # «перескажи последний скачанный PDF» — длинная фраза, и уводить её в
        # облако на уточнение незачем, а набор их не зависит ни от apps.json, ни
        # от настроек голоса. Порядок набора не меняется — на нём держится кеш.
        tools.append(hands.DOC_TOOL)
        # Поиск и открытие файла по названию — сразу за документами, тоже всегда
        # и тем же набором: файлы у хозяина есть всегда, а «открой на диске ц
        # документ договор» — длинная фраза. Порядок набора не меняется — на нём
        # держится кеш запроса.
        tools.append(hands.FIND_TOOL)
        # Буфер обмена — сразу за поиском файла, тоже всегда и тем же набором:
        # буфер у хозяина есть всегда, а «прочитай, что я скопировал» и
        # «переведи скопированное» — длинные фразы, и незачем уводить их в
        # облако на уточнение. Порядок набора не меняется — на нём держится
        # кеш запроса.
        tools.append(hands.CLIP_TOOL)
        # Питание компьютера — сразу за буфером обмена, тоже всегда и тем же
        # набором: «выключи компьютер» у хозяина случается редко, но случается,
        # а уводить такую фразу в облако на уточнение незачем. Порядок набора
        # не меняется — на нём держится кеш запроса.
        tools.append(hands.POWER_TOOL)
        tools.extend(hands.action_tools(getattr(self, "actions", None) or {}))
        if self._tools_on() and (search or wants_web(text) or follow_up):
            tools.extend(web.TOOLS)
        # И не на длинную речь: 26 сентября «…ты всё равно как-то говоришь
        # очень медленно… хотелось чуть-чуть увеличить темп» зацепилось за
        # «всё», и она ответила «Ок, молчу». Прощаются коротко.
        if aloud and "?" not in text and BYE.search(text) and len(text.split()) <= BYE_WORDS:
            tools.append(END_TOOL)
        # И последним: условные инструменты — в конце набора, чтобы начало
        # и кеш запроса от них не зависели (см. END_TOOL выше).
        if named:
            tools.append(NOT_TO_ME_TOOL)
        return tools

    def _run_pc(self, call: dict) -> str:
        """Раскладка, музыка или звук компьютера по вызову модели.

        Отдельным методом, а не строкой в круге: у всех трёх один и тот же
        хвост — звать `core/pc_control.py` и положить одну понятную строку в
        журнал («раскладка: английская», «музыка: пауза», «звук компьютера:
        30 %»). Само действие и его честный текст — внутри `pc_control`.
        """
        from core import pc_control

        import json

        answer = pc_control.run_tool(call["name"], call["args"])
        try:
            строка = str(json.loads(answer).get("journal") or "")
        except (TypeError, ValueError):
            строка = ""
        if строка:
            self._tell("pc", строка)
        return answer

    def _searched_just_now(self) -> bool:
        """Прошлый ответ — по поиску, и был он недавно."""
        for turn in reversed(self._history):
            if turn.get("role") != "assistant":
                continue
            if not turn.get("searched"):
                return False
            try:
                at = datetime.fromisoformat(turn.get("at", ""))
            except ValueError:
                return False
            return (datetime.now() - at).total_seconds() < FOLLOW_UP_MINUTES * 60
        return False

    def _memory(self) -> str:
        """Что она помнит о нём. Читается каждый раз: правится из пульта."""
        from core import memory

        try:
            return memory.as_prompt()
        except Exception:
            return ""

    def reply(
        self,
        user_text: str,
        context: str | None = None,
        image: str | None = None,
        voice: float | None = None,
        aloud: bool = False,
        can_end: bool = True,
        search: bool = False,
        first: str | None = None,
        named: bool = False,
    ) -> Iterator[str]:
        """Один разговор для голоса и чата: не смешиваем одновременные ответы.

        aloud — ответ голосом: тогда модели доступно «закончить разговор»,
        и после ответа `ended` говорит, попросила ли она об этом.
        can_end=False — закрыть нельзя: его позвали по имени или разговор
        только начинается, на такое надо ответить.
        search — фраза пришла с кнопки поиска на телефоне: интернет отдаётся
        независимо от её слов, в промпт добавляется SEARCH_ASK, в этом ходу
        доступен только интернет, а первый круг идёт с требованием вызвать
        инструмент (`tool_choice`).

        named — фраза прошла в ворота имени: разговор до неё не был открыт,
        и имя в ней — единственная причина, по которой она услышана. Тогда
        модели добавляется `not_to_me`: имя прозвучало, а говорили не с ней —
        она зовёт инструмент и молчит (см. NOT_TO_ME_TOOL). Смысл решает
        модель, шаблонов фраз тут нет. На ходах с кнопкой поиска и на заходе
        первой инструмента нет: там человек сказал, чего хочет, и молчать
        незачем.

        first — заход первой (core/proactive.py). Тогда `user_text` — не его
        реплика, а подсказка модели; в историю вместо неё ложится пометка
        с пометкой `first`, снимок в историю не идёт, а инструментов нет
        вовсе: она заговорила сама, и «запусти мне Discord» из тишины
        звучало бы пугающе.

        voice — насколько голос фразы похож на хозяина (core/speaker.py);
        None — чат или фраза слишком короткая, чтобы судить. Сходство
        ложится в историю, и чужое не попадает в память (см. digest).
        """
        with self._reply_lock:
            yield from self._reply_unlocked(
                user_text, context, image, voice, aloud, can_end, search, first,
                named,
            )

    def _reply_unlocked(
        self,
        user_text: str,
        context: str | None = None,
        image: str | None = None,
        voice: float | None = None,
        aloud: bool = False,
        can_end: bool = True,
        search: bool = False,
        first: str | None = None,
        named: bool = False,
    ) -> Iterator[str]:
        """Отдаёт ответ кусками по предложениям, пригодными для синтеза.

        `image` — снимок экрана в виде data-URL, когда её попросили
        посмотреть. В историю он не попадает: картинки дорого тащить
        в каждый следующий запрос, а разговор о них идёт уже словами.

        Интернет (core/web.py): вместо ответа модель может попросить поиск.
        Выполняем, возвращаем результат и спрашиваем снова — не больше
        TOOL_ROUNDS раз, последний раз без права искать. Пока ищем, человек
        не должен сидеть в тишине: если она сама ничего не сказала, звучит
        короткое «секунду, гляну». В историю идёт только сказанное вслух —
        найденное не копится в каждом следующем запросе.

        Запуск программы и действия на компе (core/hands.py) — тот же цикл:
        модель зовёт `launch_app`, `take_screenshot`, `save_moment` или
        `look_at_screen`, мы выполняем и отвечаем текстом, что вышло. На
        «запусти, пожалуйста, YouTube» и на «а ты можешь сделать скриншот?»
        она обязана звать инструмент, иначе врёт, что открыла или сняла.
        `look_at_screen` вдобавок отдаёт ей снимок, чтобы она ответила по
        увиденному.
        """
        self._maybe_home()
        self.ended = False
        self.not_to_me = False
        self.last_sources = []
        # Только про этот ответ: телефон показывает карточку поиска по тем
        # запросам и источникам, что были в нём, а не в прошлом.
        self.searched = False
        self.last_queries = []
        # Быстрый поиск (29.09, хозяин: «YouTube она открывает сразу, а поиск
        # долго думает»). Раньше первый поход к модели нужен был только
        # затем, чтобы она сформулировала запрос, — а запрос и так известен:
        # «найди в интернете <что>» или фраза с кнопки «Найти». Ищем сами,
        # пока звучит «секунду, гляну», и отдаём найденное вместе с репликой:
        # ответ — одним кругом. Поисковики не ответили — тоже одним кругом:
        # она честно говорит, что интернет молчит (см. `_attach_found`).
        # Прежний путь через инструмент остался для поиска со снимком.
        found = None
        if search and first is None and image is None and self._tools_on():
            found = yield from self._search_first(user_text)
        fast = found is not None
        messages = self._messages(user_text, context, image, aloud, search and not fast)
        if fast:
            _attach_found(messages, found["query"], found["result"], found["ok"])
        # Заход первой — без инструментов вовсе. Пустой список в тело
        # запроса не попадает: ниже `elif tools:` проверяет на
        # непустоту, и провайдер получает запрос вообще без `tools`.
        # На ходе с кнопки поиска набор — только интернет, а на заходе первой
        # инструментов нет вовсе: там `not_to_me` нечего решать. Быстрый
        # поиск уже сделан — отвечать по нему, без новых кругов.
        tools = [] if first is not None or fast else self._tool_list(
            aloud and can_end, user_text, search, named and not search
        )
        # Источники — только из этого ответа (сброшены выше, до быстрого
        # поиска). Кнопка поиска показывает их телефону, и старые из прошлого
        # запроса показывать нельзя.
        # Замеры времени — тоже только про этот ответ: прошлые круги в записи
        # о задержке только сбивали бы с толку.
        self.last_timing = {"rounds": []}
        # Расход этого ответа по всем кругам. Суммируется здесь, в журнал
        # уходит одним событием в конце — в том числе когда разговор
        # молча закрылся и до `_add_turn` не дошло.
        usage: dict = {}
        # Страховка — на этот ответ: чужой провайдер из прошлого ответа
        # не должен влиять на новый.
        self._hedge_spare = None
        self._hedge_model = None
        # Ход с кнопки поиска: первый круг идёт с `tool_choice`, чтобы модель
        # не ответила болтовнёй, не поискав. Снимаем в `finally` — обычный
        # ответ `tool_choice` не должен получать никогда (см. `_open_stream_here`).
        self._must_search = bool(search) and self._tools_on() and not fast

        try:
            yield from self._reply_rounds(
                messages, tools, user_text, voice, usage, first,
                found=found["query"] if fast and found["ok"] else None,
            )
        finally:
            self._must_search = False
            if usage.get("calls"):
                # `model` — тот, кто реально ответил: со страховкой это
                # может быть запасной, а расход всё равно честный суммой
                # обоих запросов (см. `_sentences`).
                self._tell("tokens", {**usage,
                                      "model": self._hedge_model or self.model})

    def _search_first(self, user_text: str):
        """Быстрый поиск до первого круга к модели.

        Генератор: пока поиск идёт отдельным потоком, отдаёт «секунду,
        гляну» — человек не сидит в тишине. Возвращает
        `{"ok", "query", "result"}`; `ok=False` — поиск не удался, и ответ
        пойдёт прежним путём (модель с инструментом).
        Запрос — из команды («найди в интернете <что>»), а фраза с кнопки
        «Найти» — сама и есть запрос.
        """
        import json

        from core import commands, web

        order = commands.understand(user_text, [])
        query = (order.target if order is not None and order.action == "search"
                 and order.target else user_text)
        query = " ".join(str(query or "").split())[:200]
        if not query:
            return {"ok": False, "query": "", "result": ""}
        box: dict = {}

        def run() -> None:
            try:
                box["queries"] = self._search_queries(user_text, query)
                # Сразу с текстами верхних страниц: по обрывкам из выдачи
                # на вопрос вроде «топ-5 нейросетей» ответить нечем.
                box["result"] = web.run_tool(
                    "web_search",
                    json.dumps({"queries": box["queries"]}, ensure_ascii=False),
                    self.on_event, read_pages=web.READ_TOP)
            except Exception as exc:
                box["result"] = json.dumps(
                    {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

        поиск = threading.Thread(target=run, daemon=True, name="fast-search")
        поиск.start()
        if self.on_event is not None:
            try:
                self.on_event("web_start", {"after": 0.0})
            except Exception:
                pass
        yield random.choice(FILLERS)
        поиск.join(timeout=_search_wait())
        result = str(box.get("result") or '{"error": "поиск не ответил"}')
        ok = not result.startswith('{"error"')
        if ok:
            _collect_sources("web_search", result, self.last_sources)
        # Запросы, что ушли в поисковик: карточка на телефоне откроет именно
        # их. Список пустой, когда поисковики не ответили, — тогда телефон
        # покажет слова хозяина (см. `VoiceLoop._send_search`).
        if ok:
            self.searched = True
            self.last_queries = [str(q) for q in (box.get("queries") or []) if str(q).strip()]
        return {"ok": ok, "query": "; ".join(box.get("queries") or [query]),
                "result": result}

    def _search_queries(self, user_text: str, query: str) -> list[str]:
        """Вопрос человека — в запросы для поисковика (REWRITE_ASK).

        Не ответила за REWRITE_TIMEOUT или ответила криво — ищем по фразе
        как есть. Платный поиск запрос составляет сам — там не переписываем.
        Последние реплики — чтобы «а найди про него подробнее» стало
        запросом про то, о чём говорили.
        """
        import json

        from core import web

        if getattr(config, "WEB_SEARCH_MODE", "free") == "paid":
            return [query]
        recent = [t for t in list(self._history)[-4:] if t.get("role") in ("user", "assistant")]
        talk = "\n".join(
            ("Человек: " if t["role"] == "user" else "Ты: ") + str(t.get("content") or "")[:300]
            for t in recent
        )
        ask = REWRITE_ASK.format(today=datetime.now().strftime("%d.%m.%Y"),
                                 talk=talk or "(его не было)", text=user_text,
                                 n=web.MAX_QUERIES)
        started = time.monotonic()
        box: dict = {}

        def rewrite() -> None:
            try:
                box["answer"] = self._ask_plainly(ask, REWRITE_TOKENS, json_mode=True,
                                                  note="поиск", timeout=REWRITE_TIMEOUT)
            except Exception as exc:
                box["error"] = exc

        # Срок — снаружи, потоком. `timeout` запроса — это срок между байтами,
        # а облако, пока думает, шлёт пустые байты, чтобы связь не рвалась:
        # 29.09 такой запрос висел больше 20 с при сроке в 4.
        переписка = threading.Thread(target=rewrite, daemon=True, name="search-queries")
        переписка.start()
        переписка.join(REWRITE_TIMEOUT)
        try:
            if "error" in box:
                raise box["error"]
            if "answer" not in box:
                raise TimeoutError(f"не ответила за {REWRITE_TIMEOUT:.0f} с")
            text = (box["answer"].choices[0].message.content or "").strip()
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            queries = [" ".join(str(q).split()) for q in data.get("queries") or []
                       if isinstance(q, str) and q.strip()][:web.MAX_QUERIES]
        except Exception as exc:
            self._tell("web_queries", {"queries": [query], "took": round(time.monotonic() - started, 2),
                                       "error": f"{type(exc).__name__}: {exc}"})
            return [query]
        self._tell("web_queries", {"queries": queries or [query],
                                   "took": round(time.monotonic() - started, 2)})
        return queries or [query]

    def _reply_rounds(
        self,
        messages: list[dict],
        tools: list[dict],
        user_text: str,
        voice: float | None,
        usage: dict,
        first: str | None = None,
        found: str | None = None,
    ) -> Iterator[str]:
        """Круги инструментов и выдача ответа. Расход копится в `usage`.

        `found` — запрос быстрого поиска, уже сделанного до первого круга:
        он идёт в пометку истории, как если бы искала сама модель.
        """
        from core import hands, web

        # Все поисковики разом отказали — повторять через секунду бесполезно.
        # 25 сентября на этом она молчала по 20 с: искала, ждала отказа,
        # искала снова. Отвечает тем, что есть.
        spoken = ""
        searched = found is not None
        queries: list[str] = [found] if found else []
        search_started = None
        dead = False
        asked_at = time.monotonic()

        for round_no in range(TOOL_ROUNDS + 1):
            budget = float(getattr(config, "WEB_SEARCH_BUDGET", TOOL_BUDGET))
            late = dead or (
                search_started is not None and time.monotonic() - search_started > budget
            )
            # Замер этого круга: когда ушли к модели, когда пришло первое
            # слово и когда первое предложение стало готово. Три числа,
            # больше ничего — на горячем пути лишнего счёта быть не должно.
            mark = {"sent": time.perf_counter(), "word": 0.0, "sentence": 0.0}
            self.last_timing["rounds"].append(mark)
            stream, tools = self._open_stream(
                messages, tools, last=round_no == TOOL_ROUNDS or late
            )
            calls: dict[int, dict] = {}
            said: list[str] = []
            reasoning: list[str] = []
            yield from self._sentences(stream, calls, said, reasoning, usage, mark)
            text = " ".join(said).strip()
            if text:
                spoken = f"{spoken} {text}".strip()
            if any(c["name"] == NOT_TO_ME_NAME for c in calls.values()):
                # Имя прозвучало, а говорили не с ней: молчим и не тратим
                # круг. Если она всё же что-то сказала — это прозвучало, и
                # голосовой цикл отработает как обычно.
                self.not_to_me = True
                break
            if any(c["name"] == END_NAME for c in calls.values()):
                # Разговор окончен: дальше не спрашиваем и не ищем.
                self.ended = True
                break
            if not calls or late:
                break
            ordered = [calls[i] for i in sorted(calls)]
            # Филлер, отметка в журнале и счётчик поиска — только про интернет.
            # Действия не ждут: они делаются мгновенно, и в историю они
            # попадают не как найденное, а как сделанное.
            if any(call["name"] not in hands.LOCAL for call in ordered):
                if not searched and self.on_event is not None:
                    try:
                        self.on_event("web_start",
                                      {"after": round(time.monotonic() - asked_at, 2)})
                    except Exception:
                        pass

                if not searched and not spoken:
                    yield random.choice(FILLERS)
                if search_started is None:
                    search_started = time.monotonic()
                searched = True
            assistant = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["args"]}}
                    for c in ordered
                ],
            }
            # DeepSeek в режиме размышлений требует вернуть их вместе с
            # вызовом, иначе отвечает 400. Остальные поле пропускают мимо.
            if reasoning:
                assistant["reasoning_content"] = "".join(reasoning)
            messages.append(assistant)
            results = []
            web_failed = []
            # Снимки — после всех ответов инструментов: OpenAI не принимает
            # сообщение пользователя между ответами на вызовы одного круга.
            shots: list[str] = []
            for call in ordered:
                # Вызовы разводим по имени: интернет и действия — разные
                # инструменты, и ни один не знает про другой.
                shot = ""
                if call["name"] in hands.GUARDED and not hands.asked_for(
                    call["name"], user_text,
                    because=str(_args_of(call).get("because") or ""),
                ):
                    # Повтор прошлой просьбы: модель не смогла процитировать
                    # слова хозяина из его последней реплики — значит, в этой
                    # фразе об этом не просили.
                    results.append(hands.not_asked(call["name"]))
                elif call["name"] == hands.YT_NAME and not hands.names_youtube(
                        user_text):
                    # Правило хозяина: YouTube — только когда он сказал
                    # «ютуб». Облако не спрашиваем: слова нет — и так ясно.
                    results.append(hands.youtube_not_named())
                elif call["name"] in hands.JUDGED and (
                        judged := self._really_asked(
                            call["name"], _args_of(call), user_text)) is not True:
                    # Цитата в реплике есть, а просьбы в ней нет: «Они в
                    # Discord'е разговаривать будут» — это не «открой Discord».
                    # Облако не ответило (judged None) — тоже не делаем:
                    # неизвестно, просил ли, а действовать вслепую нельзя.
                    results.append(
                        hands.not_really_asked(call["name"]) if judged is False
                        else hands.check_failed(call["name"]))
                elif call["name"] == hands.NAME:
                    results.append(hands.run_tool(call["args"], self.on_event))
                elif call["name"] == hands.CLOSE_NAME:
                    results.append(hands.run_close(call["args"], self.on_event))
                elif call["name"] == hands.YT_NAME:
                    results.append(hands.run_youtube(call["args"], self.on_event))
                elif call["name"] == hands.NOTE_NAME:
                    results.append(hands.run_note(call["args"], self.on_event))
                elif call["name"] == hands.READ_NAME:
                    results.append(hands.run_read_notes(call["args"], self.on_event))
                elif call["name"] == hands.DICTATE_NAME:
                    results.append(hands.run_dictation(
                        call["args"], getattr(self, "actions", None) or {}))
                elif call["name"] == hands.FOLDER_NAME:
                    # Стандартная папка. Ответ — тот же словарь, что у голосовой
                    # команды, и строка в журнал из него же: хозяин видит одно
                    # и то же в обоих путях («папка: Загрузки»).
                    results.append(hands.run_folder(call["args"], self.on_event))
                elif call["name"] in hands.PC_NAMES:
                    # Раскладка, музыка и звук компьютера. Ответ — тот же
                    # словарь, что у голосовой команды, и строка в журнал из
                    # него же: хозяин должен видеть одно и то же в обоих
                    # путях («раскладка: английская», «звук компьютера: 30 %»).
                    results.append(self._run_pc(call))
                elif call["name"] == hands.SET_REM_NAME:
                    # Напоминание или таймер. Время к этому моменту уже
                    # посчитано моделью (у неё часы в каждом запросе) и
                    # пришло готовым — здесь только храним и будим.
                    results.append(
                        hands.run_set_reminder(call["args"], self.on_event))
                elif call["name"] == hands.LIST_REM_NAME:
                    # Список того, что стоит. В `LOCAL` его нет намеренно:
                    # модель перескажет хозяину своими словами.
                    results.append(
                        hands.run_list_reminders(call["args"], self.on_event))
                elif call["name"] == hands.CANCEL_REM_NAME:
                    results.append(
                        hands.run_cancel_reminder(call["args"], self.on_event))
                elif call["name"] == hands.DOC_NAME:
                    # Документ. Ответ — тот же словарь, что у голосовой команды,
                    # и строка в журнал из `core/documents.py`: имя, страницы и
                    # знаки. Сам текст в журнал не уходит и в историю тоже — он
                    # только в этом ответе, оттуда модель перескажет хозяину.
                    # При `mode: aloud` читает голосовой цикл (действие
                    # `read_aloud`), и текст в ответе уже пустой: наружу уходит
                    # только готовая фраза, второй круг ей не нужен.
                    results.append(hands.run_document(
                        call["args"], self.on_event,
                        getattr(self, "actions", None) or {},
                    ))
                elif call["name"] == hands.FIND_NAME:
                    # Поиск файла по названию. Ответ — имена, пути и папки (путь
                    # без имени пользователя), а при `open` — ещё и готовая
                    # фраза; строки в журнал из `core/files.py`.
                    results.append(hands.run_find_file(
                        call["args"], self.on_event))
                elif call["name"] == hands.CLIP_NAME:
                    # Буфер обмена. При `mode: read` текст уходит в синтез на
                    # компьютере (действие `read_aloud`), и в ответе его уже
                    # нет: наружу уходит только готовая фраза, второй круг ей
                    # не нужен. При `translate` модель получает сам текст с
                    # пометкой «это данные» — второй круг обязателен, и он
                    # уйдёт в облако, поэтому инструмент ещё и в `JUDGED`.
                    results.append(hands.run_clipboard(
                        call["args"], self.on_event,
                        getattr(self, "actions", None) or {},
                    ))
                elif call["name"] == hands.POWER_NAME:
                    # Питание компьютера. Инструмент только спрашивает вслух
                    # и ставит локальное ожидание (`core/power.ask`): выключает
                    # компьютер голосовой цикл, когда хозяин подтвердит словами
                    # на следующем ходу. Отсюда в облако уходит только сам
                    # вопрос — и никаких прав на подтверждение модель не
                    # получает: `GUARDED` (цитата `because`) и `JUDGED`
                    # (судья) отработали выше по той же ветке.
                    results.append(hands.run_power(call["args"], self.on_event))
                elif call["name"] in hands.KEY_OF:
                    results.append(
                        hands.run_action(
                            call["name"], getattr(self, "actions", None) or {},
                        )
                    )
                    if call["name"] == hands.LOOK_NAME and not results[-1].startswith(
                        '{"error"'
                    ):
                        # Действие вернуло сам снимок, а не путь к нему.
                        shot = results[-1]
                else:
                    results.append(web.run_tool(call["name"], call["args"], self.on_event))
                    if results[-1].startswith('{"error"'):
                        web_failed.append(call["id"])
                    else:
                        # Источники — из того, что правда пришло. Подменять их
                        # выдумкой нельзя: телефон покажет их хозяину.
                        _collect_sources(call["name"], results[-1], self.last_sources)
                if not results[-1].startswith('{"error"'):
                    # В пометку истории — только то, что правда нашлось.
                    queries.append(_query_of(call))
                    if call["name"] == "web_search":
                        # Поход в интернет состоялся, и телефон должен знать
                        # какой: карточка показывает запрос, по которому
                        # хозяин откроет этот поиск в браузере (30.09).
                        self.searched = True
                        запрос = _query_of(call)
                        if запрос:
                            self.last_queries.append(запрос)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": "снимок ниже" if shot else results[-1],
                })
                if shot:
                    shots.append(shot)
            for shot in shots:
                # Сообщение role: tool картинку не несёт, поэтому снимок
                # для модели кладём отдельным сообщением пользователя — в
                # том же виде, что и картинка из voice_loop. В историю она
                # не идёт, как и раньше.
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Вот снимок экрана."},
                        {"type": "image_url", "image_url": {"url": shot}},
                    ],
                })
            # Короткий путь: в круге были только действия на компе, и все
            # удались. Второй круг в облако тогда уходит ради одного слова
            # «открыла» — это +2…5 с на пустом месте (замер 26 сентября).
            # Говорим подтверждение сами и заканчиваем ответ; в историю оно
            # идёт вместе со сказанным, чтобы потом помнила, что открыла.
            # Взгляд на экран сюда не попадает: там модель нужен снимок, и
            # интернет тоже — там нужен живой ответ из сети.
            if (not searched and not shots and not late
                    and all(hands.confirm_forms(call) for call in ordered)
                    and not any(r.startswith('{"error"') for r in results)):
                # Формы — по `confirm_forms`, а не по имени: у `read_document`
                # при `mode: aloud` второй круг не нужен (текст документа в
                # облако не уходит, а модель получила готовую фразу), а при
                # `retell` — нужен, и без него она не перескажет.
                line = hands.confirm(ordered, results=results)
                if line:
                    spoken = f"{spoken} {line}".strip()
                    yield line
                    break
            # Провалившийся запуск — не приговор: программы бывают и не те,
            # и модель вправе попробовать другую. Глушим цикл только когда
            # не вышло ни одного поиска — тогда искать больше нечем.
            dead = bool(web_failed) and len(web_failed) == len(ordered) - sum(
                1 for c in ordered if c["name"] in hands.LOCAL)

        if searched and not spoken:
            spoken = "Не вышло ничего толком найти, спроси по-другому."
            yield spoken

        if self.not_to_me and not spoken:
            # Говорили не с ней, а она промолчала — в историю это не пишем и
            # для памяти не считаем: 28 сентября в 23:22 она так записала в
            # память слова хозяина о его же программе («у хозяина видеокарта
            # жрёт 3 ГБ»). Сказала бы что-то вслух — пишем как обычно,
            # сказанное не прячем.
            return
        if self.ended and not spoken:
            # Молчаливое закрытие в историю не пишем: 25 сентября строки
            # «(молча закончила разговор)» подряд приучили модель молчать и на
            # «Труба, привет». Реплику всё равно считаем для памяти.
            return
        # Заход первой: в историю кладётся не подсказка модели, а пометка
        # о молчании. Иначе в следующем запросе она читала бы свой же
        # текст как реплику хозяина и закапывалась в собственные советы.
        if first is not None:
            self._add_turn(first, spoken, first_turn=True)
            return
        self._add_turn(user_text, spoken, voice, [q for q in queries if q])

    def _open_stream(self, messages: list[dict], tools: list[dict], last: bool):
        """Поток ответа. Возвращает (поток, остались ли инструменты).

        Отказы разбираем по лесенке, чтобы интернет не ронял разговор:
        OpenAI в Chat Completions пускает инструменты только без
        размышлений (reasoning_effort=none) — это заодно и быстрее: первое
        слово через 1.0 с вместо 1.6, замерено 25 сентября. Модель, которая
        такого параметра не знает, спрашиваем без него. Провайдер, который
        не принимает инструменты вовсе, запоминаем и дальше без них.

        Пока история без вызовов инструментов, отвечаем со страховкой
        (см. `_hedge`): облако умеет молчать по 6 с, и второй запрос того
        же вопроса запасному стоит копейки, а молчание — это секунды тишины.
        """
        from openai import APIConnectionError, PermissionDeniedError

        if self._hedge_spare and any(m.get("role") == "tool" for m in messages):
            # Ответила страховка: её вызовы инструментов основной провайдер
            # может не принять. Отдаём найденное текстом, как при отказе.
            self._flatten_tool_rounds(messages)
            self._hedge_spare = None

        hedge = self._hedge(messages, tools, last)
        if hedge is not None:
            return hedge, tools

        try:
            return self._open_stream_here(messages, tools, last)
        except (PermissionDeniedError, APIConnectionError) as exc:
            if not self._fall_back(exc):
                raise
            self._flatten_tool_rounds(messages)
            return self._open_stream_here(messages, tools, last)

    def _hedge(self, messages: list[dict], tools: list[dict], last: bool):
        """Страховка от заминок облака. None — страховки нет.

        Страховка только в первом круге: дальше история с вызовами
        инструментов собрана под конкретного провайдера, и отдать её
        запасному со своими особенностями нельзя. Запасного нет — тоже
        None, ждём основного как раньше.
        """
        if last or not bool(getattr(config, "HEDGE", False)):
            return None
        if any(m.get("role") == "tool" for m in messages):
            return None
        # Основной — локальный сервер: страховки нет вовсе. Он почти всегда
        # думает дольше HEDGE_AFTER, и тот же вопрос каждый раз уходил бы
        # параллельно в платное облако — хозяин выбрал локальную, чтобы не
        # платить. Обрыв связи с ним по-прежнему ловит `_fall_back`.
        from core import settings

        if settings.is_local(self.provider):
            return None
        spare = self._spare()
        if spare is None:
            return None

        was = self.provider
        after = float(getattr(config, "HEDGE_AFTER", HEDGE_AFTER))

        def on_error(who, exc, hedge):
            """Основной отказал — как раньше: уходим на запасного целиком."""
            if not self._fall_back(exc):
                return False
            self._flatten_tool_rounds(messages)
            hedge.retry(lambda: self._open_stream_here(messages, tools, last)[0])
            return True

        def on_winner(who, fired):
            # Срабатывание страховки — в журнал: хозяин должен видеть, как
            # часто это бывает. Победитель — в подписи, а `model` события
            # `tokens` тоже должен называть того, кто реально ответил.
            if who == "spare":
                self._hedge_spare = spare
                self._hedge_model = f"{spare}/{config.PROVIDERS[spare]['model']}"
            if fired:
                self._tell("hedge", {"from": was, "to": spare,
                                     "after": after, "winner": who})

        hedge = _Hedge(
            lambda: self._open_stream_here(messages, tools, last)[0],
            lambda: self._spare_stream(spare, messages, tools),
            after, on_error, on_winner,
        )
        return hedge

    def _spare_stream(self, spare: str, messages: list[dict], tools: list[dict]):
        """Тот же вопрос запасному провайдеру — с его параметрами.

        Тело собирается заново и по правилам запасного: у него свой
        потолок токенов (`completion_limits`) и свой `reasoning_effort`.
        Что провайдер понимает, запоминаем только ему — основной об этом
        знать не должен.
        """
        from openai import BadRequestError

        spec = config.PROVIDERS[spare]
        client = _client(spare)
        body = {
            "model": spec["model"],
            "messages": messages,
            **completion_limits(spare, config.MAX_TOKENS, config.TEMPERATURE),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            if spare == "openai":
                body["reasoning_effort"] = "none"
            # Ход с кнопки поиска: первый круг обязан искать, а не болтать.
            if self._wants_tool_choice(messages):
                body["tool_choice"] = "required"
        for _ in range(4):
            try:
                return client.chat.completions.create(**body)
            except BadRequestError as exc:
                message = str(exc).lower()
                if "stream_options" in body and (
                    "stream_options" in message or "include_usage" in message
                ):
                    body.pop("stream_options")
                    continue
                if "reasoning_effort" in body and "reasoning" in message:
                    body.pop("reasoning_effort")
                    continue
                # Параметр провайдер не знает: убираем его и повторяем.
                # Инструменты остаются: следующая ветка иначе выключила бы
                # их совсем, и поиска не стало бы никогда.
                if "tool_choice" in body and (
                    "tool_choice" in message or "required" in message
                ):
                    body.pop("tool_choice")
                    continue
                if "tools" in body and ("tool" in message or "function" in message):
                    body.pop("tools", None)
                    body.pop("reasoning_effort", None)
                    continue
                raise
        return client.chat.completions.create(**body)

    def _open_stream_here(self, messages: list[dict], tools: list[dict], last: bool):
        from openai import BadRequestError

        body = {
            "model": self._model,
            "messages": messages,
            **completion_limits(self.provider, config.MAX_TOKENS, config.TEMPERATURE),
            "stream": True,
        }
        if self._usage_ok is not False:
            # Без этого в потоке нет расхода: usage приходит отдельным куском
            # с пустым choices в самом конце.
            body["stream_options"] = {"include_usage": True}
        if tools and last:
            # Последний раз — без инструментов и с прямой просьбой. Просто
            # запретить вызов (tool_choice=none) мало: DeepSeek 25 сентября
            # в ответ на это выдал свою разметку вызова обычным текстом,
            # и она ушла бы в озвучку.
            from core import web

            # Смысл этой подсказки — «хватит искать», и искать можно только
            # интернетом. Поэтому смотрим на инструменты сети, а не на «не из
            # hands.LOCAL»: `read_notes` в LOCAL не входит намеренно (модели
            # нужен второй круг, чтобы пересказать прочитанное), и по старой
            # проверке на «запиши это в заметки» она бы сказала «хватит искать».
            searching = any(t in web.TOOLS for t in tools)
            if searching:
                last_words = ("Хватит искать. Ответь сейчас тем, что уже нашла; "
                              "чего не нашла — так и скажи, без выдумок.")
            else:
                # В круге только действия на компе: искать тут нечего, а вот
                # «открыла» без ответа инструмента прозвучало бы враньём.
                last_words = ("Всё, хватит пробовать. Ответь сейчас: что удалось, "
                              "а что нет. О том, что получилось, говори уверенно; "
                              "о том, что не вышло, — честно, и ничем не "
                              "оправдывайся.")
            body["messages"] = messages + [{
                "role": "system",
                "content": last_words,
            }]
        elif tools:
            body["tools"] = tools
            if self.provider == "openai":
                body["reasoning_effort"] = "none"
            # Ход с кнопки поиска: первый круг обязан искать, а не болтать
            # (28.09, 21:25 — ответила болтовнёй, ни разу не поискав).
            if self._wants_tool_choice(messages):
                body["tool_choice"] = "required"

        # Попыток на столько отказов, сколько их бывает: stream_options,
        # reasoning_effort, инструменты — плюс последняя, уже чистая.
        for _ in range(4):
            try:
                return self._client.chat.completions.create(**body), tools
            except BadRequestError as exc:
                message = str(exc).lower()
                if "stream_options" in body and (
                    "stream_options" in message or "include_usage" in message
                ):
                    # Провайдер расход в потоке не отдаёт — идём без него и
                    # запоминаем, чтобы больше не пробовать.
                    body.pop("stream_options")
                    self._usage_ok = False
                    continue
                if "reasoning_effort" in body and "reasoning" in message:
                    body.pop("reasoning_effort")
                    continue
                # Параметр провайдер не знает: убираем его и повторяем, а
                # инструменты оставляем — иначе следующая ветка выключила бы
                # их совсем и поиска не стало бы никогда.
                if "tool_choice" in body and (
                    "tool_choice" in message or "required" in message
                ):
                    body.pop("tool_choice")
                    self._choice_ok = False
                    continue
                if tools and ("tool" in message or "function" in message) and not any(
                    m.get("role") == "tool" for m in messages
                ):
                    self._tools_ok = False
                    tools = []
                    for key in ("tools", "tool_choice", "reasoning_effort"):
                        body.pop(key, None)
                    continue
                raise
        return self._client.chat.completions.create(**body), tools

    @staticmethod
    def _sentences(
        stream, calls: dict, said: list, reasoning: list, usage: dict,
        mark: dict | None = None,
    ) -> Iterator[str]:
        """Режет поток на предложения; попутно собирает вызовы инструментов.

        В `said` кладётся ровно то, что ушло наружу, — оно и попадёт в историю.

        В `usage` копится расход этого круга. Последний кусок с расходом идёт
        с пустым `choices` — его надо разобрать до проверки на `choices`,
        иначе число пропадёт молча.

        `mark` — словарь замера круга: в него кладётся момент первого куска
        текста от модели и момент первого готового предложения. Пишем только
        когда словарь дан, иначе лишних вычислений на горячем пути.

        Расход у провайдера на весь круг, а не на кусок, поэтому повторный
        кусок с usage заменяет уже учтённый, а не складывается с ним. Круги
        инструментов при этом суммируются: у каждого своя `committed`.
        """
        buffer = ""
        leaked = False
        counted = False
        committed: dict = {}

        for chunk in stream:
            spent = getattr(chunk, "usage", None)
            if spent is not None:
                # Круг считается один, сколько бы кусков с usage ни пришло.
                if not counted:
                    usage["calls"] = usage.get("calls", 0) + 1
                    counted = True
                for key, value in read_usage(spent).items():
                    usage[key] = usage.get(key, 0) + value - committed.get(key, 0)
                    committed[key] = value
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            thought = getattr(delta, "reasoning_content", None)
            if thought:
                reasoning.append(thought)
            for call in getattr(delta, "tool_calls", None) or []:
                slot = calls.setdefault(call.index, {"id": "", "name": "", "args": ""})
                if call.id:
                    slot["id"] = call.id
                if call.function is not None:
                    if call.function.name:
                        slot["name"] += call.function.name
                    if call.function.arguments:
                        slot["args"] += call.function.arguments

            piece = delta.content
            if not piece or leaked:
                continue
            if mark is not None and not mark["word"]:
                # Первое слово модели: дальше ждать уже нечего.
                mark["word"] = time.perf_counter()

            # Служебная разметка вызова инструмента, выданная текстом, —
            # это не речь. Всё с неё и дальше выбрасываем.
            buffer += piece
            marker = next((m for m in TOOL_MARKUP if m in buffer), None)
            if marker is not None:
                buffer = buffer[: buffer.index(marker)]
                leaked = True

            # Отдаём всё, что уже стало законченным предложением.
            parts = SENTENCE_END.split(buffer)
            while len(parts) > 1:
                candidate = parts.pop(0).strip()
                # ESpeech звучит ровнее на фразах длиннее пары слов: каждый
                # слишком короткий кусок заново копирует тембр образца.
                minimum = 55 if config.TTS_ENGINE == "espeech" else MIN_CHUNK
                if len(candidate) >= minimum:
                    said.append(candidate)
                    if mark is not None and not mark["sentence"]:
                        # Первое готовое предложение — с ним уже можно
                        # начинать синтез, не дожидаясь конца ответа.
                        mark["sentence"] = time.perf_counter()
                    yield candidate
                    buffer = " ".join(parts)
                    parts = SENTENCE_END.split(buffer)
                else:
                    # Слишком короткий кусок склеиваем со следующим.
                    parts[0] = f"{candidate} {parts[0]}"
                    buffer = " ".join(parts)
                    break

        tail = buffer.strip()
        if tail:
            said.append(tail)
            if mark is not None and not mark["sentence"]:
                mark["sentence"] = time.perf_counter()
            yield tail

    def say_first(self, nudge: str, note: str, image: str | None = None,
                  context: str | None = None) -> Iterator[str]:
        """Проактивная реплика: модель заговаривает первой, без вопроса.

        `nudge` — подсказка, что именно сделать (core/proactive.py), в
        промпт идёт как его просьба. `note` — то, что ляжет в историю
        вместо его реплики. Снимок уходит в модель, но в историю не идёт.
        """
        return self.reply(
            nudge, context, image=image, aloud=True, first=note
        )

    def remember(self, user_text: str, reply: str) -> None:
        """Записывает в историю то, что случилось помимо неё.

        Команды вроде «сделай скриншот» выполняются на месте и в облако
        не уходят. Без этой записи она про них не знает вовсе: человек
        говорит «спасибо за скриншот», а она отвечает, что ничего не
        делала. Теперь сделанное попадает в тот же разговор.
        """
        with self._reply_lock:
            self._add_turn(user_text, reply)

    def announce(self, note: str, reply: str) -> None:
        """Её собственная реплика в историю — как заход первой.

        Так в историю ложится сработавшее напоминание (core/reminders.py):
        сказанного хозяином тут ничего, а без пометки она через пару реплик
        забыла бы, что напоминание вообще было. Пометка `first` — та же, что
        у проактивности: в разговор такая строка идёт как её собственная
        мысль, а не как реплика хозяина, и в память о нём (`own_turns`) не
        попадает.
        """
        with self._reply_lock:
            self._add_turn(str(note or ""), str(reply or ""), first_turn=True)

    def _add_turn(self, user_text: str, reply: str, voice: float | None = None,
                  searched: list[str] | None = None,
                  first_turn: bool = False) -> None:
        """Реплика человека и её ответ — в историю и на диск.

        searched — что искала для этого ответа. Без этой пометки 25 сентября
        она видела в истории свой курс или погоду без источника и либо
        лезла перепроверять на каждой реплике, либо каялась «выдумала».

        first_turn — заход первой: `user_text` тут не его реплика, а
        пометка, и в память о нём такой ход не идёт (см. own_turns).
        """
        now = datetime.now().isoformat(timespec="seconds")
        turn = {"role": "user", "content": user_text, "at": now}
        if first_turn:
            turn["first"] = True
        elif voice is not None:
            turn["voice"] = round(float(voice), 2)
        self._history.append(turn)
        answer = {"role": "assistant", "content": reply, "at": now}
        if searched:
            answer["searched"] = searched
        self._history.append(answer)
        self._undigested += 2
        self._trim_history()
        self._keep_history()

    def _trim_history(self) -> None:
        """Укорачивает историю пачкой, а не по одной реплике.

        deque(maxlen=...) выталкивал самую старую реплику на каждом ответе:
        начало запроса съезжало, и кеш префикса ловил только характер. Здесь
        при переполнении сразу отрезается кусок до HISTORY_TURNS - 8, так
        что начало стоит на месте следующие несколько ответов.

        Неразобранные реплики — последние, поэтому обрезка их не теряет:
        счётчик `_undigested` только ужимается до длины истории, и выжимка
        памяти остаётся в границах.
        """
        limit = int(getattr(config, "HISTORY_TURNS", 0) or 0)
        if limit <= 0:
            # 0 — «без истории», как было при deque(maxlen=0).
            self._history.clear()
            self._undigested = 0
            return
        if len(self._history) <= limit:
            return
        keep = max(4, limit - HISTORY_KEEP_BACK)
        for _ in range(len(self._history) - keep):
            self._history.popleft()
        # Неразобранное — это последние реплики, а режутся первые. Счётчик
        # ужимается только когда неразобранных больше, чем осталось в
        # истории: иначе выжимка уехала бы за её пределы.
        self._undigested = min(self._undigested, len(self._history))

    def forget(self) -> None:
        with self._reply_lock:
            self._history.clear()
            self._undigested = 0
            from core import memory

            memory.clear_history()

    # --- Просили ли на самом деле -------------------------------------------
    #
    # 27.09, 22:03. Хозяин, разговаривая с друзьями, сказал ей, что они будут
    # разговаривать в Discord'е, — назвал его как место. Модель позвала
    # `launch_app` с цитатой из этой фразы, и Discord запустился: `asked_for`
    # проверяет, что цитата
    # СКАЗАНА в этой реплике, а не что это ПРОСЬБА что-то сделать.
    # Упоминание программы ≠ просьба её открыть.
    #
    # Уговорами в описании инструмента («зови, только если просит») это не
    # лечится — она их и нарушает. Поэтому перед действием, которое что-то
    # открывает или закрывает на экране, та же модель отвечает на один
    # короткий вопрос: просили или нет. Без характера, без истории разговора,
    # одним словом. Проверено на живой модели: 30 из 30 верно, ~0.9 с.
    # Голосовые команды из core/commands.py сюда не ходят: «открой дискорд»
    # из таблицы хозяин сказал прямо, и мгновенно, как было.

    def _really_asked(self, name: str, args: dict, said: str) -> bool | None:
        """Просил ли хозяин это в этой фразе — по смыслу, а не по цитате.

        True — да, делай. False — нет, не делай. None — облако не ответило
        (проверку пропускать нельзя: неизвестно, просил ли, значит не делаем).
        """
        from core import hands

        action = hands.action_words(name, args)
        started = time.monotonic()
        try:
            answer = self._ask_plainly(
                hands.JUDGE_PROMPT.format(said=str(said or ""), action=action),
                max_tokens=5, note="проверка", timeout=8.0,
            )
            text = (answer.choices[0].message.content or "").strip()
        except Exception:
            text = ""  # облако молчало или упало — проверки нет
        took = round(time.monotonic() - started, 2)
        if not text:
            self._tell("action_check", {"action": action, "answer": "ошибка",
                                        "took": took})
            return None
        yes = hands.judge_says_yes(text)
        self._tell("action_check", {"action": action,
                                    "answer": "да" if yes else "нет",
                                    "took": took})
        return yes

    # --- Память -----------------------------------------------------------

    def _ask_plainly(self, prompt: str, max_tokens: int, json_mode: bool = False,
                     note: str = "память", timeout: float | None = None):
        """Разовый вопрос мимо разговора: без потока и без размышлений.

        Первый раз пробуем попросить не думать вслух. Не понял провайдер —
        запоминаем это и дальше спрашиваем обычным способом. OpenAI о том
        же просим своим параметром (reasoning_effort=none).

        json_mode — просим ответ строго JSON. Не принял провайдер — спросим
        без этого и разберём текст сами.

        note — чем помечен этот расход в журнале («память», «проверка»).
        timeout — сколько ждать облако; заданный уходит в сам запрос, чтобы
        проверка просьбы не висела, если облако молчит.
        """
        from openai import APIConnectionError, PermissionDeniedError

        self._maybe_home()
        try:
            answer = self._ask_plainly_here(prompt, max_tokens, json_mode, timeout)
        except (PermissionDeniedError, APIConnectionError) as exc:
            if not self._fall_back(exc):
                raise
            answer = self._ask_plainly_here(prompt, max_tokens, json_mode, timeout)
        # Выжимка памяти тоже стоит токенов, и хозяин спрашивал не только про
        # ответы, но и про весь расход. Отдельная пометка — в журнале видно,
        # что это не разговор.
        spent = getattr(answer, "usage", None)
        if spent is not None:
            self._tell("tokens", {**read_usage(spent), "calls": 1,
                                  "model": self.model, "note": note})
        return answer

    def _ask_plainly_here(self, prompt: str, max_tokens: int, json_mode: bool,
                          timeout: float | None = None):
        from openai import BadRequestError

        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            **completion_limits(self.provider, max_tokens, 0.2),
        }
        client = self._client
        if timeout is not None:
            # Свой срок и БЕЗ повторов: SDK openai по таймауту сам повторяет
            # запрос ещё дважды, и 27.09 проверка просьбы на холодном
            # соединении ждала 15 с вместо 5. Не успели — проверки нет, и
            # это решает вызывающий, а не молчаливый повтор.
            with_options = getattr(client, "with_options", None)
            if callable(with_options):
                client = with_options(timeout=timeout, max_retries=0)
            else:
                body["timeout"] = timeout
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.provider == "openai":
            body["reasoning_effort"] = "none"

        for _ in range(3):
            try:
                if self.provider == "deepseek" and self._quiet is not False:
                    try:
                        answer = client.chat.completions.create(
                            **body, extra_body=NO_THINKING
                        )
                        self._quiet = True
                        return answer
                    except BadRequestError as exc:
                        if "response_format" in str(exc) or "reasoning" in str(exc):
                            raise
                        self._quiet = False
                return client.chat.completions.create(**body)
            except BadRequestError as exc:
                message = str(exc)
                if "response_format" in body and "response_format" in message:
                    body.pop("response_format")
                    continue
                if "reasoning_effort" in body and "reasoning" in message:
                    body.pop("reasoning_effort")
                    continue
                raise
        return client.chat.completions.create(**body)

    def _keep_history(self) -> None:
        """Складывает разговор на диск. Молча: это не повод ломать ответ."""
        from core import memory

        try:
            memory.save_history(self._history)
        except Exception:
            pass

    def digest(self) -> dict:
        """Обновляет память по свежему куску разговора.

        Зовётся, когда разговор закончился. Отдельный дешёвый запрос без
        потока: ответ нужен целиком и вслух он не идёт. Модель видит всю
        память с номерами и возвращает правки — добавить, исправить,
        забыть (core/memory.py). Возвращает, что изменилось:
        {"added": [...], "updated": [[было, стало]], "removed": [...]},
        пустой словарь — если ничего.

        Пустой словарь значит две разные вещи, и callers это различают
        по `self.did_ask`: True — спросила и нового нет (26 сентября хозяин
        решил, что память сломалась, потому что молчание выглядело поломкой),
        False — не спрашивала, разговор был слишком короткий.
        """
        self.did_ask = False
        with self._reply_lock:
            count = min(self._undigested, len(self._history))
            fresh = list(self._history)[-count:] if count else []
            self._undigested = 0
        try:
            return self._digest(fresh)
        except Exception:
            # Облако не ответило — кусок не разобран: возвращаем его в очередь,
            # следующий разбор возьмёт его вместе с новым (29.09: раньше он
            # терялся насовсем). Не больше, чем есть в истории.
            with self._reply_lock:
                self._undigested = min(self._undigested + count, len(self._history))
            raise

    def _digest(self, fresh: list[dict]) -> dict:
        from core import memory

        fresh = own_turns(fresh, float(getattr(config, "OWNER_THRESHOLD", 0.0)))

        talk = "\n".join(
            f"{'Хозяин' if t['role'] == 'user' else 'Ты'}: {t['content']}"
            for t in fresh
            if isinstance(t.get("content"), str)
        )
        if len(talk) < 80:
            return {}

        # Снимок: по нему номера из ответа модели потом переводятся обратно
        # в факты — память за время запроса могли поправить (см. memory._renumber).
        snapshot = memory.load_facts()
        known = memory.numbered(snapshot)
        ask = (
            "Ниже кусок разговора человека с его голосовым помощником и то, "
            "что помощник уже помнит о человеке — с номерами строк.\n\n"
            "Обнови память. Можно:\n"
            "- add — новый факт, которого в памяти нет;\n"
            "- update — факт из памяти изменился или уточнился; сюда же "
            "слияние похожих: «играет в шахматы» и «любит шахматы» — один "
            "факт, а «купил ноутбук» после «продал ноутбук, взял планшет» надо "
            "заменить, а не держать оба;\n"
            "- remove — факт больше не верен, человек сам его опроверг, или "
            "это дубль, который ты только что слила в другую строку.\n\n"
            "Правила:\n"
            "- факт — одна короткая строка от третьего лица: техника, игры, "
            "работа, привычки, вкусы, имена людей и питомцев, важные "
            "обстоятельства жизни;\n"
            "- ничего сиюминутного: «попросил скриншот», «завтра к врачу», "
            "«сейчас играет» — это к утру протухнет;\n"
            "- ничего про самого помощника;\n"
            "- только то, что человек сказал о себе сам. Пересказ стрима, "
            "видео, чужого разговора, прочитанное вслух — не факт о нём;\n"
            "- не трогай то, о чём этот разговор ничего не говорит;\n"
            "- не больше шести правок, только самое стоящее;\n"
            "- weight — важность: 3 — главное о человеке (близкие люди, "
            "работа, основное железо, любимые игры), 2 — полезное, 1 — мелочь;\n"
            "- менять нечего — пустой список.\n\n"
            "Ответ — только JSON такого вида:\n"
            '{"ops": [{"op": "add", "text": "...", "weight": 2}, '
            '{"op": "update", "id": 3, "text": "...", "weight": 3}, '
            '{"op": "remove", "id": 5, "why": "..."}]}\n\n'
            f"Память:\n{known or '(пусто)'}\n\n"
            f"Разговор:\n{talk}"
        )

        # Потолок щедрый не от жадности: если провайдер не примет просьбу
        # молчать про размышления, они съедят бюджет раньше ответа.
        self.did_ask = True
        answer = self._ask_plainly(ask, DIGEST_TOKENS, json_mode=True)
        choice = answer.choices[0]
        text = (choice.message.content or "").strip()
        if not text:
            if choice.finish_reason == "length":
                raise RuntimeError(
                    "не хватило токенов на выжимку: модель думала дольше, "
                    f"чем позволено ({DIGEST_TOKENS})"
                )
            return {}

        return memory.apply_changes(memory_ops(text), seen=[f["text"] for f in snapshot])


def own_turns(turns: list[dict], threshold: float) -> list[dict]:
    """Реплики хозяина с её ответами; сказанное чужим голосом выкидываем.

    Микрофон слышит стрим, Discord и колонки. Даже когда на такую фразу
    ответили (отбор по голосу выключен или фраза прошла), в память о
    хозяине она попадать не должна. Сходство пишется в историю при каждой
    голосовой реплике; без него (чат, короткая фраза) — оставляем.

    Заход первой (`first`) — тоже не его реплика: там пометка о молчании
    и её собственный ответ. В память о хозяине оба пропускаются: в
    догадках по её заходам («помнит, что он любит рыбалку») легко вылезло
    бы то, что она выдумала, глядя на экран.
    """
    kept: list[dict] = []
    skip_reply = False
    for turn in turns:
        if turn.get("role") == "user":
            score = turn.get("voice")
            skip_reply = bool(turn.get("first")) or (
                isinstance(score, (int, float)) and threshold > 0 and score < threshold
            )
            if not skip_reply:
                kept.append(turn)
        elif not skip_reply:
            kept.append(turn)
    return kept


def memory_ops(text: str) -> list[dict]:
    """Правки памяти из ответа модели. Терпит обёртку ```json и лишний текст."""
    import json

    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return []
    if isinstance(data, dict):
        data = data.get("ops", [])
    return [op for op in data if isinstance(op, dict)] if isinstance(data, list) else []
