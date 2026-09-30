"""Питание: все проверки системного слоя идут только с подменой Windows."""

import json
import unittest
from unittest import mock

from core import power
from core import hands


class FakePower:
    def __init__(self):
        self.calls = []
        self.abort_ok = True

    def shutdown(self, seconds):
        self.calls.append(("shutdown", seconds))
        return {"ok": True}

    def restart(self, seconds):
        self.calls.append(("restart", seconds))
        return {"ok": True}

    def sleep(self):
        self.calls.append(("sleep",))
        return {"ok": True}

    def abort(self):
        self.calls.append(("abort",))
        return {"ok": self.abort_ok, "why": "отказ"}


class PowerSafety(unittest.TestCase):
    def setUp(self):
        power.reset()
        self.fake = FakePower()
        patcher = mock.patch.object(power, "backend", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(power.reset)

    def test_request_only_asks_and_direct_execute_does_nothing(self):
        self.assertTrue(power.ask("shutdown", now=0)["ok"])
        self.assertFalse(power.execute("shutdown", now=0)["ok"])
        self.assertEqual(self.fake.calls, [])
        result = json.loads(hands.run_power('{"action":"shutdown"}'))
        self.assertTrue(result["ok"])
        self.assertTrue(result["asked"])

    def test_confirmation_is_one_use_and_needs_exact_user_words(self):
        self.assertTrue(power.ask("shutdown", now=1)["ok"])
        self.assertEqual(power.answer(power.ВОПРОС["shutdown"], now=2)["verdict"], "other")
        self.assertEqual(self.fake.calls, [])
        self.assertIsNone(power.pending(now=2))
        power.ask("shutdown", now=3)
        self.assertEqual(power.answer("подтверждаю выключение", now=4)["verdict"], "confirm")
        self.assertEqual(self.fake.calls, [("shutdown", 60)])
        self.assertEqual(power.answer("подтверждаю выключение", now=5)["verdict"], "none")

    def test_yes_mismatch_timeout_and_direct_execution_are_rejected(self):
        for phrase in ("да", "точно выключить компьютер", "подтверждаю сон"):
            power.ask("shutdown", now=1)
            self.assertEqual(power.answer(phrase, now=2)["verdict"], "other")
        power.ask("shutdown", now=1)
        self.assertEqual(power.answer("подтверждаю выключение", now=32)["verdict"], "none")
        self.assertFalse(power.execute("shutdown", now=33)["ok"])
        self.assertEqual(self.fake.calls, [])

    def test_abort_failure_keeps_retry_available(self):
        power.ask("shutdown", now=1)
        power.answer("подтверждаю выключение", now=2)
        self.fake.abort_ok = False
        self.assertFalse(power.abort(now=3)["ok"])
        self.assertEqual(power.counting(now=3), "shutdown")
        self.fake.abort_ok = True
        self.assertTrue(power.abort(now=4)["ok"])
        self.assertIsNone(power.counting(now=4))


class WindowsGuard(unittest.TestCase):
    def tearDown(self):
        power.reset()

    def test_non_pult_process_never_calls_windows(self):
        power.reset()
        with mock.patch.object(power.subprocess, "run") as run:
            self.assertFalse(power.Windows().shutdown()["ok"])
            self.assertFalse(power.Windows().restart()["ok"])
            self.assertFalse(power.Windows().abort()["ok"])
            run.assert_not_called()
        self.assertFalse(power.Windows().sleep()["ok"])

    def test_armed_system_layer_uses_minute_and_is_mocked(self):
        with (mock.patch.object(power.subprocess, "run") as run,
              mock.patch.object(power, "live_runtime", return_value=True)):
            run.return_value.returncode = 0
            self.assertTrue(power.Windows().shutdown()["ok"])
            self.assertEqual(run.call_args.args[0], ["shutdown", "/s", "/t", "60"])
            self.assertTrue(power.Windows().restart()["ok"])
            self.assertEqual(run.call_args.args[0], ["shutdown", "/r", "/t", "60"])

    def test_sleep_does_not_force_applications_closed(self):
        with (mock.patch.object(power.ctypes, "windll", create=True) as windll,
              mock.patch.object(power, "live_runtime", return_value=True)):
            windll.powrprof.SetSuspendState.return_value = 1
            self.assertTrue(power.Windows().sleep()["ok"])
            windll.powrprof.SetSuspendState.assert_called_once_with(False, False, False)
            self.assertEqual(windll.powrprof.SetSuspendState.argtypes,
                             [power.ctypes.c_ubyte] * 3)
            self.assertIs(windll.powrprof.SetSuspendState.restype, power.ctypes.c_ubyte)

    def test_unmocked_confirmation_in_tests_cannot_reach_shutdown(self):
        power.reset()
        with mock.patch.object(power.subprocess, "run") as run:
            power.ask("shutdown", now=0)
            result = power.answer("подтверждаю выключение", now=1)
            self.assertEqual(result["verdict"], "confirm")
            self.assertFalse(result["ok"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
