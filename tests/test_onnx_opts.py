"""Сессии onnxruntime без кручения потоков (28.09: четыре ядра впустую)."""

import unittest
from pathlib import Path

import config
from core import onnx_opts

ROOT = Path(config.ROOT)


class ТихиеСессииTests(unittest.TestCase):
    def test_кручение_выключено(self):
        options = onnx_opts.quiet()
        self.assertEqual(options.get_session_config_entry("session.intra_op.allow_spinning"), "0")
        self.assertEqual(options.get_session_config_entry("session.inter_op.allow_spinning"), "0")

    def test_детектору_один_поток(self):
        options = onnx_opts.quiet(threads=1)
        self.assertEqual(options.intra_op_num_threads, 1)
        self.assertEqual(options.inter_op_num_threads, 1)

    def test_все_три_модели_грузятся_тихо(self):
        # Детектор речи зовут каждые 32 мс — с настройками по умолчанию его
        # потоки не засыпали вовсе. Распознавание и отпечаток — туда же.
        for файл, строка in (
            ("core/audio_in.py", "sess_options=onnx_opts.quiet(threads=1)"),
            ("core/voice_loop.py", "sess_options=onnx_opts.quiet()"),
            ("core/speaker.py", "Manager(sess_options=onnx_opts.quiet())"),
        ):
            self.assertIn(строка, (ROOT / файл).read_text(encoding="utf-8"), файл)


if __name__ == "__main__":
    unittest.main()
