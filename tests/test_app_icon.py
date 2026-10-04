"""Значок окна пульта: рисуется один раз, чужую картинку не затирает."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import app_icon


class AppIconTest(unittest.TestCase):
    def test_draws_ico_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(app_icon.config, "DATA_DIR", Path(tmp)):
                path = app_icon.ensure()
                self.assertIsNotNone(path)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes()[:4], b"\x00\x00\x01\x00")

    def test_keeps_own_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / app_icon.ICO_NAME
            own.write_bytes(b"own")
            with mock.patch.object(app_icon.config, "DATA_DIR", Path(tmp)):
                self.assertEqual(app_icon.ensure(), own)
            self.assertEqual(own.read_bytes(), b"own")

    def test_failure_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(app_icon.config, "DATA_DIR", Path(tmp)), \
                    mock.patch.object(app_icon, "draw", side_effect=OSError("нет")):
                self.assertIsNone(app_icon.ensure())


if __name__ == "__main__":
    unittest.main()
