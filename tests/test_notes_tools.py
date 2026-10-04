"""Инструменты модели для заметок: `save_note` и `read_notes`.

Ни сети, ни файлов хозяина: папка заметок временная, а «облако» — подставной
клиент, который отдаёт заготовленные круги и запоминает тела запросов.
Проверяется три вещи: набор и порядок инструментов, короткий путь записи без
второго круга и честный ответ на несуществующую тему.

Не здесь видно, как настоящая модель формулирует текст заметки.
"""

import json
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from say_helpers import assert_said
from core import abilities, hands, notes
from core.brain import Brain

APPS = [{"id": "youtube", "title": "YouTube", "kind": "url",
         "url": "https://youtube.com"}]


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


def _tool_call(name, args="{}", call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(streams)
    brain._reply_lock = threading.RLock()
    brain._history = deque(maxlen=10)
    brain._persona = "тест"
    brain._abilities = lambda: ""
    brain._memory = lambda: ""
    brain._keep_history = lambda: None
    brain._undigested = 0
    brain._tools_ok = None
    brain._quiet = None
    brain._usage_ok = None
    brain.on_event = None
    brain.actions = {}
    brain.last_sources = []
    return brain


def _names(brain, index=0):
    return [t["function"]["name"]
            for t in brain._client.bodies[index].get("tools", [])]


class NoteToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-tools-"))
        self._saved = (config.NOTES_DIR, config.WEB_SEARCH, config.TTS_ENGINE)
        config.NOTES_DIR = str(self.tmp)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        # Заметка с тем же текстом не пишется второй раз за 10 минут.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(hands, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in zip(("NOTES_DIR", "WEB_SEARCH", "TTS_ENGINE"),
                               self._saved):
            self.addCleanup(lambda n=name, v=value: setattr(config, n, v))

    def test_both_note_tools_sit_right_after_youtube(self):
        # Порядок набора стабилен ради кеша запроса, и заметки стоят сразу
        # за YouTube: их набор не зависит от `apps.json`.
        brain = _brain([[_chunk("Ок.")]])
        list(brain.reply("как дела?"))
        self.assertEqual(_names(brain)[:5],
                         [hands.NAME, hands.CLOSE_NAME, hands.YT_NAME,
                          hands.NOTE_NAME, hands.READ_NAME])

    def test_the_tools_are_described_for_the_model(self):
        for spec, must in ((hands.NOTE_TOOL, ("текст", "замет")),
                           (hands.READ_TOOL, ("теме", "замет"))):
            with self.subTest(инструмент=spec["function"]["name"]):
                text = spec["function"]["description"].lower()
                for word in must:
                    self.assertIn(word, text)
        self.assertIn("заметки", abilities.describe(APPS).lower())

    def test_writing_ends_without_a_second_round(self):
        # Заметка пишется на месте, а второй круг в облако ушёл бы ради
        # одного слова «записала» — как у YouTube.
        args = json.dumps({"text": "Он спросил, как дела.",
                           "because": "записать то, что ты сейчас сказала"},
                          ensure_ascii=False)
        brain = _brain([_tool_call(hands.NOTE_NAME, args)])
        said = list(brain.reply("а ты можешь записать то, что ты сейчас сказала"))
        self.assertEqual(len(brain._client.bodies), 1, brain._client.bodies)
        self.assertEqual(len(said), 1)
        assert_said(self, said[0], "note_saved", name="Разное — Входящие")
        body = (self.tmp / "Разное" / "Входящие.md").read_text(encoding="utf-8")
        self.assertIn("Он спросил, как дела.", body)
        # Запись от модели: сырого диктовки нет, и свёртки быть не должно.
        self.assertNotIn("[!quote]", body)

    def test_a_note_is_only_written_when_he_asked_for_it(self):
        # Повтор прошлой просьбы: в этой фразе про заметки не говорят, и цитаты
        # из неё взять неоткуда.
        args = json.dumps({"text": "Пусто.", "because": "запиши это в заметки"},
                          ensure_ascii=False)
        brain = _brain([_tool_call(hands.NOTE_NAME, args), [_chunk("Про другое.")]])
        list(brain.reply("а кстати, что нового?"))
        сообщения = brain._client.bodies[1]["messages"]
        answer = json.loads(next(m["content"] for m in reversed(сообщения)
                                 if m.get("role") == "tool"))
        self.assertIn("error", answer)
        self.assertFalse((self.tmp / "Разное" / "Входящие.md").exists())

    def test_an_empty_note_is_refused(self):
        self.assertIn("error", json.loads(hands.run_note(json.dumps({"text": "  "}))))

    def test_reading_hands_the_text_to_the_model(self):
        notes.add("Книги", "Мастер и Маргарита", "Воланд", "Зеркало.")
        args = json.dumps({"query": "книга Мастер и Маргарита",
                           "because": "что я писал по книге"},
                          ensure_ascii=False)
        brain = _brain([_tool_call(hands.READ_NAME, args), [_chunk("Ты писал…")]])
        list(brain.reply("что я писал по книге?"))
        # Обычный второй круг: модель сама пересказывает хозяину.
        self.assertEqual(len(brain._client.bodies), 2)
        answer = json.loads(brain._client.bodies[1]["messages"][-1]["content"])
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["topic"], "Мастер и Маргарита")
        self.assertIn("Зеркало.", answer["text"])
        # Записи идут с датами: «что я говорил» без времени не ответить.
        self.assertRegex(answer["text"], r"## \d\d\.\d\d\.\d{4}, \d\d:\d\d")

    def test_a_missing_topic_gets_the_list_instead_of_invented_notes(self):
        notes.add("Книги", "Мастер и Маргарита", "Воланд", "Зеркало.")
        args = json.dumps({"query": "про ремонт карты",
                           "because": "что я писал про ремонт карты"},
                          ensure_ascii=False)
        brain = _brain([_tool_call(hands.READ_NAME, args), [_chunk("Нет такой.")]])
        list(brain.reply("что я писал про ремонт карты?"))
        answer = json.loads(brain._client.bodies[1]["messages"][-1]["content"])
        self.assertIn("error", answer)
        # Список существующих обязателен: иначе модель перескажет то, чего
        # хозяин не писал.
        self.assertIn("Мастер и Маргарита", answer["error"])

    def test_reading_without_a_topic_is_an_error(self):
        self.assertIn("error", json.loads(hands.run_read_notes("{}")))

    def test_broken_arguments_do_not_raise(self):
        self.assertIn("error", json.loads(hands.run_note("не json")))
        self.assertIn("error", json.loads(hands.run_read_notes("[1,2]")))

    def test_both_tools_answer_to_their_own_words(self):
        # Оба инструмента ждут цитату из последней реплики, а не узнавания
        # по корням слов.
        for фраза in ("запиши это в заметки", "сохрани то, что ты сказала"):
            with self.subTest(фраза=фраза):
                self.assertTrue(hands.asked_for(hands.NOTE_NAME, фраза, APPS,
                                                because=фраза))
        for фраза in ("что я писал по книге", "прочитай мои мысли по проекту"):
            with self.subTest(фраза=фраза):
                self.assertTrue(hands.asked_for(hands.READ_NAME, фраза, APPS,
                                                because=фраза))
        # Про цитату из прошлой реплики — ни одного из этих инструментов:
        # в этой фразе про заметки не говорят.
        for имя in (hands.NOTE_NAME, hands.READ_NAME):
            self.assertFalse(hands.asked_for(
                имя, "а кстати, что нового?", APPS, because="запиши это в заметки"))


if __name__ == "__main__":
    unittest.main()
