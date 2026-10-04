"""Заряд телефона: разбор сообщения, журнал и подпись карточки «Телефон».

Страница шлёт заряд раз в минуту и при каждом изменении, поэтому главное
здесь — что лишнего в журнал и ленту не попадает, а тревожный переход
попадает. Ни сети, ни телефона тут нет: `WebRuntime` собирается вручную,
как в `test_app_menu`, журнал заменён списком.
"""

import threading
import unittest
from types import SimpleNamespace as NS

from core import state
from ui.web_runtime import LOW_BATTERY, WebRuntime


def _runtime(журнал=None):
    """Настоящий метод `_battery`, но без остального пульта."""
    среда = object.__new__(WebRuntime)
    среда._lock = threading.Lock()
    среда._ids = iter(range(1, 100))
    среда._battery_level = None
    среда._battery_charging = False
    среда._battery_low = False
    среда._battery_at = 0.0
    среда.строки = журнал if журнал is not None else []
    среда.log_message = lambda text: среда.строки.append(text)
    # Ленты у такой среды нет: заряд и не должен в неё попадать.
    среда._remember = lambda kind, payload: None
    return среда


class BatteryParseTests(unittest.TestCase):
    def setUp(self):
        self.среда = _runtime()

    def test_level_is_remembered_with_charging(self):
        self.среда.handle_event("battery", {"level": 82, "charging": True})
        self.assertEqual(self.среда._battery_level, 82)
        self.assertTrue(self.среда._battery_charging)

    def test_fractional_level_is_rounded(self):
        self.среда.handle_event("battery", {"level": 81.6, "charging": False})
        self.assertEqual(self.среда._battery_level, 82)
        self.assertFalse(self.среда._battery_charging)

    def test_time_of_the_report_is_kept(self):
        self.assertEqual(self.среда._battery_at, 0.0)
        self.среда.handle_event("battery", {"level": 50, "charging": False})
        self.assertGreater(self.среда._battery_at, 0)

    def test_nothing_is_written_for_an_ordinary_change(self):
        for level in (90, 60, 31, 20):
            self.среда.handle_event("battery", {"level": level, "charging": False})
        self.assertEqual(self.среда.строки, [])

    def test_dropping_below_the_fifth_part_is_written_once(self):
        self.среда.handle_event("battery", {"level": 19, "charging": False})
        self.среда.handle_event("battery", {"level": 18, "charging": False})
        self.среда.handle_event("battery", {"level": 12, "charging": False})
        self.assertEqual(len(self.среда.строки), 1)
        self.assertIn("19%", self.среда.строки[0])
        self.assertIn("зарядить", self.среда.строки[0])

    def test_low_charging_phone_is_not_alarming(self):
        # На зарядке «пора зарядить» не говорим: заряжается же.
        self.среда.handle_event("battery", {"level": 5, "charging": True})
        self.assertEqual(self.среда.строки, [])
        self.assertFalse(self.среда._battery_low)

    def test_returning_above_the_threshold_arms_the_next_alarm(self):
        self.среда.handle_event("battery", {"level": 19, "charging": False})
        self.среда.handle_event("battery", {"level": 40, "charging": False})
        self.среда.handle_event("battery", {"level": 18, "charging": False})
        self.assertEqual(len(self.среда.строки), 2)

    def test_battery_does_not_reach_the_event_feed(self):
        # Телефон шлёт это раз в минуту: в ленте «Голос» такая стока
        # вытесняла бы живые события.
        вызвано = []
        self.среда._remember = lambda kind, payload: вызвано.append(kind)
        for level in (50, 40, 30):
            self.среда.handle_event("battery", {"level": level, "charging": False})
        self.assertEqual(вызвано, [])

    def test_exactly_the_threshold_is_not_alarming(self):
        self.среда.handle_event("battery", {"level": LOW_BATTERY, "charging": False})
        self.assertEqual(self.среда.строки, [])

    def test_broken_payload_is_ignored(self):
        for payload in (None, {}, {"level": None}, {"level": "много"},
                        {"level": -5}, {"level": 140}, {"level": True}):
            self.среда.handle_event("battery", payload)
        self.assertIsNone(self.среда._battery_level)
        self.assertEqual(self.среда.строки, [])

    def test_broken_payload_keeps_the_previous_value(self):
        self.среда.handle_event("battery", {"level": 55, "charging": False})
        self.среда.handle_event("battery", {"level": "ерунда"})
        self.assertEqual(self.среда._battery_level, 55)


class BatteryNoteTests(unittest.TestCase):
    def test_note_names_the_level(self):
        среда = _runtime()
        среда.handle_event("battery", {"level": 18, "charging": False})
        self.assertEqual(среда.battery_note(), "18%")

    def test_charging_note_carries_the_bolt(self):
        среда = _runtime()
        среда.handle_event("battery", {"level": 82, "charging": True})
        self.assertEqual(среда.battery_note(), "82% ⚡")

    def test_note_is_empty_until_the_phone_reports(self):
        self.assertEqual(_runtime().battery_note(), "")


class PhoneCardTests(unittest.TestCase):
    """Подзаголовок карточки «Телефон» в панели пульта."""

    def setUp(self):
        self.среда = _runtime()
        self.сервер = NS(url="http://192.168.1.5:8764", connected=True,
                         audio_ready=True)
        self.сервер.runtime = self.среда

    def test_card_shows_level_and_bolt(self):
        self.среда.handle_event("battery", {"level": 82, "charging": True})
        карточка = state._phone(self.сервер)
        self.assertIn("звук в", карточка["внизу"])
        self.assertIn("82% ⚡", карточка["внизу"])
        self.assertTrue(карточка["хорошо"])

    def test_card_shows_low_level_without_the_bolt(self):
        self.среда.handle_event("battery", {"level": 18, "charging": False})
        внизу = state._phone(self.сервер)["внизу"]
        self.assertIn("18%", внизу)
        self.assertNotIn("⚡", внизу)

    def test_card_survives_a_phone_that_never_reported(self):
        # Обычный браузер без защищённого контекста: заряда нет вовсе.
        self.assertIn(state._phone(self.сервер)["внизу"], ("звук в телефон", "звук в колонки"))

    def test_offline_phone_shows_no_stale_level(self):
        self.среда.handle_event("battery", {"level": 15, "charging": False})
        self.сервер.connected = False
        карточка = state._phone(self.сервер)
        self.assertEqual(карточка["внизу"], "не на связи")
        self.assertFalse(карточка["хорошо"])

    def test_connected_phone_without_sound_asks_for_a_touch(self):
        # Связь поднимается сразу, а звук — только после касания. Хозяин у
        # компа должен видеть, что пора коснуться, иначе он подумает, что
        # его не слышат, и начнёт проверять колонки.
        self.сервер.audio_ready = False
        карточка = state._phone(self.сервер)
        self.assertIn("звук выключен, коснись телефона", карточка["внизу"])
        self.assertTrue(карточка["хорошо"])

    def test_sound_on_the_phone_returns_the_card_to_where_sound_goes(self):
        self.сервер.audio_ready = False
        self.среда.handle_event("battery", {"level": 82, "charging": False})
        self.сервер.audio_ready = True
        self.assertIn("звук в", state._phone(self.сервер)["внизу"])
        self.assertIn("82%", state._phone(self.сервер)["внизу"])

    def test_server_without_runtime_does_not_break_the_card(self):
        сервер = NS(url="http://192.168.1.5:8764", connected=True,
                    audio_ready=True)
        self.assertIn(state._phone(сервер)["внизу"], ("звук в телефон", "звук в колонки"))

    def test_broken_runtime_leaves_the_card_alive(self):
        # Панель зовёт сводку раз в пару секунд: исключение здесь уронило
        # бы весь блок, а не одну подпись.
        class Сломанный:
            def battery_note(self):
                raise RuntimeError("бум")

        self.сервер.runtime = Сломанный()
        self.assertIn(state._phone(self.сервер)["внизу"], ("звук в телефон", "звук в колонки"))

    def test_no_server_still_says_where_sound_goes(self):
        import config

        куда = "звук в телефон" if config.OUTPUT == "phone" else "звук в колонки"
        self.assertEqual(state._phone(None)["внизу"], куда)


if __name__ == "__main__":
    unittest.main()
