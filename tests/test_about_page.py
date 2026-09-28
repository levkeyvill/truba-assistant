"""Раздел «О программе» в настройках пульта: версия и обновления.

Проверяем `pult.js` текстом, как в `test_settings_sections.py`: раздел
должен быть последним и в списке разделов, и в подменю, галочка
`update_check` — такая же обычная галочка, как `voice_autostart`, а данные
с GitHub (заметки к выпуску, названия, ошибки) не должны попадать в
разметку: только `textContent`.

Живой пульт не трогаем: ни «Обновить», ни «Перезапустить» здесь не
нажимается — это перезапуск настоящего окна.
"""

import re
import unittest
from pathlib import Path

import config

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
PULT_CSS = Path(config.ROOT) / "ui" / "web" / "pult.css"
PULT_HTML = Path(config.ROOT) / "ui" / "web" / "pult.html"


class РазделОПрограммеTests(unittest.TestCase):
    """Порядок разделов, обращения к серверу и тексты для хозяина."""

    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")
        cls.css = PULT_CSS.read_text(encoding="utf-8")
        cls.html = PULT_HTML.read_text(encoding="utf-8")

    def _блок(self, начало, конец="\nfunction "):
        кусок = self.js.split(начало)
        self.assertEqual(len(кусок), 2, f"не нашлось начало: {начало}")
        return кусок[1].split(конец)[0]

    def _форма(self):
        """Кусок построения страницы «О программе» — от корня до конца."""
        return self.js.split("function нарисоватьОПрограмме()")[1].split(
            "async function сохранитьПроверкуОбновлений")[0]

    def test_it_is_a_separate_menu_item_not_a_settings_section(self):
        # Хозяин (28.09): «О программе надо выставить отдельным пунктом, без
        # вкладки „Настройки“». Значит: свой пункт меню после «Команд» и
        # никакой секции в общей форме.
        разделы = self.js.split("const НАСТР_РАЗДЕЛЫ = [")[1].split("];")[0]
        self.assertNotIn("'программа'", разделы)
        страница = self.js.split("настройки: [")[1].split("]")[0]
        self.assertNotIn("'программа'", страница)
        self.assertNotIn("настрСекция(содержимое, 'программа'", self.js)
        кнопки = self.html.split('id="подменю-настроек"')[1].split("</div>")[0]
        self.assertNotIn('data-настройка="программа"', кнопки)
        # Пункт меню — после «Команд», значок кружок с «i».
        пункты = [часть.split('"')[0] for часть in self.html.split('data-раздел="')[1:]]
        self.assertEqual(пункты[-1], "программа")
        self.assertLess(пункты.index("команды"), пункты.index("программа"))
        self.assertIn("<span>О программе</span>", self.html)

    def test_the_page_is_built_like_its_neighbours(self):
        # Своя функция страницы и свой набор узлов, а не секция формы.
        self.assertIn("function нарисоватьОПрограмме()", self.js)
        self.assertIn("корень.className = 'обн-страница'", self.js)
        self.assertIn("else if (имя === 'программа') нарисоватьОПрограмме();", self.js)
        # Галочка «Проверять обновления при запуске» — тут же, и шлётся сразу.
        self.assertIn("обнГалочка.addEventListener('change', сохранитьПроверкуОбновлений)",
                      self.js)
        self.assertIn("body: JSON.stringify({ update_check: хотим })", self.js)
        # Из общей формы она убрана: иначе форма упала бы на `эл.update_check`.
        self.assertNotIn("update_check: эл.update_check.checked,", self.js)
        self.assertNotIn("эл.update_check.checked =", self.js)
        # Кнопки «Поддержать автора» на странице больше нет.
        self.assertNotIn("обнПоддержать", self.js)

    def test_the_text_about_the_program_says_what_it_does(self):
        # Хозяин просил «немного написать, что программа делает и как себя
        # ведёт». Абзацы константой, разметка — только `textContent`.
        for кусок in ("Труба — голосовой помощник для Windows",
                      "Что умеет:", "Как себя ведёт:", "Что уходит в интернет:"):
            self.assertIn(кусок, self.js)
        блок = self.js.split("const ТЕКСТ_О_ПРОГРАММЕ = [")[1].split("];")[0]
        self.assertGreaterEqual(блок.count("  '"), 4)
        форма = self._форма()
        self.assertIn("for (const абзац of ТЕКСТ_О_ПРОГРАММЕ)", форма)
        self.assertIn("строка.textContent = абзац", форма)
        # И кнопка «Пройти первую настройку заново» — без `first_run_done`.
        self.assertIn("'Пройти первую настройку заново'", форма)
        self.assertIn("мастерЕщё.addEventListener('click', () => мастерОткрыть())", форма)
        перезапуск = self._блок("async function мастерОткрыть")
        self.assertNotIn("first_run_done", перезапуск)

    def test_all_five_endpoints_are_used(self):
        for адрес in ("/api/about", "/api/update/check", "/api/update/install",
                      "/api/update/status", "/api/update/restart"):
            self.assertIn(f"'{адрес}'", self.js)

    def test_the_tick_is_saved_on_its_own_page(self):
        # Галочка уехала с общей формы, поэтому шлётся сразу при переключении
        # и возвращается назад, если сервер не принял.
        self.assertIn("эл.обнГалочка.checked = данные.update_check !== false;", self.js)
        сохранение = self._блок("async function сохранитьПроверкуОбновлений")
        self.assertIn("'/api/settings'", сохранение)
        self.assertIn("body: JSON.stringify({ update_check: хотим })", сохранение)
        self.assertIn("эл.обнГалочка.checked = !хотим;", сохранение)

    def test_the_words_of_every_state_are_there(self):
        for текст in ("ещё не опубликован", "последняя версия",
                      "версия новее опубликованной", "Обновить до",
                      "Перезапустить пульт", "Вернула прежнюю версию"):
            self.assertIn(текст, self.js)
        # Состояния с сервера переводятся в слова в одном месте, а не
        # показываются хозяину как есть.
        слова = self._блок("function обнСловаПроверки")
        for состояние in ("'newer'", "'latest'", "'ahead'", "'no_repo'", "'error'"):
            self.assertIn(состояние, слова)

    def test_the_release_notes_go_in_as_text_only(self):
        # Заметки с GitHub — единственные данные, которых в пульте раньше
        # не было. Разметка оттуда не подставляется: только `textContent`.
        рисование = self._блок("function обнНарисовать(")
        self.assertIn("эл.обнЗаметки.textContent = обнПроверка.notes", рисование)
        self.assertIn("эл.обнНазвание.textContent = обнПроверка.title", рисование)
        self.assertNotIn("innerHTML", рисование)
        форма = self._форма()
        # `лист.innerHTML = ''` — очистка листа, а не разметка с сервера.
        self.assertNotIn("innerHTML = '<", форма)
        self.assertIn("обнЗаметки.className = 'обн-заметки'", форма)
        # Заметки длинные: своя прокрутка и перенос строк как в источнике.
        self.assertIn("white-space: pre-wrap", self.css)
        self.assertIn("max-height: 220px", self.css)

    def test_the_update_and_restart_buttons_are_shown_only_when_needed(self):
        итог = self._блок("function обнНарисоватьИтог")
        self.assertIn("эл.обнПерезапуск.hidden = false;", итог)
        # Неудача: «Вернула прежнюю версию» — только если сервер так сказал.
        self.assertIn("if (обнИтог.rolled_back)", итог)
        self.assertIn("текст += ' — Вернула прежнюю версию'", итог)
        # Отказ сервера (409) показывается его же словами, а не своими.
        установка = self._блок("async function поставитьОбновление", "\nfunction ")
        self.assertIn("данные.error", установка)
        self.assertIn("'/api/update/install'", установка)
        # Шаги опрашиваются, пока идёт установка, и раз в секунду.
        self.assertIn("setInterval(опроситьУстановку, 1000)",
                      self._блок("function обнНачатьОпрос"))
        self.assertIn("if (!данные.running)",
                      self._блок("async function опроситьУстановку"))
        # После перезапуска пульт закроется сам — говорим об этом словами.
        self.assertIn("'Перезапускаю…'",
                      self._блок("async function перезапуститьПульт"))

    def test_the_page_asks_the_server_when_it_opens(self):
        # Открытие страницы берёт `/api/about`; результат проверки при запуске
        # показывается сразу, второй запрос за ним не посылается.
        рисование = self._форма()
        self.assertIn("загрузитьПрограмму();", рисование)
        загрузка = self._блок("async function загрузитьПрограмму", "\nasync function")
        self.assertIn("'/api/about'", загрузка)
        self.assertIn("известное.last", загрузка)
        self.assertNotIn("'/api/update/check'", загрузка)
        # Функции страницы работают со своим набором узлов, а не с формой.
        for функция in ("загрузитьПрограмму", "проверитьОбновления",
                        "поставитьОбновление", "опроситьУстановку", "перезапуститьПульт"):
            блок = self._блок("async function " + функция, "\nasync function")
            self.assertIn("обнЭлементы", блок, функция)

    def test_the_update_dot_stands_next_to_the_about_item(self):
        # Точка переехала вместе с разделом: у пункта «О программе» в меню.
        значок = self._блок("function обнПоказатьЗначок")
        self.assertIn('[data-раздел="программа"]', значок)
        self.assertIn("'с-обновлением'", значок)
        self.assertNotIn('[data-раздел="настройки"]', значок)
        self.assertNotIn('data-настройка="программа"', значок)
        # Проверяется при загрузке пульта и после «Проверить обновления».
        self.assertIn("проверитьЗначокПриЗапуске();", self.js)
        self.assertIn("обнПоказатьЗначок(данные.state === 'newer');", self.js)
        # Точка янтарная — как акцент: обновление это приглашение, не авария.
        self.assertIn(".пункт.с-обновлением::after", self.css)
        self.assertIn("background: var(--акцент);", self.css)
        # Класс в разметке и в таблице стилей — один и тот же: с «м» на
        # конце. Без «м» точка не появилась бы, а тесты бы это не видели.
        for правило in set(re.findall(r"с-обновлен\w*", self.css)):
            self.assertEqual(правило, "с-обновлением",
                             f"в стилях другое написание: {правило}")

    def test_the_header_has_no_support_button(self):
        # Хозяин (28.09): «Поддержать автора» там не надо — навязчиво.
        # Сердечко внизу меню осталось, кнопки на странице — нет.
        форма = self._форма()
        self.assertIn("обнИмя.textContent = 'Труба'", форма)
        self.assertIn("'версия …'", форма)
        self.assertIn("'автор — …'", форма)
        self.assertNotIn("Поддержать автора", форма)
        # Версия и автор приходят из `/api/about`, а не написаны в коде.
        рисование = self._блок("function обнНарисовать(")
        self.assertIn("данные.version", рисование)
        self.assertIn("данные.author", рисование)

    def test_the_check_button_lies_beside_the_status(self):
        # Не стопкой и не в общей кнопке «Сохранить», а рядом со строкой
        # состояния — как «Проверить поиск» и «Проверить связь».
        блок = self.js.split("const обнРяд = document.createElement('div')")[1].split(
            "программа.appendChild(обнРяд)")[0]
        self.assertIn("обнСтатус", блок)
        self.assertIn("обнПроверитьКнопка", блок)
        self.assertIn("обнРяд.append(обнСтатус, обнПроверитьКнопка)", блок)
        self.assertIn("'Проверить обновления'", блок)


if __name__ == "__main__":
    unittest.main()
