r"""Мозг: облачная модель через OpenAI-совместимый протокол.

Провайдер задаётся в .env одной строкой — DeepSeek, OpenRouter, MiniMax и
OpenAI говорят на одном протоколе, поэтому смена стоит ровно ничего.

Ответ отдаётся потоком и режется по предложениям: первое предложение уходит
в синтез, пока модель дописывает остальное.
"""

from __future__ import annotations

import os
import random
import re
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Iterator

import config
from core import personas

# Режем на предложениях, но не на сокращениях и не на инициалах.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Минимальная длина куска, который есть смысл отправлять в синтез отдельно.
MIN_CHUNK = 25
# Сколько раз за ответ модель может сходить в интернет: поиск, чтение
# страницы, уточняющий поиск. Дальше — отвечает тем, что нашла.
TOOL_ROUNDS = 3
# При `finish_reason == "length"` недоговорённый хвост не озвучивается:
# модель получает его для продолжения фразы.
CONTINUE_ROUNDS = 2
# Просьба продолжить. Короткая и без инструментов: продолжать тут нечего,
# а лишние вызовы только истратили бы тот же обрезанный потолок.
CONTINUE_ASK = ("Ты оборвалась на полуслове. Продолжи ровно с того места, "
                "без повторов, вступлений и извинений.")
# Чем кончаем, когда и третьего куска не хватило. Сказать честно лучше,
# чем замолчать на середине.
CONTINUE_LAST = "Дальше не влезло — скажи «продолжай», договорю."
# Весь потолок ушёл на размышления, и сказать нечего. Тогда тот же круг
# переспрашиваем без размышлений и с таким потолком; не помогло — честная
# фраза, а не тишина, которую не отличить от «не услышала».
ROOMY_TOKENS = 1500
EMPTY_LAST = "Задумалась и не уложилась — спроси ещё раз, покороче."
# Общий срок поиска меняется в настройках через `config.WEB_SEARCH_BUDGET`.
# После общего срока модель отвечает по уже найденным результатам.
TOOL_BUDGET = 12.0
# Так начинается разметка вызова инструмента у DeepSeek, когда она
# прорывается в обычный текст. Вслух это не произносится.
TOOL_MARKUP = ("<｜", "｜DSML｜", "<|tool", "<tool_call>")

# Некоторые модели кладут размышления в текст между метками `<think>`, а не
# в поле `reasoning_content`; этот фрагмент не должен попасть в озвучку.
OPEN_THINK = "<think>"
CLOSE_THINK = "</think>"
_РАЗМЫШЛЕНИЯ = re.compile(r"<think>.*?(?:</think>|$)", re.S)


def без_размышлений(текст: str) -> str:
    """Ответ без `<think>…</think>` — для разовых запросов (память, заметки).

    Незакрытая метка (ответ упёрся в потолок посреди мыслей) режет всё до
    конца: мысли не ответ, а без закрывающей метки конца у них нет.
    """
    return _РАЗМЫШЛЕНИЯ.sub("", str(текст or "")).strip()


class _БезРазмышлений:
    """Вырезает `<think>…</think>` из потока ответа на лету.

    Метка может прийти разорванной между кусками («<thi» + «nk>»), поэтому
    недоговорённое начало метки ждёт следующего куска, а не уходит в речь.
    """

    def __init__(self) -> None:
        self.внутри = False
        self.хвост = ""

    @staticmethod
    def _начало_метки(текст: str, метка: str) -> int:
        """Сколько последних знаков текста — начало метки (ждём продолжения)."""
        for n in range(min(len(метка) - 1, len(текст)), 0, -1):
            if текст.endswith(метка[:n]):
                return n
        return 0

    def feed(self, кусок: str) -> tuple[str, str]:
        """Кусок потока → (что можно говорить, что было размышлением)."""
        текст = self.хвост + кусок
        self.хвост = ""
        видно: list[str] = []
        мысли: list[str] = []
        while текст:
            метка = CLOSE_THINK if self.внутри else OPEN_THINK
            где = текст.find(метка)
            if где == -1:
                n = self._начало_метки(текст, метка)
                if n:
                    self.хвост = текст[-n:]
                    текст = текст[:-n]
                (мысли if self.внутри else видно).append(текст)
                break
            (мысли if self.внутри else видно).append(текст[:где])
            текст = текст[где + len(метка):]
            self.внутри = not self.внутри
        return "".join(видно), "".join(мысли)

    def finish(self) -> str:
        """Конец потока: недоговорённое начало метки вне мыслей — обычный текст."""
        остаток, self.хвост = self.хвост, ""
        return "" if self.внутри else остаток
# Инструмент завершения доступен только голосовому разговору. Простые
# прощания разбирает `voice_loop`, остальные — модель по смыслу.
END_NAME = "end_conversation"
# На сколько реплик история укорачивается разом, когда упёрлась в потолок.
# По одной нельзя: каждая новая реплика сдвигала бы начало запроса, и кеш
# префикса ловил бы один только характер. Пачкой начало стоит на месте
# несколько ответов подряд — с этого и дешевле входные токены.
HISTORY_KEEP_BACK = 8
TALK_RULES = """## Как вести разговор

Ты в живом разговоре. Отвечаешь на последнюю реплику хозяина — на то, что он сказал сейчас. Всё, что было раньше, — фон, по которому понятно, о чём речь.

Короткая реплика вроде «ну да», «ага», «не, нормально» — почти всегда ответ на твои последние слова. Отвечай на её смысл как собеседник: продолжи мысль, согласись, подколи — или просто прими и закончи. Хватает одной фразы.

Сделанное — сделано. Если в истории ты уже открыла, включила, записала или сказала, что не можешь, — это уже случилось. Делать снова — только когда он прямо просит ещё раз. Отказала один раз — дальше разговор идёт о том, что он говорит сейчас.

Говори от себя: сразу по сути и своими словами. Что он сказал, он знает сам, — ответ начинается с твоей мысли. Не поняла — коротко спроси, что именно.

Он поправил тебя — прими поправку и продолжай уже с правильным, одной фразой.

Пометка «Здесь закончился прошлый разговор» делит историю. Что выше неё — уже было и закончилось; к нынешнему разговору это относится, только если он сам туда вернулся.

Как это звучит — примеры тона, не заготовки:
Ты: «Такой программы у меня нет, странно, да?» Он: «Ну да». Ты: «Вот и я о том же».
Ты: «Открыла Дискорд». Он: «Не, я не просил, я про другое». Ты: «А, ладно. Так что там?»
Он: «Мы же про диск говорили». Ты: «Точно, про диск. Что с ним?»
Он: «Хватит пересказывать, просто ответь». Ты: отвечаешь по сути, без извинений."""
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
# Ворота имени находят слово «Труба», но адресата по смыслу определяет модель.
# Если обращались к другому, инструмент запрещает ответ и запись в память.
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
            "просто упоминание, а «Труба, глянь…», «слышь, труба» — к тебе. "
            "Имя в начале фразы как обращение — тоже к тебе: «Труба, "
            "слушай…», «Труба, а …». Отвечай, даже если дальше вопрос "
            "похож на разговор с друзьями."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
# Что сказать, пока ищем, если сама она молчит: иначе секунды тишины.
# Пока идёт второй круг, звучит короткая фраза по виду дела. `FILLERS` —
# все такие фразы: по ним голосовой цикл узнаёт заполнитель, а не ответ.
LOCAL_FILLERS = ("Секунду, гляну.", "Щас посмотрю.")
FILLERS = (frozenset(LOCAL_FILLERS) | {"Погоди, гляну в интернете."}
           | personas.all_wait_phrases())
WEB_TOOLS = frozenset({"web_search", "read_page"})
# Быстрый поиск переформулирует вопрос и добавляет месяц и год для новых
# событий.
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
# Отсылки к разговору и относительные даты требуют подготовки запроса.
QUERY_CONTEXT = re.compile(
    r"\b(?:он|она|оно|они|его|ее|их|ему|ей|им|него|нее|них|нем|ней|это|этот|эта|эти|эту|"
    r"этого|этой|этому|этим|этих|этими|этом|такой|такая|такие|такого|такую|"
    r"подробнее|недавно|новые|новый|новое|"
    r"новых|последние|последний|последнего|свежие|свежее|сейчас|сегодня|"
    r"завтра|вчера|ныне)\b|^\s*(?:а|и)\b", re.IGNORECASE)

# Кнопка поиска обходит словесные ворота `ASKS` и `FRESH`: нажатие само
# задаёт намерение искать, а первый круг должен вызвать интернет.
SEARCH_ASK = (
    "Хозяин нажал кнопку поиска. Его фраза — это то, что надо найти, а не "
    "тема для разговора: запрос для поисковика составляй сама, коротко и по "
    "его словам. Сразу вызови web_search, потом ответь коротко по "
    "найденному. Не комментируй саму фразу и не переспрашивай, что искать."
)
# Интернет доступен при прямой просьбе или вопросе о меняющихся данных.
# Без этих признаков инструменты поиска модели не предлагаются.
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
# Слова о свежем при обращении «у тебя» или «твои» относятся к ассистенту,
# а не к новостям; явная просьба найти всё равно открывает интернет.
ABOUT_HER = re.compile(r"\bу тебя\b|\bс тобой\b|\bтво(й|я|ё|е|и|их|ими|ем|ей|ю)\b",
                       re.IGNORECASE)


# Короткое уточнение сразу после поиска: «А в евро?», «А завтра?». Без
# ключевых слов, но ему нужен интернет. Только следующей репликой за
# поиском, не позже FOLLOW_UP_MINUTES.
FOLLOW_UP = re.compile(r"^\s*[аи]\b", re.IGNORECASE)
FOLLOW_UP_MINUTES = 5
# Завершение разговора разрешено только при признаке прощания.
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


# Ограничение длины повторяется рядом с вопросом: в начале длинного
# промпта модель соблюдает его хуже.
ALOUD = (
    "Сейчас разговор голосом. По умолчанию — одна-две короткие фразы; "
    "длиннее, только если он сам просит объяснить или рассказать подробно."
)
# После короткого ожидания и одного повтора используем запасного
# провайдера (`_fall_back`), чтобы не задерживать ответ.
CONNECT_TIMEOUT = 4.0
RETRIES = 1
# Держим соединение дольше пауз между репликами, чтобы не открывать его
# заново перед каждым ответом.
KEEPALIVE = 300.0
# Потолок для выжимки фактов. Модель рассуждающая: размышления тратят те же
# токены, что и ответ, и на скупом потолке ответ приходит пустым.
DIGEST_TOKENS = 1500

# Для выжимки фактов отключаем размышления, чтобы не тратить токены.
# Если провайдер не принимает поле, повторяем без него.
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


def say_words(said: list[str]) -> list[str]:
    """Сказанное за круг — только непустые куски, для `assistant` в запросе.

    Продолжение возвращает модели уже произнесённое плюс
    недоговорённый хвост. Пустые куски в `_sentences` не попадают, но
    склейка тут для страховки: пустая строка в `content` у провайдера
    иногда означает «сообщение без текста» и роняет следующий запрос.
    """
    return [word for word in (str(one).strip() for one in said) if word]


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

    У модели нет своих часов. Строка времени идёт
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


def _result_of(ответ: str) -> dict:
    """Ответ инструмента разобранным словарём. Битый — пустой.

    Наружу отдаётся в `last_turn_tools`: голосовой цикл смотрит там `ok`,
    `path` и `name`, а разбирать JSON второй раз незачем.
    """
    import json

    try:
        data = json.loads(ответ)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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

    Срок чуть больше самого долгого пути в выбранном режиме. Платный
    поиск может занимать до 25 с, поэтому общий предел не должен быть 17 с.
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

    `ok=False` означает отказ поисковиков. Повтор не запускаем,
    чтобы не задерживать ответ.
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


def _at_of(turn: dict, key: str = "at") -> datetime | None:
    """Время реплики истории. None — его нет или оно не разбирается."""
    at = turn.get(key)
    if not isinstance(at, str) or not at:
        return None
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def _new_talk_note(when: datetime, ended: datetime | None = None) -> str:
    """Пометка начала разговора и известного времени закрытия прошлого."""
    ended = f" (в {ended:%H:%M})" if ended is not None else ""
    return (f"— Здесь закончился прошлый разговор{ended} и начался новый "
            f"({when:%d.%m, %H:%M}). Всё выше — прошлые разговоры: "
            "это то, что уже было. —")


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


# При отказе основного провайдера отвечает запасной с ключом; основной
# пробуется снова через `HOME_RETRY` секунд.
REGION_BLOCK = ("unsupported_country", "country, region, or territory")
SPARE_ORDER = ("deepseek", "openrouter", "minimax", "openai")
HOME_RETRY = 600.0

# Если до `HEDGE_AFTER` не пришло ни одного куска, запрос
# отправляется запасному; отвечает первый. По умолчанию выключено, поскольку
# оба запроса могут быть платными. Настройка находится в разделе «Ответы».
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


# Пока голос включён, периодически запрашиваем {base_url}/models,
# чтобы соединение оставалось открытым между репликами.
WARM_EVERY = 45.0


class KeepWarm:
    """Держит соединение с облаком тёплым, пока включён голос.

    Отдельный поток, свой таймер: ни в потоке окна, ни в голосовом цикле
    сетевых запросов быть не должно. Ошибки молча игнорируем — подогрев
    не должен мешать голосу. Текущий `client` берётся на каждый запрос,
    чтобы подогревать выбранного провайдера.
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
        self._closed = threading.Event()
        self._closed_streams = set()
        self._close_lock = threading.Lock()
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
        if self._spare_started or self._closed.is_set():
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
            if self._closed.is_set():
                self._close_once(stream)
                return
        except Exception as exc:
            self._queue.put((who, exc))
            return
        try:
            for chunk in stream:
                if self._closed.is_set():
                    return
                self._queue.put((who, chunk))
        except Exception as exc:
            self._queue.put((who, exc))
            return
        self._queue.put((who, _END))

    def _close_once(self, stream):
        with self._close_lock:
            identity = id(stream)
            if identity in self._closed_streams:
                return
            self._closed_streams.add(identity)
        _close_stream(stream)

    def close(self):
        self._closed.set()
        for stream in list(self._streams.values()):
            self._close_once(stream)
        self._queue.put((self._winner or "main", _END))

    def _alive(self) -> bool:
        """Есть ли ещё кто-то, кто может ответить."""
        return any(name not in self._done for name in self._started)

    def _win(self, who: str) -> None:
        self._winner = who
        for name, stream in self._streams.items():
            if name != who:
                self._close_once(stream)
        if self._on_winner is not None:
            self._on_winner(who, self._fired)

    def __next__(self):
        while True:
            if self._closed.is_set():
                raise StopIteration
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

    При свежей установке пользовательского файла может не быть: он появляется
    после сохранения характера в пульте. Тогда нужен готовый характер.
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

        # История разговора сохраняется между запусками.
        from core import memory

        if config.HISTORY_TURNS > 0:
            for turn in memory.load_history(config.HISTORY_TURNS):
                self._history.append(turn)
        # Число новых реплик для выжимки не выводится из длины ограниченной
        # истории: после заполнения она не растёт. Загруженное с диска уже разобрано.
        self._undigested = 0
        # Спрашивала ли модель о памяти в последней выжимке. Нужен, чтобы
        # отличить «спросила, нового нет» от «было нечего спрашивать»:
        # возвращаются оба раза пустым словарём (см. digest).
        self.did_ask = False
        # Понимает ли провайдер просьбу не думать вслух. None — не пробовали.
        self._quiet: bool | None = None
        # Принимает ли провайдер инструменты (интернет). None — не пробовали.
        self._tools_ok: bool | None = None
        # При ответе на кнопку поиска первый круг должен вызвать интернет.
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
        # По признаку поиска телефон показывает карточку после любого
        # ответа с интернетом, включая поиск без кнопки «Найти».
        self.searched: bool = False
        # Карточка открывает фактические запросы поисковика (`web_queries`).
        self.last_queries: list[str] = []
        # Накопитель для истории: заполняется по ходу ответа, в историю уходит
        # одной записью оттуда (см. `_note_search` и `_flush_search`).
        self._search_seen: list[str] = []
        # Когда в этом ответе уходили в модель и когда от неё пришло первое
        # слово и первое готовое предложение — по каждому кругу инструментов.
        # Считает только perf_counter, голосовой цикл берёт это для замера
        # ответа в журнал (ui/web_runtime.py::_log_messages).
        self.last_timing: dict = {"rounds": []}
        # Что модель вызывала в последнем ходу: [{"name", "args", "result"}].
        # Голосовой цикл читает это, чтобы самому узнать, что ход был разбором
        # документа или скопированного, и спросить про заметку — без отдельного
        # инструмента и без решения модели (см. `VoiceLoop._analysis_turn`).
        self.last_turn_tools: list[dict] = []

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

        Локальный сервер не становится запасным даже с ключом: запрос
        облачного провайдера не должен незаметно уйти на локальную модель.
        Для `local` как основного доступен облачный запасной (`_fall_back`).
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
        abilities = self._abilities()
        system = self._persona + "\n\n" + abilities + "\n\n" + TALK_RULES
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

        # Умения меняются редко и стоят в кешируемом начале.
        # Часы и память меняются часто, поэтому идут отдельным хвостом.
        tail = [_now_line()]
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
        # Пометки между репликами зависят только от сохранённой истории,
        # поэтому начало запроса остаётся одинаковым для кеша.
        gap = timedelta(minutes=float(getattr(config, "HISTORY_GAP_MINUTES", 40) or 40))
        previous_at: datetime | None = None
        previous: dict | None = None
        for turn in self._history:
            when = _at_of(turn)
            ended = _at_of(previous, "talk_end") if previous is not None else None
            closed = previous is not None and "talk_end" in previous
            if closed or (when is not None and previous_at is not None
                          and when - previous_at > gap):
                start = when or ended
                if start is not None:
                    past.append({"role": "system",
                                 "content": _new_talk_note(start, ended)})
            if when is not None:
                previous_at = when
            past.append({"role": turn["role"], "content": turn["content"]})
            if turn.get("searched"):
                past.append({"role": "system", "content": _found_note(turn["searched"])})
            previous = turn
        if previous is not None and "talk_end" in previous:
            past.append({"role": "system", "content": _new_talk_note(
                datetime.now(), _at_of(previous, "talk_end"))})
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
        Завершение разговора не предлагается на реплику с вопросом.

        При ходе с кнопки поиска доступен только интернет: другие действия
        могут отвлечь модель от запроса.

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
        # Заметки доступны всегда сразу после YouTube. Постоянный порядок
        # сохраняет кеш начала запроса.
        tools.append(hands.NOTE_TOOL)
        tools.append(hands.READ_TOOL)
        # Диктовка доступна только при работающем голосовом цикле, чтобы
        # предложение диктовать можно было выполнить. Порядок сохраняет кеш.
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
        # Поиск файла доступен всегда после документов; постоянный порядок
        # сохраняет кеш начала запроса.
        tools.append(hands.FIND_TOOL)
        # Чтение буфера доступно всегда после поиска файла; постоянный порядок
        # сохраняет кеш начала запроса.
        tools.append(hands.CLIP_TOOL)
        # Запись в буфер обмена — сразу за чтением и тем же набором: «исправь
        # ошибки в скопированном и положи обратно» — длинная фраза, и уводить
        # её в облако на уточнение незачем, а без инструмента модель ответит,
        # что не умеет писать в буфер. Порядок набора не меняется — на нём
        # держится кеш запроса.
        tools.append(hands.PUT_TOOL)
        # Питание доступно всегда после буфера; постоянный порядок сохраняет
        # кеш начала запроса. Подтверждение выполняет голосовой цикл.
        tools.append(hands.POWER_TOOL)
        # Закрытие открытого документа — сразу за питанием и тоже всегда: окно
        # с файлом есть у каждого, а «закрой документ отчёт» — длинная фраза.
        # Порядок набора не меняется — на нём держится кеш запроса.
        tools.append(hands.CLOSE_DOC_TOOL)
        tools.extend(hands.action_tools(getattr(self, "actions", None) or {}))
        # Интернет — всегда, когда он включён: в правилах ей сказано, что он
        # есть, и без инструмента на вопрос о незнакомом факте она хваталась
        # за другие действия. Когда искать, а когда нет, решает описание
        # поиска (`web.HINT`, `web.TOOLS`). Набор всегда один — кеш стоит.
        if self._tools_on():
            tools.extend(web.TOOLS)
        # Длинная реплика со словом «всё» не считается прощанием.
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

    supports_cancellation = True

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
        cancel: threading.Event | None = None,
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

        voice — сходство голоса с владельцем (core/speaker.py);
        None — чат или фраза слишком короткая, чтобы судить. Сходство
        ложится в историю, и чужое не попадает в память (см. digest).
        """
        from core.cancel_stream import ReplyCancelled, check
        with self._reply_lock:
            self._reply_cancel = cancel
            completed = False
            try:
                check(cancel)
                yield from self._reply_unlocked(
                    user_text, context, image, voice, aloud, can_end, search, first,
                    named,
                )
                completed = True
            except ReplyCancelled:
                return
            finally:
                if not completed and cancel is not None and cancel.is_set() and first is None:
                    self._add_turn(user_text, "(ответ прерван пользователем)", voice)
                self._reply_cancel = None

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
        # Запросы этого ответа попадают в историю одной записью через
        # `_flush_search`, чтобы одна попытка не распалась на несколько строк.
        self._search_seen: list[str] = []
        # Вызовы инструментов этого хода: наружу отдаются в `last_turn_tools`.
        # Чистится на каждом ответе — старые вызовы путать нельзя, иначе вопрос
        # про заметку прилетел бы к ходу, где ничего не разбирали.
        self.last_turn_tools = []
        # Быстрый поиск идёт параллельно короткой фразе ожидания, затем
        # найденное добавляется к реплике для ответа за один круг модели.
        # Для поиска со снимком остаётся путь через инструмент.
        # Расход этого ответа по всем кругам: суммируется в кругах, в журнал
        # уходит одним событием в конце. Объявлен до `try`, чтобы `finally`
        # видел его даже после сбоя.
        usage: dict = {}
        self.last_timing = {"rounds": []}
        try:
            found = None
            if search and first is None and image is None and self._tools_on():
                found = yield from self._search_first(user_text)
            fast = found is not None
            messages = self._messages(user_text, context, image, aloud,
                                      search and not fast)
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
            # Замеры времени — тоже только про этот ответ: прошлые круги в
            # записи о задержке только сбивали бы с толку.
            self.last_timing = {"rounds": []}
            # Страховка — на этот ответ: чужой провайдер из прошлого ответа
            # не должен влиять на новый.
            self._hedge_spare = None
            self._hedge_model = None
            # Ход с кнопки поиска: первый круг идёт с `tool_choice`, чтобы
            # модель не ответила болтовнёй, не поискав. Снимаем в `finally` —
            # обычный ответ `tool_choice` не должен получать никогда
            # (см. `_open_stream_here`).
            self._must_search = bool(search) and self._tools_on() and not fast

            yield from self._reply_rounds(
                messages, tools, user_text, voice, usage, first,
                found=found["query"] if fast and found["ok"] else None,
            )
        finally:
            self._must_search = False
            # Поиск уже сделан — его нельзя потерять из-за того, что генератор
            # остановили («хватит») или он упал. Ответ без поиска, неудачный
            # поход и открытие старой записи сюда не попадают: в `_search_seen`
            # лежит только то, что поисковик правда ответил.
            self._flush_search()
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
        query = (order.target if order is not None and order.action in ("search", "browser_search")
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
        yield personas.wait_phrase("web", getattr(config, "PERSONA_PRESET", None))
        deadline = time.monotonic() + _search_wait()
        while поиск.is_alive() and time.monotonic() < deadline:
            self._check_reply_cancelled()
            поиск.join(timeout=min(.05, max(0, deadline - time.monotonic())))
        self._check_reply_cancelled()
        result = str(box.get("result") or '{"error": "поиск не ответил"}')
        ok = not result.startswith('{"error"')
        if ok:
            _collect_sources("web_search", result, self.last_sources)
        # Карточка открывает фактические запросы; при пустом результате
        # голосовой цикл покажет исходную реплику (`VoiceLoop._send_search`).
        if ok:
            self.searched = True
            self.last_queries = [str(q) for q in (box.get("queries") or []) if str(q).strip()]
            # Собираем, а не пишем: в историю уйдёт одна запись на весь ответ
            # (см. `_flush_search`) — иначе один вопрос с тремя вариантами
            # показывался бы на телефоне тремя строками.
            self._note_search(self.last_queries)
        return {"ok": ok, "query": "; ".join(box.get("queries") or [query]),
                "result": result}

    def _note_search(self, queries) -> None:
        """Запомнить, что в этом ответе ушло в поисковик.

        Только сбор: в историю это уходит из `_flush_search` одной записью на
        ответ. Собирать надо всё, что поисковик ответил, — но писать по
        одному запросу нельзя: `_search_first` формулирует несколько вариантов
        одного и того же вопроса, иначе на телефоне появятся дубли.
        """
        for запрос in list(queries or []):
            текст = " ".join(str(запрос or "").split())
            if текст and текст not in self._search_seen:
                self._search_seen.append(текст)

    def _flush_search(self) -> None:
        """Один ответ — одна запись в истории поиска.

        Зовётся из `finally` в `reply`: и при нормальном конце, и когда
        генератор остановили или он упал. Граница здесь — **ответ**, а не
        отдельный `web_search` и не временное окно: два одинаковых вопроса
        подряд в разные дни остаются двумя записями, а десять запросов внутри
        одного ответа — одной.

        Сбой диска или хранилища не должен ронять разговор: телефон спросит
        историю и получит пустой список, а не услышит ошибку вместо ответа.
        """
        запросы = list(getattr(self, "_search_seen", None) or [])
        self._search_seen = []
        if not запросы:
            return
        try:
            from core import search_history

            search_history.add(запросы[0], also=запросы[1:])
        except Exception:
            pass

    def _search_queries(self, user_text: str, query: str) -> list[str]:
        """Самостоятельный запрос ищется сразу, контекстный готовит модель.

        Не ответила за REWRITE_TIMEOUT или ответила криво — ищем по фразе
        как есть. Платный поиск запрос составляет сам — там не переписываем.
        Последние реплики — чтобы «а найди про него подробнее» стало
        запросом про то, о чём говорили.
        """
        import json

        from core import web

        if getattr(config, "WEB_SEARCH_MODE", "free") == "paid":
            return [query]
        if not QUERY_CONTEXT.search(query.lower().replace("ё", "е")):
            self._tell("web_queries", {"queries": [query], "took": 0.0, "direct": True})
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

        # Внешний срок ограничивает весь поток: `timeout` запроса ограничивает
        # только паузу между байтами и не останавливает поток с пустыми байтами.
        переписка = threading.Thread(target=rewrite, daemon=True, name="search-queries")
        переписка.start()
        переписка.join(REWRITE_TIMEOUT)
        try:
            if "error" in box:
                raise box["error"]
            if "answer" not in box:
                raise TimeoutError(f"не ответила за {REWRITE_TIMEOUT:.0f} с")
            text = без_размышлений(box["answer"].choices[0].message.content or "")
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
        import json

        from core import hands, web

        reset_capture = (getattr(self, "actions", None) or {}).get("reset_capture")
        if callable(reset_capture):
            reset_capture()

        # При отказе поисковиков отвечаем по полученному и не задерживаем
        # ответ повторным поиском.
        spoken = ""
        searched = found is not None
        # Был ли в этом ответе интернет. Чтение буфера, файла или документа
        # тоже ставит `searched`, но готовое подтверждение запрещает только
        # интернет: там нужен ответ модели по найденному.
        web_used = found is not None
        queries: list[str] = [found] if found else []
        search_started = None
        dead = False
        # Круг, где все действия отклонены проверкой: дальше пробовать нечего,
        # следующий круг — сразу ответ без инструментов (см. `_open_stream`).
        self._only_refused = False
        asked_at = time.monotonic()

        for round_no in range(TOOL_ROUNDS + 1):
            self._check_reply_cancelled()
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
            from core.cancel_stream import wrap_reply_stream
            stream = wrap_reply_stream(stream, getattr(self, "_reply_cancel", None))
            calls: dict[int, dict] = {}
            said: list[str] = []
            reasoning: list[str] = []
            state: dict = {}
            try:
                yield from self._sentences(stream, calls, said, reasoning, usage, mark,
                                           state)
            finally:
                _close_stream(stream)
            # Ответ упёрся в потолок токенов: хвост фразы в озвучку не ушёл
            # (см. `_sentences`), и без продолжения голос замолчал бы посреди
            # слова. Продолжаем сами — но только если в круге не было вызовов
            # инструментов: там модель ещё думает, что собирается делать, и
            # продолжать нечего.
            if (state.get("finish") == "length" and not calls
                    and (said or state.get("tail"))):
                yield from self._continue(messages, said, state, usage)
            elif state.get("finish") == "length" and not calls and not said:
                # Весь потолок ушёл на размышления: ни слова, ни вызова.
                # Тот же круг ещё раз — без размышлений и с запасом потолка.
                self._tell("empty_retry", {
                    "limit": int(getattr(config, "MAX_TOKENS", 0) or 0),
                    "thought": len("".join(reasoning))})
                self._check_reply_cancelled()
                stream, tools = self._open_stream(
                    messages, tools, last=round_no == TOOL_ROUNDS or late,
                    think=False, roomy=True)
                stream = wrap_reply_stream(stream, getattr(self, "_reply_cancel", None))
                reasoning, state = [], {}
                try:
                    yield from self._sentences(stream, calls, said, reasoning, usage,
                                               mark, state)
                finally:
                    _close_stream(stream)
                if (state.get("finish") == "length" and not calls
                        and (said or state.get("tail"))):
                    yield from self._continue(messages, said, state, usage)
                elif not calls and not said:
                    said.append(EMPTY_LAST)
                    yield EMPTY_LAST
            self._check_reply_cancelled()
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
                # Ждут второго круга и чтение (буфер, файл, документ), но в
                # журнал «искать в интернете» и фраза про интернет — только
                # для настоящего поиска.
                web_now = any(call["name"] in WEB_TOOLS for call in ordered)
                web_used = web_used or web_now
                if not searched and web_now and self.on_event is not None:
                    try:
                        self.on_event("web_start",
                                      {"after": round(time.monotonic() - asked_at, 2)})
                    except Exception:
                        pass

                if not searched and not spoken:
                    first_call = next(call for call in ordered
                                      if call["name"] not in hands.LOCAL)
                    name = first_call["name"]
                    args = _args_of(first_call)
                    if name in WEB_TOOLS:
                        kind = "web"
                    elif name == hands.CLIP_NAME:
                        kind = args.get("mode") or "read"
                        if kind not in ("read", "translate", "analyze"):
                            kind = "read"
                    elif name == hands.DOC_NAME:
                        kind = "read" if args.get("mode") == "aloud" else "analyze"
                    elif name == hands.FIND_NAME:
                        kind = "find"
                    elif name == hands.READ_NAME:
                        kind = "notes"
                    elif name == hands.LIST_REM_NAME:
                        kind = "reminders"
                    else:
                        kind = "find"
                    if not ((name == hands.CLIP_NAME and kind == "read") or
                            (name == hands.DOC_NAME and kind == "read")):
                        yield personas.wait_phrase(
                            kind, getattr(config, "PERSONA_PRESET", None))
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
            refused = 0
            web_failed = []
            # Снимки — после всех ответов инструментов: OpenAI не принимает
            # сообщение пользователя между ответами на вызовы одного круга.
            shots: list[str] = []
            # Судьи круга уходят в облако сразу, все сразу: по очереди каждая
            # проверка стоит отдельных 1–1,5 с ожидания. В потоке только запрос,
            # разбор ответа и событие остаются в основном потоке ниже.
            answers, searches, отказы, pool = self._prejudge(ordered, user_text)
            for call in ordered:
                self._check_reply_cancelled()
                # Вызовы разводим по имени: интернет и действия — разные
                # инструменты, и ни один не знает про другой.
                call_shots: list[str] = []
                отказ = отказы.get(call["id"])
                if отказ == "quote":
                    # Без цитаты из текущей реплики действие не выполняется.
                    results.append(hands.not_asked(call["name"]))
                    refused += 1
                elif отказ == "youtube":
                    # YouTube требует явного упоминания; без него проверка
                    # моделью не нужна.
                    results.append(hands.youtube_not_named())
                    refused += 1
                elif call["name"] in hands.JUDGED and (
                        judged := self._judge_result(
                            call["name"], _args_of(call), user_text,
                            answers[call["id"]])) is not True:
                    # Цитата может быть упоминанием без просьбы. Если судья
                    # не ответил (`judged is None`), действие не выполняется.
                    results.append(
                        hands.not_really_asked(call["name"]) if judged is False
                        else hands.check_failed(call["name"]))
                    refused += 1
                elif call["name"] == hands.NAME:
                    results.append(hands.run_tool(call["args"], self.on_event))
                elif call["name"] == hands.CLOSE_NAME:
                    results.append(hands.run_close(call["args"], self.on_event))
                elif call["name"] == hands.CLOSE_DOC_NAME:
                    # Закрытие документа. Окно получает `WM_CLOSE`, а не
                    # сигнал процессу: несохранённый файл программа спросит
                    # сама. При нескольких окнах с таким названием инструмент
                    # ничего не закрывает и отдаёт заголовки — выбором
                    # занимается модель во втором круге.
                    results.append(hands.run_close_document(
                        call["args"], self.on_event))
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
                    # Папка: общий словарь ответа и строка журнала для модели
                    # и голосовой команды.
                    results.append(hands.run_folder(call["args"], self.on_event))
                elif call["name"] in hands.PC_NAMES:
                    # Управление компьютером использует общий словарь ответа
                    # и строку журнала для модели и голосовой команды.
                    results.append(self._run_pc(call))
                elif call["name"] == hands.SET_REM_NAME:
                    # Напоминание или таймер. Время к этому моменту уже
                    # посчитано моделью (у неё часы в каждом запросе) и
                    # пришло готовым — здесь только храним и будим.
                    results.append(
                        hands.run_set_reminder(call["args"], self.on_event))
                elif call["name"] == hands.LIST_REM_NAME:
                    # Список напоминаний требует пересказа моделью, поэтому
                    # не входит в `LOCAL`.
                    results.append(
                        hands.run_list_reminders(call["args"], self.on_event))
                elif call["name"] == hands.CANCEL_REM_NAME:
                    results.append(
                        hands.run_cancel_reminder(call["args"], self.on_event))
                elif call["name"] == hands.DOC_NAME:
                    # Документ: в журнал из `core/documents.py` идут только
                    # имя и размеры. Текст передаётся модели лишь на этот ответ.
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
                    # фраза; строки в журнал из `core/files.py`. Поиск шёл
                    # вместе с судьёй — берём его готовый результат.
                    results.append(hands.run_find_file(
                        call["args"], self.on_event,
                        found=(searches[call["id"]].result
                               if call["id"] in searches else None)))
                elif call["name"] == hands.CLIP_NAME:
                    # Буфер обмена. При `mode: read` текст уходит в синтез на
                    # компьютере (действие `read_aloud`), и в ответе его уже
                    # нет: наружу уходит только готовая фраза, второй круг ей
                    # не нужен. При `translate` и `analyze` модель получает
                    # сам текст с пометкой «это данные» — второй круг
                    # обязателен, и он уйдёт в облако, поэтому инструмент ещё
                    # и в `JUDGED`.
                    results.append(hands.run_clipboard(
                        call["args"], self.on_event,
                        getattr(self, "actions", None) or {},
                    ))
                elif call["name"] == hands.PUT_NAME:
                    # Запись в буфер обмена. Текст уже готов; инструмент кладёт его и
                    # говорит вслух короткое «Положила в буфер — вставляй.»
                    # В ответе самого текста нет, и второй круг не нужен.
                    results.append(hands.run_put_clipboard(
                        call["args"], self.on_event))
                elif call["name"] == hands.POWER_NAME:
                    # Питание компьютера. Инструмент только спрашивает вслух
                    # и ставит локальное ожидание (`core/power.ask`): выключает
                    # компьютер голосовой цикл после голосового подтверждения
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
                    if not results[-1].startswith('{"error"'):
                        if call["name"] == hands.LOOK_NAME:
                            # Действие вернуло сам снимок, а не путь к нему.
                            call_shots = [results[-1]]
                        elif call["name"] == hands.SAVED_LOOK_NAME:
                            # Уже сделанные кадры: экран больше не снимаем.
                            call_shots = json.loads(results[-1])
                else:
                    results.append(web.run_tool(call["name"], call["args"], self.on_event))
                    if results[-1].startswith('{"error"'):
                        web_failed.append(call["id"])
                    else:
                        # Источники для телефона берутся только из ответа поиска.
                        _collect_sources(call["name"], results[-1], self.last_sources)
                if not results[-1].startswith('{"error"'):
                    # В пометку истории — только то, что правда нашлось.
                    queries.append(_query_of(call))
                    if call["name"] == "web_search":
                        # Карточка показывает фактический запрос поисковика.
                        self.searched = True
                        запрос = _query_of(call)
                        if запрос:
                            self.last_queries.append(запрос)
                            # Собираем: несколько `web_search` внутри одного
                            # ответа — одна попытка и одна запись истории.
                            self._note_search([запрос])
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": ("снимки ниже" if len(call_shots) > 1 else "снимок ниже")
                    if call_shots else results[-1],
                })
                # Что вызывали в этом ходу — наружу, для голосового цикла. Снимки
                # сюда не кладутся: они большие, а вопрос про заметку их не касается.
                if not call_shots:
                    self.last_turn_tools.append({
                        "name": call["name"],
                        "args": _args_of(call),
                        "result": _result_of(results[-1]),
                    })
                shots.extend(call_shots)
            if pool is not None:
                pool.shutdown(wait=False)
            for shot in shots:
                # `role: tool` не несёт картинку: снимок идёт отдельным
                # сообщением пользователя и не сохраняется в истории.
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Вот снимок экрана."},
                        {"type": "image_url", "image_url": {"url": shot}},
                    ],
                })
            # Успешное действие на компьютере подтверждаем без второго круга.
            # Говорим подтверждение сами и заканчиваем ответ; в историю оно
            # идёт вместе со сказанным, чтобы потом помнила, что открыла.
            # Взгляд на экран сюда не попадает: там модель нужен снимок, и
            # интернет тоже — там нужен живой ответ из сети. Чтение вслух и
            # открытие найденного файла попадают: у них готовая фраза
            # (`confirm_forms`).
            if (not web_used and not shots and not late
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
            # Все действия круга отклонены: он ни о чём таком не просил.
            # Новые попытки стоят по 2–3 с каждая и тоже будут отклонены —
            # следующий круг сразу отвечает.
            if ordered and refused == len(ordered):
                dead = True
                self._only_refused = True

        self._check_reply_cancelled()
        if searched and not spoken:
            spoken = "Не вышло ничего толком найти, спроси по-другому."
            yield spoken

        if self.not_to_me and not spoken:
            # Если обращались не к ассистенту и ответа не было, реплика не
            # попадает в историю или память. Произнесённый ответ сохраняется.
            return
        if self.ended and not spoken:
            # Молчаливое закрытие не попадает в историю, чтобы не закреплять
            # молчание как ответ. Реплика учитывается для памяти.
            return
        # Заход первой: в историю кладётся не подсказка модели, а пометка
        # о молчании. Иначе в следующем запросе она читала бы свой же
        # текст как реплику человека и закапывалась в собственные советы.
        if first is not None:
            self._add_turn(first, spoken, first_turn=True)
            return
        self._add_turn(user_text, spoken, voice, [q for q in queries if q])

    def _continue(
        self, messages: list[dict], said: list[str], state: dict,
        usage: dict,
    ) -> Iterator[str]:
        """Договаривает ответ, упёршийся в потолок токенов.

        Запрос тот же, только с уже сказанным (вместе с недоговорённым
        хвостом) и прямой просьбой продолжить — и вовсе без инструментов:
        продолжать тут нечего, а вызовы только истратили бы тот же
        обрезанный потолок. Поток режется тем же `_sentences`, буфер
        которого начинается с хвоста, — фраза договаривается целиком, а не
        двумя кусками вслух.

        Продолжений не больше `CONTINUE_ROUNDS`, чтобы ограничить задержку
        голоса. Упёрся и третий кусок
        — говорим об этом прямо (`CONTINUE_LAST`), а не молчим.

        Всё сказанное (включая хвост) уходит в `said`, поэтому в историю
        ответ ложится одним сообщением, как и без продолжений.
        """
        tail = str(state.get("tail") or "")
        for шаг in range(1, CONTINUE_ROUNDS + 1):
            self._tell("continue", {
                "limit": int(getattr(config, "MAX_TOKENS", 0) or 0),
                "step": шаг,
                "of": CONTINUE_ROUNDS,
            })
            body = list(messages) + [
                {"role": "assistant",
                 "content": " ".join(say_words(said) + [tail]).strip()},
                {"role": "system", "content": CONTINUE_ASK},
            ]
            try:
                stream, _ = self._open_stream(body, [], last=True, think=False)
            except Exception:
                # Облако отказало на продолжении — договаривать нечем.
                # Произнесённое уже прозвучало; продолжаем после отказа.
                break
            mark = {"sent": time.perf_counter(), "word": 0.0, "sentence": 0.0}
            self.last_timing["rounds"].append(mark)
            next_state: dict = {}
            # Пробел после хвоста: куски модели приходят без разделителя,
            # а хвост обрывается прямо на слове — без него слова слипнутся.
            yield from self._sentences(stream, {}, said, [], usage, mark,
                                       next_state, f"{tail} " if tail else "")
            if next_state.get("finish") != "length":
                # Договорила: хвоста не осталось.
                return
            tail = str(next_state.get("tail") or "")
            if not tail:
                # Упёрлась в потолок, но ничего не наговорила: продолжать
                # нечего, а заново спрашивать — уже не продолжение.
                break
        said.append(CONTINUE_LAST)
        yield CONTINUE_LAST

    def _open_stream(self, messages: list[dict], tools: list[dict], last: bool,
                     think: bool = True, roomy: bool = False):
        """Поток ответа. Возвращает (поток, остались ли инструменты).

        Отказы разбираем по лесенке, чтобы интернет не ронял разговор:
        OpenAI в Chat Completions пускает инструменты только без
        размышлений (reasoning_effort=none). Модель, которая
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

        # Повтор с запасом потолка (`roomy`) — только у своего провайдера:
        # страховка собрала бы тело со своим, обычным потолком.
        hedge = None if roomy else self._hedge(messages, tools, last)
        if hedge is not None:
            return hedge, tools

        try:
            return self._open_stream_here(messages, tools, last, think, roomy)
        except (PermissionDeniedError, APIConnectionError) as exc:
            if not self._fall_back(exc):
                raise
            self._flatten_tool_rounds(messages)
            return self._open_stream_here(messages, tools, last, think, roomy)

    def _hedge(self, messages: list[dict], tools: list[dict], last: bool):
        """Страховка от заминок облака. None — страховки нет.

        Страховка только в первом круге: дальше история с вызовами
        инструментов собрана под конкретного провайдера, и отдать её
        запасному со своими особенностями нельзя. Запасного нет — тоже
        None, ждём основного.
        """
        if last or not bool(getattr(config, "HEDGE", False)):
            return None
        if any(m.get("role") == "tool" for m in messages):
            return None
        # Для локального сервера страховка отправляла бы запрос в платное
        # облако при обычной задержке. Обрыв связи обрабатывает `_fall_back`.
        from core import settings

        if settings.is_local(self.provider):
            return None
        spare = self._spare()
        if spare is None:
            return None

        was = self.provider
        after = float(getattr(config, "HEDGE_AFTER", HEDGE_AFTER))

        def on_error(who, exc, hedge):
            """После отказа основного повторяет запрос через запасного."""
            if not self._fall_back(exc):
                return False
            self._flatten_tool_rounds(messages)
            hedge.retry(lambda: self._open_stream_here(messages, tools, last)[0])
            return True

        def on_winner(who, fired):
            # Срабатывание страховки пишется в журнал. Победитель — в подписи,
            # а `model` события
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
        if spare == "deepseek" and not getattr(config, "REASONING", False):
            # Размышления съели бы потолок ответа — как у основного.
            body["extra_body"] = NO_THINKING
        for _ in range(5):
            try:
                return self._create_reply_stream(body, client=client)
            except BadRequestError as exc:
                message = str(exc).lower()
                if "extra_body" in body and "thinking" in message:
                    body.pop("extra_body")
                    continue
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
        return self._create_reply_stream(body, client=client)

    def _create_reply_stream(self, body, client=None):
        from core.cancel_stream import open_reply_stream
        client, request = self._client if client is None else client, dict(body)
        cancel = getattr(self, "_reply_cancel", None)
        return open_reply_stream(lambda: client.chat.completions.create(**request), cancel)

    def _check_reply_cancelled(self):
        from core.cancel_stream import check
        check(getattr(self, "_reply_cancel", None))

    def _open_stream_here(self, messages: list[dict], tools: list[dict], last: bool,
                          think: bool = True, roomy: bool = False):
        """`roomy` — потолок с запасом: прошлый круг весь ушёл на размышления."""
        from openai import BadRequestError

        limit = config.MAX_TOKENS
        if roomy:
            limit = max(int(limit) * 4, ROOMY_TOKENS)
        body = {
            "model": self._model,
            "messages": messages,
            **completion_limits(self.provider, limit, config.TEMPERATURE),
            "stream": True,
        }
        размышлять = think and bool(getattr(config, "REASONING", False))
        if (self.provider == "deepseek" and not размышлять
                and getattr(self, "_quiet", None) is not False):
            # DeepSeek размышляет по умолчанию, а размышления тратят тот же
            # потолок, что и ответ: на 220 токенах ответ приходил пустым, и
            # она молчала. Включаются они галочкой «Размышления перед
            # ответом». Не принял поле — лесенка ниже его уберёт и запомнит
            # (`_quiet`).
            body["extra_body"] = NO_THINKING
        if self._usage_ok is not False:
            # Без этого в потоке нет расхода: usage приходит отдельным куском
            # с пустым choices в самом конце.
            body["stream_options"] = {"include_usage": True}
        if tools and last:
            # Последний круг идёт без инструментов и с явной просьбой завершить:
            # одна лишь настройка `tool_choice=none` может вернуть разметку
            # вызова обычным текстом.
            from core import web

            # Смысл этой подсказки — «хватит искать», и искать можно только
            # интернетом. Поэтому смотрим на инструменты сети, а не на «не из
            # hands.LOCAL»: `read_notes` в LOCAL не входит намеренно (модели
            # нужен второй круг, чтобы пересказать прочитанное). Проверка
            # LOCAL ошибочно примет чтение заметок за поиск в интернете.
            # Искала ли она в этом ответе: интернет теперь в наборе всегда,
            # поэтому смотрим на сделанные вызовы, а не на выданные инструменты.
            searching = any(
                (call.get("function") or {}).get("name") in WEB_TOOLS
                for m in messages if m.get("role") == "assistant"
                for call in (m.get("tool_calls") or []))
            if getattr(self, "_only_refused", False):
                # Действия отклонены: он о них не просил. Отвечать надо на его
                # реплику, а не отчитываться о том, чего не делала.
                last_words = ("Действия тут не нужны. Ответь на его последнюю "
                              "реплику по смыслу, как в обычном разговоре, и не "
                              "упоминай действия.")
            elif searching:
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
            # Ход с кнопки поиска требует вызова интернета в первом круге.
            if self._wants_tool_choice(messages):
                body["tool_choice"] = "required"
        if not think and self.provider == "openai" and "reasoning_effort" not in body:
            # Продолжение упёршегося ответа (`_continue`) идёт без инструментов,
            # и размышления по умолчанию включились бы: они съели бы тот же
            # потолок токенов, и продолжение вышло бы пустым. Не принимает
            # модель параметр — лесенка ниже его уберёт.
            body["reasoning_effort"] = "none"

        # Попыток на столько отказов, сколько их бывает: stream_options,
        # размышления, reasoning_effort, инструменты — плюс последняя, чистая.
        for _ in range(5):
            try:
                return self._create_reply_stream(body), tools
            except BadRequestError as exc:
                message = str(exc).lower()
                if "extra_body" in body and "thinking" in message:
                    body.pop("extra_body")
                    self._quiet = False
                    continue
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
        return self._create_reply_stream(body), tools

    @staticmethod
    def _sentences(
        stream, calls: dict, said: list, reasoning: list, usage: dict,
        mark: dict | None = None, state: dict | None = None,
        buffer: str = "",
    ) -> Iterator[str]:
        """Режет поток на предложения; попутно собирает вызовы инструментов.

        В `said` кладётся ровно то, что ушло наружу, — оно и попадёт в историю.

        В `usage` копится расход этого круга. Последний кусок с расходом идёт
        с пустым `choices` — его надо разобрать до проверки на `choices`,
        иначе число пропадёт молча.

        `mark` — словарь замера круга: в него кладётся момент первого куска
        текста от модели и момент первого готового предложения. Пишем только
        когда словарь дан, иначе лишних вычислений на горячем пути.

        `state` — словарь, куда кладётся `finish_reason` последнего куска:
        вызывающий по нему узнаёт, чем кончился поток. Если это `"length"`
        (упёрлись в потолок токенов), недоговорённый хвост в озвучку не идёт
        и в `said` не попадает — он возвращается в `state["tail"]`, и с него
        начнётся продолжение. Голос на полуслове замирать не должен.

        `buffer` — с чего начинается склейка: у продолжения это хвост
        предыдущего куска, иначе фраза договорилась бы вразнобой.

        Расход у провайдера на весь круг, а не на кусок, поэтому повторный
        кусок с usage заменяет уже учтённый, а не складывается с ним. Круги
        инструментов при этом суммируются: у каждого своя `committed`.
        """
        finish = None
        leaked = False
        counted = False
        committed: dict = {}
        мысли = _БезРазмышлений()

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
            choice = chunk.choices[0]
            reason = getattr(choice, "finish_reason", None)
            if reason:
                # Причину берём у последнего куска с ней: у первых она
                # всегда None, а у провайдеров бывает и в самом начале.
                finish = reason
            delta = choice.delta

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
            # Размышления в тексте ответа (`<think>…</think>`) — не речь: в
            # мысли, а не в озвучку.
            piece, мысль = мысли.feed(piece)
            if мысль:
                reasoning.append(мысль)
            if not piece:
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

        if not leaked:
            # Недоговорённое «<thi…», которое так и не стало меткой, — текст.
            buffer += мысли.finish()
        tail = buffer.strip()
        if state is not None:
            state["finish"] = finish
        if tail and finish == "length" and state is not None:
            # Упёрлись в потолок токенов. Хвост в озвучку не идёт: фраза не
            # договорена, и синтез на полуслове замирает. Возвращаем его
            # вызывающему — с него модель продолжит ответ.
            state["tail"] = tail
            return
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

        Местные команды не проходят через модель. Запись даёт ей контекст
        выполненных действий для следующих реплик разговора.
        Подтверждения действий не требуют извлечения фактов в облаке.
        """
        with self._reply_lock:
            self._add_turn(user_text, reply, local_turn=True)

    def end_talk(self) -> None:
        """Сохраняет границу завершившегося разговора на последней реплике."""
        with self._reply_lock:
            if not self._history or "talk_end" in self._history[-1]:
                return
            self._history[-1]["talk_end"] = datetime.now().isoformat(
                timespec="seconds")
            self._keep_history()

    def announce(self, note: str, reply: str) -> None:
        """Её собственная реплика в историю — как заход первой.

        Так в историю ложится сработавшее напоминание (core/reminders.py):
        реплики человека тут нет, а без пометки она через пару реплик
        забыла бы, что напоминание вообще было. Пометка `first` — та же, что
        у проактивности: в разговор такая строка идёт как её собственная
        мысль, а не как реплика человека, и в память о нём (`own_turns`) не
        попадает.
        """
        with self._reply_lock:
            self._add_turn(str(note or ""), str(reply or ""), first_turn=True)

    def _add_turn(self, user_text: str, reply: str, voice: float | None = None,
                  searched: list[str] | None = None,
                  first_turn: bool = False, local_turn: bool = False) -> None:
        """Реплика человека и её ответ — в историю и на диск.

        `searched` сохраняет контекст поиска для ответа, чтобы последующие
        реплики не трактовали найденные данные как ответ без источника.

        first_turn — заход первой: `user_text` тут не его реплика, а
        пометка, и в память о нём такой ход не идёт (см. own_turns).
        """
        now = datetime.now().isoformat(timespec="seconds")
        turn = {"role": "user", "content": user_text, "at": now}
        if local_turn:
            turn["local"] = True
        if first_turn:
            turn["first"] = True
        elif voice is not None:
            turn["voice"] = round(float(voice), 2)
        self._history.append(turn)
        answer = {"role": "assistant", "content": reply, "at": now}
        if local_turn:
            answer["local"] = True
        if searched:
            answer["searched"] = searched
        self._history.append(answer)
        cancel = getattr(self, "_reply_cancel", None)
        if cancel is not None:
            self._voice_answer = (cancel, answer)
        self._undigested += 2
        self._trim_history()
        self._keep_history()

    def mark_voice_interrupted(self, cancel) -> None:
        """Озвучку оборвали после конца модели — не считаем полный текст услышанным.

        Память/чат могут держать замок: ожидание истории не задерживает голос.
        Пометка привязана к Event ответа, а не к последней строке истории.
        """
        record = getattr(self, "_voice_answer", None)
        if record is None or record[0] is not cancel:
            return
        def mark():
            with self._reply_lock:
                answer = record[1]
                if any(item is answer for item in self._history):
                    answer["content"] = "(ответ прерван пользователем)"
                    answer["interrupted"] = True
                    self._keep_history()
        if self._reply_lock.acquire(blocking=False):
            try:
                mark()
            finally:
                self._reply_lock.release()
        else:
            threading.Thread(target=mark, name="truba-interrupted-history", daemon=True).start()

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
    # `asked_for` подтверждает только подлинность цитаты, не смысл просьбы.
    # Для проверяемых действий модель отвечает на короткий вопрос: текущая
    # реплика и ближайший свежий обмен перед ней, без остальной истории.
    # Прямые голосовые команды из `core/commands.py` проходят собственный разбор.

    def _recent_exchange(self) -> str:
        """Ближайший обмен «человек — помощница», если он не старше 5 минут.

        Нужен судье, чтобы связать короткую реплику с тем, о чём шла речь.
        Старый разговор не берём: из него нельзя выводить новое разрешение.
        """
        if len(self._history) < 2:
            return "(нет)"
        previous_user, previous_answer = self._history[-2], self._history[-1]
        when = _at_of(previous_answer)
        now = datetime.now(when.tzinfo) if when is not None else None
        fresh = when is None or timedelta(0) <= now - when <= timedelta(minutes=5)
        if (fresh and previous_user.get("role") == "user"
                and previous_answer.get("role") == "assistant"):
            return (f"Человек: {previous_user.get('content', '')}\n"
                    f"Помощница: {previous_answer.get('content', '')}")
        return "(нет)"

    def _prejudge(self, ordered: list, said: str):
        """Судьи круга и поиск файла — все сразу, каждый в своём потоке.

        Отбор тот же, что в круге: без цитаты из реплики (`asked_for`) и без
        названного YouTube судья не зовётся вовсе. Поиск файла только читает
        диск, поэтому идёт вместе с судьёй: открыть файл можно лишь потом, а
        искать — можно сразу. Возвращает будущие по `call["id"]` (ответы судьи,
        поиск файла), отказы по `call["id"]` и пул потоков (`None`, если судей
        нет).
        """
        from core import files, hands

        ответы = []
        поиски = []
        отказы = {}
        for call in ordered:
            args = _args_of(call)
            if call["name"] in hands.GUARDED and not hands.asked_for(
                call["name"], said, because=str(args.get("because") or ""),
            ):
                отказы[call["id"]] = "quote"
                continue
            if call["name"] == hands.YT_NAME and not hands.names_youtube(said):
                отказы[call["id"]] = "youtube"
                continue
            if call["name"] in hands.JUDGED:
                ответы.append((call["id"], call["name"], args))
                if call["name"] == hands.FIND_NAME:
                    где = hands.find_file_words(call["args"])
                    if где is not None:
                        поиски.append((call["id"], *где))
        if not ответы:
            return {}, {}, отказы, None
        # Возврат к своему провайдеру берёт `_reply_lock`; здесь он у нас, а
        # поток судьи на нём встал бы, пока мы ждём его ответа.
        self._maybe_home()
        пул = ThreadPoolExecutor(max_workers=len(ответы) + len(поиски),
                                 thread_name_prefix="судья")
        return ({ид: пул.submit(self._judge_request, имя, арги, said, True)
                 for ид, имя, арги in ответы},
                {ид: пул.submit(files.find, слова, диск)
                 for ид, слова, диск in поиски},
                отказы,
                пул)

    def _judge_request(self, name: str, args: dict, said: str,
                       in_thread: bool = False) -> tuple[str | None, float, object]:
        """Спросить у облака, просил ли человек: `(ответ, секунды, расход)`.

        Только запрос: его можно унести в отдельный поток. События журнала
        и вердикт — в основном. `in_thread` — запрос из потока судьи: смена
        провайдера берёт `_reply_lock`, а его держит основной поток, который
        ждёт этот ответ. Поэтому из потока провайдер не меняется: при отказе
        региона или обрыве связи ответ — `None`, и основной поток повторяет
        проверку сам (`_really_asked`). Расход из потока тоже отдаётся
        основному: события идут оттуда. Без потока расход отмечает
        `_ask_plainly`, и третье значение — `None`.
        """
        from openai import APIConnectionError, PermissionDeniedError

        from core import hands

        action = hands.action_words(name, args)
        context = self._recent_exchange()
        if name in (hands.SET_REM_NAME, hands.CANCEL_REM_NAME):
            prompt = hands.REMINDER_JUDGE_PROMPT.format(
                context=context, said=str(said or ""), action=action)
        else:
            prompt = hands.JUDGE_PROMPT.format(
                context=context, said=str(said or ""), action=action)
        started = time.monotonic()
        spent = None
        try:
            if in_thread:
                answer = self._ask_plainly(
                    prompt,
                    max_tokens=5, note="проверка", timeout=8.0, in_thread=True,
                )
                spent = getattr(answer, "usage", None)
            else:
                answer = self._ask_plainly(
                    prompt,
                    max_tokens=5, note="проверка", timeout=8.0,
                )
            text = без_размышлений(answer.choices[0].message.content or "")
        except (PermissionDeniedError, APIConnectionError):
            # Из потока — повтор в основном, со сменой провайдера.
            text = None if in_thread else ""
        except Exception:
            text = ""  # облако молчало или упало — проверки нет
        return text, round(time.monotonic() - started, 2), spent

    def _judge_result(self, name: str, args: dict, said: str,
                      future) -> bool | None:
        """Вердикт судьи, запущенного заранее (`_prejudge`). Основной поток."""
        while not future.done():
            self._check_reply_cancelled()
            threading.Event().wait(.05)
        self._check_reply_cancelled()
        text, took, spent = future.result()
        if text is None:
            return self._really_asked(name, args, said)
        if spent is not None:
            self._tell("tokens", {**read_usage(spent), "calls": 1,
                                  "model": self.model, "note": "проверка"})
        return self._judge_verdict(name, args, text, took)

    def _judge_verdict(self, name: str, args: dict, text: str,
                       took: float) -> bool | None:
        """Разобрать ответ судьи и отметить проверку. Основной поток.

        True — да, делай. False — нет, не делай. None — облако не ответило
        (проверку пропускать нельзя: неизвестно, просил ли, значит не делаем).
        """
        from core import hands

        action = hands.action_words(name, args)
        if not text:
            self._tell("action_check", {"action": action, "answer": "ошибка",
                                        "took": took})
            return None
        yes = hands.judge_says_yes(text)
        self._tell("action_check", {"action": action,
                                    "answer": "да" if yes else "нет",
                                    "took": took})
        return yes

    def _really_asked(self, name: str, args: dict, said: str) -> bool | None:
        """Была ли в текущей реплике просьба выполнить действие."""
        text, took, _ = self._judge_request(name, args, said)
        return self._judge_verdict(name, args, text, took)

    # --- Память -----------------------------------------------------------

    def _ask_plainly(self, prompt: str, max_tokens: int, json_mode: bool = False,
                     note: str = "память", timeout: float | None = None,
                     in_thread: bool = False):
        """Разовый вопрос мимо разговора: без потока и без размышлений.

        Первый раз пробуем попросить не думать вслух. Не понял провайдер —
        запоминаем это и дальше спрашиваем обычным способом. OpenAI о том
        же просим своим параметром (reasoning_effort=none).

        json_mode — просим ответ строго JSON. Не принял провайдер — спросим
        без этого и разберём текст сами.

        note — чем помечен этот расход в журнале («память», «проверка»).
        timeout — сколько ждать облако; заданный уходит в сам запрос, чтобы
        проверка просьбы не висела, если облако молчит.

        in_thread — вопрос из потока судьи, пока основной поток держит
        `_reply_lock` и ждёт ответа: смена провайдера взяла бы тот же замок.
        Поэтому провайдер не меняется (отказ региона и обрыв уходят наружу),
        а расход не отмечается здесь — его отметит основной поток.
        """
        from openai import APIConnectionError, PermissionDeniedError

        if in_thread:
            return self._ask_plainly_here(prompt, max_tokens, json_mode, timeout)
        self._maybe_home()
        try:
            answer = self._ask_plainly_here(prompt, max_tokens, json_mode, timeout)
        except (PermissionDeniedError, APIConnectionError) as exc:
            if not self._fall_back(exc):
                raise
            answer = self._ask_plainly_here(prompt, max_tokens, json_mode, timeout)
        # Расход выжимки памяти учитывается отдельно от разговорных ответов.
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
            # Проверка имеет отдельный срок и не допускает повторов SDK.
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

        Пустой словарь различается через `self.did_ask`: `True` — выжимка
        выполнена без новых фактов, `False` — разговор был слишком коротким.
        """
        self.did_ask = False
        with self._reply_lock:
            count = min(self._undigested, len(self._history))
            fresh = list(self._history)[-count:] if count else []
            self._undigested = 0
        try:
            return self._digest(fresh)
        except Exception:
            # При отказе облака неразобранный кусок возвращается в очередь,
            # но не может быть длиннее оставшейся истории.
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
        # молчать про размышления, они съедят бюджет до ответа.
        self.did_ask = True
        answer = self._ask_plainly(ask, DIGEST_TOKENS, json_mode=True)
        choice = answer.choices[0]
        text = без_размышлений(choice.message.content or "")
        if not text:
            if choice.finish_reason == "length":
                raise RuntimeError(
                    "не хватило токенов на выжимку: модель думала дольше, "
                    f"чем позволено ({DIGEST_TOKENS})"
                )
            return {}

        return memory.apply_changes(memory_ops(text), seen=[f["text"] for f in snapshot])


def own_turns(turns: list[dict], threshold: float) -> list[dict]:
    """Реплики человека с ответами для выжимки памяти.

    Микрофон слышит также посторонние голоса и звук колонок. Реплики с
    низким сходством голоса исключаются, даже если ассистент ответил.
    Без оценки сходства (чат, короткая фраза) реплика остаётся.

    Заход первой (`first`) исключается: это собственная инициатива
    ассистента, из которой нельзя выводить факты о человеке.
    Локальные действия (`local`) остаются только контекстом разговора.
    """
    kept: list[dict] = []
    skip_reply = False
    for turn in turns:
        if turn.get("role") == "user":
            score = turn.get("voice")
            skip_reply = bool(turn.get("first") or turn.get("local")) or (
                isinstance(score, (int, float)) and threshold > 0 and score < threshold
            )
            if not skip_reply:
                kept.append(turn)
        elif not skip_reply and not turn.get("local"):
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
