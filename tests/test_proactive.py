"""Заход первой: срок, решение «пора» и то, что уходит в модель.

Без сети, микрофона и настоящих снимков. Случайность подменена, `GetLastInputInfo`
вызван один раз на живой системе — просто чтобы убедиться, что вернулось
число, а не исключение.
"""

import random
import unittest

from core import proactive

# Частота, на которой в тестах «можно». База — 25 минут.
ЧАСТОТА = "sometimes"


class _Rnd(random.Random):
    """Случайность с фиксированными ответами — по одному на вызов."""

    def __init__(self, *значения):
        super().__init__(0)
        self.значения = list(значения)

    def uniform(self, a, b):
        return self.значения.pop(0) if self.значения else a

    def random(self):
        return self.значения.pop(0) if self.значения else 0.0


class ЧастотаTests(unittest.TestCase):
    def test_never_never_speaks(self):
        self.assertIsNone(proactive.base_seconds("never"))
        self.assertFalse(proactive.Schedule().due("never", 10 ** 9))

    def test_bases_match_the_rostok(self):
        self.assertEqual(proactive.base_seconds("rare"), 60 * 60)
        self.assertEqual(proactive.base_seconds(ЧАСТОТА), 25 * 60)
        self.assertEqual(proactive.base_seconds("often"), 10 * 60)

    def test_unknown_frequency_is_never(self):
        # Мусор в settings.json руками — тоже молчание, а не исключение.
        self.assertIsNone(proactive.base_seconds("каждый час"))
        self.assertIsNone(proactive.base_seconds(None))
        self.assertFalse(proactive.Schedule().due("каждый час", 10 ** 9))

    def test_is_frequency_guards_settings(self):
        self.assertTrue(proactive.is_frequency("rare"))
        self.assertFalse(proactive.is_frequency("бывает"))


class СрокTests(unittest.TestCase):
    def test_interval_is_base_times_spread(self):
        пора = proactive.Schedule(_Rnd(1.0))
        self.assertEqual(пора.next_interval(ЧАСТОТА), 25 * 60)
        шире = proactive.Schedule(_Rnd(1.4))
        self.assertAlmostEqual(шире.next_interval(ЧАСТОТА), 25 * 60 * 1.4)

    def test_spread_stays_inside_its_bounds(self):
        # Границы разброса задаёт сам модуль: иначе «случайность» могла бы
        # выдать ноль или час вместо десяти минут.
        for _ in range(200):
            срок = proactive.Schedule().next_interval("often")
            self.assertGreaterEqual(срок, 10 * 60 * proactive.SPREAD[0])
            self.assertLessEqual(срок, 10 * 60 * proactive.SPREAD[1])

    def test_reschedule_moves_the_deadline(self):
        пора = proactive.Schedule(_Rnd(1.0))
        пора.reschedule(ЧАСТОТА, 1000.0)
        self.assertEqual(пора.due_at, 1000.0 + 25 * 60)
        self.assertFalse(пора.due(ЧАСТОТА, 1000.0))
        self.assertTrue(пора.due(ЧАСТОТА, 1000.0 + 25 * 60))

    def test_switching_to_never_stops_it(self):
        # Частота меняется на ходу: выключил в пульте — и всё, тишина.
        пора = proactive.Schedule(_Rnd(1.0))
        пора.reschedule(ЧАСТОТА, 0.0)
        self.assertFalse(пора.due("never", 10 ** 9))


class ОтступTests(unittest.TestCase):
    """Заход остался без ответа — следующий срок вдвое дальше."""

    def _срок(self, пора):
        return round(пора.next_interval(ЧАСТОТА) / 25 / 60, 3)

    def test_backoff_doubles_then_stops(self):
        пора = proactive.Schedule(_Rnd(*([1.0] * 5)))
        self.assertEqual(self._срок(пора), 1.0)
        пора.missed()
        self.assertEqual(self._срок(пора), 2.0)

        пора.missed()
        self.assertEqual(self._срок(пора), 4.0)
        # Дальше не растёт: ×8 означало бы «раз в сутки».
        пора.missed()
        self.assertEqual(self._срок(пора), 4.0)

    def test_answer_resets_the_backoff(self):
        пора = proactive.Schedule(_Rnd(1.0, 1.0))
        пора.missed()
        пора.missed()
        self.assertEqual(пора.backoff, 4)
        пора.answered()
        self.assertEqual(пора.backoff, 1)
        self.assertEqual(self._срок(пора), 1.0)


# --- Решение «пора» --------------------------------------------------------

def _решает(**правки):
    """Входы, на которых заход разрешён, с одним изменённым полем."""
    входы = {
        "frequency": ЧАСТОТА,
        "voice_ready": True,
        "listen_mode": "always",
        "in_conversation": False,
        "speaking": False,
        "echoing": False,
        "cloud_down": False,
        "owner_idle": 5.0,
        "call_sounds": False,
        "silence": 4000.0,
        "now": 1000.0,
        "due_at": 900.0,
        "interval": 1500.0,
    }
    входы.update(правки)
    return proactive.should_speak(**входы)


class РешениеTests(unittest.TestCase):
    def test_all_good_speaks(self):
        self.assertEqual(_решает(), "")

    def test_never(self):
        self.assertEqual(_решает(frequency="never"), "выключено")

    def test_voice_not_ready(self):
        self.assertEqual(_решает(voice_ready=False), "голос не готов")

    def test_hearing_is_off(self):
        self.assertEqual(_решает(listen_mode="off"), "слух заглушен")

    def test_conversation_is_going(self):
        self.assertEqual(_решает(in_conversation=True), "разговор идёт")

    def test_she_is_talking(self):
        self.assertEqual(_решает(speaking=True), "говорит")
        # Хвост эха — тоже «говорит»: микрофон ещё ловит её голос.
        self.assertEqual(_решает(echoing=True), "говорит")

    def test_cloud_is_down(self):
        self.assertEqual(_решает(cloud_down=True), "облако лежит")

    def test_owner_left_the_desk(self):
        # Ровно две минуты без ввода — уже «отошёл»: сон и уход не различить.
        self.assertEqual(_решает(owner_idle=120.0), "хозяин отошёл")
        self.assertEqual(_решает(owner_idle=119.9), "")

    def test_call_is_going(self):
        self.assertEqual(_решает(call_sounds=True), "идёт созвон")

    def test_silence_is_shorter_than_the_term(self):
        self.assertEqual(_решает(silence=1499.0), "тишина короче срока")
        self.assertEqual(_решает(silence=1500.0), "")

    def test_term_has_not_come(self):
        self.assertEqual(_решает(due_at=1001.0), "срок не пришёл")

    def test_silence_ignores_the_spread(self):
        # Разброс умеет разбудить на 0.7 базы — тишины всё равно должно
        # хватить на честный срок, иначе срок перестаёт быть сроком.
        self.assertEqual(_решает(silence=100.0, now=1000.0, due_at=1000.0),
                         "тишина короче срока")


class ТекстыTests(unittest.TestCase):
    def test_prompt_asks_the_model_not_the_owner(self):
        текст = proactive.prompt(12, look=False)
        self.assertIn("12", текст)
        self.assertIn("Заговори с ним первой", текст)
        self.assertNotIn("Вот его экран", текст)

    def test_prompt_with_screen_warns_about_private_things(self):
        текст = proactive.prompt(12, look=True)
        self.assertIn("Вот его экран прямо сейчас", текст)
        self.assertIn("экран не описывай", текст)

    def test_prompt_never_says_zero_minutes(self):
        # «молчал 0 мин» выглядит как ошибка, а не как «только что».
        self.assertIn("1 мин", proactive.prompt(0, look=False))

    def test_mark_is_a_note_not_a_replica(self):
        пометка = proactive.mark(25)
        self.assertIn("Труба заговорила первой", пометка)
        self.assertNotIn("Заговори с ним", пометка)


class ЖиваяСистемаTests(unittest.TestCase):
    def test_idle_seconds_returns_a_number(self):
        # Единственный настоящий вызов Windows во всём файле: проверяем,
        # что ctypes-обвязка не падает и вернула число. Сравнивать его с
        # чем-то тут нечего — ввод идёт от живого человека.
        self.assertIsInstance(proactive.idle_seconds(), float)


if __name__ == "__main__":
    unittest.main()
