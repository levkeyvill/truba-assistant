"""История разговора в пульте: посмотреть, не стирая, и только с компьютера.

Хозяин 1 октября: в Панели написано «18 реплик в разговоре», а открыть их
негде — кнопка была одна, и та стирала. Теперь рядом с ней «Показать историю»
и читаемый список.

Проверяем четыре уровня:

- **хранилище**: путь истории уводится во временную папку, живой
  `data/history.json` хозяина тест не читает и не пишет
  (`tests/test_data_guard.py` делает то же на весь прогон);
- **сервер**: `GET /api/settings/history` отдаёт последние реплики только
  с этого компьютера, отдаёт ровно `role`/`content`/`at` — ни сходства с
  голосом, ни служебных пометок — и ничего не стирает;
- **пульт** (`ui/web/pult.js`): кнопка, список с переносами и прокруткой,
  объяснение «история — не память» и обновление открытого списка после
  очистки; скрипт разбирается `node`;
- **страница телефона**: «Отменить», «Удалить» и «Очистить» работают с
  одного касания — это проверяется настоящим щелчком по DOM в node, а не
  поиском строки в тексте.

Облако, голос, микрофон и настоящие настройки в проверке не участвуют.
"""

import json
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import memory, weather
from core.phone import PhoneServer

ROOT = Path(config.ROOT)
PULT_JS = ROOT / "ui" / "web" / "pult.js"
PULT_CSS = ROOT / "ui" / "web" / "pult.css"
PAGE = ROOT / "web" / "index.html"


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class История(unittest.TestCase):
    """Сервер отдаёт реплики и не отдаёт лишнего."""

    @classmethod
    def setUpClass(cls):
        # Погода ходит в сеть: тесту она не нужна, иначе прогон дёргал бы
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
        # История — во временную папку: файл хозяина тут не трогаем.
        папка = tempfile.TemporaryDirectory(prefix="truba-history-")
        self.addCleanup(папка.cleanup)
        было = memory.HISTORY_PATH
        self.addCleanup(setattr, memory, "HISTORY_PATH", было)
        memory.HISTORY_PATH = Path(папка.name) / "history.json"
        self.здесь = TestClient(self.server._app)
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_реплики_приходят_с_этого_компьютера(self):
        memory.save_history([
            {"role": "user", "content": "Привет", "at": "2026-10-01T17:04:00"},
            {"role": "assistant", "content": "Здорово", "at": "2026-10-01T17:04:01"},
        ])
        ответ = self.здесь.get("/api/settings/history")
        self.assertEqual(ответ.status_code, 200)
        данные = ответ.json()
        self.assertTrue(данные["ok"])
        self.assertEqual([t["role"] for t in данные["turns"]],
                         ["user", "assistant"])
        self.assertEqual([t["content"] for t in данные["turns"]],
                         ["Привет", "Здорово"])
        self.assertTrue(данные["turns"][0]["at"], "без времени строка не годится")

    def test_телефон_списка_не_видит(self):
        # Реплики хозяина дословно, а пульт виден всему, кто знает адрес.
        memory.save_history([{"role": "user", "content": "Секретное",
                              "at": "2026-10-01T17:04:00"}])
        ответ = self.вдали.get("/api/settings/history")
        self.assertEqual(ответ.status_code, 403)
        self.assertNotIn("Секретное", ответ.text)

    def test_наружу_идут_только_роль_текст_и_время(self):
        # Внутри реплики лежит `voice` (сходство с голосом хозяина), `first`
        # (заход первой) и `searched` (что она искала). Наружу им нечего.
        memory.save_history([
            {"role": "user", "content": "Я слышу", "at": "2026-10-01T17:04:00",
             "voice": 0.61, "first": True},
            {"role": "assistant", "content": "Слышу", "at": "2026-10-01T17:04:01",
             "searched": ["курс доллара"]},
        ])
        for реплика in self.здесь.get("/api/settings/history").json()["turns"]:
            self.assertEqual(set(реплика), {"role", "content", "at"})
        тело = self.здесь.get("/api/settings/history").text
        self.assertNotIn("voice", тело)
        self.assertNotIn("searched", тело)

    def test_счётчик_и_потолок_едят_вместе(self):
        # Панель пишет «N реплик в разговоре» — это записи истории. Сколько их
        # может накопиться, хозяин тоже должен понимать, а потолок один.
        from core import state

        memory.save_history([{"role": "user", "content": "Раз",
                              "at": "2026-10-01T17:04:00"}])
        тело = self.здесь.get("/api/settings/history").json()
        self.assertEqual(len(тело["turns"]), 1)
        self.assertEqual(тело["limit"], config.HISTORY_TURNS)
        self.assertIn("1 реплик в разговоре",
                      state.everything()["память"]["внизу"])

    def test_пустая_история_не_ошибка(self):
        # Файла нет вовсе — это не поломка, а пустой список.
        тело = self.здесь.get("/api/settings/history").json()
        self.assertTrue(тело["ok"])
        self.assertEqual(тело["turns"], [])

    def test_чужой_путь_из_запроса_не_читается(self):
        # Пути в запросе нет вовсе: файл один и задан в коде. Добавление
        # параметра не должно ни открыть что-то, ни сломать ответ.
        memory.save_history([{"role": "user", "content": "Своё",
                              "at": "2026-10-01T17:04:00"}])
        for имя in ("path", "file", "turns", "../../config.py"):
            with self.subTest(имя=имя):
                тело = self.здесь.get("/api/settings/history",
                                       params={имя: "x"}).json()
                self.assertTrue(тело["ok"])
                self.assertEqual([t["content"] for t in тело["turns"]], ["Своё"])

    def test_чтение_ничего_не_стирает(self):
        # Открытие списка — только чтение: файл после него тот же.
        memory.save_history([
            {"role": "user", "content": "Раз", "at": "2026-10-01T17:04:00"},
            {"role": "assistant", "content": "Два", "at": "2026-10-01T17:04:01"},
        ])
        self.здесь.get("/api/settings/history")
        self.здесь.get("/api/settings/history")
        self.assertEqual(len(memory.load_history(0)), 2)

    def test_битый_файл_не_роняет_пульт(self):
        memory.HISTORY_PATH.write_text("{не json", encoding="utf-8")
        тело = self.здесь.get("/api/settings/history").json()
        self.assertTrue(тело["ok"],
                        "битый файл — пустой список, а не ошибка в лицо")
        self.assertEqual(тело["turns"], [])

    def test_страница_не_кешируется(self):
        # Браузер иначе показал бы старый список после очистки.
        заголовки = self.здесь.get("/api/settings/history").headers
        self.assertIn("no-store", заголовки["cache-control"])
        self.assertEqual(заголовки["pragma"], "no-cache")


class Пульт(unittest.TestCase):
    """Кнопка, список и честное объяснение — в исходнике `pult.js`."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.css = PULT_CSS.read_text(encoding="utf-8")

    def _между(self, начало: str, конец: str) -> str:
        """Текст между двумя кусками файла — им и проверяем, что рядом."""
        self.assertIn(начало, self.js, начало)
        хвост = self.js.split(начало, 1)[1]
        self.assertIn(конец, хвост, конец)
        return хвост.split(конец, 1)[0]

    def test_кнопка_стоит_рядом_с_очисткой(self):
        # Обе кнопки про историю видны сразу, без спрятанных настроек: хозяин
        # искал, где посмотреть, и не нашёл.
        self.assertIn("показатьИсторию.textContent = 'Показать историю';", self.js)
        self.assertIn("очистить.textContent = 'Очистить историю';", self.js)
        self.assertIn("показатьИсторию.addEventListener('click', показатьИсториюРазговора);",
                      self.js)
        # Кнопка не спрятана: она в общем ряду действий, без `hidden`.
        self.assertIn("appendChild(показатьИсторию)",
                      self._между("const показатьИсторию =", "const очистить ="))

    def test_список_читаемый_с_прокруткой(self):
        # Реплика — несколько строк: без переносов она читалась бы иначе, чем
        # её сказали, а список без прокрутки растянул бы всю форму.
        self.assertIn("white-space: pre-line", self.css)
        self.assertIn(".история-список", self.css)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn(".история-реплика", self.css)
        # Текст ставится текстом: это слова хозяина, разметкой быть не может.
        self.assertIn("тело.textContent = текст;", self.js)
        # Роль и время показаны словами, а не служебными значениями.
        self.assertIn("своя ? 'Ты' : 'Труба'", self.js)
        self.assertIn("историяВремя(реплика.at)", self.js)

    def test_разделы_различаены_прямо_в_интерфейсе(self):
        # Главная беда хозяина: «память» и «история» — одно слово про разные
        # вещи. Объясняем тут же, в разделе.
        пояснение = self._между("памятьПояснение.textContent =",
                                "память.appendChild(памятьПояснение)")
        self.assertIn("короткие факты", пояснение)
        self.assertIn("История — последние реплики", пояснение)
        # И что счётчик — это записи, а не пары.
        self.assertIn("количество отдельных реплик", пояснение)

    def test_список_грузится_только_по_нажатию(self):
        # Показывать историю при каждом открытии раздела незачем: она длинная,
        # а снимок настроек её не отдаёт.
        self.assertIn("fetch('/api/settings/history'", self.js)
        загрузка = self._между("async function загрузитьНастройки",
                                "async function сохранитьНастройки")
        self.assertNotIn("показатьИсториюРазговора(", загрузка)

    def test_после_очистки_открытый_список_обновляется(self):
        # Иначе на экране осталось бы то, чего уже нет.
        функция = self._между("async function очиститьИсторию()",
                               "async function забытьПамять()")
        self.assertIn("показатьИсториюРазговора();", функция)
        self.assertIn("!настрЭлементы.историяСписок.hidden", функция)

    def test_скрипт_пульта_разбирается(self):
        # `node --check` ловит опечатку в тексте скрипта — ровно то, от чего
        # падает вся страница (coordination/ГРАБЛИ.md).
        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        with tempfile.TemporaryDirectory() as папка:
            файл = Path(папка) / "pult.js"
            файл.write_text(self.js, encoding="utf-8")
            итог = subprocess.run([node, "--check", str(файл)],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(итог.returncode, 0, итог.stderr[:400])


# MARK: страница телефона


def _node(скрипт: str) -> str:
    """Выполнить скрипт в node и вернуть его вывод."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node не найден")
    with tempfile.TemporaryDirectory() as папка:
        файл = Path(папка) / "page.js"
        файл.write_text(скрипт, encoding="utf-8")
        итог = subprocess.run([node, str(файл)], capture_output=True,
                              text=True, timeout=60, encoding="utf-8")
    if итог.returncode:
        raise AssertionError(итог.stderr[:800])
    return итог.stdout


def _функция(страница: str, имя: str) -> str:
    """Текст функции поимённо.

    `node --check` такую ошибку не ловит: вызов несуществующей функции валит
    страницу в тот момент, когда до него доходят. Поэтому нужные куски
    страницы вырезаем и исполняем как есть — с подменённым DOM.
    """
    начало = страница.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = страница[начало:]
    новое = re.search(r"^\n(?:async )?function \w+\(", остаток[1:], re.M)
    if новое:
        остаток = остаток[: новое.start() + 1]
    return остаток


# Подмена DOM для одного касания. Настоящий браузер тут не нужен: важно, что
# по кнопке уходит ровно одна команда, а не две, и что событие погашено.
СТАБ_БРАУЗЕРА = r"""
const отправлено = [];
function send(что) { отправлено.push(что); }
const EDIT = false;
let waitTimer = null;
const WebSocket = { OPEN: 1 };
const socket = { readyState: 1 };
function closeMenu() {}
function closeList(box) {}
function элемент() {
  return {
    className: '', textContent: '', hidden: false, isConnected: true,
    children: [], childElementCount: 0, listeners: {}, dataset: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    setAttribute() {},
    appendChild(ребёнок) {
      this.children.push(ребёнок);
      this.childElementCount = this.children.length;
      return ребёнок;
    },
    addEventListener(что, куда) {
      (this.listeners[что] = this.listeners[что] || []).push(куда);
    },
    /* Настоящее касание: зовём обработчики и даём им событие. */
    касание() {
      let погашено = false;
      const шаблон = { stopPropagation: () => { погашено = true; } };
      for (const куда of (this.listeners.click || [])) куда(шаблон);
      return погашено;
    },
  };
}
const remList = элемент();
const histList = элемент();
const document = { createElement: элемент };
"""

_ПРОВЕРКА = r"""
// --- Проверка --------------------------------------------------------------
const итог = {};

// «Отменить» у напоминания: одно касание — одно действие, и событие погашено
// (иначе касание ушло бы ещё и в строку списка).
let отменено = 0;
const кнопкаОтмены = кнопкаСтроки('Отменить', () => { отменено += 1; });
итог.отмена_погашена = кнопкаОтмены.касание();
итог.отменено = отменено;

// «Удалить» у строки истории: то же самое, и строка не должна была открыться
// в браузере на компьютере.
let удалено = 0;
const кнопкаУдаления = кнопкаСтроки('Удалить', () => { удалено += 1; });
итог.удаление_погашено = кнопкаУдаления.касание();
итог.удалено = удалено;

// Второе касание той же кнопки тоже работает: никакого «Точно?» нет.
кнопкаУдаления.касание();
итог.удалено_подряд = удалено;

// Подпись кнопки не подменяется — «Точно?» в разметке быть не должно.
итог.подпись = кнопкаУдаления.textContent;

console.log(JSON.stringify(итог));
"""


class Телефон(unittest.TestCase):
    """Кнопки телефона — в одно касание, и это проверяется касанием."""

    def test_одно_касание_одно_действие(self):
        # Не поиском строки в тексте, а настоящим щелчком: иначе проверка
        # прошла бы и с прежним переспросом «Точно?».
        страница = PAGE.read_text(encoding="utf-8")
        куски = [_функция(страница, имя) for имя in (
            "показатьОжидание", "cardNote", "команда", "кнопкаСтроки")]
        скрипт = СТАБ_БРАУЗЕРА + "\n" + "\n".join(куски) + "\n" + _ПРОВЕРКА
        итог = json.loads(_node(скрипт))
        # Одно касание — ровно одно действие, и событие погашено.
        self.assertEqual(итог["отменено"], 1,
                         "«Отменить» не сработала с одного касания")
        self.assertTrue(итог["отмена_погашена"])
        self.assertEqual(итог["удалено"], 1,
                         "«Удалить» не сработала с одного касания")
        self.assertTrue(итог["удаление_погашено"],
                        "касание «Удалить» не должно было открыть запрос в браузере")
        # Второе касание тоже работает: переспроса нет.
        self.assertEqual(итог["удалено_подряд"], 2)
        self.assertEqual(итог["подпись"], "Удалить")

    def test_очистить_всею_историю_тоже_сразу(self):
        # Кнопка в шапке карточки шлёт команду прямо из обработчика, и переспроса
        # рядом с ней нет.
        страница = PAGE.read_text(encoding="utf-8")
        начало = страница.find("$('hist-clear').addEventListener")
        self.assertGreaterEqual(начало, 0, "обработчик «Очистить» не найден")
        # Конец обработчика — `});` с начала строки: такой же кусок есть
        # внутри самой команды, и первый попавшийся обрезал бы блок.
        блок = страница[начало:страница.find("\n});", начало)]
        self.assertIn("команда(histList, { type: 'search_history_clear' })", блок)
        кусок = страница[начало - 400:начало + 400]
        self.assertNotIn("спросить", кусок)
        self.assertNotIn("Точно?", кусок)

    def test_механизма_переспроса_больше_нет(self):
        # Ни функции, ни константы, ни класса окраски кнопки.
        страница = PAGE.read_text(encoding="utf-8")
        for мусор in ("function спросить(", "СПРОС_СЕК", "Точно?", ".спросить"):
            with self.subTest(мусор=мусор):
                self.assertNotIn(мусор, страница)

    def test_режим_макета_ничего_не_шлёт(self):
        # `?edit=1` в пульте рисует телефон без связи: команда оттуда не уходит.
        страница = PAGE.read_text(encoding="utf-8")
        self.assertIn("if (EDIT) return;", _функция(страница, "команда"))
        # И макет телефона вообще не подключается.
        self.assertIn("if (!EDIT) {\n  connect();", страница)

    def test_скрипт_страницы_разбирается(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        страница = PAGE.read_text(encoding="utf-8")
        скрипты = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", страница, re.S)
        self.assertTrue(скрипты)
        for номер, код in enumerate(скрипты):
            with tempfile.TemporaryDirectory() as папка:
                файл = Path(папка) / "page.js"
                файл.write_text(код, encoding="utf-8")
                итог = subprocess.run([node, "--check", str(файл)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(итог.returncode, 0,
                             f"скрипт {номер}: {итог.stderr[:400]}")


if __name__ == "__main__":
    unittest.main()
