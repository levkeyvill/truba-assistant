"""Страница «Команды» в пульте: инструкция, что сказать и что будет.

Главное правило здесь — страница строится из тех же данных, по которым
Труба понимает речь. Поэтому таблица `COMMANDS` проверяется целиком и в том
же порядке: переставили записи — и инструкция переставилась вместе с
поведением, а не разошлась с ним.

Данные — временные: `apps.json` подменяется на папку из `tempfile`, окно
разговора — `mock.patch.object`. Настоящий список программ тест не
трогает (tests/test_data_guard.py).

Проверка текста страницы идёт по исходнику `pult.js`: `node --check` не
ловит ни опечатку в имени, ни вызов несуществующей функции — а именно это
валяет страницу целиком (coordination/ГРАБЛИ.md). Поэтому имена, которыми
пользуется код страницы, сверяются с объявлениями.
"""

import json
import re
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import commands, launcher, weather
from core.phone import PhoneServer

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
PULT_HTML = Path(config.ROOT) / "ui" / "web" / "pult.html"

ПРИМЕРЫ = [
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com",
     "aliases": ["ютюб", "труба"]},
    {"id": "claude", "title": "Claude", "kind": "store", "app_id": "Claude!App"},
]

# Функции страницы: между ними нет постороннего кода, иначе проверки внизу
# нарезали бы чужой текст.
ТЕКСТ_СТРАНИЦЫ = (
    "нарисоватьКоманды", "комСекция", "комЧасти", "комПлашка", "комКарточка",
    "комСписокПрограмм",
)


def _apps_file(self) -> Path:
    """Временный `apps.json` вместо пользовательского списка программ."""
    папка = Path(tempfile.mkdtemp(prefix="truba-commands-"))
    self.addCleanup(shutil.rmtree, папка, True)
    файл = папка / "apps.json"
    файл.write_text(json.dumps(ПРИМЕРЫ, ensure_ascii=False), encoding="utf-8")
    подмена = mock.patch.object(launcher, "APPS_FILE", файл)
    подмена.start()
    self.addCleanup(подмена.stop)
    return файл


class GuideTests(unittest.TestCase):
    """`commands_guide()` — то, что страница получит от сервера."""

    def setUp(self):
        _apps_file(self)

    def test_every_command_comes_through_in_the_table_order(self):
        гайд = commands.commands_guide()
        self.assertEqual([часть["id"] for часть in гайд["commands"]],
                         [spec.id for spec in commands.COMMANDS])
        for часть, spec in zip(гайд["commands"], commands.COMMANDS):
            self.assertEqual(часть["title"], spec.title)
            self.assertEqual(часть["examples"], list(spec.examples))
            self.assertEqual(часть["does"], spec.does)
            self.assertEqual(часть["arg"], spec.arg)

    def test_the_window_is_whole_seconds(self):
        with mock.patch.object(config, "FOLLOW_UP_WINDOW", 12.7):
            окно = commands.commands_guide()["window"]
        self.assertIsInstance(окно, int)
        self.assertEqual(окно, 12)

    def test_programs_come_with_their_speech_names(self):
        приложения = commands.commands_guide()["apps"]
        self.assertEqual([часть["title"] for часть in приложения],
                         ["Discord", "YouTube", "Claude"])
        by_title = {часть["title"]: часть for часть in приложения}
        # Прозвища — и встроенные, и свои из apps.json: распознавание слышит
        # и «дискорд», и «ютюб».
        self.assertIn("дискорд", by_title["Discord"]["aliases"])
        self.assertIn("ютюб", by_title["YouTube"]["aliases"])

    def test_a_program_without_a_title_is_skipped(self):
        # Кнопка без названия — её не видно и на телефоне; в инструкции она
        # была бы строкой без смысла.
        с_пустым = ПРИМЕРЫ + [{"id": "steam", "title": "  ", "kind": "app"}]
        with mock.patch.object(launcher, "read_list", return_value=с_пустым):
            приложения = commands.commands_guide()["apps"]
        self.assertEqual([часть["title"] for часть in приложения],
                         ["Discord", "YouTube", "Claude"])


def _порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class EndpointTests(unittest.TestCase):
    """`GET /api/commands` — тот же ответ, только отданный сервером."""

    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_порт())
        cls.server.start()
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        _apps_file(self)
        self.client = TestClient(self.server._app)

    def test_the_page_gets_ok_and_the_guide(self):
        body = self.client.get("/api/commands").json()
        self.assertTrue(body["ok"])
        self.assertIsInstance(body["window"], int)
        self.assertEqual([часть["id"] for часть in body["commands"]],
                         [spec.id for spec in commands.COMMANDS])
        for часть in body["commands"]:
            self.assertEqual(set(часть), {"id", "title", "examples", "does", "arg"})
        self.assertEqual([часть["title"] for часть in body["apps"]],
                         ["Discord", "YouTube", "Claude"])

    def test_it_matches_what_the_guide_function_gives(self):
        # Страница и эндпоинт не должны разойтись: эндпоинт только отдаёт.
        # «skills» — второй блок «понимает по смыслу», он приходит оттуда же.
        body = self.client.get("/api/commands").json()
        гайд = commands.commands_guide()
        self.assertEqual(
            {key: body[key] for key in ("window", "commands", "skills", "apps")},
            гайд)


def _тело(скрипт: str, имя: str) -> str:
    """Текст функции `имя` — от объявления до следующей того же уровня."""
    начало = скрипт.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = скрипт[начало:]
    новое = re.search(r"^\n(?:async )?function \w+\(", остаток[1:], re.M)
    if новое:
        return остаток[: новое.start() + 1]
    return остаток


def _страница(скрипт: str) -> str:
    """Весь код страницы «Команды» слитно: функции идут подряд."""
    return "".join(_тело(скрипт, имя) for имя in ТЕКСТ_СТРАНИЦЫ)


class PageTests(unittest.TestCase):
    """Пульт: пункт меню, раздел и страница без разметки из данных."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.html = PULT_HTML.read_text(encoding="utf-8")
        cls.css = (PULT_JS.parent / "pult.css").read_text(encoding="utf-8")
        cls.код = _страница(cls.js)

    def test_the_menu_has_the_item_after_programs(self):
        self.assertIn('data-раздел="команды"', self.html)
        self.assertLess(self.html.index('data-раздел="программы"'),
                        self.html.index('data-раздел="команды"'))

    def test_the_section_is_registered_and_opened(self):
        self.assertIn("команды: {", self.js)
        self.assertIn("имя: 'Команды'", self.js)
        self.assertIn("описание: 'Как говорить с Трубой и что она умеет'", self.js)
        self.assertIn("нарисоватьКоманды()", self.js)

    def test_the_page_reads_the_server_and_does_not_build_markup(self):
        self.assertIn("fetch('/api/commands'", self.код)
        self.assertNotIn("innerHTML", self.код)

    def test_every_helper_the_page_calls_is_defined(self):
        # Опечатка в имени (`первой` вместо `первая`) роняет страницу целиком,
        # а `node --check` её пропускает. Зовём и объявляем одинаково.
        объявлены = set(re.findall(r"^function (\w+)\(", self.код, re.M))
        for имя in ТЕКСТ_СТРАНИЦЫ:
            self.assertIn(имя, объявлены)
        for имя in set(re.findall(r"(?<![\w.])(ком\w+)\(", self.код)):
            self.assertIn(имя, объявлены, "вызвана не объявленная %s" % имя)

    def test_every_class_the_page_uses_is_styled(self):
        # Каждый новый класс обязан быть в pult.css: иначе карточка рисуется
        # без рамки и читается как пустая полоса.
        for класс in set(re.findall(r"className = '([\w-]+)'", self.код)):
            self.assertIn("." + класс, self.css, "класса %s нет в pult.css" % класс)

    def test_there_is_no_green_in_the_page_decor(self):
        # Зелёный в оформлении пульта запрещён: янтарный акцент, синий второй.
        своё = self.css[self.css.index("/* ---------- Команды:"):]
        for зелёный in ("--живой", "#4ea86a", "green"):
            self.assertNotIn(зелёный, своё)

    def test_the_titles_of_the_sections_are_there(self):
        # Страница содержит вызов, мгновенные команды и навыки по смыслу.
        for заголовок in ("Как позвать и понять, что услышала",
                          "Мгновенные команды — без облака, сразу",
                          "Всё остальное — своими словами"):
            self.assertIn(заголовок, self.код)


if __name__ == "__main__":
    unittest.main()
