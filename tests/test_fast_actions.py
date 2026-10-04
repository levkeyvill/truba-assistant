"""Короткий путь для действий и замер каждого голосового ответа.

Замер первого звука: медиана 3.3 с на обычном ответе, 4.2 с с поиском,
5–9 с на действии. Проверяются два механизма:
- круг успешных действий заканчивается без второго запроса модели,
  который добавлял 2–5 с ради короткого подтверждения;
- на каждый голосовой ответ уходит событие `timing`: где именно ушло время.

Ни сети, ни звука, ни микрофона: подменены и действия, и синтез, и динамик.
Проверяется ровно одно — короткий путь действий и числа замера. Настоящее
звучание синтеза и настоящий `duck()` по звуковым сессиям Windows здесь не
измеряются: их видно только на живой машине.
"""

import json
from itertools import product
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

import config
from core import hands, launcher, web
from core.brain import Brain
from core.voice_loop import VoiceLoop
from say_helpers import variants
from ui.web_runtime import WebRuntime

APPS = [
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"},
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
]
SHOT = "data:image/jpeg;base64,КАРТИНКА"



# --- Подставная модель ----------------------------------------------------


def _chunk(content=None, calls=None, reasoning=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=reasoning)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _call(index, id_=None, name=None, args=None):
    return NS(index=index, id=id_, function=NS(name=name, arguments=args))


def _tool_call(name, args="{}", call_id="c1"):
    return [_chunk(calls=[_call(0, call_id, name, args)])]


def _two_calls(first, second):
    """Два вызова инструментов в одном круге — так модель просит оба сразу."""
    return [_chunk(calls=[_call(0, "c1", first[0], first[1]),
                          _call(1, "c2", second[0], second[1])])]


class _Client:
    """Отдаёт заранее заготовленные потоки и запоминает тела запросов."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _brain(streams, actions=None):
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
    brain.actions = {} if actions is None else actions
    brain.last_sources = []
    # Проверка «он правда просил?» спрашивает модель перед запуском и закрытием.
    # Здесь она отвечает «да»: этот файл проверяет короткий путь и замер, а
    # отказ судьи разбирает tests/test_action_judge.py.
    brain._ask_plainly = lambda *a, **k: NS(
        choices=[NS(message=NS(content="да"))])
    return brain


def _fake_actions(calls):
    """Подменённый `brain.actions`: помнит, что звали, и отвечает."""
    def make(key, answer):
        def action():
            calls.append(key)
            return answer
        return action

    return {
        "screenshot": make("screenshot", "готово, снимок на телефоне: shot.jpg"),
        "moment": make("moment", "нажал Alt+Shift+F9"),
        # Взгляд на экран отдаёт модели сам снимок, а не путь к нему.
        "look": make("look", SHOT),
    }


def _rounds(brain):
    """Сколько раз за этот ответ уходили в облако."""
    return len(brain._client.bodies)


# --- Подставной голосовой цикл --------------------------------------------


class _Speaker:
    """Динамик без звука: запоминает, что в него отправили, и может медлить."""

    gapless = True

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []
        self.idle = threading.Event()
        self.idle.set()

    def say(self, wave, rate, gap=True):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(("say", len(wave), gap))

    def pause(self, seconds, rate=24000):
        pass

    def wait(self, timeout=None):
        return True

    def interrupt(self):
        pass


class _Voice:
    """Синтез без видеокарты: отдаёт куски и тратит заданное время."""

    def __init__(self, delay=0.0):
        self.delay = delay
        self.closed = 0

    def stream(self, text, ref=None, speed=1.0):
        try:
            if self.delay:
                time.sleep(self.delay)
            yield np.zeros(480, dtype=np.float32), 24000
        finally:
            self.closed += 1

    def say(self, text, ref=None, nfe_step=None, speed=1.0):
        raise AssertionError("поток должен был пойти мимо say()")


class _Quiet:
    """Слушатель и приглушитель: ни микрофона, ни звуковых сессий Windows."""

    def __init__(self, duck_delay=0.0):
        self.duck_delay = duck_delay

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def duck(self):
        # Настоящий duck() ходит по всем звуковым сессиям Windows и вправе
        # задержать ответ; здесь он просто спит заданное число секунд.
        if self.duck_delay:
            time.sleep(self.duck_delay)


class _Brain:
    """Мозг, который тратит заданное время и честно это отмечает.

    Отметки `last_timing` ставит он сам — те же `perf_counter`, что и в
    настоящем `Brain._reply_rounds`. Иначе замер в `_answer` считал бы
    выдуманные числа, и проверять было бы нечего.
    """

    ended = False
    last_sources = []

    def __init__(self, sentences, think=0.0, rounds=1):
        self.sentences = sentences
        self.think = think
        self.rounds = rounds

    def reply(self, text, image=None, voice=None, aloud=False, can_end=True):
        # Порядок отметок — как в настоящем Brain._sentences: отправка запроса,
        # потом первое слово, потом первое готовое предложение — и предложение
        # помечается ДО отдачи наружу. Иначе замер в `_answer` увидел бы
        # предложение позже звука, и порядок этапов врал бы.
        marks = [{"sent": time.perf_counter(), "word": 0.0, "sentence": 0.0}
                 for _ in range(max(1, self.rounds))]
        self.last_timing = {"rounds": marks}
        if self.think:
            time.sleep(self.think)
        for mark in marks:
            mark["word"] = time.perf_counter()
        for sentence in self.sentences:
            for mark in marks:
                if not mark["sentence"]:
                    mark["sentence"] = time.perf_counter()
            yield sentence


def _loop(sentences, think=0.0, rounds=1,
          voice_delay=0.0, say_delay=0.0, duck_delay=0.0):
    """Голосовой цикл из одних заглушек: ни сети, ни звука, ни облака."""
    loop = object.__new__(VoiceLoop)
    loop._brain = _Brain(sentences, think, rounds)
    loop._voice = _Voice(voice_delay)
    loop._ref = None
    loop._speaker = _Speaker(say_delay)
    loop._fallback = None
    loop._server = None
    loop._listener = _Quiet()
    loop._ducker = _Quiet(duck_delay)
    loop._stop = threading.Event()
    loop._interrupt = threading.Event()
    loop._open = False
    loop._last_turn = 0.0
    loop._speaking_text = ""
    loop._spoke_at = 0.0
    loop.events = []
    loop._emit = lambda kind, payload: loop.events.append((kind, payload))
    return loop


def _timing_of(loop):
    """Замер из последнего события `timing` — как его и видит пульт."""
    found = [p for k, p in loop.events if k == "timing"]
    return found[-1] if found else None


class ShortActionTests(unittest.TestCase):
    """Круг из одних действий на компе — без второго похода в облако."""

    def setUp(self):
        self._saved = (config.WEB_SEARCH, config.TTS_ENGINE)
        config.WEB_SEARCH = False
        config.TTS_ENGINE = "silero"
        # Память о недавних действиях общая на процесс: без сброса «Открыла
        # YouTube» превращается в «Уже сделала» от предыдущего теста.
        hands.forget_done()
        self.addCleanup(hands.forget_done)
        patcher = mock.patch.object(launcher, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.WEB_SEARCH, config.TTS_ENGINE = self._saved

    def test_launch_is_confirmed_without_a_second_round(self):
        # Для короткого действия готовое подтверждение экономит 2–5 с
        # второго круга по замеру.
        brain = _brain([_tool_call(hands.NAME,
                                  '{"app": "youtube", "because": "открой, пожалуйста, YouTube"}'),
                        [_chunk("Держи, YouTube открыт.")]])
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")) as start:
            said = list(brain.reply("Ну, открой, пожалуйста, YouTube."))
        start.assert_called_once_with("youtube")
        self.assertEqual(_rounds(brain), 1)
        # Подтверждение — с названием программы из apps.json, а не с её id.
        self.assertEqual(len(said), 1)
        self.assertIn("YouTube", said[0])
        # В историю идёт сказанное, служебного ответа инструмента там нет.
        self.assertEqual(brain._history[-1]["content"], said[0])
        self.assertNotIn("запущено", brain._history[-1]["content"])

    def test_two_actions_in_one_phrase(self):
        # «Открой YouTube и закрой Discord» — одна фраза через «и», а не две.
        brain = _brain([
            _two_calls((hands.NAME,
                        '{"app": "youtube", "because": "Открой YouTube"}'),
                       (hands.CLOSE_NAME,
                        '{"app": "discord", "because": "закрой Discord"}')),
            [_chunk("Готово, всё сделала.")],
        ])
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")), \
                mock.patch.object(launcher, "close", return_value=(True, "Discord закрыта")):
            said = list(brain.reply("Открой YouTube и закрой Discord."))
        self.assertEqual(_rounds(brain), 1)
        self.assertEqual(len(said), 1)
        line = said[0]
        self.assertIn("YouTube", line)
        self.assertIn("Discord", line)
        self.assertIn(" и ", line)
        # Точка внутри фразы читалась бы как остановка посреди мысли.
        self.assertTrue(line.endswith("."))
        self.assertEqual(line.count("."), 1)
        self.assertEqual(brain._history[-1]["content"], line)

    def test_three_actions_stay_one_sentence(self):
        # Три действия — тоже одна фраза. Заглавная нужна только в первой
        # части: «Открыла YouTube и Закрыла Discord» — это список, а не фраза,
        # и слушать такое вслух невозможно.
        line = hands.confirm([
            {"name": hands.NAME, "args": '{"app": "youtube"}'},
            {"name": hands.CLOSE_NAME, "args": '{"app": "discord"}'},
            {"name": hands.SHOT_NAME, "args": "{}"},
        ], apps=APPS)
        expected = {hands._join([part.rstrip(".") for part in parts])
                    for parts in product(variants("launch", app="YouTube"),
                                         variants("close", app="Discord"),
                                         variants("screenshot"))}
        self.assertIn(line, expected)
        self.assertEqual(line.count("."), 1)

    def test_confirm_picks_among_variants_evenly(self):
        # Подтверждение должно быть живым, а не одной и той же фразой: иначе
        # повторение звучит однообразно. Проверяем, что выбор
        # действительно случаен, а не всегда первый вариант.
        calls = [{"name": hands.NAME, "args": '{"app": "youtube"}'}]
        seen = {hands.confirm(calls, apps=APPS) for _ in range(200)}
        self.assertEqual(seen, variants("launch", app="YouTube"))

    def test_confirm_says_nothing_about_a_screen_look(self):
        # Взгляд на экран подтверждением не заканчивается: там модель должна
        # увидеть снимок и ответить по нему.
        self.assertEqual(hands.confirm([{"name": hands.LOOK_NAME, "args": "{}"}],
                                       apps=APPS), "")

    def test_model_text_and_action_still_get_a_short_confirmation(self):
        # Модель в этом круге что-то сказала вместе с вызовом: её слова
        # остаются, подтверждение дописывается после — короткое.
        brain = _brain([[_chunk("Сейчас.")] + _tool_call(
            hands.NAME, '{"app": "youtube", "because": "Открой, пожалуйста, YouTube"}')])
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")):
            said = list(brain.reply("Открой, пожалуйста, YouTube."))
        self.assertEqual(_rounds(brain), 1)
        self.assertEqual(said[0], "Сейчас.")
        self.assertIn("YouTube", said[-1])
        # Всё сказанное в историю — и её слова, и наше подтверждение.
        self.assertEqual(brain._history[-1]["content"], "Сейчас. " + said[-1])

    def test_failed_action_still_asks_the_model(self):
        # Не открылось — говорить «открыла» нельзя, и решать должна модель:
        # у неё есть причина отказа.
        brain = _brain([_tool_call(hands.NAME,
                                  '{"app": "youtube", "because": "Открой, пожалуйста, YouTube"}'),
                        [_chunk("Не вышло, файла нет.")]])
        with mock.patch.object(launcher, "launch",
                               return_value=(False, "файл не найден")):
            said = list(brain.reply("Открой, пожалуйста, YouTube."))
        self.assertEqual(_rounds(brain), 2)
        self.assertEqual(said, ["Не вышло, файла нет."])
        answer = brain._client.bodies[1]["messages"][-1]
        self.assertEqual(answer["role"], "tool")
        self.assertIn("не вышло", answer["content"])

    def test_repeated_request_is_refused_and_the_model_answers(self):
        # Повтор прошлой просьбы: в этой фразе про YouTube не сказано, значит
        # инструмент не выполняется и короткого пути тут быть не может.
        brain = _brain([_tool_call(hands.NAME, '{"app": "youtube"}'),
                        [_chunk("Ты о чём сейчас?")]])
        with mock.patch.object(launcher, "launch", return_value=(True, "YouTube")) as start:
            said = list(brain.reply("А ты о чём сейчас?"))
        start.assert_not_called()
        self.assertEqual(_rounds(brain), 2)
        сообщения = brain._client.bodies[1]["messages"]
        refused = next(m for m in reversed(сообщения) if m.get("role") == "tool")
        self.assertIn("не упоминай это действие и проверку", refused["content"])
        # Следующий круг — сразу ответ, без новых попыток.
        self.assertNotIn("tools", brain._client.bodies[1])
        # Ответ — от модели, а не наше подтверждение: сказать «открыла» было бы
        # враньём, ведь ничего не открывали.
        self.assertEqual(said, ["Ты о чём сейчас?"])

    def test_look_at_screen_needs_the_model(self):
        # Взгляд на экран отдаёт модели сам снимок: ей надо на него посмотреть,
        # поэтому подтверждения вместо второго круга тут быть не может.
        calls = []
        brain = _brain([_tool_call(hands.LOOK_NAME,
                                  '{"because": "Что у меня на экране"}'),
                        [_chunk("У тебя открыт YouTube.")]],
                       _fake_actions(calls))
        said = list(brain.reply("Что у меня на экране?"))
        self.assertEqual(calls, ["look"])
        self.assertEqual(_rounds(brain), 2)
        self.assertEqual(said, ["У тебя открыт YouTube."])
        # Снимок ушёл модели отдельным сообщением — иначе она бы его не увидела.
        second = json.dumps(brain._client.bodies[1]["messages"], ensure_ascii=False, default=str)
        self.assertIn("image_url", second)

    def test_search_alongside_an_action_still_asks_the_model(self):
        # Поиск в том же круге: ответ нужен живой, из сети, — подтверждения
        # вместо второго круга тут не выйдет. Интернет открывает слово
        # «найди» само (wants_web), кнопка телефона тут ни при чём.
        brain = _brain([
            _two_calls((hands.NAME,
                        '{"app": "youtube", "because": "Открой YouTube"}'),
                       ("web_search", '{"query": "что нового"}')),
            [_chunk("Открыла, и вот что нового.")],
        ])
        with mock.patch.object(config, "WEB_SEARCH", True), \
                mock.patch.object(launcher, "launch", return_value=(True, "YouTube")), \
                mock.patch.object(web, "run_tool", return_value='{"results": []}') as tool:
            said = list(brain.reply("Открой YouTube и найди, что нового"))
        tool.assert_called_once()
        self.assertEqual(_rounds(brain), 2)
        self.assertEqual(said[-1], "Открыла, и вот что нового.")


class TimingTests(unittest.TestCase):
    """Замер каждого голосового ответа: событие `timing` и его подпись."""

    def setUp(self):
        self._saved = config.OUTPUT
        config.OUTPUT = "speakers"
        self.addCleanup(lambda: setattr(config, "OUTPUT", self._saved))

    def test_timing_carries_every_stage_in_order(self):
        # Отсчёт — от конца фразы, и каждая стадия в тесте действительно
        # тратит своё время: приглушение спит, модель думает, синтез и динамик
        # тоже. Замер обязан всё это увидеть и сложить в том же порядке.
        heard = time.perf_counter()
        loop = _loop(["Нормально, кожаный, лежу."], think=0.3,
                     voice_delay=0.05, say_delay=0.01, duck_delay=0.02)
        loop._answer("как дела?", heard_at=heard, stt=0.2)
        data = _timing_of(loop)
        self.assertEqual(set(data), {"total", "stt", "duck", "word", "sentence",
                                     "synth", "say", "rounds"})
        self.assertEqual(data["stt"], 0.2)
        self.assertEqual(data["rounds"], 1)
        # Числа правдоподобные: счёт от конца фразы, всё в пределах ответа.
        self.assertGreaterEqual(data["duck"], 0.02)
        self.assertGreaterEqual(data["word"], 0.3)
        self.assertGreaterEqual(data["sentence"], data["word"])
        self.assertGreaterEqual(data["synth"], 0.05)
        self.assertGreaterEqual(data["say"], 0.01)
        # Этапы идут по времени: слово раньше предложения, звук — позже обоих.
        self.assertLessEqual(data["word"], data["sentence"])
        self.assertLessEqual(data["sentence"], data["total"])
        for key, value in data.items():
            if key == "rounds":
                continue
            self.assertIsInstance(value, float, key)
            self.assertLessEqual(value, 30.0, key)

    def test_timing_is_emitted_once_per_answer(self):
        loop = _loop(["Один ответ."])
        loop._answer("вопрос")
        self.assertEqual([k for k, _ in loop.events].count("timing"), 1)
        self.assertEqual([k for k, _ in loop.events].count("spoken"), 1)

    def test_timing_counts_every_tool_round(self):
        # Кругов инструментов может быть несколько: по их числу сразу видно,
        # уходила ли она в облако повторно (обычный ответ — один круг).
        loop = _loop(["Ответ после поиска."], rounds=2)
        loop._answer("вопрос с поиском")
        self.assertEqual(_timing_of(loop)["rounds"], 2)

    def test_plain_answer_is_one_round(self):
        # Обычный ответ без инструментов — тоже круг, но ровно один: строка
        # замера должна отличать его от ответа с поиском.
        loop = _loop(["Нормально."])
        loop._answer("как дела?")
        self.assertEqual(_timing_of(loop)["rounds"], 1)

    def test_silent_model_leaves_stages_empty(self):
        # Облако молчало: до первого слова не дошло вовсе. Такого этапа в
        # замере нет (None → прочерк в журнале), а не ноль: ноль читался бы
        # как «заняло ноль секунд» и исказил бы замер.
        class _Mute(_Brain):
            def reply(self, *args, **kwargs):
                # Ни слова, ни предложения не пришло вовсе, и отметок нет.
                self.last_timing = {"rounds": [{"sent": time.perf_counter(),
                                                "word": 0.0, "sentence": 0.0}]}
                return iter(())

        loop = _loop([])
        loop._brain = _Mute([], 0.0, 1)
        loop._answer("вопрос", stt=0.4)
        data = _timing_of(loop)
        self.assertIsNone(data["word"])
        self.assertIsNone(data["sentence"])
        self.assertIsNone(data["synth"])
        self.assertIsNone(data["say"])
        # Ответ не прозвучал, а замер всё равно один — на голосовой ответ.
        self.assertEqual([k for k, _ in loop.events].count("timing"), 1)

    def test_brain_keeps_marks_of_every_round(self):
        # Мозг отдаёт замеры по каждому кругу: без них в журнале не напечатать,
        # на каком этапе задержался звук.
        brain = _brain([[_chunk("Нормально, кожаный, лежу.")]])
        list(brain.reply("как дела?"))
        rounds = brain.last_timing["rounds"]
        self.assertEqual(len(rounds), 1)
        self.assertGreater(rounds[0]["sent"], 0.0)
        self.assertGreaterEqual(rounds[0]["word"], rounds[0]["sent"])
        self.assertGreaterEqual(rounds[0]["sentence"], rounds[0]["word"])

    def test_journal_line_shows_where_the_wait_went(self):
        # Строка замера должна попасть в журнал для диагностики задержки.
        line = WebRuntime._log_messages("timing", {
            "total": 3.2, "stt": 0.2, "duck": 0.05, "word": 1.1, "sentence": 1.6,
            "synth": 0.45, "say": 0.02, "rounds": 1,
        })[0]
        self.assertTrue(line.startswith("задержка 3.20 с: "), line)
        for part in ("слух 0.20", "приглушение 0.05", "модель до 1-го слова 1.10",
                     "до 1-го предложения 1.60", "синтез 1-го куска 0.45",
                     "в телефон 0.02", "инструменты 1 круг"):
            self.assertIn(part, line)

    def test_journal_line_marks_a_stage_that_never_happened(self):
        # Этапа, которого не было, — прочерк, а не ноль: ноль означал бы
        # «заняло ноль секунд», скрывая отсутствие этапа.
        line = WebRuntime._log_messages("timing", {
            "total": 8.0, "stt": 0.2, "duck": 0.05, "word": None,
            "sentence": None, "synth": None, "say": None, "rounds": 2,
        })[0]
        self.assertIn("модель до 1-го слова —", line)
        self.assertIn("синтез 1-го куска —", line)
        self.assertIn("слух 0.20", line)
        self.assertIn("инструменты 2 круг", line)


if __name__ == "__main__":
    unittest.main()
