"""Спокойный свет проверяется синтетикой, без микрофона и динамика."""
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
import test_data_guard
from ui.web_runtime import WebRuntime


class CalmMotion(unittest.TestCase):
    def test_javascript_motion_contract(self):
        root = Path(__file__).resolve().parent.parent
        node = shutil.which('node')
        if not node:
            bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            node = str(bundled) if bundled.exists() else None
        if not node:
            self.skipTest('Node не установлен')
        result = subprocess.run([node, str(root / 'tests/home_wave_checks.cjs')],
                                capture_output=True, text=True, timeout=10, cwd=root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"passed":14', result.stdout)

    def test_unaddressed_voice_does_not_activate_visual_hearing(self):
        rt = object.__new__(WebRuntime)
        rt.voice = NS(running=True, ready=True, in_conversation=False,
                      _speaker_idle=lambda: True, _speech_from=1,
                      _listener=NS(last_level=1))
        state = rt.voice_state()
        self.assertEqual(state['activity'], 'listening')
        self.assertEqual(state['input_level'], 0)
        rt.voice.in_conversation = True
        state = rt.voice_state()
        self.assertEqual(state['activity'], 'hearing')
        self.assertEqual(state['input_level'], 1)

    def test_reminder_playback_still_activates_visual_speech(self):
        rt = object.__new__(WebRuntime)
        rt.voice = NS(running=True, ready=True, in_conversation=False,
                      _speaker_idle=lambda: False, _speech_from=1,
                      _speaker=NS(meter=NS(level=.25)), _listener=NS(last_level=1))
        state = rt.voice_state()
        self.assertEqual(state['activity'], 'speaking')
        self.assertEqual(state['input_level'], 0)
        self.assertEqual(state['output_level'], .25)


if __name__ == '__main__':
    unittest.main()
