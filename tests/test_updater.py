"""Обновление с GitHub: проверка, белый список, откат, API.

Ни сети, ни микрофона, ни живой папки проекта тут нет: `root` — всегда
временная папка, `httpx` подменён, а `_pip_install` и `_smoke` заменены
заглушками. Живой GitHub тест не дёргает.

Самое важное здесь — что НЕ трогается: `data/`, `settings.json`, `.env`,
`apps.json`, `prompts/`, `models/`, `voice/` лежат в поддельном архиве, и
после установки должны остаться ровно такими, какими были. Второе по важности
— отказ на пути в архиве: `../` и абсолютный путь не должны приводить ни к
чему, даже к откату.
"""

import io
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from collections import deque
from pathlib import Path
from unittest import mock

import config
from core import updater, weather
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

# --- Подделки вместо сети --------------------------------------------------


class _Ответ:
    """Ответ GitHub: код, тело, и — по желанию — исключение вместо json."""

    def __init__(self, status_code=200, тело=None, падает=False):
        self.status_code = status_code
        self._тело = тело
        self._падает = падает
        self.url = "https://api.github.com/repos/x/zipball/v1.0.0"

    def json(self):
        if self._падает:
            raise ValueError("не json")
        return self._тело


class _Клиент:
    """httpx.Client с готовыми ответами — вместо похода в сеть."""

    ответы: list = []
    вызвано: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        _Клиент.вызвано.append(url)
        if not _Клиент.ответы:
            return _Ответ(404)
        return _Клиент.ответы.pop(0)


def _сеть(ответы):
    """Подмена httpx.Client на время теста — для `check`."""
    _Клиент.ответы = list(ответы)
    _Клиент.вызвано = []
    return mock.patch("httpx.Client", _Клиент)


def _качать(архив: bytes):
    """Подмена загрузки архива — для `install`."""
    def скачать(url, куда, timeout=300.0):
        if not updater._адрес_свой(url):
            raise ValueError("непонятный адрес архива — установка отменена")
        Path(куда).write_bytes(архив)
    return mock.patch.object(updater, "_скачать", скачать)


def _zip(файлы: dict, верхняя: str = "levkeyvill-truba-abc123/") -> bytes:
    """Поддельный выпуск: всё лежит в одной верхней папке, как у GitHub."""
    поток = io.BytesIO()
    with zipfile.ZipFile(поток, "w", zipfile.ZIP_DEFLATED) as коробка:
        for имя, текст in файлы.items():
            коробка.writestr(f"{верхняя}{имя}", текст)
    return поток.getvalue()


def _zip_без_проверки(файлы: dict) -> bytes:
    """То же, но без верхней папки — для случаев с плохими именами."""
    поток = io.BytesIO()
    with zipfile.ZipFile(поток, "w", zipfile.ZIP_DEFLATED) as коробка:
        for имя, текст in файлы.items():
            коробка.writestr(имя, текст)
    return поток.getvalue()


def _выпуск(тег="v9.9.9", **поле):
    тело = {
        "tag_name": тег,
        "name": f"Труба {тег}",
        "body": "Что нового",
        "published_at": "2026-09-27T10:00:00Z",
        "zipball_url": "https://api.github.com/repos/levkeyvill/truba/zipball/"
                       + тег,
    }
    тело.update(поле)
    return _Ответ(200, тело)


# --- Номер версии ----------------------------------------------------------


class ParseVersionTests(unittest.TestCase):
    def test_the_usual_spellings(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0))
        self.assertEqual(updater.parse_version("  v0.10.1 "), (0, 10, 1))

    def test_junk_is_none(self):
        for мусор in ("", "v", "1", "v1.2.3.4", "последняя", "1.2.beta",
                      "1..2", None, 123, "-1.2.3"):
            self.assertIsNone(updater.parse_version(мусор), мусор)


# --- Проверка обновления ----------------------------------------------------


class CheckTests(unittest.TestCase):
    def _проверить(self, ответы):
        with _сеть(ответы):
            return updater.check()

    def test_repository_is_not_published_yet(self):
        # 404 — это не поломка, а «рано». Пульт должен сказать об этом спокойно.
        итог = self._проверить([_Ответ(404)])
        self.assertEqual(итог, {"ok": True, "state": "no_repo",
                               "current": config.VERSION})

    def test_no_network_is_an_error_not_an_exception(self):
        class _Обрыв:
            def __init__(self, **kwargs):
                raise OSError("сеть упала")

        with mock.patch("httpx.Client", _Обрыв):
            итог = updater.check()
        self.assertFalse(итог["ok"])
        self.assertEqual(итог["state"], "error")
        self.assertEqual(итог["error"], "нет связи с GitHub")
        self.assertEqual(итог["current"], config.VERSION)

    def test_server_trouble_and_broken_json_are_the_same_error(self):
        for ответ in (_Ответ(500), _Ответ(502), _Ответ(200, None, падает=True)):
            with self.subTest(ответ=ответ.status_code):
                итог = self._проверить([ответ])
                self.assertFalse(итог["ok"])
                self.assertEqual(итог["error"], "нет связи с GitHub")

    def test_a_newer_release(self):
        итог = self._проверить([_выпуск()])
        self.assertTrue(итог["ok"])
        self.assertEqual(итог["state"], "newer")
        self.assertEqual(итог["latest"], "9.9.9")
        self.assertEqual(итог["current"], config.VERSION)
        self.assertEqual(итог["title"], "Труба v9.9.9")
        self.assertEqual(итог["notes"], "Что нового")
        self.assertEqual(итог["published"], "2026-09-27T10:00:00Z")
        self.assertTrue(итог["zip"].startswith("https://api.github.com/"))

    def test_the_same_release_is_latest(self):
        итог = self._проверить([_выпуск(тег=f"v{config.VERSION}")])
        self.assertEqual(итог["state"], "latest")
        self.assertEqual(итог["latest"], config.VERSION)

    def test_a_older_release_means_we_are_ahead(self):
        # Так бывает у того, кто правит код: на GitHub ещё старая версия.
        итог = self._проверить([_выпуск(тег="v0.0.1")])
        self.assertEqual(итог["state"], "ahead")
        self.assertEqual(итог["latest"], "0.0.1")

    def test_notes_are_cut(self):
        итог = self._проверить([_выпуск(body="а" * 9000)])
        self.assertEqual(len(итог["notes"]), updater.NOTES_LIMIT)

    def test_it_asks_github_about_our_own_repository(self):
        self._проверить([_выпуск()])
        self.assertEqual(len(_Клиент.вызвано), 1)
        self.assertEqual(
            _Клиент.вызвано[0],
            f"https://api.github.com/repos/{config.UPDATE_REPO}/releases/latest")

    def test_an_unreadable_tag_is_an_error(self):
        итог = self._проверить([_выпуск(тег="что-то")])
        self.assertFalse(итог["ok"])
        self.assertEqual(итог["state"], "error")
        self.assertEqual(итог["error"], "непонятный номер версии на GitHub")



# --- Установка -------------------------------------------------------------

# Личные данные в поддельном выпуске: всё это лежит в архиве и не должно
# измениться ни на байт.
ЛИЧНОЕ = {
    "data/history.json": '["реплики хозяина"]',
    "data/session.log": "журнал",
    "settings.json": '{"provider": "deepseek"}',
    ".env": "DEEPSEEK_API_KEY=секрет",
    "apps.json": '[]',
    "prompts/persona.md": "характер хозяина",
    "models/модель.bin": "веса",
    "voice/образец.wav": "голос",
    "vendor/thing.js": "стороннее",
    "скрины/снимок.png": "картинка",
}


def _проект(tmp: Path) -> Path:
    """Настоящий проект во временной папке: код есть, личное тоже.

    Без `coordination/`: такая папка — рабочая, и установка в ней отказана
    (отдельный тест ниже).
    """
    корень = tmp / "truba"
    for папка in ("core", "ui", "data", "prompts", "models", "voice", "vendor",
                 "скрины"):
        (корень / папка).mkdir(parents=True, exist_ok=True)
    (корень / "core" / "phone.py").write_text("старая версия\n", encoding="utf-8")
    (корень / "ui" / "window.py").write_text("старое окно\n", encoding="utf-8")
    (корень / "config.py").write_text("VERSION = '0.9.0'\n", encoding="utf-8")
    (корень / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    (корень / "Труба.vbs").write_text("WScript\n", encoding="utf-8")
    for имя, текст in ЛИЧНОЕ.items():
        (корень / имя).write_text(текст, encoding="utf-8")
    return корень


class ОбщиеУсловия(unittest.TestCase):
    """Временный проект, подменённый pip и подменённый smoke."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-updater-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = _проект(self.tmp)
        self.шаги: list = []
        # pip и smoke — заглушки: настоящий pip ходит в сеть, а smoke
        # грузил бы модели.
        pip_patch = mock.patch.object(updater, "_pip_install",
                                      mock.Mock(return_value=True))
        smoke_patch = mock.patch.object(updater, "_smoke",
                                        mock.Mock(return_value=True))
        self.pip = pip_patch.start()
        self.smoke = smoke_patch.start()
        self.addCleanup(pip_patch.stop)
        self.addCleanup(smoke_patch.stop)
        self.выпуск = {"state": "newer", "zip":
                       "https://api.github.com/repos/levkeyvill/truba/zipball/v9.9.9",
                       "latest": "9.9.9"}

    def _ставить(self, файлы, выпуск=None):
        with _качать(_zip(файлы)):
            return updater.install(выпуск or self.выпуск, root=self.root,
                                   on_step=self.шаги.append)


class InstallTests(ОбщиеУсловия):
    def test_the_code_is_replaced(self):
        итог = self._ставить({
            "core/phone.py": "новая версия\n",
            "ui/web_runtime.py": "новый пульт\n",
            "config.py": "VERSION = '9.9.9'\n",
        })
        self.assertTrue(итог["ok"], итог)
        self.assertEqual(итог["from"], config.VERSION)
        self.assertEqual(итог["to"], "9.9.9")
        self.assertTrue(итог["restart"])
        self.assertEqual((self.root / "core" / "phone.py").read_text(encoding="utf-8"),
                         "новая версия\n")
        self.assertEqual((self.root / "config.py").read_text(encoding="utf-8"),
                         "VERSION = '9.9.9'\n")
        # Пульт показывает каждый шаг установки.
        self.assertIn("качаю обновление", self.шаги)
        self.assertIn("проверяю запуск", self.шаги)

    def test_the_person_is_never_touched(self):
        # Личное лежит в архиве — и обязано остаться прежним.
        итог = self._ставить(dict(ЛИЧНОЕ, **{
            "core/phone.py": "новая версия\n"}))
        self.assertTrue(итог["ok"], итог)
        for имя, текст in ЛИЧНОЕ.items():
            with self.subTest(имя=имя):
                self.assertEqual((self.root / имя).read_text(encoding="utf-8"),
                                 текст)

    def test_local_files_that_are_not_in_the_release_stay(self):
        (self.root / "core" / "заметка_разработчика.py").write_text(
            "не из выпуска\n", encoding="utf-8")
        итог = self._ставить({"core/phone.py": "новая\n"})
        self.assertTrue(итог["ok"], итог)
        self.assertTrue((self.root / "core" / "заметка_разработчика.py").is_file())

    def test_a_backup_is_made(self):
        итог = self._ставить({"core/phone.py": "новая\n"})
        self.assertTrue(итог["ok"], итог)
        бэкапы = list((self.root / "data" / "updates").glob("backup_*.zip"))
        self.assertEqual(len(бэкапы), 1)
        self.assertIn(config.VERSION, бэкапы[0].name)
        with zipfile.ZipFile(бэкапы[0]) as коробка:
            self.assertIn("core/phone.py", коробка.namelist())

    def test_last_json_remembers_the_move(self):
        итог = self._ставить({"core/phone.py": "новая\n"})
        self.assertTrue(итог["ok"], итог)
        запись = json.loads(
            (self.root / "data" / "updates" / "last.json").read_text(encoding="utf-8"))
        self.assertEqual(запись["from"], config.VERSION)
        self.assertEqual(запись["to"], "9.9.9")
        self.assertTrue(запись["when"])

    def test_no_more_than_three_backups_are_kept(self):
        папка = self.root / "data" / "updates"
        папка.mkdir(parents=True, exist_ok=True)
        for номер in range(5):
            (папка / f"backup_0.0.{номер}_2020-01-0{номер + 1}_0000.zip").write_bytes(
                b"PK\x05\x06" + b"\0" * 18)
        self._ставить({"core/phone.py": "новая\n"})
        осталось = sorted(p.name for p in папка.glob("backup_*.zip"))
        self.assertEqual(len(осталось), updater.KEEP_BACKUPS)
        # Удалён самый старый, а не свежий.
        self.assertNotIn("backup_0.0.0_2020-01-01_0000.zip", осталось)


class ОтказTests(ОбщиеУсловия):
    """Отказы: путь в архиве, рабочая папка, упавший smoke."""

    def test_evil_paths_change_nothing(self):
        for плохое in ("../evil.py", "../../windows/system32/evil.py",
                       "/абсолютный/evil.py", "C:/зло/evil.py",
                       "core/../../evil.py"):
            with self.subTest(имя=плохое):
                (self.root / "core" / "phone.py").write_text(
                    "старая версия\n", encoding="utf-8")
                with _качать(_zip_без_проверки({плохое: "зло\n"})):
                    итог = updater.install(self.выпуск, root=self.root)
                self.assertFalse(итог["ok"], плохое)
                self.assertIn("недопустимое имя", итог["error"])
                # Ничего не изменилось, бэкапа тоже нет: отказываемся до
                # самой замены.
                self.assertEqual(
                    (self.root / "core" / "phone.py").read_text(encoding="utf-8"),
                    "старая версия\n")
                self.assertFalse(
                    (self.root / "data" / "updates").exists()
                    and list((self.root / "data" / "updates").glob("backup_*.zip")))

    def test_the_developer_folder_refuses_without_downloading(self):
        # В папке разработки стоит `coordination/` — обновлять её нельзя.
        (self.root / "coordination").mkdir()
        скачано = []

        def скачать(url, куда, timeout=300.0):
            скачано.append(url)
            Path(куда).write_bytes(_zip({"core/phone.py": "зло\n"}))

        with mock.patch.object(updater, "_скачать", скачать):
            итог = updater.install(self.выпуск, root=self.root)
        self.assertFalse(итог["ok"])
        self.assertEqual(итог["error"],
                         "это рабочая папка разработки — её обновлять нельзя")
        self.assertEqual(скачано, [])
        self.assertEqual(
            (self.root / "core" / "phone.py").read_text(encoding="utf-8"),
            "старая версия\n")

    def test_a_failed_smoke_rolls_everything_back(self):
        self.smoke.return_value = False
        итог = self._ставить({"core/phone.py": "новая\n",
                               "config.py": "VERSION = '9.9.9'\n"})
        self.assertFalse(итог["ok"])
        self.assertTrue(итог["rolled_back"])
        self.assertEqual(
            (self.root / "core" / "phone.py").read_text(encoding="utf-8"),
            "старая версия\n")
        self.assertEqual((self.root / "config.py").read_text(encoding="utf-8"),
                         "VERSION = '0.9.0'\n")

    def test_a_failed_pip_rolls_back_too(self):
        self.pip.return_value = False
        (self.root / ".venv" / "Scripts").mkdir(parents=True)
        (self.root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
        итог = self._ставить({"core/phone.py": "новая\n",
                               "requirements.txt": "httpx\ntorch\n"})
        self.assertFalse(итог["ok"])
        self.assertTrue(итог["rolled_back"])
        self.assertEqual(
            (self.root / "core" / "phone.py").read_text(encoding="utf-8"),
            "старая версия\n")


class ТребованияTests(ОбщиеУсловия):
    """pip зовётся только когда requirements.txt действительно изменился."""

    def setUp(self):
        super().setUp()
        (self.root / ".venv" / "Scripts").mkdir(parents=True)
        (self.root / ".venv" / "Scripts" / "python.exe").write_bytes(b"")

    def test_changed_requirements_call_pip(self):
        итог = self._ставить({"requirements.txt": "httpx\ntorch\n"})
        self.assertTrue(итог["ok"], итог)
        self.pip.assert_called_once()
        self.assertIn("ставлю библиотеки", self.шаги)

    def test_unchanged_requirements_do_not_call_pip(self):
        итог = self._ставить({"requirements.txt": "httpx\n"})
        self.assertTrue(итог["ok"], итог)
        self.pip.assert_not_called()

    def test_without_a_venv_libraries_are_not_checked(self):
        shutil.rmtree(self.root / ".venv")
        итог = self._ставить({"requirements.txt": "httpx\ntorch\n"})
        self.assertTrue(итог["ok"], итог)
        self.pip.assert_not_called()
        self.assertIn("библиотеки не проверены", self.шаги)



# --- API -------------------------------------------------------------------

# Порт, на котором поднялся сервер: `_local_secret` сверяет Host с ним.
ПОРТ: list = []


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _пульт() -> WebRuntime:
    """Пульт без голоса и без журнала: только состояние обновления."""
    среда = object.__new__(WebRuntime)
    среда._lock = threading.Lock()
    среда._update_lock = threading.Lock()
    среда._update_check = None
    среда._update_running = False
    среда._update_steps = deque(maxlen=30)
    среда._update_result = None
    среда._pult_close = None
    среда._remember = lambda kind, payload: None
    # Фон — прямой вызов: иначе тест ждал бы поток.
    среда._bg = lambda func, *args: func(*args)
    return среда


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Погода ходит в сеть — в тесте она не нужна.
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)
        ПОРТ.append(cls.server.port)

    def setUp(self):
        from fastapi.testclient import TestClient

        self.server.runtime = _пульт()
        self.client = TestClient(self.server._app, base_url=f"http://127.0.0.1:{ПОРТ[0]}")
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app, base_url=f"http://127.0.0.1:{ПОРТ[0]}",
                                client=("192.168.1.50", 5555))

    def test_about_tells_the_version(self):
        тело = self.client.get("/api/about").json()
        self.assertTrue(тело["ok"])
        self.assertEqual(тело["version"], config.VERSION)
        self.assertEqual(тело["repo"], config.UPDATE_REPO)
        self.assertEqual(тело["author"], config.AUTHOR)
        self.assertIs(тело["update_check"], True)

    def test_about_is_not_given_to_the_network(self):
        self.assertEqual(self.вдали.get("/api/about").status_code, 403)

    def test_install_is_refused_to_a_stranger(self):
        # С телефона обновление запускать нельзя: это запись в файлы.
        ответ = self.вдали.post("/api/update/install")
        self.assertEqual(ответ.status_code, 403)
        self.assertFalse(ответ.json()["ok"])

    def test_install_is_refused_with_a_foreign_host(self):
        # Страница с чужим Host (DNS rebinding) — тоже отказ.
        ответ = self.client.post("/api/update/install",
                                  headers={"host": "evil.example"})
        self.assertEqual(ответ.status_code, 403)
        self.assertFalse(ответ.json()["ok"])

    def test_restart_is_refused_to_a_stranger(self):
        self.assertEqual(self.вдали.post("/api/update/restart").status_code, 403)

    def test_install_without_a_check_asks_to_check_first(self):
        ответ = self.client.post("/api/update/install")
        self.assertFalse(ответ.json()["ok"])
        self.assertIn("сначала проверь", ответ.json()["error"])

    def test_a_second_install_while_running_is_refused(self):
        self.server.runtime._update_check = {
            "state": "newer", "zip": "https://api.github.com/x.zip", "latest": "9.9.9"}
        self.server.runtime._update_running = True
        ответ = self.client.post("/api/update/install")
        self.assertFalse(ответ.json()["ok"])
        self.assertIn("уже идёт", ответ.json()["error"])

    def test_status_carries_steps_and_the_last_check(self):
        self.server.runtime._update_check = {"state": "latest", "current": "0.9.0"}
        self.server.runtime._update_steps.append("качаю обновление")
        тело = self.client.get("/api/update/status").json()
        self.assertTrue(тело["ok"])
        self.assertFalse(тело["running"])
        self.assertEqual(тело["steps"], ["качаю обновление"])
        self.assertIsNone(тело["result"])
        self.assertEqual(тело["last"]["state"], "latest")

    def test_check_stores_the_result_in_the_pult(self):
        with _сеть([_выпуск()]):
            тело = self.client.post("/api/update/check").json()
        self.assertEqual(тело["state"], "newer")
        self.assertEqual(self.server.runtime._update_check["latest"], "9.9.9")

    def test_install_runs_in_the_background_and_answers_at_once(self):
        # Сеть не нужна: подменяем саму установку.
        with mock.patch.object(updater, "install",
                               mock.Mock(return_value={"ok": True, "to": "9.9.9"})):
            self.server.runtime._update_check = {
                "state": "newer", "zip": "https://api.github.com/x.zip",
                "latest": "9.9.9"}
            тело = self.client.post("/api/update/install").json()
        self.assertEqual(тело, {"ok": True, "started": True})
        self.assertEqual(self.server.runtime._update_result["to"], "9.9.9")
        self.assertFalse(self.server.runtime._update_running)


if __name__ == "__main__":
    unittest.main()

