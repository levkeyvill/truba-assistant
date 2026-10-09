"""Размышления не съедают ответ: DeepSeek без них, пустой круг переспрашивается.

DeepSeek по умолчанию размышляет, и размышления тратят тот же потолок, что
и ответ: на 220 токенах она отвечала пустотой и молчала. Модель подменена:
потоки заготовлены, тела запросов запоминаются.
"""

import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from openai import BadRequestError

import config
from core import brain as brain_mod
from test_fast_actions import _brain, _chunk


def _think(text="думаю, думаю, думаю"):
    """Кусок одних размышлений."""
    return _chunk(reasoning=text)


def _end(reason):
    delta = NS(content=None, tool_calls=None, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=reason)])


def _отказ(текст):
    ответ = mock.Mock(status_code=400, headers={}, request=mock.Mock())
    return BadRequestError(текст, response=ответ, body=None)


class РазмышленияTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.MAX_TOKENS, config.WEB_SEARCH, config.REASONING)
        config.MAX_TOKENS = 220
        config.WEB_SEARCH = False
        config.REASONING = False
        self.addCleanup(self._restore)

    def _restore(self):
        config.MAX_TOKENS, config.WEB_SEARCH, config.REASONING = self._saved

    def test_галочка_размышлений_их_включает(self):
        config.REASONING = True
        brain = _brain([[_chunk("Подумала и говорю."), _end("stop")]])
        list(brain.reply("Как дела?"))
        self.assertNotIn("extra_body", brain._client.bodies[0])

    def test_с_размышлениями_пустой_круг_переспрашивается_без_них(self):
        config.REASONING = True
        brain = _brain([
            [_think(), _end("length")],
            [_chunk("Коротко: дело в шине."), _end("stop")],
        ])
        said = list(brain.reply("Почему медленнее?"))
        self.assertEqual(said, ["Коротко: дело в шине."])
        self.assertNotIn("extra_body", brain._client.bodies[0])
        self.assertEqual(brain._client.bodies[1].get("extra_body"), brain_mod.NO_THINKING)

    def test_deepseek_отвечает_без_размышлений(self):
        brain = _brain([[_chunk("Нормально."), _end("stop")]])
        said = list(brain.reply("Как дела?"))
        self.assertEqual(said, ["Нормально."])
        тело = brain._client.bodies[0]
        self.assertEqual(тело.get("extra_body"), brain_mod.NO_THINKING)
        self.assertEqual(тело["max_tokens"], 220)

    def test_другой_провайдер_без_поля(self):
        brain = _brain([[_chunk("Нормально."), _end("stop")]])
        brain.provider = brain._home = "minimax"
        list(brain.reply("Как дела?"))
        self.assertNotIn("extra_body", brain._client.bodies[0])

    def test_не_принял_поле_убираем_и_запоминаем(self):
        brain = _brain([[_chunk("Раз."), _end("stop")], [_chunk("Два."), _end("stop")]])
        настоящий = brain._client.chat.completions.create
        отказали = []

        def создать(**тело):
            if "extra_body" in тело:
                отказали.append(True)
                raise _отказ("unknown parameter: thinking")
            return настоящий(**тело)

        brain._client.chat.completions.create = создать
        self.assertEqual(list(brain.reply("Как дела?")), ["Раз."])
        self.assertEqual(list(brain.reply("А теперь?")), ["Два."])
        self.assertEqual(отказали, [True])
        self.assertFalse(brain._quiet)

    def test_потолок_ушёл_в_размышления_переспрашиваем(self):
        brain = _brain([
            [_think(), _think(), _end("length")],
            [_chunk("Видеопамять тут ни при чём."), _end("stop")],
        ])
        события = []
        brain.on_event = lambda kind, data: события.append((kind, data))
        said = list(brain.reply("Почему на 16 ГБ медленнее, чем на 12?"))
        self.assertEqual(said, ["Видеопамять тут ни при чём."])
        повтор = brain._client.bodies[1]
        # Повтор — с запасом потолка и без размышлений.
        self.assertEqual(повтор["max_tokens"], brain_mod.ROOMY_TOKENS)
        self.assertEqual(повтор.get("extra_body"), brain_mod.NO_THINKING)
        self.assertIn("empty_retry", [kind for kind, _ in события])
        # В историю — сказанное, а не пустота.
        self.assertEqual(brain._history[-1]["content"], "Видеопамять тут ни при чём.")

    def test_и_повтор_пустой_честная_фраза(self):
        brain = _brain([
            [_think(), _end("length")],
            [_think(), _end("length")],
        ])
        said = list(brain.reply("Объясни подробно."))
        self.assertEqual(said, [brain_mod.EMPTY_LAST])
        self.assertEqual(len(brain._client.bodies), 2)

    def test_обычный_ответ_без_повтора(self):
        brain = _brain([[_chunk("Ага."), _end("stop")]])
        list(brain.reply("Ну да."))
        self.assertEqual(len(brain._client.bodies), 1)

    def test_журнал_пишет_повтор(self):
        from ui.web_runtime import WebRuntime

        строки = WebRuntime._log_messages("empty_retry", {"limit": 220, "thought": 900})
        self.assertIn("размышления", " ".join(строки))
        self.assertIn("220", " ".join(строки))


class ПотолокTests(unittest.TestCase):
    """Потолок по умолчанию 500; прежние 220 поднимаются при обновлении один раз."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from core import settings

        self.settings = settings
        self.путь = Path(tempfile.mkdtemp()) / "settings.json"
        patcher = mock.patch.object(settings, "SETTINGS_PATH", self.путь)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _файл(self, **поля):
        self.путь.write_text(json.dumps(поля), encoding="utf-8")

    def test_по_умолчанию_500(self):
        # `config.MAX_TOKENS` другие тесты могли переписать настройками —
        # смотрим на значения по умолчанию.
        self.assertEqual(self.settings.DEFAULTS["max_tokens"], 500)
        self.assertEqual(self.settings.load_settings()["max_tokens"], 500)

    def test_прежние_220_поднимаются(self):
        self._файл(max_tokens=220)
        self.assertEqual(self.settings.load_settings()["max_tokens"], 500)

    def test_свой_потолок_не_трогаем(self):
        self._файл(max_tokens=800)
        self.assertEqual(self.settings.load_settings()["max_tokens"], 800)

    def test_220_выбранные_после_подъёма_остаются(self):
        self._файл(max_tokens=220)
        self.settings.save_settings({"max_tokens": 220})
        self.assertEqual(self.settings.load_settings()["max_tokens"], 220)

    def test_размышления_выключены_по_умолчанию(self):
        self.assertFalse(self.settings.load_settings()["reasoning"])


if __name__ == "__main__":
    unittest.main()
