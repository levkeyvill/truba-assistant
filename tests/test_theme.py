"""Светлая тема: настройка, доставка телефону и обе страницы.

Четыре вещи, которые легко разъехаться:
  - `theme` проверяется мягко, как сетка телефона: чужое значение — тёмная
    тема, а не отказ сохранять остальное;
  - сервер вписывает `data-theme` в `<html>` страницы пульта (иначе пульт
    мигает тёмным) и шлёт `theme` телефону при подключении и после смены;
  - пульт умеет поле «Тема» и предпросмотр без «Сохранить»;
  - страница телефона красится по сообщению, помнит тему и не имеет ни
    одной кнопки переключения.

Пути к данным — во временную папку: живой settings.json тест не трогает.
"""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import phone, settings
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

PULT = Path(config.ROOT) / "ui" / "web"
PAGE = Path(config.ROOT) / "web" / "index.html"


def _runtime() -> WebRuntime:
    """Пульт без голоса: нужно только сохранение настроек."""
    runtime = object.__new__(WebRuntime)
    runtime._provider_test_lock = threading.Lock()
    runtime._lock = threading.Lock()
    runtime.server = mock.Mock()
    runtime.brain = None
    runtime.voice = NS(_brain=None, _close_conversation=lambda: None, running=False,
                       _apps=None)
    runtime._enroll = None
    runtime._jobs = {}
    runtime._audio_stale = False
    runtime._remember = lambda kind, payload: None
    return runtime


def _временные_настройки(тест):
    """Живой settings.json хозяина тест не трогает."""
    папка = Path(tempfile.mkdtemp(prefix="truba-theme-"))
    тест.addCleanup(shutil.rmtree, папка, True)
    подмена = mock.patch.multiple(
        settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
    подмена.start()
    тест.addCleanup(подмена.stop)


def _server() -> PhoneServer:
    """`PhoneServer` без сокета и потока: поднимать сервер тут незачем.

    Рассылка перехвачена: `_broadcast` кладёт отправителя в `разослано`,
    и тест достаёт из него содержимое, не заводя сетевого потока.
    """
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None
    server.разослано = []
    server._broadcast = lambda отправитель, targets=None: server.разослано.append(
        отправитель)
    return server


class _Клиент:
    """Телефон, который только запоминает, что ему отдали."""

    def __init__(self):
        self.тексты = []

    def send_text(self, текст):
        self.тексты.append(текст)


def _последнее(server) -> dict:
    """Что последним ушло телефонам (отправитель сам отдаёт JSON)."""
    клиент = _Клиент()
    server.разослано[-1](клиент)
    return json.loads(клиент.тексты[-1])


class ТемаНастройкаTests(unittest.TestCase):
    """`core/settings.py`: значение по умолчанию и проверка."""

    def setUp(self):
        _временные_настройки(self)
        self.addCleanup(setattr, config, "THEME", config.THEME)

    def test_по_умолчанию_тёмная(self):
        self.assertEqual(config.THEME, "dark")
        self.assertEqual(settings.DEFAULTS["theme"], "dark")
        self.assertEqual(settings.load_settings()["theme"], "dark")

    def test_обе_темы_берутся_как_есть(self):
        self.assertEqual(settings.validate_theme("dark"), "dark")
        self.assertEqual(settings.validate_theme("light"), "light")
        self.assertEqual(settings.validate_theme(" light "), "light")

    def test_мусор_становится_тёмной_темой(self):
        # Правка settings.json руками или версия из будущего — не причина
        # не открыться пульту.
        for мусор in (None, "", "серая", "DARK", 0, 1, True, [], {}):
            self.assertEqual(settings.validate_theme(мусор), config.THEME, мусор)

    def test_сохранённая_тема_доезжает_до_config(self):
        settings.save_settings({"theme": "light"})
        settings.apply_to_config()
        self.assertEqual(config.THEME, "light")
        # И обратно: мусор в файле не оставляет пульт в выдуманной теме.
        config.THEME = "dark"
        settings.SETTINGS_PATH.write_text(json.dumps({"theme": "фиолетовая"}),
                                          encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.THEME, "dark")

    def test_фон_окна_свой_у_каждой_темы(self):
        # Тёмное окно под светлую страницу моргает чёрным, и это видно при
        # каждом запуске.
        self.assertNotEqual(config.ФОН_ОКНА, config.ФОН_ОКНА_СВЕТЛАЯ)
        self.assertEqual(config.фон_окна("light"), config.ФОН_ОКНА_СВЕТЛАЯ)
        self.assertEqual(config.фон_окна("dark"), config.ФОН_ОКНА)
        self.assertEqual(config.фон_окна("ерунда"), config.ФОН_ОКНА)

    def test_окно_пульта_берёт_фон_из_настроек(self):
        окно = (Path(config.ROOT) / "ui" / "window.py").read_text(encoding="utf-8")
        self.assertIn("background_color=config.фон_окна(config.THEME)", окно)
        self.assertNotIn("background_color=config.ФОН_ОКНА,", окно)


class ТемаВРазметкеTests(unittest.TestCase):
    """Тему вписывает сервер — до первой отрисовки, без мигания."""

    def setUp(self):
        _временные_настройки(self)

    def test_тема_вписывается_в_открывающий_тег(self):
        settings.save_settings({"theme": "light"})
        got = phone._с_темой('<html lang="ru">\n<head></head>\n</html>')
        self.assertIn('<html lang="ru" data-theme="light">', got)

    def test_тёмная_тема_тоже_прописывается(self):
        # Иначе пришлось бы гадать по умолчанию: тема должна быть видна
        # прямо в разметке, а не только прийти по связи.
        self.assertEqual(phone._тема(), "dark")
        got = phone._с_темой('<html lang="ru">')
        self.assertIn('data-theme="dark"', got)

    def test_испорченная_разметка_не_роняет_страницу(self):
        # Файл правили руками — пусть покажется тёмной, но не упадёт.
        for страница in ("<p>мимо</p>", "<html", '<html data-theme="light">'):
            self.assertIsInstance(phone._с_темой(страница), str)

    def test_уже_прописанную_тему_не_переписываем(self):
        got = phone._с_темой('<html lang="ru" data-theme="light">')
        self.assertEqual(got.count("data-theme"), 1)

    def test_тема_читается_заново_при_каждом_запросе(self):
        # Хозяин переключил тему в пульте, а телефон лежал: страница должна
        # прийти уже в новой.
        self.assertEqual(phone._тема(), "dark")
        settings.save_settings({"theme": "light"})
        self.assertEqual(phone._тема(), "light")

    def test_битый_settings_json_не_роняет_страницу(self):
        settings.SETTINGS_PATH.write_text("{не json", encoding="utf-8")
        self.assertEqual(phone._тема(), config.THEME)

    def test_у_обоих_страниц_есть_куда_вписать_тему(self):
        # Тему вписывает сервер, значит в самих файлах её быть не должно:
        # иначе она застыла бы там навсегда.
        for имя in ("ui/web/pult.html", "web/index.html"):
            разметка = (Path(config.ROOT) / имя).read_text(encoding="utf-8")
            начало = разметка.find("<html")
            тег = разметка[начало:разметка.find(">", начало)]
            self.assertTrue(тег.startswith("<html"), имя)
            self.assertNotIn("data-theme", тег, имя)


class ТемаТелефонуTests(unittest.TestCase):
    """`send_theme` и ответ на смену темы в пульте."""

    def setUp(self):
        _временные_настройки(self)
        self.server = _server()

    def test_сообщение_уходит_всем_сразу(self):
        self.server.send_theme("light")
        self.assertEqual(_последнее(self.server),
                         {"type": "theme", "value": "light"})
        self.server.send_theme("dark")
        self.assertEqual(_последнее(self.server),
                         {"type": "theme", "value": "dark"})

    def test_мусор_в_сообщение_не_попадает(self):
        # Телефон ждёт ровно «dark» или «light»: всё прочее молча станет
        # тёмной, а не уедет в несуществующую тему.
        for мусор in (None, "", "серая", 7, True):
            self.server.send_theme(мусор)
            self.assertEqual(_последнее(self.server)["value"], config.THEME, мусор)


class ТемаПриСохраненииTests(unittest.TestCase):
    """`WebRuntime.save_settings` принимает тему и разсылает её телефону."""

    def setUp(self):
        _временные_настройки(self)
        self.addCleanup(setattr, config, "THEME", config.THEME)
        self.среда = _runtime()

    def test_сохранённая_тема_разошлась_телефонам(self):
        ответ = self.среда.save_settings({"theme": "light"})
        self.assertTrue(ответ["ok"], ответ)
        self.среда.server.send_theme.assert_called_once_with("light")
        self.assertEqual(settings.load_settings()["theme"], "light")

    def test_такая_же_тема_ничего_не_шлёт(self):
        # «Сохранить» жмут ради любой настройки — телефон зря дёргаться не должен.
        self.среда.save_settings({"theme": "dark"})
        self.среда.server.send_theme.assert_not_called()

    def test_мусор_не_ломает_остальное_сохранение(self):
        # Опечатка в теме — не причина потерять ключ и модель.
        ответ = self.среда.save_settings({"theme": "радуга", "history_turns": 9})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(settings.load_settings()["theme"], "dark")
        self.assertEqual(settings.load_settings()["history_turns"], 9)
        self.среда.server.send_theme.assert_not_called()


class ТемаВПультеTests(unittest.TestCase):
    """Поле «Тема» в «Настройках → Система» и предпросмотр."""

    @classmethod
    def setUpClass(cls):
        cls.js = (PULT / "pult.js").read_text(encoding="utf-8")
        cls.css = (PULT / "pult.css").read_text(encoding="utf-8")

    def test_поле_темы_есть_в_разделе_система(self):
        система = self.js.split("настрСекция(содержимое, 'система'")[1].split("\n  }")[0]
        self.assertIn("настрПоле(система, 'theme', theme)", система)

    def test_в_списке_обе_темы_с_русскими_названиями(self):
        self.assertIn("const ТЕМЫ = [['dark', 'Тёмная'], ['light', 'Светлая']]",
                      self.js)

    def test_выбор_перекрашивает_сразу_без_сохранения(self):
        # Предпросмотр: тему проверяют глазами, ждать «Сохранить» нельзя.
        self.assertIn("theme.addEventListener('change', () => темуПоставить(theme.value))",
                      self.js)
        self.assertIn("function темуПоставить(значение)", self.js)
        self.assertIn("document.documentElement.dataset.theme", self.js)

    def test_тема_едет_в_сохранение(self):
        self.assertIn("theme: эл.theme.value,", self.js)

    def test_ушёл_без_сохранения_вернётся_сохранённая(self):
        # `заполнитьНастройки` ставит тему из снимка, а не из формы.
        заполнить = self.js.split("function заполнитьНастройки")[1].split("\n}")[0]
        self.assertIn("эл.theme.value = s.theme === 'light' ? 'light' : 'dark';",
                      заполнить)
        self.assertIn("темуПоставить(эл.theme.value);", заполнить)

    def test_светлая_тема_переопределяет_все_токены(self):
        светлая = self.css.split(':root[data-theme="light"] {')[1].split("\n}")[0]
        for токен in ("--фон", "--панель", "--панель-выше", "--линия", "--текст",
                      "--приглушён", "--тусклый", "--акцент", "--акцент-тень",
                      "--синий", "--живой", "--тревога"):
            self.assertIn(токен + ":", светлая, токен)

    def test_у_qr_кода_подложка_осталась_белой(self):
        # Камера телефона читает тёмный код только на светлом.
        self.assertIn("padding: 12px; background: #fff;", self.css)


class ТемаНаТелефонеTests(unittest.TestCase):
    """Страница телефона: красится по сообщению, кнопок темы нет."""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_светлая_тема_переопределяет_все_токены(self):
        светлая = self.html.split(':root[data-theme="light"] {')[1].split("\n  }")[0]
        for токен in ("--bg", "--panel", "--line", "--text", "--muted", "--dim",
                      "--accent", "--info", "--bot", "--warn", "--good"):
            self.assertIn(токен + ":", светлая, токен)

    def test_телефон_красится_по_сообщению(self):
        self.assertIn("function applyTheme(value)", self.html)
        self.assertIn("document.documentElement.dataset.theme = тема;", self.html)
        self.assertIn("else if (msg.type === 'theme') applyTheme(msg.value);", self.html)

    def test_телефон_помнит_тему_и_не_падает_без_хранилища(self):
        # Инкогнито и запрет хранилища — обычное дело, а не повод ничем
        # не красить телефон.
        self.assertIn("try { localStorage.setItem('тема', тема); }", self.html)
        self.assertIn("try {\n  const помнили = localStorage.getItem('тема');", self.html)

    def test_на_телефоне_нет_кнопок_темы(self):
        # Лишних кнопок на телефоне не заводим: тема меняется в пульте.
        for запрещён in ('data-тема', 'id="тема"', "показатьТему", "Тёмная"):
            self.assertNotIn(запрещён, self.html, запрещён)

    def test_заряд_и_подсветка_краёв_остались_своими(self):
        # Их трогать нельзя: заряд — индикатор, край — признак «микрофон
        # работает». На светлом рамка «она говорит» просто стала плотнее.
        for свой in ("#s-bat.mid .bat  { color: #ffd24a; }",
                     "#s-bat.low .bat  { color: #ff5c5c; }"):
            self.assertIn(свой, self.html, свой)
        self.assertIn(':root[data-theme="light"] #edge.talk {', self.html)
        self.assertIn(':root[data-theme="light"] #edge::before { border-width: 4px; }',
                      self.html)
