r"""Руки: всё, что она делает на компе, когда модель позовёт.

26 сентября выяснилось, что запускать программы мало. Хозяин попросил скриншот,
мгновенный повтор и посмотреть на экран — три длинные фразы, а инструментов
у модели не было, и она отвечала, что не может. Короткие команды брал местный
разбор (core/commands.py), длинные уходили в облако.

Поэтому здесь инструменты действий: launch_app, close_app, take_screenshot,
save_moment, look_at_screen и youtube. Первые два работают сами по белому
списку apps.json (launcher.launch), остальные зовут те же действия, что и
голосовые команды: словарь actions мозгу отдаёт тот, кто его создал, и про
VoiceLoop мозг ничего не знает. YouTube — исключение из «мгновенных»: там
поиск ролика в сети, но отвечать на «включи ролик» всё равно нечем, кроме
короткой фразы, поэтому он в LOCAL и подтверждается без второго круга.

У каждого действия обязательный параметр `because` — точные слова хозяина из
его последней реплики, в которых он просил об этом. Проверяем их вхождением в
саму реплику (`asked_for`): «делать только то, о чём просят сейчас» проверяется
не корнями слов, а цитатой.

Честность проверяется здесь же: сказать «запустила» можно только после того,
как инструмент ответил, что получилось. Слова придут в ответ модели уже после
того, как она позвала инструмент, — иначе правило в промпте было бы пустым.

Белый список один и тот же, что у голосовых команд и кнопок телефона:
`apps.json`. Запускается только через `launcher.launch` — ни путей, ни команд
от модели не принимаем, а `enum` в описании собран из того же списка, так что
выбрать можно только заранее заведённое.
"""

import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Callable

import config

from core.youtube import KINDS  # виды просьбы к YouTube — список один на всех

NAME = "launch_app"
CLOSE_NAME = "close_app"
SHOT_NAME = "take_screenshot"
MOMENT_NAME = "save_moment"
LOOK_NAME = "look_at_screen"
YT_NAME = "youtube"
NOTE_NAME = "save_note"
READ_NAME = "read_notes"
DICTATE_NAME = "start_dictation"

# Ключи в brain.actions — те же слова, что в core/commands.py, чтобы словарь
# читался одинаково с обеих сторон.
KEY_OF = {SHOT_NAME: "screenshot", MOMENT_NAME: "moment", LOOK_NAME: "look"}
# Всё, что делается на компе. Интернет (core/web.py) сюда не входит: там нужен
# живой ответ из сети, а эти действия мгновенные. YouTube сюда тоже: поиск
# ролика занимает полторы секунды, а ответ всё равно не разговорный.
# `save_note` — записать мысль в файл: дело на месте, и второй круг в облако
# ушёл бы ради одного слова «записала». `read_notes` сюда НЕ входит: там
# модель нужна сама, чтобы пересказать прочитанное хозяину своими словами.
LOCAL = frozenset({NAME, CLOSE_NAME, SHOT_NAME, MOMENT_NAME, LOOK_NAME, YT_NAME,
                   NOTE_NAME, DICTATE_NAME})
# Инструменты, у которых спрашивается «просил ли он об этом в этой фразе».
# Проверяется цитатой: модель обязана передать в `because` слова хозяина из
# его последней реплики, и цитата ищется в самой реплике (см. `asked_for`).
# `read_notes` тоже: без проверки модель на «а ты можешь?» читала бы ему
# заметки, которых он не просил, и отвечала бы выдумкой.
GUARDED = LOCAL | {READ_NAME}
# Инструменты, перед которыми спрашиваем у модели: «он правда просил?».
# Запуск программы, закрытие программы, YouTube — то, что что-то ОТКРЫВАЕТ или
# ЗАКРЫВАЕТ на экране: ровно они в 27.09 открыли Discord на фразе «Они в
# Discord'е разговаривать будут» — то самое упоминание, а не просьба.
# Снимок экрана и взгляд на экран добавлены случаем 28.09, 23:22: хозяин
# рассказывал другу, сколько памяти ест программа, а модель сама позвала
# `look_at_screen` — и снимок его экрана ушёл в облако без всякой просьбы.
# Лишняя секунда проверки (~0.9 с) дешевле такого.
# Момент (`MOMENT_NAME`) сюда НЕ входит: клип повтора наружу никуда не
# уходит, в облако уйдёт только слово «нажала». Заметки и диктовка — тоже.
JUDGED = frozenset({NAME, CLOSE_NAME, YT_NAME, SHOT_NAME, LOOK_NAME})

# Сколько секунд снимок лежит на телефоне, пока не уберётся сам.
# Снимок по просьбе — для хозяина: есть время рассмотреть, что у него там.
SHOT_HIDE = 15.0
# Взгляд на экран — снимок для неё, а не для него: телефон показывает коротко,
# чтобы хозяин видел, что она посмотрела, и не держал чужой кадр на себе.
LOOK_HIDE = 6.0

# Правило хозяина (27.09): «я говорю найди на ютубе или открой на ютубе,
# остальное всё — поиск в интернет». Модель же «искала всё на YouTube»: на
# вопрос «что за мем про кошечку и трубу?» открыла выдачу YouTube. Поэтому
# YouTube модели разрешён, только если в реплике есть само слово — в любом
# написании распознавания («ютуб», «ютьюб», «YouTube»). Это не заплатка на
# формулировку, а его договорённость: одно слово, которое он сам говорит.
YT_WORDS = ("ютуб", "ютьюб", "ютюб", "youtube", "you tube")


def names_youtube(said: str) -> bool:
    """Сказал ли хозяин в реплике «ютуб» — в любом написании."""
    text = str(said or "").lower().replace("ё", "е")
    return any(word in text for word in YT_WORDS)


def youtube_not_named() -> str:
    """Ответ модели, когда YouTube позвали без слова «ютуб» в реплике."""
    return _error(
        "YouTube открывается, только когда хозяин сам говорит «ютуб». Он не "
        "говорил — не открывай. Хочет что-то найти или узнать — поищи в "
        "интернете (web_search) или ответь сама"
    )

Event = Callable[[str, object], None]
# Отдаёт сервер телефона или None. Именно функция, а не сам сервер: голосовой
# цикл поднимает его позже, при загрузке моделей.
Phone = Callable[[], object]


# Обязательный параметр у каждого действия: точные слова хозяина из его
# последней реплики, в которых он просил именно об этом. Раньше проверка шла
# по корням слов («откр», «скрин», «клип») и по названию программы, и модель
# проходила её выдуманной цитатой. Теперь цитата и есть проверка: `asked_for`
# ищет `because` в его последней реплике целиком. Нет в реплике такой просьбы —
# не зови инструмент вовсе.
BECAUSE = {
    "type": "string",
    "description": "ТОЧНЫЕ слова хозяина из его последней реплики, в которых "
                   "он просит именно об этом, без пересказа. Если в последней "
                   "реплике такой просьбы нет — не зови инструмент вовсе",
}


def _spec(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _with_because({"type": "object", "properties": {},
                                          "required": []}),
        },
    }


def _with_because(parameters: dict) -> dict:
    """Добавляет обязательный `because` к объявлению инструмента."""
    parameters["properties"]["because"] = dict(BECAUSE)
    required = parameters.setdefault("required", [])
    if "because" not in required:
        required.append("because")
    return parameters


SHOT_TOOL = _spec(
    SHOT_NAME,
    "Снять снимок экрана и показать картинку хозяину на телефон. Вызывай, "
    "когда он прямо просит скриншот, снимок, «заскринь», «сфоткай экран». "
    "Сказать «готово, скинула на телефон» можно только после того, как "
    "инструмент ответит, что снимок получился.",
)

MOMENT_TOOL = _spec(
    MOMENT_NAME,
    "Сохранить клип последних минут игры — мгновенный повтор NVIDIA. "
    "Вызывай, когда он прямо просит сохранить момент, клип, «клипани». "
    "Если в ответе написано, что повтор был выключен, скажи честно: клип не "
    "сохранился, повтор включила.",
)

LOOK_TOOL = _spec(
    LOOK_NAME,
    "Посмотреть, что сейчас на экране, и ответить по увиденному: снимок "
    "уходит тебе, а не ему. Вызывай только когда он прямо просит посмотреть "
    "на экран — «что у меня на экране», «глянь на экран». Если непонятно, о "
    "чём он, переспроси: снимок уходит в облако, лишний раз его слать нельзя. "
    "Слова «попью», «посмотрю» и подобные — это не просьба смотреть.",
)

# Объявление не зависит от `apps.json`: YouTube в нём одна кнопка, и ролики с
# каналами на ней запускать нечем. Ставится сразу после `close_app`, чтобы
# порядок набора не зависел от того, что в списке программ.
YT_TOOL = {
    "type": "function",
    "function": {
        "name": YT_NAME,
        "description": (
            "Включить ролик, открыть канал или поиск на YouTube — ТОЛЬКО "
            "когда хозяин сам сказал «ютуб»: «включи на ютубе …», «открой на "
            "ютубе канал …», «найди на ютубе …». Без слова «ютуб» — не сюда: "
            "найти или узнать что-то — это поиск в интернете. kind: video — включить ролик, channel — открыть канал, search "
            "— показать выдачу, ничего не выбирая. В query передай его словами, "
            "как он сказал, без своего пересказа: ролик ищется по смыслу фразы. "
            "Сказать «включила» или «открыла» можно только после ответа "
            "инструмента — там поле text это уже готовая короткая фраза, "
            "проговори её и ничего не добавляй. Слово «клип» («сохрани клип») — "
            "это save_moment, а «включи клип такой-то группы» — сюда."
        ),
        "parameters": _with_because({
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Что искать: словами хозяина"},
                "kind": {"type": "string", "enum": list(KINDS),
                         "description": "video — ролик, channel — канал, "
                                        "search — выдача"},
                "again": {"type": "boolean",
                          "description": "true — только если он прямо просит "
                                         "открыть ещё раз: «открой ещё раз», "
                                         "«заново», «снова». Иначе false, и "
                                         "тот же ролик второй раз не "
                                         "открывается"},
            },
            "required": ["query"],
        }),
    },
}

NOTE_TOOL = {
    "type": "function",
    "function": {
        "name": NOTE_NAME,
        "description": (
            "Записать заметку в папку хозяина, когда он в разговоре говорит "
            "«запиши это в заметки», «сохрани то, что ты сказала», «запиши "
            "мысль про …». Текст заметки пиши сама, своими словами, из того, "
            "что вы вместе обсудили: раздела, темы и заголовка в инструменте "
            "нет — он сам разберёт твой текст, а тебе нужен готовый к "
            "сохранению текст (абзацы, без «заметка:» и без повторов). "
            "Пиши только то, что сказал хозяин. Никаких своих служебных "
            "строк вроде «жду диктовку», «будут замечания» — если текста ещё "
            "нет, зови start_dictation. Сказать «записала» можно только после "
            "ответа инструмента — там поле text это уже готовая короткая "
            "фраза, произнеси её и ничего не добавляй."
        ),
        "parameters": _with_because({
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "Текст заметки: что записать, своими "
                                        "словами, без команды хозяина"},
                "topic": {"type": "string",
                          "description": "Тема, если он её назвал: «Мастер и "
                                         "Маргарита», «Труба». Не назвал — пусто"},
                "section": {"type": "string",
                            "description": "Раздел: Книги, Проекты, Идеи, "
                                           "Разное. Не ясно — пусто"},
                "title": {"type": "string",
                          "description": "Заголовок записи, 3–7 слов"},
            },
            "required": ["text"],
        }),
    },
}

READ_TOOL = {
    "type": "function",
    "function": {
        "name": READ_NAME,
        "description": (
            "Прочитать, что хозяин уже записал в заметки по теме: «что я "
            "писал про книгу?», «прочитай мои мысли по проекту». Отдаёт "
            "ему текст записей с датами, а пересказать его — его дело, "
            "своими словами. Такой темы нет — в ответе будет список "
            "существующих, тогда переспроси у него, какую имел в виду."
        ),
        "parameters": _with_because({
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Тема словами хозяина: «книга Мастер "
                                         "и Маргарита», «проект Труба»"},
            },
            "required": ["query"],
        }),
    },
}

DICTATE_TOOL = {
    "type": "function",
    "function": {
        "name": DICTATE_NAME,
        "description": (
            "Включает диктовку заметки: всё, что хозяин скажет дальше, "
            "копится в заметку, пока он не скажет «всё» или не замолчит. "
            "Зови, когда он просит записать, сделать или дописать заметку, "
            "а самого текста в этой фразе ещё нет. Если текст уже сказан "
            "в этой же фразе — save_note. Не говори «диктуй» без этого "
            "инструмента: без него запись не идёт."
        ),
        "parameters": _with_because({
            "type": "object",
            "properties": {
                "hint": {"type": "string",
                         "description": "Куда писать, его словами: «Проекты / "
                                        "Труба», «по книге Мастер и Маргарита». "
                                        "Не назвал — пусто"},
            },
            "required": [],
        }),
    },
}


# Порядок объявления не меняется: одинаковый набор в каждом запросе нужен не
# только модели, но и кешу OpenAI — запрос с тем же началом стоит дешевле.
ACTION_TOOLS = {
    "screenshot": SHOT_TOOL,
    "moment": MOMENT_TOOL,
    "look": LOOK_TOOL,
}


def action_tools(actions: dict) -> list[dict]:
    """Объявления инструментов действий — одни и те же в каждом запросе.

    Чего в `actions` нет — того инструмента нет: пустой словарь значит, что
    действия в этой сборке не подключены, и врать про них нельзя.
    """
    return [
        spec for key, spec in ACTION_TOOLS.items() if callable(actions.get(key))
    ]


def read_list() -> list[dict]:
    """Программы из `apps.json` — тот же список, что у кнопок телефона."""
    from core import launcher

    try:
        return launcher.read_list()
    except Exception:
        return []


def _named(apps: list[dict]) -> list[tuple[str, str]]:
    """Пары (id, название) из `apps.json` — общий список для обоих инструментов."""
    return [
        (str(app.get("id", "")).strip(), str(app.get("title", "")).strip() or app_id)
        for app in apps
        for app_id in [str(app.get("id", "")).strip()]
        if app_id
    ]


def _app_param(named: list[tuple[str, str]]) -> dict:
    return _with_because({
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "enum": [app_id for app_id, _ in named],
                "description": (
                    "Программа из списка — строго как написано в скобках, "
                    "например chatgpt на «Chat GPT» или youtube на «ютуб»"
                ),
            }
        },
        "required": ["app"],
    })


def tool(apps: list[dict]) -> dict:
    """Объявление инструмента для модели. `enum` — id программ из `apps.json`."""
    named = _named(apps)
    if not named:
        raise ValueError("запускать нечего: список программ пуст")

    return {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Запустить программу на компьютере хозяина. Список другой не "
                "будет: " + ", ".join(f"{title} ({app_id})" for app_id, title in named) +
                ". Сопоставь, как он назвал программу вслух, с её названием в "
                "скобках, и передай именно его в app. Ничего, чего здесь нет, "
                "запустить нельзя: тогда прямо скажи, что не можешь, и назови, "
                "что можешь. Вызывай, только когда он просит запустить или "
                "открыть одну из этих программ."
            ),
            "parameters": _app_param(named),
        },
    }


def close_tool(apps: list[dict]) -> dict:
    """Объявление закрытия. Тот же список и тот же `enum`, что у запуска.

    Ставится сразу после `launch_app` и всегда: порядок набора инструментов
    в каждом запросе одинаковый, на этом держится кеш OpenAI.
    """
    named = _named(apps)
    if not named:
        raise ValueError("закрывать нечего: список программ пуст")

    return {
        "type": "function",
        "function": {
            "name": CLOSE_NAME,
            "description": (
                "Закрыть программу на компьютере хозяина, когда он прямо "
                "просит закрыть или выключить её. Список тот же, что у "
                "launch_app: " + ", ".join(
                    f"{title} ({app_id})" for app_id, title in named
                ) + ". Передай в app то же, что и там, — id из скобок. "
                "Закрытие необратимо: зови инструмент только когда просьба "
                "прямая, и говорить «закрыла» можно только после его ответа. "
                "Не вышло — скажи честно, что не закрылось и почему."
            ),
            "parameters": _app_param(named),
        },
    }


def _error(text: str) -> str:
    """Ошибка в том же виде, что у `web.run_tool` — её ждёт и цикл в brain."""
    return json.dumps({"error": text}, ensure_ascii=False)


# --- Просили ли об этом в этой фразе ---------------------------------------
# 26 сентября: инструменты даются в каждом запросе, и на «М-м, кстати, знаешь,
# что ещё надо?» она второй раз открыла YouTube — повторила просьбу из прошлой
# реплики. Проверять это по корням слов («откр», «скрин», «клип») и по названию
# программы было ненадёжно: 27.09 на жалобу «и ты ещё не договариваешь название
# ролика, да?» модель позвала `youtube` в третий раз — слово «ролик» есть, а
# просили только объяснить.
#
# Теперь проверка простая и одна: модель обязана процитировать `because` —
# точные слова хозяина из его последней реплики, — а мы ищем эту цитату в
# самой реплике. Нет цитаты, нет и действия: `not_asked`.


def _plain(text: str) -> str:
    """Приводит строку к виду, в котором цитата и фраза сравнимы."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def asked_for(name: str, said: str, apps: list[dict] | None = None,
              because: str = "") -> bool:
    """Просил ли хозяин в этой фразе именно об этом — по его же цитате.

    `because` — то, что модель назвала словами из `said`. Считаем, что просили,
    только если цитата непустая, в ней есть слово от трёх букв (иначе проверку
    проходит слово «ну») и она целиком входит в фразу: цитата из прошлой реплики,
    выдуманная или пересказанная не входит.

    `apps` оставлен в сигнатуре для совместимости с вызовами: по названию
    программы просьба больше не ищется.
    """
    if name not in GUARDED:
        return True  # не действие на компе — не наше дело
    quote = _plain(because)
    if not quote or not _plain(said):
        return False
    if not any(len(word) >= 3 for word in quote.split()):
        return False
    return quote in _plain(said)




def not_asked(name: str) -> str:
    """Ответ модели, когда в этой фразе об этом не просили."""
    return _error(
        "в этой реплике хозяин об этом не просил — не делай и не говори, что "
        "сделала. Ответь на то, что он сказал сейчас; если непонятно, переспроси."
    )


# --- А просил ли он на самом деле -------------------------------------------
# 27.09, 22:03. Проверка выше (`asked_for`) смотрела только, что цитата из
# `because` СКАЗАНА в текущей реплике. Но хозяин лишь упомянул, что друзья
# будут разговаривать в Discord'е, — Discord назван как МЕСТО, а
# модель процитировала фразу целиком, цитата в неё вошла, и Discord запустился.
# Упоминание программы ≠ просьба её открыть.
#
# Уговорами в описании инструмента («зови, только если просит») это не лечится:
# модель их и так нарушает. Смысл понимает модель — значит, спрашиваем её же,
# отдельно и коротко: одна фраза хозяина и одно действие словами, ответ «да»
# или «нет». Без характера, без истории разговора, без списков слов-триггеров
# в коде. Проверено на живой модели: 30 из 30 верно, ~0.9 с.
#
# В тот же вечер модель «искала всё на YouTube»: на вопрос «а что за мем про
# кошечку и трубу?» открыла выдачу YouTube. Отсюда фраза про YouTube: он —
# только когда речь о ролике, клипе, канале или о самом YouTube. И действие
# судье описывается по виду (ролик / канал / поиск): «открой канал Джо Спина»
# при действии «искать видео» судья честно отвечал «нет». С этим — 46 из 48 на
# 16 фразах трижды; оба промаха — «открой поиск и найди аниме …», где правда
# непонятно, интернет это или YouTube.
JUDGE_PROMPT = (
    "Ты проверяешь одно: просит ли человек в этой реплике сделать действие "
    "прямо сейчас. Просьба — когда он велит или просит это сделать (в любой "
    "форме: «открой», «можешь открыть?», «давай …», «врубай»). Не просьба — "
    "упоминание программы, рассказ, жалоба, вопрос о том, что ты умеешь. "
    "Если он просит о ДРУГОМ деле (поговорить с кем-то, рассказать, "
    "подождать), а программа названа лишь как место, где это будет, — это не "
    "просьба запустить её. YouTube — только когда речь о ролике, клипе, "
    "канале или о самом YouTube; просьба что-то найти или узнать без этого — "
    "не просьба открыть YouTube. Посмотреть на экран или снять его — только "
    "когда он сам просит об этом: «глянь на экран», «что у меня на экране», "
    "«сделай скриншот». Рассказ о компьютере, о памяти, о программах и "
    "разговорам о них просьбой смотреть не считается. Ответь одним словом: "
    "да или нет.\n\n"
    "Реплика: «{said}»\n"
    "Действие: {action}"
)


def action_words(name: str, args: dict) -> str:
    """Действие словами — так его понимает судья, а не как имя инструмента."""
    if not isinstance(args, dict):
        args = {}
    if name == YT_NAME:
        # По виду просьбы, как её выполнит core/youtube.py (по умолчанию —
        # ролик): судья сравнивает с тем, что хозяин просил, и «открой канал»
        # при «искать видео» считал другой просьбой.
        query = str(args.get('query') or '').strip()
        kind = str(args.get('kind') or 'video')
        if kind == "channel":
            return f"открыть на YouTube канал: {query}"
        if kind == "search":
            return f"показать поиск на YouTube: {query}"
        return f"включить на YouTube ролик: {query}"
    if name == CLOSE_NAME:
        return f"закрыть программу {str(args.get('app') or '').strip()}"
    if name == NAME:
        return f"запустить программу {str(args.get('app') or '').strip()}"
    if name == SHOT_NAME:
        return "сделать снимок экрана и показать его на телефоне"
    if name == LOOK_NAME:
        return "посмотреть на его экран (снимок экрана уйдёт тебе в облако)"
    return str(name or "")


def judge_says_yes(text: str) -> bool:
    """Ответ судьи: «да» — True. Всё остальное (молчание, «не знаю») — False."""
    answer = (text or "").strip().lower()
    answer = answer.strip(" \t\r\n.,!?:;«»\"'()-—…")
    return answer.startswith("да")


def not_really_asked(name: str) -> str:
    """Ответ модели, когда программу упомянули, но не просили её открыть."""
    return _error(
        "хозяин упомянул это, но не просил сделать — не делай и не говори, "
        "что сделала. Ответь на то, что он сказал сейчас"
    )


def check_failed(name: str) -> str:
    """Ответ модели, когда проверку просьбы провести не вышло."""
    return _error(
        "не получилось проверить просьбу — коротко переспроси хозяина, "
        "сделать ли это, и не делай, пока не ответит"
    )


def _ok(**fields) -> str:
    """Успешный ответ инструмента: тот же JSON, что ждёт цикл в brain."""
    return json.dumps(fields, ensure_ascii=False)


# --- Не делать одно и то же дважды ------------------------------------------
# 27.09, 11:23–11:24 — не только YouTube. Модель увидела похожую просьбу или
# жалобу и повторила действие: тот же ролик открылся трижды за минуту. Повтор
# ролика — частный случай, и правильное место защиты у всех действий одно: там,
# где действие выполняется. Уговорами в промпте («не повторяй») это не лечится.
#
# Память общая на ВЫЗОВЫ ИНСТРУМЕНТОВ МОДЕЛЬЮ. Голосовые команды из
# core/commands.py сюда не ходят: «сделай скриншот» дважды подряд — это две
# просьбы и два снимка, и запрещать второе нельзя.
#
# Ключ — имя инструмента плюс нормализованные аргументы: у `launch_app` это
# программа, у `save_note` — текст, у `youtube` — запрос (а по адресу повтор
# ловит ещё и сам core/youtube.py).
_did_lock = threading.Lock()
_did: dict[str, float] = {}
# Что сказать вслух, когда действие уже было. Короткое: «уже сделала» — и всё.
REPEAT_SAID = "Уже сделала."


def _normalized(name: str, arguments: str) -> str:
    """Ключ памяти: имя инструмента и его аргументы мелкими буквами."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    parts = []
    for key in sorted(args):
        value = args[key]
        if isinstance(value, str):
            value = re.sub(r"\s+", " ", value).strip().lower()
        parts.append(f"{key}={value}")
    return name + "|" + "|".join(parts)


def seconds_since_done(name: str, arguments: str) -> float | None:
    """Сколько секунд назад модель делала это. None — не делала или не считаем.

    None же, когда прошло больше окна из `config.ACTION_REPEAT_SECONDS`: там
    повтор уже не повтор, а новая просьба. Без этой проверки защита держалась
    бы до перезапуска, и «сделай скриншот» через час не работал бы вовсе.
    """
    window = float(config.ACTION_REPEAT_SECONDS.get(name, 0) or 0)
    if window <= 0:
        return None
    with _did_lock:
        when = _did.get(_normalized(name, arguments))
    if when is None:
        return None
    ago = time.monotonic() - when
    return ago if ago < window else None


def remember_done(name: str, arguments: str) -> None:
    """Пометить: это действие только что выполнено."""
    with _did_lock:
        _did[_normalized(name, arguments)] = time.monotonic()


def forget_done() -> None:
    """Забыть недавние действия. Ими пользуются тесты."""
    with _did_lock:
        _did.clear()


def repeated(name: str, arguments: str, again: bool = False) -> str:
    """Ответ модели, если это действие уже делали недавно. '' — можно делать.

    `again` пробивает защиту — он есть только у `youtube` («открой ещё раз»).
    """
    if again:
        return ""
    ago = seconds_since_done(name, arguments)
    if ago is None:
        return ""
    return _ok(
        ok=True, repeat=True, text=REPEAT_SAID,
        note=f"уже сделано {int(ago)} с назад, второй раз не делаю",
    )


def run_tool(
    arguments: str,
    on_event: Event | None = None,
    apps: list[dict] | None = None,
) -> str:
    """Выполняет вызов модели. Всегда возвращает строку для сообщения tool."""

    def emit(kind: str, payload) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    known = [
        str(app.get("id", "")).strip()
        for app in (read_list() if apps is None else apps)
        if str(app.get("id", "")).strip()
    ]
    app_id = str(args.get("app", "")).strip()
    if app_id not in known:
        return _error(
            f"нет такой программы: {app_id or '(пусто)'}. Есть только: "
            + (", ".join(known) or "(список пуст)")
            + ". Ничего, чего здесь нет, запустить нельзя."
        )

    # Тот же запуск, что она только что сделала (окно — config):
    # второй раз запускать незачем, хозяин получит «Уже сделала».
    already = repeated(NAME, arguments)
    if already:
        return already

    from core import launcher

    try:
        ok, what = launcher.launch(app_id)
    except Exception as exc:
        ok, what = False, f"{type(exc).__name__}: {exc}"
    # Тот же вид события, что у голосовой команды и у кнопки телефона:
    # в журнале пульта это «программа запущена: …».
    emit("launched" if ok else "launch_failed", what)
    if not ok:
        return _error(f"не вышло: {what}")
    remember_done(NAME, arguments)
    return _ok(ok=True, app=app_id, text=f"запущено: {what}")


def run_close(
    arguments: str,
    on_event: Event | None = None,
    apps: list[dict] | None = None,
) -> str:
    """Закрывает программу по вызову модели. Строка — для сообщения tool.

    Тот же белый список, тот же вид ответа и то же правило честности: сказать
    «закрыла» модель вправе только после того, как инструмент ответил, что
    вышло. Отказ возвращается как `{"error": ...}` — и дальше модель
    пересказывает его хозяину своими словами, а не выдумывает результат.
    """

    def emit(kind: str, payload) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    known = [
        str(app.get("id", "")).strip()
        for app in (read_list() if apps is None else apps)
        if str(app.get("id", "")).strip()
    ]
    app_id = str(args.get("app", "")).strip()
    if app_id not in known:
        return _error(
            f"нет такой программы: {app_id or '(пусто)'}. Есть только: "
            + (", ".join(known) or "(список пуст)")
            + ". Ничего, чего здесь нет, закрыть нельзя."
        )

    # Тот же щит, что у запуска: закрыть то, что уже закрыто, модель могла
    # решить по жалобе «у тебя всё ещё открыто», и второй раз закрывать нечего.
    already = repeated(CLOSE_NAME, arguments)
    if already:
        return already

    from core import launcher

    try:
        ok, what = launcher.close(app_id)
    except Exception as exc:
        ok, what = False, f"{type(exc).__name__}: {exc}"
    # Тот же вид события, что у голосовой команды: в журнале пульта это
    # «программа закрыта: …».
    emit("closed" if ok else "close_failed", what)
    if not ok:
        return _error(f"не вышло: {what}")
    remember_done(CLOSE_NAME, arguments)
    return _ok(ok=True, app=app_id, text=f"закрыто: {what}")


def run_youtube(arguments: str, on_event: Event | None = None) -> str:
    """Открывает ролик, канал или выдачу YouTube. Строка — для сообщения tool.

    Делает ровно то же, что голосовая команда «включи на ютубе …»: тот же
    `core/youtube.py`, тот же вид ответа. Ролик не нашёлся — откроется страница
    поиска, и в `text` будет честно сказано, что нашлась только она: выдуманный
    ролик хозяину дороже лишнего слова.
    """

    def emit(kind: str, payload) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    from core import youtube

    # Повтор пробивает только прямой просьбой хозяина: «открой ещё раз».
    again = bool(args.get("again"))
    try:
        what = youtube.act(str(args.get("query", "")), str(args.get("kind", "video")),
                           again=again)
    except Exception as exc:
        return _error(f"не вышло: {type(exc).__name__}: {exc}")
    if not what.get("ok"):
        return _error(str(what.get("error", "не вышло")))
    emit("youtube", youtube.journal(what))
    if what.get("repeat"):
        # Ролик тот же и только что открыт. Вслух — «Он уже открыт.», а модели
        # объяснение из `note`: иначе она позовёт инструмент ещё раз или
        # скажет хозяину «включила», а это будет враньём.
        return _ok(ok=True, repeat=True, kind=what.get("kind", ""),
                   url=what.get("url", ""), text="Он уже открыт.",
                   note=str(what.get("note", "")))
    return _ok(ok=True, kind=what.get("kind", ""), url=what.get("url", ""),
               text=str(what.get("text", "")))


def run_note(arguments: str, on_event: Event | None = None) -> str:
    """Записывает заметку по вызову модели. Строка — для сообщения tool.

    Текст пишет она сама, поэтому сырого диктовки тут нет: в файле
    лежит ровно то, что она сочла нужным. Слово «запиши» в разговоре до
    сюда обычно не доходит — короткие фразы разбирает voice_loop, — но
    «а сохрани то, что ты сейчас сказала» остаётся целиком в облаке.
    """

    def emit(kind: str, payload) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    text = str(args.get("text", "")).strip()
    if not text:
        return _error("пустую заметку писать нечего")

    # Ключ памяти — по самому тексту: тот же текст, записанный минуту назад,
    # это тот же файл, а не новая заметка.
    already = repeated(NOTE_NAME, json.dumps({"text": text}, ensure_ascii=False))
    if already:
        return already

    from core import notes

    # Тема названа — сначала ищем уже существующую («по книге Мастер и
    # Маргарита» → Книги / Мастер и Маргарита), чтобы не плодить дубли;
    # нет такой — заводим новую; ничего не названо — во «Входящие».
    section = str(args.get("section", "")).strip()
    topic = str(args.get("topic", "")).strip()
    title = str(args.get("title", "")).strip() or " ".join(text.split()[:6])
    found = None
    if topic:
        try:
            found = _find_topic(f"{section} {topic}".strip()) or _find_topic(topic)
        except Exception:
            found = None
    try:
        if found is not None:
            section, topic = found
        elif topic:
            section = notes.soft_name(section or "Разное")
            topic = notes.soft_name(topic)
        else:
            section, topic = "Разное", "Входящие"
        placed = notes.add(section, topic, title, text)
    except Exception as exc:
        return _error(f"не записала: {type(exc).__name__}: {exc}")
    emit("note", f"записала в «{section} / {topic}» ({len(text.split())} слов)")
    remember_done(NOTE_NAME, json.dumps({"text": text}, ensure_ascii=False))
    return _ok(ok=True, path=str(placed),
               text=f"Записала в «{section} — {topic}».")


def run_read_notes(arguments: str, on_event: Event | None = None) -> str:
    """Отдаёт модели записи темы. Строка — для сообщения tool.

    Раздел и тему ищем сами по словам хозяина: он сказал «по книге Мастер
    и Маргарита», а на диске лежит папка «Книги» и файл «Мастер и
    Маргарита». Не нашли — отдаём список того, что есть, чтобы модель
    переспросила, а не выдумала содержимое.
    """

    def emit(kind: str, payload) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    from core import notes

    query = str(args.get("query", "")).strip()
    if not query:
        return _error("тема не названа")

    try:
        found = _find_topic(query)
    except Exception as exc:
        return _error(f"не посмотрела заметки: {type(exc).__name__}: {exc}")
    if found is None:
        emit("note", f"темы «{query}» нет")
        return _error("такой темы нет, есть только: " + _topic_list())
    section, topic = found
    try:
        text = notes.topic_text(section, topic)
    except Exception as exc:
        return _error(f"не прочитала: {type(exc).__name__}: {exc}")
    if not text.strip():
        return _error(f"в теме «{topic}» пока пусто")
    emit("note", f"прочитала «{section} / {topic}» ({len(text)} знаков)")
    return json.dumps(
        {"ok": True, "section": section, "topic": topic, "text": text},
        ensure_ascii=False,
    )


def run_dictation(arguments: str, actions: dict | None = None) -> str:
    """Включает режим диктовки по вызову модели. Строка — для сообщения tool.

    Здесь нужен `hint`: хозяин сказал «допиши в заметку про трубу» — куда
    писать, он объяснил в той же фразе, а дальше будет только диктовать.

    Голос выключен — честная ошибка, а не «записала»: модель получит её и
    ответит хозяину сама, попросив текст в одну фразу.
    """
    from core.voice_loop import DICTATION_START

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _error("аргументы не разобрались как JSON")

    action = (actions or {}).get("dictation")
    if not callable(action):
        return _error("диктовка сейчас недоступна")

    hint = str(args.get("hint", "") or "").strip()
    try:
        started = bool(action(hint))
    except Exception as exc:
        return _error(f"не вышло: {type(exc).__name__}: {exc}")
    if not started:
        return _error("Голос выключен — диктовать некуда. Скажи текст сразу, "
                      "я запишу")
    return _ok(ok=True, hint=hint, text=DICTATION_START)


def _folded(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Zа-яё]+", " ", (text or "").lower().replace("ё", "е")).strip()


def _topic_list() -> str:
    """Список тем для ответа модели одной строкой."""
    from core import notes

    parts = [f"{s['name']} / {t['name']}"
             for s in notes.sections() for t in s["topics"]]
    return ", ".join(parts) or "(пока пусто)"


def _find_topic(query: str):
    """Ищет тему по словам хозяина.

    Он говорит «что я писал по книге Мастер и Маргарита», а на диске лежит
    папка «Книги» и файл «Мастер и Маргарита»: слово «книга» в «книгах» не
    входит дословно, и искать только по полному вхождению значило бы всегда
    говорить «такой темы нет» — а хозяин её назвал. Поэтому смотрим, сколько
    его слов нашлось в теме, и берём самую подходящую: сначала ту, где
    нашлись все, потом самую короткую по имени.

    Ни одного слова не нашлось — возвращаем None: модель получит список
    существующих и переспросит, а не перескажет то, чего он не писал.
    """
    from core import notes

    wanted = _folded(query)
    if not wanted:
        return None
    # Слова короче трёх букв («по», «и», «я») в имя темы не входят и
    # ничего не различают — их в счёт не берём.
    words = [w for w in wanted.split() if len(w) > 2]
    best = None
    best_score = (0, 0)
    for section in notes.sections():
        for topic in section["topics"]:
            name = _folded(topic["name"])
            if name == wanted:
                return section["name"], topic["name"]
            where = section["name"].lower().replace("ё", "е")
            hits = [w for w in words
                    if w in name or w in _folded(where)]
            if not hits:
                continue
            # Сначала темы, где нашлись все слова, потом — самая короткая
            # по имени: «Книги» и «Книги и статьи» иначе спорили бы.
            score = (len(hits), -len(topic["name"]))
            if score > best_score:
                best_score = score
                best = (section["name"], topic["name"])
    return best


# --- Короткое подтверждение вместо второго круга ------------------------
# 26 сентября: на «открой ютуб» модель в первом круге зовёт launch_app, мы
# выполняем, а второй круг уходит в облако только ради слова «открыла» — это
# +2…5 с на пустом месте. Здесь сказать это можно самим: действие уже сделано
# и уже ответило, что получилось. Взгляд на экран (look_at_screen) сюда не
# попадает намеренно: там модель нужен сам снимок, чтобы ответить по нему.
CONFIRM = {
    NAME: ("Открыла {title}.", "Запустила {title}.", "Готово, {title} открыт."),
    CLOSE_NAME: ("Закрыла {title}.", "Выключила {title}.", "Готово, {title} закрыт."),
    SHOT_NAME: ("Готово, снимок на телефоне.", "Снимок уже на телефоне."),
    MOMENT_NAME: ("Клип сохранён.", "Готово, клип сохранён."),
    # У YouTube своя фраза, а не вариант по имени программы: она приходит
    # полем `text` из ответа инструмента — там уже сказано, что именно
    # нашлось, и это «Включила: <название>» короче любого «Открыла YouTube».
    YT_NAME: ("{text}",),
    # Заметка — так же: поле `text` уже содержит «Записала в «…»» с
    # разделом и темой, а вариант по одному имени тут ничего не значит.
    NOTE_NAME: ("{text}",),
    # Диктовка: поле `text` — это ровно то, что хозяину и нужно услышать
    # («Диктуй. Скажешь «всё» — запишу.»), и говорит это голос, не модель.
    DICTATE_NAME: ("{text}",),
}


def _title_of(app_id: str, apps: list[dict]) -> str:
    for app in apps:
        if str(app.get("id", "")).strip() == app_id:
            return str(app.get("title", "")).strip() or app_id
    return app_id


def _result_dict(results: list[str] | None, index: int) -> dict:
    """Разобранный ответ инструмента по номеру, или пустой словарь."""
    if not results or index >= len(results):
        return {}
    try:
        data = json.loads(results[index])
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _result_text(results: list[str] | None, index: int) -> str:
    """Поле `text` из ответа инструмента по номеру, или пусто."""
    return str(_result_dict(results, index).get("text", ""))


def _is_repeat(results: list[str] | None, index: int) -> bool:
    """Отвечает ли инструмент «уже сделано», а не «сделано»."""
    return bool(_result_dict(results, index).get("repeat"))


def _lower_first(text: str) -> str:
    # Только русское слово: «Закрыла» → «закрыла», а «Telegram» — название,
    # его не трогаем.
    if text and "А" <= text[0] <= "Я":
        return text[:1].lower() + text[1:]
    return text


def _join(parts: list[str]) -> str:
    """Склеить части в одну фразу: «Открыла YouTube и закрыла Discord».

    Две вещи по чужим вариантам CONFIRM. Точка внутри фразы не нужна: каждый
    вариант рассчитан на то, что его скажут самого по себе, и в общей фразе
    она читалась бы как «YouTube.» — с остановкой посреди мысли. И заглавная
    нужна только в первой части: «Открыла YouTube и Закрыла Discord» — это уже
    не фраза, а список. Поэтому точка снимается со всех, а строчная буква
    остаётся лишь у первой.
    """
    if len(parts) == 1:
        return parts[0]
    return " и ".join([parts[0]] + [_lower_first(p) for p in parts[1:]]) + "."


def confirm(calls: list[dict], apps: list[dict] | None = None,
            results: list[str] | None = None) -> str:
    """Одна короткая фраза на круг, где были только действия на компе.

    `calls` — словари `{"name": ..., "args": ...}` в том порядке, в каком
    модель звала инструменты. Название программы берётся из `apps.json`, как
    везде: на «закрой дискорд» хозяин должен услышать «Закрыла Discord», а не
    её id. Пустая строка — подтверждать нечем (среди вызовов есть взгляд на
    экран или чужое действие), и тогда нужен обычный второй круг.

    Несколько действий — одна фраза через «и»: «Открыла YouTube и закрыла
    Discord». Порядок и вид берутся из первого варианта каждого действия —
    со случайным выбором такая фраза читалась бы хуже.
    """
    if not calls:
        return ""
    known = read_list() if apps is None else apps
    one = len(calls) == 1
    parts: list[str] = []
    for call in calls:
        forms = CONFIRM.get(call.get("name", ""))
        if forms is None:
            return ""
        try:
            args = json.loads(call.get("args") or "{}")
        except ValueError:
            return ""
        if not isinstance(args, dict):
            return ""
        title = ""
        if "{title}" in forms[0]:
            title = _title_of(str(args.get("app", "")).strip(), known)
        # «Закрой телеграм», а он и не был запущен: закрытие «удалось», но
        # «Закрыла Telegram» было бы враньём — говорим, как есть. Так же и
        # YouTube с заметкой: свой `text` из ответа инструмента — это уже
        # готовая фраза про то, что нашлось, и «{text}» без него оставлять
        # нечего. Условие — по самому шаблону, а не по списку имён: забытый
        # в списке инструмент упал бы с KeyError прямо в момент ответа.
        said = _result_text(results, len(parts))
        # Действие уже было — инструмент вернул `repeat` со своим `text`
        # («Уже сделала.», «Он уже открыт.»). Формы вида «Включила {title}.»
        # или «Сохранила момент» здесь врали бы: ничего не включали и не
        # сохраняли. Поэтому повтор говорит своим текстом — он и есть ответ.
        if _is_repeat(results, len(parts)):
            if not said:
                return ""
            parts.append(said)
            continue
        if call.get("name") in (CLOSE_NAME, YT_NAME) and "не запущен" in said:
            if not said:
                return ""
            parts.append(said.rstrip("."))
            continue
        if "{text}" in forms[0]:
            if not said:
                return ""
            # Точку тут не срезаем: `text` — уже готовая фраза, написанная
            # с точкой, и без неё голос выдаёт телеграф. Срезается она ниже,
            # при склейке нескольких действий в одну фразу.
            parts.append(said)
            continue
        # Несколько действий — первый вариант: со случайным выбором фраза
        # «Готово, снимок на телефоне и Готово, клип сохранён» звучала бы хуже.
        parts.append((random.choice(forms) if one else forms[0]).format(title=title))
    if one:
        return parts[0]
    return _join([part.rstrip(".") for part in parts])


# --- Действия: одно и то же для голоса, для телефона и для модели ---------


def shoot(emit: Event, phone: Phone | None = None,
          hide: float = SHOT_HIDE) -> tuple[Path, str]:
    """Снимает экран и показывает его на телефоне. Возвращает (файл, картинка).

    Ровно то, что делала голосовая команда «скриншот»: тот же звук затвора,
    то же событие в журнал, тот же файл в папке с картинками.

    `hide` — сколько секунд картинка лежит на телефоне, пока не уберётся сама
    (28.09: «скрин не скрывается сам»). Взгляд на экран кладёт её ненадолго:
    снимок там нужен ей, а не хозяину.
    """
    from core import screen

    path, image = screen.take("primary")
    emit("shot_ready", str(path))
    server = phone() if phone is not None else None
    if server is not None:
        try:
            server.send_sound("shutter")
            server.send_shot(image, path.name, hide)
        except Exception:
            pass
    return path, image


def clip(emit: Event, phone: Phone | None = None) -> tuple[str, bool]:
    """Сохраняет клип мгновенного повтора. Возвращает (что нажали, повтор был выключен).

    Повтор мог быть выключен — чаще всего выключала его сама NVIDIA, см.
    core/replay.py. Тогда клипа нет, но повтор включаем сразу: раз попросили,
    повтор ему теперь нужен. Про «был выключен» голосовой цикл и модель
    говорят по-разному, поэтому флаг отдаётся наружу, а не зашит в строку.
    """
    from core import hotkeys, replay

    server = phone() if phone is not None else None

    def sound(name: str) -> None:
        if server is not None:
            try:
                server.send_sound(name)
            except Exception:
                pass

    try:
        what = hotkeys.save_moment()
    except replay.ReplayOff as exc:
        emit("moment_failed", str(exc))
        sound("fail")
        threading.Thread(target=replay.turn_on, daemon=True).start()
        # Модели это уходит словами: клипа нет, и врать про него нельзя.
        return f"момент не сохранился: {exc}. Повтор включила", True
    except Exception as exc:
        emit("moment_failed", f"{type(exc).__name__}: {exc}")
        sound("fail")
        raise
    emit("moment_sent", what)
    sound("moment")
    return what, False


def actions_for(emit: Event, phone: Phone | None = None) -> dict:
    """Готовый `brain.actions` — тем же кодом, что и голосовые команды.

    Каждому действию отдаются тот же телефон и та же отправка событий, что и
    раньше, поэтому «сделай скриншот» голосом и take_screenshot моделью дают
    один и тот же снимок с тем же звуком.
    """

    def screenshot() -> str:
        path, _image = shoot(emit, phone)
        return f"готово, снимок на телефоне: {path.name}"

    def moment() -> str:
        # Модели нужно ясное «сохранён / нет»: голое «Alt+F10» она поймёт
        # как угодно.
        what, off = clip(emit, phone)
        if off:
            return _error(what)
        return _ok(ok=True, text=f"клип сохранён ({what})")

    def look() -> str:
        # Модели нужен сам снимок, а не путь к нему: телефон его всё равно
        # видит, а в облако уходит только то, что вернулось отсюда. На телефоне
        # он лежит недолго: этот кадр хозяину не его, а ей.
        _path, image = shoot(emit, phone, hide=LOOK_HIDE)
        return image

    return {"screenshot": screenshot, "moment": moment, "look": look}


def run_action(name: str, actions: dict) -> str:
    """Вызывает действие из `brain.actions`. Строка — для сообщения tool.

    Отдельно on_event не нужен: события отправляет сам action — это замыкания
    из `actions_for`, с emit, который ему отдал тот же создатель мозга.
    """
    key = KEY_OF.get(name, "")
    action = (actions or {}).get(key)
    if not callable(action):
        return _error(f"действие «{key or name}» сейчас недоступно")
    # Тот же щит от повтора, что у остальных действий модели: снимок или клип,
    # сделанные только что, второй раз делать нечего. Аргументов у этих
    # инструментов нет, поэтому ключ — одно имя. Взгляд на экран (`look`)
    # сюда не входит: там модель сама просит снимок, чтобы на него посмотреть.
    if key in ("screenshot", "moment"):
        already = repeated(name, "{}")
        if already:
            return already
    try:
        done = str(action())
    except Exception as exc:
        return _error(f"не вышло: {type(exc).__name__}: {exc}")
    if key in ("screenshot", "moment") and not done.startswith('{"error"'):
        remember_done(name, "{}")
    return done
