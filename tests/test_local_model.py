"""Локальная модель как ещё один сервис ответов — без сети и без Ollama.

Хозяин попросил «возможность добавить локальную модель с пометкой, что она не
тестировалась». Здесь проверяем ровно то, что можно проверить без железа:
ключ-заглушка вместо ключа, адрес из настройки, честная ошибка выключенного
сервера, запрет local быть запасным и бесплатный расход. Настоящий OpenAI-клиент
подменён, локальный сервер не запускается.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import brain as brain_module
from core import settings, usage
from core.brain import Brain
from ui.web_runtime import WebRuntime

PULT = Path(config.ROOT) / "ui" / "web" / "pult.js"


def _клиент(падать=False, модели=None):
    """Поддельный OpenAI-клиент: запоминает адрес, ключ и число походов."""
    виден = {"base_url": None, "api_key": None, "calls": 0}

    class _Client:
        def __init__(self, **kwargs):
            виден["base_url"] = kwargs.get("base_url")
            виден["api_key"] = kwargs.get("api_key")
            self.chat = NS(completions=NS(create=self._create))
            self.models = NS(list=self._list)

        def _create(self, **body):
            виден["calls"] += 1
            if падать:
                raise RuntimeError("соединение отклонено")
            return NS(choices=[NS(message=NS(content="работает"))])

        def _list(self):
            виден["calls"] += 1
            if падать:
                raise RuntimeError("соединение отклонено")
            return [NS(id=имя) for имя in (модели or ["qwen3:8b", "llama3.1"])]

        def close(self):
            pass

    return NS(OpenAI=_Client), виден


def _runtime() -> WebRuntime:
    """Пульт без живого окружения: нужны два замка и заглушки вместо голоса."""
    runtime = object.__new__(WebRuntime)
    runtime._provider_test_lock = threading.Lock()
    runtime.brain = None
    runtime.voice = NS(_brain=None)
    runtime._remember = lambda kind, payload: None
    return runtime


def _мозг(provider="local", model="qwen3:8b"):
    """Brain без клиента: проверяем только выбор запасного при обрыве."""
    brain = object.__new__(Brain)
    brain.provider = provider
    brain._model = model
    brain._reply_lock = threading.RLock()
    brain.on_event = None
    return brain


def _без_облачных_ключей(тест):
    """Обнуляет ключи облаков в окружении: config.py загрузил их из .env
    хозяина, и тест одиночного запуска не должен на них опираться."""
    подмена = mock.patch.dict("os.environ", {
        "DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "",
        "MINIMAX_API_KEY": "", "OPENAI_API_KEY": ""})
    подмена.start()
    тест.addCleanup(подмена.stop)


def _временные_пути(тест):
    """Каждый тест пишет настройки и .env в свою папку, а не хозяину."""
    папка = Path(tempfile.mkdtemp(prefix="truba-local-"))
    тест.addCleanup(shutil.rmtree, папка, True)
    подмена = mock.patch.multiple(
        settings, SETTINGS_PATH=папка / "settings.json", ENV_PATH=папка / ".env")
    подмена.start()
    тест.addCleanup(подмена.stop)
    # `apply_to_config` и `save_settings` переносят в config и папку заметок.
    # Возвращаем её на место, иначе следующий тест получает «Документы».
    заметки = config.NOTES_DIR
    тест.addCleanup(setattr, config, "NOTES_DIR", заметки)
    return папка


class КлючЗаглушкаTests(unittest.TestCase):
    def setUp(self):
        _временные_пути(self)
        _без_облачных_ключей(self)
        self._было = config.PROVIDERS["local"]["model"]
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        config.PROVIDERS["local"]["model"] = self._было

    def test_local_gets_a_stub_key(self):
        # Ключа в .env нет вовсе — сервер всё равно годен к хождению.
        self.assertEqual(settings.get_api_key("local"), "")
        self.assertEqual(settings.key_for("local"), config.LOCAL_KEY_STUB)
        self.assertTrue(settings.has_key("local"))
        self.assertTrue(settings.is_local("local"))

    def test_cloud_key_is_still_mandatory(self):
        self.assertEqual(settings.key_for("deepseek"), "")
        self.assertFalse(settings.has_key("deepseek"))
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
            Brain(provider="deepseek")

    def test_local_brain_without_a_model_says_what_to_do(self):
        config.PROVIDERS["local"]["model"] = ""
        with self.assertRaisesRegex(RuntimeError, "не выбрана"):
            Brain(provider="local")

    def test_notice_names_the_model_and_the_address(self):
        заметка = settings.local_notice("local", "qwen3:8b",
                                         "http://127.0.0.1:1234/v1")
        self.assertIn("qwen3:8b", заметка)
        self.assertIn("http://127.0.0.1:1234/v1", заметка)
        self.assertIn("с Трубой не проверялась", заметка)
        # Облаку такая пометка не нужна.
        self.assertEqual(settings.local_notice("deepseek"), "")


class АдресTests(unittest.TestCase):
    def setUp(self):
        _временные_пути(self)
        self._было = config.PROVIDERS["local"]["base_url"]
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        config.PROVIDERS["local"]["base_url"] = self._было

    def test_good_addresses_are_accepted(self):
        self.assertEqual(settings.validate_local_url("http://127.0.0.1:11434/v1"),
                         "http://127.0.0.1:11434/v1")
        self.assertEqual(settings.validate_local_url("  https://box:8080/v1/  "),
                         "https://box:8080/v1")
        # Пусто — не ошибка: сервер просто не настроен.
        self.assertEqual(settings.validate_local_url(""), "")

    def test_bad_addresses_are_rejected(self):
        for значение in ("127.0.0.1:11434", "ftp://127.0.0.1", "http:// два",
                         "http://", 12):
            with self.assertRaises(ValueError):
                settings.validate_local_url(значение)

    def test_saved_address_reaches_config(self):
        settings.save_settings({"local_url": "http://127.0.0.1:1234/v1"})
        settings.apply_to_config()
        self.assertEqual(config.PROVIDERS["local"]["base_url"],
                         "http://127.0.0.1:1234/v1")
        self.assertEqual(settings.load_settings()["local_url"],
                         "http://127.0.0.1:1234/v1")

    def test_pult_saves_the_address_and_blames_the_field(self):
        runtime = _runtime()
        ответ = runtime.save_settings({
            "provider": "local", "model": "qwen3:8b",
            "local_url": "http://127.0.0.1:1234/v1"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(config.PROVIDERS["local"]["base_url"],
                         "http://127.0.0.1:1234/v1")
        for плохое in ("127.0.0.1:1234", "ftp://127.0.0.1", "http://два сокета"):
            отказ = runtime.save_settings({
                "provider": "local", "model": "qwen3:8b", "local_url": плохое})
            self.assertFalse(отказ["ok"])
            self.assertTrue(any(e.startswith("local_url:") for e in отказ["errors"]),
                            отказ)

    def test_pult_asks_for_a_model_and_an_address(self):
        runtime = _runtime()
        отказ = runtime.save_settings({"provider": "local", "model": "",
                                       "local_url": "http://127.0.0.1:11434/v1"})
        self.assertFalse(отказ["ok"])
        self.assertIn("model: впиши модель или обнови список", отказ["errors"])
        settings.save_settings({"local_url": ""})
        отказ = runtime.save_settings({"provider": "local", "model": "qwen3:8b",
                                       "local_url": ""})
        self.assertFalse(отказ["ok"])
        self.assertIn("local_url: впиши адрес локального сервера", отказ["errors"])


class ПроверкаСвязиTests(unittest.TestCase):
    def setUp(self):
        _временные_пути(self)
        self._адрес = ("config.PROVIDERS", "local", "base_url")
        self._было = config.PROVIDERS["local"]["base_url"]
        config.PROVIDERS["local"]["base_url"] = "http://127.0.0.1:4321/v1"
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        config.PROVIDERS["local"]["base_url"] = self._было
        config.PROVIDERS["local"]["model"] = "qwen3:8b"

    def test_connection_check_works_without_a_key(self):
        подделка, виден = _клиент()
        with mock.patch.dict("sys.modules", {"openai": подделка}):
            ответ = _runtime().test_provider("local")
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(виден["api_key"], config.LOCAL_KEY_STUB)
        self.assertEqual(виден["base_url"], "http://127.0.0.1:4321/v1")

    def test_model_list_comes_from_the_local_server(self):
        подделка, виден = _клиент(модели=["qwen3:8b"])
        with mock.patch.dict("sys.modules", {"openai": подделка}):
            ответ = _runtime().list_models("local")
        self.assertEqual(ответ["models"], ["qwen3:8b"])
        self.assertEqual(виден["base_url"], "http://127.0.0.1:4321/v1")

    def test_switched_off_server_is_explained_in_plain_words(self):
        подделка, _ = _клиент(падать=True)
        with mock.patch.dict("sys.modules", {"openai": подделка}):
            with self.assertRaises(ValueError) as упало:
                _runtime().list_models("local")
        self.assertIn("локальный сервер не отвечает по адресу", str(упало.exception))
        self.assertIn("Ollama", str(упало.exception))
        with mock.patch.dict("sys.modules", {"openai": подделка}):
            with self.assertRaises(ValueError) as упало:
                _runtime().test_provider("local")
        self.assertIn("http://127.0.0.1:4321/v1", str(упало.exception))

    def test_without_a_model_the_pult_says_what_to_do(self):
        config.PROVIDERS["local"]["model"] = ""
        with self.assertRaisesRegex(ValueError, "обнови список"):
            _runtime().test_provider("local")
class ЗапаснойTests(unittest.TestCase):
    """Локальный сервер не запасной и не второй в страховке — никогда."""

    ОБЛАКА = ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
              "MINIMAX_API_KEY", "OPENAI_API_KEY")

    def setUp(self):
        # Ключ есть даже у локального — это ничего не должно менять.
        self.env = mock.patch.dict("os.environ", {
            "DEEPSEEK_API_KEY": "sk-cloud", "LOCAL_API_KEY": "local"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self._был = brain_module.SPARE_ORDER
        # Даже если кто-то впишет local в порядок запасных.
        brain_module.SPARE_ORDER = ("local", "deepseek")
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        brain_module.SPARE_ORDER = self._был

    def _спрятать_облака(self):
        спрятанные = {имя: os.environ.pop(имя, None) for имя in self.ОБЛАКА}
        self.addCleanup(lambda: [os.environ.__setitem__(к, з)
                                 for к, з in спрятанные.items() if з])

    def test_spare_is_never_local_even_with_a_key(self):
        self.assertEqual(_мозг("local")._spare(), "deepseek")

    def test_hedge_never_picks_local(self):
        self._спрятать_облака()
        было = config.HEDGE
        config.HEDGE = True
        self.addCleanup(setattr, config, "HEDGE", было)
        # Облака без ключей нет — остаётся только локальный, и он не годится.
        self.assertIsNone(_мозг("openai")._spare())
        self.assertIsNone(_мозг("openai")._hedge([], [], False))

    def test_local_as_main_has_no_hedge_to_the_paid_cloud(self):
        # Локальная думает дольше 2.5 с почти всегда: со страховкой каждый
        # вопрос уходил бы ещё и в платное облако. Облако с ключом тут есть.
        было = config.HEDGE
        config.HEDGE = True
        self.addCleanup(setattr, config, "HEDGE", было)
        мозг = _мозг("local")
        self.assertEqual(мозг._spare(), "deepseek")
        self.assertIsNone(мозг._hedge([{"role": "user", "content": "привет"}], [], False))

    def test_local_as_main_falls_back_to_the_cloud(self):
        from openai import APIConnectionError

        class _Молчит:
            def __init__(self):
                self.chat = NS(completions=NS(create=self._create))

            def _create(self, **body):
                raise APIConnectionError(request=NS())

        мозг = _мозг("local")
        мозг._client = _Молчит()
        self.assertTrue(мозг._fall_back(APIConnectionError(request=NS())))
        self.assertEqual(мозг.provider, "deepseek")


class РасходTests(unittest.TestCase):
    def setUp(self):
        self._папка = Path(tempfile.mkdtemp(prefix="truba-local-"))
        self.addCleanup(shutil.rmtree, self._папка, True)
        self._было = usage.USAGE_PATH
        usage.USAGE_PATH = self._папка / "usage.jsonl"
        self.addCleanup(self._вернуть)

    def _вернуть(self):
        usage.USAGE_PATH = self._было

    def _записать(self, момент, **поля):
        запись = {"at": момент.isoformat(), "calls": 1, "cached": 0}
        запись.update(поля)
        usage.USAGE_PATH.write_text(
            json.dumps(запись, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_local_price_is_zero_not_unknown(self):
        self.assertEqual(usage.price_for("local/qwen3:8b"),
                         {"input": 0.0, "cached": 0.0, "output": 0.0})

    def test_summary_marks_local_as_free_instead_of_unpriced(self):
        сейчас = datetime.now()
        self._записать(сейчас, model="local/qwen3:8b", prompt=5000, completion=200)
        период = usage.summary(сейчас, сейчас)["periods"]["today"]
        self.assertEqual(период["usd"], 0.0)
        self.assertFalse(период["unpriced"])
        self.assertTrue(период["local"])
        # Токены, если сервер их отдал, видны; не отдаст — тоже не падаем.
        self.assertEqual(период["prompt"], 5000)


class ЖурналTests(unittest.TestCase):
    def test_local_brain_line_says_it_was_not_checked(self):
        строка = WebRuntime._log_messages(
            "brain_local", settings.local_notice("local", "qwen3:8b",
                                                 "http://127.0.0.1:11434/v1"))
        self.assertEqual(len(строка), 1)
        self.assertIn("мозг: локальная модель qwen3:8b", строка[0])
        self.assertIn("с Трубой не проверялась", строка[0])

    def test_pult_keeps_the_warning_and_the_three_addresses(self):
        текст = PULT.read_text(encoding="utf-8")
        self.assertIn("Локальная (не проверялась)", текст)
        self.assertIn("с Трубой не проверялась", текст)
        self.assertIn("локальная — бесплатно", текст)
        for адрес in ("http://127.0.0.1:11434/v1", "http://127.0.0.1:1234/v1",
                      "http://127.0.0.1:8080/v1"):
            self.assertIn(адрес, текст)

    def test_brain_card_mentions_the_same_thing(self):
        # Карточка «мозг» на Панели: пометка под именем модели.
        текст = (Path(config.ROOT) / "core" / "state.py").read_text(encoding="utf-8")
        self.assertIn("локальная, не проверялась", текст)

    def test_pult_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node не установлен")
        result = subprocess.run([node, "--check", str(PULT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
