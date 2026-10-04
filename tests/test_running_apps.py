"""Что запущено на компьютере: `core/running_apps.list_running`.

Модуль перечисляет видимые окна и отдаёт по строке на exe.

Настоящих окон в тесте нет и быть не должно: перечисление и `psutil.Process`
подменены. Проверяем то, что чаще всего
ломается: свои и системные окна в список не попадают, на exe одна строка,
у лаунчера с обновлением путь через `Update.exe`, а одно упавшее окно не роняет
весь список.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import launcher, running_apps


class _Процесс:
    """Подделка `psutil.Process`: имя и путь — методы, а не поля.

    Именно так у настоящего psutil: без вызова вместо строки получается метод.
    """

    def __init__(self, pid, exe, дети=()):
        self.pid = pid
        self._exe = exe
        self._дети = list(дети)

    def exe(self):
        if isinstance(self._exe, Exception):
            raise self._exe
        return self._exe

    def name(self):
        return Path(self._exe).name if isinstance(self._exe, str) else ""

    def children(self, recursive=False):
        return list(self._дети)


def _процессы(словарь):
    """Подмена `psutil.Process`: по pid отдаём нужный объект."""
    return lambda pid: словарь[pid]


class ОкнаТесты(unittest.TestCase):
    """Список запущенных: кого показываем, кого нет и как названы."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-running-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.список = mock.Mock(return_value=[])
        подмена = mock.patch.object(launcher, "read_list", self.список)
        подмена.start()
        self.addCleanup(подмена.stop)
        # Описание файла читается настоящим win32api — на подставных путях оно
        # падает, и это правильно: название тогда берётся из имени файла.
        self._свои = mock.patch.object(running_apps, "_own_pids", return_value={99})
        self._свои.start()
        self.addCleanup(self._свои.stop)

    def _список(self, окна, процессы):
        """Прогон `list_running` на подставных окнах и процессах."""
        with mock.patch.object(running_apps, "_windows", return_value=окна), \
                mock.patch.object(running_apps, "psutil") as psutil:
            psutil.Process = _процессы(процессы)
            return running_apps.list_running()

    def _окно(self, pid, exe):
        return (pid, "окно", pid)


    def test_свои_окна_в_список_не_попадают(self):
        # Пульт, сервер и окно Трубы хозяин не открывал, чтобы пользоваться
        # программой, — кнопки «Труба» ему не нужна.
        окна = [(99, "Труба", 99), (10, "Firefox", 10)]
        процессы = {99: _Процесс(99, r"C:\Truba\python.exe"),
                    10: _Процесс(10, r"C:\Program Files\Firefox\firefox.exe")}
        строки = self._список(окна, процессы)
        self.assertEqual([с["process"] for с in строки], ["firefox.exe"])

    def test_системные_окна_пропущены(self):
        # Проводник, панель поиска и прочее хозяйское — не программы.
        имена = sorted(running_apps.SKIP_NAMES)
        self.assertIn("explorer.exe", имена)
        self.assertIn("msedgewebview2.exe", имена)
        self.assertIn("lockapp.exe", имена)
        окна = [(10, "Проводник", 10), (11, "Поиск", 11)]
        процессы = {10: _Процесс(10, r"C:\Windows\explorer.exe"),
                    11: _Процесс(11, r"C:\Windows\explorer.exe")}
        self.assertEqual(self._список(окна, процессы), [])

    def test_приложения_магазина_пропущены(self):
        # В `\WindowsApps\` лежат приложения Магазина: запустить их файлом
        # нельзя, значит и предлагать нечего.
        exe = r"C:\Program Files\WindowsApps\WhatsApp_1.0\WhatsApp.exe"
        строки = self._список([(10, "WhatsApp", 10)], {10: _Процесс(10, exe)})
        self.assertEqual(строки, [])

    def test_одна_строка_на_exe_даже_при_двух_окнах(self):
        # У программы бывает несколько окон, а кнопка должна быть одна.
        exe = r"C:\Program Files\Firefox\firefox.exe"
        окна = [(10, "Первое окно — ВК", 10), (11, "Второе окно", 11)]
        процессы = {10: _Процесс(10, exe), 11: _Процесс(11, exe)}
        строки = self._список(окна, процессы)
        self.assertEqual(len(строки), 1)
        self.assertEqual(строки[0]["path"], exe)
        self.assertEqual(строки[0]["process"], "firefox.exe")

    def test_упавшее_окно_не_роняет_список(self):
        # Программа закрылась между перечислением окон и чтением пути — это
        # обычное дело, и из-за одного такого окна список не должен исчезать.
        exe = r"C:\Program Files\Notepad\notepad.exe"
        окна = [(10, "Блокнот", 10), (11, "Умершее", 11)]
        процессы = {10: _Процесс(10, exe),
                    11: _Процесс(11, PermissionError("нет доступа"))}
        строки = self._список(окна, процессы)
        self.assertEqual([с["process"] for с in строки], ["notepad.exe"])

    def test_сортировка_по_названию_и_потолок(self):
        # Порядок по названию — иначе кнопки прыгали бы при каждом обновлении.
        окна = []
        процессы = {}
        for номер in range(running_apps.MAX_ITEMS + 5):
            exe = r"C:\Программы\П%02d\p.exe" % номер
            окна.append((номер + 1, "окно", номер + 1))
            процессы[номер + 1] = _Процесс(номер + 1, exe)
        строки = self._список(окна, процессы)
        self.assertEqual(len(строки), running_apps.MAX_ITEMS)
        названия = [с["title"] for с in строки]
        self.assertEqual(названия, sorted(названия, key=str.lower))


class ОбновлениеТесты(unittest.TestCase):
    """Лаунчер с обновлением: прямой путь сломается при первом же обновлении."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-updater-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        (self.папка / "Discord").mkdir()
        (self.папка / "Discord" / "Update.exe").write_bytes(b"upd")
        (self.папка / "Discord" / "app-1.2.3").mkdir()
        (self.папка / "Discord" / "app-1.2.3" / "Discord.exe").write_bytes(b"app")
        self.список = mock.Mock(return_value=[])
        подмена = mock.patch.object(launcher, "read_list", self.список)
        подмена.start()
        self.addCleanup(подмена.stop)
        свои = mock.patch.object(running_apps, "_own_pids", return_value={99})
        свои.start()
        self.addCleanup(свои.stop)
        значок = mock.patch.object(running_apps, "_значок", return_value="")
        значок.start()
        self.addCleanup(значок.stop)

    def _список(self, exe):
        with mock.patch.object(running_apps, "_windows", return_value=[(10, "окно", 10)]), \
                mock.patch.object(running_apps, "psutil") as psutil:
            psutil.Process = _процессы({10: _Процесс(10, exe)})
            return running_apps.list_running()

    def _discord(self):
        return self._список(str(self.папка / "Discord" / "app-1.2.3" / "Discord.exe"))

    def test_путь_через_обновлятор(self):
        # Так уже записано у Discord в apps.json: `Update.exe --processStart`.
        строка = self._discord()[0]
        self.assertEqual(строка["path"], str(self.папка / "Discord" / "Update.exe"))
        # Имя, а не путь: папка `app-1.2.3` исчезнет с первым обновлением.
        self.assertEqual(строка["args"], ["--processStart", "Discord.exe"])
        self.assertEqual(строка["how"], "shell")
        self.assertEqual(строка["process"], "Discord.exe")

    def test_уже_есть_узнаётся_по_процессу_из_аргумента(self):
        # В `path` лежит Update.exe, а работает Discord.exe: без разбора
        # `--processStart` мы решили бы, что программа ещё не добавлена.
        self.список.return_value = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"%LOCALAPPDATA%\Discord\Update.exe",
            "args": ["--processStart", "Discord.exe"]}]
        self.assertTrue(self._discord()[0]["already"])

    def test_уже_есть_узнаётся_по_полю_process(self):
        self.список.return_value = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"C:\Users\user\LaunchDiscord.exe",
            "process": "Discord.exe"}]
        self.assertTrue(self._discord()[0]["already"])

    def test_чужое_имя_не_считается_своим(self):
        # Регистр не важен (Windows им не различает), а вот разные exe — да.
        self.список.return_value = [{
            "id": "discord", "title": "Discord", "kind": "app",
            "path": r"C:\Program Files\DiscordCanary\Canary.exe"}]
        self.assertFalse(self._discord()[0]["already"])

    def test_без_обновлятора_путь_прямой(self):
        # Обычная программа запускается файлом, лаунчер ей не нужен.
        обычный = self.папка / "Блокнот.exe"
        обычный.write_bytes(b"exe")
        строка = self._список(str(обычный))[0]
        self.assertEqual(строка["path"], str(обычный))
        self.assertEqual(строка["args"], [])
        self.assertEqual(строка["how"], "shell")


class ОформлениеТесты(unittest.TestCase):
    """Значок и ограничения — как их ждёт пульт."""

    def test_значок_берётся_у_exe_и_при_неудаче_пустой(self):
        значок = mock.Mock(return_value=None)
        with mock.patch.object(running_apps.app_icons, "picture_for", значок):
            self.assertEqual(running_apps._значок(r"C:\П\Discord.exe", 0), "")
        значок.assert_called_once()
        запись = значок.call_args[0][0]
        self.assertEqual(запись["icon_source"], "exe")
        self.assertEqual(запись["path"], r"C:\П\Discord.exe")

    def test_свои_процессы_включают_детей(self):
        # Окно Трубы открывает и дочерний процесс (браузер пульта) — и он
        # тоже не должен попасть в список.
        свой = mock.Mock()
        свой.pid = 1234
        ребёнок = mock.Mock()
        ребёнок.pid = 1235
        свой.children.return_value = [ребёнок]
        with mock.patch.object(running_apps.psutil, "Process", return_value=свой), \
                mock.patch.object(running_apps.os, "getpid", return_value=1234):
            свои = running_apps._own_pids()
        self.assertEqual(свои, {1234, 1235})


if __name__ == "__main__":
    unittest.main()
