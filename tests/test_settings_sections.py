"""Порядок в настройках: отдельные разделы «Поиск» и «Сама заговаривает».

Три вещи, которые легко разъехаться:
  - частота захода первой запоминается сама (`proactive_last`), чтобы Панели
    было к чему возвращать галочку, и не берётся из запроса;
  - `/state` отдаёт блок «возможности» для двух переключателей Панели;
  - в пульте восемь кнопок разделов настроек (последняя — «О программе»),
    а «Поиск» и «Первая» стоят именно между «Ответами» и «Характером».

Пути к данным — во временную папку, как в остальном прогоне
(tests/test_data_guard.py); живой settings.json хозяина тест не трогает.
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
from core import proactive, settings, state
from ui.web_runtime import WebRuntime

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
PULT_HTML = Path(config.ROOT) / "ui" / "web" / "pult.html"


def _runtime() -> WebRuntime:
    """Пульт без живого окружения: только замки и заглушки вместо голоса."""
    runtime = object.__new__(WebRuntime)
    runtime._provider_test_lock = threading.Lock()
    runtime._lock = threading.Lock()
    runtime.server = mock.Mock()
    runtime.brain = None
    runtime.voice = NS(_brain=None, _close_conversation=lambda: None, running=False)
    runtime._enroll = None
    runtime._jobs = {}
    runtime._audio_stale = False
    runtime._remember = lambda kind, payload: None
    return runtime


class ПоследняяЧастотаTests(unittest.TestCase):
    """`proactive_last` пишет сам сервер, из значения `proactive`."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-sections-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        # `apply_to_config` переносит в config и папку заметок — возвращаем.
        заметки = config.NOTES_DIR
        self.addCleanup(setattr, config, "NOTES_DIR", заметки)
        self._было = (config.PROACTIVE, config.PROACTIVE_LAST)
        self.addCleanup(self._вернуть)
        self.runtime = _runtime()

    def _вернуть(self):
        config.PROACTIVE, config.PROACTIVE_LAST = self._было

    def _сохранено(self):
        return json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))

    def test_a_frequency_is_remembered_as_the_last_one(self):
        ответ = self.runtime.save_settings({"proactive": "rare"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self._сохранено()["proactive_last"], "rare")
        # И в config сразу, без перезапуска: Панель перерисовывается сама.
        self.assertEqual(config.PROACTIVE_LAST, "rare")

    def test_never_leaves_the_last_frequency_alone(self):
        # Выключил — значит включать обратно есть к чему: к «часто».
        self.runtime.save_settings({"proactive": "often"})
        self.runtime.save_settings({"proactive": "never"})
        self.assertEqual(self._сохранено()["proactive_last"], "often")
        self.assertEqual(config.PROACTIVE_LAST, "often")

    def test_it_is_not_taken_from_the_request(self):
        # Хозяин этого поля не видит, значит и подсунуть его нечем.
        self.runtime.save_settings({"proactive": "rare"})
        self.runtime.save_settings({"proactive_last": "often"})
        self.assertEqual(self._сохранено()["proactive_last"], "rare")

    def test_never_is_never_stored_as_the_last_one(self):
        # На чистом месте включили и выключили: включать обратно не к чему,
        # поэтому apply_to_config оставляет промежуточный вариант.
        settings.save_settings({"proactive": "never", "proactive_last": "never"})
        settings.apply_to_config()
        self.assertEqual(config.PROACTIVE_LAST, "sometimes")

    def test_junk_by_hand_falls_back_to_a_usable_frequency(self):
        settings.SETTINGS_PATH.write_text(
            '{"proactive": "иногда", "proactive_last": "каждый час"}',
            encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.PROACTIVE_LAST, "sometimes")
        self.assertIn(config.PROACTIVE_LAST, proactive.FREQUENCIES)


class ВозможностиВСостоянииTests(unittest.TestCase):
    """Панели нужен блок «возможности» из /state."""

    def setUp(self):
        self._было = (config.WEB_SEARCH, config.WEB_SEARCH_MODE,
                      config.PROACTIVE, config.PROACTIVE_LAST)
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        (config.WEB_SEARCH, config.WEB_SEARCH_MODE,
         config.PROACTIVE, config.PROACTIVE_LAST) = self._было

    def test_the_block_has_all_four_keys(self):
        срез = state.everything()
        self.assertIn("возможности", срез)
        self.assertEqual(set(срез["возможности"]),
                         {"поиск", "поиск_где", "первой", "первой_последняя"})

    def test_it_mirrors_the_settings(self):
        config.WEB_SEARCH = True
        config.WEB_SEARCH_MODE = "auto"
        config.PROACTIVE = "often"
        config.PROACTIVE_LAST = "rare"
        блок = state.everything()["возможности"]
        self.assertIs(блок["поиск"], True)
        self.assertEqual(блок["поиск_где"], "auto")
        self.assertEqual(блок["первой"], "often")
        self.assertEqual(блок["первой_последняя"], "rare")

    def test_it_does_not_break_the_rest_of_the_slice(self):
        # Пульт спрашивает /state каждые две секунды: лишний ключ не должен
        # ни ронять срез, ни мешать карточкам наверху.
        срез = state.everything()
        for имя in ("слух", "голос", "мозг", "телефон", "память", "железо", "повтор"):
            self.assertIn(имя, срез)


class РазделыВПультеTests(unittest.TestCase):
    """Порядок кнопок разделов и новых секций формы."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.html = PULT_HTML.read_text(encoding="utf-8")

    def test_seven_buttons_in_the_menu(self):
        # «О программе» — отдельный пункт, поэтому в настройках восемь кнопок:
        # семь прежних и «Главная» (вид фигуры из точек).
        кнопки = self.html.split('id="подменю-настроек"')[1].split("</div>")[0]
        self.assertEqual(кнопки.count("data-настройка="), 8)
        for раздел in ("ответы", "поиск", "первой", "характер",
                       "память", "телефон", "главная", "система"):
            self.assertIn(f'data-настройка="{раздел}"', кнопки)
        # Порядок кнопок совпадает с порядком страницы настроек.
        порядок = [часть.split('"')[0] for часть in
                   self.html.split('data-настройка="')[1:]]
        self.assertEqual(порядок,
                         ["ответы", "поиск", "первой", "характер",
                          "память", "телефон", "главная", "система"])
        # «О программе» — отдельный пункт меню, а не раздел формы.
        self.assertIn('data-раздел="программа"', self.html)


    def test_the_two_sections_are_pages_of_the_settings(self):
        начало = self.js.split("настройки: [")[1].split("]")[0]
        for раздел in ("'ответы'", "'поиск'", "'первой'", "'характер'"):
            self.assertIn(раздел, начало)
        # Между «Ответами» и «Характером» — именно эти два.
        self.assertLess(начало.index("'ответы'"), начало.index("'поиск'"))
        self.assertLess(начало.index("'поиск'"), начало.index("'первой'"))
        self.assertLess(начало.index("'первой'"), начало.index("'характер'"))

    def test_both_sections_are_in_the_form_and_built_in_order(self):
        # Секции скрываются по data-section-id, поэтому в форме они должны
        # быть все — и «Сохранить» по-прежнему шлёт полный набор полей.
        for раздел in ("поиск", "первой"):
            self.assertIn(f"настрСекция(содержимое, '{раздел}'", self.js)
        массив = self.js.split("секции: [")[1].split("]")[0]
        for раздел in ("мозг", "поиск", "первая", "характер"):
            self.assertIn(раздел, массив)
        # Порядок в массиве совпадает с порядком создания секций.
        self.assertLess(массив.index("мозг"), массив.index("поиск"))
        self.assertLess(массив.index("поиск"), массив.index("первая"))
        self.assertLess(массив.index("первая"), массив.index("характер"))

    def test_the_old_stacking_of_rows_is_gone(self):
        # Ряды полей называются `.настр-ряд`; несуществующий класс в
        # `closest` однажды обнулил всю форму.
        self.assertNotIn("'.настр-строка'", self.js)
        self.assertNotIn("поискРяд", self.js)
        self.assertNotIn("поискСтатус", self.js)

    def test_check_buttons_live_next_to_save(self):
        низ = self.js.split("низ.className = 'настр-низ'")[1].split("корень.appendChild(низ)")[0]
        for кнопка in ("проверитьСвязьКнопка", "проверитьПоискКнопка",
                       "послушатьПробуКнопка"):
            self.assertIn(кнопка, низ)
        # Порядок в строке: [Сохранить] [проверка] [статус].
        self.assertLess(низ.index("сохранить.textContent"),
                        низ.index("проверитьСвязьКнопка"))
        self.assertLess(низ.index("послушатьПробуКнопка"), низ.index("статус"))

    def test_check_buttons_appear_only_in_their_own_section(self):
        показать = self.js.split("function настрПоказатьРаздел")[1].split("\n}")[0]
        self.assertIn("проверитьСвязь.hidden = выбран !== 'ответы'", показать)
        self.assertIn("проверитьПоиск.hidden = выбран !== 'поиск'", показать)
        self.assertIn("послушатьПробу.hidden = выбран !== 'голос'", показать)

    def test_the_search_check_needs_the_tick(self):
        # Проверять нечего, пока искать выключено.
        поля = self.js.split("function настрПоляПоиска")[1].split("\n}")[0]
        self.assertIn("проверитьПоиск.disabled = !вкл", поля)

    def test_proactive_texts_come_from_one_list(self):
        # Частоты живут в одной константе: иначе подпись на Панели и список
        # в настройках разъедутся.
        self.assertIn("настрВыбор(ЧАСТОТЫ_ПЕРВОЙ", self.js)
        self.assertIn("частотаКоротко(частота)", self.js)


class КлючИПанельTests(unittest.TestCase):
    """Ключ доступа звёздочками и два переключателя на Панели."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.html = PULT_HTML.read_text(encoding="utf-8")

    def test_the_key_is_shown_with_dots_and_not_as_a_placeholder(self):
        # Никакого «пусто = не менять»: хозяин должен видеть, что ключ стоит.
        self.assertNotIn("пусто = не менять", self.js)
        self.assertIn("'Вставь ключ API'", self.js)
        self.assertIn("Ключ сохранён", self.js)
        self.assertIn("Ключа нет — вставь и сохрани", self.js)
        # Подстановка не должна считаться «введённым»: иначе «Сохранить»
        # отправил бы сохранённый ключ заново.
        подстановка = self.js.split("async function настрПоказатьКлюч")[1].split("function настрПодсказкаКлюча")[0]
        self.assertIn("/api/settings/key/reveal", подстановка)
        self.assertIn("сохранённыеКлючи", подстановка)
        self.assertNotIn("ключиВПравке[имя] =", подстановка)

    def test_the_block_is_built_from_state(self):
        блок = self.js.split("function блокВозможностей")[1].split("\nasync function")[0]
        self.assertIn("шапкаБлока('Возможности')", блок)
        # Данные — из того же /state, что и «Запись игры», через помощника,
        # который переживает первую отрисовку, пока срез ещё не приехал.
        self.assertIn("последние.возможности", self.js)
        self.assertIn("возможностиСейчас()", блок)
        for раздел in ("'поиск'", "'первой'"):
            self.assertIn(раздел, блок)

    def test_each_tick_sends_one_field(self):
        строка = self.js.split("function строкаВозможности")[1].split("\nfunction ")[0]
        self.assertIn("{ web_search: хочу }", self.js)
        self.assertIn("{ proactive: хочу ? последняя : 'never' }", self.js)
        # Как у `строкаПовтора`: при ошибке галочка возвращается на место.
        self.assertIn("галочка.checked = !хочу", строка)
        self.assertIn("/api/settings", строка)
        # В срез кладётся то, что вернёт сервер, а не галочка: «первой» —
        # частота строкой, иначе подпись на мгновение останется пустой.
        self.assertIn("было[название] = хочу ? вкл : выкл", строка)
        self.assertIn("было[название] = прежнее", строка)

    def test_where_to_search_is_spoken_plainly(self):
        for слово in ("бесплатно", "сначала бесплатно", "платно"):
            self.assertIn(f"'{слово}'", self.js)

    def test_transitions_from_the_blocks(self):
        # Строка голоса и шапка «Расхода» ведут по кнопке, как «Вся история →».
        self.assertIn("открытьГолосПодраздел('голос')", self.js)
        self.assertIn("открытьНастройкиПодраздел('ответы')", self.js)
        self.assertIn("'Настроить →'", self.js)

    def test_the_connection_lamp_moved_to_the_header(self):
        # Огонёк связи стоит в шапке рядом с часами, «Поддержать» — внизу один.
        self.assertIn('<div class="шапка-справа">', self.html)
        низ = self.html.split('<div class="низ">')[1].split("</nav>")[0]
        self.assertNotIn("связь", низ)
        self.assertEqual(низ.count('id="поддержать"'), 1)

    def test_support_logos_are_squares_with_titles(self):
        # Логотипы — константа в коде, а не разметка из ответа сервера.
        лого = self.js.split("const ЛОГОТИПЫ")[1].split("};")[0]
        self.assertIn("229ED9", лого)
        self.assertIn("FF0000", лого)
        рисование = self.js.split("async function нарисоватьПоддержку")[1]
        self.assertIn("кнопка.innerHTML = ЛОГОТИПЫ[что]", рисование)
        # Подпись уходит в подсказку, а не в текст кнопки.
        self.assertIn("кнопка.setAttribute('aria-label', подпись)", рисование)
        self.assertIn("кнопка.textContent = подпись", рисование)


if __name__ == "__main__":
    unittest.main()
