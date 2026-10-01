"""Кнопки-переключатели в подменю: состояние, поправка, подсветка на телефоне.

Ни одного настоящего нажатия и ни одного настоящего процесса тут нет:
`hotkeys.press` подменён, а время старта процесса задаёт тест. Иначе прогон
тестов жал бы клавиши хозяину на живом столе.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core import hotkeys, launcher, toggles

APPS = [
    {
        "id": "discord", "title": "Discord", "kind": "app",
        "path": "Discord.exe", "args": ["--processStart", "Discord.exe"],
        "menu": [
            {"kind": "hotkey", "id": "mute", "title": "Микрофон", "icon": "mic",
             "keys": "ctrl+shift+alt+m", "toggle": True},
            {"kind": "hotkey", "id": "deafen", "title": "Звук",
             "icon": "headphones", "keys": "ctrl+shift+alt+d",
             "toggle": True, "implies": "mute"},
            {"kind": "site", "id": "gh", "title": "GitHub",
             "url": "https://github.com"},
        ],
    },
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe"},
]

# Время старта, каким его видит тест. Поменялось — программа перезапустилась.
СТАРТ = [1000.0]


class Базовый(unittest.TestCase):
    def setUp(self):
        toggles.forget()
        СТАРТ[0] = 1000.0
        self.addCleanup(toggles.forget)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.нажато = []
        patcher = mock.patch.object(hotkeys, "press", side_effect=self.нажато.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        # psutil в тесте не нужен: время старта процесса задаёт сам тест.
        patcher = mock.patch.object(toggles, "_process_start",
                                    side_effect=lambda item: СТАРТ[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def состояние(self):
        """Что ушло бы телефону сейчас."""
        return launcher.menu_states("discord")


class НажатиеТесты(Базовый):
    def test_нажатие_переключателя_жмёт_и_переворачивает(self):
        ok, _ = launcher.run_menu("discord", "hotkey:mute")
        self.assertTrue(ok)
        self.assertEqual(self.нажато, [[0x11, 0x10, 0x12, 0x4D]])
        self.assertIs(self.состояние()["hotkey:mute"], True)

    def test_второе_нажатие_возвращает_обратно(self):
        launcher.run_menu("discord", "hotkey:mute")
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self.состояние()["hotkey:mute"], False)

    def test_у_сайта_состояния_нет(self):
        self.assertNotIn("site:gh", self.состояние())

    def test_нажатие_сайта_состояние_не_трогает(self):
        with mock.patch.object(launcher.subprocess, "Popen"):
            launcher.run_menu("discord", "site:gh")
        self.assertIs(self.состояние()["hotkey:mute"], False)

    def test_неудачное_нажатие_состояние_не_меняет(self):
        # Нажатие, которое система не приняла, подсветку бы не изменило:
        # иначе телефон врал бы, что микрофон выключен.
        with mock.patch.object(hotkeys, "press", side_effect=OSError("запрещено")):
            ok, _ = launcher.run_menu("discord", "hotkey:mute")
        self.assertFalse(ok)
        self.assertIs(self.состояние()["hotkey:mute"], False)

    def test_чужой_ключ_состояние_не_меняет(self):
        launcher.run_menu("discord", "hotkey:выключить-всё")
        self.assertEqual(self.состояние(),
                         {"hotkey:mute": False, "hotkey:deafen": False})



class ПоправкаТесты(Базовый):
    def test_подгон_переворачивает_без_нажатия(self):
        ok, what = launcher.flip_menu("discord", "hotkey:mute")
        self.assertTrue(ok)
        self.assertIn("Микрофон", what)
        self.assertEqual(self.нажато, [])
        self.assertIs(self.состояние()["hotkey:mute"], True)

    def test_подгон_не_запускает_программу(self):
        with mock.patch.object(launcher.subprocess, "Popen") as пуск:
            launcher.flip_menu("discord", "hotkey:mute")
        пуск.assert_not_called()

    def test_чужой_ключ_в_подгонке_отказ(self):
        ok, what = launcher.flip_menu("discord", "hotkey:выключить-всё")
        self.assertFalse(ok)
        self.assertIn("нет такого пункта", what)

    def test_чужой_пункт_чужой_программы_отказ(self):
        ok, what = launcher.flip_menu("youtube", "hotkey:mute")
        self.assertFalse(ok)
        self.assertIn("нет такого пункта", what)

    def test_не_переключатель_подгонкой_не_бывает(self):
        ok, what = launcher.flip_menu("discord", "site:gh")
        self.assertFalse(ok)
        self.assertIn("не переключатель", what)

    def test_битое_сообщение_не_ломает(self):
        for app, item in ((None, "hotkey:mute"), ("discord", None),
                          ("", ""), (5, 5)):
            ok, _ = launcher.flip_menu(app, item)
            self.assertFalse(ok)


class ПерезапускТесты(Базовый):
    def test_другое_время_старта_сбрасывает_состояние(self):
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self.состояние()["hotkey:mute"], True)
        СТАРТ[0] = 2000.0  # Discord перезапустили
        self.assertIs(self.состояние()["hotkey:mute"], False)

    def test_исчезнувший_процесс_сбрасывает_состояние(self):
        launcher.run_menu("discord", "hotkey:mute")
        СТАРТ[0] = None  # процесса нет вовсе
        self.assertIs(self.состояние()["hotkey:mute"], False)

    def test_сброс_касается_всех_пунктов_программы(self):
        launcher.run_menu("discord", "hotkey:mute")
        launcher.run_menu("discord", "hotkey:deafen")
        СТАРТ[0] = 2000.0
        self.assertEqual(self.состояние(),
                         {"hotkey:mute": False, "hotkey:deafen": False})

    def test_пока_процесс_тот_же_сброса_нет(self):
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self.состояние()["hotkey:mute"], True)

    def test_после_сброса_счёт_идёт_с_нуля(self):
        launcher.run_menu("discord", "hotkey:mute")
        СТАРТ[0] = 2000.0
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self.состояние()["hotkey:mute"], True)

    def test_чужая_программа_не_сбрасывается(self):
        launcher.run_menu("discord", "hotkey:mute")
        СТАРТ[0] = 2000.0
        toggles.flip("firefox", APPS[1], "hotkey:что-то")
        self.assertIs(toggles.own("firefox", "hotkey:что-то"), True)


class ЗависимостьТесты(Базовый):
    def test_звук_включён_микрофон_тоже_подсвечен(self):
        launcher.run_menu("discord", "hotkey:deafen")
        on = self.состояние()
        self.assertIs(on["hotkey:deafen"], True)
        self.assertIs(on["hotkey:mute"], True)

    def test_связь_только_в_показе(self):
        # Выключенный звук выключает микрофон в Discord, но состояние
        # микрофона от этого не меняется: телефон должен правдить дальше.
        launcher.run_menu("discord", "hotkey:deafen")
        self.assertFalse(toggles.own("discord", "hotkey:mute"))
        self.assertIs(self.состояние()["hotkey:mute"], True)

    def test_микрофон_включён_звук_не_трогает(self):
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self.состояние()["hotkey:deafen"], False)

    def test_подсвеченный_сосед_не_считается_настоящим(self):
        # «Звук» подсвечен из-за `implies`, и его состояние не должно
        # выглядеть настоящим включением: выключим «Звук» — и погаснет он
        # сам, а «Микрофон», включённый по-настоящему, гореть останется.
        launcher.run_menu("discord", "hotkey:deafen")
        launcher.run_menu("discord", "hotkey:mute")
        launcher.run_menu("discord", "hotkey:deafen")
        on = self.состояние()
        self.assertIs(on["hotkey:deafen"], False)
        self.assertIs(on["hotkey:mute"], True)
        self.assertIs(toggles.own("discord", "hotkey:mute"), True)

    def test_без_связи_всё_просто(self):
        простой = [{"id": "a", "title": "A", "kind": "app", "path": "a.exe",
                    "menu": [
                        {"kind": "hotkey", "id": "m", "title": "М",
                         "keys": "ctrl+m", "toggle": True},
                        {"kind": "hotkey", "id": "s", "title": "С",
                         "keys": "ctrl+s", "toggle": True},
                    ]}]
        with mock.patch.object(launcher, "read_list", return_value=простой):
            launcher.run_menu("a", "hotkey:s")
            self.assertEqual(launcher.menu_states("a"),
                             {"hotkey:m": False, "hotkey:s": True})


class ТелефонуТесты(Базовый):
    """Что телефон рисует: `toggle` и `on` у каждого пункта меню."""

    def _пункты(self):
        return {п["key"]: п for п in launcher.build_menu(APPS[0])}

    def test_у_переключателя_есть_toggle_и_on(self):
        пункт = self._пункты()["hotkey:mute"]
        self.assertIs(пункт["toggle"], True)
        self.assertIs(пункт["on"], False)

    def test_после_нажатия_on_становится_true(self):
        launcher.run_menu("discord", "hotkey:mute")
        self.assertIs(self._пункты()["hotkey:mute"]["on"], True)

    def test_у_обычного_пункта_нет_ни_toggle_ни_on(self):
        пункт = self._пункты()["site:gh"]
        self.assertNotIn("toggle", пункт)
        self.assertNotIn("on", пункт)

    def test_у_программы_без_меню_состояния_нет(self):
        self.assertEqual(launcher.menu_states("firefox"), {})


class ПроверкаJsonТесты(unittest.TestCase):
    """Что пульт записал — то и прочитается, а мусор в apps.json не пройдёт."""

    def _пункт(self, **поле):
        основа = {"kind": "hotkey", "id": "mute", "title": "Микрофон",
                  "keys": "ctrl+shift+alt+m"}
        основа.update(поле)
        return основа

    def _ошибка(self, меню):
        # `_check_menu` отвечает парой (текст ошибки, готовые пункты): имя
        # пункта придумывает сервер, и пульту нужно то, что он записал.
        from ui.web_runtime import WebRuntime

        return WebRuntime._check_menu("discord", меню)[0]

    def _проверка(self, меню):
        from ui.web_runtime import WebRuntime

        return WebRuntime._check_menu("discord", меню)

    def test_честный_toggle_проходит(self):
        self.assertEqual(self._ошибка([
            self._пункт(),
            self._пункт(id="deafen", title="Звук", keys="ctrl+shift+alt+d",
                        toggle=True, implies="mute"),
        ]), "")

    def test_toggle_не_bool_ошибка(self):
        for плохо in ("да", 1, [], {}):
            with self.subTest(значение=плохо):
                self.assertIn("переключатель", self._ошибка([self._пункт(toggle=плохо)]))

    def test_implies_в_пустоту_ошибка(self):
        why = self._ошибка([self._пункт(toggle=True, implies="netakogo")])
        self.assertIn("netakogo", why)
        self.assertIn("меню 1", why)

    def test_implies_на_сам_себя_ошибка(self):
        why = self._ошибка([self._пункт(toggle=True, implies="mute")])
        self.assertIn("меню 1", why)

    def test_implies_не_латиницей_ошибка(self):
        why = self._ошибка([self._пункт(toggle=True, implies="микрофон")])
        self.assertIn("меню 1", why)

    def test_связь_с_другим_пунктом_проходит(self):
        self.assertEqual(self._ошибка([
            self._пункт(toggle=True),
            self._пункт(id="deafen", title="Звук", keys="ctrl+shift+alt+d",
                        toggle=True, implies="mute"),
        ]), "")

    def test_пустое_implies_не_ошибка(self):
        self.assertEqual(self._ошибка([self._пункт(toggle=True, implies="")]), "")

    def test_имя_пункта_придумывается_из_русского_названия(self):
        # Хозяину не нужно переводить «Микрофон» в `microphone` руками: сервер
        # придумывает имя сам и отдаёт его пульту вместе с проверкой.
        why, пункты = self._проверка([{"kind": "hotkey", "id": "",
                                        "title": "Микрофон",
                                        "keys": "ctrl+shift+alt+m"}])
        self.assertEqual(why, "")
        self.assertEqual(пункты[0]["id"], "mikrofon")

    def test_мусорный_toggle_в_apps_json_пункт_убирает(self):
        # apps.json правят руками; плохой пункт молча пропускается, а не
        # показывается на телефоне и не ломает всё меню.
        item = {"id": "x", "menu": [
            {"kind": "hotkey", "id": "ok", "title": "Ок", "keys": "ctrl+m"},
            {"kind": "hotkey", "id": "мусор", "title": "Мусор", "keys": "ctrl+n",
             "toggle": "да"},
        ]}
        self.assertEqual([п["key"] for п in launcher.build_menu(item)], ["hotkey:ok"])

    def test_implies_на_несуществующий_пункт_убирается(self):
        item = {"id": "x", "menu": [
            {"kind": "hotkey", "id": "a", "title": "А", "keys": "ctrl+a",
             "toggle": True, "implies": "netakogo"},
        ]}
        self.assertNotIn("implies", launcher.read_menu(item)[0])

    def test_implies_на_сам_себя_убирается(self):
        item = {"id": "x", "menu": [
            {"kind": "hotkey", "id": "a", "title": "А", "keys": "ctrl+a",
             "toggle": True, "implies": "a"},
        ]}
        self.assertNotIn("implies", launcher.read_menu(item)[0])

    def test_связь_остаётся_при_чтении(self):
        item = {"id": "x", "menu": [
            {"kind": "hotkey", "id": "a", "title": "А", "keys": "ctrl+a",
             "toggle": True},
            {"kind": "hotkey", "id": "b", "title": "Б", "keys": "ctrl+b",
             "toggle": True, "implies": "a"},
        ]}
        self.assertEqual(launcher.read_menu(item)[1]["implies"], "a")

    def test_поля_проходят_через_clean_menu(self):
        чисто = launcher.clean_menu([
            {"kind": "hotkey", "id": "b", "title": "Б", "keys": " ctrl+b ",
             "toggle": True, "implies": "a"},
        ])
        self.assertEqual(чисто[0]["keys"], "ctrl+b")
        self.assertIs(чисто[0]["toggle"], True)
        self.assertEqual(чисто[0]["implies"], "a")


class ПультСообщенияТесты(Базовый):
    """`menu` и `menu_flip` с телефона: состояние уходит всем телефонам."""

    def setUp(self):
        super().setUp()
        import collections
        import threading
        from types import SimpleNamespace as NS

        from ui.web_runtime import WebRuntime

        self.состояния = []
        self.тосты = []
        self.среда = object.__new__(WebRuntime)
        self.среда._lock = threading.Lock()
        # Потоки не нужны: _bg выполняет функцию сразу.
        self.среда._bg = lambda func, *args: func(*args)
        self.среда.события = []
        self.среда._ids = iter(range(1, 100))
        self.среда._overview = {}
        self.среда._events = collections.deque(maxlen=10)
        self.среда._remember = lambda kind, payload: self.среда.события.append(
            (kind, payload))
        self.среда.server = NS(
            send_toast=lambda text, ok=True: self.тосты.append((text, ok)),
            send_sound=lambda name: None,
            send_menu_state=lambda app, on: self.состояния.append((app, on)),
        )
        patcher = mock.patch("core.bookmarks.read", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_после_нажатия_состояние_уходит_телефону(self):
        self.среда.handle_event("menu", {"app": "discord", "item": "hotkey:mute"})
        self.assertEqual(self.нажато, [[0x11, 0x10, 0x12, 0x4D]])
        self.assertEqual(len(self.состояния), 1)
        приложение, on = self.состояния[0]
        self.assertEqual(приложение, "discord")
        self.assertIs(on["hotkey:mute"], True)

    def test_подгон_шлёт_состояние_без_нажатия(self):
        self.среда.handle_event("menu_flip",
                                {"app": "discord", "item": "hotkey:mute"})
        self.assertEqual(self.нажато, [])
        self.assertEqual(len(self.состояния), 1)
        self.assertIs(self.состояния[0][1]["hotkey:mute"], True)
        self.assertIn("подсветка поправлена", self.тосты[0][0])

    def test_подгон_чужого_ключа_ничего_не_шлёт(self):
        self.среда.handle_event("menu_flip", {"app": "discord", "item": "hotkey:нет"})
        self.assertEqual(self.состояния, [])
        self.assertIs(self.тосты[0][1], False)

    def test_запуск_программы_состояния_не_шлёт(self):
        with mock.patch.object(launcher, "launch", return_value=(True, "Discord")):
            self.среда.handle_event("menu", {"app": "discord", "item": "launch"})
        self.assertEqual(self.состояния, [])

    def test_подгон_попадает_в_журнал(self):
        self.среда.handle_event("menu_flip", {"app": "discord", "item": "hotkey:mute"})
        вид, текст = self.среда.события[-1]
        self.assertEqual(вид, "menu_flipped")
        self.assertIn("Микрофон", текст["line"])

    def test_подгон_в_журнале_читается_строкой(self):
        from ui.web_runtime import WebRuntime

        строки = WebRuntime._log_messages("menu_flipped",
                                          {"line": "подсветка: Микрофон"})
        self.assertEqual(строки, ["подсветка переключателя: подсветка: Микрофон"])

    def test_сломанное_сообщение_не_поднимает(self):
        for payload in (None, {}, {"app": None, "item": 5}, {"app": "discord"}):
            self.среда.handle_event("menu_flip", payload)
        self.assertEqual(self.состояния, [])


class СтраницаТесты(unittest.TestCase):
    """Скрипты страниц должны собираться: опечатка видна только на телефоне."""

    def test_скрипт_телефона_проходит_node_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node не установлен")
        страница = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        скрипты = re.findall(r"<script>(.*?)</script>", страница, re.S)
        self.assertEqual(len(скрипты), 1, "ожидался один блок <script>")
        папка = Path(tempfile.mkdtemp(prefix="truba-toggle-"))
        self.addCleanup(shutil.rmtree, папка, True)
        файл = папка / "page.js"
        файл.write_text(скрипты[0], encoding="utf-8")
        result = subprocess.run([node, "--check", str(файл)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_скрипт_пульта_проходит_node_check(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node не установлен")
        result = subprocess.run(
            [node, "--check", str(config.ROOT / "ui" / "web" / "pult.js")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_страница_знает_новые_сообщения(self):
        страница = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("'menu_state'", страница)
        self.assertIn("'menu_flip'", страница)

    def test_подсветка_переключателя_красная_и_перечёркнутая(self):
        страница = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("#menu .app.flip.on", страница)
        self.assertIn("#ff5c5c", страница)
        self.assertIn("rotate(-45deg)", страница)


if __name__ == "__main__":
    unittest.main()
