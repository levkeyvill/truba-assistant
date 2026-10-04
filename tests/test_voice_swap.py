"""Смена голоса в пульте при работающем голосе — на временных настройках."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from core import settings, voice_prep
from core.tts import Reference
from ui.web_runtime import WebRuntime


class VoiceSwapTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(config, k) for k in dir(config) if k.isupper()}
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        (root / "voice").mkdir()
        for name in ("old", "new"):
            (root / "voice" / f"{name}.wav").write_bytes(b"")
        patches = [
            patch.object(settings, "SETTINGS_PATH", root / "settings.json"),
            patch.object(settings, "ENV_PATH", root / ".env"),
            patch.object(voice_prep, "voices_dir", lambda: root / "voice"),
            patch.object(voice_prep, "read_text", lambda name: f"текст {name}"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        settings.save_settings({"tts_engine": "higgs", "voice_name": "old"})
        settings.apply_to_config()

        self.rt = object.__new__(WebRuntime)
        self.rt.brain = None
        self.rt._audio_stale = False
        self.rt.events = []
        self.rt._remember = lambda kind, payload: self.rt.events.append((kind, payload))
        self.voice = SimpleNamespace(
            _brain=None, _loaded=True, _voice="higgs", _speaker="phone", _listener="mic",
            _ref=Reference(audio=root / "voice" / "old.wav", text="текст old"),
            running=True, in_conversation=False, _speaker_idle=lambda: True,
        )
        self.rt.voice = self.voice

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(config, k, v)
        self._dir.cleanup()

    def test_new_sample_applies_at_once_while_voice_runs(self):
        result = self.rt.save_settings({"voice_name": "new"})

        self.assertTrue(result["ok"])
        self.assertEqual(self.voice._ref.audio.name, "new.wav")
        self.assertEqual(self.voice._ref.text, "текст new")
        # Модели не тронуты: перезагружать нечего.
        self.assertTrue(self.voice._loaded)
        self.assertNotIn("note", result)

    def test_engine_change_while_running_waits_for_restart(self):
        with patch("core.higgs_voice.release"):
            result = self.rt.save_settings({"tts_engine": "silero"})

        self.assertTrue(self.voice._loaded)  # посреди работы не трогаем
        self.assertTrue(self.rt._audio_stale)
        self.assertIn("выключишь и включишь", result["note"])

    def test_restart_after_stale_change_reloads_audio(self):
        self.rt._audio_stale = True
        self.rt._lock = __import__("threading").Lock()
        self.rt._enroll = None
        self.rt._jobs = {}
        self.voice.running = False
        started = []
        self.voice.start = lambda: started.append(True)
        self.rt.voice_state = lambda: {}
        with patch("core.higgs_voice.release"):
            self.rt.brain = object()  # чтобы не собирать настоящий мозг
            self.rt.voice_toggle()

        self.assertEqual(started, [True])
        self.assertFalse(self.voice._loaded)
        self.assertIsNone(self.voice._ref)
        self.assertFalse(self.rt._audio_stale)

    def test_voice_off_drops_audio_as_before(self):
        self.voice.running = False
        self.rt.save_settings({"voice_name": "new"})

        self.assertFalse(self.voice._loaded)
        self.assertIsNone(self.voice._ref)


if __name__ == "__main__":
    unittest.main()
