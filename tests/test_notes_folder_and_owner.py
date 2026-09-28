"""Папка заметок — на странице «Заметки», образец голоса — в «Голос → Слух».

Обе вещи хозяин искал не там, где они лежали: папку — в «Настройки →
Система», а запись образца — на «Проверке», хотя пользуются ею «Только хозяин»
и «Строгость узнавания» рядом с ней. Проверяем, что перенос состоялся и что
форма настроек больше не упоминает `notes_dir` (иначе следующее «Сохранить»
вернуло бы папку по умолчанию и стёрло бы выбор хозяина).

Окно и микрофон не поднимаются: `pick_folder` проверяется на подставном
`_window`, а `webview` подменяется в `sys.modules` — настоящий pywebview без
окна в тестах не нужен. Папка заметок — временная, как в остальном прогоне
(tests/test_data_guard.py).
"""

import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import config
from ui.web_runtime import WebRuntime
from ui.window import PultBridge

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"


def _тело(скрипт: str, имя: str) -> str:
    """Текст функции `имя` — от строки объявления до следующей того же уровня.

    `node --check` такую опечатку не ловит: вызов несуществующей функции или
    чужое имя переменной валят страницу целиком в тот момент, когда до них
    доходит (coordination/ГРАБЛИ.md).
    """
    начало = скрипт.find("function " + имя + "(")
    assert начало >= 0, "функция %s не найдена" % имя
    остаток = скрипт[начало:]
    новое = re.search(r"^\n(?:async )?function \w+\(", остаток[1:], re.M)
    if новое:
        return остаток[: новое.start() + 1]
    return остаток


class ВыборПапкиТесты(unittest.TestCase):
    """`pick_folder` — тот же нативный диалог, что у значка и программы."""

    def setUp(self):
        self.bridge = PultBridge(runtime=None)
        # Настоящий webview без окна не импортируется: подменяем модуль
        # целиком, с единственным нужным — `FileDialog`.
        webview = types.ModuleType("webview")
        webview.FileDialog = types.SimpleNamespace(OPEN="open", FOLDER="folder")
        подмена = mock.patch.dict(sys.modules, {"webview": webview})
        подмена.start()
        self.addCleanup(подмена.stop)
        self._состояние = {}

        def create_file_dialog(вид, **kwargs):
            self._состояние["вид"] = вид
            self._состояние["kwargs"] = kwargs
            return self._состояние["ответ"]

        self.окно = mock.Mock()
        self.окно.create_file_dialog.side_effect = create_file_dialog
        self.bridge._window = self.окно

    def test_the_chosen_folder_comes_back(self):
        self._состояние["ответ"] = ("D:\\Заметки",)
        ответ = self.bridge.pick_folder("")
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["path"], "D:\\Заметки")

    def test_it_asks_for_a_folder_not_for_a_file(self):
        self._состояние["ответ"] = ("D:\\Заметки",)
        self.bridge.pick_folder("")
        self.assertEqual(self._состояние["вид"], "folder")

    def test_cancelling_is_not_a_failure(self):
        self._состояние["ответ"] = None
        ответ = self.bridge.pick_folder("")
        self.assertFalse(ответ["ok"])
        self.assertTrue(ответ["cancelled"])

    def test_without_a_window_it_says_so(self):
        # pywebview отдаёт методы странице только после `webview.start()`,
        # а до этого окна нет — молчание тут было бы хуже отказа.
        self.bridge._window = None
        for старт in ("", "C:\\Нет\\Такой"):
            with self.subTest(старт=старт):
                ответ = self.bridge.pick_folder(старт)
                self.assertFalse(ответ["ok"])
                self.assertTrue(ответ["error"])
                self.assertNotIn("cancelled", ответ)

    def test_a_real_folder_is_the_starting_point(self):
        self._состояние["ответ"] = ("D:\\Заметки",)
        папка = Path(tempfile.mkdtemp(prefix="truba-pick-"))
        self.addCleanup(shutil.rmtree, папка, True)
        self.bridge.pick_folder(str(папка))
        self.assertEqual(self._состояние["kwargs"]["directory"], str(папка))

    def test_a_missing_or_junk_start_falls_back_to_nothing(self):
        # Несуществующая папка открыла бы диалог от корня диска, а мусор
        # вовсе заставил бы pywebview споткнуться: тому нужен реальный путь.
        self._состояние["ответ"] = ("D:\\Заметки",)
        for старт in ("", "   ", "не папка", 42, None, "C:\\Нет\\Такой"):
            with self.subTest(старт=старт):
                self.bridge.pick_folder(старт)
                self.assertEqual(self._состояние["kwargs"]["directory"], "")



class СвояПапкаТесты(unittest.TestCase):
    """`notes_list` говорит пульту, своя ли папка — ради кнопки «По умолчанию»."""

    def setUp(self):
        self.runtime = object.__new__(WebRuntime)
        self._было = config.NOTES_DIR
        self.addCleanup(setattr, config, "NOTES_DIR", self._было)
        self.папка = Path(tempfile.mkdtemp(prefix="truba-custom-"))
        self.addCleanup(shutil.rmtree, self.папка, True)

    def test_the_default_folder_is_not_a_custom_one(self):
        config.NOTES_DIR = ""
        self.assertFalse(self.runtime.notes_list()["custom"])

    def test_blanks_around_a_path_still_count_as_custom(self):
        # Папку хозяин мог задать с пробелами вокруг пути — это уже выбор.
        config.NOTES_DIR = "  " + str(self.папка) + "  "
        self.assertTrue(self.runtime.notes_list()["custom"])

    def test_a_chosen_folder_is_custom(self):
        config.NOTES_DIR = str(self.папка)
        self.assertTrue(self.runtime.notes_list()["custom"])


class ПереносВНастройкиТесты(unittest.TestCase):
    """Где что лежит теперь — проверяем по тексту страницы."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = PULT_JS.read_text(encoding="utf-8")

    def test_the_settings_form_has_no_notes_folder_anymore(self):
        # Поле убрано из формы, из тела «Сохранить» и из `настрЭлементы`:
        # осталась бы одна строка — и пульт молча затирал бы выбор хозяина
        # пустым значением при каждом нажатии «Сохранить».
        self.assertNotIn("notes_dir", _тело(self.скрипт, "построитьНастройки"))
        self.assertNotIn("notes_dir", _тело(self.скрипт, "заполнитьНастройки"))
        self.assertNotIn("notes_dir", _тело(self.скрипт, "сохранитьНастройки"))

    def test_the_notes_page_offers_both_buttons(self):
        тело = _тело(self.скрипт, "нарисоватьЗаметки")
        self.assertIn("сменитьПапку", тело)
        self.assertIn("поУмолчанию", тело)

    def test_the_enrolment_left_the_check_page(self):
        # Запись образца ушла в «Голос → Слух»; на «Проверке» её больше нет.
        тело = _тело(self.скрипт, "нарисоватьПроверку")
        self.assertNotIn("enroll", тело)
        self.assertNotIn("Образец хозяина", тело)

    def test_the_settings_form_sends_the_enrolment_request(self):
        # Эндпоинт остался тем же, но зовётся он теперь из формы: иначе
        # образец нечем было бы записать нигде.
        тело = _тело(self.скрипт, "построитьНастройки")
        self.assertIn("хозяинНачатьЗапись", тело)
        self.assertIn("хозяинЗакончитьЗапись", тело)
        self.assertIn("/api/check/enroll/start", self.скрипт)

    def test_every_new_russian_name_is_declared_and_used_alike(self):
        # Каждое новое имя — напарой «объявлено и занесено»: латинская `k`
        # вместо русской `к` проходит `node --check`, а страницу валит.
        for имя in ("хозяинЗаписать", "хозяинЗакончить", "хозяинСтатус",
                    "хозяинТекст", "хозяинИтог", "сменитьПапку", "поУмолчанию"):
            with self.subTest(имя=имя):
                объявлено = re.search(
                    r"(?:const|let)\s+" + имя + r"\b", self.скрипт) is not None
                занесено = ("{ " + имя + ",") in self.скрипт
                self.assertTrue(объявлено or занесено,
                                "%s нигде не объявлено" % имя)
        # Функции записи объявлены и зовутся теми же именами.
        for имя in ("хозяинНачатьЗапись", "хозяинСледитьЗапись",
                    "хозяинЗакончитьЗапись", "хозяинПоказатьЗапись", "хозяинЖива"):
            with self.subTest(имя=имя):
                self.assertIn("function " + имя + "(", self.скрипт, имя)


if __name__ == "__main__":
    unittest.main()
