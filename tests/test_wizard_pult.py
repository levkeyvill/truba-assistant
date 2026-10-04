"""Мастер первого запуска в пульте: слой `#мастер`, шесть шагов, маршруты.

Тесты пульт не исполняют (см. test_pult_sound_page.py), поэтому здесь и строки
в `pult.js`, и грабли, которые уже случались: класс с префиксом `мастер-`,
`Number()` вместо общей `число()` на строке из поля, кириллица в именах.
"""

import re
import unittest
from pathlib import Path

import config

PULT = Path(config.ROOT) / "ui" / "web"


class МастерПультаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (PULT / "pult.js").read_text(encoding="utf-8")
        cls.html = (PULT / "pult.html").read_text(encoding="utf-8")
        cls.css = (PULT / "pult.css").read_text(encoding="utf-8")

    def test_the_layer_is_in_the_markup_and_the_script(self):
        self.assertIn('id="мастер"', self.html)
        self.assertIn("$('мастер-дальше')", self.js)
        self.assertIn("мастерНарисоватьШаг()", self.js)
        # Слой поверх всего окна, карточка по центру.
        self.assertIn("#мастер", self.css)
        self.assertIn(".мастер {", self.css)
        self.assertIn("position: fixed; inset: 0;", self.css)

    def test_it_starts_only_for_a_fresh_install(self):
        # У хозяина `first_run_done` уже true, и мастера он не видит никогда.
        self.assertIn("first_run_done", self.js)
        # Мастер открывается на сохранённом шаге, а не всегда с первого.
        self.assertIn("if (s.first_run_done === false) {", self.js)
        self.assertIn("Number(s.wizard_step)", self.js)
        self.assertNotIn("if (s.first_run_done === false) мастерОткрыть(1);", self.js)
        self.assertIn("спроситьПервыйЗапуск();", self.js)
        # С «Начать» и «Пропустить настройку» флаг тоже ставится.
        self.assertIn("мастерСохранить({ first_run_done: true })", self.js)

    def test_the_markers_of_the_first_run(self):
        for адрес in ("/api/open/key-page", "/api/phone/qr", "/api/audio",
                      "/api/hardware", "/api/weather/find", "/api/personas",
                      "/api/voice/toggle", "/api/settings/test-provider"):
            self.assertIn(адрес, self.js)

    def test_all_six_steps_are_drawn(self):
        таблица = self.js.split("const МАСТЕР_РИСОВАТЬ = {")[1].split("};")[0]
        for шаг in range(1, 7):
            self.assertIn(f"{шаг}: мастерШаг", таблица)
        self.assertIn("const МАСТЕР_ШАГОВ = 6;", self.js)
        self.assertIn("'Шаг ' + мастерШаг + ' из ' + МАСТЕР_ШАГОВ", self.js)

    def test_sound_and_weather_are_reused_not_copied(self):
        # Мастер зовёт те же функции, что и «Звук»/«Телефон», со своей
        # проверкой «жив» вместо формы настроек. Полоска уровня — исключение:
        # на шаге 4 она смотрит на **выбранный** микрофон, а не на тот, который
        # держит голос, поэтому у неё свой опрос (`мастерУровень*`).
        for кусок in ("await загрузитьЗвук(эл, мастерЖива);",
                      "звукЗаписать(эл, мастерЖива, '/api/audio/test?probe=1')",
                      "прослушатьЗапись(эл, мастерЖива)",
                      "найтиГород(эл, мастерЖива)",
                      "звукПоказатьВход(эл, мастерЖива)",
                      "звукУровеньПоказать(эл, level, тихо, мастерЖива)"):
            self.assertIn(кусок, self.js)
        self.assertIn("мастерУровеньВключить(эл)", self.js)
        self.assertNotIn("звукУровеньВключить(эл, мастерЖива)", self.js)
        # Вторых копий этих функций быть не должно: мастер и форма настроек
        # обязаны проверять звук одними и теми же правилами.
        for функция in ("function звукЗаполнить(", "async function загрузитьЗвук(",
                        "async function найтиГород(", "function погодаВыбрать(",
                        "async function прослушатьЗапись(",
                        "function звукЗаписьПоказать("):
            self.assertEqual(self.js.count(функция), 1, функция)

    def test_the_wizard_does_not_reuse_the_settings_form_barely(self):
        # Форма после переноса «О программе» не падает: поля `update_check`
        # в ней больше нет, и собирать его негде.
        self.assertNotIn("эл.update_check", self.js)
        self.assertNotIn("update_check: эл.update_check.checked", self.js)

    def test_numbers_from_the_form_are_read_with_number(self):
        # Общая `число()` берёт только настоящие числа: на «1» из select
        # отвечает пустотой, и «Вход 2» тихо стал бы «Входом 1».
        self.assertIn("звукЧисло(эл.mic_channel.value)", self.js)
        self.assertNotIn("число(эл.mic_channel.value)", self.js)

    def test_no_latin_letter_inside_a_cyrillic_arrow(self):
        # Параметр `п` кириллицей и `p.value` латиницей — ReferenceError,
        # который обнуляет страницу (см. ГРАБЛИ.md).
        for найдено in re.finditer(r"\(п\) =>\s*\n?\s*([^;]+);", self.js):
            self.assertNotRegex(найдено.group(1), r"(?<![\wа-яё])p\.")

    def test_the_wizard_classes_do_not_collide(self):
        # В прошлой задаче панель назвали `прог-кнопка`, а это уже класс
        # кнопок ↑ ↓ ✕: панель сжалась, поля вылезли за рамку. Поэтому
        # у мастера своё имя — с `мастер-`.
        for класс in ("мастер-карточка", "мастер-шапка", "мастер-полоса",
                      "мастер-тело", "мастер-низ", "мастер-пропустить"):
            self.assertIn("." + класс, self.css)
        self.assertIn("префиксом", self.css)

    def test_no_green_in_the_wizard_painting(self):
        # Зелёный в оформлении запрещён: он у «связь есть / работает».
        блок = self.css.split("/* ---------- Мастер первого запуска ----------")[1]
        self.assertNotIn("var(--живой)", блок)
        self.assertNotIn("var(--зелёный)", блок)

    def test_the_card_scrolls_and_the_buttons_stay(self):
        # Окно пульта бывает 1000×700: прокручивается карточка, а кнопки
        # «Назад» и «Дальше» остаются на виду.
        self.assertIn(".мастер-тело { flex: 1 1 auto; min-height: 0; overflow-y: auto;",
                      self.css)
        self.assertIn(".мастер-низ {", self.css)
        self.assertIn("max-height: 100%;", self.css)

    def test_escape_does_not_close_it(self):
        # Случайное нажатие Esc не должно сбрасывать шесть шагов.
        блок = self.js.split("function мастерЗакрыть(")[1].split("\n}")[0]
        self.assertNotIn("Escape", блок)
        self.assertIn("мастерПропустить", self.js)

    def test_skipping_asks_first(self):
        пропуск = self.js.split("function мастерПропустить()")[1].split("\n}")[0]
        self.assertIn("Пропустить первую настройку?", пропуск)
        self.assertIn("«Настройках» и «Голосе»", пропуск)
        self.assertIn("мастерСохранить({ first_run_done: true })", пропуск)

    def test_the_step_is_remembered_after_next_and_back(self):
        # Закрыл пульт на пятом шаге (после QR) — в следующий раз откроется на
        # нём же, а не на первом.
        self.assertIn("function мастерШагЗапомнить(шаг)", self.js)
        self.assertIn("мастерСохранить({ wizard_step: шаг })", self.js)
        дальше = self.js.split("async function мастерДальше()")[1].split("\n}\n")[0]
        self.assertIn("мастерШагЗапомнить(мастерШаг);", дальше)
        # Шаг пишется после перехода, а не до проверки: при ошибке сохранения
        # шага «Дальше» не перелистывает.
        self.assertLess(дальше.index("if (ошибка) { мастерСтатус(эл, ошибка, true); return; }"),
                        дальше.index("мастерШагЗапомнить(мастерШаг);"))
        назад = self.js.split("function мастерНазад()")[1].split("\n}\n")[0]
        self.assertIn("мастерШагЗапомнить(мастерШаг);", назад)

    def test_the_step_lives_in_settings_not_in_the_browser(self):
        # Шаг — часть данных установки, поэтому в localStorage его нет.
        for строка in re.finditer(r"localStorage[^\n]*", self.js):
            self.assertNotIn("wizard_step", строка.group(0))
        for строка in re.finditer(r"wizard_step[^\n]*", self.js):
            self.assertNotIn("localStorage", строка.group(0))

    def test_back_does_not_throw_away_what_was_typed(self):
        # Введённый ключ и город не должны пропадать от «Назад»: значения
        # живут в полях, а перерисовка шага их не трогает.
        self.assertIn("function мастерНазад()", self.js)
        self.assertIn("мастерШаг -= 1;", self.js)


if __name__ == "__main__":
    unittest.main()
