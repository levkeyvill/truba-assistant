"""Макет телефона в «Программах»: перетаскивание, «+» и подменю.

Проверяем перетаскивание плиток, добавление через пустые клетки макета и
открытие подменю программы. Взаимосвязанные условия:

  - страница телефона в режиме `?edit=1` не отдаёт картинку мыши (`draggable`,
    `-webkit-user-drag`, `dragstart`), иначе жест обрывается на `pointercancel`;
  - пустые клетки для добавления не считаются программами в `layoutApps`;
  - макет всегда альбомный: телефон прислал «стоячий» размер — пульт и сервер
    запоминают его перевёрнутым (ширина — большее из двух чисел);
  - подменю в макете ничего не выполняет, а сообщает пульту
    `truba-edit-menu-pick` / `truba-edit-menu-add`, и пульт проверяет `app`,
    `index` и `kind` — сообщение может прийти откуда угодно, а список программ
    доверять ему нельзя.

Функции пульта проверяем через node (как в `tests/*.mjs`): вырезаем тело
функции и выполняем в отдельном процессе — так ловится и опечатка, и неверная
арифметика. Данные сервера — во временной папке (tests/test_data_guard.py).
"""

import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import config
from core import phone
from core.phone import PhoneServer

PAGE = Path(config.ROOT) / "web" / "index.html"
PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"


def node() -> str:
    """Путь к node или пустая строка: без него проверки через node пропускаются."""
    return shutil.which("node") or ""


def через_node(скрипт: str) -> str:
    """Выполнить скрипт в node и вернуть stdout.

    Отдельный процесс и отдельный файл: иначе тест упал бы с чужой ошибкой и
    не сказал бы, чья это была.
    """
    папка = tempfile.mkdtemp(prefix="truba-mockup-")
    куда = Path(папка) / "проверка.js"
    куда.write_text(скрипт, encoding="utf-8")
    try:
        итог = subprocess.run([node(), str(куда)], capture_output=True, text=True,
                              timeout=60)
    finally:
        shutil.rmtree(папка, ignore_errors=True)
    if итог.returncode != 0:
        raise AssertionError("node: " + итог.stderr.strip())
    return итог.stdout


def _тело(скрипт: str, имя: str) -> str:
    """Текст функции `имя` — от объявления до следующей того же уровня.

    Режем по началу строки с `function`, а не по пустой строке перед ней: в
    `pult.js` функции идут встык, и такой разрез захватывал бы ещё пол-файла
    (а в node лишний текст — это повторные объявления и ошибка компиляции).
    """
    начало = скрипт.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = скрипт[начало:]
    новое = re.search(r"^(?:async )?function \w+\(", остаток[1:], re.M)
    if новое:
        остаток = остаток[: новое.start() + 1]
    return остаток


def _между(скрипт: str, начало: str, конец: str) -> str:
    """Текст от `начало` до `конец` — для кусков вне отдельной функции."""
    кусок = скрипт.split(начало)
    assert len(кусок) == 2, "не нашлось начало: %s" % начало
    assert кусок[1].count(конец) == 1, "конец найден не раз: %s" % конец
    return начало + кусок[1].split(конец)[0]


class СтраницаТелефона(unittest.TestCase):
    """`web/index.html` текстом: как в `test_phone_grid.py`."""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.js = cls.html.split("<script>")[1].split("</script>")[0]

    def _блок(self, начало, конец=None):
        """Кусок скрипта телефона: до ближайшей строки `}` в нулевом отступе
        (то есть до конца функции верхнего уровня) или до `конец`."""
        кусок = self.js.split(начало)
        self.assertEqual(len(кусок), 2, f"не нашлось начало: {начало}")
        хвост = кусок[1]
        if конец is not None:
            return начало + хвост.split(конец)[0]
        граница = хвост.find("\n}\n")
        self.assertGreater(граница, 0, f"не нашёлся конец блока: {начало}")
        return начало + хвост[: граница + 3]

    def _правка(self) -> str:
        """Код блока `if (EDIT)` — без его комментария.

        Комментарий здесь не пустяк: он упоминает и `connect()`, и `WebSocket`,
        и их в коде блока нет, а проверка «в макете связи нет» именно на это и
        смотрит (см. test_phone_grid.py).
        """
        блок = _между(self.js, "/* ---------- Режим правки ----------",
                     "editSay({ type: 'truba-edit-ready' })")
        return блок.split("*/", 1)[-1]

    def _правило(self, селектор: str) -> str:
        начало = self.html.find(селектор)
        self.assertGreaterEqual(начало, 0, "правило %s не найдено" % селектор)
        return self.html[начало:начало + 420]


class Перетаскивание(СтраницаТелефона):
    """Значок — картинка, и мышь тянет именно её. В макете это запрещено."""

    def test_the_image_cannot_be_dragged(self):
        # Три защиты сразу: картинка не тянется, не выделяется и не берёт
        # указатель (жест целиком достаётся плитке).
        блок = self._правило("body.edit .app img {")
        for кусок in ("-webkit-user-drag: none", "user-select: none",
                      "pointer-events: none"):
            self.assertIn(кусок, блок, кусок)
        блок = self._правило("body.edit .app {")
        self.assertIn("touch-action: none", блок)
        self.assertIn("user-select: none", блок)

    def test_dragstart_is_cancelled(self):
        # На документе: браузер не должен увести картинку со страницы, даже
        # если он решил перетаскивание начать.
        код = self._правка()
        self.assertIn("dragstart", код)
        self.assertIn("event.preventDefault()", код)

    def test_the_attribute_is_set_on_the_image(self):
        # Одних стилей мало: без атрибута браузер всё равно начнёт своё
        # перетаскивание картинки.
        блок = self._блок("function appFace(item) {")
        self.assertIn('draggable="false"', блок)
        self.assertIn("EDIT", блок)

    def test_the_drag_logic_itself_is_untouched(self):
        # Порог 6 px и подмена места по рамке — то, что и делает жест
        # перетаскиванием. Правка их не трогала, только добавила запрет.
        блок = self._блок("function editBindTile(el, item) {")
        self.assertIn("const EDIT_DRAG_PX = 6;", self.js)
        for кусок in ("elementFromPoint", "over.after(el)", "over.before(el)",
                      "type: 'truba-edit-order'"):
            self.assertIn(кусок, блок, кусок)
class ПустыеКлетки(СтраницаТелефона):
    """«+» добавляет программу — пустыми клетками самой сетки макета.

    Клетки занимают свободные места сетки до `рядов × 4`, не меняя числа
    программ в раскладке. Они есть только в макете, их ровно столько, сколько
    нужно, нажатие шлёт
    `truba-edit-add`, и ни порядок программ, ни перетаскивание их не видят.
    """

    def _считает_пустые(self, программ, рядов) -> int:
        """Сколько клеток дорисует макет — считаем в node, а не разбором текста."""
        if not node():
            self.skipTest("node не найден")
        скрипт = (
            # Из реальной страницы нужны только эти две вещи: колонок всегда
            # четыре, а программы считаются по `data-app`.
            "const TILE_COLS = 4;\n"
            "const appsBox = { querySelectorAll: () => [" + ", ".join(
                "{ dataset: { app: 'p%d' } }" % i for i in range(программ)
            ) + "] };\n"
            + self._блок("function editСколькоПустых(вид) {")
            + "console.log(editСколькоПустых({ rows: " + str(рядов) + " }));\n"
        )
        return int(json.loads(через_node(скрипт)))

    def test_they_fill_the_grid_up_to_rows_times_four(self):
        # Два ряда — восемь мест, один — четыре: хозяин именно это и просил.
        self.assertEqual(self._считает_пустые(0, 2), 8)
        self.assertEqual(self._считает_пустые(3, 2), 5)
        self.assertEqual(self._считает_пустые(1, 1), 3)
        self.assertEqual(self._считает_пустые(4, 1), 0)

    def test_there_are_none_when_there_is_no_room(self):
        # Программ больше, чем клеток: пустых клеток нет вовсе, лишние
        # программы по-прежнему листаются вбок.
        self.assertEqual(self._считает_пустые(8, 2), 0)
        self.assertEqual(self._считает_пустые(7, 1), 0)
        self.assertEqual(self._считает_пустые(12, 2), 0)

    def test_they_are_only_drawn_in_the_mockup(self):
        # На настоящем телефоне пустых клеток нет: рисует их `drawApps` под
        # `if (EDIT)`, и больше их не зовёт никто.
        блок = self._блок("function drawApps(items, layout) {")
        self.assertIn("if (EDIT) editНарисоватьПустые(вид);", блок)
        вне = self.js.replace(блок, "")
        self.assertEqual(вне.count("editНарисоватьПустые("), 1,
                         "пустые клетки рисуются не только в макете")
        # У пустых клеток свой класс; отдельной плитки «+» в сетке нет.
        клетка = self._блок("function editПустаяКлетка() {")
        self.assertIn("el.className = 'app пусто'", клетка)
        self.assertNotIn("data-app", клетка)

    def test_a_click_asks_the_pult_to_add(self):
        клетка = self._блок("function editПустаяКлетка() {")
        self.assertIn("editSay({ type: 'truba-edit-add' })", клетка)
        self.assertIn("el.textContent = '+'", клетка)

    def test_they_look_like_tiles_but_empty(self):
        # Того же размера, что и программы (размер задаёт `layoutApps` общий
        # `--tile`), рамка пунктирная, «+» приглушённый и янтарный при наведении.
        блок = self._правило("body.edit #apps .app.пусто {")
        self.assertIn("border: 1px dashed var(--line);", блок)
        self.assertIn("color: var(--muted);", блок)
        self.assertIn("cursor: pointer;", блок)
        наведение = self._правило("body.edit #apps .app.пусто:hover {")
        self.assertIn("color: var(--accent);", наведение)

    def test_the_order_of_programs_does_not_see_them(self):
        # `editOrder` ищет `.app[data-app]` — у пустой клетки такого нет,
        # поэтому порядок программ «+» не портит. Там же и перетаскивание.
        self.assertIn(".app[data-app]", self._блок("function editOrder() {"))
        блок = self._блок("function editBindTile(el, item) {")
        self.assertIn("под.closest('.app[data-app]')", блок)
        self.assertIn(".app[data-app]", self._блок("function editPick(id) {"))

    def test_the_mockup_layout_counts_them(self):
        # Иначе при трёх программах в двух рядах макет показал бы один ряд,
        # и клетке «+» было бы некуда встать. Раскладка получает число
        # программ, а клетки добавляет сама — только в макете.
        блок = self._блок("function layoutApps(count, layout) {")
        self.assertIn("const клеток = EDIT ? Math.max(count, вид.rows * TILE_COLS) : count;",
                      блок)
        # Считаем по клеткам, а не по программам: иначе при пустых клетках
        # сетка поехала бы вбок.
        self.assertIn("клеток > TILE_COLS * вид.rows", блок)
        self.assertIn("Math.ceil(клеток / рядов)", блок)

    def test_the_floating_button_is_gone(self):
        # Её заменили клетки: ни узла, ни функции постановки, ни стилей.
        # Ищем `#edit-add`, а не подстроку `edit-add`: она законно живёт в
        # сообщении `truba-edit-add`, которое шлёт нажатие на пустую клетку.
        for кусок in ("#edit-add", "editСоздатьКнопку", "editПоставитьКнопку",
                      "EDIT_ADD_SIZE"):
            self.assertNotIn(кусок, self.js, кусок)
            self.assertNotIn(кусок, self.html, кусок)
        код = self._правка()
        self.assertNotIn("editСоздатьКнопку();", код)
        self.assertNotIn("editПоставитьКнопку();", код)
class ПодменюМакета(СтраницаТелефона):
    """Касание значка в макете открывает подменю — и оно ничего не выполняет."""

    def test_a_touch_opens_the_menu_and_still_picks(self):
        блок = self._блок("function editBindTile(el, item) {")
        self.assertIn("editSay({ type: 'truba-edit-pick', id: item.id })", блок)
        self.assertIn("openMenu(item)", блок)
        # Перетаскивание подменю не открывает: `openMenu` стоит внутри
        # `if (!перенесли)`, а не в конце обработчика.
        self.assertLess(блок.index("if (!перенесли)"), блок.index("openMenu(item)"))
        self.assertIn("type: 'truba-edit-order'", блок)

    def test_the_real_phone_menu_still_launches(self):
        # Не-`EDIT` не тронут: главная плитка запускает программу.
        блок = self._блок("function openMenu(app) {")
        self.assertIn("send({ type: 'menu', app: app.id, item: 'launch' })", блок)
        self.assertIn("if (EDIT) { editSay({ type: 'truba-edit-pick', id: app.id }); return; }",
                      блок)

    def test_items_only_report_the_pick(self):
        блок = self._блок("function menuBindClick(el, item, app, номер) {")
        self.assertIn("truba-edit-menu-pick", блок)
        self.assertIn("app: app.id, index: номер", блок)
        # В ветке `EDIT` ничего не отправляется на телефон.
        ветка = блок.split("if (EDIT) {")[1].split("\n  }")[0]
        self.assertNotIn("send(", ветка)

    def test_the_plus_tile_and_the_two_kinds(self):
        # Последняя плитка подменю — «+», рисованная как обычный пункт, с
        # подписью «добавить», и она есть даже у программы без пунктов.
        блок = self._блок("function openMenu(app) {")
        self.assertIn("if (EDIT) menuTiles.appendChild(editПлиткаПлюс(app))", блок)
        self.assertIn("const пункты = Array.isArray(app.menu) ? app.menu : []", блок)
        # Плитка «+» входит в раскладку — иначе последний пункт уехал бы вниз.
        self.assertIn("пункты.concat([{ kind: 'плюс' }])", блок)
        плюс = self._блок("function editПлиткаПлюс(app) {")
        self.assertIn("tag.textContent = 'добавить'", плюс)
        self.assertIn("editПоказатьВиды(app)", плюс)

    def test_the_two_kinds_are_reported_to_the_pult(self):
        блок = self._блок("function editПоказатьВиды(app) {")
        self.assertIn("editПлиткаВида(app, 'site'", блок)
        self.assertIn("'Сайт'", блок)
        self.assertIn("editПлиткаВида(app, 'hotkey'", блок)
        self.assertIn("'Сочетание клавиш'", блок)
        плитка = self._блок("function editПлиткаВида(app, вид, значок, подпись) {")
        self.assertIn("type: 'truba-edit-menu-add'", плитка)
        self.assertIn("kind: вид", плитка)

    def test_an_open_menu_is_redrawn_not_closed(self):
        # Хозяин добавил пункт — пришло новое сообщение. Закрывать подменю
        # нельзя: правка была бы не видна. Программу узнаём по `id`.
        блок = self._блок("function editОбновитьМеню(items) {")
        self.assertIn("menuBox.classList.contains('on')", блок)
        self.assertIn("items.find((item) => item && item.id === currentMenu.id)", блок)
        self.assertIn("openMenu(такая)", блок)
        код = self._правка()
        self.assertIn("editОбновитьМеню(items);", код)

    def test_an_empty_place_closes_the_menu_as_on_the_phone(self):
        # То же, что и на телефоне: `pointerdown` мимо плитки закрывает.
        блок = self._блок("menuBox.addEventListener('pointerdown'")
        self.assertIn("closeMenu();", блок)

    def test_the_phone_keeps_its_own_menu_behaviour(self):
        # Настоящий телефон: переключатель, запуск пункта, меню по наличию
        # пунктов — всё как было.
        блок = self._блок("function menuBindClick(el, item, app, номер) {")
        self.assertIn("if (item.toggle) {", блок)
        self.assertIn("send({ type: 'menu', app: currentMenu.id, item: item.key })", блок)
        блок = self._блок("function drawApps(items, layout) {")
        self.assertIn("if (item.menu && item.menu.length) { openMenu(item); return; }", блок)
        self.assertIn("send({ type: 'launch', id: item.id })", блок)
class РазмерЭкрана(unittest.TestCase):
    """Макет всегда альбомный: телефон в проекте лежит горизонтально."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_mockup_is_landscape_whatever_the_phone_says(self):
        # Телефон мог прислать «стоячий» размер (повернули и отключили) — макет
        # обязан остаться альбомным. Считаем в node, а не разбором текста:
        # проверять надо результат, а не наличие `Math.max`.
        if not node():
            self.skipTest("node не найден")
        скрипт = (
            "let ПРОГ_ШИРИНА_ЭКРАНА = 851;\n"
            "let ПРОГ_ВЫСОТА_ЭКРАНА = 393;\n"
            + _тело(self.js, "прогВзятьРазмерЭкрана") + "\n"
            "const выход = [];\n"
            "const размеры = [{w:393,h:851},{w:851,h:393},{w:800,h:600},"
            "{w:640,h:480},{w:'мусор',h:851},{w:100,h:851},{}];\n"
            "for (const v of размеры) {\n"
            "  ПРОГ_ШИРИНА_ЭКРАНА = 851; ПРОГ_ВЫСОТА_ЭКРАНА = 393;\n"
            "  const ok = прогВзятьРазмерЭкрана({ viewport: v });\n"
            "  выход.push([ok, ПРОГ_ШИРИНА_ЭКРАНА, ПРОГ_ВЫСОТА_ЭКРАНА]);\n"
            "}\n"
            "console.log(JSON.stringify(выход));\n"
        )
        строки = json.loads(через_node(скрипт))
        self.assertEqual(строки[0], [True, 851, 393])   # стоячий телефон
        self.assertEqual(строки[1], [True, 851, 393])   # альбомный телефон
        self.assertEqual(строки[2], [True, 800, 600])   # шире, чем выше
        self.assertEqual(строки[3], [True, 640, 480])   # тоже шире, чем выше
        # Мусор и неправдоподобные числа — прежние 851×393, а не пустой макет.
        for плохое in строки[4:]:
            self.assertEqual(плохое, [False, 851, 393], плохое)

    def test_the_phone_stores_the_same_landscape_size(self):
        # Сервер отдаёт размер в `/api/apps`: тот же, что у макета, иначе пульт
        # написал бы «Экран телефона 393×851» и встал бы столбом.
        сервер = self._сервер()
        сервер._on_message(None, {"type": "viewport", "w": 393, "h": 851})
        self.assertEqual(сервер.viewport, {"w": 851, "h": 393})
        # И на диск — иначе после перезапуска пульта макет прыгнул бы обратно.
        сохранено = json.loads(Path(phone.VIEWPORT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(сохранено, {"w": 851, "h": 393})
        # Альбомный размер не трогаем.
        сервер._on_message(None, {"type": "viewport", "w": 851, "h": 393})
        self.assertEqual(сервер.viewport, {"w": 851, "h": 393})

    def _сервер(self) -> PhoneServer:
        """`PhoneServer` без сокета и потока, файл размера — во временный."""
        папка = Path(tempfile.mkdtemp(prefix="truba-viewport-"))
        self.addCleanup(shutil.rmtree, str(папка), True)
        прежний = phone.VIEWPORT_FILE
        self.addCleanup(setattr, phone, "VIEWPORT_FILE", прежний)
        phone.VIEWPORT_FILE = папка / "phone_viewport.json"
        сервер = object.__new__(PhoneServer)
        сервер._clients = set()
        сервер._audio = set()
        сервер._listeners = []
        сервер._ready = threading.Event()
        сервер._loop = None
        сервер._viewport = None
        return сервер
class Пульт(unittest.TestCase):
    """`ui/web/pult.js`: новые сообщения и проверка входящих значений."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def _тело(self, имя):
        return _тело(self.js, имя)

    def test_the_items_go_with_their_submenu(self):
        # Без пунктов подменю макет нечего рисовать, и нажать на значок было бы
        # не на что. Название без текста — «без названия», а не пустая плитка.
        блок = self._тело("прогПозиции")
        self.assertIn("menu: прогПунктыМеню(п)", блок)
        пункты = self._тело("прогПунктыМеню")
        self.assertIn("Array.isArray(п.menu)", пункты)
        self.assertIn("'без названия'", пункты)
        for поле in ("kind", "title", "icon", "keys", "url"):
            self.assertIn(поле, пункты, поле)

    def test_both_new_answers_are_handled(self):
        блок = self._тело("прогСлушатьЭкран")
        self.assertIn("'truba-edit-menu-add'", блок)
        self.assertIn("прогДобавитьПунктИзМакета(ctx, msg.app, msg.kind)", блок)
        self.assertIn("'truba-edit-menu-pick'", блок)
        self.assertIn("прогВыбратьПунктИзМакета(ctx, msg.app, msg.index)", блок)
        # Проверка отправителя и адреса обязательна: страниц-отправителей
        # в пульте много, а список программ доверять им нельзя.
        self.assertIn("событие.source !== ctx.экран.contentWindow", блок)
        self.assertIn("событие.origin !== location.origin", блок)

    def test_the_add_buttons_and_the_mockup_share_one_function(self):
        # Иначе пути разошлись бы, и с макета пункт появлялся бы не с тем
        # значком или не в том списке.
        панель = self._тело("прогПостроитьМеню")
        self.assertIn("прогДобавитьПункт(ctx, номер, вид)", панель)
        self.assertIn("добавить('site')", панель)
        self.assertIn("добавить('hotkey')", панель)
        блок = self._тело("прогДобавитьПункт")
        self.assertIn("тек.menu.push(пункт)", блок)
        self.assertIn("прогСвободныйIdПункта", блок)
        # Фокус — в поле названия нового пункта, экран обновляется предпросмотром.
        self.assertIn('[data-меню-поле="title"]', блок)
        self.assertIn("прогОбновитьПревью(ctx)", блок)

    def test_a_bad_app_is_ignored(self):
        # Сообщение может прийти откуда угодно: неизвестная программа — это не
        # программа, и добавлять пункт некуда.
        блок = self._тело("прогНомерПоId")
        self.assertIn("typeof id !== 'string' || !id", блок)
        for имя in ("прогДобавитьПунктИзМакета", "прогВыбратьПунктИзМакета"):
            with self.subTest(имя=имя):
                блок = self._тело(имя)
                # Проверка имени — до всякой работы с черновиком.
                self.assertIn("if (прогНомерПоId(app) < 0) return;", блок)
                self.assertLess(блок.index("прогНомерПоId(app) < 0"),
                                блок.index("прогДобавитьПункт(")
                                if "прогДобавитьПункт(" in блок
                                else блок.index("const номер = прогНомерПоId(app)"))

    def test_a_bad_kind_is_ignored(self):
        блок = self._тело("прогДобавитьПунктИзМакета")
        self.assertIn("вид !== 'site' && вид !== 'hotkey'", блок)
        # Вид приходит от макета строкой: объект с полем `kind` — мусор.
        self.assertIn("typeof kind === 'string'", блок)

    def test_a_bad_index_is_ignored(self):
        блок = self._тело("прогВыбратьПунктИзМакета")
        self.assertIn("typeof index !== 'number'", блок)
        self.assertIn("Number.isInteger(index)", блок)
        self.assertIn("index < 0", блок)
        # Номер обязан быть в пределах списка: иначе показали бы чужой пункт.
        self.assertIn("index >= сколько", блок)
        self.assertIn("Array.isArray(тек.menu)", блок)

    def test_the_rejections_are_checked_by_node(self):
        # Проверяем не слова проверки, а сам код: прогоняем обе дороги с плохими
        # значениями и смотрим, что черновик не изменился.
        if not node():
            self.skipTest("node не найден")
        куски = "\n".join(self._тело(имя) for имя in (
            "прогНомерПоId", "прогДобавитьПунктИзМакета",
            "прогВыбратьПунктИзМакета", "прогСвободныйIdПункта"))
        скрипт = (
            "let прогПриложения = [{ id: 'a', menu: [{ kind: 'hotkey' }] }];\n"
            "const выход = [];\n"
            # Панели тут нет: проверяем только решения функций, поэтому
            # `прогВыбрать` подменяем — она рисует свойства и зовёт сеть.
            "function прогСобратьВсе() {}\n"
            "function прогВыбрать(ctx, id) {\n"
            "  const куда = прогПриложения.findIndex((п) => п.id === id);\n"
            "  if (куда < 0) return;\n"
            "  ctx.индекс = куда; ctx.выбранный = id; ctx.слот = -1;\n"
            "  ctx.свойства = { querySelectorAll: () => [] };\n"
            "}\n"
            "const ctx = { список: null, свойства: null, статус: null };\n"
            + куски + "\n"
            # Неизвестная программа, чужой вид, не строка — пункт не добавляется.
            "const добавления = [['нет', 'site'], ['a', 'bookmark'],"
            " ['a', 42], [null, 'site'], ['a', null]];\n"
            "for (const [app, kind] of добавления) {\n"
            "  прогДобавитьПунктИзМакета(ctx, app, kind);\n"
            "  выход.push(прогПриложения[0].menu.length);\n"
            "}\n"
            # Плохой номер не выбирает программу, правильный — выбирает.
            "const номера = [['нет', 0], ['a', 'мусор'], ['a', 9],"
            " ['a', -1], ['a', 1.5], ['a', 0]];\n"
            "for (const [app, index] of номера) {\n"
            "  ctx.выбранный = '';\n"
            "  прогВыбратьПунктИзМакета(ctx, app, index);\n"
            "  выход.push(String(ctx.выбранный || ''));\n"
            "}\n"
            "console.log(JSON.stringify(выход));\n"
        )
        строки = json.loads(через_node(скрипт))
        self.assertEqual(строки[:5], [1, 1, 1, 1, 1],
                         "плохое сообщение добавило пункт")
        self.assertEqual(строки[5:], ["", "", "", "", "", "a"], строки)

    def test_every_helper_the_tab_calls_is_defined(self):
        # Опечатка в кириллическом имени проходит `node --check`, а вкладку
        # валит целиком (coordination/ГРАБЛИ.md).
        объявлены = set(re.findall(r"^(?:async )?function\s+([\wЁё]+)\(",
                                   self.js, re.M))
        вызваны = set()
        for имя in ("прогСлушатьЭкран", "прогДобавитьПунктИзМакета",
                    "прогВыбратьПунктИзМакета", "прогДобавитьПункт",
                    "прогПозиции", "прогВзятьРазмерЭкрана"):
            вызваны |= set(re.findall(r"(?<![\w.])(прог\w+)\s*\(",
                                      self._тело(имя)))
        self.assertTrue(вызваны, "вкладка «Программы» не найдена в pult.js")
        self.assertEqual(sorted(вызваны - объявлены), [],
                         "вызваны, но не объявлены: вкладка упала бы в браузере")

    def test_the_line_endings_are_as_they_should_be(self):
        # `index.html` — LF (страница отдаётся как есть), `pult.js` — CRLF.
        self.assertNotIn(b"\r\n", PAGE.read_bytes())
        self.assertIn(b"\r\n", PULT_JS.read_bytes())


if __name__ == "__main__":
    unittest.main()