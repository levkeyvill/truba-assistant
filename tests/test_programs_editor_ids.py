"""Имена кнопок и пунктов меню: придумываются, не повторяются, одинаковы у
клиента и сервера.

Хозяин пишет название обычное русское («Мой блог»), а телефон узнаёт кнопку по
латинскому `id`. Имя придумывает и пульт, и сервер — и если они посчитают
разное, то телефон получит не то имя, что на экране, а следующая правка
рассыплется. Поэтому проверяем оба счёта рядом и сравниваем.

Живых данных здесь нет: `apps.json` и значки подменяются временными
(`mock.patch.object`), микрофон, сеть и программы хозяина не трогаются.
"""

import json
import re
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import config
from core import app_icons, launcher
from ui.web_runtime import WebRuntime

PULT_JS = config.ROOT / "ui" / "web" / "pult.js"


def _node(скрипт: str) -> str:
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


def _куски(имена) -> str:
    """Текст нужных функций `pult.js` насквозь, как их видит node."""
    скрипт = PULT_JS.read_text(encoding="utf-8")
    куски = []
    for имя in имена:
        начало = скрипт.find("function " + имя + "(")
        assert начало >= 0, "функция %s не найдена" % имя
        остаток = скрипт[начало:]
        # Следующая функция — конец куска. `pult.js` записан в CRLF, поэтому
        # ищем перевод строки вместе с `\r`: иначе кусок не обрезался и тянул
        # за собой пол-страницы с дублями переменных.
        новое = re.search(r"\n(?:async )?function \w+\(", остаток[1:])
        if новое:
            остаток = остаток[: новое.start() + 1]
        куски.append(остаток.replace("\r\n", "\n"))
    return "\n".join(куски)


def _таблица() -> str:
    """`ПРОГ_ТРАНСЛИТ` из `pult.js` — без неё нет функции придумывания имени."""
    скрипт = PULT_JS.read_text(encoding="utf-8")
    начало = скрипт.find("const ПРОГ_ТРАНСЛИТ")
    assert начало >= 0, "ПРОГ_ТРАНСЛИТ не найден"
    конец = скрипт.find("};", начало)
    return скрипт[начало:конец + 2].replace("\r\n", "\n")


def _клиент(подготовка: str, проверка: str) -> dict:
    """Исполняет вырезанные функции пульта в node с подставленным списком."""
    куски = (_таблица() + "\n"
             + _куски(("прогIdИзНазвания", "прогСвоёId", "прогОсноваIdКнопки",
                       "прогСвободныйIdКнопки", "прогСвободныйIdПункта")))
    # Таблица и функции идут первыми: `const ПРОГ_ТРАНСЛИТ` недоступен, пока
    # не дошла строка объявления, а подстановка зовёт функции сразу.
    стаб = "const прогПриложения = [];\n" + подготовка + "\n"
    return json.loads(_node(куски + "\n" + стаб + проверка))


class ИмяКнопки(unittest.TestCase):
    """`launcher.чистое_id` — серверное правило имени."""

    def test_латинское_имя_остаётся_как_есть(self):
        # Написанное руками имя телефон помнит: менять его — значит потерять
        # плитку у хозяина.
        self.assertEqual(launcher.чистое_id("discord", "Мой блог", []), "discord")

    def test_пустое_имя_берётся_из_русского_названия(self):
        self.assertEqual(launcher.чистое_id("", "Мой блог", []), "moyblog")

    def test_русское_имя_тоже_заменяется_латиницей(self):
        # Пульт сначала оставлял русский `id` в форме, а сервер придумывал
        # латинский: телефон получал имя, которого не было на экране.
        self.assertEqual(launcher.чистое_id("мой блог", "Мой блог", []), "moyblog")

    def test_два_одинаковых_названия_дают_разные_имена(self):
        первый = launcher.чистое_id("", "Блог", [])
        второй = launcher.чистое_id("", "Блог", [первый])
        self.assertEqual(первый, "blog")
        self.assertEqual(второй, "blog2")

    def test_имя_не_длиннее_64_знаков(self):
        имя = launcher.чистое_id("", "Щ" * 60, [])
        self.assertLessEqual(len(имя), 64, имя)

    def test_номер_влезает_в_предел_и_не_столкнулся(self):
        # Длинное название обрезается до 64, и номер дописывается в обрезок:
        # иначе обрезанный хвост снова стал бы занятым именем.
        занято = {"а" * 64}
        имя = launcher.чистое_id("", "а" * 60, занято)
        self.assertLessEqual(len(имя), 64, имя)
        self.assertNotIn(имя, занято)

    def test_пункт_меню_не_длиннее_40_знаков(self):
        занято = {"б" * 40}
        имя = launcher.чистое_id("", "б" * 60, занято, 40)
        self.assertLessEqual(len(имя), 40, имя)
        self.assertNotIn(имя, занято)

    def test_написанное_длиннее_предела_не_уважается(self):
        # Пункт меню — 40 знаков, кнопка — 64: имя длиннее не пройдёт проверку
        # телефона, поэтому такое лучше заменить, чем записать.
        self.assertEqual(launcher.чистое_id("я" * 41, "Пункт", [], 40), "punkt")


class СохранениеСписка(unittest.TestCase):
    """`apps_save` со всеми видами кнопок и подменю — на временных данных."""

    def _среда(self):
        среда = WebRuntime.__new__(WebRuntime)
        среда.voice = types.SimpleNamespace(_apps=None)
        среда._bg = mock.Mock()
        return среда

    def _сохранить(self, items, прежние=()):
        среда = self._среда()
        записано = {}
        with mock.patch.object(launcher, "read_list", return_value=list(прежние)), \
                mock.patch.object(launcher, "save_list",
                                  side_effect=lambda список: записано.setdefault("список", список)):
            итог = среда.apps_save(items)
        return итог, записано.get("список")

    def test_все_виды_кнопок_сохраняются(self):
        items = [
            {"title": "Дискорд", "kind": "app", "path": r"C:\Discord\Discord.exe"},
            {"title": "Мой блог", "kind": "url", "url": "https://example.com/blog"},
            {"title": "Телеграм", "kind": "store", "app_id": "9WZDNCRFJ2P"},
            {"title": "Проекты", "kind": "folder", "path": r"C:\Проекты"},
        ]
        итог, список = self._сохранить(items)
        self.assertTrue(итог["ok"], итог)
        self.assertEqual([запись["kind"] for запись in список],
                         ["app", "url", "store", "folder"])
        # Русское название без ручного `id` — обычное дело, а не ошибка.
        self.assertEqual([запись["id"] for запись in список],
                         ["diskord", "moyblog", "telegram", "proekty"])
        # В ответе — то, что записали: пульт обновляет по нему поля формы.
        self.assertEqual([запись["id"] for запись in итог["apps"]],
                         [запись["id"] for запись in список])

    def test_подменю_с_сайтом_и_сочетанием(self):
        items = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"C:\Discord\Discord.exe",
            "menu": [
                {"kind": "hotkey", "title": "Микрофон", "keys": "ctrl+shift+m"},
                {"kind": "site", "title": "Мой профиль", "url": "https://example.com/me"},
            ],
        }]
        итог, список = self._сохранить(items)
        self.assertTrue(итог["ok"], итог)
        меню = список[0]["menu"]
        self.assertEqual([пункт["id"] for пункт in меню], ["mikrofon", "moyprofil"])
        self.assertEqual(меню[1]["url"], "https://example.com/me")
        self.assertEqual(меню[0]["keys"], "ctrl+shift+m")

    def test_подменю_третьего_пункта_не_столкнулось(self):
        items = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"C:\Discord\Discord.exe",
            "menu": [
                {"kind": "site", "title": "Профиль", "url": "https://a.example"},
                {"kind": "site", "title": "Профиль", "url": "https://b.example"},
                {"kind": "site", "title": "Профиль", "url": "https://c.example"},
            ],
        }]
        итог, список = self._сохранить(items)
        self.assertTrue(итог["ok"], итог)
        имена = [пункт["id"] for пункт in список[0]["menu"]]
        self.assertEqual(len(set(имена)), 3, имена)

    def test_плохое_подменю_не_записывается(self):
        items = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"C:\Discord\Discord.exe",
            "menu": [{"kind": "site", "title": "Профиль", "url": "file:///C:/x"}],
        }]
        итог, список = self._сохранить(items)
        self.assertFalse(итог["ok"])
        self.assertIsNone(список)
        self.assertIn("http", итог["error"])

    def test_плохое_меню_отвечает_парой_на_всех_ветках(self):
        # `_check_menu` отвечает парой (ошибка, пункты): все ветки — одного вида,
        # иначе сервер упал бы на `why, разобранные = ...` при плохом поле.
        среда = self._среда()
        for плохое in ({"пункт": 1}, [{"kind": "site"}], "строка", [5],
                       [{"kind": "hotkey", "title": "Тихий", "keys": "ctrl+q"}] * 25):
            with self.subTest(плохое=плохое):
                why, пункты = среда._check_menu("discord", плохое)
                self.assertTrue(why)
                self.assertIsNone(пункты)

    def test_прежние_поля_не_теряются(self):
        # `process` и `aliases` пульт может не прислать — берём из прежней записи.
        прежние = [{"id": "chatgpt", "title": "ChatGPT", "kind": "app",
                    "path": r"C:\x\ChatGPT.exe", "process": "ChatGPT.exe",
                    "aliases": ["чатик"]}]
        итог, список = self._сохранить(
            [{"id": "chatgpt", "title": "ChatGPT", "kind": "app",
              "path": r"C:\x\ChatGPT.exe"}], прежние)
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(список[0]["process"], "ChatGPT.exe")
        self.assertEqual(список[0]["aliases"], ["чатик"])

    def test_предпросмотр_трёх_кнопок_ничего_не_пишет(self):
        # Значки для макета: список и apps.json при этом не меняются.
        среда = self._среда()
        items = [
            {"id": "a", "kind": "url", "url": "https://example.com"},
            {"id": "b", "kind": "app", "path": r"C:\нет\такой.exe"},
            {"id": "c", "kind": "folder", "path": r"C:\Проекты"},
        ]
        with mock.patch.object(launcher, "resolve", return_value=None), \
                mock.patch.object(app_icons, "picture_for", return_value=None), \
                mock.patch.object(launcher, "save_list") as сохранение:
            значки = среда.apps_preview(items)
        self.assertEqual([значок["id"] for значок in значки], ["a", "b", "c"])
        сохранение.assert_not_called()


class ИмяВПульте(unittest.TestCase):
    """Счёт имени в `pult.js` — тем же правилом, что на сервере."""

    def test_пустое_имя_придумывается_из_названия(self):
        # Сосед с тем же названием занял `moyblog`, поэтому вторая строка
        # получает номер — ровно как на сервере.
        итог = _клиент(
            "прогПриложения.push({ id: 'moyblog', title: 'Мой блог' });"
            "прогПриложения.push({ id: '', title: 'Мой блог' });",
            "console.log(JSON.stringify({ имя: прогСвободныйIdКнопки(1, 'Мой блог') }));")
        self.assertEqual(итог["имя"], "moyblog2")

    def test_строки_собираются_по_очереди_и_не_берут_одно_имя(self):
        # Так это и делает `прогСобратьСтроку`: имя каждой строки сразу встаёт
        # в её поле, поэтому следующая строка видит занятое имя.
        итог = _клиент(
            "прогПриложения.push({ id: '', title: 'Мой блог' });"
            "прогПриложения.push({ id: '', title: 'Мой блог' });"
            "const первый = прогСвободныйIdКнопки(0, 'Мой блог');"
            "прогПриложения[0].id = первый;"
            "const второй = прогСвободныйIdКнопки(1, 'Мой блог');",
            "console.log(JSON.stringify({ первый, второй }));")
        self.assertEqual(итог["первый"], "moyblog")
        self.assertEqual(итог["второй"], "moyblog2")

    def test_своё_латинское_имя_не_трогается(self):
        итог = _клиент(
            "прогПриложения.push({ id: 'Discord' }, { id: '' });",
            "console.log(JSON.stringify({ id: прогСвоёId(' Discord '),"
            " рус: прогСвоёId('мой блог'), пусто: прогСвоёId('') }));")
        self.assertEqual(итог["id"], "Discord")
        # Русское имя пульт обязан заменить: телефон его не узнает.
        self.assertEqual(итог["рус"], "")
        self.assertEqual(итог["пусто"], "")

    def test_имя_кнопки_не_длиннее_64(self):
        итог = _клиент(
            "прогПриложения.push({ id: 'щ'.repeat(64) });"
            "прогПриложения.push({ id: '' });",
            "console.log(JSON.stringify({ id: прогСвободныйIdКнопки(1, 'щ'.repeat(60)) }));")
        self.assertLessEqual(len(итог["id"]), 64, итог["id"])
        self.assertNotEqual(итог["id"], "щ" * 64)

    def test_имя_пункта_не_длиннее_40(self):
        итог = _клиент(
            "const список = [{ id: 'item' }];",
            "console.log(JSON.stringify({ id: прогСвободныйIdПункта(список, 'item') }));")
        self.assertEqual(итог["id"], "item2")

    def test_клиент_и_сервер_считают_одно_и_то_же(self):
        # Один и тот же список названий прогоняем через node и через Python:
        # разошлись бы — телефон получил бы не то имя, что на экране.
        названия = ["Мой блог", "Дискорд", "Щёлкалка", "Профиль", "Мой блог",
                    "Очень длинное название программы для проверки обрезки имени"]
        # Каждая строка — отдельная запись списка, и её имя возвращается в поле
        # сразу, как это делает `прогСобратьСтроку`. Иначе соседние строки не
        # видели бы друг друга и взяли бы одно имя.
        подготовка = ("const портрет = [];\n"
                      "for (const название of %s) {\n"
                      "  const номер = прогПриложения.length;\n"
                      "  прогПриложения.push({ id: '', title: название });\n"
                      "  const id = прогСвободныйIdКнопки(номер, название);\n"
                      "  портрет.push(id);\n"
                      "  прогПриложения[номер].id = id;\n"
                      "}\n" % json.dumps(названия, ensure_ascii=False))
        проверка = "console.log(JSON.stringify(портрет));"
        с_клиента = _клиент(подготовка, проверка)
        занятые = []
        с_сервера = []
        for название in названия:
            имя = launcher.чистое_id("", название, занятые)
            занятые.append(имя)
            с_сервера.append(имя)
        self.assertEqual(с_клиента, с_сервера)

    def test_после_сохранения_имена_берутся_у_сервера(self):
        # Ответ сервера — последнее слово: иначе в форме осталось бы русское имя.
        блок = PULT_JS.read_text(encoding="utf-8")
        блок = блок[блок.find("async function прогСохранить("):]
        блок = блок[:блок.find("\nfunction прогЕстьЧерновикСетки")]
        self.assertIn("Array.isArray(данные.apps)", блок)
        self.assertIn("п.id = String(сохранённая.id)", блок)
        # И пул не закрывается: перерисовка панели, а не перезагрузка вкладки.
        self.assertIn("прогПерерисовать(ctx)", блок)
        self.assertNotIn("location.reload", блок)


class ПутьНазад(unittest.TestCase):
    """Кнопка «Назад» из свойств: назад к списку, а не угадывать плитку."""

    СТАБ = """
    function элемент() {
      return { type: '', className: '', textContent: '', title: '',
        listeners: {}, addEventListener(что, куда) {
          (this.listeners[что] = this.listeners[что] || []).push(куда);
        }, щелчок() { for (const куда of (this.listeners.click || [])) куда({}); } };
    }
    const document = { createElement: элемент };
    let перерисован = 0;
    const прогПриложения = [{ id: 'a' }];
    function прогПерерисовать(ctx) {
      перерисован += 1;
      ctx.свойства.innerHTML = '';
    }
    """

    def test_кнопка_возвращает_к_списку(self):
        # Проверяем исполнением: `node --check` не поймает, что обработчик
        # ничего не делает, а хозяин из свойств выйти не сможет.
        проверка = """
        const ctx = { индекс: 3, слот: 2, выбранный: 'discord',
          свойства: { innerHTML: 'было' } };
        const к = прогКнопкаНазад(ctx);
        к.щелчок();
        console.log(JSON.stringify({ надпись: к.textContent, индекс: ctx.индекс,
          слот: ctx.слот, выбранный: ctx.выбранный, перерисован }));
        """
        куски = _куски(("прогКнопкаНазад", "прогПоказатьСписок"))
        итог = json.loads(_node(self.СТАБ + "\n" + куски + "\n" + проверка))
        self.assertIn("Все программы", итог["надпись"])
        self.assertEqual(итог["индекс"], -1)
        self.assertEqual(итог["слот"], -1)
        self.assertEqual(итог["выбранный"], "")
        self.assertEqual(итог["перерисован"], 1)

    def test_кнопки_назад_видно_только_из_свойств(self):
        скрипт = PULT_JS.read_text(encoding="utf-8")
        блок = скрипт[скрипт.find("function прогПерерисовать("):]
        блок = блок[:блок.find("\nfunction ")]
        self.assertIn("ctx.шапкаРяд.hidden = !выбрано", блок)

    def test_у_кнопки_назад_есть_стиль(self):
        css = (config.ROOT / "ui" / "web" / "pult.css").read_text(encoding="utf-8")
        self.assertIn(".прог-назад", css)


if __name__ == "__main__":
    unittest.main()
