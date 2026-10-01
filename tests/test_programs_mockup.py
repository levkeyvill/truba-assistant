"""Макет телефона в «Программах»: перетаскивание, «+» и подменю (1 октября).

Хозяин нажал на значок в макете — и ничего, кроме выбора программы, не
произошло; заодно мышь тянула не плитку, а картинку значка. Просил он три
вещи: таскать значки мышью, добавлять программу кнопкой «+» прямо с макета и
открывать подменю программы с его «+». Проверяем то, что легко разъехаться:

  - страница телефона в режиме `?edit=1` не отдаёт картинку мыши (`draggable`,
    `-webkit-user-drag`, `dragstart`), иначе жест обрывается на `pointercancel`;
  - кнопка «+» стоит ПОВЕРХ области программ, а не плиткой в сетке: `layoutApps`
    по-прежнему считает только программы (28.09 девятая плитка ломала раскладку);
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
class КнопкаПлюс(СтраницаТелефона):
    """«+» добавляет программу — кнопкой поверх сетки, а не плиткой в ней."""

    def test_the_button_lives_outside_the_grid(self):
        # В `#apps` она была бы девятой программой: и `layoutApps`, и
        # `childElementCount` на повороте считали бы её. Поэтому она в `body`.
        блок = self._блок("function editСоздатьКнопку() {")
        self.assertIn("document.body.appendChild(кнопка)", блок)
        self.assertNotIn("appsBox.appendChild", блок)

    def test_the_button_is_only_in_the_mockup(self):
        # На настоящем телефоне её нет: `editСоздатьКнопку` зовётся только
        # внутри `if (EDIT)`, и до блока правки её нет вовсе.
        код = self._правка()
        self.assertIn("editСоздатьКнопку();", код)
        вне = self.js.split("/* ---------- Режим правки ----------")[0]
        self.assertNotIn("editСоздатьКнопку(", вне)

    def test_it_is_a_round_amber_button_of_forty_pixels(self):
        блок = self._правило("body.edit #edit-add {")
        self.assertIn("width: 40px; height: 40px;", блок)
        self.assertIn("border-radius: 50%", блок)
        self.assertIn("var(--accent)", блок)
        # Поверх сетки, а не в потоке: иначе она тянула бы за собой плитки.
        self.assertIn("position: fixed", блок)
        self.assertIn("z-index: 12", блок)
        self.assertIn("const EDIT_ADD_SIZE = 40;", self.js)

    def test_it_avoids_the_tiles_by_their_boxes(self):
        # При восьми программах сетка заполнена целиком: считаем рамки и ищем
        # свободное место, иначе «+» накрыла бы восьмой значок.
        блок = self._блок("function editПоставитьКнопку() {")
        self.assertIn("appsBox.getBoundingClientRect()", блок)
        self.assertIn(".app[data-app]", блок)
        self.assertIn("рамка.right", блок)
        self.assertIn("кнопка.hidden", блок)

    def test_a_click_asks_the_pult_to_add(self):
        блок = self._блок("function editСоздатьКнопку() {")
        self.assertIn("editSay({ type: 'truba-edit-add' })", блок)
        self.assertIn("кнопка.textContent = '+'", блок)

    def test_the_grid_layout_is_still_about_programs_only(self):
        блок = self._блок("function drawApps(items, layout) {")
        self.assertIn("layoutApps(items.length, вид)", блок)
        # Кнопки нет внутри `drawApps` — её место не влияет на сетку.
        self.assertNotIn("edit-add", блок)

    def test_it_is_placed_after_every_update(self):
        # Размер плиток и положение сетки меняются после каждого сообщения —
        # кнопку надо ставить заново, иначе она останется в старом углу.
        код = self._правка()
        self.assertIn("editПоставитьКнопку();", код)
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