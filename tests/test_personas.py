"""Готовые характеры: «Пиздабол Edition», спокойная, дружелюбная, деловая.

28.09 хозяин: «человек скачает, а она „привет, кожаный“ — пусть будет так,
но лучше дать возможность нормально отвечать». В «Настройки → Характер» —
карточки: нажал — текст подставился. Выбранный характер ещё и решает, чем
Труба отвечает без модели («спокойной ночи», «на связи» при запуске).

Живые `settings.json` и `prompts/persona.md` не трогаются: настройки — во
временной папке, характер хозяина в этих тестах не читается и не пишется.
"""

import json
import random
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import personas, settings, voice_loop, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
МАТ = ("кожан", "бля", "нахуй", "хуй", "пизд", "ёб", "еба", "подъёб", "подъеб")


class ПресетыTests(unittest.TestCase):
    def test_four_presets_in_order_and_the_default(self):
        self.assertEqual(list(personas.PRESETS),
                         ["pizdabol", "calm", "friendly", "business"])
        self.assertEqual(personas.DEFAULT, "pizdabol")
        self.assertEqual(personas.PRESETS["pizdabol"]["title"], "Пиздабол Edition")

    def test_every_text_keeps_what_she_breaks_without(self):
        for ключ in personas.PRESETS:
            with self.subTest(ключ=ключ):
                текст = personas.text(ключ)
                self.assertIn("Тебя зовут Труба", текст)
                self.assertIn("женского рода", текст)
                self.assertIn("## Длина и звучание", текст)
                self.assertIn("синтезатор", текст)
                self.assertIn("## Что ты знаешь о происходящем", текст)
                self.assertIn("просит помолчать", текст)

    def test_no_names_of_a_real_person_in_the_texts(self):
        # Готовые характеры — для всех: имя хозяина модель знает из памяти.
        for ключ in personas.PRESETS:
            with self.subTest(ключ=ключ):
                текст = personas.text(ключ).lower()
                for имя in ("илья", "ильи", "илью", "ильёй", "илюх", "mrkips"):
                    self.assertNotIn(имя, текст)

    def test_the_calm_ones_have_no_swearing_and_no_nicknames(self):
        for ключ in ("calm", "friendly", "business"):
            with self.subTest(ключ=ключ):
                текст = personas.text(ключ).lower()
                for слово in МАТ:
                    self.assertNotIn(слово, текст)
                фразы = " ".join(sum(personas.bye_words(ключ).values(), ()))
                фразы += " ".join(personas.ready_words(ключ))
                self.assertNotIn("кожан", фразы.lower())

    def test_pizdabol_keeps_todays_quick_words(self):
        self.assertEqual({k: tuple(v) for k, v in personas.bye_words("pizdabol").items()},
                         {k: tuple(v) for k, v in voice_loop.BYE_WORDS.items()})
        self.assertEqual(tuple(personas.ready_words("pizdabol")),
                         tuple(voice_loop.READY_WORDS))

    def test_every_preset_answers_every_goodbye(self):
        for ключ in personas.PRESETS:
            with self.subTest(ключ=ключ):
                self.assertEqual(set(personas.bye_words(ключ)), set(voice_loop.BYE_WORDS))
                self.assertTrue(all(personas.bye_words(ключ).values()))
                self.assertTrue(personas.ready_words(ключ))

    def test_unknown_id_is_the_default(self):
        self.assertEqual(personas.valid("что-то"), "pizdabol")
        self.assertEqual(personas.valid(None), "pizdabol")
        self.assertEqual(personas.get("что-то")["title"], "Пиздабол Edition")
        self.assertTrue(personas.text("что-то"))

    def test_catalog_for_the_pult(self):
        список = personas.catalog()
        self.assertEqual([п["id"] for п in список], list(personas.PRESETS))
        self.assertNotIn("text", список[0])
        self.assertTrue(personas.catalog(True)[0]["text"])


class БыстрыеФразыTests(unittest.TestCase):
    def setUp(self):
        было = config.PERSONA_PRESET
        self.addCleanup(setattr, config, "PERSONA_PRESET", было)

    def test_goodbye_comes_from_the_chosen_preset(self):
        config.PERSONA_PRESET = "calm"
        with mock.patch.object(random, "choice", side_effect=lambda v: v[0]):
            self.assertEqual(voice_loop.goodbye_words("спасибо большое"), "Пожалуйста!")
            self.assertEqual(voice_loop.goodbye_words("спокойной ночи"), "Спокойной ночи!")

    def test_pizdabol_says_it_as_before(self):
        config.PERSONA_PRESET = "pizdabol"
        with mock.patch.object(random, "choice", side_effect=lambda v: v[-1]):
            self.assertEqual(voice_loop.goodbye_words("спасибо"), "Обращайся, кожаный.")


class НастройкаTests(unittest.TestCase):
    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-personas-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        for имя in ("NOTES_DIR", "PERSONA_PRESET"):
            self.addCleanup(setattr, config, имя, getattr(config, имя))

    def test_default_is_pizdabol(self):
        self.assertEqual(settings.DEFAULTS["persona_preset"], "pizdabol")

    def test_saved_preset_reaches_config_and_garbage_does_not(self):
        settings.SETTINGS_PATH.write_text(json.dumps({"persona_preset": "business"}),
                                          encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.PERSONA_PRESET, "business")
        settings.SETTINGS_PATH.write_text(json.dumps({"persona_preset": "злой"}),
                                          encoding="utf-8")
        settings.apply_to_config()
        self.assertEqual(config.PERSONA_PRESET, "pizdabol")

    def test_the_pult_saves_it_and_fixes_garbage(self):
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
        ответ = runtime.save_settings({"persona_preset": "friendly"})
        self.assertTrue(ответ.get("ok"), ответ)
        сохранено = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(сохранено["persona_preset"], "friendly")
        runtime.save_settings({"persona_preset": "злой"})
        сохранено = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(сохранено["persona_preset"], "pizdabol")


def _порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_порт())
        cls.server.start()
        cls.server._ready.wait(timeout=5)

    def test_the_pult_gets_four_presets_with_texts(self):
        тело = TestClient(self.server._app).get("/api/personas").json()
        self.assertTrue(тело["ok"])
        self.assertEqual([п["id"] for п in тело["presets"]], list(personas.PRESETS))
        self.assertTrue(all(п["text"] for п in тело["presets"]))
        self.assertEqual(тело["default"], "pizdabol")


class ПультTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_cards_load_and_the_choice_is_saved(self):
        self.assertIn("/api/personas", self.js)
        self.assertIn("persona_preset: характерВыбранный", self.js)
        self.assertIn("характерЗагрузить(", self.js)

    def test_own_edits_are_not_overwritten_silently(self):
        блок = self.js[self.js.index("function характерВыбрать("):]
        self.assertIn("confirm(", блок)
        self.assertIn("textContent", self.js[self.js.index("async function характерЗагрузить("):
                                            self.js.index("function характерОтметить(")])


if __name__ == "__main__":
    unittest.main()
