"""Раскладка, музыка и звук компьютера — три дела голосом и по-разному.

Главное здесь — что в тестах не происходит ничего настоящего: ни одного
нажатия медиа-клавиши, ни одного сообщения окну, ни одной правки громкости на
машине хозяина. Подменены `hotkeys.press`, `PostMessageW`,
`GetKeyboardLayoutList`, `GetKeyboardLayout`, `GetForegroundWindow` и объект
громкости pycaw, поэтому проверяется решение Трубы, а не её действия.

Проверяется четыре вещи:
- раскладка ищется по основному языку, поэтому en-GB подходит наравне с
  en-US, а «раскладки в системе нет» — честная неудача, а не выдумка;
- медиа-клавиша уходит в `press` тем кодом, которым Windows её понимает;
- громкость Windows не путается с громкостью её голоса, а число в ответе
  читается вслух словами;
- мгновенная команда («пауза», «звук компа на тридцать») и инструмент модели
  приводят к тому же действию, а простое «тише» — это по-прежнему громкость
  её голоса.
"""

import json
import threading
import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import abilities, commands, hands, hotkeys, pc_control
from core.brain import Brain

APPS = [
    {"id": "youtube", "title": "YouTube", "kind": "url", "url": "https://youtube.com"},
    {"id": "discord", "title": "Discord", "kind": "app", "path": "Discord.exe"},
    {"id": "telegram", "title": "Telegram", "kind": "app", "path": "Telegram.exe"},
    {"id": "firefox", "title": "Firefox", "kind": "app", "path": "firefox.exe"},
]

# HKL раскладок: у en-US и en-GB основной язык один и тот же (0x09), у
# русского — 0x19. Ищем именно по нему, а не по точному HKL.
EN_US = 0x04090409
EN_GB = 0x08090809
RU = 0x04190419
DE = 0x04070407  # немецкая — «другая»

_active = []


def _patch(patcher):
    """Включает подмену и запоминает её, чтобы снять в конце всего файла."""
    patcher.start()
    _active.append(patcher)
    return patcher


def tearDownModule():
    for patcher in reversed(_active):
        patcher.stop()
    _active.clear()


# --- Подставная оконная система --------------------------------------------


def _окна(layouts, before=RU, after=None, focus=0, accepted=True, hwnd=0x1234):
    """Подменяет всю оконную часть `pc_control` и возвращает список сообщений.

    `after` — раскладка, которая прочитается после сообщения; по умолчанию она
    равна `before`, то есть «окно её не взяло» и мы честно говорим об отказе.
    """
    if after is None:
        after = before
    state = {"layout": before}
    posted = []

    def post(target, message, wparam, lparam):
        posted.append((target, message, wparam, lparam))
        if accepted:
            state["layout"] = after
        return accepted

    _patch(mock.patch.object(pc_control, "GetForegroundWindow", lambda: hwnd))
    _patch(mock.patch.object(pc_control, "GetWindowThreadProcessId", lambda _h: 77))
    _patch(mock.patch.object(pc_control, "GetKeyboardLayoutList", lambda: list(layouts)))
    _patch(mock.patch.object(pc_control, "GetKeyboardLayout",
                             lambda _t: state["layout"]))
    _patch(mock.patch.object(pc_control, "GetGUIThreadInfo", lambda _t: focus))
    _patch(mock.patch.object(pc_control, "PostMessageW", post))
    # Настоящие 150 мс ожидания в тесте не нужны: проверяем решение, а не время.
    _patch(mock.patch.object(pc_control, "time", NS(sleep=lambda _s: None)))
    return posted


# --- Подставная громкость ----------------------------------------------------


class _Громкость:
    """IAudioEndpointVolume из трёх строк: помнит, что в него ставили."""

    def __init__(self, level=0.5, muted=False):
        self.level = float(level)
        self.muted = bool(muted)
        self.calls = []

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, value, _ctx):
        self.calls.append(("set", float(value)))
        self.level = float(value)

    def SetMute(self, value, _ctx):
        self.calls.append(("mute", int(value)))
        self.muted = bool(value)

    def GetMute(self):
        return self.muted


def _звук(level=0.4, muted=False):
    """Подставляет громкость и возвращает её: видно, что в неё ставили."""
    endpoint = _Громкость(level, muted)
    _patch(mock.patch.object(pc_control, "_endpoint_volume", lambda: endpoint))
    return endpoint



# --- Раскладка ---------------------------------------------------------------


class LayoutTests(unittest.TestCase):
    def test_a_language_is_looked_up_by_its_primary_code(self):
        # en-GB отличается от en-US только подстановкой, а хозяин говорит
        # «английская» — подойдёт любая из них.
        posted = _окна([EN_GB, RU], before=RU, after=EN_GB)
        answer = pc_control.switch_layout("en")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["lang"], "en")
        self.assertTrue(answer["changed"])
        target, message, wparam, lparam = posted[0]
        self.assertEqual(message, pc_control.WM_INPUTLANGCHANGEREQUEST)
        self.assertEqual(wparam, 0)
        # В сообщении — найденный HKL, а не код, который хозяин назвал.
        self.assertEqual(lparam, EN_GB)

    def test_both_english_spellings_are_accepted(self):
        for en in (EN_US, EN_GB):
            with self.subTest(раскладка=hex(en)):
                posted = _окна([en, RU], before=RU, after=en)
                self.assertTrue(pc_control.switch_layout("en")["ok"])
                self.assertEqual(posted[0][3], en)

    def test_the_frame_first_then_the_focus_window(self):
        # 30.09: hwndFocus окна пульта (WebView2) просьбу не взял, а окно
        # целиком — взяло. Сначала рамка; сработало — вторую не шлём.
        posted = _окна([EN_US, RU], before=RU, after=EN_US, focus=0xBEEF)
        self.assertTrue(pc_control.switch_layout("en")["ok"])
        self.assertEqual([p[0] for p in posted], [0x1234])

    def test_focus_window_is_the_second_try(self):
        # Рамка не взяла — пробуем окно ввода: некоторые программы слушают его.
        posted = _окна([EN_US, RU], before=RU, focus=0xBEEF)
        сдвиг = {"n": 0}
        настоящий = pc_control.PostMessageW

        def вторая_берёт(target, message, wparam, lparam):
            ответ = настоящий(target, message, wparam, lparam)
            сдвиг["n"] += 1
            return ответ

        with mock.patch.object(pc_control, "GetKeyboardLayout",
                               lambda _t: EN_US if сдвиг["n"] >= 2 else RU), \
                mock.patch.object(pc_control, "PostMessageW", вторая_берёт):
            answer = pc_control.switch_layout("en")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual([p[0] for p in posted], [0x1234, 0xBEEF])

    def test_the_frame_takes_the_message_when_there_is_no_focus(self):
        posted = _окна([EN_US, RU], before=RU, after=EN_US, focus=0)
        pc_control.switch_layout("en")
        self.assertEqual(posted[0][0], 0x1234)

    def test_a_missing_layout_is_honest_about_it(self):
        _окна([RU, DE], before=RU)
        answer = pc_control.switch_layout("en")
        self.assertFalse(answer["ok"])
        self.assertIn("раскладки в системе нет", answer["text"])

    def test_the_current_one_is_not_switched_again(self):
        posted = _окна([EN_US, RU], before=EN_US)
        answer = pc_control.switch_layout("en")
        self.assertTrue(answer["ok"])
        self.assertFalse(answer["changed"])
        self.assertEqual(answer["text"], "И так английская.")
        self.assertEqual(posted, [])

    def test_next_asks_the_system_for_the_following_one(self):
        posted = _окна([EN_US, RU], before=RU, after=EN_US)
        answer = pc_control.switch_layout("next")
        self.assertTrue(answer["ok"], answer)
        # Следующая по кругу из списка, а не HKL_NEXT: его окна понимают через раз.
        self.assertEqual(posted[0][3], EN_US)

    def test_next_with_a_single_layout_is_honest(self):
        posted = _окна([RU], before=RU)
        answer = pc_control.switch_layout("next")
        self.assertFalse(answer["ok"])
        self.assertIn("одна раскладка", answer["text"])
        self.assertEqual(posted, [])

    def test_a_window_that_ignored_the_request_is_not_a_success(self):
        # Система молчит, а проверить нечем — говорим правду, а не «переключила».
        _окна([EN_US, RU], before=RU, after=RU)
        answer = pc_control.switch_layout("en")
        self.assertFalse(answer["ok"])
        self.assertIn("не переключилась", answer["text"])

    def test_a_window_that_refused_the_message_says_so(self):
        _окна([EN_US, RU], before=RU, after=EN_US, accepted=False)
        answer = pc_control.switch_layout("en")
        self.assertFalse(answer["ok"])
        self.assertIn("не приняло", answer["text"])

    def test_no_active_window_is_not_a_crash(self):
        _окна([EN_US, RU], hwnd=0)
        answer = pc_control.switch_layout("en")
        self.assertFalse(answer["ok"])
        self.assertIn("окна", answer["text"])

    def test_an_unknown_language_is_refused_without_touching_anything(self):
        posted = _окна([EN_US, RU])
        answer = pc_control.switch_layout("кхмерский")
        self.assertFalse(answer["ok"])
        self.assertEqual(posted, [])

    def test_the_said_text_names_the_new_layout(self):
        _окна([EN_US, RU], before=RU, after=EN_US)
        answer = pc_control.switch_layout("en")
        self.assertEqual(answer["text"], "Теперь английская раскладка.")
        self.assertEqual(answer["journal"], "раскладка: английская")

    def test_current_layout_names_the_language(self):
        _окна([EN_US, RU], before=RU)
        self.assertEqual(pc_control.current_layout(), "ru")
        _окна([EN_US, RU], before=EN_GB)
        self.assertEqual(pc_control.current_layout(), "en")
        # Немецкая — не наш язык: молчим, а не выдумываем «другую».
        _окна([EN_US, DE], before=DE)
        self.assertEqual(pc_control.current_layout(), "")


# --- Музыка -----------------------------------------------------------------


class MediaTests(unittest.TestCase):
    def _press(self, action):
        """Нажимает клавишу подменой и возвращает ответ и ушедшие коды."""
        seen = []
        _patch(mock.patch.object(hotkeys, "press",
                                 lambda keys: seen.append(list(keys))))
        return pc_control.media(action), seen

    def test_each_action_presses_its_own_virtual_key(self):
        # Коды из документации Windows: play/pause 0xB3, next 0xB0,
        # previous 0xB1, stop 0xB2.
        for action, vk in (("play_pause", 0xB3), ("next", 0xB0),
                           ("previous", 0xB1), ("stop", 0xB2)):
            with self.subTest(действие=action):
                answer, seen = self._press(action)
                self.assertTrue(answer["ok"], answer)
                self.assertEqual(seen, [[vk]])
                self.assertEqual(answer["action"], action)

    def test_the_answer_promises_only_the_key_pressed(self):
        # Проигрывание мы не проверяем, поэтому «Нажала паузу», а не «музыка на
        # паузе»: выдуманный факт хозяин потом будет искать.
        for action, слова in (("play_pause", "Нажала паузу."),
                              ("next", "Следующий трек."),
                              ("previous", "Предыдущий трек."),
                              ("stop", "Нажала стоп.")):
            with self.subTest(действие=action):
                answer, _ = self._press(action)
                self.assertEqual(answer["text"], слова)

    def test_the_journal_says_what_was_pressed(self):
        answer, _ = self._press("next")
        self.assertEqual(answer["journal"], "музыка: следующий трек")

    def test_an_unknown_action_presses_nothing(self):
        answer, seen = self._press("перемотать на пять минут")
        self.assertFalse(answer["ok"])
        self.assertEqual(seen, [])


# --- Громкость компьютера ---------------------------------------------------


class PcVolumeTests(unittest.TestCase):
    def test_set_writes_the_level_asked_for(self):
        endpoint = _звук(0.3)
        answer = pc_control.pc_volume("set", level=30)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["level"], 30)
        self.assertIn(("set", 0.30), endpoint.calls)

    def test_the_answer_says_the_number_in_words(self):
        # Ответ читается вслух синтезом, а цифры он не читает; «процентов» —
        # с правильным падежом, иначе звучит «тридцать процент».
        _звук(0.3)
        for level, слова in ((30, "Звук компьютера — тридцать процентов."),
                             (5, "Звук компьютера — пять процентов."),
                             (100, "Звук компьютера — сто процентов."),
                             (21, "Звук компьютера — двадцать один процент.")):
            with self.subTest(уровень=level):
                self.assertEqual(pc_control.pc_volume("set", level=level)["text"],
                                 слова)

    def test_the_journal_says_the_number_as_digits(self):
        # В журнале наоборот — цифрами: там его читает хозяин глазами.
        _звук(0.3)
        answer = pc_control.pc_volume("set", level=30)
        self.assertEqual(answer["journal"], "звук компьютера: 30 %")

    def test_up_and_down_move_by_the_step(self):
        endpoint = _звук(0.5)
        self.assertEqual(pc_control.pc_volume("up")["level"], 60)
        self.assertIn(("set", 0.6), endpoint.calls)
        # Второй шаг считается от того, что стало: 60 − 10 = 50.
        self.assertEqual(pc_control.pc_volume("down")["level"], 50)
        self.assertIn(("set", 0.5), endpoint.calls)

    def test_the_step_can_be_asked_for_explicitly(self):
        _звук(0.5)
        self.assertEqual(pc_control.pc_volume("up", step=25)["level"], 75)
        self.assertEqual(pc_control.pc_volume("down", step=25)["level"], 50)

    def test_the_edges_are_the_answer_itself(self):
        _звук(1.0)
        self.assertEqual(pc_control.pc_volume("up")["text"], "Громче некуда.")
        endpoint = _звук(0.0)
        answer = pc_control.pc_volume("down")
        self.assertEqual(answer["text"], "Тише некуда.")
        # И ползунок при этом не трогаем: некуда и нечего.
        self.assertEqual(answer["level"], 0)
        self.assertEqual(endpoint.calls, [])

    def test_zero_and_hundred_are_real_levels(self):
        _звук(0.7)
        self.assertEqual(pc_control.pc_volume("set", level=0)["level"], 0)
        self.assertEqual(pc_control.pc_volume("set", level=100)["level"], 100)

    def test_mute_and_unmute(self):
        endpoint = _звук(0.4)
        answer = pc_control.pc_volume("mute")
        self.assertTrue(answer["ok"])
        self.assertTrue(endpoint.muted)
        self.assertEqual(answer["text"], "Выключила звук.")
        self.assertEqual(answer["journal"], "звук компьютера: выключен")
        answer = pc_control.pc_volume("unmute")
        self.assertFalse(endpoint.muted)
        self.assertEqual(answer["text"], "Включила звук.")

    def test_moving_the_volume_takes_the_mute_off(self):
        # Иначе хозяин крутит ползунок, не слышит ничего и решает, что
        # громкость сломалась.
        endpoint = _звук(0.4, muted=True)
        pc_control.pc_volume("up")
        self.assertFalse(endpoint.muted)
        self.assertIn(("mute", 0), endpoint.calls)

    def test_get_only_reads(self):
        endpoint = _звук(0.3, muted=True)
        answer = pc_control.pc_volume("get")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["level"], 30)
        self.assertTrue(answer["muted"])
        self.assertEqual(answer["text"], "Звук компьютера выключен.")
        self.assertEqual(endpoint.calls, [])

    def test_an_unknown_action_changes_nothing(self):
        endpoint = _звук(0.4)
        answer = pc_control.pc_volume("на двадцать")
        self.assertFalse(answer["ok"])
        self.assertEqual(endpoint.calls, [])

    def test_set_without_a_number_asks_instead_of_guessing(self):
        endpoint = _звук(0.4)
        answer = pc_control.pc_volume("set")
        self.assertFalse(answer["ok"])
        self.assertIn("процентов", answer["text"])
        self.assertEqual(endpoint.calls, [])

    def test_a_level_outside_the_scale_is_not_a_level(self):
        _звук(0.4)
        self.assertFalse(pc_control.pc_volume("set", level=120)["ok"])
        self.assertFalse(pc_control.pc_volume("set", level=-10)["ok"])


# --- Вызов модели ------------------------------------------------------------


class RunToolTests(unittest.TestCase):
    """`run_tool` — мост между облаком и `pc_control`: JSON туда, JSON обратно."""

    def test_broken_arguments_are_an_error_and_not_an_exception(self):
        for плохо in ("{не json", "[]", '"строка"', ""):
            with self.subTest(аргументы=плохо):
                answer = json.loads(pc_control.run_tool(hands.MEDIA_NAME, плохо))
                self.assertIn("error", answer)
                self.assertTrue(answer["text"])

    def test_an_unknown_tool_is_an_error(self):
        answer = json.loads(pc_control.run_tool("выключи_компьютер", "{}"))
        self.assertIn("error", answer)
        self.assertIn("выключи_компьютер", answer["error"])

    def test_an_unknown_action_comes_back_as_an_error(self):
        # Не «error» спрятанной в тексте, а именно error впереди: голосовой цикл
        # по нему не станет подтверждать невыполненное.
        answer = json.loads(
            pc_control.run_tool(hands.PCVOL_NAME, '{"action": "на двадцать"}'))
        self.assertIn("error", answer)
        self.assertTrue(answer["text"])

    def test_a_failure_from_below_stays_a_failure_up_here(self):
        _окна([RU, DE], before=RU)
        answer = json.loads(
            pc_control.run_tool(hands.LAYOUT_NAME, '{"lang": "en"}'))
        self.assertIn("error", answer)
        self.assertIn("раскладки в системе нет", answer["error"])

    def test_a_success_carries_the_ready_made_phrase(self):
        _окна([EN_US, RU], before=RU, after=EN_US)
        answer = json.loads(
            pc_control.run_tool(hands.LAYOUT_NAME, '{"lang": "en"}'))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["text"], "Теперь английская раскладка.")
        self.assertEqual(answer["journal"], "раскладка: английская")

    def test_the_three_tools_reach_their_own_function(self):
        _звук(0.4)
        _окна([EN_US, RU], before=RU, after=EN_US)
        with mock.patch.object(hotkeys, "press") as press:
            answer = json.loads(pc_control.run_tool(
                hands.LAYOUT_NAME, '{"lang": "ru"}'))
            self.assertIn("раскладка: русская", answer["journal"])
            answer = json.loads(pc_control.run_tool(
                hands.MEDIA_NAME, '{"action": "play_pause"}'))
            press.assert_called_with([0xB3])
        self.assertEqual(answer["text"], "Нажала паузу.")
        answer = json.loads(pc_control.run_tool(
            hands.PCVOL_NAME, '{"action": "set", "level": 30}'))
        self.assertEqual(answer["level"], 30)



# --- Мгновенная команда -----------------------------------------------------


class InstantCommandTests(unittest.TestCase):
    """Разбор фраз из `core/commands.py` — без единого настоящего действия."""

    def test_every_layout_form_keeps_the_language(self):
        for фраза, kind in (("переключи на английский", "en"),
                            ("поменяй раскладку на русский", "ru"),
                            ("английская раскладка", "en"),
                            ("переключись на русский", "ru")):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual((order.action, order.kind), ("layout", kind))

    def test_layout_without_a_language_is_the_next_one(self):
        order = commands.understand("смени раскладку", APPS)
        self.assertEqual((order.action, order.kind), ("layout", "next"))

    def test_layout_with_a_tail_goes_to_the_model(self):
        # «смени раскладку в игре» — это не только про раскладку.
        self.assertIsNone(commands.understand("смени раскладку в игре", APPS))

    def test_every_media_form_keeps_its_key(self):
        for фраза, kind in (("пауза", "play_pause"),
                            ("на паузу", "play_pause"),
                            ("поставь на паузу", "play_pause"),
                            ("сними с паузы", "play_pause"),
                            ("продолжи музыку", "play_pause"),
                            ("следующий трек", "next"),
                            ("следующая песня", "next"),
                            ("включи следующую песню", "next"),
                            ("предыдущий трек", "previous")):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual((order.action, order.kind), ("media", kind))

    def test_media_with_a_tail_is_not_a_command(self):
        # «Пауза в игре» и «на паузу поставь видео…» — разговор, а не кнопка.
        for фраза in ("пауза в игре",
                      "на паузу поставь видео в браузере и скажи что там",
                      "следующий трек в плейлисте дискорда"):
            with self.subTest(фраза=фраза):
                self.assertIsNone(commands.understand(фраза, APPS), фраза)

    def test_a_media_phrase_beats_the_program_launcher(self):
        # «Включи следующий трек» начинается с «включи» — без порядка в таблице
        # фраза уехала бы в запуск программы.
        order = commands.understand("включи следующий трек", APPS)
        self.assertEqual((order.action, order.kind), ("media", "next"))

    def test_the_level_is_read_from_digits_and_from_words(self):
        for фраза, level in (("звук компа на тридцать", 30),
                             ("звук компа на 30", 30),
                             ("громкость компьютера пятьдесят", 50),
                             ("звук компа на 50", 50),
                             ("звук компа на десять", 10),
                             ("звук компа на сто", 100),
                             ("звук компа на ноль", 0),
                             ("звук компа на половину", 50),
                             ("звук компа на тридцать пять", 35)):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual((order.action, order.kind), ("pc_volume", "set"))
                self.assertEqual(order.level, level)

    def test_an_unreadable_level_goes_to_the_model(self):
        # Молчание хуже, чем один лишний круг в облако.
        self.assertIsNone(commands.understand("звук компа на ноль-ноль", APPS))

    def test_the_direction_comes_from_the_words_not_from_the_group(self):
        # В группе `dir` стоит только «по», а «потише» и «тише» — одно и то же.
        for фраза, kind in (("сделай музыку потише", "down"),
                            ("сделай музыку тише", "down"),
                            ("звук компа громче", "up"),
                            ("сделай музыку громче", "up"),
                            ("сделай звук компьютера тише", "down")):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual((order.action, order.kind), ("pc_volume", kind))

    def test_mute_and_unmute(self):
        for фраза, kind in (("выключи звук", "mute"),
                            ("выключи звук компьютера", "mute"),
                            ("включи звук", "unmute"),
                            ("включи звук компьютера", "unmute")):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual((order.action, order.kind), ("pc_volume", kind))

    def test_a_tail_after_the_sound_turns_it_into_a_conversation(self):
        # «Выключи звук в дискорде» — это про дискорд, и регуляркой не отличить.
        self.assertIsNone(commands.understand("выключи звук в дискорде", APPS))
        self.assertIsNone(commands.understand("звук компа громче в игре", APPS))

    def test_a_missing_argument_gets_a_sane_default(self):
        # Модель может забыть поле: пустое — это не исключение, а действие
        # по умолчанию (следующая раскладка, текущая громкость).
        _окна([EN_US, RU], before=RU, after=EN_US)
        answer = json.loads(pc_control.run_tool(hands.LAYOUT_NAME, "{}"))
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["lang"], "en")
        _звук(0.4)
        self.assertTrue(json.loads(
            pc_control.run_tool(hands.PCVOL_NAME, "{}"))["ok"])


    def test_a_broken_device_is_a_failure_and_not_a_crash(self):
        def boom():
            raise OSError("устройства вывода нет")

        with mock.patch.object(pc_control, "_endpoint_volume", boom):
            answer = pc_control.pc_volume("get")
        self.assertFalse(answer["ok"])
        self.assertIn("недоступна", answer["text"])


    def test_a_failed_press_is_reported_and_never_raises(self):
        def boom(_keys):
            raise OSError("SendInput не сработала")

        _patch(mock.patch.object(hotkeys, "press", boom))
        answer = pc_control.media("play_pause")
        self.assertFalse(answer["ok"])
        self.assertIn("клавиша", answer["text"])


    def test_the_computer_sound_beats_the_voice_volume(self):
        # «Сделай музыку потише» — это компьютер; «тише» без слова «музыка» —
        # по-прежнему громкость её голоса, иначе она лишилась бы обеих.
        self.assertEqual(commands.understand("сделай музыку потише", APPS).action,
                         "pc_volume")
        for фраза in ("тише", "громче", "потише", "громкость пять", "убавь громкость"):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual(order.action, "volume", фраза)

    def test_the_sound_phrase_is_not_a_close_and_not_a_launch(self):
        self.assertEqual(commands.understand("выключи звук", APPS).action,
                         "pc_volume")
        self.assertEqual(commands.understand("включи звук", APPS).action,
                         "pc_volume")

    def test_the_old_commands_still_work(self):
        for фраза, action in (("сделай скриншот", "screenshot"),
                              ("клип", "moment"),
                              ("заметка", "note"),
                              ("открой ютуб", "launch"),
                              ("закрой дискорд", "close"),
                              ("выключи телеграм", "close"),
                              ("включи фаерфокс", "launch"),
                              ("поищи кошек", "search"),
                              ("открой на ютубе ремонт видеокарты", "youtube")):
            with self.subTest(фраза=фраза):
                order = commands.understand(фраза, APPS)
                self.assertIsNotNone(order, фраза)
                self.assertEqual(order.action, action, фраза)

    def test_an_unparsed_phrase_is_none_and_does_not_break_the_next_one(self):
        # `_BUILD` вправе вернуть None: значит «не моё», и разбор идёт дальше.
        self.assertIsNone(commands.understand("звук компа на ноль-ноль", APPS))
        self.assertEqual(commands.understand("звук компа на 30", APPS).action,
                         "pc_volume")

    def test_the_pult_sees_the_three_new_lines(self):
        guide = commands.commands_guide()
        строки = {строка["id"]: строка for строка in guide["commands"]}
        for id_ in ("layout", "media", "pc_volume"):
            with self.subTest(команда=id_):
                self.assertIn(id_, строки)
                self.assertTrue(строки[id_]["examples"], id_)
                self.assertTrue(строки[id_]["does"], id_)

    def test_the_three_come_before_the_voice_volume(self):
        # Порядок в таблице значим: «выключи звук» без него ушло бы в закрытие
        # программы, а «включи следующий трек» — в её запуск.
        порядок = [spec.id for spec in commands.COMMANDS]
        for id_ in ("layout", "media", "pc_volume"):
            for раньше in ("volume", "close", "launch"):
                self.assertLess(порядок.index(id_), порядок.index(раньше))


# --- Инструменты модели -----------------------------------------------------


class HandsTests(unittest.TestCase):
    """Объявления и место в списках: судья здесь лишняя секунда, а не защита."""

    def test_all_three_are_local_and_therefore_guarded(self):
        for name in (hands.LAYOUT_NAME, hands.MEDIA_NAME, hands.PCVOL_NAME):
            with self.subTest(инструмент=name):
                self.assertIn(name, hands.LOCAL)
                self.assertIn(name, hands.GUARDED)

    def test_none_of_them_is_judged(self):
        # Действия безвредные и обратимые: лишняя секунда проверки не нужна.
        for name in (hands.LAYOUT_NAME, hands.MEDIA_NAME, hands.PCVOL_NAME):
            with self.subTest(инструмент=name):
                self.assertNotIn(name, hands.JUDGED)

    def test_the_quote_is_required_of_each_one(self):
        # Без `because` модель выдумывает цитату, и действие не выполняется.
        for tool in hands.PC_TOOLS:
            with self.subTest(инструмент=tool["function"]["name"]):
                self.assertIn("because", tool["function"]["parameters"]["required"])

    def test_the_enums_are_the_ones_pc_control_understands(self):
        # Не «просто перечислили», а ровно те значения, которые pc_control
        # потом и разбирает: лишнее в enum модель выберет, а получим отказ.
        props = [tool["function"]["parameters"]["properties"]
                 for tool in hands.PC_TOOLS]
        self.assertEqual(set(props[0]["lang"]["enum"]),
                         set(pc_control.LAYOUT_LANG) | {"next"})
        self.assertEqual(set(props[1]["action"]["enum"]),
                         set(pc_control.MEDIA_VK))
        self.assertEqual(set(props[2]["action"]["enum"]),
                         set(pc_control.VOLUME_ACTIONS))
        # Уровень ограничен той же шкалой, что и громкость в самой Windows.
        self.assertEqual(
            (props[2]["level"]["minimum"], props[2]["level"]["maximum"]),
            (0, 100))


    def test_the_voice_repeats_the_answer_verbatim(self):
        for name in (hands.LAYOUT_NAME, hands.MEDIA_NAME, hands.PCVOL_NAME):
            with self.subTest(инструмент=name):
                self.assertEqual(hands.CONFIRM[name], ("{text}",))

    def test_the_descriptions_say_where_not_to_call_them(self):
        # «Тише» без слова «комп» — громкость её голоса; «клип» — save_moment.
        self.assertIn("не зови", hands.PCVOL_TOOL["function"]["description"].lower())
        self.assertIn("клип", hands.MEDIA_TOOL["function"]["description"].lower())

    def test_the_abilities_list_names_the_three(self):
        текст = abilities.describe(APPS)
        for слово in ("раскладку", "музыкой", "громкость звука компьютера"):
            with self.subTest(слово=слово):
                self.assertIn(слово, текст)



# --- Набор инструментов и раздача -------------------------------------------


def _чан(content=None, calls=None):
    delta = NS(content=content, tool_calls=calls, reasoning_content=None)
    return NS(choices=[NS(delta=delta, finish_reason=None)])


def _вызов(name, args):
    return [_чан(calls=[NS(index=0, id="c1",
                            function=NS(name=name, arguments=args))])]


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.bodies = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **body):
        self.bodies.append(body)
        return iter(self.streams.pop(0))


def _мозг(streams):
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


def _набор(brain):
    return [tool["function"]["name"]
            for tool in brain._tool_list(False, "переключи на английский")]


class BrainToolsTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        self.addCleanup(self._restore)
        patcher = mock.patch.object(hands, "read_list", return_value=APPS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        config.WEB_SEARCH = self._saved

    def test_all_three_are_always_in_the_set(self):
        names = _набор(_мозг([]))
        for name in (hands.LAYOUT_NAME, hands.MEDIA_NAME, hands.PCVOL_NAME):
            with self.subTest(инструмент=name):
                self.assertIn(name, names)

    def test_the_set_does_not_depend_on_programs_or_actions(self):
        # Ни apps.json, ни настроек голоса эти три не требуют: иначе на
        # «а ты можешь паузу сделать?» модели нечем было бы ответить.
        brain = _мозг([])
        brain.actions = {}
        self.assertEqual(_набор(brain), _набор(_мозг([])))

    def test_the_order_of_the_set_is_the_same_twice(self):
        # На порядке держится кеш запроса: сдвиг строки вверх обесценивает его.
        brain = _мозг([])
        self.assertEqual(_набор(brain), _набор(brain))

    def test_they_sit_right_after_the_dictation_and_before_the_actions(self):
        # Блок заметок и диктовки, потом сразу они, потом действия: тот же
        # порядок в каждом запросе, на нём держится кеш.
        brain = _мозг([])
        brain.actions = {"screenshot": lambda: "ок"}
        names = _набор(brain)
        начало = names.index(hands.LAYOUT_NAME)
        self.assertEqual(names[начало:начало + 3],
                         [hands.LAYOUT_NAME, hands.MEDIA_NAME, hands.PCVOL_NAME])
        self.assertLess(names.index("read_notes"), начало)
        self.assertLess(начало, names.index("take_screenshot"))

    def test_a_call_reaches_pc_control_and_lands_in_the_journal(self):
        _окна([EN_US, RU], before=RU, after=EN_US)
        events = []
        brain = _мозг([_вызов(hands.LAYOUT_NAME,
                               '{"lang": "en", "because": "переключи на английский"}'),
                       [_чан("Переключила.")],
                       [_чан("Готово.")]])
        brain.on_event = lambda kind, payload: events.append((kind, payload))
        list(brain.reply("переключи на английский"))
        self.assertIn(("pc", "раскладка: английская"), events)

    def test_a_long_phrase_goes_through_the_model_to_the_same_action(self):
        # Короткое «пауза» делается на месте, а «а ты можешь сделать паузу?» —
        # через инструмент; действие одно и то же.
        _звук(0.4)
        seen = []
        _patch(mock.patch.object(hotkeys, "press",
                                 lambda keys: seen.append(list(keys))))
        brain = _мозг([_вызов(hands.MEDIA_NAME,
                               '{"action": "play_pause", "because": "сделать паузу"}'),
                       [_чан("Нажала.")], [_чан("Готово.")]])
        list(brain.reply("а ты можешь сделать паузу?"))
        self.assertEqual(seen, [[0xB3]])

    def test_both_paths_end_in_the_same_place(self):
        # Голосом: `_run_command` зовёт `pc_control`; по-разному: `run_tool`.
        # Проверяем, что обе дороги дают одну и ту же фразу вслух.
        _звук(0.4)
        через_инструмент = json.loads(pc_control.run_tool(
            hands.PCVOL_NAME, '{"action": "mute"}'))["text"]
        self.assertEqual(через_инструмент, "Выключила звук.")
        order = commands.understand("выключи звук", APPS)
        self.assertEqual(order.action, "pc_volume")
        self.assertEqual(order.kind, "mute")
        self.assertEqual(pc_control.pc_volume(order.kind, level=order.level)["text"],
                         через_инструмент)


if __name__ == "__main__":
    unittest.main()
