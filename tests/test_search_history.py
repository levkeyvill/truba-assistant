"""История поиска на телефоне: что пишется, что не пишется и как это видно.

Хозяин спросил «что ты там искала?» — и карточка на телефоне показывает список.
Проверяем четыре уровня:

- **хранилище** (`core/search_history.py`): запрос пишется, переживает
  перезапуск, новые сверху, больше ста не копим, в файле только запрос и время
  — ни источников, ни ответов, ни ключей;
- **мозг** (`core/brain.py`): в историю попадают только **успешные** поиски и
  именно те запросы, что ушли в поисковик, а не слова хозяина;
- **телефон**: список приходит по уже авторизованному WebSocket (сокетов нет —
  отвечать некому, и в сеть ничего не уходит), ошибка чтения едет сообщением,
  а не молчанием;
- **страница**: встроенный JavaScript разбирается браузером.

Ни сети, ни микрофона, ни настоящих файлов хозяина: `web.run_tool` подменён,
`launcher.open_web` и `os.startfile` не вызываются вовсе, пути истории и
напоминаний переставлены во временную папку.
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import launcher, reminders, search_history, web
from core.brain import Brain
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ROOT = Path(config.ROOT)
PAGE = ROOT / "web" / "index.html"

FOUND = json.dumps({
    "query": "курс доллара",
    "results": [{"title": "Курс ЦБ", "url": "https://cbr.ru/rates",
                 "snippet": "92 рубля"}],
}, ensure_ascii=False)

# MARK: мозг


def _чан(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _вызов(имя, аргументы="{}", call_id="c1"):
    """Один круг ответа: модель зовёт инструмент."""
    return [_чан(calls=[NS(index=0, id=call_id,
                           function=NS(name=имя, arguments=аргументы))])]


def _реплика(текст):
    """Круг ответа: модель говорит словами."""
    return [_чан(текст)]


class _Client:
    """Отдаёт заготовленные потоки; тело запроса запоминает."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _мозг(*потоки):
    """Настоящий `Brain` без облака, без сети и без записи истории."""
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(потоки)
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {}
    brain.last_sources = []
    brain.last_queries = []
    brain.searched = False
    return brain


def _запросы_мозга() -> list[str]:
    return [one["query"] for one in search_history.entries()]


# MARK: хранилище


class Пути(unittest.TestCase):
    """Ни один тест ниже не должен трогать живые файлы хозяина."""

    def setUp(self):
        for имя in ("search_history", "reminders"):
            модуль = {"search_history": search_history,
                      "reminders": reminders}[имя]
            было = модуль.PATH
            self.addCleanup(setattr, модуль, "PATH", было)
            папка = tempfile.TemporaryDirectory(prefix="truba-tmp-")
            self.addCleanup(папка.cleanup)
            модуль.PATH = Path(папка.name) / f"{имя}.json"


class Хранилище(Пути):
    def test_запрос_пишется_и_читается_обратно(self):
        search_history.add("курс доллара")
        one = search_history.entries()
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["query"], "курс доллара")
        self.assertTrue(one[0]["at"], "без времени строка не годится")

    def test_пустой_запрос_не_запоминается(self):
        self.assertIsNone(search_history.add("   "))
        self.assertIsNone(search_history.add(None))
        self.assertEqual(search_history.entries(), [])

    def test_в_файле_только_запрос_и_время(self):
        # Ни источников, ни ответа, ни ключей: это список слов, ушедших в
        # интернет, а не переписка. `id` — служебный ключ строки, и он здесь
        # единственное, что добавилось.
        search_history.add("курс доллара")
        записан = json.loads(search_history.PATH.read_text(encoding="utf-8"))
        self.assertEqual(sorted(записан[0]), ["at", "id", "query"])
        self.assertNotIn("cbr.ru",
                         search_history.PATH.read_text(encoding="utf-8"))

    def test_у_каждой_записи_свой_непредсказуемый_id(self):
        # Удалять с телефона придётся по `id`, а не по тексту: повтор запроса
        # делает текст негодным ключом.
        search_history.add("курс доллара")
        search_history.add("курс доллара")
        записи = search_history.entries()
        first, second = sorted(one["id"] for one in записи)
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"\A[0-9a-f]{32}\Z")

    def test_удаление_идёт_строго_по_своему_id(self):
        # Два одинаковых поиска разными попытками: убирается та, которую
        # ткнули, а не обе с таким текстом.
        search_history.add("курс доллара")
        search_history.add("курс доллара")
        newer = search_history.entries()[0]
        self.assertTrue(search_history.delete(newer["id"]))
        self.assertEqual(_запросы_мозга(), ["курс доллара"])
        # Повторное удаление того же — честный отказ, а не «убрать вторую».
        self.assertFalse(search_history.delete(newer["id"]))
        self.assertEqual(_запросы_мозга(), ["курс доллара"])

    def test_чужой_id_ничего_не_убирает(self):
        # С телефона нельзя прислать путь или адрес: `delete` сверяет только
        # строки уже прочитанного списка и ничего по `id` не открывает.
        search_history.add("курс доллара")
        for мусор in ("../../config.py", "http://127.0.0.1/x", "", "  ",
                     "не-32-hex-символа", None, 42):
            self.assertFalse(search_history.delete(мусор), мусор)
        self.assertEqual(_запросы_мозга(), ["курс доллара"])
        self.assertTrue(search_history.PATH.exists())

    def test_старая_запись_без_id_тоже_удаляется(self):
        # Файл, каким он был до 01.10: `id` нет вовсе. Такую запись телефон
        # показать может, а удалить — не может, поэтому id проставляется и
        # переписывается на диск.
        search_history.PATH.write_text(
            json.dumps([{"at": "2026-09-30T18:04:11+05:00",
                         "query": "курс доллара цб"}], ensure_ascii=False),
            encoding="utf-8")
        запись = search_history.entries()[0]
        self.assertRegex(запись["id"], r"\A[0-9a-f]{32}\Z")
        # Id пережил перезапуск: он уже в файле, а не выдуман при чтении.
        self.assertEqual(search_history.entries()[0]["id"], запись["id"])
        self.assertTrue(search_history.delete(запись["id"]))
        self.assertEqual(search_history.entries(), [])

    def test_непрошедшая_миграция_не_показывает_временный_id(self):
        search_history.PATH.write_text(
            json.dumps([{"at": "2026-09-30T18:04:11+05:00",
                         "query": "курс доллара"}], ensure_ascii=False),
            encoding="utf-8")
        with mock.patch.object(search_history, "_write",
                               side_effect=OSError("диск занят")):
            with self.assertRaises(OSError):
                search_history.entries()
        # Повторный запрос после восстановления диска получает постоянный id.
        запись = search_history.entries()[0]
        self.assertTrue(search_history.delete(запись["id"]))

    def test_очистка_истории(self):
        for номер in range(5):
            search_history.add(f"запрос {номер}")
        self.assertEqual(search_history.clear(), 5)
        self.assertEqual(search_history.entries(), [])
        # Второй раз чистить нечего — и это не ошибка.
        self.assertEqual(search_history.clear(), 0)

    def test_одна_попытка_с_несколькими_запросами_одна_запись(self):
        # Внутри одного отхода в поисковик запросов несколько, но хозяин один
        # раз спросил: в истории это одна строка, а прочие запросы — при ней же.
        search_history.add("курс доллара цб", also=["доллар сегодня", "cbr курс"])
        запись = search_history.entries()[0]
        self.assertEqual(запись["query"], "курс доллара цб")
        self.assertEqual(search_history.entries()[0]["query"], "курс доллара цб")
        на_disk = json.loads(search_history.PATH.read_text(encoding="utf-8"))
        self.assertEqual(на_disk[0]["queries"], ["доллар сегодня", "cbr курс"])
        # Главный запрос в `queries` не дублируется.
        search_history.add("погода", also=["погода", "погода в москве"])
        на_disk = json.loads(search_history.PATH.read_text(encoding="utf-8"))
        self.assertEqual(на_disk[-1]["queries"], ["погода в москве"])
        # Телефону `queries` не нужны: рисуется главный запрос.
        self.assertNotIn("queries", search_history.entries()[0])

    def test_новая_запись_сверху(self):
        search_history.add("первый")
        search_history.add("второй")
        self.assertEqual(_запросы_мозга(), ["второй", "первый"])

    def test_не_более_ста(self):
        for номер in range(search_history.MAX + 20):
            search_history.add(f"запрос {номер}")
        записи = search_history.entries()
        self.assertEqual(len(записи), search_history.MAX)
        # Старое ушло, новое осталось.
        self.assertEqual(записи[0]["query"],
                         f"запрос {search_history.MAX + 19}")

    def test_повторный_поиск_остаётся_в_истории(self):
        # Повторение того же запроса позже — отдельный реальный поиск.
        search_history.add("курс доллара")
        search_history.add("курс доллара")
        self.assertEqual(len(search_history.entries()), 2)
        # Другой запрос тоже добавляется.
        search_history.add("погода")
        self.assertEqual(len(search_history.entries()), 3)

    def test_переживает_перезапуск(self):
        search_history.add("курс доллара")
        # «Перезапуск»: состояние процесса не участвует — только файл.
        self.assertEqual(search_history.entries()[0]["query"], "курс доллара")

    def test_длинный_запрос_обрезается(self):
        search_history.add("а" * 5000)
        self.assertLessEqual(len(search_history.entries()[0]["query"]),
                             search_history.QUERY_MAX)

    def test_битый_файл_читается_как_пустой(self):
        search_history.PATH.write_text("{не json", encoding="utf-8")
        self.assertEqual(search_history.entries(), [])

    def test_нет_файла_и_пустой_файл(self):
        self.assertEqual(search_history.entries(), [])
        search_history.PATH.write_text("[]", encoding="utf-8")
        self.assertEqual(search_history.entries(), [])

    def test_мусор_в_записях_пропускается(self):
        search_history.PATH.write_text(
            json.dumps([{"at": "x"}, "мусор", {"at": "y", "query": "наш"}],
                       ensure_ascii=False), encoding="utf-8")
        self.assertEqual(_запросы_мозга(), ["наш"])

    def test_entries_уважает_просьбу_о_коротком_списке(self):
        for номер in range(5):
            search_history.add(f"запрос {номер}")
        self.assertEqual(len(search_history.entries(limit=2)), 2)

    def test_путь_берётся_из_переменной_модуля(self):
        # Тесты подменяют `PATH`; константа была бы неподменяемой.
        self.assertIsInstance(search_history.PATH, Path)


# MARK: мозг


class МозгПишетИсторию(Пути):
    """В историю попадает только то, что правда ушло в поисковик."""

    def setUp(self):
        super().setUp()
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = True
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))

    def _инструмент(self, *аргументы):
        patch = mock.patch.object(web, "run_tool", return_value=FOUND)
        self.addCleanup(patch.stop)
        patch.start()

    def _ошибка(self):
        patch = mock.patch.object(web, "run_tool",
                                 return_value='{"error": "интернет молчит"}')
        self.addCleanup(patch.stop)
        patch.start()

    def test_поиск_инструментом_попадает_в_историю(self):
        self._инструмент()
        мозг = _мозг(
            _вызов("web_search", json.dumps({"query": "курс доллара"},
                                            ensure_ascii=False)),
            _реплика("Девяносто два рубля."))
        list(мозг.reply("сколько стоит доллар"))
        self.assertEqual(мозг.last_queries, ["курс доллара"])
        self.assertIn("курс доллара", _запросы_мозга())

    def test_быстрый_путь_тоже_пишет(self):
        # Кнопка «Найди»: запросы пишет модель в `_search_queries`, и в историю
        # идут именно они, а не фраза хозяина.
        self._инструмент()
        мозг = _мозг(_реплика("Девяносто два рубля."))
        мозг._search_queries = lambda text, query: ["курс доллара 30 сентября"]
        list(мозг.reply("курс доллара", search=True))
        self.assertEqual(мозг.last_queries, ["курс доллара 30 сентября"])
        self.assertIn("курс доллара 30 сентября", _запросы_мозга())

    def test_несколько_запросов_в_одном_ответе_одна_запись(self):
        # `_search_first` формулирует вопрос несколькими способами — это одна
        # попытка хозяина, и в истории она должна лежать одной строкой (30.09
        # на телефоне это выглядело дублем одного вопроса).
        self._инструмент()
        мозг = _мозг(_реплика("Девяносто два рубля."))
        мозг._search_queries = lambda text, query: [
            "курс доллара 30 сентября", "доллар цб сегодня", "cbr курс доллара"]
        list(мозг.reply("курс доллара", search=True))
        self.assertEqual(мозг.last_queries,
                         ["курс доллара 30 сентября", "доллар цб сегодня",
                          "cbr курс доллара"])
        записи = search_history.entries()
        self.assertEqual(len(записи), 1, "одна попытка — одна запись")
        self.assertEqual(записи[0]["query"], "курс доллара 30 сентября")
        на_disk = json.loads(search_history.PATH.read_text(encoding="utf-8"))
        self.assertEqual(на_disk[0]["queries"],
                         ["доллар цб сегодня", "cbr курс доллара"])

    def test_несколько_поисков_инструментом_тоже_одна_запись(self):
        # Тот же ответ по другому пути: модель зовёт `web_search` дважды.
        self._инструмент()
        мозг = _мозг(
            _вызов("web_search", json.dumps({"query": "курс доллара"},
                                            ensure_ascii=False), "c1"),
            _вызов("web_search", json.dumps({"query": "доллар цб"},
                                            ensure_ascii=False), "c2"),
            _реплика("Девяносто два рубля."))
        list(мозг.reply("сколько стоит доллар"))
        self.assertEqual(мозг.last_queries, ["курс доллара", "доллар цб"])
        self.assertEqual(len(search_history.entries()), 1)

    def test_два_отдельных_одинаковых_поиска_две_записи(self):
        # Дедупликации по словам быть не должно: хозяин спросил то же самое
        # в другой раз — это другой поход в интернет.
        self._инструмент()
        for _ in range(2):
            мозг = _мозг(_реплика("Девяносто два рубля."))
            мозг._search_queries = lambda text, query: ["курс доллара"]
            list(мозг.reply("курс доллара", search=True))
        self.assertEqual(_запросы_мозга(), ["курс доллара", "курс доллара"])
        self.assertNotEqual(search_history.entries()[0]["id"],
                            search_history.entries()[1]["id"])

    def test_остановленный_ответ_не_теряет_поиск(self):
        # Хозяин сказал «хватит», пока она говорила: поиск-то уже сделан, и
        # history без него обманывала бы. Первым куском идёт «секунду, гляну»,
        # вторым — сам ответ: к этому моменту запрос уже ушёл в поисковик.
        self._инструмент()
        мозг = _мозг(_реплика("Девяносто два рубля."))
        мозг._search_queries = lambda text, query: ["курс доллара"]
        поток = мозг.reply("курс доллара", search=True)
        # Первым куском идёт «секунду, гляну» (набор случайный), вторым — сам
        # ответ: к этому моменту запрос уже ушёл в поисковик.
        self.assertNotIn("Девяносто", next(поток))
        self.assertIn("Девяносто", next(поток))
        self.assertEqual(search_history.entries(), [],
                         "запись появилась слишком рано — не по границе ответа")
        поток.close()
        self.assertEqual(_запросы_мозга(), ["курс доллара"])

    def test_упавший_ответ_не_теряет_поиск(self):
        self._инструмент()
        мозг = _мозг(_реплика("Девяносто два рубля."))
        мозг._search_queries = lambda text, query: ["курс доллара"]
        with mock.patch.object(Brain, "_reply_rounds",
                               side_effect=RuntimeError("облако упало")):
            with self.assertRaises(RuntimeError):
                list(мозг.reply("курс доллара", search=True))
        self.assertEqual(_запросы_мозга(), ["курс доллара"])

    def test_неудачный_поиск_в_историю_не_попадает(self):
        # Попытка без результата — не запрос: хозяин зря искал бы его в списке.
        self._ошибка()
        мозг = _мозг(
            _вызов("web_search", json.dumps({"query": "курс доллара"},
                                            ensure_ascii=False)),
            _реплика("Не нашла."))
        list(мозг.reply("сколько стоит доллар"))
        self.assertFalse(мозг.searched)
        self.assertEqual(search_history.entries(), [])

    def test_слова_хозяина_в_историю_не_попадают(self):
        # Запрос пишет модель сама; слова хозяина она переписывает, и в
        # истории им не место.
        self._инструмент()
        мозг = _мозг(
            _вызов("web_search", json.dumps({"query": "смешной анекдот"},
                                            ensure_ascii=False)),
            _реплика("Смешно."))
        list(мозг.reply("расскажи анекдот про кота"))
        self.assertEqual(_запросы_мозга(), ["смешной анекдот"])

    def test_ответ_без_поиска_ничего_не_пишет(self):
        мозг = _мозг(_реплика("Нормально."))
        list(мозг.reply("как дела?"))
        self.assertFalse(мозг.searched)
        self.assertEqual(search_history.entries(), [])

    def test_сбой_хранилища_не_роняет_разговор(self):
        # Телефон спросит историю и получит пустой список, а не услышит
        # ошибку вместо ответа.
        мозг = _мозг(_реплика("Без поиска."))
        сломан = mock.patch.object(search_history, "add",
                                   side_effect=OSError("диск занят"))
        with сломан:
            self.assertEqual(list(мозг.reply("как дела?")),
                             ["Без поиска."])


# MARK: телефон


def _сервер() -> PhoneServer:
    """`PhoneServer` без сокета и потока: собираем то, что ушло бы наружу."""
    сервер = object.__new__(PhoneServer)
    сервер._clients = set()
    сервер._audio = set()
    сервер._listeners = []
    сервер._ready = threading.Event()
    сервер._loop = None
    return сервер


def _runtime() -> WebRuntime:
    """Пульта без голоса: нужны только списки и отправка телефону."""
    runtime = object.__new__(WebRuntime)
    runtime._lock = threading.Lock()
    runtime._ids = iter(range(1000))
    runtime._events = []
    runtime._log_path = Path(tempfile.mkdtemp(prefix="truba-log-")) / "log.txt"
    runtime.server = mock.Mock()
    runtime.voice = NS(running=False, ready=False)
    # В этих проверках важен ответ телефона, а не планировщик потоков.
    # Выполняем фоновую работу сразу, чтобы не ждать чужие daemon-потоки
    # от других модулей при полном прогоне.
    runtime._bg = lambda func, *args: func(*args)
    return runtime


class Телефон(unittest.TestCase):
    def setUp(self):
        for имя, модуль in (("search_history", search_history),
                            ("reminders", reminders)):
            было = модуль.PATH
            self.addCleanup(setattr, модуль, "PATH", было)
            папка = tempfile.TemporaryDirectory(prefix="truba-tmp-")
            self.addCleanup(папка.cleanup)
            модуль.PATH = Path(папка.name) / f"{имя}.json"

    def _просьба(self, runtime, kind, payload=None):
        runtime.handle_event(kind, payload if payload is not None
                             else {"type": kind})

    def _история(self, runtime):
        args, kwargs = runtime.server.send_search_history.call_args
        self.assertEqual(kwargs.get("error", ""), "")
        return args[0]

    def test_удаление_одной_записи_с_телефона(self):
        # Снять одну строку по её `id` и получить свежий список — телефон после
        # ответа перерисовывает карточку.
        search_history.add("первый")
        search_history.add("второй")
        runtime = _runtime()
        newer = search_history.entries()[0]
        self._просьба(runtime, "search_history_delete",
                      {"type": "search_history_delete", "id": newer["id"]})
        self.assertEqual([one["query"] for one in self._история(runtime)],
                         ["первый"])

    def test_отказ_удаления_едет_словами_и_списком(self):
        # Чужой id (устаревшая кнопка, два телефона) — не «готово» молча, а
        # понятная ошибка; и список остаётся, чтобы строка вернулась на место.
        search_history.add("курс доллара")
        runtime = _runtime()
        self._просьба(runtime, "search_history_delete",
                      {"type": "search_history_delete", "id": "0" * 32})
        args, kwargs = runtime.server.send_search_history.call_args
        self.assertTrue(kwargs.get("error"))
        self.assertEqual([one["query"] for one in args[0]], ["курс доллара"])

    def test_очистка_истории_с_телефона(self):
        for номер in range(3):
            search_history.add(f"запрос {номер}")
        runtime = _runtime()
        self._просьба(runtime, "search_history_clear",
                      {"type": "search_history_clear"})
        self.assertEqual(self._история(runtime), [])

    def test_отмена_напоминания_с_телефона(self):
        # Тем же самым `reminders_cancel`, что зовёт Панель: он проверяет id и
        # отдаёт обновлённый список. Второй реализации не появилось.
        запись = reminders.add(datetime.now().astimezone() + timedelta(minutes=20),
                               text="вытащить пиццу")
        runtime = _runtime()
        self._просьба(runtime, "reminders_cancel",
                      {"type": "reminders_cancel", "id": запись["id"]})
        runtime.server.send_reminders.assert_called_once()
        args, kwargs = runtime.server.send_reminders.call_args
        self.assertEqual(args[0], [])
        self.assertEqual(kwargs.get("error", ""), "")
        self.assertEqual(reminders.pending(), [])

    def test_отказ_отмены_едет_словами(self):
        # «Отменить всё» с телефона нельзя, и несуществующего r9 — тоже.
        reminders.add(datetime.now().astimezone() + timedelta(minutes=20),
                      text="вытащить пиццу")
        runtime = _runtime()
        self._просьба(runtime, "reminders_cancel",
                      {"type": "reminders_cancel", "id": "all"})
        args, kwargs = runtime.server.send_reminders.call_args
        self.assertTrue(kwargs.get("error"))
        # Напоминание осталось стоять, и телефон это видит.
        self.assertEqual(len(args[0]), 1)
        self.assertEqual(len(reminders.pending()), 1)

    def test_список_напоминаний_едет_на_телефон(self):
        runtime = _runtime()
        self._просьба(runtime, "reminders_list")
        runtime.server.send_reminders.assert_called_once()
        self.assertEqual(runtime.server.send_reminders.call_args[0][0], [])

    def test_напоминание_доходит_до_телефона(self):
        reminders.add(datetime.now().astimezone() + timedelta(minutes=20),
                      text="вытащить пиццу")
        runtime = _runtime()
        self._просьба(runtime, "reminders_list")
        items = runtime.server.send_reminders.call_args[0][0]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "вытащить пиццу")
        self.assertTrue(items[0]["due"], "без срока строка не годится")

    def test_ошибка_списка_едет_словами(self):
        runtime = _runtime()
        with mock.patch.object(runtime, "reminders_list",
                               side_effect=OSError("файл закрыт")):
            self._просьба(runtime, "reminders_list")
        args, kwargs = runtime.server.send_reminders.call_args
        self.assertEqual(args[0], [])
        self.assertTrue(kwargs.get("error"))

    def test_история_едет_на_телефон(self):
        search_history.add("курс доллара цб")
        runtime = _runtime()
        self._просьба(runtime, "search_history_list")
        items = runtime.server.send_search_history.call_args[0][0]
        self.assertEqual([one["query"] for one in items], ["курс доллара цб"])

    def test_ошибка_истории_едет_словами(self):
        runtime = _runtime()
        with mock.patch.object(search_history, "entries",
                               side_effect=ValueError("битый файл")):
            self._просьба(runtime, "search_history_list")
        args, kwargs = runtime.server.send_search_history.call_args
        self.assertEqual(args[0], [])
        self.assertTrue(kwargs.get("error"))

    def test_просмотр_ничего_не_выполняет(self):
        # Ни браузера, ни папок, ни команд: только чтение и ответ.
        runtime = _runtime()
        with mock.patch.object(launcher, "open_web") as браузер, \
                mock.patch("os.startfile") as запускалка:
            search_history.add("курс доллара цб")
            self._просьба(runtime, "search_history_list")
            self._просьба(runtime, "reminders_list")
        браузер.assert_not_called()
        запускалка.assert_not_called()

    def test_сокет_не_прошедший_проверку_не_получает_списка(self):
        # Ответ уходит по `_broadcast`, а в `_clients` попадают только те, кто
        # прошёл `ws_refusal`. Нет сокетов — нет и ответа в сеть.
        сервер = _сервер()
        сервер.send_reminders([{"text": "вытащить пиццу"}])
        сервер.send_search_history([{"query": "курс доллара", "at": "сейчас"}])
        self.assertEqual(сервер._clients, set())

    def test_сообщения_имеют_нужный_вид(self):
        сервер = _сервер()
        ушли = []
        сервер._broadcast = lambda sender, targets=None: sender(
            NS(send_text=ушли.append))
        сервер.send_reminders(
            [{"text": "пицца", "due": "2026-09-30T17:00:00+05:00",
              "kind": "reminder", "minutes": 0}],
            error="не получила список")
        сервер.send_search_history(
            [{"at": "2026-09-30T16:04:00+05:00", "query": "курс доллара цб"}])
        напоминания = json.loads(ушли[0])
        self.assertEqual(напоминания["type"], "reminders")
        self.assertEqual(напоминания["items"][0]["text"], "пицца")
        self.assertEqual(напоминания["error"], "не получила список")
        история = json.loads(ушли[1])
        self.assertEqual(история["type"], "search_history")
        self.assertEqual(история["items"][0]["query"], "курс доллара цб")
        self.assertEqual(история["error"], "")

    def test_проверка_ключа_осталась_на_своём_месте(self):
        # Новый список не должен был ослабить проверку Origin/ключа.
        from core import phone

        def сокет(origin=None, host="192.168.0.10:8765", client="192.168.1.50"):
            return NS(headers={"host": host, **({"origin": origin} if origin else {})},
                      query_params={}, client=NS(host=client))

        self.assertEqual(phone.ws_refusal(сокет()), "нет ключа")
        self.assertEqual(phone.ws_refusal(сокет(client="127.0.0.1",
                                                origin="https://evil.example",
                                                host="127.0.0.1:8765")),
                         "чужая страница")

    def test_редактор_в_пульте_не_создаёт_связи(self):
        # Иначе комп считал бы макет вторым телефоном, а хозяин — что программа
        # запустилась.
        self.assertIn("if (!EDIT) {\n  connect();",
                      PAGE.read_text(encoding="utf-8"))


# MARK: страница


class Страница(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.страница = PAGE.read_text(encoding="utf-8")
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node не найден")
        cls.ошибки = []
        скрипты = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>",
                             cls.страница, re.S)
        for номер, код in enumerate(скрипты):
            with tempfile.TemporaryDirectory() as папка:
                файл = Path(папка) / "page.js"
                файл.write_text(код, encoding="utf-8")
                итог = subprocess.run([node, "--check", str(файл)],
                                      capture_output=True, text=True, timeout=60)
            if итог.returncode:
                cls.ошибки.append(f"скрипт {номер}: {итог.stderr[:400]}")

    def test_скрипт_страницы_разбирается(self):
        self.assertEqual(self.ошибки, [])


if __name__ == "__main__":
    unittest.main()
