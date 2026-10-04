"""Выпуск не несёт ничего от компьютера автора.

Настройки по умолчанию не должны зависеть от голоса, программ или видеокарты
конкретной установки. Совместимость Higgs проверяется до загрузки весов.
"""

import ast
import json
import unittest
from pathlib import Path
from unittest import mock

import config
from core import state

ROOT = Path(config.ROOT)


def _умолчания() -> dict:
    """Значения из самого config.py — тесты подменяют config на лету."""
    дерево = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
    значения = {}
    for узел in дерево.body:
        if isinstance(узел, ast.Assign) and len(узел.targets) == 1 \
                and isinstance(узел.targets[0], ast.Name):
            try:
                значения[узел.targets[0].id] = ast.literal_eval(узел.value)
            except ValueError:
                pass
    return значения


class ReleaseDefaultsTests(unittest.TestCase):
    def test_nothing_personal_is_a_default(self):
        у = _умолчания()
        # Устройства, город, папка заметок, образец голоса — выбирает человек.
        for имя in ("MIC_NAME", "SPEAKER_NAME", "WEATHER_CITY", "NOTES_DIR", "VOICE_NAME"):
            self.assertEqual(у[имя], "", f"{имя} по умолчанию должен быть пустым")
        self.assertIsNone(у["WEATHER_LAT"])
        self.assertIsNone(у["WEATHER_LON"])
        # Сторож повтора NVIDIA жмёт клавиши — только если включил сам.
        self.assertFalse(у["REPLAY_GUARD"])
        self.assertFalse(у["OWNER_ONLY"])

    def test_the_status_line_survives_an_empty_sample(self):
        with mock.patch.object(config, "TTS_ENGINE", "higgs"), \
                mock.patch.object(config, "VOICE_NAME", ""):
            внизу = state._voice()["внизу"]
        self.assertIn("образец не выбран", внизу)
        self.assertNotIn("·  ·", внизу)


class NotOnlyTheAuthorsMachineTests(unittest.TestCase):
    def test_the_command_check_uses_the_persons_own_programs(self):
        # Проверка первого запуска должна опираться на программы текущей установки.
        from core import launcher, selftest

        стартовые = json.loads((ROOT / "apps.default.json").read_text(encoding="utf-8"))
        for список in (стартовые, []):
            отчёт = selftest.Report()
            with mock.patch.object(launcher, "read_list", return_value=список):
                selftest.commands_work(отчёт)
            итог = отчёт.results[-1]
            self.assertTrue(итог.ok, f"{итог.value}: {итог.note}")

    def test_higgs_refuses_a_card_without_fp8_before_downloading(self):
        # FP8 — только RTX 40 и 50. На RTX 30 (8.6) — понятный отказ, и
        # 9 ГБ весов не качаются зря.
        import torch
        from core import higgs_voice

        голос = higgs_voice.HiggsVoice(device="cuda", precision="fp8")
        with mock.patch.object(torch.cuda, "is_available", return_value=True), \
                mock.patch.object(torch.cuda, "get_device_capability", return_value=(8, 6)), \
                mock.patch.object(higgs_voice, "_weights_folder",
                                  side_effect=AssertionError("качать не должен")):
            with self.assertRaisesRegex(RuntimeError, "RTX 40 и 50"):
                голос.load()

    def test_the_wizard_warns_about_the_swearing_default(self):
        # «Пиздабол Edition» остаётся по умолчанию, но
        # мимо мата не пройти: надпись под карточками, вопрос на «Дальше» и
        # строчка в «Пропустить настройку?».
        pult = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertEqual(_умолчания()["PERSONA_PRESET"], "pizdabol")
        self.assertIn("Осторожно: в «Пиздабол Edition» она матерится и подкалывает.", pult)
        self.assertIn("if (эл.мат) эл.мат.hidden = эл.выбран !== 'pizdabol';", pult)
        сохранить = pult.split("async function мастерСохранитьХарактер(эл)")[1].split("\n}")[0]
        self.assertIn("window.confirm(", сохранить)
        self.assertIn("return 'Выбери характер — карточки выше';", сохранить)
        пропуск = pult.split("async function мастерПропустить()")[1].split("\n}")[0]
        self.assertIn("Характер останется «Пиздабол Edition» — с матом", пропуск)

    def test_no_word_for_word_talk_with_friends_in_the_code(self):
        # Личные реплики и имена не должны попадать в публичный код.
        for папка in ("core", "ui", "web", "tests"):
            for путь in (ROOT / папка).rglob("*"):
                if путь.suffix not in (".py", ".js", ".html", ".mjs") or путь.name == Path(__file__).name:
                    continue
                текст = путь.read_text(encoding="utf-8", errors="ignore").lower()
                for слово in ("трынди", "попиздеть", "саня"):
                    self.assertNotIn(слово, текст, f"{путь.relative_to(ROOT)}: {слово}")

    def test_the_pult_greys_out_higgs_where_the_card_cannot(self):
        pult = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("эл.железоСовет = совет;", pult)
        self.assertIn("совет.voice === 'higgs'", pult)
        self.assertIn("нужна RTX 40 или 50, от 8 ГБ", pult)


if __name__ == "__main__":
    unittest.main()
