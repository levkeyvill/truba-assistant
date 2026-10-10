"""Готовые характеры: «Пиздабол Edition», спокойная, дружелюбная, деловая.

В «Настройки → Характер» карточка подставляет текст. Выбранный характер
также определяет ответы без модели («спокойной ночи», «на связи» при запуске).

Живые `settings.json` и `prompts/persona.md` не трогаются: настройки — во
временной папке, характер хозяина в этих тестах не читается и не пишется.
"""

import json
import random
import shutil
import socket
import string
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
    def test_say_sets_have_every_key_and_format_every_fact(self):
        keys = set(personas.SAY_NEUTRAL)
        for preset, phrases in [(None, personas.SAY_NEUTRAL)] + [
                (name, spec["say"]) for name, spec in personas.PRESETS.items()]:
            with self.subTest(preset=preset):
                self.assertEqual(set(phrases), keys)
                for key, variants in phrases.items():
                    self.assertTrue(2 <= len(variants) <= 4, key)
                    fields = {field for phrase in variants
                              for _, field, _, _ in string.Formatter().parse(phrase)
                              if field}
                    values = {field: f"Факт-{field}" for field in fields}
                    for phrase in variants:
                        said = phrase.format(**values)
                        self.assertNotIn("{", said)
                        for value in values.values():
                            self.assertIn(value, said)

    def test_say_uses_neutral_fallback_and_avoids_immediate_repeat(self):
        personas._say_last.clear()
        with mock.patch.object(random, "choice", side_effect=lambda phrases: phrases[0]):
            first = personas.say("launch", "pizdabol", app="Браузер")
            second = personas.say("launch", "pizdabol", app="Браузер")
            self.assertNotEqual(first, second)
            self.assertIn(personas.say("launch", "custom", app="Браузер"),
                          {phrase.format(app="Браузер")
                           for phrase in personas.SAY_NEUTRAL["launch"]})

    def test_four_presets_in_order_and_the_default(self):
        # Четыре готовых и «Свой» последним.
        self.assertEqual(list(personas.PRESETS),
                         ["pizdabol", "calm", "friendly", "business", "custom"])
        self.assertEqual(personas.DEFAULT, "pizdabol")
        self.assertEqual(personas.PRESETS["pizdabol"]["title"], "Пиздабол Edition")

    def test_every_text_keeps_what_she_breaks_without(self):
        # У «Своего» готового текста нет: его пишет человек.
        self.assertEqual(personas.text(personas.CUSTOM), "")
        for ключ in personas.PRESETS:
            if ключ == personas.CUSTOM:
                continue
            with self.subTest(ключ=ключ):
                текст = personas.text(ключ)
                self.assertIn("Тебя зовут Труба", текст)
                self.assertIn("женского рода", текст)
                self.assertIn("## Длина и звучание", текст)
                self.assertIn("синтезатор", текст)
                self.assertIn("## Что ты знаешь о происходящем", текст)
                self.assertIn("просит помолчать", текст)

    def test_the_calm_ones_have_no_swearing_and_no_nicknames(self):
        for ключ in ("calm", "friendly", "business", "custom"):
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

    def test_wait_phrases_cover_every_kind_and_match_the_preset(self):
        виды = {"read", "translate", "analyze", "find", "notes", "reminders", "web"}
        for ключ, набор in personas.PRESETS.items():
            with self.subTest(ключ=ключ):
                self.assertEqual(set(набор["wait"]), виды)
                self.assertTrue(all(2 <= len(фразы) <= 4
                                    for фразы in набор["wait"].values()))
        self.assertEqual(set(personas.WAIT_NEUTRAL), виды)
        with mock.patch.object(random, "choice", side_effect=lambda фразы: фразы[0]):
            self.assertEqual(personas.wait_phrase("find", "pizdabol"),
                             personas.PRESETS["pizdabol"]["wait"]["find"][0])
            self.assertEqual(personas.wait_phrase("find", None),
                             personas.WAIT_NEUTRAL["find"][0])
            self.assertEqual(personas.wait_phrase("find", "нет_такого"),
                             personas.WAIT_NEUTRAL["find"][0])
            self.assertEqual(personas.wait_phrase("unknown", "calm"),
                             personas.WAIT_NEUTRAL["find"][0])
        self.assertEqual(personas.all_wait_phrases(),
                         frozenset(фраза for набор in
                                   [personas.WAIT_NEUTRAL] +
                                   [пресет["wait"] for пресет in personas.PRESETS.values()]
                                   for фразы in набор.values() for фраза in фразы))

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
        voice_loop._last_quick.clear()
        self.addCleanup(voice_loop._last_quick.clear)

    def test_goodbye_comes_from_the_chosen_preset(self):
        config.PERSONA_PRESET = "calm"
        with mock.patch.object(random, "choice", side_effect=lambda v: v[0]):
            self.assertEqual(voice_loop.goodbye_words("спасибо большое"), "Пожалуйста!")
            self.assertEqual(voice_loop.goodbye_words("спокойной ночи"), "Спокойной ночи!")

    def test_pizdabol_thanks_are_not_polite_templates_or_nicknames(self):
        config.PERSONA_PRESET = "pizdabol"
        with mock.patch.object(random, "choice", side_effect=lambda v: v[-1]):
            first = voice_loop.goodbye_words("спасибо")
            second = voice_loop.goodbye_words("спасибо")
        self.assertNotEqual(first, second)
        self.assertNotIn("пожалуйста", first.lower() + second.lower())
        self.assertNotIn("кожан", first.lower() + second.lower())


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


class СвоиСтилиTests(unittest.TestCase):
    """Правка одного стиля не теряется при выборе другого."""

    def setUp(self):
        папка = Path(tempfile.mkdtemp(prefix="truba-personas-own-"))
        self.addCleanup(shutil.rmtree, папка, True)
        подмена = mock.patch.multiple(
            settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env",
            PERSONA_PATH=папка / "persona.md", PERSONAS_DIR=папка / "personas")
        подмена.start()
        self.addCleanup(подмена.stop)
        self.addCleanup(setattr, config, "PERSONA_PRESET", config.PERSONA_PRESET)

    def test_свой_текст_стиля_хранится_отдельно(self):
        self.assertIsNone(settings.persona_own("pizdabol"))
        settings.save_persona_own("pizdabol", "Мой пиздабол (выдумано для теста).")
        settings.save_persona_own("custom", "Совсем свой характер (выдумано для теста).")
        self.assertEqual(settings.persona_own("pizdabol"), "Мой пиздабол (выдумано для теста).")
        self.assertEqual(settings.persona_own("custom"), "Совсем свой характер (выдумано для теста).")
        self.assertIsNone(settings.persona_own("calm"))

    def test_текст_как_у_готового_не_хранится(self):
        settings.save_persona_own("calm", "правка")
        settings.save_persona_own("calm", personas.text("calm"))
        self.assertIsNone(settings.persona_own("calm"))

    def test_чужой_стиль_не_пишется(self):
        settings.save_persona_own("../злой", "x")
        self.assertIsNone(settings.persona_own("../злой"))
        self.assertFalse(settings.PERSONAS_DIR.exists())

    def _среда(self):
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

    def test_пульт_сохраняет_выбранный_и_черновики(self):
        ответ = self._среда().save_settings({
            "persona": "Деловая, но своя (выдумано для теста).",
            "persona_preset": "business",
            "persona_drafts": {"pizdabol": "Мой пиздабол (выдумано для теста)."},
        })
        self.assertTrue(ответ.get("ok"), ответ)
        self.assertEqual(settings.persona_own("business"), "Деловая, но своя (выдумано для теста).")
        self.assertEqual(settings.persona_own("pizdabol"), "Мой пиздабол (выдумано для теста).")
        self.assertEqual(settings.load_persona(), "Деловая, но своя (выдумано для теста).")

    def test_перенос_настроек_берёт_свои_стили(self):
        from core import transfer

        self.assertEqual(transfer._часть_файла("prompts/personas/custom.md"), "persona")
        self.assertEqual(transfer._часть_файла("prompts/personas/pizdabol.md"), "persona")
        self.assertEqual(transfer._часть_файла("prompts/personas/злой.md"), "")
        self.assertEqual(transfer._часть_файла("prompts/personas/../settings.md"), "")

    def test_мусор_в_черновиках_отклоняется(self):
        ответ = self._среда().save_settings({"persona_drafts": {"злой": "x"}})
        self.assertFalse(ответ.get("ok"))
        self.assertIsNone(settings.persona_own("злой"))


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
        self.assertTrue(all(п["text"] for п in тело["presets"] if п["id"] != "custom"))
        # Свой вариант каждого стиля приходит рядом с готовым (или null).
        self.assertTrue(all("own" in п for п in тело["presets"]))
        self.assertEqual(тело["default"], "pizdabol")


class ПультTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = PULT_JS.read_text(encoding="utf-8")

    def test_no_two_functions_share_a_name(self):
        # Вторая функция с тем же именем молча заменяет первую: так в пульте
        # жила копия «железа» под именем «характерВыбрать».
        import re
        from collections import Counter

        имена = Counter(re.findall(r"^(?:async )?function ([^\s(]+)\(", self.js, re.M))
        self.assertEqual([имя for имя, раз in имена.items() if раз > 1], [])

    def test_cards_load_and_the_choice_is_saved(self):
        self.assertIn("/api/personas", self.js)
        self.assertIn("persona_preset: характерВыбранный", self.js)
        self.assertIn("характерЗагрузить(", self.js)

    def test_own_edits_are_not_overwritten_silently(self):
        # Смена стиля ничего не теряет и не спрашивает: текст ушедшего стиля
        # остаётся за ним, у выбранного — свой.
        блок = self.js[self.js.index("function характерВыбрать("):
                       self.js.index("function характерИсходный(")]
        self.assertNotIn("confirm(", блок)
        self.assertIn("характерыЧерновики[характерВыбранный] = поле.value", блок)
        self.assertIn("характерТекст(готовый.id)", блок)
        self.assertIn("persona_drafts:", self.js)
        self.assertIn("textContent", self.js[self.js.index("async function характерЗагрузить("):
                                            self.js.index("function характерОтметить(")])


if __name__ == "__main__":
    unittest.main()
