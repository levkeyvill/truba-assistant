"""Выход и рамка окна: подмены не закрывают настоящий пульт."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import test_data_guard
from ui.window import PultBridge, TrayWindowController
from ui import window_chrome


class Closing(unittest.TestCase):
    def setUp(self):
        self.window = mock.MagicMock()
        self.controller = TrayWindowController(self.window)
        self.controller._chrome = mock.Mock()

    def test_no_keeps_window_and_voice_alive_without_hiding(self):
        with mock.patch.object(window_chrome, 'confirm_close', return_value=False):
            self.assertIs(self.controller.closing(), False)
        self.window.hide.assert_not_called()
        self.window.destroy.assert_not_called()
        self.assertFalse(self.controller._quitting)

    def test_yes_allows_normal_shutdown(self):
        with mock.patch.object(window_chrome, 'confirm_close', return_value=True):
            self.assertIsNone(self.controller.closing())
        self.assertTrue(self.controller._quitting)
        self.window.hide.assert_not_called()

    def test_programmatic_restart_does_not_ask_again(self):
        self.controller.quit()
        with mock.patch.object(window_chrome, 'confirm_close') as ask:
            self.assertIsNone(self.controller.closing())
        ask.assert_not_called()
        self.window.destroy.assert_called_once()

    def test_cancel_on_dialog_failure(self):
        with mock.patch.object(window_chrome, 'confirm_close', side_effect=OSError('вымышленная ошибка')), \
             mock.patch('logging.Logger.exception'):
            self.assertIs(self.controller.closing(), False)
        self.window.destroy.assert_not_called()

    def test_confirmation_is_bound_even_without_tray(self):
        closing = self.window.events.closing
        with mock.patch('ui.tray.Tray') as tray:
            tray.return_value.available = False
            self.assertFalse(self.controller.bind())
        closing.__iadd__.assert_called_once_with(self.controller.closing)

    def test_minimize_hides_in_tray_and_does_not_destroy(self):
        self.controller._tray = mock.Mock()
        self.controller.minimized()
        self.window.hide.assert_called_once()
        self.window.destroy.assert_not_called()
        self.controller._chrome.set_quiet.assert_called_once_with(False)

    def test_minimize_without_tray_keeps_window_reachable(self):
        self.controller.minimized()
        self.window.hide.assert_not_called()

    def test_show_restores_native_frame_first(self):
        calls = mock.Mock()
        calls.attach_mock(self.controller._chrome, 'chrome')
        calls.attach_mock(self.window, 'window')
        self.controller.show()
        self.assertEqual(calls.mock_calls[:3], [mock.call.chrome.set_quiet(False),
                                               mock.call.window.show(), mock.call.window.restore()])


class FakeFrame:
    def __init__(self, style=None):
        self.value = window_chrome.FRAME_BITS | 0x01000000 | 0x00080000 if style is None else style
        self.applied = []
        self.hovered = False

    def style(self, handle):
        return self.value

    def apply(self, handle, value):
        self.value = value
        self.applied.append((handle, value))

    def pointer_inside(self, handle):
        return self.hovered


class Frame(unittest.TestCase):
    def setUp(self):
        self.native = NS(Enabled=True, WindowState='Maximized', InvokeRequired=False,
                         Handle=NS(ToInt64=lambda: 0x1ABCDEF01))
        self.window = NS(native=self.native)
        self.backend = FakeFrame()
        self.chrome = window_chrome.WindowChrome(self.window, self.backend)

    def test_quiet_removes_only_caption_and_resize_frame(self):
        before = self.backend.value
        self.assertTrue(self.chrome.set_quiet(True)['ok'])
        self.assertEqual(self.backend.value, before & ~window_chrome.FRAME_BITS)
        self.assertEqual(self.backend.applied[0][0], 0x1ABCDEF01)

    def test_restore_preserves_changes_to_maximized_or_minimized_state(self):
        self.chrome.set_quiet(True)
        self.backend.value |= 0x20000000
        self.chrome.set_quiet(False)
        self.assertEqual(self.backend.value & window_chrome.FRAME_BITS, window_chrome.FRAME_BITS)
        self.assertTrue(self.backend.value & 0x20000000)

    def test_repeated_requests_do_not_repaint_native_frame(self):
        for _ in range(100):
            self.chrome.set_quiet(True)
        for _ in range(100):
            self.chrome.set_quiet(False)
        self.assertEqual(len(self.backend.applied), 2)

    def test_windowed_hover_and_idle_never_change_frame(self):
        self.native.WindowState = 'Normal'
        before = self.backend.value
        for _ in range(100):
            self.chrome.set_quiet(True)
            self.chrome.set_quiet(False)
        self.assertEqual(self.backend.value, before)
        self.assertEqual(self.backend.applied, [])

    def test_restore_to_windowed_recovers_frame_even_if_home_stays_quiet(self):
        before = self.backend.value
        self.chrome.set_quiet(True)
        self.native.WindowState = 'Normal'
        self.chrome.set_quiet(True)
        self.assertEqual(self.backend.value, before)
        self.assertEqual(len(self.backend.applied), 2)

    def test_minimized_window_recovers_frame_and_does_not_hide_it_again(self):
        before = self.backend.value
        self.chrome.set_quiet(True)
        self.native.WindowState = 'Minimized'
        self.chrome.set_quiet(True)
        self.chrome.set_quiet(True)
        self.assertEqual(self.backend.value, before)
        self.assertEqual(len(self.backend.applied), 2)

    def test_pointer_anywhere_in_maximized_window_defers_hiding(self):
        self.backend.hovered = True
        for _ in range(100):
            self.assertTrue(self.chrome.set_quiet(True)['deferred'])
            self.chrome.set_quiet(False)
        self.assertEqual(self.backend.applied, [])

    def test_fullscreen_without_caption_is_left_borderless(self):
        self.backend.value = 0x01000000
        self.chrome.set_quiet(True)
        self.chrome.set_quiet(False)
        self.assertEqual(self.backend.applied, [])

    def test_frame_under_cursor_is_deferred_to_keep_close_button_clickable(self):
        self.backend.hovered = True
        self.assertTrue(self.chrome.set_quiet(True)['deferred'])
        self.assertEqual(self.backend.applied, [])
        self.backend.hovered = False
        self.chrome.set_quiet(True)
        self.assertEqual(len(self.backend.applied), 1)

    def test_modal_dialog_defers_frame_changes(self):
        self.native.Enabled = False
        self.assertTrue(self.chrome.set_quiet(True)['deferred'])
        self.assertEqual(self.backend.applied, [])

    def test_frame_is_restored_before_fullscreen_toggle(self):
        self.window.toggle_fullscreen = mock.Mock()
        bridge = PultBridge(None)
        bridge._window, bridge._chrome = self.window, self.chrome
        before = self.backend.value
        self.chrome.set_quiet(True)
        self.assertTrue(bridge.toggle_fullscreen()['ok'])
        self.assertEqual(self.backend.value, before)
        self.window.toggle_fullscreen.assert_called_once()

    def test_restore_failure_does_not_enter_fullscreen_with_wrong_frame(self):
        self.window.toggle_fullscreen = mock.Mock()
        bridge = PultBridge(None)
        bridge._window, bridge._chrome = self.window, self.chrome
        self.chrome.set_quiet(True)
        with mock.patch.object(self.backend, 'apply', side_effect=OSError('вымышленная ошибка')), \
             mock.patch.object(window_chrome.log, 'exception'):
            self.assertFalse(bridge.toggle_fullscreen()['ok'])
        self.window.toggle_fullscreen.assert_not_called()

    def test_bridge_rejects_unexpected_types(self):
        bridge = PultBridge(None)
        bridge._chrome = mock.Mock()
        for value in ['false', 0, None, {}]:
            self.assertFalse(bridge.set_home_quiet(value)['ok'])
        bridge._chrome.set_quiet.assert_not_called()

    def test_window_not_ready_returns_an_error(self):
        self.window.native = None
        self.assertFalse(self.chrome.set_quiet(True)['ok'])

    def test_dialog_has_own_owner_yes_no_and_default_no(self):
        api = mock.Mock()
        api.MessageBoxW.return_value = 6
        with mock.patch.object(window_chrome.sys, 'platform', 'win32'), \
             mock.patch.object(window_chrome.ctypes, 'WinDLL', return_value=api, create=True):
            self.assertTrue(window_chrome.confirm_close(self.window))
            api.MessageBoxW.return_value = 7
            self.assertFalse(window_chrome.confirm_close(self.window))
        args = api.MessageBoxW.call_args.args
        self.assertEqual(args[0], 0x1ABCDEF01)
        self.assertEqual(args[3] & 0xF, 4)
        self.assertEqual(args[3] & 0x300, 0x100)

    def test_javascript_keeps_native_frame_in_sync_with_home(self):
        root = Path(__file__).resolve().parent.parent
        node = shutil.which('node')
        if not node:
            bundled = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            node = str(bundled) if bundled.exists() else None
        if not node:
            self.skipTest('Node не установлен')
        result = subprocess.run([node, str(root/'tests/home_window_checks.cjs')],
                                cwd=root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('"passed":12', result.stdout)


if __name__ == '__main__':
    unittest.main()
