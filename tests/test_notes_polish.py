"""Причёсывание надиктованного: `core/notes.py::polish`.

Облака тут нет: `_ask_plainly` подменён заглушкой, которая помнит промпт и
отдаёт заготовленный ответ (или поднимает ошибку, как молчащее облако).
Проверяется ровно три вещи: разбор ответа, что модели показаны существующие
темы и что надиктованное не теряется, даже если облако не ответило.

Не здесь видно, сколько на живой машине секунд занимает причёсывание и как
облако отвечает на такой промпт в среднем.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

import config
from core import notes

SAID = "ну значит волданд это типа зеркало всего и вот ещё что я хотел сказать"


class _Облако:
    """Заглушка `brain._ask_plainly`: пишет промпты и отдаёт заготовку."""

    def __init__(self, answer="", error=None):
        self.answer = answer
        self.error = error
        self.prompts = []

    def _ask_plainly(self, prompt, max_tokens, json_mode=False):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return NS(choices=[NS(message=NS(content=self.answer))])


def _ответ(**fields):
    return json.dumps(fields, ensure_ascii=False)


class PolishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-polish-"))
        self._saved = config.NOTES_DIR
        config.NOTES_DIR = str(self.tmp)
        self.addCleanup(lambda: setattr(config, "NOTES_DIR", self._saved))

    def test_good_json_becomes_section_topic_title_and_text(self):
        brain = _Облако(_ответ(section="Книги", topic="Мастер и Маргарита",
                              title="Воланд как зеркало",
                              text="Воланд — зеркало того, кто на него смотрит."))
        got = notes.polish(brain, SAID, "запиши мысль по книге")
        self.assertEqual(got, {
            "section": "Книги", "topic": "Мастер и Маргарита",
            "title": "Воланд как зеркало",
            "text": "Воланд — зеркало того, кто на него смотрит.",
            "raw": False,
        })

    def test_json_wrapped_in_a_fence_is_still_read(self):
        # Модель regularly оборачивает JSON в ```json — это не поломка.
        brain = _Облако('```json\n' + _ответ(
            section="Идеи", topic="Кофе", title="Про кофе", text="Первый.")
            + '\n```')
        got = notes.polish(brain, SAID)
        self.assertEqual(got["section"], "Идеи")
        self.assertFalse(got["raw"])

    def test_existing_topics_are_shown_to_the_model(self):
        known = [{"name": "Книги", "topics": ["Мастер и Маргарита"]},
                 {"name": "Проекты", "topics": ["Труба"]}]
        brain = _Облако(_ответ(section="Книги", topic="Мастер и Маргарита",
                              title="Тема", text="Текст."))
        notes.polish(brain, SAID, "запиши мысль по книге", known)
        prompt = brain.prompts[0]
        self.assertIn("Книги: Мастер и Маргарита", prompt)
        self.assertIn("Проекты: Труба", prompt)
        # Новую тему модель всё равно может назвать: список — подсказка, а не
        # белый список.
        self.assertIn("придумай новые", prompt)

    def test_empty_list_of_known_topics_does_not_break_the_prompt(self):
        brain = _Облако(_ответ(section="Разное", topic="Входящие",
                              title="Тема", text="Текст."))
        notes.polish(brain, SAID, "", notes.known_topics())
        self.assertIn("(пока пусто)", brain.prompts[0])

    def test_the_command_is_a_hint_and_never_gets_into_the_note(self):
        brain = _Облако(_ответ(section="Книги", topic="Мастер и Маргарита",
                              title="Тема", text="Текст."))
        notes.polish(brain, SAID, "запиши мысль по книге Мастер и Маргарита")
        prompt = brain.prompts[0]
        self.assertIn("запиши мысль по книге Мастер и Маргарита", prompt)
        # Надиктованное в промпте — ровно то, что он сказал голосом.
        self.assertIn(SAID, prompt)

    def test_the_prompt_forbids_adding_anything_of_its_own(self):
        # Главный страх хозяина: диктовка причёсывается, а модель от себя
        # добавляет «отличная мысль!» — и в заметке появляется то, чего он
        # не говорил. Промпт обязан это запрещать прямо.
        brain = _Облако(_ответ(section="Идеи", topic="Кофе",
                              title="Тема", text="Текст."))
        notes.polish(brain, SAID)
        prompt = brain.prompts[0].lower()
        self.assertIn("ничего не добавляй", prompt)
        self.assertIn("не меняй смысл", prompt)
        self.assertIn("не сокращай", prompt)

    def test_broken_json_is_written_as_it_was_spoken(self):
        brain = _Облако("ой, да я не знаю, извини")
        got = notes.polish(brain, SAID)
        self.assertEqual(got["section"], "Разное")
        self.assertEqual(got["topic"], "Входящие")
        self.assertEqual(got["title"], "Без обработки")
        self.assertEqual(got["text"], SAID)
        self.assertTrue(got["raw"])

    def test_an_empty_note_is_treated_as_no_answer(self):
        # Надиктованное было, а текста в ответе нет: это не заметка, и терять
        # надиктованное нельзя — пишем сырое.
        brain = _Облако(_ответ(section="Книги", topic="Тема", title="Тема",
                              text="   "))
        got = notes.polish(brain, SAID)
        self.assertTrue(got["raw"])
        self.assertEqual(got["text"], SAID)

    def test_a_cloud_that_threw_keeps_the_dictation(self):
        brain = _Облако(error=RuntimeError("облако молчит"))
        got = notes.polish(brain, SAID)
        self.assertTrue(got["raw"])
        self.assertEqual(got["text"], SAID)
        self.assertEqual(got["section"], "Разное")

    def test_without_a_brain_the_dictation_is_kept_too(self):
        # Мозга в этой сборке нет, а мысль всё равно нельзя выбрасывать.
        got = notes.polish(None, SAID)
        self.assertTrue(got["raw"])
        self.assertEqual(got["text"], SAID)

    def test_names_from_the_model_are_cleaned_like_any_other(self):
        brain = _Облако(_ответ(section="Книги:Мир", topic="Мастер/Маргарита #1",
                              title="Тема", text="Текст."))
        got = notes.polish(brain, SAID)
        self.assertEqual(got["section"], "Книги Мир")
        self.assertEqual(got["topic"], "Мастер Маргарита 1")

    def test_a_model_trying_to_escape_the_folder_gets_nothing_of_its_own(self):
        # Модель назвала разделом путь вверх. Помещать это некуда, и тихо
        # починить мысль нельзя: сырое в «Разное / Входящие».
        brain = _Облако(_ответ(section="..", topic="Побег",
                              title="Тема", text="Хитрость."))
        got = notes.polish(brain, SAID)
        self.assertTrue(got["raw"])
        self.assertEqual(got["text"], SAID)

        brain = _Облако(_ответ(section="..", topic="Побег",
                              title="Тема", text="Хитрость."))
        got = notes.polish(brain, SAID)
        self.assertTrue(got["raw"])
        self.assertEqual(got["text"], SAID)

    def test_the_note_is_written_with_the_dictation_in_a_quoted_block(self):
        # Путь целиком: причёсанное пишется текстом заметки, а надиктованное
        # уходит в свою свёртку «Как было сказано» — распознавание могло
        # исказить, и оригинал должен лежать рядом.
        notes.add("Книги", "Тема", "Воланд как зеркало", "Причёсанный текст.",
                  raw=SAID)
        body = (self.tmp / "Книги" / "Тема.md").read_text(encoding="utf-8")
        self.assertIn("Причёсанный текст.", body)
        self.assertIn("> [!quote]- Как было сказано", body)
        self.assertIn(f"> {SAID}", body)

    def test_a_raw_note_has_no_quote_of_itself(self):
        # В «Разное / Входящие» сырое и есть текст заметки: повторять его же
        # в свёртке незачем, заметка не должна удваиваться в размере.
        got = notes._as_is(SAID)
        path = notes.add(got["section"], got["topic"], got["title"],
                         got["text"], raw="")
        body = path.read_text(encoding="utf-8")
        self.assertNotIn("[!quote]", body)
        self.assertIn(SAID, body)


if __name__ == "__main__":
    unittest.main()
