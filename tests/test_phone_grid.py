"""Сетка программ на телефоне: вид, сообщение и режим правки.

Три вещи, которые легко разъехаться:
  - четыре настройки вида (`phone_*`) должны доехать до `config` и обратно в
    телефон, а кривое значение — молча стать значением по умолчанию;
  - `send_apps` кладёт `layout` в сообщение, а `apps_save` принимает цвет
    значка (`color`) и не пропускает мусор;
  - страница телефона: `?edit=1` не поднимает WebSocket (иначе комп заговорит
    в макет), слушает только родителя и умеет показать `round`/`bare`,
    подписи и плитку «+».

Пути к данным — во временную папку, как в остальном прогоне
(tests/test_data_guard.py): живой settings.json и apps.json хозяина тест
не трогает.
"""

import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import launcher, settings
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

PAGE = Path(config.ROOT) / "web" / "index.html"


def _server() -> PhoneServer:
    """`PhoneServer` без сокета и потока: поднимать сервер тут незачем."""
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None
    return server


def _runtime() -> WebRuntime:
    """Пульт без голоса: нужны только сохранение настроек и список."""
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


class НастройкиСеткиTests(unittest.TestCase):
    """Значения по умолчанию, проверки и запись в `config`."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-grid-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        # `apply_to_config` переносит в config и папку заметок — возвращаем.
        self.addCleanup(setattr, config, "NOTES_DIR", config.NOTES_DIR)
        # Старый settings.json в новом тесте трогает и температуру.
        self.addCleanup(setattr, config, "TEMPERATURE", config.TEMPERATURE)
        self._было = (config.PHONE_COLS, config.PHONE_ROWS,
                      config.PHONE_ICON_STYLE, config.PHONE_LABELS)
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        (config.PHONE_COLS, config.PHONE_ROWS,
         config.PHONE_ICON_STYLE, config.PHONE_LABELS) = self._было

    def _сохранено(self):
        return json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))

    def test_defaults_look_like_the_phone_did_before(self):
        # По умолчанию четыре колонки и два ряда, без подписей.
        self.assertEqual(settings.DEFAULTS["phone_cols"], 4)
        self.assertEqual(settings.DEFAULTS["phone_rows"], 2)
        self.assertEqual(settings.DEFAULTS["phone_icon_style"], "plate")
        self.assertFalse(settings.DEFAULTS["phone_labels"])

    def test_good_values_are_taken(self):
        # Колонок всегда четыре: что бы ни прислали — и «авто», и пять.
        for значение in ("auto", 3, 4, 5, 6, None, "сколько"):
            self.assertEqual(settings.validate_phone_cols(значение), 4, значение)
        for вид in ("plate", "round", "bare"):
            self.assertEqual(settings.validate_phone_icon_style(вид), вид)
        self.assertTrue(settings.validate_phone_labels(True))
        self.assertFalse(settings.validate_phone_labels(False))

    def test_one_and_two_rows_are_possible_now(self):
        # Один и два ряда — допустимые значения в числовой и строковой форме.
        self.assertEqual(settings.PHONE_ROWS_MIN, 1)
        self.assertEqual(settings.PHONE_ROWS_MAX, 2)
        for число in (1, 2, "1", "2", " 1 "):
            self.assertEqual(settings.validate_phone_rows(число),
                             int(str(число).strip()), число)
        # Целое дробное — обычная запись единицы, а полтора ряда не бывает.
        self.assertEqual(settings.validate_phone_rows(1.0), 1)
        self.assertEqual(settings.validate_phone_rows(2.0), 2)

    def test_three_rows_and_junk_become_two(self):
        # Выбора «3» больше нет: три ряда на телефоне не помещались. Старое
        # «3» из живого settings.json и любая опечатка молча становятся двумя
        # рядами — телефон не рисует сетку, которой в пульте не выбрать.
        for число in (3, "3", 0, 4, -1, 9, None, "", "два", [], {}, True, 1.5):
            self.assertEqual(settings.validate_phone_rows(число), 2, число)

    def test_bad_values_fall_back_to_the_default_without_raising(self):
        # Опечатка в настройке — не причина оставить телефон пустым и не
        # причина бросить исключение в пульте.
        for мусор in (None, "", "сколько", 0, 2, 7, 99, [], {}, True):
            self.assertEqual(settings.validate_phone_cols(мусор), config.PHONE_COLS,
                             мусор)
        for мусор in (None, "", "квадрат", 1, [], {}):
            self.assertEqual(settings.validate_phone_icon_style(мусор),
                             config.PHONE_ICON_STYLE, мусор)
        for мусор in (None, 0, 1, "да", [], {}):
            self.assertEqual(settings.validate_phone_labels(мусор),
                             bool(config.PHONE_LABELS), мусор)

    def test_saved_values_reach_config_and_the_phone_layout(self):
        # Один ряд сохраняется и попадает в раскладку телефона.
        settings.save_settings({"phone_cols": 4, "phone_rows": 1,
                                "phone_icon_style": "round", "phone_labels": True})
        settings.apply_to_config()
        self.assertEqual((config.PHONE_COLS, config.PHONE_ROWS,
                          config.PHONE_ICON_STYLE, config.PHONE_LABELS),
                         (4, 1, "round", True))
        self.assertEqual(settings.grid_layout(),
                         {"cols": 4, "rows": 1, "style": "round", "labels": True})

    def test_the_old_three_rows_setting_becomes_two(self):
        # Сохранённое значение «3» приводится к двум рядам. Загрузка и
        # grid_layout должны показать два ряда, а
        # остальные настройки — те же, что лежали в файле.
        settings.SETTINGS_PATH.write_text(json.dumps({
            "phone_cols": 4, "phone_rows": 3, "phone_icon_style": "round",
            "phone_labels": True, "temperature": 0.4,
            "phone_actions": [{"kind": "builtin", "id": "screenshot"},
                              {"kind": "builtin", "id": "moment"},
                              {"kind": "builtin", "id": "search"},
                              {"kind": "builtin", "id": "note_start"}]}),
            encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.PHONE_ROWS, 2)
        self.assertEqual(settings.grid_layout(),
                         {"cols": 4, "rows": 2, "style": "round", "labels": True})
        # Прочие настройки тройка не тронула — и список программ тоже.
        self.assertEqual(config.TEMPERATURE, 0.4)
        self.assertEqual(settings.phone_actions(),
                         [dict(ячейка) for ячейка in settings.DEFAULT_PHONE_ACTIONS])

    def test_saving_three_rows_writes_two(self):
        # Старая страница пульта или живой клиент может ещё прислать тройку:
        # в файл должно лечь два, а не то, что прислали.
        settings.save_settings({"phone_rows": 3})
        self.assertEqual(self._сохранено()["phone_rows"], 2)
        settings.save_settings({"phone_rows": 3})
        settings.apply_to_config()
        self.assertEqual(config.PHONE_ROWS, 2)

    def test_broken_settings_json_does_not_break_the_grid(self):
        settings.SETTINGS_PATH.write_text(json.dumps({
            "phone_cols": "много", "phone_rows": 9,
            "phone_icon_style": "квадрат", "phone_labels": "возможно"}),
            encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(settings.grid_layout(),
                         {"cols": 4, "rows": 2, "style": "plate",
                          "labels": False})

    def test_pult_saves_the_grid_and_sends_the_list_to_the_phone(self):
        runtime = _runtime()
        переслано = []
        runtime._send_apps = lambda: переслано.append(True)
        ответ = runtime.save_settings({"phone_cols": 5, "phone_rows": 1,
                                       "phone_icon_style": "bare",
                                       "phone_labels": True})
        self.assertTrue(ответ["ok"], ответ)
        # Пять колонок — мимо: колонок всегда четыре. Один ряд — законный
        # выбор, его сохраняем как есть, не заменяя на два.
        self.assertEqual(self._сохранено()["phone_cols"], 4)
        self.assertEqual(self._сохранено()["phone_rows"], 1)
        self.assertTrue(self._сохранено()["phone_labels"])
        # Вид сетки телефон берёт из сообщения со списком — список ушёл заново.
        self.assertTrue(переслано)

    def test_the_pult_cannot_save_a_third_row(self):
        # Старый пульт (или живой клиент) может прислать «3»: в файл ложится
        # два, и телефон получает два ряда, а не сетку, которой больше нет.
        # Файл настроек готовим сами: пока в нём лежит один ряд, «3» — это
        # изменение, и пульт правда пишет; пустой папки он не создаёт.
        settings.SETTINGS_PATH.write_text(json.dumps({"phone_rows": 1}),
                                         encoding="utf-8")
        runtime = _runtime()
        runtime._send_apps = lambda: None
        runtime.save_settings({"phone_rows": 3})
        self.assertEqual(self._сохранено()["phone_rows"], 2)
        settings.apply_to_config()
        self.assertEqual(settings.grid_layout()["rows"], 2)

    def test_another_setting_saved_alone_does_not_resend_the_list(self):
        runtime = _runtime()
        переслано = []
        runtime._send_apps = lambda: переслано.append(True)
        runtime.save_settings({"temperature": 0.5})
        self.assertFalse(переслано)


class СообщениеТелефонуTests(unittest.TestCase):
    """`send_apps` несёт вид сетки вместе со списком."""

    def setUp(self):
        self.server = _server()
        self._было = (config.PHONE_COLS, config.PHONE_ROWS,
                      config.PHONE_ICON_STYLE, config.PHONE_LABELS)
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        (config.PHONE_COLS, config.PHONE_ROWS,
         config.PHONE_ICON_STYLE, config.PHONE_LABELS) = self._было

    def _сообщение(self, *args):
        class _Телефон:
            def __init__(self):
                self.текст = ""

            def send_text(self, payload):
                self.текст = payload

        телефон = _Телефон()
        with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
            self.server.send_apps(*args)
        шлёт.call_args[0][0](телефон)
        return json.loads(телефон.текст)

    def test_layout_travels_with_the_list(self):
        config.PHONE_COLS, config.PHONE_ROWS = 3, 2
        config.PHONE_ICON_STYLE, config.PHONE_LABELS = "round", True
        msg = self._сообщение([{"id": "firefox"}])
        self.assertEqual(msg["type"], "apps")
        self.assertEqual(msg["items"], [{"id": "firefox"}])
        # Три колонки из старой настройки телефон рисует как четыре: колонок
        # всегда четыре, сколько бы ни лежало в живом settings.json.
        self.assertEqual(msg["layout"],
                         {"cols": 4, "rows": 2, "style": "round", "labels": True})

    def test_without_an_argument_the_layout_comes_from_config(self):
        # Один ряд из живого config — законный выбор, телефон получает его.
        config.PHONE_COLS, config.PHONE_ROWS = "auto", 1
        config.PHONE_ICON_STYLE, config.PHONE_LABELS = "plate", False
        msg = self._сообщение([])
        self.assertEqual(msg["layout"],
                         {"cols": 4, "rows": 1, "style": "plate",
                          "labels": False})

    def test_the_old_three_rows_from_config_become_two(self):
        # Старый config мог получить тройку из прежнего settings.json: телефон
        # всё равно должен получить два ряда.
        config.PHONE_ROWS = 3
        msg = self._сообщение([])
        self.assertEqual(msg["layout"]["rows"], 2)

    def test_explicit_layout_wins(self):
        config.PHONE_COLS = 6
        msg = self._сообщение([], {"cols": 2, "rows": 3, "style": "bare",
                                   "labels": True})
        self.assertEqual(msg["layout"]["cols"], 2)


class СохранениеСпискаTests(unittest.TestCase):
    """`apps_save` и необязательный цвет значка."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-grid-apps-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.object(launcher, "APPS_FILE", self.папка / "apps.json")
        подмена.start()
        self.addCleanup(подмена.stop)
        self.среда = object.__new__(WebRuntime)
        self.среда.voice = NS(_apps=None)
        self.среда._lock = threading.Lock()
        self.среда._bg = lambda func, *args: None
        self.среда._send_apps = lambda: None
        self.среда._remember = lambda kind, payload: None

    def _кнопка(self, **поле):
        основа = {"id": "discord", "title": "Discord", "kind": "app",
                  "path": "Discord.exe"}
        основа.update(поле)
        return основа

    def test_good_color_is_saved(self):
        res = self.среда.apps_save([self._кнопка(color="#FF7139")])
        self.assertTrue(res["ok"], res)
        self.assertEqual(launcher.read_list()[0]["color"], "#ff7139")

    def test_bad_color_is_dropped_and_the_rest_is_saved(self):
        res = self.среда.apps_save([self._кнопка(color="оранжевый")])
        self.assertTrue(res["ok"], res)
        self.assertNotIn("color", launcher.read_list()[0])

    def test_no_color_at_all_is_not_a_problem(self):
        res = self.среда.apps_save([self._кнопка()])
        self.assertTrue(res["ok"], res)
        self.assertNotIn("color", launcher.read_list()[0])


class СтраницаТелефонаTests(unittest.TestCase):
    """`web/index.html` текстом: как в `test_about_page.py`."""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.js = cls.html.split("<script>")[1].split("</script>")[0]

    def _блок(self, начало, конец="\n\n"):
        кусок = self.js.split(начало)
        self.assertEqual(len(кусок), 2, f"не нашлось начало: {начало}")
        return кусок[1].split(конец)[0]

    def test_edit_mode_is_asked_for_by_the_query(self):
        self.assertIn("const EDIT = new URLSearchParams(location.search)"
                      ".get('edit') === '1';", self.js)

    def test_edit_mode_never_opens_the_socket(self):
        # Иначе комп решит, что телефон на связи, и заговорит в макет.
        блок = self.js.split("if (!EDIT) {")[1].split("\n}")[0]
        self.assertIn("connect();", блок)
        self.assertIn("watchBattery();", блок)
        правка = self._блок("/* ---------- Режим правки ----------")
        # В коде блока `connect()` нет; в комментарии про него — есть, поэтому
        # смотрим только на строки после конца комментария.
        код = правка.split("*/", 1)[-1]
        self.assertNotIn("connect(", код)
        self.assertNotIn("WebSocket", код)

    def test_edit_mode_does_not_wake_up_or_reload(self):
        # Ни звука, ни полного экрана, ни удержания экрана, ни перезагрузки.
        self.assertIn("if (EDIT) return;",
                      self.js.split("async function wakeUp() {")[1].split("started")[0])
        self.assertIn("if (EDIT) return;",
                      self.js.split("function reloadSoon() {")[1].split("\n}")[0])
        self.assertIn("if (EDIT) return;",
                      self.js.split("setInterval(async () => {")[1].split("}, 30000)")[0])

    def test_data_comes_from_the_parent_only(self):
        блок = self._блок("window.addEventListener('message'")
        self.assertIn("event.source !== window.parent", блок)
        self.assertIn("event.origin !== location.origin", блок)
        self.assertIn("msg.type !== 'truba-edit'", блок)
        self.assertIn("drawApps(items, msg.layout)", блок)

    def test_all_four_messages_are_on_the_page(self):
        for что in ("truba-edit-ready", "truba-edit-pick", "truba-edit-order",
                    "truba-edit-action"):
            self.assertIn(f"'{что}'", self.js, что)
        self.assertIn("type: 'truba-edit'", self.js)
        # «+» добавления — пустые клетки сетки макета, а не лишняя плитка,
        # которая изменила бы раскладку. Сообщение `truba-edit-add` шлёт
        # нажатие на пустую клетку — см. tests/test_programs_mockup.py.
        self.assertIn("type: 'truba-edit-add'", self.js)

    def test_touching_a_tile_picks_it_and_dragging_reorders(self):
        # Касание без сдвига — выбор, сдвиг больше 6 px — перетаскивание.
        self.assertIn("const EDIT_DRAG_PX = 6;", self.js)
        блок = self._блок("function editBindTile(el, item) {")
        self.assertIn("type: 'truba-edit-pick'", блок)
        self.assertIn("type: 'truba-edit-order'", блок)
        self.assertIn("elementFromPoint", блок)
        self.assertIn("if (!перенесли) { editSay(", блок)

    def test_the_floating_add_button_is_gone(self):
        # Пустые клетки сетки заменяют плавающую кнопку «+».
        # Ищем узел `#edit-add`, а не подстроку `edit-add`: она законно живёт
        # в сообщении `truba-edit-add`, которое шлёт нажатие на пустую клетку.
        for кусок in ("#edit-add", "editСоздатьКнопку", "editПоставитьКнопку",
                      "EDIT_ADD_SIZE"):
            self.assertNotIn(кусок, self.js, кусок)
            self.assertNotIn(кусок, self.html, кусок)
        # Отдельной плитки «+» в сетке нет: лишняя плитка
        # ломала раскладку. Пустая клетка — другое, у неё свой класс.
        for кусок in ("editAddTile", "'app add'", ".app.add"):
            self.assertNotIn(кусок, self.js, кусок)
            self.assertNotIn(кусок, self.html, кусок)
        # Раскладка по-прежнему считается по числу программ, а не по числу
        # узлов в сетке (пустые клетки — узлы `#apps`, но не программы).
        блок = self._блок("function drawApps(items, layout) {", "\n\n")
        self.assertIn("layoutApps(items.length, вид)", блок)

    def test_the_grid_is_always_four_columns_and_one_or_two_rows(self):
        # Четыре колонки постоянны; число рядов ограничено настройкой.
        блок = self._блок("function gridView(layout) {", "\n\n")
        self.assertIn("const TILE_COLS = 4", self.js)
        self.assertIn("const TILE_COLS = 4, TILE_ROWS_MIN = 1, TILE_ROWS_MAX = 2;",
                      self.js)
        self.assertIn("cols: TILE_COLS", блок)
        self.assertNotIn("l.cols", блок)
        # Плитка меряется по всей сетке 4 × рядов, даже когда программ три.
        self.assertIn("free - TILE_GAP * (TILE_COLS - 1)) / TILE_COLS", self.js)

    def test_one_row_is_understood_by_the_phone_page(self):
        # `gridView` принимает один ряд, и
        # раскладка считает по сетке 4 × 1 (плитка крупнее, но не больше
        # `TILE_MAX` — ограничение сверху остаётся).
        блок = self._блок("function gridView(layout) {", "\n\n")
        self.assertIn("n >= TILE_ROWS_MIN && n <= TILE_ROWS_MAX", блок)
        self.assertIn("? n : TILE_ROWS", блок)
        self.assertIn("Math.min(TILE_MAX, byWidth, byHeight)", self.js)
        # Число клеток в ряду считается по `рядов`, а не по константе «2».
        # Берём всю функцию `layoutApps` (в ней пустые строки, поэтому
        # разрез по `\n\n` отсёк бы её после вертикальной раскладки).
        раскладка = self.js.split("function layoutApps(count, layout) {")[1]
        раскладка = раскладка.split("\n}\n")[0]
        self.assertIn("const рядов = Math.max(1, Math.min(вид.rows,", раскладка)
        self.assertIn("Math.ceil(клеток / TILE_COLS)", раскладка)

    def test_three_rows_from_an_old_message_become_two(self):
        # Старая страница или сервер пришлёт `rows: 3` — показываем два ряда.
        # Лишние плитки при этом не пропадают: сетка становится шире и листается
        # вбок (`scroll` в `layoutApps`), то есть текущим способом.
        self.assertIn("TILE_ROWS = 2", self.js)
        блок = self._блок("function gridView(layout) {", "\n\n")
        self.assertIn("n <= TILE_ROWS_MAX", блок)
        self.assertIn("? n : TILE_ROWS", блок)
        self.assertIn("const scroll = неВлезает || need > free;", self.js)
        self.assertIn("appsBox.classList.toggle('narrow', scroll);", self.js)

    def test_program_names_go_in_through_text_content(self):
        # Название программы — текст, а не разметка из сети.
        блок = self._блок("function drawApps(items, layout) {")
        self.assertIn("name.textContent = item.title;", блок)

    def test_the_three_styles_and_the_labels_are_in_the_styles(self):
        for кусок in ("#apps.round .app", "#apps.bare .app", "#apps.labels .app",
                      ".app .app-name", "border-radius: 50%",
                      "object-fit: cover"):
            self.assertIn(кусок, self.html, кусок)
        # Подписи читают издали: меньше 15 px на таком экране не прочесть.
        блок = self.html.split(".app .app-name {")[1].split("}")[0]
        размер = re.search(r"font-size:\s*(\d+)px", блок)
        self.assertIsNotNone(размер)
        self.assertGreaterEqual(int(размер.group(1)), 15)

    def test_the_own_color_of_a_program_wins(self):
        self.assertIn("item.color || APP_COLORS[item.id]", self.js)

    def test_the_rest_of_the_screen_is_visible_but_not_pressable(self):
        # Правило в стилях: круг, режимы и погода нажиматься не должны, но
        # остаются на месте — макет должен выглядеть как телефон.
        блок = "  body.edit #orb" + self.html.split("  body.edit #orb")[1].split("}")[0]
        for что in ("#orb", "#режимы", "#s-wx"):
            self.assertIn(что, блок, что)
        self.assertIn("pointer-events: none", блок)
        self.assertIn("document.body.classList.add('edit');", self.js)

    def test_the_bottom_buttons_stay_pressable_in_edit(self):
        # Нижние кнопки правятся касанием по макету, поэтому
        # `#actions` из правила `pointer-events: none` убран — иначе выбрать
        # кнопку было бы нечем.
        блок = "  body.edit #orb" + self.html.split("  body.edit #orb")[1].split("}")[0]
        self.assertNotIn("#actions", блок)


if __name__ == "__main__":
    unittest.main()
