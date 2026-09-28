"""Сторож мгновенного повтора NVIDIA — на выдуманных журналах, без клавиш."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core import hotkeys, replay

ON = b"[25:11:17:37:609][R][CNTNR:1: 2] DVR Active = true\n"
AUTO_OFF = (
    b"[25:02:23:42:480][R][CNTNR:1: 2] CCaptureSession::LogProtectedContentAppDetails: "
    b"Protected Content is running by PID: 1560 App:\\Device\\HarddiskVolume3\\Users\\u\\AppData"
    b"\\Local\\Programs\\Codex\\resources\\cua_node\\bin\\x\n"
    b"[25:02:23:42:480][R][CNTNR:1: 2] CCaptureSession::IsDVRDisableRequired YES : err(121:0)\n"
    b"[25:02:23:52:554][R][CNTNR:1: 2] CCaptureControl::DisableIR: Auto-Disable IR Session\n"
    b"[25:02:23:52:555][R][CNTNR:1: 2] DVR Active = false\n"
)
MANUAL_OFF = b"[25:12:00:00:000][R][CNTNR:1: 2] DVR Active = false\n"
NOISE = b"[25:12:00:01:000][R][CNTNR:1: 2] CircularSampleBuffer::LogState: ...\n"


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log, self.old, self.memo = self.dir / "a.log", self.dir / "a.old", self.dir / "m.json"

    def status(self, log=b"", old=b""):
        self.log.write_bytes(log)
        self.old.write_bytes(old)
        return replay.status(self.log, self.old, self.memo)

    def test_on(self):
        self.assertTrue(self.status(AUTO_OFF + ON + NOISE).on)

    def test_auto_off_by_codex(self):
        s = self.status(ON + AUTO_OFF + NOISE)
        self.assertIs(s.on, False)
        self.assertTrue(s.auto_off)
        self.assertEqual(s.blocker_pid, 1560)
        self.assertIn("Codex", s.blocker_app)

    def test_manual_off_is_not_auto(self):
        s = self.status(ON + MANUAL_OFF)
        self.assertIs(s.on, False)
        self.assertFalse(s.auto_off)
        self.assertEqual(s.text, "выключен вручную")

    def test_rotated_log_falls_back_to_old_then_memo(self):
        s = self.status(NOISE, ON + AUTO_OFF)
        self.assertTrue(s.auto_off)
        # Журнал сменился дважды — строк о повторе нигде нет, помним сами.
        s2 = self.status(NOISE, NOISE)
        self.assertIs(s2.on, False)
        self.assertTrue(s2.auto_off)

    def test_unknown_without_log_and_memo(self):
        self.assertIsNone(self.status(NOISE, NOISE).on)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.guard = replay.Guard(lambda kind, payload: self.events.append(kind))
        self._saved = getattr(config, "REPLAY_GUARD", True)
        config.REPLAY_GUARD = True

    def tearDown(self):
        config.REPLAY_GUARD = self._saved

    def run_check(self, status, blocker=False, turned_on=True):
        with mock.patch.object(replay, "status", return_value=status), \
                mock.patch.object(replay, "blocker_running", return_value=blocker), \
                mock.patch.object(replay, "turn_on", return_value=turned_on) as turn_on:
            self.guard.check()
        return turn_on

    def test_waits_while_codex_holds_screen(self):
        off = replay.Status(on=False, auto_off=True, blocker_app="Codex")
        turn_on = self.run_check(off, blocker=True)
        turn_on.assert_not_called()
        self.run_check(off, blocker=True)
        self.assertEqual(self.events, ["replay_waiting"])  # сообщает один раз

    def test_turns_back_on_when_codex_gone(self):
        turn_on = self.run_check(replay.Status(on=False, auto_off=True, blocker_app="Codex"))
        turn_on.assert_called_once()
        self.assertEqual(self.events, ["replay_restored"])

    def test_leaves_manual_off_alone(self):
        turn_on = self.run_check(replay.Status(on=False, auto_off=False))
        turn_on.assert_not_called()
        self.assertEqual(self.events, [])

    def test_off_switch_and_unknown(self):
        config.REPLAY_GUARD = False
        self.run_check(replay.Status(on=False, auto_off=True)).assert_not_called()
        config.REPLAY_GUARD = True
        self.run_check(replay.Status(on=None)).assert_not_called()

    def test_backoff_after_failure(self):
        off = replay.Status(on=False, auto_off=True)
        self.run_check(off, turned_on=False).assert_called_once()
        self.assertEqual(self.events, ["replay_failed"])
        # Сразу снова не жмём — ждём паузу.
        self.run_check(off, turned_on=False).assert_not_called()


class ToggleSafetyTests(unittest.TestCase):
    def test_never_presses_blind(self):
        for state in (replay.Status(on=True), replay.Status(on=None)):
            with mock.patch.object(replay, "status", return_value=state), \
                    mock.patch.object(hotkeys, "press") as press:
                replay.turn_on(wait=0)
            press.assert_not_called()

    def test_presses_toggle_when_off(self):
        states = iter([replay.Status(on=False, auto_off=True), replay.Status(on=True)])
        with mock.patch.object(replay, "status", side_effect=lambda *a, **k: next(states)), \
                mock.patch.object(hotkeys, "press") as press, \
                mock.patch.object(hotkeys, "combo", return_value=[18, 16, 121]), \
                mock.patch.object(replay.time, "sleep"):
            self.assertTrue(replay.turn_on(wait=5))
        press.assert_called_once_with([18, 16, 121])

    def test_moment_refuses_when_replay_off(self):
        with mock.patch.object(replay, "status", return_value=replay.Status(on=False)), \
                mock.patch.object(hotkeys, "press") as press:
            with self.assertRaises(replay.ReplayOff):
                hotkeys.save_moment()
        press.assert_not_called()
        with mock.patch.object(replay, "status", return_value=replay.Status(on=None)), \
                mock.patch.object(hotkeys, "press") as press:
            hotkeys.save_moment()
        press.assert_called_once()


if __name__ == "__main__":
    unittest.main()
