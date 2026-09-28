"""Уборка: журнал не растёт бесконечно.

Всё на временных папках: настоящий session.log тестами не трогается.
Копии рабочей папки — `tests/test_backup_tool.py`.
"""

import tempfile
import threading
import unittest
from pathlib import Path

from ui.web_runtime import WebRuntime


class LogRotateTests(unittest.TestCase):
    """Журнал больше LOG_LIMIT_MB уезжает в session.1.log при старте пульта."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.data = Path(self._dir.name)
        self.runtime = object.__new__(WebRuntime)
        self.runtime._lock = threading.Lock()
        self.runtime._log_path = self.data / "session.log"

    def tearDown(self):
        self._dir.cleanup()

    def test_big_log_is_renamed_and_new_one_starts_empty(self):
        self.runtime._log_path.write_text("старое\n" * 400_000, encoding="utf-8")
        self.assertGreater(self.runtime._log_path.stat().st_size, 5 * 1024 * 1024)
        (self.data / "session.1.log").write_text("ещё более старый журнал\n", encoding="utf-8")

        self.runtime._rotate_log()

        previous = self.data / "session.1.log"
        self.assertTrue(previous.is_file())
        self.assertIn("старое", previous.read_text(encoding="utf-8")[:200])
        self.assertNotIn("ещё более старый", previous.read_text(encoding="utf-8")[:200])
        self.assertTrue(self.runtime._log_path.is_file())
        self.assertEqual(self.runtime._log_path.stat().st_size, 0)
        # Больше двух файлов журнала не осталось.
        self.assertEqual(sorted(p.name for p in self.data.iterdir()),
                         ["session.1.log", "session.log"])
        # Вкладка «Логи» читает уже новый файл.
        self.assertEqual(self.runtime.logs(), [])

    def test_small_log_is_left_alone(self):
        self.runtime._log_path.write_text("скромный журнал\n" * 100, encoding="utf-8")
        before = self.runtime._log_path.read_text(encoding="utf-8")

        self.runtime._rotate_log()

        self.assertEqual(self.runtime._log_path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.data / "session.1.log").exists())

    def test_missing_log_is_not_created_by_rotation(self):
        self.runtime._rotate_log()
        self.assertFalse(self.runtime._log_path.exists())

    def test_log_message_after_rotation_lands_in_new_file(self):
        self.runtime._log_path.write_text("x" * (6 * 1024 * 1024), encoding="utf-8")

        self.runtime._rotate_log()
        self.runtime.log_message("веб-пульт запущен")

        lines = self.runtime.logs_from(0)["lines"]
        self.assertEqual(lines[-1].split("] ")[-1], "веб-пульт запущен")
        self.assertEqual(self.runtime.logs()[-1].split("] ")[-1], "веб-пульт запущен")


if __name__ == "__main__":
    unittest.main()

