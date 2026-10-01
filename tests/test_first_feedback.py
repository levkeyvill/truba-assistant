"""Первые отзывы после выкладки (29.09): экран телефона и место на диске.

Люди спрашивали «как на телефоне https сделать» — экран гас, потому что
Wake Lock браузер даёт только защищённому адресу. Ответ — два способа в
«Настройки → Телефон» и в README. И в «Твоём компьютере» строка «мало места
на диске» стояла дважды: красным советом по железу и серым статусом голосов.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core import voices_install

ROOT = Path(config.ROOT)
PULT = ROOT / "ui" / "web" / "pult.js"
README = ROOT / "README.md"
RUNTIME = ROOT / "ui" / "web_runtime.py"


class PhoneScreenHelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pult = PULT.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_settings_phone_section_explains_both_ways(self):
        self.assertIn("function телефонНеГасить(", self.pult)
        self.assertIn("телефонНеГасить(телефон, телАдрес)", self.pult)
        for кусок in ("Не выключать экран",
                      "chrome://flags/#unsafely-treat-insecure-origin-as-secure",
                      "Enabled", "Relaunch", "Чтобы экран телефона не гас"):
            self.assertIn(кусок, self.pult, f"в пульте нет: {кусок}")
            self.assertIn(кусок, self.readme, f"в README нет: {кусок}")

    def test_the_flag_gets_the_origin_of_the_chosen_address(self):
        # Флажок Chrome ждёт «http://адрес:порт» — без пути и слэша в конце,
        # и ровно тот адрес, что выбран в списке выше.
        self.assertIn("new URL(выбор.value).origin", self.pult)
        self.assertIn("выбор.addEventListener('change', обновить)", self.pult)
        self.assertIn("if (эл.неГаситьОбновить) эл.неГаситьОбновить();", self.pult)

    def test_the_wizard_points_there(self):
        self.assertIn("Экран телефона гаснет — как это исправить", self.pult)


class FullScreenHelpTests(unittest.TestCase):
    """Полный экран на телефоне (Firefox Android и iPhone): хозяин спросил
    01.10. Инструкция обязана быть и в мастере, и в «Настройки → Телефон»,
    и обещать полный экран там, где браузер его не даёт, нельзя."""

    @classmethod
    def setUpClass(cls):
        cls.pult = PULT.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_the_helper_is_used_in_the_wizard_and_in_the_settings(self):
        self.assertIn("function телефонВоВесьЭкран(", self.pult)
        # Шаг телефона мастера и обычная форма — по одному вызову.
        self.assertIn("телефонВоВесьЭкран(тело);", self.pult)
        self.assertIn("телефонВоВесьЭкран(телефон);", self.pult)

    def test_both_browsers_are_described_in_the_pult_and_the_readme(self):
        # 01.10: шаг про iPhone переименован — на айфоне полным экраном
        # занимается и Firefox (он на том же движке Safari).
        for кусок in ("Как открыть Трубу на весь экран", "Firefox на Android",
                      "iPhone (Safari или Firefox)", "Установить",
                      "Открывать как веб-приложение", "с иконки"):
            self.assertIn(кусок, self.pult, f"в пульте нет: {кусок}")
            self.assertIn(кусок, self.readme, f"в README нет: {кусок}")

    def test_the_iphone_button_is_admitted_to_be_useless(self):
        # Кнопка «во весь экран» на айфоне не работает — сказано прямо, а не
        # оставлено читать по инструкции, как будто кнопка полезна.
        for кусок in ("кнопка «во весь экран»", "ничего не делает",
                      "тогда он привязан к компьютеру"):
            self.assertIn(кусок, self.pult, f"в пульте нет: {кусок}")
            self.assertIn(кусок, self.readme, f"в README нет: {кусок}")

    def test_the_firefox_shortcut_is_not_promised_as_fullscreen(self):
        # Ярлык «Добавить на главный экран» — не приложение: обещать ему
        # полный экран нельзя, и про это сказано прямо.
        for кусок in ("Добавить на главный экран", "не гарантирует"):
            self.assertIn(кусок, self.pult)
            self.assertIn(кусок, self.readme)

    def test_ios_meta_tag_is_present_but_not_the_only_way(self):
        self.assertIn('<meta name="apple-mobile-web-app-capable" content="yes">',
                      self.index)


class DiskWarningOnceTests(unittest.TestCase):
    def test_the_disk_refusal_is_marked_for_the_pult(self):
        железо = {"gpus": [{"name": "NVIDIA GeForce RTX 5060 Ti", "vram_gb": 16,
                            "driver": "610.88", "compute_cap": 12.0}],
                  "disk_free_gb": 13.5}
        with tempfile.TemporaryDirectory() as папка, \
                mock.patch("core.hardware.detect", return_value=железо), \
                mock.patch("core.hardware.cuda_index", return_value="cu130"):
            итог = voices_install.check(папка)
        self.assertFalse(итог["ok"])
        self.assertEqual(итог.get("reason"), "disk")
        self.assertIn("мало места на диске", итог["error"])

    def test_the_warning_shows_the_same_number_as_the_table(self):
        from core import hardware

        self.assertEqual(hardware.warnings({"disk_free_gb": 13.5})[0],
                         "мало места на диске (13.5 ГБ) — качественным голосам нужно от 15 ГБ")
        self.assertIn("(14 ГБ)", hardware.warnings({"disk_free_gb": 14.0})[0])

    def test_the_status_carries_the_reason_and_the_pult_does_not_repeat_it(self):
        self.assertIn('"reason":', RUNTIME.read_text(encoding="utf-8"))
        pult = PULT.read_text(encoding="utf-8")
        self.assertIn("данные.reason === 'disk'", pult)
        self.assertIn("Освободи место на диске — тогда здесь появится кнопка установки", pult)


if __name__ == "__main__":
    unittest.main()
