"""Закрытие открытого документа по названию.

`close_window_of` обходит видимые окна и закрывает нужное одним `WM_CLOSE` —
так же, как крестик. Настоящих окон в тестах нет: и список окон, и `user32`
подменены, поэтому ничьи документы не закрываются, а сообщения никому не
уходят. Живое окно и живой `WM_CLOSE` видны только на машине хозяина.

Проверяется ровно то, что обещано инструменту: одно подходящее окно
закрывается, несколько — не закрывается ничего и возвращаются заголовки,
нет ни одного — честный отказ, окно пульта и Проводника не трогаем.
"""

import contextlib
import json
import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from say_helpers import assert_said
from core import abilities, commands, documents, hands, launcher
from core.brain import Brain

APPS = [{"id": "notepad", "title": "Блокнот", "kind": "app", "path": "notepad.exe"}]

# Окно документа и соседнее окно с другим названием.
ОТЧЁТ = (0x101, "отчёт.txt — Блокнот", 11)
ЗАПИСКА = (0x202, "записка.txt — Блокнот", 12)


def _процесс(имя):
    """Подделка `psutil.Process`: имя — метод, как у настоящего (ГРАБЛИ)."""
    return NS(name=lambda: имя, cmdline=lambda: [])


def _мир(*список):
    """Окна вместе с процессами под ними: заголовок и процесс — одна правда.

    Элементы — тройки `(handle, заголовок, pid)` или пары «окно, процесс».
    """
    словарь = {}
    for пара in список:
        if isinstance(пара, tuple) and len(пара) == 2:
            окно, процесс = пара
            словарь[int(окно[2])] = процесс
    окна = [пара[0] if isinstance(пара, tuple) and len(пара) == 2 else пара
            for пара in список]

    def Process(pid):
        объект = словарь.get(int(pid))
        if объект is None:
            raise RuntimeError("нет такого процесса")
        return объект

    стек = contextlib.ExitStack()
    стек.enter_context(mock.patch.object(documents, "_видимые_окна",
                                         return_value=list(окна)))
    стек.enter_context(mock.patch.object(documents, "psutil", NS(Process=Process)))
    return стек


def _user32(отправлено, ответ=1):
    """Подмена `ctypes.windll.user32`: собирает отправленные сообщения."""
    class _User32:
        def PostMessageW(self, hwnd, message, wparam, lparam):
            отправлено.append((int(hwnd), int(message)))
            return ответ

    return mock.patch("ctypes.windll", NS(user32=_User32()))


def _закрыто(*_):
    """Подмена отправки `WM_CLOSE`: возвращает (подмена, список окон)."""
    отправлено = []

    def послать(hwnd):
        отправлено.append(int(hwnd))
        return True

    return mock.patch.object(documents, "_закрыть_окно", side_effect=послать), отправлено


class ЗакрытиеОкна(unittest.TestCase):
    """Само действие: одно окно, несколько, ни одного, пульт и Проводник."""

    def test_одно_окно_получает_wm_close(self):
        подмена, отправлено = _закрыто()
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
            что = documents.close_window_of("отчёт")
        self.assertTrue(что["ok"])
        self.assertEqual(отправлено, [ОТЧЁТ[0]])
        self.assertIn("отчёт.txt", что["title"])

    def test_имя_можно_без_расширения_и_в_другом_регистре(self):
        # Голос не пишет расширение, а регистр у окон свой.
        for слова in ("отчёт", "Отчёт", "ОТЧЁТ", "отчет"):
            with self.subTest(слова=слова):
                подмена, отправлено = _закрыто()
                with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
                    что = documents.close_window_of(слова)
                self.assertTrue(что["ok"], слова)
                self.assertEqual(отправлено, [ОТЧЁТ[0]], слова)

    def test_другое_окно_не_трогаем(self):
        # «Закрой записку» при открытом отчёте: отчёт закрываться не должен.
        подмена, отправлено = _закрыто()
        with _мир((ОТЧЁТ, _процесс("notepad.exe")),
                  (ЗАПИСКА, _процесс("notepad.exe"))), подмена:
            что = documents.close_window_of("записка")
        self.assertTrue(что["ok"])
        self.assertEqual(отправлено, [ЗАПИСКА[0]])

    def test_два_окна_не_закрываем_ни_одного(self):
        подмена, отправлено = _закрыто()
        левое = (0x303, "отчёт.txt — Блокнот", 21)
        правое = (0x404, "Отчёт за сентябрь.docx — Word", 22)
        with _мир((левое, _процесс("notepad.exe")),
                  (правое, _процесс("winword.exe"))), подмена:
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [])
        self.assertEqual(что["candidates"], [левое[1], правое[1]])

    def test_нет_ни_одного_окна_честный_отказ(self):
        подмена, отправлено = _закрыто()
        with _мир((ЗАПИСКА, _процесс("notepad.exe"))), подмена:
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [])
        self.assertEqual(что["candidates"], [])
        self.assertIn("не вижу открытого документа «отчёт»", что["text"])

    def test_окно_пульта_не_закрываем(self):
        # В заголовке пульта то же слово: под ним не документ, а сама Труба.
        пульт = (0x505, "отчёт — Труба — пульт", 31)
        подмена, отправлено = _закрыто()
        with _мир((пульт, _процесс("pythonw.exe"))), подмена:
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [])

    def test_окно_проводника_не_закрываем(self):
        # Папка с тем же названием — не документ.
        папка = (0x606, "отчёт", 41)
        подмена, отправлено = _закрыто()
        with _мир((папка, _процесс("explorer.exe"))), подмена:
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [])

    def test_без_слов_запроса_ничего_не_делаем(self):
        подмена, отправлено = _закрыто()
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
            for пусто in ("", "   ", "а", None, 42):
                with self.subTest(слова=пусто):
                    что = documents.close_window_of(пусто)
                    self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [])

    def test_окна_не_прочитались_разговор_не_роняем(self):
        with mock.patch.object(documents, "_видимые_окна",
                               side_effect=OSError("сломалось")):
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertIn("не вижу открытого документа", что["text"])

    def test_сообщение_уходит_через_user32(self):
        # Настоящее API: `WM_CLOSE` окну и только ему.
        отправлено = []
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), _user32(отправлено):
            что = documents.close_window_of("отчёт")
        self.assertTrue(что["ok"])
        self.assertEqual(отправлено, [(ОТЧЁТ[0], documents.WM_CLOSE)])
        self.assertEqual(documents.WM_CLOSE, 0x0010)

    def test_user32_отказал_разговор_не_роняем(self):
        отправлено = []
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), _user32(отправлено, ответ=0):
            что = documents.close_window_of("отчёт")
        self.assertFalse(что["ok"])
        self.assertEqual(отправлено, [(ОТЧЁТ[0], documents.WM_CLOSE)])

class Инструмент(unittest.TestCase):
    """`hands.run_close_document` — ответ инструмента и его место в наборе."""

    def setUp(self):
        # Память о недавних действиях общая на процесс: без сброса второй
        # прогон подряд отвечал бы «уже сделала».
        hands.forget_done()
        self.addCleanup(hands.forget_done)

    def test_одно_окно_отвечает_готовой_фразой(self):
        подмена, отправлено = _закрыто()
        события = []
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
            ответ = json.loads(hands.run_close_document(
                '{"name": "отчёт", "because": "закрой документ отчёт"}',
                lambda вид, текст: события.append((вид, текст))))
        self.assertTrue(ответ["ok"])
        self.assertEqual(ответ["title"], ОТЧЁТ[1])
        assert_said(self, ответ["text"], "doc_close", name="отчёт.txt")
        self.assertEqual(отправлено, [ОТЧЁТ[0]])
        self.assertEqual(события, [("closed", ОТЧЁТ[1])])

    def test_несколько_окон_возвращают_заголовки(self):
        подмена, отправлено = _закрыто()
        левое = (0x707, "отчёт.txt — Блокнот", 51)
        правое = (0x808, "Отчёт.docx — Word", 52)
        with _мир((левое, _процесс("notepad.exe")),
                  (правое, _процесс("winword.exe"))), подмена:
            ответ = json.loads(hands.run_close_document(
                '{"name": "отчёт", "because": "закрой документ отчёт"}', None))
        self.assertFalse(ответ["ok"])
        self.assertEqual(ответ["candidates"], [левое[1], правое[1]])
        self.assertEqual(отправлено, [])

    def test_нет_окна_ошибка_а_не_ложное_подтверждение(self):
        подмена, отправлено = _закрыто()
        with _мир(), подмена:
            ответ = json.loads(hands.run_close_document(
                '{"name": "отчёт", "because": "закрой документ отчёт"}', None))
        self.assertIn("не вижу открытого документа", ответ["error"])
        self.assertEqual(отправлено, [])

    def test_без_названия_и_с_битыми_аргументами(self):
        for аргументы in ('{}', '{"name": "  "}', '{"name": null}', "мусор", "[]"):
            with self.subTest(аргументы=аргументы):
                подмена, отправлено = _закрыто()
                with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
                    ответ = json.loads(hands.run_close_document(аргументы, None))
                self.assertIn("error", ответ)
                self.assertEqual(отправлено, [])

    def test_инструмент_в_наборе_и_порядок_прежних_не_изменился(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        self.addCleanup(setattr, config, "WEB_SEARCH", self._saved)
        with mock.patch.object(launcher, "read_list", return_value=APPS):
            brain = _brain()
            list(brain.reply("как дела?"))
            другой = _brain()
            list(другой.reply("закрой документ отчёт"))
        имена = [t["function"]["name"]
                 for t in brain._client.bodies[0].get("tools", [])]
        self.assertIn(hands.CLOSE_DOC_NAME, имена)
        # Новый инструмент — в конце постоянной части, перед действиями от
        # `brain.actions`; всё, что было до него, осталось на месте.
        self.assertEqual(имена[:имена.index(hands.CLOSE_DOC_NAME)], [
            hands.NAME, hands.CLOSE_NAME, hands.YT_NAME, hands.NOTE_NAME,
            hands.READ_NAME, hands.LAYOUT_NAME, hands.MEDIA_NAME,
            hands.PCVOL_NAME, hands.FOLDER_NAME, hands.SET_REM_NAME,
            hands.LIST_REM_NAME, hands.CANCEL_REM_NAME, hands.DOC_NAME,
            hands.FIND_NAME, hands.CLIP_NAME, hands.PUT_NAME, hands.POWER_NAME,
        ])
        # Набор одинаковый на болтовне и на просьбе — на нём держится кеш.
        self.assertEqual(
            [t["function"]["name"]
             for t in другой._client.bodies[0].get("tools", [])], имена)

    def test_слова_для_судьи(self):
        self.assertEqual(
            hands.action_words(hands.CLOSE_DOC_NAME, {"name": "отчёт"}),
            "закрыть документ «отчёт»")
        # Кривые аргументы проверку не роняют.
        for аргументы in ({}, {"name": None}, None, "мусор"):
            self.assertIsInstance(
                hands.action_words(hands.CLOSE_DOC_NAME, аргументы), str)

    def test_действие_под_защитой_как_у_закрытия_программы(self):
        self.assertIn(hands.CLOSE_DOC_NAME, hands.LOCAL)
        self.assertIn(hands.CLOSE_DOC_NAME, hands.JUDGED)
        self.assertIn(hands.CLOSE_DOC_NAME, hands.GUARDED)
        self.assertEqual(hands.CONFIRM[hands.CLOSE_DOC_NAME], ("{text}",))

    def test_модель_зовёт_инструмент_и_получает_фразу(self):
        brain = _brain([[_вызов(hands.CLOSE_DOC_NAME,
                                '{"name": "отчёт", "because": '
                                '"закрой документ отчёт"}')]])
        подмена, отправлено = _закрыто()
        with _мир((ОТЧЁТ, _процесс("notepad.exe"))), подмена:
            сказанное = list(brain.reply("закрой документ отчёт"))
        self.assertEqual(отправлено, [ОТЧЁТ[0]])
        self.assertEqual(len(сказанное), 1)
        assert_said(self, сказанное[0], "doc_close", name="отчёт.txt")

    def test_в_умениях_и_навыках_есть_про_закрытие(self):
        self.assertTrue(any("закрывать открытый документ" in строка
                            for строка in abilities.ACTIONS))
        self.assertIn(hands.CLOSE_DOC_NAME, abilities.describe(APPS))
        навык = next(навык for навык in commands.SKILLS
                     if навык.title == "Документы: закрыть")
        self.assertTrue(any("закрой документ" in пример.lower()
                            for пример in навык.examples))
        self.assertIn("Документы: закрыть",
                      [одна["title"] for одна in commands.commands_guide()["skills"]])


# --- Мозг без сети и без микрофона -----------------------------------------


def _вызов(имя, аргументы):
    delta = NS(content=None,
               tool_calls=[NS(index=0, id="c1",
                              function=NS(name=имя, arguments=аргументы))],
               reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _реплика(текст):
    delta = NS(content=текст, tool_calls=None, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams=None):
    brain = object.__new__(Brain)
    brain.provider = "deepseek"
    brain._home = "deepseek"
    brain._home_at = 0.0
    brain._model = "test"
    brain._client = _Client(streams or [[_реплика("Ок.")]])
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
    # Судья (`hands.JUDGED`) спрашивает у модели «просил ли он?»: здесь она
    # отвечает «да», а отказ проверяет tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain
