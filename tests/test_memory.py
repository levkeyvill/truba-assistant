"""Память второго захода: правки, слияние, важность, архив — на временном файле."""

import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from core import memory, voice_loop
from core.brain import Brain, memory_ops, own_turns
from core.voice_loop import VoiceLoop
from ui.web_runtime import WebRuntime


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(memory, "MEMORY_PATH", Path(self._dir.name) / "memory.json")
        self._patch.start()
        memory.save_facts([
            {"text": "Взял RTX 5070 Ti.", "added": "2026-09-01T10:00", "weight": 3},
            {"text": "Играет в Тарков.", "added": "2026-09-02T10:00"},
            {"text": "Любит Escape from Tarkov.", "added": "2026-09-03T10:00"},
            {"text": "Есть кот Борис.", "added": "2026-09-04T10:00", "weight": 3},
        ], [])

    def tearDown(self):
        self._patch.stop()
        self._dir.cleanup()

    def texts(self):
        return [f["text"] for f in memory.load_facts()]

    def test_replace_keeps_old_in_archive(self):
        changed = memory.apply_changes([
            {"op": "update", "id": 1, "text": "Продал RTX 5070 Ti, теперь сидит на 4070."}])
        self.assertEqual(changed["updated"], [["Взял RTX 5070 Ti.", "Продал RTX 5070 Ti, теперь сидит на 4070."]])
        self.assertEqual(self.texts()[0], "Продал RTX 5070 Ti, теперь сидит на 4070.")
        self.assertEqual(memory.load_facts()[0]["weight"], 3)
        archive = memory.load_forgotten()
        self.assertEqual(archive[-1]["text"], "Взял RTX 5070 Ti.")
        self.assertIn("заменена", archive[-1]["why"])

    def test_merge_duplicates(self):
        changed = memory.apply_changes([
            {"op": "update", "id": 2, "text": "Играет в Escape from Tarkov и любит его."},
            {"op": "remove", "id": 3, "why": "дубль"},
        ])
        self.assertEqual(self.texts(), ["Взял RTX 5070 Ti.", "Играет в Escape from Tarkov и любит его.",
                                        "Есть кот Борис."])
        self.assertEqual(changed["removed"], ["Любит Escape from Tarkov."])

    def test_update_into_existing_text_is_merge(self):
        memory.apply_changes([{"op": "update", "id": 3, "text": "Играет в Тарков."}])
        self.assertEqual(self.texts().count("Играет в Тарков."), 1)
        self.assertEqual(len(self.texts()), 3)

    def test_removals_are_limited(self):
        changed = memory.apply_changes([{"op": "remove", "id": i} for i in (1, 2, 3, 4)])
        self.assertEqual(len(changed["removed"]), memory.MAX_REMOVALS)
        self.assertEqual(len(self.texts()), 4 - memory.MAX_REMOVALS)

    def test_bad_ops_are_ignored(self):
        before = self.texts()
        changed = memory.apply_changes([
            {"op": "update", "id": 99, "text": "x"}, {"op": "remove", "id": "abc"},
            {"op": "add", "text": "ок"}, {"op": "add", "text": "Играет в Тарков!"},
            "мусор", {"op": "explode"}])
        self.assertEqual(changed, {})
        self.assertEqual(self.texts(), before)

    def test_ids_refer_to_list_before_changes(self):
        memory.apply_changes([
            {"op": "remove", "id": 1},
            {"op": "update", "id": 4, "text": "Есть кот Борис, рыжий."},
        ])
        self.assertEqual(self.texts()[-1], "Есть кот Борис, рыжий.")
        self.assertNotIn("Взял RTX 5070 Ti.", self.texts())

    def test_prompt_prefers_important_when_full(self):
        facts = [{"text": f"Мелочь номер {i}.", "weight": 1} for i in range(70)]
        facts.insert(0, {"text": "Кот Борис.", "weight": 3})
        memory.save_facts(facts, [])
        with mock.patch.object(memory, "FACTS_IN_PROMPT", 10):
            prompt = memory.as_prompt()
        self.assertIn("Кот Борис.", prompt)
        self.assertIn("Мелочь номер 69.", prompt)
        self.assertNotIn("Мелочь номер 0.", prompt)

    def test_manual_edit_keeps_weight(self):
        memory.from_text("Есть кот Борис.\nНовый факт руками.")
        facts = memory.load_facts()
        self.assertEqual(facts[0]["weight"], 3)
        self.assertEqual(facts[1]["weight"], memory.DEFAULT_WEIGHT)


class DigestTests(unittest.TestCase):
    def test_full_history_still_digests_new_turns(self):
        """Регрессия: история упёрлась в потолок — новые реплики всё равно разбираются."""
        brain = object.__new__(Brain)
        brain._reply_lock = threading.RLock()
        brain._history = deque(({"role": "user", "content": f"старое {i}"} for i in range(4)), maxlen=4)
        brain._undigested = 0
        brain._keep_history = lambda: None
        brain._add_turn("Я продал свою видеокарту и купил другую, попроще, на время.",
                        "Ну и правильно, кожаный, деньги нужнее.")
        seen = []
        brain._ask_plainly = lambda ask, *a, **k: seen.append(ask) or NS(
            choices=[NS(message=NS(content='{"ops": []}'), finish_reason="stop")])
        with mock.patch.object(memory, "numbered", return_value=""),                 mock.patch.object(memory, "apply_changes", return_value={}):
            brain.digest()
        self.assertEqual(len(seen), 1)
        self.assertIn("продал свою видеокарту", seen[0])
        self.assertNotIn("старое", seen[0])
        # Второй раз разбирать нечего — в облако не ходим.
        brain.digest()
        self.assertEqual(len(seen), 1)

    def test_foreign_voice_is_not_remembered(self):
        turns = [
            {"role": "user", "content": "Стример: я купил новую мышку", "voice": 0.08},
            {"role": "assistant", "content": "Круто."},
            {"role": "user", "content": "Я сам купил клавиатуру", "voice": 0.66},
            {"role": "assistant", "content": "Показывай."},
            {"role": "user", "content": "Напечатано в чате"},
            {"role": "assistant", "content": "Ок."},
        ]
        kept = own_turns(turns, 0.35)
        self.assertEqual([t["content"] for t in kept],
                         ["Я сам купил клавиатуру", "Показывай.", "Напечатано в чате", "Ок."])
        # Порог 0 — отбор выключен, всё остаётся.
        self.assertEqual(len(own_turns(turns, 0.0)), 6)

    def test_voice_score_goes_to_history_not_to_api(self):
        brain = object.__new__(Brain)
        brain._reply_lock = threading.RLock()
        brain._history = deque(maxlen=10)
        brain._undigested = 0
        brain._keep_history = lambda: None
        brain._add_turn("привет", "здорово", 0.614)
        self.assertEqual(brain._history[0]["voice"], 0.61)
        brain._persona = "т"
        brain._abilities = lambda: ""
        brain._memory = lambda: ""
        self.assertTrue(all(set(m) == {"role", "content"} for m in brain._messages("ещё", None)))

    def test_ops_parser(self):
        self.assertEqual(memory_ops('```json\n{"ops": [{"op": "add", "text": "x"}]}\n```'),
                         [{"op": "add", "text": "x"}])
        self.assertEqual(memory_ops('[{"op": "remove", "id": 2}]'), [{"op": "remove", "id": 2}])
        self.assertEqual(memory_ops("НЕТ"), [])
        self.assertEqual(memory_ops('{"ops": "мусор"}'), [])

    def test_digest_asks_json_and_applies(self):
        brain = object.__new__(Brain)
        brain.provider = "openai"
        brain._home = "openai"
        brain._model = "test"
        brain._quiet = None
        brain._history = deque([
            {"role": "user", "content": "Слушай, я продал свою 5070 Ti, теперь на 4070 сижу, пока денег нет."},
            {"role": "assistant", "content": "Ну ты даёшь, кожаный. Зато на пиво останется."},
        ])
        brain._undigested = 2
        brain._reply_lock = threading.RLock()
        bodies = []

        def create(**body):
            bodies.append(body)
            content = '{"ops": [{"op": "update", "id": 1, "text": "Продал 5070 Ti, сидит на 4070."}]}'
            return NS(choices=[NS(message=NS(content=content), finish_reason="stop")])

        brain._client = NS(chat=NS(completions=NS(create=create)))
        with mock.patch.object(memory, "numbered", return_value="1. Взял RTX 5070 Ti. [важность 3]"), \
                mock.patch.object(memory, "apply_changes", return_value={"updated": [["a", "b"]]}) as apply:
            changed = brain.digest()
        self.assertEqual(changed, {"updated": [["a", "b"]]})
        self.assertEqual(apply.call_args[0][0], [{"op": "update", "id": 1, "text": "Продал 5070 Ti, сидит на 4070."}])
        self.assertEqual(bodies[0]["response_format"], {"type": "json_object"})
        self.assertEqual(bodies[0]["reasoning_effort"], "none")
        self.assertIn("1. Взял RTX 5070 Ti.", bodies[0]["messages"][0]["content"])

    def test_digest_says_whether_it_asked(self):
        """Пустой словарь значит две разные вещи — их надо различать."""
        brain = object.__new__(Brain)
        brain._reply_lock = threading.RLock()
        brain._history = deque(maxlen=10)
        brain._undigested = 0
        brain.did_ask = False
        # Подмена пути не даёт `_add_turn` писать в рабочую историю.
        brain._keep_history = lambda: None
        asked = []
        brain._ask_plainly = lambda ask, *a, **k: asked.append(ask) or NS(
            choices=[NS(message=NS(content='{"ops": []}'), finish_reason="stop")])
        with mock.patch.object(memory, "numbered", return_value=""), \
                mock.patch.object(memory, "apply_changes", return_value={}):
            # Разговор короткий: до запроса не дошли.
            brain._add_turn("привет", "здорово")
            self.assertEqual(brain.digest(), {})
            self.assertFalse(brain.did_ask)
            self.assertEqual(asked, [])

            # Разговор длинный: спросила, нового нет.
            brain._add_turn("Я переехал в новую квартиру и купил там кота, зовут Мурзик.",
                            "Поздравляю, Мурзик — отличное имя для кота.")
            self.assertEqual(brain.digest(), {})
            self.assertTrue(brain.did_ask)
            self.assertEqual(len(asked), 1)


class MemoryCheckedEventTests(unittest.TestCase):
    """Видно, что разбор памяти был, даже когда ничего не изменилось."""

    def loop_for(self, brain):
        loop = object.__new__(VoiceLoop)
        loop._brain = brain
        loop.events = []
        loop._emit = lambda kind, payload: loop.events.append((kind, payload))
        return loop

    def digest_events(self, learned, did_ask):
        brain = mock.Mock()
        brain.digest.return_value = learned
        brain.did_ask = did_ask
        loop = self.loop_for(brain)
        with mock.patch.object(voice_loop, "threading") as threads:
            loop._digest_later()
            threads.Thread.assert_called_once()
            threads.Thread.call_args.kwargs["target"]()  # поток выполняем сразу
        return loop.events

    def test_checked_is_emitted_when_nothing_new(self):
        self.assertEqual(self.digest_events({}, True), [("memory_checked", None)])

    def test_nothing_is_emitted_when_did_not_ask(self):
        self.assertEqual(self.digest_events({}, False), [])

    def test_remembered_still_wins(self):
        learned = {"added": ["Взял RTX 5070 Ti."]}
        self.assertEqual(self.digest_events(learned, True), [("remembered", learned)])

    def test_error_is_reported_and_nothing_else(self):
        brain = mock.Mock()
        brain.digest.side_effect = RuntimeError("провайдер не отвечает")
        brain.did_ask = False
        loop = self.loop_for(brain)
        with mock.patch.object(voice_loop, "threading") as threads:
            loop._digest_later()
            threads.Thread.call_args.kwargs["target"]()
        self.assertEqual([k for k, _ in loop.events], ["error"])
        self.assertIn("память: RuntimeError", loop.events[0][1])

    def test_label_for_the_pult(self):
        self.assertEqual(
            WebRuntime._log_messages("memory_checked", None),
            ["память: разобрала разговор, нового о тебе нет"])


if __name__ == "__main__":
    unittest.main()
