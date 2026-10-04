"""Чтение вслух: разметка, предложения, голос и остановка.

Ни синтеза, ни звука, ни потоков надолго: голосовой цикл собирается из
заглушек, а поток чтения — настоящий, но работает на доли секунды (пауза между
предложениями в тесте равна нулю). Настоящие файлы хозяина не открываются:
«Загрузки», «Документы» и «Рабочий стол» подменены, как в `test_documents.py`.

Главное, что тут проверяется, — текст документа не уходит из компьютера: в
ответе инструмента модели его нет, в журнале его нет, в истории только строка
«(прочитала вслух «отчёт.txt»)».
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from say_helpers import assert_said
from core import documents, folders, hands, voice_loop
from core.voice_loop import VoiceLoop

ОБРАЗЕЦ = "Отчёт за сентябрь: выручка выросла на 12 процентов."
РАЗМЕТКА = (
    "# Отчёт за сентябрь\n"
    "\n"
    "Выручка **выросла** на _12_ процентов, и это `хорошо`.\n"
    "\n"
    "> Заметка бухгалтера.\n"
    "\n"
    "- пункт первый\n"
    "- пункт второй\n"
    "\n"
    "Подробности на [сайте отчёта](https://example.com/otchet).\n"
    "\n"
    "| Позиция | Сумма |\n"
    "| --- | --- |\n"
    "| Кран | 1500 |\n"
)


# --- Разметка .md и нарезка на предложения ---------------------------------


class Разметка(unittest.TestCase):
    """`plain_for_speech`: разметки в синтезе быть не должно."""

    def test_заголовки_выделения_и_цитаты_убраны(self):
        чисто = documents.plain_for_speech(РАЗМЕТКА)
        for мусор in ("#", "**", "_12_", "`хорошо`", ">"):
            self.assertNotIn(мусор, чисто)
        # Слова при этом на месте: убиралось оформление, а не текст.
        for слово in ("Отчёт за сентябрь", "выросла", "12 процентов",
                      "Заметка бухгалтера", "пункт первый"):
            self.assertIn(слово, чисто)

    def test_ссылки_становятся_словами(self):
        чисто = documents.plain_for_speech(РАЗМЕТКА)
        # Адрес вслух не прочесть, а текст ссылки — можно.
        self.assertIn("Подробности на сайте отчёта", чисто)
        self.assertNotIn("https://example.com", чисто)
        self.assertNotIn("example.com", чисто)

    def test_голые_ссылки_становятся_словом_ссылка(self):
        self.assertEqual(
            documents.plain_for_speech("Смотри http://ya.ru/news и всё."),
            "Смотри ссылка и всё.")

    def test_таблица_читается_ячейками_через_запятую(self):
        чисто = documents.plain_for_speech(РАЗМЕТКА)
        self.assertIn("Позиция, Сумма", чисто)
        self.assertIn("Кран, 1500", чисто)
        # Строка-разделитель таблицы — оформление, а не данные.
        self.assertNotIn("---", чисто)
        self.assertNotIn("|", чисто)

    def test_пустые_строки_сжимаются(self):
        # Подряд идущие переносы синтез читает как паузу, а не как текст.
        self.assertNotIn("\n\n", documents.plain_for_speech(РАЗМЕТКА))

    def test_знаки_препинания_в_словах_остаются(self):
        # Звёздочка в «C++» и подчёркивание в имени — не разметка.
        self.assertEqual(documents.plain_for_speech("Переменная my_value и C++."),
                         "Переменная my_value и C++.")


class Нарезка(unittest.TestCase):
    """`sentences`: предложения — куски, а не абзацы и не один кусок."""

    def test_режется_по_точкам_вопросам_и_восклицаниям(self):
        self.assertEqual(
            documents.sentences("Раз. Два! Три? Четыре"),
            ["Раз.", "Два!", "Три?", "Четыре"])

    def test_абзацы_не_склеиваются_в_одно_предложение(self):
        куски = documents.sentences("Первый абзац\n\nВторой абзац")
        self.assertEqual(len(куски), 2)
        self.assertIn("Первый", куски[0])
        self.assertIn("Второй", куски[1])

    def test_длинное_предложение_режется_по_запятым(self):
        длинное = "раз, " * 200
        куски = documents.sentences(длинное)
        self.assertGreater(len(куски), 1)
        for кусок in куски:
            self.assertLessEqual(len(кусок), documents.ДЛИННОЕ_ПРЕДЛОЖЕНИЕ)
        # Порядок и содержание не теряются: сложенное обратно — исходный текст.
        self.assertEqual(", ".join(куски), длинное.strip().rstrip(","))

    def test_без_запятых_режется_по_знакам(self):
        # Иначе «хватит» посреди безостановочной речи не сработает вовсе.
        куски = documents.sentences("а" * 900)
        self.assertGreater(len(куски), 1)
        self.assertTrue(all(len(кусок) <= documents.ДЛИННОЕ_ПРЕДЛОЖЕНИЕ
                            for кусок in куски))

    def test_пустой_текст_даёт_пустой_список(self):
        for текст in ("", "   \n\n  ", None):
            with self.subTest(текст=текст):
                self.assertEqual(documents.sentences(текст), [])


# --- Инструмент read_document при mode: aloud ------------------------------


class СтандартныеПапки(unittest.TestCase):
    """«Загрузки» подменены: настоящие папки хозяина тест не открывает.

    Диски и хранилища Obsidian — тоже подмена: поиск по названию идёт по всем
    дискам (`core/files.py`), и без неё тест ушёл бы рыться в настоящих дисках.
    """

    def setUp(self):
        self.корень = Path(tempfile.mkdtemp(prefix="truba-aloud-folders-"))
        self.addCleanup(shutil.rmtree, str(self.корень), True)
        self.папки = {}
        for имя in ("downloads", "documents", "desktop"):
            папка = self.корень / имя
            папка.mkdir()
            self.папки[имя] = папка
        patcher = mock.patch.object(
            folders, "path_of",
            side_effect=lambda folder_id: self.папки.get(str(folder_id)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(folders, "_drives", return_value=())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("core.notes._vaults", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _лежит(self, куда, имя, текст=ОБРАЗЕЦ):
        target = self.папки[куда] / имя
        target.write_text(текст, encoding="utf-8")
        return target

    def _ответ(self, аргументы, события=None, действия=None):
        return json.loads(hands.run_document(
            json.dumps(аргументы, ensure_ascii=False),
            (lambda вид, что: события.append((вид, что))) if события is not None
            else None,
            действия,
        ))


def _действие(прочитали=None):
    """Подмена голосового цикла: помнит текст и отвечает как он."""
    прочитали = [] if прочитали is None else прочитали

    def read_aloud(text=None, name="", resume=False):
        if resume:
            return True, "отчёт.txt"
        прочитали.append((text, name))
        return True, name

    read_aloud.прочитали = прочитали
    return read_aloud


class ИнструментВслух(СтандартныеПапки):
    """При `aloud` модель получает фразу, а не текст документа."""

    def test_в_ответе_модели_нет_текста_документа(self):
        self._лежит("downloads", "отчёт.txt")
        прочитали = []
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "because": "прочитай вслух последний скачанный"},
            действия={"read_aloud": _действие(прочитали)})
        self.assertTrue(ответ.get("ok"), ответ)
        # Строка из файла не должна уйти никуда: ответ это весь текст, который
        # увидит модель.
        self.assertNotIn(ОБРАЗЕЦ, json.dumps(ответ, ensure_ascii=False))
        self.assertNotIn("выросла", json.dumps(ответ, ensure_ascii=False))
        # Голос при этом текст получил — читать ей надо.
        self.assertEqual(прочитали, [(ОБРАЗЕЦ, "отчёт.txt")])
        # Ответ — готовая фраза для голоса, а не текст.
        assert_said(self, ответ["text"], "doc_read", name="отчёт.txt")

    def test_retell_по_прежнему_отдаёт_текст_модели(self):
        # Режим пересказа не сломан: там модель нужна сама.
        self._лежит("downloads", "отчёт.txt")
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт",
             "because": "что в файле с отчётом?"},
            действия={"read_aloud": _действие()})
        self.assertIn("выросла", ответ["text"])

    def test_второго_круга_нет_только_при_aloud(self):
        # Подтверждение без второго круга решается по `mode`, а не по имени
        # инструмента: при `retell` модель должна пересказать сама.
        for аргументы, ожидаем in (
                ('{"which": "by_name", "mode": "aloud"}', ("{text}",)),
                ('{"which": "by_name"}', None),
                ('{"which": "by_name", "mode": "retell"}', None)):
            with self.subTest(аргументы=аргументы):
                self.assertEqual(
                    hands.confirm_forms({"name": hands.DOC_NAME,
                                         "args": аргументы}),
                    ожидаем)
        # Остальные инструменты не изменились: формы по-прежнему из CONFIRM.
        self.assertEqual(
            hands.confirm_forms({"name": hands.NOTE_NAME, "args": "{}"}),
            hands.CONFIRM[hands.NOTE_NAME])

    def test_фраза_при_aloud_это_поле_text_ответа(self):
        # Именно этим берёт фразу `CONFIRM`: без неё «Читаю …» не прозвучало бы.
        self._лежит("downloads", "отчёт.txt")
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "because": "прочитай вслух"},
            действия={"read_aloud": _действие()})
        line = hands.confirm(
            [{"name": hands.DOC_NAME, "args": '{"mode": "aloud"}'}],
            results=[json.dumps(ответ, ensure_ascii=False)])
        assert_said(self, line, "doc_read", name="отчёт.txt")
        # А при `retell` подтверждения нет — нужен второй круг с текстом.
        self.assertEqual(hands.confirm(
            [{"name": hands.DOC_NAME, "args": '{"mode": "retell"}'}],
            results=[json.dumps({"text": "текст"}, ensure_ascii=False)]), "")

    def test_голос_выключен_это_честный_отказ(self):
        # Без действия `read_aloud` читать некому — обещать нельзя.
        self._лежит("downloads", "отчёт.txt")
        for действия in ({}, {"read_aloud": None}, {"read_aloud": "не функция"}):
            with self.subTest(действия=действия):
                ответ = self._ответ(
                    {"which": "by_name", "name": "отчёт", "mode": "aloud",
                     "because": "прочитай вслух"}, действия=действия)
                self.assertIn("голос выключен", ответ["error"])
                self.assertIn("нечем", ответ["error"])

    def test_пустой_документ_не_читается(self):
        self._лежит("downloads", "пусто.txt", "   \n  ")
        ответ = self._ответ(
            {"which": "by_name", "name": "пусто", "mode": "aloud",
             "because": "прочитай вслух"}, действия={"read_aloud": _действие()})
        self.assertIn("error", ответ)

    def test_в_журнал_идёт_имя_без_текста(self):
        self._лежит("downloads", "отчёт.txt")
        события = []
        self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "because": "прочитай вслух"}, события,
            действия={"read_aloud": _действие()})
        self.assertEqual([вид for вид, _ in события], ["document_aloud"])
        строка = события[0][1]
        self.assertIn("отчёт.txt", строка)
        self.assertIn("вслух", строка)
        self.assertNotIn("выросла", json.dumps(события, ensure_ascii=False))

    def test_судье_и_объявлению_сказано_про_вслух(self):
        слова = hands.action_words(
            hands.DOC_NAME, {"which": "by_name", "mode": "aloud"})
        self.assertIn("вслух", слова)
        self.assertNotIn("aloud", слова)
        описание = hands.DOC_TOOL["function"]["description"]
        for слово in ("вслух", "продолжи читать", "хватит", "aloud"):
            self.assertIn(слово, описание)
        параметры = hands.DOC_TOOL["function"]["parameters"]
        self.assertEqual(параметры["properties"]["resume"]["type"], "boolean")

    def test_судья_остаётся_на_месте_и_при_aloud(self):
        # Вслух текст в облако не уходит, но файл всё равно чужой: судья решает,
        # просил ли хозяин.
        self.assertIn(hands.DOC_NAME, hands.JUDGED)
        self.assertIn(hands.DOC_NAME, hands.GUARDED)
        # И в `CONFIRM` его нет: там решение принимает `confirm_forms`.
        self.assertNotIn(hands.DOC_NAME, hands.CONFIRM)

    def test_resume_идёт_в_голос_без_повторного_чтения_файла(self):
        # Продолжение — это дело голосового цикла: он один знает, где остановился.
        self._лежит("downloads", "отчёт.txt")
        прочитали = []
        действие = _действие(прочитали)
        действие(ОБРАЗЕЦ, "отчёт.txt", False)
        прочитали.clear()
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "resume": True, "because": "продолжи читать"},
            действия={"read_aloud": действие})
        self.assertTrue(ответ.get("ok"), ответ)
        assert_said(self, ответ["text"], "doc_resume", name="отчёт.txt")
        # Текст документа второй раз не передавался: продолжать нечего, кроме
        # того, что уже в памяти у голоса.
        self.assertEqual(прочитали, [])

    def test_resume_без_прошлого_чтения_отказывает(self):
        # Голос скажет «я ничего не читала» — но проверить это должен он, а
        # не инструмент: он места не знает.
        self._лежит("downloads", "отчёт.txt")
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "resume": True, "because": "продолжи читать"},
            действия={"read_aloud": lambda *a, **k: (False, "ничего не читала")})
        self.assertIn("ничего не читала", ответ["error"])

    def test_resume_без_голоса_отказывает(self):
        self._лежит("downloads", "отчёт.txt")
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "mode": "aloud",
             "resume": True, "because": "продолжи читать"})
        self.assertIn("голос выключен", ответ["error"])

    def test_resume_в_retell_отказывает(self):
        # Продолжить пересказ с середины фразы нельзя — такого режима нет.
        self._лежит("downloads", "отчёт.txt")
        ответ = self._ответ(
            {"which": "by_name", "name": "отчёт", "resume": True,
             "because": "продолжи читать"}, действия={"read_aloud": _действие()})
        self.assertIn("только чтение вслух", ответ["error"])

    def test_журнал_пульта_умеет_строки_чтения(self):
        from ui.web_runtime import WebRuntime

        self.assertEqual(
            WebRuntime._log_messages("document_aloud", "документ вслух: отчёт.pdf"),
            ["документ вслух: отчёт.pdf"])
        self.assertEqual(
            WebRuntime._log_messages("document_stopped",
                                     "чтение остановлено на 14 из 80"),
            ["чтение остановлено на 14 из 80"])


# --- Голосовой цикл: чтение, остановка и продолжение -----------------------


def _цикл(ready=True, стойка=None):
    """Голосовой цикл из заглушек: ни микрофона, ни синтеза, ни облака.

    Синтез подменён в `_say_reading`: проверяется решение цикла — какие
    предложения, в каком порядке и когда остановиться, — а не звук.

    `стойка` — пара событий `(начал, ждать)`: первым синтез сообщает, что он
    заговорил, и замирает до второго. Им тест держит чтение на середине, где и
    живут «хватит» с проверкой окна разговора.
    """
    loop = object.__new__(VoiceLoop)
    loop.events = []
    loop.said = []
    loop.remembered = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    loop._tell_phone = lambda *args, **kwargs: None
    loop._open_conversation = lambda: setattr(loop, "_open", True)
    loop._close_conversation = mock.Mock()
    loop._server = None
    loop._fallback = None
    loop._speaker = None
    loop._brain = None
    loop._turn = None
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._read_stop = None
    loop._reading = None
    loop._dictation = None
    loop._open = False
    loop._last_turn = 0.0
    loop._voice = NS(stream=None)
    loop._listener = None
    loop._output = lambda *args, **kwargs: object()
    loop._speaker_idle = lambda: True
    loop.ready = ready
    loop._thread = mock.Mock(is_alive=lambda: ready)
    # Мозга нет: строку в историю проверять нечем, а падать она не должна.
    loop._remember_reading = lambda имя: loop.remembered.append(имя)

    def _говорит(фраза, speaker, *, first=False):
        loop.said.append(фраза)
        if стойка is not None:
            начал, ждать = стойка
            начал.set()
            ждать.wait(2.0)
        return True

    loop._say_reading = _говорит
    return loop


def _стойка():
    """Пара событий, на которой синтез держится на середине предложения."""
    return threading.Event(), threading.Event()


def _скажет_после(loop, сколько, сказано, раз=1):
    """Синтез, который после `сколько` предложений означает «хватит»."""
    осталось = [раз]

    def _говорит(фраза, speaker, *, first=False):
        сказано.append(фраза)
        if len(сказано) >= сколько and осталось[0] > 0:
            осталось[0] -= 1
            loop.shut_up()
        return True

    return _говорит


def _дождаться(loop, timeout=2.0):
    """Дождаться конца чтения. Поток настоящий, но пауза в нём нулевая."""
    край = time.monotonic() + timeout
    while time.monotonic() < край:
        if loop._reading is None or not loop._reading.get("активно"):
            return True
        time.sleep(0.01)
    return False


def _чтение_без_звука():
    """Синтез и динамик подменены, чтобы проверить текст для сверки эха."""
    loop = object.__new__(VoiceLoop)
    loop._stop = threading.Event()
    loop._ducker = mock.Mock()
    loop._listener = None
    loop._speaking_text = "Прежний ответ."
    loop._tell_phone = lambda **kwargs: None
    loop._emit = lambda *args: None
    loop._sound_of = lambda phrase, speaker: (iter([(b"", 1)]), False)
    return loop, mock.Mock()


class ЭхоЧтения(unittest.TestCase):
    def test_три_предложения_копятся(self):
        loop, speaker = _чтение_без_звука()
        for номер, фраза in enumerate(("Первое предложение.", "Второе предложение.",
                                      "Третье предложение.")):
            self.assertTrue(loop._say_reading(фраза, speaker, first=номер == 0))
        self.assertEqual(loop._speaking_text,
                         "Первое предложение. Второе предложение. Третье предложение.")

    def test_длинное_эхо_из_начала_и_короткий_хвост(self):
        loop, speaker = _чтение_без_звука()
        предложения = [f"Раздел {n} описывает важную проверку." for n in range(11)]
        предложения.append("Шучу.")
        for номер, фраза in enumerate(предложения):
            loop._say_reading(фраза, speaker, first=номер == 0)
        self.assertTrue(loop._is_own_echo(" ".join(предложения[:10])))
        self.assertTrue(loop._is_own_echo("Шучу."))

    def test_кнопка_обрывает_предложение_на_ближайшем_куске(self):
        # Касание круга посреди длинного предложения: остальные куски этого
        # предложения в динамик не идут, и чтение дальше не продолжается.
        loop, speaker = _чтение_без_звука()
        loop._read_stop = threading.Event()
        loop._fallback = None
        loop._speaker = speaker
        loop._interrupt = threading.Event()
        отдано = []

        def куски(phrase, _speaker):
            for n in range(5):
                if n == 2:
                    loop.shut_up()
                отдано.append(n)
                yield (b"", 1)

        loop._sound_of = lambda phrase, sp: (куски(phrase, sp), True)
        self.assertFalse(loop._say_reading("Очень длинное предложение.", speaker,
                                           first=True))
        self.assertEqual(speaker.say.call_count, 2)
        speaker.pause.assert_not_called()

    def test_продолжение_начинает_текст_заново(self):
        loop, speaker = _чтение_без_звука()
        loop._say_reading("Прошлое чтение.", speaker, first=True)
        lock = threading.Lock()
        loop._turn_lock = lambda: lock
        loop._output = lambda: speaker
        loop._finish_reading = lambda состояние, всего: None
        состояние = {"стоп": threading.Event(), "предложения":
                     ["Первое.", "Второе.", "Продолжение.", "Последнее."], "at": 2}
        with mock.patch.object(voice_loop, "READ_ALOUD_GAP", 0):
            loop._read_aloud(состояние)
        self.assertEqual(loop._speaking_text, "Продолжение. Последнее.")


class ЧтениеВслух(unittest.TestCase):

    def setUp(self):
        # Пауза между предложениями — в тесте нулевая: поток чтения настоящий,
        # но живёт доли секунды, а не полминуты на документ.
        patcher = mock.patch.object(voice_loop, "READ_ALOUD_GAP", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_читает_по_предложениям_через_свой_синтез(self):
        loop = _цикл()
        началось, имя = loop.read_aloud(РАЗМЕТКА, "отчёт.md")
        self.assertTrue(началось)
        self.assertEqual(имя, "отчёт.md")
        self.assertTrue(_дождаться(loop), "чтение не кончилось")
        # В синтез ушли предложения без разметки — ровно тем, что чистит
        # `plain_for_speech` и режет `sentences`.
        self.assertEqual(loop.said, documents.sentences(
            documents.plain_for_speech(РАЗМЕТКА)))
        self.assertNotIn("**", " ".join(loop.said))
        self.assertNotIn("|", " ".join(loop.said))
        # Строку старта в журнал отдаёт инструмент (у него ещё и страницы), а
        # голосовой цикл про документ молчит: текста в журнале быть не должно.
        self.assertEqual([вид for вид, _ in loop.events
                          if вид == "document_aloud"], [])

    def test_дочитал_дочиста_и_продолжать_некогда(self):
        loop = _цикл()
        loop.read_aloud("Раз. Два.", "отчёт.txt")
        self.assertTrue(_дождаться(loop))
        self.assertIsNone(loop._reading)
        началось, причина = loop.read_aloud(resume=True)
        self.assertFalse(началось)
        self.assertIn("ничего не читала", причина)
        # И в журнале нет строки об остановке: её никто не останавливал.
        self.assertEqual([вид for вид, _ in loop.events
                          if вид == "document_stopped"], [])

    def test_хватит_останавливает_и_место_помнится(self):
        loop = _цикл()
        # После второго предложения звучит «хватит» — в коде это
        # `shut_up`, поднимающий тот же флаг, что и вся её речь.
        сказано = []
        loop._say_reading = _скажет_после(loop, 2, сказано)
        началось, _ = loop.read_aloud("Раз. Два. Три. Четыре. Пять.", "отчёт.txt")
        self.assertTrue(началось)
        self.assertTrue(_дождаться(loop), "чтение не остановилось")
        self.assertLess(len(сказано), 5, "остановка не сработала")
        # Журнал — с номером предложения, без текста документа.
        строки = [что for вид, что in loop.events if вид == "document_stopped"]
        self.assertEqual(len(строки), 1, loop.events)
        self.assertRegex(строки[0], r"чтение остановлено на \d+ из 5")
        # Место осталось в памяти процесса — «продолжи читать» его найдёт.
        self.assertIsNotNone(loop._reading)
        self.assertLess(loop._reading["at"], 5)

    def test_продолжение_начинается_с_того_же_предложения(self):
        loop = _цикл()
        сказано = []
        loop._say_reading = _скажет_после(loop, 2, сказано)
        loop.read_aloud("Раз. Два. Три. Четыре. Пять.", "отчёт.txt")
        self.assertTrue(_дождаться(loop))
        где = len(сказано)
        # «Продолжи читать» — тот же инструмент с `resume`. Дальше «хватит» уже
        # не звучит: сработав один раз, оно иначе остановило бы и продолжение.
        loop._say_reading = _скажет_после(loop, 99, сказано, раз=0)
        началось, имя = loop.read_aloud(resume=True)
        self.assertTrue(началось)
        self.assertEqual(имя, "отчёт.txt")
        self.assertTrue(_дождаться(loop))
        # Продолжение не перечитывает начало и не теряет хвост.
        self.assertEqual(сказано[:где], ["Раз.", "Два."])
        self.assertEqual(сказано[где:], ["Три.", "Четыре.", "Пять."])

    def test_продолжать_без_прошлого_чтения_отказывает(self):
        loop = _цикл()
        началось, причина = loop.read_aloud(resume=True)
        self.assertFalse(началось)
        self.assertIn("ничего не читала", причина)
        self.assertEqual(loop.said, [], "отказ не должен читать вслух")

    def test_выключенный_голос_читать_нечем(self):
        loop = _цикл(ready=False)
        началось, причина = loop.read_aloud(РАЗМЕТКА, "отчёт.md")
        self.assertFalse(началось)
        self.assertEqual(причина, "голос выключен, читать нечем")
        self.assertEqual(loop.said, [])

    def test_пустой_текст_не_запускает_чтение(self):
        loop = _цикл()
        началось, причина = loop.read_aloud("   \n\n  ", "пусто.txt")
        self.assertFalse(началось)
        self.assertIn("нет текста", причина)
        self.assertEqual(loop.said, [])

    def test_остановка_не_забывает_о_чём_речь(self):
        # `shut_up` обрывает и чтение: ровно то же, что обрывает её речь.
        начал, ждать = _стойка()
        loop = _цикл(стойка=(начал, ждать))
        loop.read_aloud("Раз. Два. Три.", "отчёт.txt")
        self.assertTrue(начал.wait(2.0), "чтение не дошло до синтеза")
        стоп = loop._read_stop
        self.assertIsNotNone(стоп, "чтение не пометило себя флагом")
        self.assertFalse(стоп.is_set())
        loop.shut_up()
        self.assertTrue(стоп.is_set())
        ждать.set()
        self.assertTrue(_дождаться(loop))

    def test_его_новая_фраза_обрывает_чтение(self):
        # Перебивание голосом: фраза проходит через `_handle`, и чтение должно
        # встать. Замок хода тут подменён на мгновенный — иначе фраза встала бы
        # в очередь за читающим предложением и проверить было бы нечего (что
        # она проходит, проверяет test_остановка_не_блокирует_новую_фразу).
        начал, ждать = _стойка()
        loop = _цикл(стойка=(начал, ждать))
        loop._turn = NS(acquire=lambda timeout=None: True, release=lambda: None)
        loop._stt = NS(recognize=lambda phrase, sample_rate: "хватит")
        loop._should_answer = lambda *args, **kwargs: True
        loop._turn_body = mock.Mock()
        loop._take_held = lambda текст: текст
        loop._first_schedule = lambda: NS(answered=lambda: None)
        loop._first_pending = False
        loop.read_aloud("Раз. Два. Три.", "отчёт.txt")
        self.assertTrue(начал.wait(2.0), "чтение не дошло до синтеза")
        with mock.patch("core.voice_loop.unfinished", return_value=False):
            loop._handle(b"\x00\x01")
        ждать.set()
        self.assertTrue(_дождаться(loop))
        self.assertEqual(loop.said, ["Раз."], "после перебивания читать нельзя")
        # Фраза хозяина обработана как обычно: до неё он говорит, а не молчит.
        loop._turn_body.assert_called_once()

    def test_окно_разговора_не_закрывается_пока_она_читает(self):
        # Иначе «хватит» и «продолжи читать» пришлось бы звать по имени.
        начал, ждать = _стойка()
        loop = _цикл(стойка=(начал, ждать))
        loop.read_aloud("Раз. Два.", "отчёт.txt")
        self.assertTrue(начал.wait(2.0), "чтение не дошло до синтеза")
        loop._check_window()
        loop._close_conversation.assert_not_called()
        ждать.set()
        self.assertTrue(_дождаться(loop))
        # Вне чтения сторож обрабатывает фразы обычным способом.
        loop._open = True
        loop._last_turn = 0.0
        with mock.patch("core.voice_loop.time.monotonic", return_value=10_000.0):
            loop._check_window()
        loop._close_conversation.assert_called_once()

    def test_остановка_не_блокирует_новую_фразу(self):
        # Замок хода берётся на предложение, а не на весь документ: иначе
        # «хватит» висело бы в очереди до конца чтения.
        loop = _цикл()
        началось, _ = loop.read_aloud("Раз. Два. Три.", "отчёт.txt")
        self.assertTrue(началось)
        # Пока читает, хозяин может взять ход: проверяем, что он свободен.
        self.assertTrue(loop._turn_lock().acquire(timeout=0.5),
                        "ход занят на всё чтение")
        loop._turn_lock().release()
        self.assertTrue(_дождаться(loop))

    def test_история_помнит_только_что_прочитала(self):
        # Без пометки она через пару реплик забыла бы, что читала, а сам текст
        # документа в историю попасть не должен.
        loop = _цикл()
        loop.read_aloud(РАЗМЕТКА, "отчёт.md")
        self.assertTrue(_дождаться(loop))
        self.assertEqual(loop.remembered, ["отчёт.md"])
        self.assertNotIn("выросла", json.dumps(loop.remembered, ensure_ascii=False))

    def test_в_историю_чтение_ничего_своего_не_пишет(self):
        # Ход «прочитай вслух» — «Читаю «…»» ложится в историю сам, мозгом.
        # Дополнительная строка дала бы пустой ответ перед основным ходом.
        loop = _цикл()
        brain = NS(announce=mock.Mock())
        loop._brain = brain
        loop._remember_reading = VoiceLoop._remember_reading.__get__(loop)
        loop.read_aloud(РАЗМЕТКА, "отчёт.md")
        self.assertTrue(_дождаться(loop))
        brain.announce.assert_not_called()

    def test_мозг_получает_действие_чтения_вслух(self):
        # Без действия в `brain.actions` модель на «прочитай вслух» не знала бы,
        # что читать некому.
        brain = NS()
        loop = object.__new__(VoiceLoop)
        loop._brain = brain
        loop._emit = lambda kind, payload: None
        loop._server = None
        loop._wire_brain()
        self.assertIn("read_aloud", brain.actions)
        self.assertTrue(callable(brain.actions["read_aloud"]))

    def test_пульт_тоже_отдаёт_мозгу_чтение_вслух(self):
        from ui.web_runtime import WebRuntime

        brain = NS()
        pult = object.__new__(WebRuntime)
        pult.brain = brain
        pult.server = None
        pult.voice = NS(begin_dictation=mock.Mock(return_value=True),
                        read_aloud=mock.Mock(return_value=(True, "отчёт.txt")))
        pult._remember = lambda kind, payload: None
        pult._wire_brain()
        # Чат пульта ходит в тот же мозг: чтение вслух то же, что и голосом.
        self.assertEqual(brain.actions["read_aloud"]("текст", "отчёт.txt", False),
                         (True, "отчёт.txt"))
