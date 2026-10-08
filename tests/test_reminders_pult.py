"""Напоминания на Панели: API, страница «Команды» и сам `pult.js`.

Часть B основной задачи `coordination/задания/task_reminders.md`: хранилище,
будильник и инструменты написаны в `tests/test_reminders.py`, здесь только
пульт.

Три вещи проверяются по-разному и потому по-разному:

* **API поднимается по-настоящему** — `PhoneServer` на свободном порту,
  `TestClient` стучится как `testclient` и проходит проверку «только с
  компьютера», а для проверки отказа подменён адрес клиента. Напоминания
  лежат во временной папке, живой `data/reminders.json` не создаётся.
* **`commands_guide()["skills"]`** — это то, что страница «Команды» раздаёт
  хозяину, поэтому набор не должен быть пустым, а у каждой строки должны
  быть живые примеры: карточка без примера никого ничему не учит.
* **Страница Панели** проверяется по исходнику `pult.js`. `node --check`
  не ловит ни опечатку в имени, ни вызов несуществующей функции, а именно
  это валит страницу целиком (`coordination/ГРАБЛИ.md`).

Ни микрофона, ни сети, ни облака: только временные файлы и подмены.
"""

import atexit
import re
import shutil
import socket
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import commands, reminders, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

SAFE = Path(tempfile.mkdtemp(prefix="truba-reminders-pult-"))
atexit.register(shutil.rmtree, SAFE, True)

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
PULT_CSS = Path(config.ROOT) / "ui" / "web" / "pult.css"

# Код Панели про напоминания. Функции идут подряд, поэтому нарезка по
# следующему объявлению не заходит за чужой код (как в
# `tests/test_commands_page.py`). Порядок — как в файле.
ТЕКСТ_ПАНЕЛИ = (
    "спроситьНапоминания", "напоминаниеФраза", "напоминаниеОтменить",
    "напоминаниеСтрока", "напоминанияТаймерСнять", "блокНапоминаний",
    "напоминаниеСобытие",
)


# Верхний уровень pult.js: функция либо объявление, начинающееся с нулевого
# отступа. Режем по нему, а не только по следующей `function`, иначе последняя
# функция в списке забирала бы весь остаток файла (там есть чужие таймеры).
ВЕРХ = re.compile(r"^(?:async function|function|let|const|var|class|/\*)",
                  re.M)


def _тело(скрипт: str, имя: str) -> str:
    """Текст функции `имя` — от объявления до следующего верхнего уровня."""
    начало = скрипт.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = скрипт[начало:]
    новое = ВЕРХ.search(остаток, 1)
    if новое:
        return остаток[: новое.start()]
    return остаток


def _панель(скрипт: str) -> str:
    """Весь код Панели про напоминания слитно."""
    return "".join(_тело(скрипт, имя) for имя in ТЕКСТ_ПАНЕЛИ)


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _сейчас() -> datetime:
    return datetime.now().astimezone()


class Хранилище(unittest.TestCase):
    """Общая подмена: свой файл напоминаний и своё время."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=str(SAFE)))
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = mock.patch.object(
            reminders, "PATH", self.folder / "reminders.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, reminders, "_LAST_ID", 0)
        reminders._LAST_ID = 0
        self.now = _сейчас()

    def _поставить(self, через_минут=30, текст="вытащить пиццу",
                   kind=reminders.KIND_REMINDER, minutes=0):
        return reminders.add(self.now + timedelta(minutes=через_минут),
                             текст, kind=kind, minutes=minutes, now=self.now)


class Руки(Хранилище):
    """`reminders_list` / `reminders_cancel` — то, что отдаёт API."""

    def _среда(self):
        среда = object.__new__(WebRuntime)
        среда._remember = mock.Mock()
        return среда

    def test_список_по_времени_и_с_нужными_полями(self):
        self._поставить(через_минут=40)
        self._поставить(через_минут=5, kind=reminders.KIND_TIMER, minutes=5,
                        текст="")
        ответ = self._среда().reminders_list()
        self.assertTrue(ответ["ok"])
        self.assertEqual([одна["minutes"] for одна in ответ["items"]], [5, 0])
        # Поля — ровно те, что рисует строка Панели. `say` сюда не идёт:
        # это фраза, которую она произнесёт, Панели она не нужна.
        self.assertEqual(set(ответ["items"][0]),
                         {"id", "due", "text", "kind", "minutes"})
        self.assertEqual(ответ["items"][1]["kind"], "reminder")
        self.assertEqual(ответ["items"][1]["text"], "вытащить пиццу")

    def test_пустой_список_не_ошибка(self):
        # Пока напоминаний нет, Панель просто не рисует блок — это не поломка.
        self.assertEqual(self._среда().reminders_list(),
                         {"ok": True, "items": []})

    def test_время_уезжает_строкой_с_поясом(self):
        запись = self._поставить()
        # Не datetime: браузер разбирает ISO сам, а объект в JSON не поедет.
        self.assertEqual(self._среда().reminders_list()["items"][0]["due"],
                         запись["due"])
        self.assertIn("T", запись["due"])

    def test_отмена_убирает_и_пишет_в_журнал(self):
        запись = self._поставить()
        среда = self._среда()
        ответ = среда.reminders_cancel({"id": запись["id"]})
        self.assertTrue(ответ["ok"])
        self.assertEqual(ответ["items"], [])
        вид, строка = среда._remember.call_args[0]
        self.assertEqual(вид, "reminder_cancel")
        self.assertIn("напоминание отменено", строка)
        self.assertIn("вытащить пиццу", строка)

    def test_отмена_таймера_тоже_в_журнале(self):
        запись = self._поставить(через_минут=5, kind=reminders.KIND_TIMER,
                                 minutes=5, текст="")
        среда = self._среда()
        среда.reminders_cancel({"id": запись["id"]})
        self.assertIn("таймер отменено", среда._remember.call_args[0][1])

    def test_строка_отмены_доходит_до_журнала_пульта(self):
        # `_log_messages` — то, что пишется в файл журнала: без этой ветки
        # отмена осталась бы только в ленте событий.
        self.assertEqual(
            WebRuntime._log_messages("reminder_cancel",
                                     "таймер отменено: 17:05"),
            ["таймер отменено: 17:05"])

    def test_чужая_или_пустая_отмена_честно_отказывает(self):
        среда = self._среда()
        for тело in ({}, {"id": ""}, {"id": "r99"}):
            with self.subTest(тело=тело):
                with self.assertRaises(ValueError):
                    среда.reminders_cancel(тело)
        # Молчаливое «готово» здесь было бы враньём: хозяин поверил бы, что
        # снял, а напоминание всё ещё позвонило бы.
        среда._remember.assert_not_called()

    def test_кнопкой_нельзя_отменить_сразу_всё(self):
        # «all» — команда голоса («отмени всё»). Кнопка «✕» стоит на одной
        # строке, и одно нажатие не должно уносить то, чего хозяин не видел.
        self._поставить()
        self._поставить(через_минут=40)
        with self.assertRaises(ValueError):
            self._среда().reminders_cancel({"id": "all"})
        self.assertEqual(len(reminders.pending()), 2)


class Api(Хранилище):
    """`GET /api/reminders` и `POST /api/reminders/cancel` — только с компа."""

    @classmethod
    def setUpClass(cls):
        # Погода ходит в сеть: в тесте она не нужна, иначе прогон дёргал бы
        # интернет хозяина.
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            import time
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        super().setUp()
        # Пульт: настоящие методы, но без журнала на диске.
        runtime = object.__new__(WebRuntime)
        runtime._remember = mock.Mock()
        self.server.runtime = runtime
        self.runtime = runtime
        self.addCleanup(setattr, self.server, "runtime", runtime)
        self.client = TestClient(self.server._app)
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_список_приходит_с_этого_компьютера(self):
        self._поставить()
        body = self.client.get("/api/reminders").json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["text"], "вытащить пиццу")

    def test_отмена_убирает_строку(self):
        запись = self._поставить()
        answer = self.client.post("/api/reminders/cancel",
                                  json={"id": запись["id"]})
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.json()["ok"])
        self.assertEqual(self.client.get("/api/reminders").json()["items"], [])

    def test_телефон_не_видит_ни_списка_ни_отмены(self):
        # Расписание хозяина видно всему, кто знает адрес пульта, а телефон
        # лежит в сети — значит, оба маршрута только с компьютера.
        self._поставить()
        for method, path in (("get", "/api/reminders"),
                             ("post", "/api/reminders/cancel")):
            with self.subTest(путь=path):
                answer = getattr(self.вдали, method)(path)
                self.assertEqual(answer.status_code, 403, path)
                self.assertIn("только с этого компьютера", answer.json()["error"])
        # И ничего не отменилось мимо хозяина.
        self.assertEqual(len(reminders.pending()), 1)

    def test_отмена_без_id_даёт_400(self):
        self._поставить()
        answer = self.client.post("/api/reminders/cancel", json={})
        self.assertEqual(answer.status_code, 400)
        self.assertFalse(answer.json()["ok"])

    def test_тело_не_объект_даёт_400(self):
        answer = self.client.post("/api/reminders/cancel", json=["r1"])
        self.assertEqual(answer.status_code, 400)
        self.assertIn("нужен JSON-объект", answer.json()["error"])

    def test_без_пульта_отвечает_503(self):
        self.server.runtime = None
        try:
            self.assertEqual(self.client.get("/api/reminders").status_code, 503)
            self.assertEqual(
                self.client.post("/api/reminders/cancel", json={"id": "r1"}
                                 ).status_code, 503)
        finally:
            self.server.runtime = self.runtime


class Навыки(unittest.TestCase):
    """`commands_guide()["skills"]` — второй блок страницы «Команды»."""

    def setUp(self):
        self.гайд = commands.commands_guide()
        self.навыки = self.гайд["skills"]

    def test_набор_непустой(self):
        # Пустой второй блок — это страница с заголовком и ничем.
        self.assertTrue(self.навыки)
        self.assertEqual([навык["title"] for навык in self.навыки],
                         [навык.title for навык in commands.SKILLS])

    def test_у_каждой_строки_есть_что_делает_и_примеры(self):
        for навык in self.навыки:
            with self.subTest(строка=навык["title"]):
                self.assertEqual(set(навык), {"title", "examples", "does"})
                self.assertTrue(навык["does"].strip(), навык["title"])
                # Два примера — минимум: с одним непонятно, это целая
                # способность или один случайный запрос.
                self.assertGreaterEqual(len(навык["examples"]), 2, навык["title"])
                self.assertLessEqual(len(навык["examples"]), 4, навык["title"])
                for пример in навык["examples"]:
                    self.assertTrue(пример.strip(), навык["title"])

    def test_строки_про_то_что_умеют_инструменты(self):
        # Набор включает напоминания и таймеры,
        # заметки, YouTube, поиск, взгляд на экран, раскладка с музыкой и
        # звуком, папки и диски.
        заголовки = [навык["title"].lower() for навык in self.навыки]
        for кусок in ("напоминания", "заметки", "youtube", "интернете",
                      "экран", "раскладка", "музыка", "папки", "диски"):
            self.assertTrue(any(кусок in заголовок for заголовок in заголовки),
                            "нет строки про %s" % кусок)

    def test_в_примерах_есть_живые_фразы_хозяина(self):
        # Примеры — то, что он говорит вслух, а не описание инструмента.
        примеры = [пример.lower() for навык in self.навыки
                   for пример in навык["examples"]]
        self.assertTrue(any("напомни через" in пример for пример in примеры))
        self.assertTrue(any("таймер" in пример for пример in примеры))
        self.assertTrue(any("заметк" in пример for пример in примеры))
        self.assertTrue(any("интернете" in пример or "поищи" in пример
                            for пример in примеры))
        # Длинные формулировки, которых нет в мгновенной таблице.
        self.assertTrue(any("слушай" in пример for пример in примеры))

    def test_простые_таймеры_локальные_а_напоминания_у_модели(self):
        self.assertEqual(commands.understand("таймер на пять минут").action, "timer")
        self.assertIsNone(commands.understand("напомни через двадцать минут проверить духовку"))
        self.assertIsNone(commands.understand("поставь таймер в пять вечера"))


class Панель(unittest.TestCase):
    """Код Панели про напоминания — по исходнику `pult.js`."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.css = PULT_CSS.read_text(encoding="utf-8")
        cls.код = _панель(cls.js)

    def test_код_напоминаний_найден(self):
        self.assertTrue(self.код, "код Панели про напоминания не найден в pult.js")

    def test_каждая_вызванная_функция_объявлена(self):
        # Опечатка в имени (`напоминаниеОтменит` вместо `напоминаниеОтменить`)
        # роняет страницу целиком, а `node --check` её пропускает.
        объявлены = set(re.findall(r"^(?:async )?function ([\wЁё]+)\(",
                                   self.код, re.M))
        for имя in ТЕКСТ_ПАНЕЛИ:
            self.assertIn(имя, объявлены, имя)
        вызваны = set(re.findall(r"(?<![\w.])(напоминани\w+)\(", self.код))
        self.assertTrue(вызваны, "блок напоминаний нигде не зовётся")
        self.assertEqual(sorted(вызваны - объявлены), [],
                         "вызваны, но не объявлены: страница упала бы в браузере")

    def test_каждый_класс_есть_в_pult_css(self):
        # Новый класс без правила рисуется без рамки и читается как пустая
        # полоса (та же проверка есть на странице «Команды»).
        for класс in set(re.findall(r"className = '([\w-]+)'", self.код)):
            self.assertIn("." + класс, self.css, "класса %s нет в pult.css" % класс)

    def test_блок_читает_сервер_а_не_диск(self):
        self.assertIn("fetch('/api/reminders'", self.код)
        self.assertIn("'/api/reminders/cancel'", self.код)
        self.assertNotIn("innerHTML", self.код)

    def test_таймер_раз_в_тридцать_секунд(self):
        # 60 раз в секунду Панель бы грела: тут ровно один таймер на весь блок
        # и раз в полминуты — этого хватает на «через N мин».
        self.assertIn("НАПОМИНАНИЯ_ПАУЗА = 30000", self.js)
        self.assertEqual(len(re.findall(r"setInterval\(", self.код)), 1)

    def test_таймер_живёт_только_пока_блок_на_экране(self):
        # Снят при уходе с Панели и при пустом списке — иначе таймер
        # остался бы крутить невидимую вкладку.
        self.assertIn("clearInterval(напоминанияТаймер)", self.код)
        self.assertIn("блокНапоминаний()", _тело(self.js, "нарисоватьПанель"))
        self.assertIn("напоминанияТаймерСнять()", _тело(self.js, "открыть"))

    def test_блок_рисуется_только_когда_есть_активные(self):
        блок = _тело(self.js, "блокНапоминаний")
        self.assertIn("if (!напоминанияКэш.length)", блок)
        self.assertIn("return null", блок)
        # И Панель не вставляет `null` в лист — это упало бы на `appendChild`.
        панель = _тело(self.js, "нарисоватьПанель")
        self.assertIn("if (напоминания) лист.appendChild(напоминания)", панель)

    def test_строка_как_обещано_хозяину(self):
        # «17:00 · через 23 мин — вытащить пиццу»: время, «через» и о чём.
        фраза = _тело(self.js, "напоминаниеФраза")
        for кусок in ("getHours()", "getMinutes()", "' · '", "через ", " мин",
                      " — "):
            self.assertIn(кусок, фраза, кусок)
        # У таймера без слов хвост — «таймер», иначе строка обрывалась бы
        # на тире.
        self.assertIn("'таймер'", фраза)

    def test_кнопка_отмены_без_подтверждения(self):
        # Отмена будильника ничего не удаляет: confirm здесь только раздражал
        # бы (в отличие от удаления заметки).
        self.assertNotIn("confirm(", self.код)
        кнопка = _тело(self.js, "напоминаниеСтрока")
        self.assertIn("✕", кнопка)
        self.assertIn("напоминаниеОтменить", кнопка)

    def test_отмена_убирает_строку_даже_при_отказе(self):
        # Сначала убираем, потом спрашиваем сервер: если он откажет, список
        # вернётся и строка встанет на место — иначе на экране остался бы
        # «✕», который ничего не снял.
        отмена = _тело(self.js, "напоминаниеОтменить")
        self.assertIn("напоминанияКэш.filter", отмена)
        self.assertIn("спроситьНапоминания(true)", отмена)

    def test_панель_узнаёт_о_постановке_отмене_и_срабатывании(self):
        # События те же, что идут в журнал; по ним список перечитывается.
        событие = _тело(self.js, "напоминаниеСобытие")
        for вид in ("reminder", "reminder_cancel", "reminder_fired"):
            self.assertIn(вид, событие, вид)
        опрос = _тело(self.js, "опроситьРантайм")
        self.assertIn("напоминаниеСобытие", опрос)
        self.assertIn("спроситьНапоминания(true)", опрос)

    def test_в_оформлении_блока_нет_зелёного(self):
        # Зелёный в пульте — цвет «работает»; напоминание не работает само
        # по себе, и зелёная строка читалась бы как «всё хорошо».
        начало = self.css.index(".панель-напоминания")
        кусок = self.css[начало:начало + 1200]
        for зелёный in ("--живой", "#4ea86a", "green"):
            self.assertNotIn(зелёный, кусок, зелёный)


class КомандыСтраница(unittest.TestCase):
    """Команды и навыки объединены в один каталог из серверных данных."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.css = PULT_CSS.read_text(encoding="utf-8")
        cls.код = _тело(cls.js, "нарисоватьКоманды")
        cls.карточка = _тело(cls.js, "комКарточка")
        cls.группы = _тело(cls.js, "комГруппы")

    def test_both_server_tables_supply_the_grouped_catalog(self):
        self.assertIn("данные.commands", self.группы)
        self.assertIn("данные.skills", self.группы)
        self.assertIn("комГруппы(данные)", self.код)

    def test_all_call_styles_use_one_card(self):
        for name in ("ком-карточка", "ком-что", "ком-примеры"):
            self.assertIn("'" + name + "'", self.карточка)
        self.assertIn("комПлашка", self.карточка)
        self.assertNotIn("function комНавык(", self.js)

    def test_помощники_объявлены(self):
        declared = set(re.findall(r"^function ([\wЁё]+)\(", self.js, re.M))
        called = set(re.findall(r"(?<![\w.])(ком\w+)\(", self.код))
        self.assertEqual(sorted(called - declared), [])

    def test_страница_не_вставляет_разметку_из_ответа(self):
        self.assertNotIn("innerHTML", self.код + self.карточка)

    def test_в_оформлении_страницы_нет_зелёного(self):
        start = self.css.index("/* ---------- Команды:")
        for color in ("--живой", "#4ea86a", "green"):
            self.assertNotIn(color, self.css[start:])


class СтраховкаДанных(Хранилище):
    def test_живой_файл_не_создаётся(self):
        self._поставить()
        среда = object.__new__(WebRuntime)
        среда._remember = mock.Mock()
        среда.reminders_list()
        живой = config.DATA_DIR / "reminders.json"
        # Путь тестов — не живой. Есть ли живой файл, не проверяем: его пишет
        # пульт.
        self.assertNotEqual(reminders.PATH, живой)


if __name__ == "__main__":
    unittest.main()
