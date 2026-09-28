"""Подменю программы: сочетания клавиш, закладки Firefox, выполнение.

Ни Firefox, ни нажатий, ни сети тут нет: базы закладок собираются в тесте
на временной папке, а `hotkeys.press` подменён — иначе тест жал бы клавиши
хозяину на живом столе.
"""

import io
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import bookmarks, hotkeys, launcher

APPS = [
    {
        "id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe",
        "menu": [
            {"kind": "hotkey", "id": "mute", "title": "Микрофон", "icon": "mic",
             "keys": "ctrl+shift+alt+m"},
            {"kind": "hotkey", "id": "deafen", "title": "Звук",
             "icon": "headphones", "keys": "ctrl+shift+alt+d"},
            {"kind": "site", "id": "gh", "title": "GitHub",
             "url": "https://github.com"},
        ],
    },
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe",
     "bookmarks": "firefox"},
    {"id": "youtube", "title": "YouTube", "kind": "url",
     "url": "https://youtube.com"},
]

MARKS = [
    {"id": "bm:1", "title": "Яндекс", "url": "https://ya.ru"},
    {"id": "bm:2", "title": "GitHub", "url": "https://github.com",
     "image": "data:image/png;base64,AAAA"},
]


class ParseTests(unittest.TestCase):
    def test_modifiers_and_letter(self):
        self.assertEqual(hotkeys.parse("ctrl+shift+alt+m"), [0x11, 0x10, 0x12, 0x4D])

    def test_alt_function_key(self):
        self.assertEqual(hotkeys.parse("alt+f10"), [0x12, 0x79])

    def test_win_and_space(self):
        self.assertEqual(hotkeys.parse("win+space"), [0x5B, 0x20])

    def test_order_does_not_matter(self):
        # Порядок в строке не должен менять порядок нажатия: сначала
        # модификаторы, потом клавиша — иначе программа получит голую букву.
        self.assertEqual(hotkeys.parse("alt+ctrl+m"), hotkeys.parse("ctrl+alt+m"))

    def test_spaces_between_parts_are_allowed(self):
        self.assertEqual(hotkeys.parse(" ctrl + m "), [0x11, 0x4D])

    def test_repeated_modifier_is_not_pressed_twice(self):
        self.assertEqual(hotkeys.parse("ctrl+ctrl+m"), [0x11, 0x4D])

    def test_upper_case_is_understood(self):
        self.assertEqual(hotkeys.parse("Ctrl+Shift+F5"), [0x11, 0x10, 0x74])

    def test_dangling_modifier_is_an_error(self):
        with self.assertRaises(ValueError) as ошибка:
            hotkeys.parse("ctrl+")
        self.assertIn("ctrl+shift+alt+m", str(ошибка.exception))

    def test_two_keys_are_an_error(self):
        with self.assertRaises(ValueError) as ошибка:
            hotkeys.parse("ctrl+m+k")
        self.assertIn("одна", str(ошибка.exception))

    def test_unknown_key_says_what_is_known(self):
        with self.assertRaises(ValueError) as ошибка:
            hotkeys.parse("ctrl+колесо")
        self.assertIn("f1", str(ошибка.exception))

    def test_modifiers_only_are_an_error(self):
        with self.assertRaises(ValueError) as ошибка:
            hotkeys.parse("ctrl+shift")
        self.assertIn("клавиши", str(ошибка.exception))

    def test_empty_text_is_an_error(self):
        with self.assertRaises(ValueError):
            hotkeys.parse("")

    def test_non_text_is_an_error(self):
        with self.assertRaises(ValueError):
            hotkeys.parse(None)


def _png(color=(255, 0, 0), size=32) -> bytes:
    from PIL import Image

    буфер = io.BytesIO()
    Image.new("RGBA", (size, size), color + (255,)).save(буфер, "PNG")
    return буфер.getvalue()


def _profile(папка: Path, with_icons=True) -> Path:
    """Собирает профиль Firefox, каким его оставил бы настоящий браузер."""
    root = папка / "Firefox"
    профиль = root / "Profiles" / "abc.default-release"
    профиль.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[General]\nStartWithLastProfile=1\n\n"
        "[Profile0]\nName=default-release\nIsRelative=1\n"
        "Path=Profiles/abc.default-release\nDefault=1\n\n"
        "[Install4F96D1932A9F858E]\nDefault=Profiles/abc.default-release\nLocked=1\n",
        encoding="utf-8",
    )

    # Схема как у настоящего Firefox (сверено 26.09 на профиле хозяина):
    # закладки в moz_bookmarks, адреса в moz_places. Первая версия теста
    # выдумала схему, и код с ней проходил тесты, а на живом профиле падал.
    db = sqlite3.connect(профиль / "places.sqlite")
    db.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    db.execute(
        "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INT, fk INT,"
        " parent INT, position INT, title TEXT, guid TEXT)"
    )
    db.execute("INSERT INTO moz_places VALUES (10, 'https://ya.ru', 'Яндекс')")
    db.execute("INSERT INTO moz_places VALUES (11, 'https://github.com', 'GitHub')")
    db.execute("INSERT INTO moz_bookmarks VALUES (1, 2, NULL, 0, 0, 'toolbar', 'toolbar_____')")
    # position решает порядок: вторая строка должна прийти первой.
    db.execute("INSERT INTO moz_bookmarks VALUES (2, 1, 10, 1, 5, 'Яндекс', 'aaaaaaaaaaaa')")
    db.execute("INSERT INTO moz_bookmarks VALUES (3, 1, 11, 1, 1, 'GitHub', 'bbbbbbbbbbbb')")
    # Папка внутри панели — не закладка, в список попасть не должна.
    db.execute("INSERT INTO moz_bookmarks VALUES (4, 2, NULL, 1, 2, 'Внутрь', 'cccccccccccc')")
    db.commit()
    db.close()

    if not with_icons:
        return root

    db = sqlite3.connect(профиль / "favicons.sqlite")
    db.execute("CREATE TABLE moz_pages_w_icons (id INTEGER PRIMARY KEY, page_url TEXT)")
    db.execute("CREATE TABLE moz_icons_to_pages (page_id INT, icon_id INT)")
    db.execute(
        "CREATE TABLE moz_icons (id INTEGER PRIMARY KEY, icon_url TEXT,"
        " width INT, root INT, data BLOB)"
    )
    # Два размера значка: телефон должен взять тот, что ближе к 64.
    db.execute("INSERT INTO moz_icons VALUES (1, 'https://github.com/favicon.ico', 16, 1, ?)",
               (_png(size=16),))
    db.execute("INSERT INTO moz_icons VALUES (2, 'https://github.com/favicon.ico', 64, 1, ?)",
               (_png(size=64),))
    db.execute("INSERT INTO moz_pages_w_icons VALUES (1, 'https://github.com')")
    db.execute("INSERT INTO moz_icons_to_pages VALUES (1, 1)")
    db.execute("INSERT INTO moz_icons_to_pages VALUES (1, 2)")
    db.commit()
    db.close()
    return root


class BookmarksTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-bm-test-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.корень = _profile(self.папка)
        self.ошибки = []
        patcher = mock.patch.object(
            bookmarks, "profiles_ini", return_value=self.корень / "profiles.ini"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(bookmarks.forget)
        кеш = mock.patch.object(bookmarks, "CACHE_DIR", self.папка / "icons")
        кеш.start()
        self.addCleanup(кеш.stop)

    def test_reads_in_order_with_icon(self):
        закладки = bookmarks.read(force=True)
        self.assertEqual([з["url"] for з in закладки],
                         ["https://github.com", "https://ya.ru"])
        self.assertEqual([з["id"] for з in закладки], ["bm:1", "bm:2"])
        self.assertEqual([з["title"] for з in закладки], ["GitHub", "Яндекс"])

    def test_icon_comes_as_png_data_url(self):
        закладки = bookmarks.read(force=True)
        картинка = закладки[0].get("image", "")
        self.assertTrue(картинка.startswith("data:image/png;base64,"), картинка)
        # Без значка картинки нет — телефон нарисует букву.
        self.assertNotIn("image", закладки[1])

    def test_icon_cache_is_a_file_named_by_host(self):
        bookmarks.read(force=True)
        self.assertTrue((self.папка / "icons" / "github.com.png").is_file())

    def test_missing_profile_is_empty_and_says_why(self):
        patcher = mock.patch.object(
            bookmarks, "profiles_ini", return_value=self.папка / "нет.ini"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(
            bookmarks.read(force=True, on_error=self.ошибки.append), []
        )
        self.assertTrue(any("профиль" in text for text in self.ошибки), self.ошибки)

    def test_no_profile_never_raises(self):
        with mock.patch.object(bookmarks, "profiles_ini",
                               return_value=self.папка / "нет.ini"):
            self.assertEqual(bookmarks.read(force=True), [])

    def test_broken_database_is_empty_not_a_crash(self):
        (self.корень / "Profiles" / "abc.default-release" / "places.sqlite").write_bytes(
            b"this is not a database"
        )
        bookmarks.forget()
        self.assertEqual(
            bookmarks.read(force=True, on_error=self.ошибки.append), []
        )
        self.assertTrue(self.ошибки)

    def test_result_is_reused_within_a_minute(self):
        первый = bookmarks.read(force=True)
        self.assertEqual(bookmarks.read(), первый)
        # Список копируется: правка выданного не должна портить кеш.
        первый[0]["title"] = "правка"
        self.assertNotEqual(bookmarks.read()[0]["title"], "правка")

    def test_work_copy_does_not_touch_the_profile(self):
        профиль = self.корень / "Profiles" / "abc.default-release"
        было = (профиль / "places.sqlite").stat().st_mtime_ns
        bookmarks.read(force=True)
        self.assertEqual((профиль / "places.sqlite").stat().st_mtime_ns, было)
        self.assertFalse((профиль / "places.sqlite-wal").exists())


class SendAppsTests(unittest.TestCase):
    """Что уходит телефону: у кнопки с меню есть поле `menu` с ключами."""

    def setUp(self):
        self.приложения = [
            {"id": "discord", "title": "Discord", "icon": "discord", "kind": "app"},
            {"id": "firefox", "title": "Firefox", "icon": "browser", "kind": "app"},
            {"id": "youtube", "title": "YouTube", "icon": "play", "kind": "url"},
        ]
        for источник, кнопка in zip(APPS, self.приложения):
            кнопка["меню"] = launcher.build_menu(источник, MARKS)

    def test_keys_are_kind_and_id(self):
        self.assertEqual([п["key"] for п in self.приложения[0]["меню"]],
                         ["hotkey:mute", "hotkey:deafen", "site:gh"])

    def test_ready_item_has_what_the_phone_draws(self):
        пункт = self.приложения[0]["меню"][0]
        self.assertEqual(пункт["title"], "Микрофон")
        self.assertEqual(пункт["icon"], "mic")
        self.assertEqual(пункт["kind"], "hotkey")
        # Ключ и адрес на телефон не уходят: выполнять будет сервер.
        self.assertNotIn("keys", пункт)

    def test_bookmarks_come_after_manual_items(self):
        ключи = [п["key"] for п in self.приложения[1]["меню"]]
        self.assertEqual(ключи, ["bm:1", "bm:2"])

    def test_bookmark_keeps_its_own_image(self):
        с_картинкой = [п for п in self.приложения[1]["меню"] if "image" in п]
        self.assertEqual(len(с_картинкой), 1)
        self.assertTrue(с_картинкой[0]["image"].startswith("data:image/png"))

    def test_button_without_menu_has_no_menu_field(self):
        self.assertNotIn("menu", self.приложения[2])

    def test_firefox_without_bookmarks_flag_keeps_no_bookmarks(self):
        без = {"id": "ff", "title": "FF", "path": "ff.exe", "menu": []}
        self.assertEqual(launcher.build_menu(без, MARKS), [])

    def test_decorate_reads_marks_once_for_the_whole_list(self):
        with mock.patch.object(launcher, "read_list", return_value=APPS), \
                mock.patch.object(launcher, "load", return_value=self.приложения), \
                mock.patch.object(bookmarks, "read",
                                  return_value=MARKS) as читать:
            launcher.decorate_menus(self.приложения)
        читать.assert_called_once()

    def test_decorate_does_not_read_firefox_when_no_menu(self):
        простые = [{"id": "youtube", "title": "YouTube", "kind": "url"}]
        with mock.patch.object(launcher, "read_list", return_value=APPS[:1]), \
                mock.patch.object(bookmarks, "read",
                                  return_value=MARKS) as читать:
            launcher.decorate_menus(простые)
        читать.assert_not_called()

    def test_decorate_tolerates_a_broken_bookmark_reader(self):
        with mock.patch.object(launcher, "read_list", return_value=APPS), \
                mock.patch.object(bookmarks, "read", side_effect=OSError("диск")):
            # Firefox останется без закладок, но не уронит пульт.
            готово = launcher.decorate_menus([{"id": "firefox", "title": "Firefox"}])
        self.assertNotIn("menu", готово[0])

    def test_broken_menu_item_is_left_out_not_shown(self):
        кривой = {"id": "x", "menu": [
            {"kind": "hotkey", "id": "ok", "title": "Ок", "keys": "ctrl+m"},
            {"kind": "hotkey", "id": "плохо", "title": "Плохо", "keys": "ctrl+"},
            {"kind": "site", "id": "неurl", "title": "Не url", "url": "file:///x"},
        ]}
        self.assertEqual([п["key"] for п in launcher.build_menu(кривой)], ["hotkey:ok"])


class RunMenuTests(unittest.TestCase):
    """Выполнение по `{"type": "menu", "app", "item"}` с телефона.

    Ключ приходит извне, поэтому главное здесь — что выполнить можно только
    то, что лежит в apps.json, и что чужой ключ не выполняется.
    """

    def setUp(self):
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.нажато = []
        patcher = mock.patch.object(hotkeys, "press", side_effect=self.нажато.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.запущено = []
        patcher = mock.patch.object(
            launcher, "load", side_effect=self._готово
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _готово(self):
        for источник in APPS:
            yield {"id": источник["id"], "title": источник["title"],
                   "kind": источник["kind"], "path": источник.get("path", ""),
                   "args": [], "how": "direct"}

    def test_hotkey_presses_the_right_codes(self):
        ok, what = launcher.run_menu("discord", "hotkey:mute")
        self.assertTrue(ok)
        self.assertEqual(self.нажато, [[0x11, 0x10, 0x12, 0x4D]])
        self.assertIn("Микрофон", what)

    def test_second_hotkey_uses_its_own_combination(self):
        launcher.run_menu("discord", "hotkey:deafen")
        self.assertEqual(self.нажато, [[0x11, 0x10, 0x12, 0x44]])

    def test_site_opens_the_url_in_the_program(self):
        with mock.patch.object(launcher.subprocess, "Popen") as пуск:
            ok, what = launcher.run_menu("discord", "site:gh")
        self.assertTrue(ok)
        self.assertIn("https://github.com", пуск.call_args[0][0])
        self.assertIn("GitHub", what)

    def test_bookmark_opens_its_url(self):
        with mock.patch.object(launcher.subprocess, "Popen") as пуск:
            ok, what = launcher.run_menu("firefox", "bm:2", MARKS)
        self.assertTrue(ok)
        self.assertIn("https://github.com", пуск.call_args[0][0])
        self.assertIn("GitHub", what)

    def test_unknown_key_is_refused(self):
        ok, what = launcher.run_menu("discord", "hotkey:выключить-всё")
        self.assertFalse(ok)
        self.assertIn("нет такого пункта", what)
        self.assertEqual(self.нажато, [])

    def test_key_of_another_program_is_refused(self):
        # Ключ из чужой кнопки выполняться не должен, даже если настоящий.
        with mock.patch.object(launcher.subprocess, "Popen") as пуск:
            ok, _ = launcher.run_menu("youtube", "site:gh", MARKS)
        self.assertFalse(ok)
        пуск.assert_not_called()

    def test_unknown_program_is_refused(self):
        ok, what = launcher.run_menu("calc", "hotkey:mute")
        self.assertFalse(ok)
        self.assertIn("нет такого пункта", what)
        self.assertEqual(self.нажато, [])

    def test_non_http_url_never_reaches_the_program(self):
        # Такой пункт не попадает в меню вовсе, и выполнить его нельзя:
        # адрес проверяется дважды — при чтении apps.json и при самом запуске.
        подделка = [{"id": "discord", "title": "Discord", "kind": "app",
                     "menu": [{"kind": "site", "id": "x", "title": "Файл",
                               "url": "file:///C:/Windows/System32/cmd.exe"}]}]
        with mock.patch.object(launcher, "read_list", return_value=подделка), \
                mock.patch.object(launcher.subprocess, "Popen") as пуск:
            ok, what = launcher.run_menu("discord", "site:x")
        self.assertFalse(ok)
        self.assertIn("нет такого пункта", what)
        пуск.assert_not_called()

    def test_open_url_refuses_a_non_http_address_itself(self):
        # Вторая линия: даже если бы пункт как-то просочился, сам запуск
        # не откроет ни файловый, ни командный адрес.
        with mock.patch.object(launcher.subprocess, "Popen") as пуск, \
                mock.patch.object(launcher, "os") as разблокировано:
            ok, what = launcher.open_url("discord", "file:///C:/Windows/cmd.exe")
        self.assertFalse(ok)
        self.assertIn("не открываю", what)
        пуск.assert_not_called()
        разблокировано.startfile.assert_not_called()

    def test_launch_item_is_a_plain_start(self):
        with mock.patch.object(launcher, "launch",
                               return_value=(True, "Discord")) as старт:
            ok, what = launcher.run_menu("discord", "launch")
        старт.assert_called_once_with("discord")
        self.assertTrue(ok)
        self.assertEqual(what, "Discord")

    def test_press_failure_is_reported_not_raised(self):
        with mock.patch.object(hotkeys, "press", side_effect=OSError("запрещено")):
            ok, what = launcher.run_menu("discord", "hotkey:mute")
        self.assertFalse(ok)
        self.assertIn("запрещено", what)


class SaveTests(unittest.TestCase):
    """Сохранение списка из пульта: хорошее сохраняется, плохое — с ошибкой.

    Живой apps.json не трогаем: путь подменяется на временный файл, иначе
    прогон тестов стёр бы хозяину список кнопок.
    """

    def setUp(self):
        import collections
        import threading
        from types import SimpleNamespace as NS

        from ui.web_runtime import WebRuntime

        self.папка = Path(tempfile.mkdtemp(prefix="truba-apps-test-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.файл = self.папка / "apps.json"
        patcher = mock.patch.object(launcher, "APPS_FILE", self.файл)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Настоящий WebRuntime тянет за собой сервер и голос: здесь нужны
        # только сохранение и разбор списка, поэтому берём объект без
        # конструктора и подменяем то, что он дёргает наружу.
        self.среда = object.__new__(WebRuntime)
        self.среда.voice = NS(_apps=None)
        self.среда._lock = threading.Lock()
        self.среда._bg = lambda func, *args: None
        self.среда._send_apps = lambda: None
        self.среда.события = []
        self.среда._ids = iter(range(1, 100))
        self.среда._overview = {}
        self.среда._events = collections.deque(maxlen=10)
        self.среда._remember = lambda kind, payload: self.среда.события.append(
            (kind, payload))

    def _кнопка(self, **поле):
        основа = {"id": "discord", "title": "Discord", "kind": "app",
                  "path": "Discord.exe"}
        основа.update(поле)
        return основа

    def test_good_menu_is_saved(self):
        res = self.среда.apps_save([self._кнопка(menu=[
            {"kind": "hotkey", "id": "mute", "title": "Микрофон",
             "icon": "mic", "keys": "ctrl+shift+alt+m"},
            {"kind": "site", "id": "gh", "title": "GitHub",
             "url": "https://github.com"},
        ])])
        self.assertTrue(res["ok"], res)
        сохранено = launcher.read_list()
        self.assertEqual(сохранено[0]["menu"][0]["keys"], "ctrl+shift+alt+m")
        self.assertEqual(сохранено[0]["menu"][1]["url"], "https://github.com")

    def test_bookmarks_flag_is_saved(self):
        res = self.среда.apps_save([
            self._кнопка(id="firefox", path="firefox.exe", bookmarks="firefox")])
        self.assertTrue(res["ok"], res)
        self.assertEqual(launcher.read_list()[0]["bookmarks"], "firefox")

    def test_empty_bookmarks_flag_is_not_written(self):
        res = self.среда.apps_save([self._кнопка(bookmarks="")])
        self.assertTrue(res["ok"], res)
        self.assertNotIn("bookmarks", launcher.read_list()[0])

    def test_bad_combination_is_refused_with_a_readable_reason(self):
        res = self.среда.apps_save([self._кнопка(menu=[
            {"kind": "hotkey", "id": "mute", "title": "Микрофон", "keys": "ctrl+"},
        ])])
        self.assertFalse(res["ok"])
        self.assertIn("меню 1", res["error"])
        # Файл не тронут: плохое не должно затирать хорошее.
        self.assertFalse(self.файл.exists())

    def test_non_http_site_is_refused(self):
        res = self.среда.apps_save([self._кнопка(menu=[
            {"kind": "site", "id": "x", "title": "Файл",
             "url": "file:///C:/Windows/System32/cmd.exe"},
        ])])
        self.assertFalse(res["ok"])
        self.assertIn("http", res["error"])

    def test_repeated_item_id_is_refused(self):
        res = self.среда.apps_save([self._кнопка(menu=[
            {"kind": "hotkey", "id": "m", "title": "А", "keys": "ctrl+a"},
            {"kind": "hotkey", "id": "m", "title": "Б", "keys": "ctrl+b"},
        ])])
        self.assertFalse(res["ok"])
        self.assertIn("повтор", res["error"])

    def test_item_without_title_is_refused(self):
        res = self.среда.apps_save([self._кнопка(menu=[
            {"kind": "hotkey", "id": "m", "title": "  ", "keys": "ctrl+a"},
        ])])
        self.assertFalse(res["ok"])
        self.assertIn("название", res["error"])

    def test_bad_bookmarks_value_is_refused(self):
        res = self.среда.apps_save([self._кнопка(bookmarks="chrome")])
        self.assertFalse(res["ok"])
        self.assertIn("bookmarks", res["error"])

    def test_menu_survives_a_round_trip_into_the_phone(self):
        self.среда.apps_save([self._кнопка(menu=[
            {"kind": "hotkey", "id": "mute", "title": "Микрофон",
             "icon": "mic", "keys": "ctrl+shift+alt+m"},
        ])])
        отправлено = launcher.build_menu(launcher.read_list()[0])
        self.assertEqual(отправлено[0]["key"], "hotkey:mute")
        # Всё, что телефон увидел, должно выполниться.
        with mock.patch.object(hotkeys, "press") as жим:
            ok, _ = launcher.run_menu("discord", отправлено[0]["key"])
        жим.assert_called_once_with([0x11, 0x10, 0x12, 0x4D])
        self.assertTrue(ok)


class PhoneEventTests(unittest.TestCase):
    """Сообщение телефона `menu` доходит до выполнения и попадает в журнал."""

    def setUp(self):
        import collections
        import threading
        from types import SimpleNamespace as NS

        from ui.web_runtime import WebRuntime

        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(hotkeys, "press")
        self.нажатие = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(bookmarks, "read", return_value=MARKS)
        patcher.start()
        self.addCleanup(patcher.stop)
        # load() обходит Program Files в поисках программы — на это уходят
        # секунды. Здесь важен только ключ, который уйдёт в Popen.
        patcher = mock.patch.object(launcher, "load", return_value=[
            {"id": "discord", "title": "Discord", "kind": "app",
             "path": "Discord.exe", "args": [], "how": "direct"},
            {"id": "firefox", "title": "Firefox", "kind": "app",
             "path": "firefox.exe", "args": [], "how": "direct"},
            {"id": "youtube", "title": "YouTube", "kind": "url",
             "url": "https://youtube.com"},
        ])
        patcher.start()
        self.addCleanup(patcher.stop)

        # Потоки здесь не нужны: _bg выполняет функцию сразу, иначе тест
        # проверял бы не выполнение, а планировщик.
        self.среда = object.__new__(WebRuntime)
        self.среда._lock = threading.Lock()
        self.среда._bg = lambda func, *args: func(*args)
        self.среда._ids = iter(range(1, 100))
        self.среда._overview = {}
        self.среда._events = collections.deque(maxlen=20)
        self.среда.события = []
        self.среда._remember = lambda kind, payload: self.среда.события.append(
            (kind, payload))
        self.тосты = []
        self.звуки = []
        self.среда.server = NS(
            send_toast=lambda text, ok=True: self.тосты.append((text, ok)),
            send_sound=lambda name: self.звуки.append(name),
        )

    def test_hotkey_item_is_pressed_and_toasted(self):
        self.среда.handle_event("menu", {"app": "discord", "item": "hotkey:mute"})
        self.нажатие.assert_called_once_with([0x11, 0x10, 0x12, 0x4D])
        self.assertEqual(self.тосты[0][1], True)
        self.assertIn("open", self.звуки)

    def test_journal_line_names_the_program_and_the_item(self):
        self.среда.handle_event("menu", {"app": "discord", "item": "hotkey:mute"})
        вид, текст = self.среда.события[-1]
        self.assertEqual(вид, "menu_done")
        self.assertIn("Discord", текст["line"])
        self.assertIn("Микрофон", текст["line"])

    def test_journal_line_is_readable_in_the_log(self):
        from ui.web_runtime import WebRuntime

        строки = WebRuntime._log_messages("menu_done", {"line": "кнопка телефона: Discord → Микрофон"})
        self.assertEqual(строки, ["кнопка телефона: Discord → Микрофон"])

    def test_failed_item_is_reported_and_marked(self):
        self.среда.handle_event("menu", {"app": "discord", "item": "hotkey:нет"})
        вид, текст = self.среда.события[-1]
        self.assertEqual(вид, "menu_failed")
        self.assertIn("не вышло", текст["line"])
        self.assertEqual(self.тосты[0][1], False)
        self.assertIn("fail", self.звуки)
        self.нажатие.assert_not_called()

    def test_site_item_does_not_press_anything(self):
        with mock.patch.object(launcher.subprocess, "Popen") as пуск:
            self.среда.handle_event("menu", {"app": "discord", "item": "site:gh"})
        self.нажатие.assert_not_called()
        self.assertIn("https://github.com", пуск.call_args[0][0])

    def test_launch_item_starts_the_program(self):
        with mock.patch.object(launcher, "launch",
                               return_value=(True, "Discord")) as старт:
            self.среда.handle_event("menu", {"app": "discord", "item": "launch"})
        старт.assert_called_once_with("discord")
        # Стрелки тут лишние: программа и результат запуска названы одинаково.
        вид, текст = self.среда.события[-1]
        self.assertEqual(текст["line"], "кнопка телефона: Discord")

    def test_broken_payload_does_not_raise(self):
        for payload in (None, {}, {"app": None, "item": 5}, {"app": "discord"}):
            self.среда.handle_event("menu", payload)
        self.нажатие.assert_not_called()
