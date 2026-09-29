"""Качественные голоса кнопкой из пульта: отказы и собранные команды.

Ничего не ставится и не качается: `hardware.detect` и запуск команд
подменены. Проверяем ровно то, что должно упасть до сети — рабочая папка,
отсутствие видеокарты, старый драйвер, мало места — и что при нормальном
железе собираются те же команды, что у `install.ps1 -Voices`.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import hardware, voices_install


def _железо(карты, место=300.0):
    """Готовый результат `detect` — без похода в nvidia-smi."""
    return {"gpus": list(карты), "disk_free_gb": место}


def _с_железом(карты, место=300.0):
    return mock.patch.object(hardware, "detect",
                             return_value=_железо(карты, место))


КАРТА = {"name": "NVIDIA GeForce RTX 5070 Ti", "vram_gb": 15.9,
         "driver": "616.56", "compute_cap": 12.0}
СТАРАЯ_КАРТА = {"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0,
                "driver": "531.14", "compute_cap": 8.6}


class RefuseTests(unittest.TestCase):
    """Отказ должен быть словами и до всякой сети."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-voices-"))
        self.addCleanup(shutil.rmtree, self.папка, True)

    def test_working_folder_is_refused(self):
        # В рабочей папке уже стоит torch с CUDA. Переставить его поверх
        # работающей копии — значит сломать разработку.
        (self.папка / "coordination").mkdir()
        with _с_железом([КАРТА]):
            ответ = voices_install.check(self.папка)
        self.assertFalse(ответ["ok"])
        self.assertIn("рабочей папке", ответ["error"])

    def test_no_nvidia_is_refused(self):
        with _с_железом([]):
            ответ = voices_install.check(self.папка)
        self.assertFalse(ответ["ok"])
        self.assertIn("NVIDIA", ответ["error"])

    def test_old_driver_is_refused_and_says_it(self):
        with _с_железом([СТАРАЯ_КАРТА]):
            ответ = voices_install.check(self.папка)
        self.assertFalse(ответ["ok"])
        self.assertIn("драйвер", ответ["error"])
        self.assertIn("531", ответ["error"])

    def test_little_disk_is_refused(self):
        with _с_железом([КАРТА], место=9.0):
            ответ = voices_install.check(self.папка)
        self.assertFalse(ответ["ok"])
        self.assertIn("места", ответ["error"])

    def test_good_hardware_passes_and_names_the_build(self):
        with _с_железом([КАРТА]):
            ответ = voices_install.check(self.папка)
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["cuda_index"], "cu130")

    def test_install_stops_at_the_refusal_and_never_runs_a_command(self):
        запуск = mock.Mock()
        (self.папка / "coordination").mkdir()
        with _с_железом([КАРТА]), \
                mock.patch.object(voices_install.subprocess, "run", запуск):
            итог = voices_install.install(self.папка)
        self.assertFalse(итог["ok"])
        self.assertIn("рабочей папке", итог["error"])
        запуск.assert_not_called()


class CommandTests(unittest.TestCase):
    """Собранные команды — те же, что у установщика."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-voices-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        (self.папка / ".venv" / "Scripts").mkdir(parents=True)
        (self.папка / ".venv" / "Scripts" / "python.exe").write_bytes(b"")

    def test_pip_when_there_is_no_uv(self):
        # Установщик кладёт `uv` в `.tools`; если его нет, работаем через
        # `python -m pip` того же окружения.
        команды = voices_install.команды(self.папка, "cu130")
        self.assertEqual(len(команды), 2)
        торч, библиотеки = команды
        self.assertTrue(торч[0].endswith("python.exe"))
        self.assertIn("-m", торч)
        self.assertIn("pip", торч)
        self.assertIn("install", торч)

    def test_torch_goes_from_the_cuda_index(self):
        торч, _ = voices_install.команды(self.папка, "cu130")
        self.assertIn("torch==2.10.0", торч)
        self.assertIn("torchaudio==2.10.0", торч)
        self.assertIn("--index-url", торч)
        self.assertIn("https://download.pytorch.org/whl/cu130", торч)

    def test_pip_reinstalls_only_torch(self):
        # Переставлять всё заново нельзя: базовая Труба работает на этом же
        # torch. Поэтому у pip — точечная переустановка без зависимостей.
        торч, _ = voices_install.команды(self.папка, "cu130")
        self.assertIn("--force-reinstall", торч)
        self.assertIn("--no-deps", торч)
        # Переставляются ровно torch и torchaudio — версии зафиксированы
        # те же, что у `install.ps1` и `requirements-voices.txt`.
        self.assertIn("torch==2.10.0", торч)
        self.assertIn("torchaudio==2.10.0", торч)

    def test_voices_libraries_use_the_same_index(self):
        _, библиотеки = voices_install.команды(self.папка, "cu130")
        self.assertIn("requirements-voices.txt", библиотеки)
        self.assertIn("--extra-index-url", библиотеки)
        self.assertIn("https://download.pytorch.org/whl/cu130", библиотеки)
        # Без uv — pip, а флага `--index-strategy` у pip нет: упал бы на нём.
        self.assertNotIn("--index-strategy", библиотеки)

    def test_uv_gets_the_index_strategy(self):
        tools = self.папка / ".tools"
        tools.mkdir()
        (tools / "uv.exe").write_bytes(b"")
        _, библиотеки = voices_install.команды(self.папка, "cu130")
        self.assertIn("--index-strategy", библиотеки)


class ОтдельнымОкномTests(unittest.TestCase):
    """Кнопка в пульте открывает установщик окном и закрывает пульт: изнутри
    пульта torch не заменить — Windows держит файлы работающей библиотеки."""

    def setUp(self):
        import shutil
        import tempfile

        self.папка = Path(tempfile.mkdtemp(prefix="truba-voices-win-"))
        self.addCleanup(shutil.rmtree, self.папка, True)

    def _можно(self):
        return mock.patch.object(voices_install, "check",
                                 return_value={"ok": True, "cuda_index": "cu130"})

    def test_the_bat_is_started_in_its_own_window(self):
        (self.папка / voices_install.BAT).write_bytes(b"@echo off\r\n")
        with self._можно(), mock.patch.object(voices_install.subprocess, "Popen") as запуск:
            ответ = voices_install.launch_external(self.папка)
        self.assertTrue(ответ["ok"], ответ)
        команда = запуск.call_args[0][0]
        self.assertEqual(команда[:3], ["cmd.exe", "/c", "start"])
        self.assertTrue(команда[-2].endswith(voices_install.BAT))
        # Пульт уже спросил «Начать?» — установщик второй раз не спрашивает.
        self.assertEqual(команда[-1], "-Yes")

    def test_no_bat_is_said_in_words(self):
        with self._можно(), mock.patch.object(voices_install.subprocess, "Popen") as запуск:
            ответ = voices_install.launch_external(self.папка)
        self.assertFalse(ответ["ok"])
        запуск.assert_not_called()

    def test_a_refused_check_starts_nothing(self):
        (self.папка / voices_install.BAT).write_bytes(b"x")
        with mock.patch.object(voices_install, "check",
                               return_value={"ok": False, "error": "видеокарты NVIDIA нет"}), \
                mock.patch.object(voices_install.subprocess, "Popen") as запуск:
            ответ = voices_install.launch_external(self.папка)
        self.assertFalse(ответ["ok"])
        запуск.assert_not_called()

    def test_uv_is_used_when_it_is_there(self):
        tools = self.папка / ".tools"
        tools.mkdir()
        (tools / "uv.exe").write_bytes(b"")
        торч, библиотеки = voices_install.команды(self.папка, "cu130")
        self.assertTrue(торч[0].endswith("uv.exe"))
        self.assertIn("--python", торч)
        # uv умеет переставить только torch — это и делает установщик.
        self.assertIn("--reinstall-package", торч)
        self.assertIn("torch", торч)
        self.assertNotIn("--no-deps", торч)
        self.assertTrue(библиотеки[0].endswith("uv.exe"))

    def test_older_driver_gets_its_own_index(self):
        торч, _ = voices_install.команды(self.папка, "cu128")
        self.assertIn("https://download.pytorch.org/whl/cu128", торч)


class InstallTests(unittest.TestCase):
    """Сам запуск: команды уходят по очереди, итог и журнал честные."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-voices-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.журнал = self.папка / "voices_install.log"
        self.шаги = []
        подмена = mock.patch.object(voices_install, "LOG_PATH", self.журнал)
        подмена.start()
        self.addCleanup(подмена.stop)

    def _запуски(self, код=0):
        """Подмена запуска: запоминает команды, ничего не выполняет."""
        виден = []

        def запуск(команда, **kwargs):
            виден.append(команда)
            self.assertEqual(kwargs.get("creationflags"),
                             voices_install._окно_нет())
            return mock.Mock(returncode=код)

        return виден, mock.patch.object(voices_install.subprocess, "run", запуск)

    def test_runs_both_commands_and_asks_for_a_restart(self):
        # torch уже загружен в процессе пульта: без перезапуска новая сборка
        # не подхватится, и хозяин решит, что кнопка не сработала.
        виден, запуск = self._запуски()
        with _с_железом([КАРТА]), запуск:
            итог = voices_install.install(self.папка,
                                          on_step=self.шаги.append)
        self.assertTrue(итог["ok"], итог)
        self.assertTrue(итог["restart"])
        self.assertEqual(len(виден), 2)
        self.assertIn("cu130", " ".join(виден[0]))
        self.assertTrue(self.шаги)

    def test_nothing_to_run_means_a_refusal_with_a_reason(self):
        виден, запуск = self._запуски(код=1)
        with _с_железом([КАРТА]), запуск:
            итог = voices_install.install(self.папка)
        self.assertFalse(итог["ok"])
        self.assertIn("не поставились", итог["error"])
        # Первая команда упала — вторую запускать незачем.
        self.assertEqual(len(виден), 1)

    def test_journal_tells_the_whole_story(self):
        _, запуск = self._запуски()
        with _с_железом([КАРТА]), запуск:
            voices_install.install(self.папка)
        журнал = self.журнал.read_text(encoding="utf-8")
        self.assertIn("качественные голоса", журнал)
        self.assertIn("перезапусти пульт", журнал)

    def test_failed_install_is_written_to_the_journal(self):
        _, запуск = self._запуски(код=1)
        with _с_железом([КАРТА]), запуск:
            voices_install.install(self.папка)
        self.assertIn("не поставились", self.журнал.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
