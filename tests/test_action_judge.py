"""Проверка смысла просьбы перед действиями модели.

`asked_for` подтверждает только цитату из текущей реплики; упоминание
программы или экрана ещё не разрешает действие. Судья отдельно проверяет
запуск, закрытие, снимок и взгляд на экран.

`_ask_plainly` и запуск программ подменены: тесты проверяют ветвление и
отказы без обращения к облаку или настоящим программам.
"""

import json
import threading
import unittest
from collections import deque
from datetime import datetime
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import hands
from core.brain import Brain
from ui.web_runtime import WebRuntime

APPS = [{"id": "youtube", "title": "YouTube", "kind": "url",
         "url": "https://youtube.com"},
        {"id": "discord", "title": "Discord", "kind": "app",
         "path": "Discord.exe"}]
# Discord здесь назван как место разговора, без просьбы открыть программу.
ФРАЗА = ("Ладно, болтай с ними тогда. Они в Discord'е разговаривать будут. "
         "Тебе же не сложно с ними поговорить, правда?")


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
    brain.actions = {"screenshot": lambda: "готово, снимок на телефоне: a.jpg",
                     "look": lambda: "data:image/jpeg;base64,КАРТИНКА"}
    brain.last_sources = []
    return brain


def _судья(brain, ответ="нет", падает=False):
    """Подмена облака для проверки: пишет промпты, отдаёт заготовку.

    `падает=True` имитирует отсутствие ответа; без решения действие запрещено.
    """
    спросили = []

    def спросить(prompt, *args, **kwargs):
        спросили.append((prompt, kwargs))
        if падает:
            raise RuntimeError("облако молчит")
        return NS(choices=[NS(message=NS(content=ответ))])

    brain._ask_plainly = спросить
    return спросили


def _запуск(инструмент=hands.NAME, аргументы=None):
    """Круг, где модель зовёт действие, и круг, где она отвечает."""
    if аргументы is None:
        аргументы = {"app": "discord",
                     "because": "Они в Discord'е разговаривать будут"}
    return _brain([_tool_call(инструмент,
                             json.dumps(аргументы, ensure_ascii=False)),
                   [_chunk("Поняла.")]])


def _последний_ответ(brain):
    """Что ушло модели в последнем круге: ответ её инструмента."""
    сообщения = brain._client.bodies[-1]["messages"]
    return json.loads(next(m["content"] for m in reversed(сообщения)
                           if m.get("role") == "tool"))


class Обстановка(unittest.TestCase):
    """Ни интернета, ни настоящих программ, ни памяти о прошлых действиях."""

    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        self.addCleanup(lambda: setattr(config, "WEB_SEARCH", self._saved))
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(hands, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)


class СудьяРешает(Обстановка):
    """Три ответа судьи: «нет», «да» и «не ответил»."""

    def test_a_mention_is_not_a_request(self):
        brain = _запуск()
        _судья(brain, "нет")
        with mock.patch.object(hands, "run_tool") as запуск:
            list(brain.reply(ФРАЗА))
        запуск.assert_not_called()
        self.assertIn("не упоминай это действие и проверку",
                      _последний_ответ(brain)["error"])

    def test_after_a_refusal_she_answers_without_new_tries(self):
        # Отклонённое действие — не повод пробовать другое: каждая попытка
        # стоит 2–3 с, и он ни о чём таком не просил. Следующий круг — без
        # инструментов и с просьбой просто ответить по смыслу.
        brain = _запуск()
        _судья(brain, "нет")
        with mock.patch.object(hands, "run_tool"):
            list(brain.reply(ФРАЗА))
        последний = brain._client.bodies[-1]
        self.assertNotIn("tools", последний)
        self.assertIn("Действия тут не нужны", последний["messages"][-1]["content"])

    def test_a_direct_request_is_done(self):
        # Судья ответил «Да.» — с точкой, как умеет отвечать модель.
        brain = _запуск()
        _судья(brain, "Да.")
        with mock.patch.object(hands, "run_tool",
                               return_value='{"ok": true}') as запуск:
            list(brain.reply(ФРАЗА))
        запуск.assert_called_once()

    def test_a_silent_judge_means_do_not_do_it(self):
        brain = _запуск()
        _судья(brain, падает=True)
        with mock.patch.object(hands, "run_tool") as запуск:
            list(brain.reply(ФРАЗА))
        запуск.assert_not_called()
        self.assertIn("не получилось проверить",
                      _последний_ответ(brain)["error"])


class КогоСпрашиваем(Обстановка):
    """Судью зовут не всегда, а только там, где это нужно."""

    # Снимок и взгляд идут через `run_action`, а не через `run_tool`: судим по
    # нему, иначе проверка «действие не выполнено» ничего бы не замечала.
    ЭКРАН = ((hands.SHOT_NAME, "сделай скрин, пожалуйста", "сделай скрин"),
             (hands.LOOK_NAME, "а что у меня на экране?", "что у меня на экране"))

    def _круг_со_снимком(self, инструмент, фраза, цитата, ответ):
        """Модель зовёт действие с экрана, судья отвечает заданным словом."""
        brain = _запуск(инструмент, {"because": цитата})
        спросили = _судья(brain, ответ)
        with mock.patch.object(hands, "run_action",
                               return_value="готово, снимок на телефоне") as действие:
            list(brain.reply(фраза))
        return brain, спросили, действие

    def test_the_screen_is_judged_too(self):
        # Упоминание памяти программы не разрешает снимок или взгляд на экран;
        # оба действия требуют решения судьи.
        for инструмент, фраза, цитата in self.ЭКРАН:
            with self.subTest(инструмент=инструмент):
                brain, спросили, действие = self._круг_со_снимком(
                    инструмент, фраза, цитата, "нет")
                self.assertEqual(len(спросили), 1, "судью не спросили")
                действие.assert_not_called()
                self.assertIn("не упоминай это действие и проверку",
                              _последний_ответ(brain)["error"])

    def test_an_asked_screenshot_is_taken(self):
        # Ответ «да» — и действие сделано: судья не мешает прямой просьбе.
        for инструмент, фраза, цитата in self.ЭКРАН:
            with self.subTest(инструмент=инструмент):
                _brain, _спросили, действие = self._круг_со_снимком(
                    инструмент, фраза, цитата, "да")
                действие.assert_called_once()

    def test_a_missed_quote_is_never_judged(self):
        # Цитаты из реплики нет — до судьи дело не доходит: `asked_for`
        # отклоняет действие до отдельного запроса в облако.
        brain = _запуск(аргументы={"app": "discord", "because": "открыть Discord"})
        спросили = _судья(brain, "да")
        with mock.patch.object(hands, "run_tool") as запуск:
            list(brain.reply("М-м, кстати, знаешь, что ещё надо?"))
        запуск.assert_not_called()
        self.assertEqual(спросили, [])

    def test_the_judge_sees_the_phrase_and_the_action_in_words(self):
        brain = _запуск()
        спросили = _судья(brain, "нет")
        list(brain.reply(ФРАЗА))
        self.assertEqual(len(спросили), 1)
        промпт = спросили[0][0]
        self.assertIn(ФРАЗА, промпт)
        self.assertIn("запустить программу discord", промпт)
        # Короткий вопрос: 5 токенов ответа и 5 секунд на облако.
        self.assertEqual(спросили[0][1]["max_tokens"], 5)
        self.assertEqual(спросили[0][1]["timeout"], 8.0)

    def test_followup_to_another_task_cannot_create_a_reminder(self):
        brain = _запуск(hands.SET_REM_NAME, {
            "because": "с первого туда улетаем", "kind": "reminder",
            "at": "2026-09-30T22:03", "text": "посмотреть авиабилеты"})
        now = datetime.now().isoformat(timespec="seconds")
        brain._history.extend([
            {"role": "user", "content": "Посмотри стоимость билетов туда и обратно.",
             "at": now},
            {"role": "assistant", "content": "На какие даты?", "at": now},
        ])
        prompts = _судья(brain, "нет")
        with mock.patch.object(hands, "run_set_reminder") as create:
            list(brain.reply("С первого туда улетаем, второго обратно."))
        create.assert_not_called()
        self.assertEqual(len(prompts), 1)
        self.assertIn("На какие даты?", prompts[0][0])
        self.assertIn("стоимость билетов", prompts[0][0])
        self.assertIn("поставить напоминание", prompts[0][0])

    def test_explicit_reminder_is_allowed_after_semantic_check(self):
        brain = _запуск(hands.SET_REM_NAME, {
            "because": "напомни через двадцать минут", "kind": "reminder",
            "seconds": 1200, "text": "налить кофе"})
        prompts = _судья(brain, "да")
        with mock.patch.object(hands, "run_set_reminder",
                               return_value='{"ok":true,"text":"Напомню."}') as create:
            list(brain.reply("Напомни через двадцать минут налить кофе."))
        create.assert_called_once()
        self.assertEqual(len(prompts), 1)

    def test_mention_does_not_cancel_a_reminder(self):
        brain = _запуск(hands.CANCEL_REM_NAME, {
            "because": "не про напоминание", "id": "r1"})
        _судья(brain, "нет")
        with mock.patch.object(hands, "run_cancel_reminder") as cancel:
            list(brain.reply("Я не про напоминание, а про другое дело."))
        cancel.assert_not_called()


class СловаСудьи(unittest.TestCase):
    """Разбор ответа судьи и действие словами — без сети и без модели."""

    def test_five_actions_are_judged(self):
        # Судья нужен для действий с окнами и экраном, передачи текста
        # документа или буфера модели и вопроса о питании компьютера.
        # Упоминание возможности выключения не должно запускать подтверждение.
        # Локальное сохранение момента через судью не проходит.
        self.assertEqual(hands.JUDGED, frozenset(
            {hands.NAME, hands.CLOSE_NAME, hands.YT_NAME,
             hands.SHOT_NAME, hands.LOOK_NAME, hands.SAVED_LOOK_NAME,
             hands.FOLDER_NAME, hands.SET_REM_NAME, hands.CANCEL_REM_NAME,
             hands.DOC_NAME, hands.FIND_NAME, hands.CLIP_NAME,
             hands.POWER_NAME, hands.CLOSE_DOC_NAME}))
        self.assertNotIn(hands.MOMENT_NAME, hands.JUDGED)

    def test_an_action_is_named_in_words(self):
        for имя, аргументы, ждём in (
            (hands.NAME, {"app": "discord"}, "запустить программу discord"),
            (hands.CLOSE_NAME, {"app": "discord"}, "закрыть программу discord"),
            (hands.YT_NAME, {"query": "борщ"}, "включить на YouTube ролик: борщ"),
            (hands.YT_NAME, {"query": "борщ", "kind": "channel"},
             "открыть на YouTube канал: борщ"),
            (hands.YT_NAME, {"query": "борщ", "kind": "search"},
             "показать поиск на YouTube: борщ"),
            # Словами, а не именем инструмента: судья «take_screenshot» и
            # «look_at_screen» читать не станет, а «посмотреть на его экран
            # (снимок уйдёт тебе в облако)» — прочитает.
            (hands.SHOT_NAME, {}, "сделать снимок экрана и показать его на телефоне"),
            (hands.LOOK_NAME, {}, "посмотреть на его экран (снимок экрана уйдёт тебе в облако)"),
            (hands.SAVED_LOOK_NAME, {}, "посмотреть на уже сделанные снимки экрана (они уйдут тебе в облако)"),
            (hands.SET_REM_NAME, {"kind": "reminder", "seconds": 1200,
                                  "text": "налить кофе"},
             "поставить напоминание через 1200 секунд: налить кофе"),
            (hands.CANCEL_REM_NAME, {"all": True},
             "отменить все напоминания и таймеры"),
            # Буфер обмена — тем же способом: словами, с напоминанием, что при
            # переводе текст уйдёт в облако.
            (hands.CLIP_NAME, {"mode": "read"},
             "прочитать вслух то, что хозяин скопировал, без пересказа"),
            (hands.CLIP_NAME, {"mode": "translate", "language": "английский"},
             "прочитать то, что хозяин скопировал, и перевести на английский "
             "(текст уйдёт тебе в облако)"),
        ):
            with self.subTest(инструмент=имя):
                self.assertEqual(hands.action_words(имя, аргументы), ждём)

    def test_broken_arguments_do_not_raise(self):
        for имя in (hands.NAME, hands.CLOSE_NAME, hands.YT_NAME,
                    hands.SHOT_NAME, hands.LOOK_NAME):
            for аргументы in ({}, {"app": None}, {"query": "  "}, None, "мусор"):
                with self.subTest(инструмент=имя, аргументы=аргументы):
                    self.assertIsInstance(
                        hands.action_words(имя, аргументы), str)

    def test_yes_is_yes_whatever_the_capitals_and_quotes(self):
        for ответ in ("да", "Да.", " ДА", "«да»", "да\n"):
            with self.subTest(ответ=ответ):
                self.assertTrue(hands.judge_says_yes(ответ))

    def test_anything_else_means_no(self):
        # Молчание и «не знаю» — это не согласие: действовать нельзя.
        for ответ in ("нет", "Нет.", "не знаю", "", None):
            with self.subTest(ответ=ответ):
                self.assertFalse(hands.judge_says_yes(ответ))

    def test_the_prompt_says_what_it_checks(self):
        self.assertIn("{said}", hands.JUDGE_PROMPT)
        self.assertIn("{action}", hands.JUDGE_PROMPT)
        self.assertIn("упоминание программы", hands.JUDGE_PROMPT)
        # Про экран — общей фразой, без списка слов: рассказ о компьютере и о
        # памяти программ просьбой смотреть не считается.
        self.assertIn("экран", hands.JUDGE_PROMPT)
        self.assertIn("памяти", hands.JUDGE_PROMPT)
        # Судью зовём без характера и без истории разговора.
        self.assertNotIn("Ты —", hands.JUDGE_PROMPT)

    def test_the_judge_sees_the_fresh_exchange_before_the_phrase(self):
        # «На рабочем столе, слитно» уточняет его же просьбу: без прошлого
        # обмена судья отвечал «он только уточнил», и она просила повторить.
        brain = _brain([])
        спросили = _судья(brain, "да")
        brain._history.append({"role": "user", "content": "Открой файл план ремонта."})
        brain._history.append({"role": "assistant", "content": "Не нашла, уточни название."})
        self.assertTrue(brain._really_asked(
            hands.FIND_NAME, {"name": "планремонта", "open": True},
            "На рабочем столе файл планремонта, слитно."))
        промпт = спросили[-1][0]
        self.assertIn("Открой файл план ремонта.", промпт)
        self.assertIn("Не нашла, уточни название.", промпт)
        self.assertIn("уточняет его же просьбу", промпт)

    def test_old_exchange_is_not_shown_to_the_judge(self):
        brain = _brain([])
        спросили = _судья(brain, "нет")
        brain._history.append({"role": "user", "content": "Открой Хром."})
        brain._history.append({"role": "assistant", "content": "Не могу.",
                               "at": "2026-01-01T10:00:00"})
        brain._really_asked(hands.NAME, {"app": "firefox"}, "Ну да.")
        self.assertNotIn("Открой Хром.", спросили[-1][0])

    def test_refusals_are_errors_for_the_model(self):
        ожидание = ("Это действие сейчас не нужно: в этой реплике он о нём не просит. "
                    "Ответь на его последнюю реплику по смыслу, как в обычном "
                    "разговоре, и не упоминай это действие и проверку.")
        self.assertEqual(json.loads(hands.not_really_asked(hands.NAME))["error"],
                         ожидание)
        сбой = json.loads(hands.check_failed(hands.NAME))["error"]
        self.assertIn("не получилось проверить просьбу", сбой)
        self.assertIn("не делай", сбой)
        self.assertIn("коротко скажи, что не вышло", сбой)


class Журнал(unittest.TestCase):
    def test_the_check_shows_up_in_the_journal(self):
        строки = WebRuntime._log_messages("action_check", {
            "action": "запустить программу discord", "answer": "нет",
            "took": 0.9})
        self.assertEqual(строки,
                         ["проверка просьбы: запустить программу discord — "
                          "нет (0.9 с)"])


class СобытиеПроверки(Обстановка):
    def test_the_event_carries_the_action_the_answer_and_the_time(self):
        brain = _запуск()
        _судья(brain, "нет")
        события = []
        brain.on_event = lambda вид, данные: события.append((вид, данные))
        with mock.patch.object(hands, "run_tool"):
            list(brain.reply(ФРАЗА))
        проверки = [данные for вид, данные in события if вид == "action_check"]
        self.assertEqual(len(проверки), 1)
        self.assertEqual(проверки[0]["action"], "запустить программу discord")
        self.assertEqual(проверки[0]["answer"], "нет")
        self.assertIsInstance(проверки[0]["took"], float)
        self.assertLessEqual(проверки[0]["took"], 30.0)

    def test_a_failed_check_is_said_so_in_the_journal(self):
        brain = _запуск()
        _судья(brain, падает=True)
        события = []
        brain.on_event = lambda вид, данные: события.append((вид, данные))
        with mock.patch.object(hands, "run_tool"):
            list(brain.reply(ФРАЗА))
        ответы = [данные.get("answer") for вид, данные in события
                  if вид == "action_check"]
        self.assertEqual(ответы, ["ошибка"])


if __name__ == "__main__":
    unittest.main()
