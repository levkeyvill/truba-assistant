"""Свечение телефона: рамка идёт за состояниями.

Хозяин жаловался: «непонятно, услышала она меня или нет». Рамка по краям
экрана теперь повторяет её состояние — «слушаю» (окно открыто), «думаю»
(фраза дошла), диктовка. Сверки голоса на лету и вспышек «принято /
пропущено» больше нет: 27.09 они загорались то так, то эдак, и понять по ним
ничего было нельзя. Осталось проверить ровно то, чем телефон живёт:

* `Listener.on_voice` — начало речи, громкость внутри фразы (не чаще раза в
  0.1 с) и конец; выброшенная короткая фраза тоже гасит рамку, а жёсткий
  разрез длинной — нет, потому что человек не замолкал;
* падающий подписчик не роняет нарезку: микрофон важнее подсветки;
* `PhoneServer.send_dictation` кладёт в `_broadcast` JSON нужного вида;
* страница знает про `#edge` и движется только прозрастью, без
  `requestAnimationFrame`.

Ни микрофона, ни звука, ни сети: детектор речи и телефон подменены, сигнал
синтетический, файл страницы только читается.
"""

import json
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core.audio_in import VAD_HOP, Listener, voice_level
from core.phone import PhoneServer
from core.voice_loop import VoiceLoop

PAGE = Path(config.ROOT) / "web" / "index.html"


# --- Нарезка фраз на синтетическом сигнале --------------------------------


class _Vad:
    """Детектор речи по расписанию, а не по настоящему звуку.

    `plan` — вероятность по кускам: 1.0 означает речь, 0.0 — тишину.
    Настоящий Silero тут не нужен: проверяется нарезка, а не распознавание.
    """

    def __init__(self, plan: list[float]):
        self.plan = list(plan)
        self.at = 0

    def probability(self, chunk) -> float:
        value = self.plan[self.at] if self.at < len(self.plan) else 0.0
        self.at += 1
        return value


class _Очередь(queue.Queue):
    """Очередь микрофона, которая по концу записи останавливает слушателя.

    Настоящая очередь живёт вечно: `phrases()` блокируется в ожидании следующего
    блока, пока его не пришлёт живой микрофон. Здесь запись кончилась — и ждать
    больше нечего.
    """

    def __init__(self, stop, blocks: int):
        super().__init__()
        self._stop = stop
        self._blocks = blocks

    def get(self, timeout=None, block=True):
        if self._blocks <= 0:
            self._stop.set()
            raise queue.Empty
        self._blocks -= 1
        return super().get(timeout=timeout, block=block)


def _куски(секунды: float) -> int:
    return int(секунды * config.SAMPLE_RATE / VAD_HOP)


def _слушатель(план: list[float], on_voice=None) -> Listener:
    """Слушатель без микрофона: очередь набита заранее, детектор — по плану."""
    stop = threading.Event()
    слушатель = object.__new__(Listener)
    # Один блок на всю запись: `phrases()` сама режет его на куски по VAD_HOP.
    слушатель._queue = _Очередь(stop, 1)
    слушатель._stop = stop
    слушатель._vad = _Vad(план)
    слушатель._barge_threshold = 0
    слушатель._may_start = lambda preroll: True
    слушатель.on_voice = on_voice
    # Громкий сигнал постоянной амплитуды: уровень у всех кусков один, и
    # проверять можно именно его, а не случайные пики.
    слушатель._queue.put(np.ones(len(план) * VAD_HOP, dtype=np.float32))
    return слушатель


def _нарезать(слушатель: Listener) -> list[np.ndarray]:
    фразы = []
    for фраза in слушатель.phrases():
        фразы.append(фраза)
    return фразы


def _речь(секунды: float) -> list[float]:
    """План: сначала столько секунд речи, потом тишина до конца фразы."""
    return [1.0] * _куски(секунды) + [0.0] * _куски(3)


def _подписчик(список: list):
    def слышу(on: bool, level: float) -> None:
        список.append((on, level))
    return слышу


class _часы:
    """Часы, идущие с куска VAD, — как настоящий микрофон.

    Синтетическая запись нарезается быстрее, чем живая, и круговые часы
    отмерили бы за всю фразу меньше сотой доли секунды. Ограничение «не чаще
    раза в 0.1 с» без таких часов проверялось бы вхолостую, а с ними видно
    ровно то, что увидит телефон.
    """

    def __enter__(self):
        self._шаг = VAD_HOP / config.SAMPLE_RATE
        self._было = time.monotonic
        self._теперь = 1000.0
        self._патч = mock.patch("core.audio_in.time.monotonic", self._тик)
        self._патч.start()
        return self

    def __exit__(self, *exc):
        self._патч.stop()
        return False

    def _тик(self):
        значение = self._теперь
        self._теперь += self._шаг
        return значение


class VoiceLevelTests(unittest.TestCase):
    """Громкость 0…1: тихо не значит «не слышно»."""

    def test_a_normal_voice_is_already_near_the_top(self):
        # Голос на расстоянии руки даёт RMS около 0.08: такой кусок должен
        # зажигать рамку целиком, иначе телефон молчал бы в самый нужный момент.
        level = voice_level(np.full(VAD_HOP, 0.08, dtype=np.float32))
        self.assertAlmostEqual(level, 1.0, places=4)
        self.assertGreater(level, 0.9)

    def test_a_quiet_voice_is_still_visible(self):
        # Вчетверо тише — рамка горит вполсилы, а не гаснет совсем.
        level = voice_level(np.full(VAD_HOP, 0.02, dtype=np.float32))
        self.assertAlmostEqual(level, 0.5, places=2)

    def test_loud_never_goes_above_one(self):
        # Крик в микрофон: выше единицы показывать нечего, иначе телефон бы
        # «захлебнулся» на CSS.
        loud = voice_level(np.full(VAD_HOP, 0.9, dtype=np.float32))
        self.assertEqual(loud, 1.0)

    def test_silence_is_zero(self):
        self.assertEqual(voice_level(np.zeros(VAD_HOP, dtype=np.float32)), 0.0)


class OnVoiceTests(unittest.TestCase):
    """Сигналы подписчика: начало речи, дыхание, конец."""

    def test_speech_start_reports_voice_right_away(self):
        # Хозяин ждёт ответа в момент начала фразы: промедление читается как
        # «не услышала».
        звонки: list = []
        _нарезать(_слушатель(_речь(1.0), _подписчик(звонки)))

        self.assertTrue(звонки, "начало речи должно сообщаться")
        self.assertTrue(звонки[0][0], "первый звонок — «слышу»")
        self.assertGreater(звонки[0][1], 0.0, "с уровнем громкости")

    def test_inside_a_phrase_the_level_comes_not_often_than_ten_a_second(self):
        # Сообщений на каждый кусок (тридцать в секунду) телефон бы не
        # переварил: рамке хватает десяти, глаз не отличает.
        #
        # Часы поддельные: синтетическая запись нарезается мгновенно, и
        # настоящие часы не успевают отмерить и сотую долю секунды —
        # ограничение «не чаще раза в 0.1 с» проверялось бы вхолостую.
        звонки: list = []
        with _часы():
            _нарезать(_слушатель(_речь(2.0), _подписчик(звонки)))

        # Две секунды речи: ждём десяток сообщений, но не больше тридцати.
        self.assertLessEqual(len(звонки), 30)
        self.assertGreater(len(звонки), 3, "громкость внутри фразы должна идти")

    def test_the_end_of_a_phrase_turns_the_glow_off(self):
        # Без этого телефон светился бы после каждой сказанной фразы.
        звонки: list = []
        _нарезать(_слушатель(_речь(1.0), _подписчик(звонки)))

        self.assertEqual(звонки[-1], (False, 0.0))

    def test_a_phrase_thrown_away_as_too_short_still_turns_it_off(self):
        # Щелчок мышью или кашель: фраза короче MIN_PHRASE, в распознавание
        # не уйдёт — но рамка обязана погаснуть, иначе горит она до
        # следующей настоящей фразы.
        короткая = [1.0] + [0.0] * _куски(3)
        звонки: list = []
        фразы = _нарезать(_слушатель(короткая, _подписчик(звонки)))

        self.assertEqual(фразы, [], "короткая фраза не должна дойти до распознавания")
        self.assertIn((False, 0.0), звонки, "рамка должна погаснуть")

    def test_a_hard_cut_of_a_long_phrase_keeps_the_glow_on(self):
        # 50 секунд без единой паузы: фраза режется на куски для
        # распознавания, но человек-то не замолкал. «Речь кончилась» здесь
        # означало бы мигание рамки на стыке.
        план = [1.0] * _куски(50) + [0.0] * _куски(3)
        звонки: list = []
        фразы = _нарезать(_слушатель(план, _подписчик(звонки)))

        self.assertGreater(len(фразы), 1, "длинная речь режется")
        # Единственное «погасить» — в самом конце, когда пришла тишина.
        погасили = [i for i, (on, _) in enumerate(звонки) if not on]
        self.assertEqual(погасили, [len(звонки) - 1], "мигать на стыке нельзя")

    def test_a_broken_subscriber_does_not_stop_the_phrases(self):
        # Связь с телефоном может оборваться в любой момент. Микрофон от
        # этого не должен замолчать: роняет цикл всё, что бросит исключение.
        def слышу(on: bool, level: float) -> None:
            raise RuntimeError("телефон отвалился")

        фразы = _нарезать(_слушатель(_речь(1.0), слышу))
        self.assertEqual(len(фразы), 1, "фраза всё равно нарезана")

    def test_without_a_subscriber_nothing_breaks(self):
        # Телефон не подключён (голос в колонки) — подписчика нет вовсе.
        self.assertIsNone(Listener.on_voice)
        фразы = _нарезать(_слушатель(_речь(1.0)))
        self.assertEqual(len(фразы), 1)


# --- Что уходит в телефон --------------------------------------------------


def _server() -> PhoneServer:
    """`PhoneServer` без сокета и потока: поднятый сервер тут не нужен."""
    server = object.__new__(PhoneServer)
    server._clients = set()
    server._audio = set()
    server._listeners = []
    server._ready = threading.Event()
    server._loop = None
    return server


def _сообщения(действие) -> list[dict]:
    """Что действие кладёт в `_broadcast`, разобранное обратно в словари."""
    сервер = _server()
    отправлено: list[dict] = []

    class _Соединение:
        def send_text(self, payload):
            отправлено.append(json.loads(payload))

    with mock.patch.object(PhoneServer, "_broadcast") as шлёт:
        действие(сервер)
    шлёт.call_args[0][0](_Соединение())
    return отправлено


class ServerGlowTests(unittest.TestCase):
    def test_dictation_says_whether_it_is_going(self):
        # Единственное сообщение о рамке, что осталось от подсветки:
        # идёт диктовка — край телефона тлеет янтарём.
        for идёт in (True, False):
            сообщения = _сообщения(lambda s, on=идёт: s.send_dictation(on))
            self.assertEqual(сообщения, [{"type": "dictation", "on": идёт}])


# --- Голосовой цикл --------------------------------------------------------


class _Телефон:
    """Сервер телефона: только то, к чему ходит рамка диктовки."""

    def __init__(self):
        self.dictation = []

    def send_dictation(self, on):
        self.dictation.append(on)


def _цикл(телефон=None) -> VoiceLoop:
    """Голосовой цикл вручную: без микрофона, синтеза и облака.

    Распознавание и сам ответ подменены — проверяется только то, что цикл
    сообщил телефону, а не как он ответил.
    """
    loop = object.__new__(VoiceLoop)
    loop._server = телефон
    loop._stt = mock.Mock()
    loop._should_answer = mock.Mock(return_value=True)
    loop._turn_body = mock.Mock()
    # Замок хода и срок захода первой создаются на месте — оставляем их None.
    loop._turn = None
    loop._first = None
    loop._first_pending = False
    loop._voice_score = None
    loop._open = True
    loop._last_turn = 1e9
    loop._search_next = False
    loop._interrupt = threading.Event()
    return loop


class DictationGlowTests(unittest.TestCase):
    """Диктовка держит янтарный край, пока идёт запись."""

    def _цикл(self, телефон=None) -> VoiceLoop:
        loop = _цикл(телефон)
        loop.events = []
        loop._emit = lambda kind, payload: loop.events.append((kind, payload))
        loop._say_back = lambda words: None
        loop._open_conversation = mock.Mock()
        loop._dictation = None
        loop._dictation_at = 0.0
        loop._dictation_command = ""
        # Голос запущен: иначе `begin_dictation` честно отвечает «диктовать
        # некому», и весь этот тест был бы про другое.
        loop.ready = True
        loop._thread = mock.Mock(is_alive=lambda: True)
        return loop

    def test_starting_the_dictation_lights_the_edge(self):
        телефон = _Телефон()
        loop = self._цикл(телефон)

        self.assertTrue(loop.begin_dictation())
        self.assertEqual(телефон.dictation, [True])

    def test_finishing_the_dictation_goes_dark(self):
        телефон = _Телефон()
        loop = self._цикл(телефон)
        loop.begin_dictation()
        loop._dictation = ["мысль про воланда"]
        loop._write_note = mock.Mock()

        loop._finish_dictation()

        self.assertEqual(телефон.dictation, [True, False])

    def test_an_empty_dictation_still_goes_dark(self):
        # «Всё» сразу после начала: заметки не будет, но край гореть не должен.
        телефон = _Телефон()
        loop = self._цикл(телефон)
        loop.begin_dictation()

        loop._finish_dictation()

        self.assertEqual(телефон.dictation, [True, False])

    def test_cancelling_the_dictation_goes_dark(self):
        телефон = _Телефон()
        loop = self._цикл(телефон)
        loop.begin_dictation()

        loop._cancel_dictation()

        self.assertEqual(телефон.dictation, [True, False])

    def test_the_watchdog_of_silence_goes_dark(self):
        # Тишина кончила запись сама — телефон об этом тоже должен узнать.
        телефон = _Телефон()
        loop = self._цикл(телефон)
        loop._stop = threading.Event()
        loop._dictation = ["надиктовано"]
        loop._dictation_at = 100.0
        loop._listener = None
        loop._write_note = mock.Mock()

        with mock.patch("core.voice_loop.time.monotonic", return_value=200.0):
            loop._check_dictation_pause()

        self.assertEqual(телефон.dictation, [False])


# --- Страница телефона -----------------------------------------------------


class PhonePageGlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_the_glow_layer_is_on_the_page(self):
        # Слой должен быть один и с `pointer-events: none`: иначе рамка
        # перехватила бы касания по подменю, погоде и снимку экрана.
        self.assertIn('id="edge"', self.html)
        self.assertRegex(self.html, r"#edge\s*\{[^}]*pointer-events:\s*none")
        self.assertRegex(self.html, r"#edge\s*\{[^}]*z-index:\s*30")

    def test_the_glow_follows_her_state(self):
        # 27.09: рамка повторяет её состояние — «слушаю» (окно открыто),
        # «думаю» (фраза дошла), диктовка. Угадывание голоса на лету убрано.
        self.assertIn("msg.type === 'dictation'", self.html)
        self.assertIn("edgeDictation(!!msg.on)", self.html)
        for имя in ("edgeApply", "edgeState", "edgeDictation"):
            self.assertIn(f"function {имя}(", self.html)
        setstate = self.html.split("function setState(name) {")[1].split("\n}")[0]
        self.assertIn("edgeState(name)", setstate)
        for вид in ("hear", "heard"):
            self.assertNotIn(f"msg.type === '{вид}'", self.html)

    def test_the_glow_does_not_spin_frames_by_hand(self):
        # Телефон на батарее: только CSS, и двигается одна прозрачность — без
        # перерисовки градиента (перелив по кругу убран 27.09).
        блок = self.html[self.html.index("function edgeApply("):
                           self.html.index("function edgeDictation(") + 1]
        self.assertNotIn("requestAnimationFrame", блок)
        self.assertNotIn("--edge-angle", self.html)
        for имя in ("edge-open", "edge-think", "edge-dict"):
            self.assertRegex(self.html, r"@keyframes\s+" + имя + r"\s*\{[^}]*opacity")

    def test_the_glow_lives_on_our_palette(self):
        # Зелёного в оформлении нет — даже тут.
        for цвет in ("#6b8afd", "#9b7bff", "#d98b52", "#ff7aa8"):
            self.assertIn(цвет, self.html)


if __name__ == "__main__":
    unittest.main()
