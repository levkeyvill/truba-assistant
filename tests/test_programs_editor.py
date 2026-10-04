"""Вкладка «Программы» в пульте: живой экран телефона и правка руками.

Хозяин попросил не длинный список форм, а сам телефон, на котором он жмёт
значок и правит его руками. Проверяем то, что легко разъехаться:

  - пульт открывает страницу телефона в `?edit=1` и разговаривает с ней
    сообщениями `truba-edit` / `truba-edit-pick` / `truba-edit-add` /
    `truba-edit-order`;
  - сообщения принимаются только от нашего iframe и только с нашего адреса:
    страниц-отправителей в пульте много, а список программ доверять им нельзя;
  - вид сетки (`phone_cols`, `phone_rows`, `phone_icon_style`, `phone_labels`)
    едет в `/api/settings` и никуда больше;
  - значок выбирается: рисованный из набора, из файла (`/api/apps/icon`) или
    из самой программы (`pick_icon`), а `color` попадает в черновик;
  - старого списка форм (`прог-список`) в коде больше нет.

Проверка идёт по исходнику `pult.js`: `node --check` не ловит ни опечатку в
имени, ни вызов несуществующей функции — а именно это валит страницу целиком
(coordination/ГРАБЛИ.md). Поэтому имена, которыми пользуется код, сверяются с
объявлениями.

Данные — временные: живой `apps.json` и `settings.json` хозяина тест не трогает
(tests/test_data_guard.py).
"""

import re
import unittest

import config

PULT_JS = config.ROOT / "ui" / "web" / "pult.js"
PULT_CSS = config.ROOT / "ui" / "web" / "pult.css"

# Функции вкладки: между ними нет постороннего кода, иначе проверки внизу
# нарезали бы чужой текст.
ТЕКСТ_ВКЛАДКИ = (
    "нарисоватьПрограммы", "прогНарисоватьСетку", "прогПостроитьСтроку",
    "прогСлушатьЭкран", "прогВыбрать", "прогПорядок", "прогОтправитьЭкран",
    "прогОбновитьПревью", "прогСохранить", "прогЗагрузитьСетку",
    "прогПрименитьЗначок", "прогСобратьСтроку", "прогПерерисовать",
    "прогНарисоватьОбраз", "прогПозиции", "прогОбновитьЭкран",
    "прогЗагрузитьЗапущенные", "прогКарточкаЗапущенного",
    "прогВзятьРазмерЭкрана", "прогПрименитьРазмерЭкрана",
)


def _тело(скрипт: str, имя: str) -> str:
    """Текст функции `имя` — от объявления до следующей того же уровня.

    `node --check` такую опечатку не ловит: вызов несуществующей функции или
    чужое имя переменной валят страницу в тот момент, когда до них доходит.
    """
    начало = скрипт.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = скрипт[начало:]
    новое = re.search(r"^\n(?:async )?function \w+\(", остаток[1:], re.M)
    if новое:
        остаток = остаток[: новое.start() + 1]
    return _без_комментариев(остаток)


def _без_комментариев(скрипт: str) -> str:
    """Текст без комментариев.

    Имена вызовов ищутся регуляркой по всему тексту функции, а в комментарии
    может стоять любая проза: «как у программы (core/app_icons.py умеет и
    папку)» — это не вызов `программы(`, и вкладка из-за него не падает.
    Комментарий убираем, а длину строк сохраняем, чтобы номера строк в
    сообщениях об ошибке остались настоящими.
    """
    Блочный = re.compile(r"/\*.*?\*/", re.S)
    Однострочный = re.compile(r"(?<!:)//[^\n]*")

    def убрать(текст: str) -> str:
        # Пробелы вместо символов: номера строк в сообщениях об ошибке
        # остаются настоящими, а текст выравнивается по исходному.
        текст = Блочный.sub(lambda м: re.sub(r"[^\n]", " ", м.group(0)), текст)
        return Однострочный.sub("", текст)

    return убрать(скрипт)


def _правило(селектор: str, длина: int = 400) -> str:
    """Начало правила CSS — чтобы проверять его, а не весь файл."""
    css = PULT_CSS.read_text(encoding="utf-8")
    начало = css.find(селектор)
    assert начало >= 0, "правило %s не найдено" % селектор
    return css[начало:начало + длина]


class ЭкранТелефонаТесты(unittest.TestCase):
    """Пульт и страница телефона в режиме правки — по разные стороны iframe."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_screen_is_an_iframe_of_our_own_page(self):
        # `?edit=1` — единственный признак режима правки на странице телефона.
        блок = _тело(self.js, "нарисоватьПрограммы")
        self.assertIn("createElement('iframe')", блок)
        self.assertIn("экран.src = '/?edit=1'", блок)
        # Размер экрана — как у настоящего телефона (иначе макет врёт), а пока
        # телефон не подключался — прежние 851×393, телефон в альбомной.
        self.assertIn("let ПРОГ_ШИРИНА_ЭКРАНА = 851", self.js)
        self.assertIn("let ПРОГ_ВЫСОТА_ЭКРАНА = 393", self.js)
        self.assertIn("transform-origin: 0 0", _правило(".прог-телефон-экран {"))

    def test_the_screen_takes_the_size_from_the_phone(self):
        # Размер едет из `viewport` в ответе `/api/apps`; мусор — прежние
        # 851×393, а не пустое окно.
        блок = _тело(self.js, "прогВзятьРазмерЭкрана")
        self.assertIn("данные.viewport", блок)
        self.assertIn("Number.isFinite", блок)
        # Под экраном написано, чей это размер, — молчаливый макет хозяин принял
        # бы за свой телефон.
        подпись = _тело(self.js, "прогПрименитьРазмерЭкрана")
        self.assertIn("Размер телефона ещё не известен", подпись)
        self.assertIn("Экран телефона ", подпись)
        self.assertIn("прогПрименитьРазмерЭкрана(ctx, прогВзятьРазмерЭкрана(данные))",
                      _тело(self.js, "нарисоватьПрограммы"))

    def test_we_send_the_draft_and_the_layout(self):
        блок = _тело(self.js, "прогОтправитьЭкран")
        self.assertIn("type: 'truba-edit'", блок)
        for поле in ("items", "layout", "selected"):
            self.assertIn(поле, блок)
        # Матчинг — свой адрес, а не «любой»: страниц-отправителей много.
        self.assertIn("location.origin", блок)

    def test_the_screen_is_not_spammed_on_every_keystroke(self):
        # Правка идёт на каждое нажатие клавиши, а список значков возить
        # незачем часто: между отправками пауза, последняя её дожидается.
        self.assertIn("const ПРОГ_ПАУЗА_ЭКРАНА = 150", self.js)
        блок = _тело(self.js, "прогОбновитьЭкран")
        self.assertIn("if (ctx.таймерЭкрана) return;", блок)
        self.assertIn("setTimeout", блок)

    def test_all_four_answers_of_the_phone_page_are_handled(self):
        блок = _тело(self.js, "прогСлушатьЭкран")
        self.assertIn("'truba-edit-ready'", блок)
        self.assertIn("'truba-edit-pick'", блок)
        self.assertIn("'truba-edit-add'", блок)
        self.assertIn("'truba-edit-order'", блок)

    def test_only_our_own_iframe_may_talk(self):
        # Проверка отправителя и адреса — обязательна: пульт слушает `message`
        # на всём окне, а прислать сообщение может любая страница.
        блок = _тело(self.js, "прогСлушатьЭкран")
        self.assertIn("событие.source !== ctx.экран.contentWindow", блок)
        self.assertIn("событие.origin !== location.origin", блок)

    def test_pick_add_and_order_reach_the_draft(self):
        блок = _тело(self.js, "прогСлушатьЭкран")
        self.assertIn("прогВыбрать(ctx, String(msg.id || ''))", блок)
        self.assertIn("прогПоказатьДобавление(ctx, true)", блок)
        self.assertIn("прогПорядок(ctx, msg.ids)", блок)
        # Порядок с экрана переставляет черновик, а не рисует по нему.
        порядок = _тело(self.js, "прогПорядок")
        self.assertIn("прогПриложения = новый", порядок)
        self.assertIn("прогОбновитьПревью(ctx)", порядок)

    def test_every_helper_the_tab_calls_is_defined(self):
        # Опечатка в кириллическом имени (`цвeт` вместо `цвет`) проходит
        # `node --check`, а вкладку валит целиком. `async function` — тоже
        # объявление, а половина нашей вкладки именно такая.
        объявлены = set(re.findall(r"^(?:async )?function\s+([\wЁё]+)\(",
                                   self.js, re.M))
        вызваны = set()
        for имя in ТЕКСТ_ВКЛАДКИ:
            вызваны |= set(re.findall(r"(?<![\w.])(прог\w+)\s*\(",
                                      _тело(self.js, имя)))
        self.assertTrue(вызваны, "вкладка «Программы» не найдена в pult.js")
        self.assertEqual(sorted(вызваны - объявлены), [],
                         "вызваны, но не объявлены: вкладка упала бы в браузере")


class ВидСеткиТесты(unittest.TestCase):
    """Три ключа из `/api/settings` — и только они. Колонок нет: их всегда 4."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_columns_group_is_not_there_anymore(self):
        # Выбор колонок убран: сетка всегда четыре программы в ширину.
        блок = _тело(self.js, "прогНарисоватьСетку")
        self.assertNotIn("Колонки", блок)
        self.assertNotIn("'cols'", блок)

    def test_one_or_two_rows_are_offered(self):
        # В переключателе «Ряды» две кнопки: один ряд или два. «3»
        # предлагать нечего: трёх рядов на телефоне не помещалось,
        # и осталось бы только старое значение из живого settings.json.
        блок = _тело(self.js, "прогНарисоватьСетку")
        ряды = [строка for строка in блок.splitlines() if "'Ряды'" in строка]
        self.assertTrue(ряды, "в пульте нет переключателя рядов")
        self.assertIn("[['1', '1'], ['2', '2']]", ряды[0])
        self.assertNotIn("'3'", ряды[0])
        # Ряды — число: сервер проверяет их как число, а не как слово.
        self.assertIn("(ключ === 'style')", блок)
        self.assertIn("Number(значение)", блок)
        # Переключение сразу перерисовывает макет (черновик, как у значков) —
        # иначе хозяин увидел бы старый ряд и решил, что кнопка не сработала.
        self.assertIn("прогОбновитьЭкран(ctx);", блок)
        self.assertIn("прогПометитьЧерновик(ctx);", блок)
        # Макет в пульте показывает столько же рядов, сколько телефон нарисует.
        self.assertIn("rows: прогНормаРядов(с.rows)", _тело(self.js, "прогВидСетки"))

    def test_the_three_keys_are_read_from_settings(self):
        блок = _тело(self.js, "прогЗагрузитьСетку")
        self.assertIn("fetch('/api/settings'", блок)
        for ключ in ("phone_rows", "phone_icon_style", "phone_labels"):
            self.assertIn(ключ, блок)
        # Ряды из живого settings.json читаем — выбора «1 или 2» теперь два.
        # Нормализуем: старое «3» пульт показывает и сохраняет как два, а не
        # подсовывает телефону сетку, которой в переключателе нет.
        self.assertIn("прогСетка.rows = прогНормаРядов(s.phone_rows);", блок)
        self.assertIn("const ПРОГ_РЯДЫ = [1, 2];", self.js)
        self.assertIn("return ПРОГ_РЯДЫ.includes(n) ? n : ПРОГ_РЯДЫ_УМОЛЧАНИЕ;",
                      self.js)
        # Старое `phone_cols` в живом settings.json больше не решает ничего.
        self.assertNotIn("s.phone_cols", блок)

    def test_the_three_keys_go_to_settings_and_nowhere_else(self):
        блок = _тело(self.js, "прогСохранить")
        self.assertIn("fetch('/api/settings'", блок)
        for ключ in ("phone_rows", "phone_icon_style", "phone_labels"):
            self.assertIn(ключ, блок)
        self.assertNotIn("phone_cols:", блок)
        # Чужие разделы настроек пульт переписывать под себя не должен.
        отправка = блок[блок.find("phone_rows"):]
        self.assertNotIn("model", отправка)
        self.assertNotIn("voice", отправка)


class ЗапущенныеТесты(unittest.TestCase):
    """«Из запущенных…» — первым пунктом меню «+ Программа»."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_first_item_of_the_menu_is_running_ones(self):
        блок = _тело(self.js, "нарисоватьПрограммы")
        первый = блок[блок.find("const варианты = ["):]
        первый = первый[:первый.find("];") + 1]
        self.assertIn("Из запущенных…", первый.split("],")[0])

    def test_the_list_comes_from_the_computer(self):
        блок = _тело(self.js, "прогЗагрузитьЗапущенные")
        self.assertIn("fetch('/api/apps/running'", блок)
        self.assertIn("Смотрю, что запущено…", блок)
        # Обработчик пункта меню сворачивает блок добавления перед действием,
        # поэтому панель обязана показать его сама: иначе карточки есть в DOM,
        # но хозяин их не видит (так и вышло в проверке в браузере).
        self.assertIn("прогПоказатьДобавление(ctx, true)", блок)
        # Пока грузится — не пустая панель, а слова, что мы смотрим.
        self.assertIn("Не вижу открытых программ", _тело(self.js, "прогНарисоватьЗапущенные"))
        self.assertIn("Обновить", _тело(self.js, "прогНарисоватьЗапущенные"))

    def test_already_added_ones_are_dimmed_and_not_pressable(self):
        # Иначе получилось бы две одинаковые кнопки на телефоне.
        блок = _тело(self.js, "прогКарточкаЗапущенного")
        self.assertIn("уже есть", блок)
        self.assertIn("карточка.disabled = true", блок)
        self.assertIn("прогДобавитьСтроку(ctx", блок)
        # Значок 32 px и имя exe рядом с названием — как просил хозяин.
        self.assertIn("прог-запущенная-значок", блок)
        self.assertIn("width: 32px", _правило(".прог-запущенная-значок {"))

    def test_the_button_says_what_it_does(self):
        блок = _тело(self.js, "нарисоватьПрограммы")
        self.assertIn("Добавить: из запущенных, файл с компьютера, ссылка или вручную",
                      блок)
        # Подсказка под экраном больше не зовёт плитку «+», которой нет, и
        # зовёт нижние кнопки: ими правят что они делают.
        self.assertIn("«+ Программа» ниже", блок)
        self.assertIn("Нажми нижнюю кнопку", блок)
        self.assertNotIn("«+» в конце сетки", блок)
        # И пустая панель свойств — тоже: «+» в сетке больше нет.
        пусто = _тело(self.js, "прогПерерисовать")
        self.assertIn("кнопка «+ Программа» под экраном", пусто)
        self.assertNotIn("или «+», чтобы добавить", пусто)



class ЗначокТесты(unittest.TestCase):
    """Значок выбирается тремя способами, и телефон видит выбор сразу."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_drawn_set_is_only_for_the_drawn_icon(self):
        блок = _тело(self.js, "прогПрименитьЗначок")
        self.assertIn("показать('набор', тек === 'drawn')", блок)
        self.assertIn("показать('цвет', тек === 'drawn')", блок)
        self.assertIn("показать('файл', тек === 'file')", блок)
        # Источник — кнопки, а не выпадающий список со словами «рисованный».
        for надпись in ("Из набора", "Из файла…", "Из самой программы"):
            self.assertIn(надпись, self.js)

    def test_from_a_file_works_the_way_it_always_did(self):
        # Старый путь не переписан: картинка уходит в `/api/apps/icon`,
        # значок из программы — через нативный `pick_icon`.
        блок = _тело(self.js, "прогПостроитьСтроку")
        self.assertIn("fetch('/api/apps/icon'", блок)
        self.assertIn("api.pick_icon(ключ)", блок)
        # И то, и другое оставляет источником `file`, а не выдуманный ключ.
        self.assertIn("тек.icon_source = 'file'", блок)

    def test_from_the_program_itself_is_only_offered_to_a_program(self):
        # Значок берётся из файла программы: у ссылки и приложения Магазина
        # такого файла нет, и предлагать им нечего.
        блок = _тело(self.js, "прогПрименитьЗначок")
        self.assertIn("к.dataset.источник === 'exe' && вид !== 'app'", блок)

    def test_colour_reaches_the_draft_and_the_phone(self):
        # `<input type="color">` → `color` у программы.
        блок = _тело(self.js, "прогПостроитьСтроку")
        self.assertIn("цвет.type = 'color'", блок)
        сбор = _тело(self.js, "прогСобратьСтроку")
        self.assertIn("п.color = цвет", сбор)
        # И едет на экран сразу, а не после «Сохранить».
        self.assertIn("цвет.addEventListener('input'", блок)
        # Сброс цвета переживает следующую же правку соседнего поля.
        self.assertIn("dataset.сброшен", сбор)

    def test_the_icon_tiles_look_like_the_phone_page(self):
        # Плитки 44×44, выбранная обведена акцентом.
        self.assertIn("width: 44px;", _правило(".прог-значок {"))
        self.assertIn("var(--акцент)", _правило(".прог-значок.выбран {"))


class ЭкранДанныеТесты(unittest.TestCase):
    """Значки для экрана — те же, что положили бы на телефон."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_the_screen_takes_icons_from_the_preview_endpoint(self):
        блок = _тело(self.js, "прогОбновитьПревью")
        self.assertIn("fetch('/api/apps/preview'", блок)
        # Картинки — в карту, а не в черновик: `прогГотовое` их не шлёт.
        self.assertIn("ctx.картинки", блок)
        self.assertIn("прогОбновитьЭкран(ctx)", блок)

    def test_the_order_on_the_phone_is_the_draft_order(self):
        self.assertIn("прогПриложения.map", _тело(self.js, "прогПозиции"))


class НижниеКнопкиТесты(unittest.TestCase):
    """Правка нижних кнопок телефона: панель, черновик и макет.

    Хозяин 28.09 захотел менять их на свои («сцена в OBS», «заглушить себя» в
    Discord). Проверяем по исходнику `pult.js`: вкладку «Программы» вживую
    никто не откроет, а опечатка в имени валит страницу целиком
    (coordination/ГРАБЛИ.md).
    """

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.page = (config.ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_there_are_exactly_four_cells_and_they_are_drafts(self):
        self.assertIn("const ПРОГ_КНОПКИ_УМОЛЧАНИЕ", self.js)
        блок = self.js.split("const ПРОГ_КНОПКИ_УМОЛЧАНИЕ")[1].split("];")[0]
        for нужный in ("screenshot", "moment", "search", "note_start"):
            self.assertIn(нужный, блок, нужный)
        # Макет смотрит на черновик, сервер — на сохранённое.
        self.assertIn("прогКнопкиЧерновик", _тело(self.js, "прогВидКнопок"))
        self.assertIn("прогКнопкиСохранённые", _тело(self.js, "прогЕстьЧерновикКнопок"))

    def test_the_screen_reports_a_touch_of_a_bottom_button(self):
        self.assertIn("truba-edit-action", self.js)
        # Касание в макете выбирает кнопку, а не запускает её.
        self.assertIn("прогВыбратьКнопку(ctx, Number(msg.slot))", self.js)
        self.assertIn("type: 'truba-edit-action'", self.page)
        self.assertIn("editPickAction(msg.action)", self.page)

    def test_the_panel_is_named_and_offers_every_kind(self):
        # Название панели — в шапке правой колонки, а не дважды: выбрана
        # кнопка — значит шапка «Свойства программы» сменилась.
        шапка = _тело(self.js, "прогПерерисовать")
        self.assertIn("'Кнопка внизу '", шапка)
        self.assertIn("'Свойства программы'", шапка)
        for вид in ("Снимок экрана", "Момент (повтор NVIDIA)", "Найти голосом",
                    "Заметка", "Сочетание клавиш", "Открыть программу",
                    "Пункт подменю программы", "Пусто"):
            self.assertIn(вид, self.js, вид)
        блок = _тело(self.js, "прогПостроитьКнопку")
        # Программа выбирается списком, а не строкой в поле.
        self.assertIn("ПРОГ_КНОПКИ_ВИДЫ.some", блок)

    def test_the_hotkey_field_catches_the_keys_itself(self):
        # Тот же ввод с захватом нажатия, что у пункта подменю: второй такой
        # ввод был бы двумя правдами об одном и том же.
        блок = _тело(self.js, "прогДописатьПоляКнопки")
        self.assertIn("прогСочетаниеИзСобытия", блок)
        self.assertIn("Сочетание поймано", блок)
        self.assertIn("data-кнопка-поле", блок)

    def test_the_menu_item_is_offered_only_where_it_exists(self):
        блок = _тело(self.js, "прогДописатьПоляКнопки")
        self.assertIn("прогПрограммыСМеню()", блок)
        self.assertIn("— у программы нет подменю —", блок)
        # Ключ пункта — как на сервере и в подменю на телефоне.
        self.assertIn("'hotkey:'", блок)

    def test_the_button_can_be_put_back_as_it_was(self):
        блок = _тело(self.js, "прогДописатьПоляКнопки")
        self.assertIn("Вернуть как было", блок)
        self.assertIn("ПРОГ_КНОПКИ_УМОЛЧАНИЕ[слот]", блок)

    def test_the_hint_names_the_examples_of_the_owner(self):
        блок = _тело(self.js, "прогДописатьПоляКнопки")
        # Примеры («Сочетание клавиш») названы прямо: без них поле
        # звучит абстрактно.
        self.assertIn("сцена в OBS", блок)
        self.assertIn("Микрофон", блок)

    def test_saving_sends_phone_actions_and_only_when_changed(self):
        блок = _тело(self.js, "прогСохранить")
        self.assertIn("phone_actions", блок)
        self.assertIn("прогЕстьЧерновикКнопок()", блок)
        self.assertIn("/api/settings", блок)

    def test_the_draft_mark_lights_up_for_the_buttons_too(self):
        блок = _тело(self.js, "прогЕстьЧерновик")
        self.assertIn("прогЕстьЧерновикКнопок()", блок)


class СтарыйСписокТесты(unittest.TestCase):
    """Длинного списка форм больше нет."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.css = PULT_CSS.read_text(encoding="utf-8")

    def test_the_old_form_list_is_not_built_anymore(self):
        self.assertNotIn("прог-список", self.js)
        # И его стилей тоже: мёртвые правила в таблице путают следующего.
        self.assertNotIn(".прог-список", self.css)

    def test_the_schematic_mockup_gave_way_to_the_real_phone(self):
        self.assertNotIn("прог-макет", self.css)
        self.assertNotIn("прог-проекция", self.css)
        # Правая колонка — свойства одной программы, а не всех сразу.
        блок = _тело(self.js, "прогПерерисовать")
        self.assertIn("прогПриложения[ctx.индекс]", блок)
        self.assertIn("прог-пусто", блок)

    def test_the_hint_says_where_to_start(self):
        # Ничего не выбрано — подсказка с двумя способами начать.
        блок = _тело(self.js, "прогПерерисовать")
        self.assertIn("Нажми значок на экране телефона", блок)


class ОформлениеТесты(unittest.TestCase):
    """Стили новых классов: без рамки внутри рамки и в две колонки."""

    @classmethod
    def setUpClass(cls):
        cls.css = PULT_CSS.read_text(encoding="utf-8")
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_two_columns_collapse_on_a_narrow_window(self):
        блок = _правило(".прог-две {")
        self.assertIn("minmax(0, 1.35fr) minmax(300px, 1fr)", блок)
        self.assertIn("gap: 20px", блок)
        self.assertIn("@media (max-width: 1100px)", self.css)

    def test_the_phone_body_is_dark_and_rounded(self):
        корпус = _правило(".прог-телефон-корпус {")
        self.assertIn("#0b0c10", корпус)
        self.assertIn("border-radius: 28px", корпус)
        self.assertIn("padding: 12px", корпус)
        self.assertIn("box-shadow", корпус)
        # Экран срезан по скруглению, у iframe своей рамки нет.
        self.assertIn("overflow: hidden", _правило(".прог-телефон-рамка {"))
        self.assertIn("border: 0;", _правило(".прог-телефон-экран {"))

    def test_every_new_class_has_a_style(self):
        # Класс без стиля — это «сработало, но выглядит как в соседней
        # вкладке», а хозяин такое видит сразу.
        used = set()
        for мат in re.finditer(r"'(прог-[^']*)'", self.js):
            used |= set(мат.group(1).split(" "))
        self.assertTrue(used)
        missing = sorted(имя for имя in used if ("." + имя) not in self.css)
        self.assertEqual(missing, [], "у классов нет стилей: %s" % ", ".join(missing))

    def test_the_toggles_look_like_the_ones_next_door(self):
        # Тот же вид и то же active-состояние, что у фильтров журнала, а не
        # выдуманный третий стиль переключателей.
        self.assertIn("border-radius: 999px;", _правило(".прог-переключатели button {"))
        self.assertIn("var(--акцент)", _правило(".прог-переключатели button.активный {"))

    def test_the_line_endings_are_as_they_should_be(self):
        # `pult.css` — LF (стили отдаются как есть), `pult.js` — CRLF.
        self.assertNotIn(b"\r\n", PULT_CSS.read_bytes())
        self.assertIn(b"\r\n", PULT_JS.read_bytes())


if __name__ == "__main__":
    unittest.main()
