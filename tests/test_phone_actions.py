"""Нижние кнопки телефона: настройка `phone_actions` и её выполнение.

Хозяин 28.09 захотел менять их на свои: смена сцены в OBS, «заглушить себя»
в Discord, горячая клавиша. Проверяем то, что легко разъехаться:

  - валидатор мягкий: кривая ячейка становится ячейкой по умолчанию **на своём
    месте**, а не отказом (иначе одна опечатка убрала бы все четыре кнопки);
  - сервер берёт ячейку по номеру из **своих** настроек: с телефона приходит
    только номер, и мусор в нём не значит «выполнить что попало»;
  - ячейка `menu` зовёт тот же `launcher.run_menu`, что и пункт подменю.

Ни нажатий клавиш, ни сети, ни записанных настроек хозяина тут нет:
`settings.SETTINGS_PATH` подменён временной папкой, а `hotkeys.press` подменён
— иначе тест жал бы клавиши хозяину на живом столе.
"""

import collections
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from core import hands, hotkeys, launcher, screen, settings
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime, action_items

APPS = [
    {
        "id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe",
        "menu": [
            {"kind": "hotkey", "id": "mute", "title": "Микрофон", "icon": "mic",
             "toggle": True, "keys": "ctrl+shift+alt+m"},
        ],
    },
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe"},
]


def _server() -> PhoneServer:
    """`PhoneServer` без сокета и потока: поднимать сервер тут незачем."""
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None
    return server


class Валидатор(unittest.TestCase):
    """`validate_phone_actions`: всегда четыре ячейки, проверка мягкая."""

    def test_defaults_are_the_four_former_buttons(self):
        self.assertEqual(settings.DEFAULTS["phone_actions"], [
            {"kind": "builtin", "id": "screenshot"},
            {"kind": "builtin", "id": "moment"},
            {"kind": "builtin", "id": "search"},
            {"kind": "builtin", "id": "note_start"},
        ])

    def test_good_cells_are_taken_as_they_are(self):
        ячейки = [
            {"kind": "hotkey", "title": "Сцена", "icon": "replay", "keys": "ctrl+alt+o"},
            {"kind": "app", "app": "discord"},
            {"kind": "menu", "app": "discord", "item": "hotkey:mute"},
            {"kind": "none"},
        ]
        self.assertEqual(settings.validate_phone_actions(ячейки), ячейки)

    def test_long_title_is_cut_to_what_the_phone_can_read(self):
        got = settings.validate_phone_actions(
            [{"kind": "hotkey", "title": "О" * 40, "keys": "ctrl+m"}])
        self.assertEqual(len(got[0]["title"]), 24)

    def test_unknown_icon_becomes_a_keyboard(self):
        # Незнакомый значок — не причина убрать кнопку: телефон нарисует
        # клавиатуру, и хозяин увидит, что кнопка есть.
        got = settings.validate_phone_actions(
            [{"kind": "hotkey", "title": "Сцена", "icon": "нетакое", "keys": "ctrl+m"}])
        self.assertEqual(got[0]["icon"], "keyboard")

    def test_broken_cell_becomes_the_default_of_the_same_place(self):
        # Кривая ячейка не должна убирать кнопку и не должна сдвигать
        # остальные: у каждого места своя кнопка по умолчанию.
        got = settings.validate_phone_actions([
            {"kind": "выдумка"}, None, {"kind": "hotkey", "title": "", "keys": "ctrl+m"},
        ])
        self.assertEqual(got, settings.DEFAULTS["phone_actions"])

    def test_unparsable_keys_become_the_default_cell(self):
        # Нажать нечем — такую ячейку сервер заменил бы на кнопку по умолчанию.
        for keys in ("", "ctrl+", "ctrl+shift", "ctrl+колесо", "мнемоника"):
            got = settings.validate_phone_actions(
                [{"kind": "hotkey", "title": "Сцена", "keys": keys}])
            self.assertEqual(got[0], {"kind": "builtin", "id": "screenshot"}, keys)

    def test_menu_without_item_becomes_the_default_cell(self):
        got = settings.validate_phone_actions([
            {"kind": "menu", "app": "discord"}, {"kind": "app"},
        ])
        self.assertEqual(got, settings.DEFAULTS["phone_actions"])

    def test_whole_thing_of_garbage_gives_four_defaults(self):
        for мусор in (None, "сколько", 5, {}, [1, 2, 3]):
            got = settings.validate_phone_actions(мусор)
            self.assertEqual(got, settings.DEFAULTS["phone_actions"], мусор)
            self.assertEqual(len(got), 4, мусор)

    def test_never_longer_than_four_and_never_shorter(self):
        for длина in (0, 1, 3, 4, 9):
            got = settings.validate_phone_actions(
                [{"kind": "none"} for _ in range(длина)])
            self.assertEqual(len(got), 4, длина)

    def test_unknown_program_is_kept_its_own_fault(self):
        # apps.json хозяин правит руками в любой момент. Кнопку терять из-за
        # этого нельзя: нажатие само скажет «такой программы больше нет».
        got = settings.validate_phone_actions([
            {"kind": "app", "app": "obs-нет"},
            {"kind": "menu", "app": "obs-нет", "item": "hotkey:x"},
        ])
        self.assertEqual(got[0], {"kind": "app", "app": "obs-нет"})
        self.assertEqual(got[1],
                         {"kind": "menu", "app": "obs-нет", "item": "hotkey:x"})


class СообщениеТелефону(unittest.TestCase):
    """`send_actions` несёт кнопки, а `none` в список не попадает."""

    def setUp(self):
        self.server = _server()

    def _сообщение(self, items):
        class _Телефон:
            текст = ""

            def send_text(self, payload):
                self.текст = payload

        телефон = _Телефон()
        with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
            self.server.send_actions(items)
        шлёт.call_args[0][0](телефон)
        return json.loads(телефон.текст)

    def test_builtin_buttons_carry_their_title_and_icon(self):
        msg = self._сообщение(action_items([
            {"kind": "builtin", "id": "screenshot"},
            {"kind": "builtin", "id": "note_start"},
        ]))
        self.assertEqual(msg["type"], "actions")
        self.assertEqual([(i["slot"], i["title"]) for i in msg["items"]],
                         [(0, "экран"), (1, "заметка")])

    def test_none_cell_is_skipped_but_the_slot_number_survives(self):
        # `slot` — это место, а не номер в списке: без него нажатие третьей
        # кнопки после сдвига означало бы вторую.
        msg = self._сообщение(action_items([
            {"kind": "none"}, {"kind": "builtin", "id": "moment"},
            {"kind": "builtin", "id": "search"},
        ]))
        self.assertEqual([i["slot"] for i in msg["items"]], [1, 2])

    def test_keys_and_program_names_do_not_leave_the_computer(self):
        # Телефон знает только номер кнопки: сочетание клавиш и id программы
        # выполнять он всё равно не будет. Имя значка из набора телефона
        # (`discord`) — другое дело, такие же, как у программ в сетке.
        # Список программ — свой: у свежей установки в apps.json Discord нет
        # (29.09 тест падал на установке из ZIP, у автора проходил).
        with mock.patch.object(launcher, "read_list", return_value=APPS):
            msg = self._сообщение(action_items([
                {"kind": "hotkey", "title": "Сцена", "icon": "replay", "keys": "ctrl+alt+o"},
                {"kind": "app", "app": "discord"},
            ]))
        self.assertNotIn("keys", json.dumps(msg, ensure_ascii=False))
        self.assertNotIn("ctrl+alt+o", json.dumps(msg, ensure_ascii=False))
        # Программа названа по-человечески, а её внутренним именем — никак.
        self.assertEqual(msg["items"][1]["title"], "Discord")
        self.assertNotIn("id", msg["items"][1])

    def test_menu_cell_carries_the_toggle_state(self):
        with mock.patch.object(launcher, "read_list", return_value=APPS):
            msg = self._сообщение(action_items([
                {"kind": "menu", "app": "discord", "item": "hotkey:mute"},
            ]))
        self.assertEqual(msg["items"][0]["title"], "Микрофон")
        self.assertTrue(msg["items"][0]["toggle"])
        self.assertIn("on", msg["items"][0])
        # По программе телефон узнаёт свой `menu_state` и шлёт `menu_flip`:
        # без неё подсветка внизу застывала бы.
        self.assertEqual(msg["items"][0]["app"], "discord")

    def test_menu_cell_of_a_gone_item_does_not_break_the_message(self):
        with mock.patch.object(launcher, "read_list", return_value=[]):
            msg = self._сообщение(action_items([
                {"kind": "menu", "app": "нет", "item": "hotkey:нет"},
            ]))
        self.assertEqual(msg["items"][0]["title"], "hotkey:нет")


class СнимокНаТелефоне(unittest.TestCase):
    """Сообщение `shot` несёт, сколько секунд лежать, и сервер кладёт столько.

    28.09, 23:22: снимок показался поверх всего и висел, пока хозяин не коснётся
    («скрин не скрывается сам»). Теперь секунды шлёт сервер, страница по ним
    ставит таймер.
    """

    def setUp(self):
        self.server = _server()
        self.path = mock.Mock()
        self.path.__str__ = lambda _self="": "shot.jpg"
        self.path.name = "shot.jpg"
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def _снимок(self, **kwargs):
        class _Телефон:
            текст = ""

            def send_text(self, payload):
                self.текст = payload

        телефон = _Телефон()
        with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
            self.server.send_shot("КАРТИНКА", "2026-09-28_23-22-07.png", **kwargs)
        шлёт.call_args[0][0](телефон)
        return json.loads(телефон.текст)

    def _секунды_от_shoot(self, **kwargs):
        """Сколько секунд `hands.shoot` положил в сообщение телефону."""
        class _Телефончик:
            def __init__(self):
                self.секунды = None

            def send_sound(self, name):
                pass

            def send_shot(self, image, caption, hide=15.0):
                self.секунды = hide

        телефон = _Телефончик()
        with mock.patch.object(screen, "take", return_value=(self.path, "КАРТИНКА")):
            hands.shoot(lambda kind, payload: None, lambda: телефон, **kwargs)
        return телефон.секунды

    def test_the_message_carries_the_seconds(self):
        msg = self._снимок(hide=6.0)
        self.assertEqual(msg["type"], "shot")
        self.assertEqual(msg["image"], "КАРТИНКА")
        self.assertEqual(msg["caption"], "2026-09-28_23-22-07.png")
        self.assertEqual(msg["hide"], 6.0)

    def test_without_seconds_the_phone_gets_the_default(self):
        self.assertEqual(self._снимок()["hide"], 15.0)

    def test_shoot_passes_the_seconds_it_was_given(self):
        # Взгляд на экран кладёт кадр ненадолго: он нужен ей, а не хозяину.
        self.assertEqual(self._секунды_от_shoot(hide=6.0), 6.0)
        self.assertEqual(hands.LOOK_HIDE, 6.0)

    def test_shoot_without_a_number_lays_the_default_fifteen(self):
        self.assertEqual(self._секунды_от_shoot(), hands.SHOT_HIDE)


def _среда(кнопки=None):
    """Пульт без голоса: нужны нижние кнопки, тосты и журнал."""
    runtime = object.__new__(WebRuntime)
    runtime._lock = threading.Lock()
    # Потоки не нужны: _bg выполняет сразу, иначе тест проверял бы
    # планировщик, а не выполнение.
    runtime._bg = lambda func, *args: func(*args)
    runtime._ids = iter(range(1, 100))
    runtime._overview = {}
    runtime._events = collections.deque(maxlen=20)
    runtime.события = []
    runtime._remember = lambda kind, payload: runtime.события.append((kind, payload))
    runtime.тосты = []
    runtime.звуки = []
    runtime.server = NS(
        send_toast=lambda text, ok=True: runtime.тосты.append((text, ok)),
        send_sound=lambda name: runtime.звуки.append(name),
    )
    runtime.кнопки = mock.patch.object(
        settings, "phone_actions",
        return_value=settings.validate_phone_actions(кнопки))
    runtime.кнопки.start()
    return runtime


class НажатиеКнопки(unittest.TestCase):
    """`action` по номеру: ячейку берёт сервер, а не телефон."""

    def setUp(self):
        self.ячейки = [
            {"kind": "hotkey", "title": "Сцена OBS", "icon": "replay", "keys": "ctrl+alt+o"},
            {"kind": "app", "app": "discord"},
            {"kind": "menu", "app": "discord", "item": "hotkey:mute"},
            {"kind": "none"},
        ]
        self.среда = _среда(self.ячейки)
        self.addCleanup(self.среда.кнопки.stop)
        for имя, подмена in (
            ("нажатие", mock.patch.object(hotkeys, "press")),
            ("список", mock.patch.object(launcher, "read_list", return_value=APPS)),
            ("запуск", mock.patch.object(launcher, "launch", return_value=(True, "Discord"))),
            ("меню", mock.patch.object(launcher, "run_menu", return_value=(True, "Микрофон"))),
        ):
            setattr(self, имя, подмена.start())
            self.addCleanup(подмена.stop)

    def test_hotkey_cell_presses_its_own_keys(self):
        self.среда.handle_event("action", {"slot": 0})
        self.нажатие.assert_called_once_with(hotkeys.parse("ctrl+alt+o"))
        self.assertEqual(self.среда.тосты[0], ("Сцена OBS", True))
        self.assertIn("open", self.среда.звуки)
        вид, текст = self.среда.события[-1]
        self.assertEqual(вид, "action_done")
        self.assertIn("Сцена OBS", текст["line"])

    def test_app_cell_launches_the_program(self):
        self.среда.handle_event("action", {"slot": 1})
        self.запуск.assert_called_once_with("discord")
        self.нажатие.assert_not_called()

    def test_menu_cell_runs_the_menu_item(self):
        # Тем же `run_menu`, что и у пункта подменю: поэтому подсветка
        # переключателя работает как там.
        self.среда.handle_event("action", {"slot": 2})
        self.меню.assert_called_once_with("discord", "hotkey:mute", mock.ANY)

    def test_empty_cell_does_nothing(self):
        self.среда.handle_event("action", {"slot": 3})
        self.нажатие.assert_not_called()
        self.запуск.assert_not_called()
        self.меню.assert_not_called()

    def test_garbage_from_the_phone_executes_nothing(self):
        # С телефона приходит только номер: мусор в нём — это «ничего», а не
        # «выполнить что-нибудь».
        for payload in (None, {}, {"slot": None}, {"slot": "мусор"},
                        {"slot": -1}, {"slot": 99}, {"slot": [1]}, {"slot": 1.5}):
            self.среда.handle_event("action", payload)
        self.нажатие.assert_not_called()
        self.запуск.assert_not_called()
        self.меню.assert_not_called()

    def test_phone_cannot_name_what_to_run(self):
        # Попытка выполнить чужое: телефон шлёт `app`/`keys`, а сервер берёт
        # ячейку из своих настроек. Чужое имя в сообщении игнорится.
        self.среда.handle_event("action", {"slot": 0, "app": "firefox", "keys": "ctrl+q"})
        self.нажатие.assert_called_once_with(hotkeys.parse("ctrl+alt+o"))

    def test_gone_program_is_reported_not_crashed(self):
        # Записи в apps.json может не быть — хозяин правит файл руками.
        self.addCleanup(self.среда.кнопки.stop)
        self.среда = _среда([{"kind": "app", "app": "obs-нет"}])
        self.addCleanup(self.среда.кнопки.stop)
        self.среда.handle_event("action", {"slot": 0})
        self.запуск.assert_not_called()
        self.assertEqual(self.среда.тосты[-1], ("такой программы больше нет", False))
        self.assertIn("fail", self.среда.звуки)

    def test_keys_refused_by_the_system_are_reported(self):
        self.addCleanup(self.среда.кнопки.stop)
        self.среда = _среда([{"kind": "hotkey", "title": "Сцена", "icon": "replay",
                              "keys": "ctrl+alt+o"}])
        self.addCleanup(self.среда.кнопки.stop)
        self.нажатие.side_effect = OSError("система отклонила")
        self.среда.handle_event("action", {"slot": 0})
        вид, текст = self.среда.события[-1]
        self.assertEqual(вид, "action_failed")
        self.assertIn("отклонила", текст["line"])
        self.assertEqual(self.среда.тосты[-1][1], False)


class Сохранение(unittest.TestCase):
    """Пульт шлёт `phone_actions` в `/api/settings`, и список едет телефону."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-actions-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        self.среда = _среда()
        self.addCleanup(self.среда.кнопки.stop)
        self.разослано = []
        self.среда._send_actions = lambda: self.разослано.append(True)

    def _сохранено(self):
        return json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))

    def test_cells_are_saved_and_sent_to_the_phone(self):
        ответ = self.среда.save_settings({"phone_actions": [
            {"kind": "none"}, {"kind": "app", "app": "discord"},
            {"kind": "hotkey", "title": "Сцена", "icon": "replay", "keys": "ctrl+alt+o"},
            {"kind": "builtin", "id": "moment"},
        ]})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self._сохранено()["phone_actions"][0], {"kind": "none"})
        # Телефон рисует нижние кнопки по сообщению — после смены оно ушло.
        self.assertTrue(self.разослано)

    def test_another_setting_does_not_resend_the_buttons(self):
        self.среда.save_settings({"temperature": 0.5})
        self.assertFalse(self.разослано)


class СтраницаТелефона(unittest.TestCase):
    """`web/index.html` текстом: нижние кнопки и карточки закладок."""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "web" / "index.html")\
            .read_text(encoding="utf-8")
        cls.js = cls.html.split("<script>")[1].split("</script>")[0]

    def _функция(self, имя):
        """Тело функции до следующей того же уровня.

        Разрез по пустой строке годится не всегда: тело может быть длинным,
        а проверить надо всё (coordination/ГРАБЛИ.md — проверять поведение,
        а не огрызок).
        """
        начало = self.js.find("function " + имя + "(")
        self.assertGreaterEqual(начало, 0, имя)
        остаток = self.js[начало:]
        следующая = re.search(r"^\n(?:function|const|let) \w+", остаток[1:], re.M)
        return остаток[:следующая.start() + 1] if следующая else остаток

    def test_the_buttons_are_built_from_the_server_list(self):
        # `ACTIONS` остался запасным: сервер может не прислать сообщение.
        self.assertIn("drawActions(null);", self.js)
        self.assertIn("msg.type === 'actions'", self.js)
        self.assertIn("actionItems || ACTIONS", self.js)

    def test_a_press_sends_only_the_number_of_the_button(self):
        # Ни клавиш, ни имён программ телефон знать не должен и не умеет:
        # что делать кнопка — решает комп по своим настройкам.
        блок = self._функция("drawActions")
        self.assertIn("send({ type: 'action', slot: slot })", блок)
        self.assertIn("Number.isFinite(Number(action.slot))", блок)

    def test_builtin_buttons_keep_their_old_behaviour(self):
        # Заметка открывает оверлей, «найти» слушает: это живёт на телефоне.
        блок = self._функция("drawActions")
        self.assertIn("action.id === 'note_start'", блок)
        self.assertIn("openNotes()", блок)
        self.assertIn("action.id === 'search'", блок)
        self.assertIn("listenNow('скажи, что найти')", блок)
        self.assertIn("send({ type: action.id })", блок)

    def test_the_search_button_says_what_it_is_waiting_for(self):
        # «слушает тебя» после «найти» ничего не объясняет: от человека ждут
        # запрос, а не просто слова. Подпись — своя, круг остался прежним.
        блок = self._функция("drawActions")
        self.assertIn("listenNow('скажи, что найти')", блок)
        подпись = self._функция("listenNow")
        self.assertIn("'слушает тебя'", подпись)
        self.assertIn("label.textContent = text", подпись)
        # Круг в центре по-прежнему зовёт её без подписи.
        self.assertIn("listenNow();", self.js)

    def test_a_toggle_button_lights_up_and_can_be_corrected_by_a_hold(self):
        блок = self._функция("drawActions")
        self.assertIn("el.classList.add('flip')", блок)
        self.assertIn("send({ type: 'menu_flip'", блок)
        self.assertIn("HOLD_MS", блок)
        # И серверное `menu_state` догоняет и её, а не только плитки подменю.
        self.assertIn(".act.flip", self._функция("markMenuState"))

    def test_in_edit_mode_a_press_picks_the_button_instead_of_running_it(self):
        блок = self._функция("drawActions")
        self.assertIn("if (EDIT) { editSay({ type: 'truba-edit-action', slot: slot }); return; }",
                      блок)
        # Подсветка выбранной кнопки — как у выбранного значка.
        self.assertIn("el.classList.toggle('picked'",
                      self._функция("editPickAction"))
        self.assertIn(".act.picked", self.html)

    def test_a_bookmark_is_a_card_two_tiles_wide_with_a_name(self):
        # 28.09: «закладки браузера выглядят не очень — плитки эти».
        self.assertIn("grid-column: span 2;", self.html)
        карточка = self.html.split("#menu .app.card {")[1].split("}")[0]
        self.assertIn("width: calc(var(--tile, 76px) * 2 + 10px)", карточка)
        self.assertIn("height: var(--tile, 76px)", карточка)
        # Название читается, а не угадывается по логотипу.
        self.assertIn("-webkit-line-clamp: 2", self.html)
        self.assertIn("font-size: 16px", self.html)
        блок = self._функция("menuCard")
        self.assertIn("card-name", блок)
        self.assertIn("card-cap", блок)

    def test_the_site_picture_is_not_stretched(self):
        # Фавиконка 16–32 px, растянутая на 44 px, — мыло (28.09).
        правило = self.html.split("#menu .app.card .card-face img {")[1].split("}")[0]
        self.assertIn("max-width: 32px", правило)
        self.assertIn("object-fit: contain", правило)

    def test_a_site_without_a_picture_is_just_its_name(self):
        # 02.10 хозяин о букве на цвете домена: «некрасиво… может просто
        # названия оставить без превью лого?» — карточка без логотипа теперь
        # одно название, буквенного значка больше нет вовсе.
        карточка = self._функция("menuCard")
        self.assertIn("el.classList.add('без-лого')", карточка)
        self.assertNotIn("letter", карточка)
        self.assertNotIn("siteColor", self.js)
        self.assertIn("#menu .app.card.без-лого", self.html)

    def test_hotkey_items_stay_square_tiles(self):
        блок = self._функция("menuItem")
        self.assertIn("item.kind === 'bookmark' || item.kind === 'site'", блок)
        # Пункт-сочетание остаётся плиткой: у него нет названия сайта.
        self.assertIn("el.className = 'app';", блок)

    def test_the_menu_layout_knows_that_a_card_takes_two_columns(self):
        self.assertIn("function menuRows(spans, cols)", self.js)
        self.assertIn("function menuSpans(items)", self.js)
        # Плотная укладка: иначе справа от плитки программы зияла бы дыра.
        self.assertIn("grid-auto-flow: row dense;", self.html)
        # Плитка программы занимает два столбца и два ряда.
        плитка = self.html.split("#menu .app.main {")[1].split("}")[0]
        self.assertIn("grid-row: span 2;", плитка)

    def test_the_shot_hides_itself_after_the_seconds_the_server_sent(self):
        # 28.09: «скрин не скрывается сам» — он висел, пока хозяин не коснётся.
        блок = self._функция("showShot")
        self.assertIn("function showShot(image, caption, hide)", self.js)
        self.assertIn("setTimeout(", блок)
        self.assertIn("clearTimeout(shotTimer)", блок)
        # Секунд нет или мусор — 15: столько же, сколько hands.SHOT_HIDE.
        self.assertIn("Number.isFinite(seconds)", блок)
        self.assertIn("15000", блок)
        # По таймеру снимок убирается так же, как по касанию, и картинка
        # отпускается: кадр Full HD иначе висит в памяти телефона.
        self.assertIn("function hideShot()", self.js)
        self.assertIn("shotImg.src = '';", self._функция("hideShot"))
        self.assertIn("clearTimeout(shotTimer)",
                      self.js.split("shotBox.addEventListener")[1])
        # Секунды приходят с сообщением, а не с потолка.
        self.assertIn("showShot(msg.image, msg.caption, msg.hide)", self.js)
