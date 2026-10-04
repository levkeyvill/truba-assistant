"""Выбор модели без обращения к настоящим ключам и внешним API."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from core import settings
from ui.web_runtime import WebRuntime, _validated_model
from core.brain import completion_limits


class ModelSettingsTests(unittest.TestCase):
    def test_custom_model_ids_for_every_provider(self):
        self.assertEqual(_validated_model("deepseek", "deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertEqual(
            _validated_model("openrouter", "deepseek/deepseek-v4-flash:free"),
            "deepseek/deepseek-v4-flash:free",
        )
        self.assertEqual(_validated_model("deepseek", "deepseek-chat"), "deepseek-chat")
        with self.assertRaisesRegex(ValueError, "без пробелов"):
            _validated_model("openrouter", "not a model")

    def test_completion_limits_for_openai(self):
        self.assertEqual(completion_limits("openai", 256, 0.7),
                         {"max_completion_tokens": 256})
        self.assertEqual(completion_limits("deepseek", 64, 0.7),
                         {"max_tokens": 64, "temperature": 0.7})

    def test_selected_model_survives_reload_without_changing_other_providers(self):
        previous = {name: spec["model"] for name, spec in config.PROVIDERS.items()}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, "SETTINGS_PATH", Path(directory) / "settings.json"):
                try:
                    models = settings.load_settings()["models"]
                    models["deepseek"] = "deepseek-v4-pro"
                    settings.save_settings({"models": models})
                    settings.apply_to_config()
                    self.assertEqual(config.PROVIDERS["deepseek"]["model"], "deepseek-v4-pro")
                    self.assertEqual(settings.load_settings()["models"]["deepseek"], "deepseek-v4-pro")
                    self.assertEqual(settings.load_settings()["models"]["openai"], previous["openai"])
                finally:
                    for name, model in previous.items():
                        config.PROVIDERS[name]["model"] = model

    def test_connection_check_uses_unsaved_model_without_network(self):
        called = {}

        def create(**kwargs):
            called["model"] = kwargs["model"]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="работает"))])

        fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        runtime = object.__new__(WebRuntime)
        runtime._provider_test_lock = threading.Lock()
        with patch.dict("sys.modules", {"openai": fake_openai}), \
             patch.object(settings, "get_api_key", return_value="test-only-key"):
            result = runtime.test_provider("deepseek", model="deepseek-v4-pro")
        self.assertTrue(result["ok"])
        self.assertEqual(called["model"], "deepseek-v4-pro")

    def test_live_model_catalog_and_saved_key_reveal_without_network(self):
        fake_client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: [SimpleNamespace(id="new-model"),
                                                 SimpleNamespace(id="old-model")]),
            close=lambda: None,
        )
        fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: fake_client)
        runtime = object.__new__(WebRuntime)
        with patch.dict("sys.modules", {"openai": fake_openai}), \
             patch.object(settings, "get_api_key", return_value="test-only-key"):
            self.assertEqual(runtime.list_models("openai")["models"],
                             ["new-model", "old-model"])
            self.assertEqual(runtime.reveal_api_key("openai")["key"], "test-only-key")

    def test_web_settings_save_switches_model_without_touching_real_keys(self):
        previous = {name: spec["model"] for name, spec in config.PROVIDERS.items()}
        runtime = object.__new__(WebRuntime)
        runtime.brain = None
        runtime.voice = SimpleNamespace(_brain=None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, "SETTINGS_PATH", Path(directory) / "settings.json"), \
                 patch.object(settings, "ENV_PATH", Path(directory) / ".env"):
                try:
                    result = runtime.save_settings({
                        "provider": "deepseek", "model": "deepseek-v4-pro"})
                    self.assertTrue(result["ok"])
                    self.assertEqual(settings.load_settings()["models"]["deepseek"], "deepseek-v4-pro")
                    self.assertEqual(config.PROVIDERS["deepseek"]["model"], "deepseek-v4-pro")
                finally:
                    for name, model in previous.items():
                        config.PROVIDERS[name]["model"] = model


class JournalTests(unittest.TestCase):
    def test_journal_keeps_phrases(self):
        from ui.web_runtime import WebRuntime

        # Телефон при подключении видит честное состояние, а не «слушаю».
        runtime = object.__new__(WebRuntime)
        runtime.voice = SimpleNamespace(running=False, ready=False)
        self.assertEqual(runtime._phone_state_now(), "voiceoff")
        runtime.voice = SimpleNamespace(running=True, ready=False)
        self.assertEqual(runtime._phone_state_now(), "loading")
        runtime.voice = SimpleNamespace(running=True, ready=True, _idle_state=lambda: "asleep")
        self.assertEqual(runtime._phone_state_now(), "asleep")
        heard = WebRuntime._log_messages("heard", {"text": "Как дела?", "seconds": 1.8, "stt_time": 0.1})
        self.assertTrue(heard[0].startswith("услышала: Как дела?"))
        self.assertEqual(WebRuntime._log_messages("sentence", "Нормально."), ["ответила: Нормально."])
        self.assertIn("закрыла разговор", WebRuntime._log_messages("ended_by_model", "ок, всё")[0])


if __name__ == "__main__":
    unittest.main()
