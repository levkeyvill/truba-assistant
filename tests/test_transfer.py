"""Перенос настроек: файл экспорта, разбор и применение.

02.10: человек удалил Трубу, поставил заново — и снова ходил за ключом, заново
рисул кнопки телефона, заново надиктовывал голос. Теперь есть один файл, который
уносит всё это на другую машину.

Здесь проверяется и то, что файл собирается правильно, и то, что чужой или
подсунутый архив не может ничего протащить в настройки: белый список путей,
`..` и абсолютные пути — отказ всего файла, битый JSON — отказ, ключи берутся
только известные.

Настоящих путей здесь нет: корень проекта, `data/` и папка «Документы» уезжают
во временную, проводник подменён, голос и сеть не запускаются.
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
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import notes, settings, transfer
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime

ПОРТ: list = []


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _пульт(сервер=None) -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер, голос и мозг."""
    среда = object.__new__(WebRuntime)
    среда.server = сервер
    среда._lock = threading.Lock()
    среда._transfer_lock = threading.Lock()
    среда._transfer_blob = None
    среда._transfer_token = ""
    среда.события = []
    среда._remember = lambda kind, payload: среда.события.append((kind, payload))
    среда.log_message = lambda text: среда.события.append(("log", text))
    среда.close_pult = lambda: среда.события.append(("closed", None))
    среда.voice = NS(running=False, _listener=None, _speaker_idle=lambda: True,
                     in_conversation=False)
    return среда


class ПереносBase(unittest.TestCase):
    """Временный корень вместо проекта и временные «Документы»."""

    def setUp(self):
        self.база = Path(tempfile.mkdtemp(prefix="truba-transfer-"))
        self.addCleanup(shutil.rmtree, self.база, True)
        self.корень = self.база / "Труба"
        self.данные = self.корень / "data"
        self.документы = self.база / "Документы"
        self.данные.mkdir(parents=True)
        (self.корень / "prompts").mkdir(parents=True)
        # Ключи, которые переносить можно, берутся из `.env.example` рядом с
        # проектом — в тесте он тоже настоящий, только свой.
        (self.корень / ".env.example").write_text(
            "# пример\nLLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=\n",
            encoding="utf-8")
        for подмена in (mock.patch.object(config, "ROOT", self.корень),
                        mock.patch.object(config, "DATA_DIR", self.данные),
                        mock.patch("core.notes.documents_dir",
                                   return_value=self.документы)):
            подмена.start()
            self.addCleanup(подмена.stop)

    def _файл(self, относительный: str, содержимое) -> Path:
        """Положить файл части на диск (путь как в архиве)."""
        путь = transfer._путь(относительный)
        путь.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(содержимое, bytes):
            путь.write_bytes(содержимое)
        else:
            путь.write_text(содержимое, encoding="utf-8")
        return путь

    def _настоящая_установка(self) -> None:
        """Всё содержимое частей на месте — как у настроенной Трубы."""
        self._файл("settings.json", json.dumps({"first_run_done": True,
                                                "voice_name": "мой"}))
        self._файл("apps.json", json.dumps([{"id": "firefox", "title": "Firefox"}]))
        self._файл("prompts/persona.md", "Я Труба.\n")
        self._файл("data/memory.json", json.dumps({"facts": [{"text": "кот"}]}))
        self._файл("data/history.json", json.dumps({"turns": []}))
        self._файл("data/reminders.json", json.dumps([]))
        self._файл("data/voiceprint.npz", "отпечаток".encode("utf-8"))
        self._файл("data/phone_key.txt", "ключ-телефона")
        self._файл("data/icons/custom/firefox.png", "картинка".encode("utf-8"))
        self._файл("voice/мой.wav", "звук".encode("utf-8"))
        self._файл("voice/мой.txt", "мой текст")
        self._файл("voice/без-текста.wav", "один звук".encode("utf-8"))
        self._файл(".env", "LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=скрытый\n"
                           "МОЙ_ПАРОЛЬ=не трогать\n")

    def _архив(self, файлы: dict, формат=transfer.FORMAT, манифест=None) -> bytes:
        """Собрать ZIP руками — так проверяем то, что Труба не собирала."""
        буфер = io.BytesIO()
        with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED) as коробка:
            for имя, содержимое in файлы.items():
                коробка.writestr(имя, содержимое)
            if манифест is not False:
                коробка.writestr(transfer.ЗАПИСЬ, json.dumps(
                    манифест if манифест is not None else {
                        "format": формат, "version": config.VERSION,
                        "created": "2026-10-02T09:30:00", "parts": [],
                        "files": {}}, ensure_ascii=False))
        return буфер.getvalue()

    def _наш_архив(self, части=("settings", "persona")) -> bytes:
        """Настоящий файл переноса из текущего состояния диска."""
        итог = transfer.export(list(части),
                               folder=self.база / "экспорт")
        return Path(итог["path"]).read_bytes()
class ЭкспортTests(ПереносBase):
    """Экспорт собирает выбранное, молча пропускает отсутствующее."""

    def test_собирает_выбранные_части(self):
        self._настоящая_установка()
        итог = transfer.export(["settings", "persona", "memory"],
                               folder=self.база / "экспорт")
        self.assertTrue(итог["ok"])
        self.assertGreater(итог["bytes"], 0)
        with zipfile.ZipFile(итог["path"]) as коробка:
            имена = set(коробка.namelist())
            манифест = json.loads(коробка.read(transfer.ЗАПИСЬ))
        self.assertEqual(манифест["format"], transfer.FORMAT)
        self.assertEqual(манифест["version"], config.VERSION)
        self.assertTrue(манифест["created"])
        self.assertEqual(sorted(итог["parts"]), ["memory", "persona", "settings"])
        self.assertEqual(имена, {"transfer.json", "settings.json",
                                 "prompts/persona.md", "data/memory.json",
                                 "data/history.json"})
        self.assertEqual(манифест["files"]["settings.json"],
                         (self.корень / "settings.json").stat().st_size)

    def test_имя_файла_и_папка_по_умолчанию(self):
        self._настоящая_установка()
        итог = transfer.export(["settings"])
        путь = Path(итог["path"])
        self.assertEqual(путь.parent, self.документы / transfer.EXPORT_FOLDER)
        self.assertRegex(путь.name,
                         r"^Труба-перенос-\d{4}-\d{2}-\d{2}_\d{4}\.zip$")

    def test_отсутствующие_части_пропускаются(self):
        # Свежая установка: настроек и характера нет, переносить нечего, и
        # жаловаться незачем — хозяин отметил что можно.
        self._файл(".env", "LLM_PROVIDER=deepseek\n")
        итог = transfer.export(["settings", "persona", "keys"],
                               folder=self.база / "экспорт")
        self.assertEqual(итог["parts"], ["keys"])
        with zipfile.ZipFile(итог["path"]) as коробка:
            self.assertEqual(set(коробка.namelist()),
                             {"transfer.json", ".env"})

    def test_переносить_нечего_это_честный_отказ(self):
        with self.assertRaises(ValueError):
            transfer.export(["settings"], folder=self.база / "экспорт")
        with self.assertRaises(ValueError):
            transfer.export([], folder=self.база / "экспорт")
        with self.assertRaises(ValueError):
            transfer.export(["ерунда"], folder=self.база / "экспорт")

    def test_ключи_только_известные(self):
        # Своя пометка и пароль стороннего сервиса в перенос не идут: файл
        # переноса хозяин может отдать другому человеку.
        self._файл(".env", "# ключи\nLLM_PROVIDER=openrouter\n"
                           "OPENROUTER_API_KEY=открытый\n"
                           "МОЙ_ПАРОЛЬ=не трогать\n"
                           "строка без знака равно\n")
        итог = transfer.export(["keys"], folder=self.база / "экспорт")
        with zipfile.ZipFile(итог["path"]) as коробка:
            текст = коробка.read(".env").decode("utf-8")
        self.assertIn("OPENROUTER_API_KEY=открытый", текст)
        self.assertIn("LLM_PROVIDER=openrouter", текст)
        self.assertNotIn("МОЙ_ПАРОЛЬ", текст)
        self.assertNotIn("знака равно", текст)

    def test_отпечаток_установки_журналы_и_кеши_не_попадают(self):
        self._настоящая_установка()
        self._файл("data/install_id.txt", "отпечаток-этой-копии")
        self._файл("data/session.log", "журнал разговора")
        self._файл("data/usage.jsonl", '{"tokens": 1}')
        self._файл("data/apps_cache.json", "{}")
        self._файл("data/icons/firefox-128-abc.png", "кеш значка")
        self._файл("data/higgs_refs/что-то", "кеш модели")
        self._файл("data/tts/что-то", "кеш озвучки")
        self._файл("data/silero.onnx", "модель")
        self._файл("data/search_history.json", "[]")
        итог = transfer.export(list(transfer.ТАБЛИЦА), folder=self.база / "экспорт")
        with zipfile.ZipFile(итог["path"]) as коробка:
            имена = set(коробка.namelist())
        for лишний in ("data/install_id.txt", "data/session.log",
                       "data/usage.jsonl", "data/apps_cache.json",
                       "data/icons/firefox-128-abc.png", "data/higgs_refs/что-то",
                       "data/tts/что-то", "data/silero.onnx",
                       "data/search_history.json"):
            self.assertNotIn(лишний, имена)
        # а свои на месте
        self.assertIn("data/icons/custom/firefox.png", имена)
        self.assertIn("data/voiceprint.npz", имена)

    def test_голос_без_текста_не_переносится(self):
        self._настоящая_установка()
        итог = transfer.export(["voices"], folder=self.база / "экспорт")
        with zipfile.ZipFile(итог["path"]) as коробка:
            имена = set(коробка.namelist())
        self.assertIn("voice/мой.wav", имена)
        self.assertIn("voice/мой.txt", имена)
        self.assertNotIn("voice/без-текста.wav", имена)
        self.assertEqual(итог["parts"], ["voices"])

    def test_список_частей_для_галочек(self):
        self._настоящая_установка()
        свод = {часть["id"]: часть for часть in transfer.части()}
        self.assertEqual(sorted(свод), sorted(transfer.ТАБЛИЦА))
        self.assertEqual(свод["settings"]["title"], "Настройки")
        self.assertTrue(свод["settings"]["ready"])
        self.assertGreater(свод["settings"]["bytes"], 0)
        self.assertTrue(свод["settings"]["size"].endswith("Б"))
        # подпись голоса — общий размер, а не «файлов: 3»
        self.assertEqual(свод["voices"]["files"], 3)
        self.assertEqual(свод["persona"]["title"], "Характер")
        self.assertEqual(свод["keys"]["files"], 1)
class РазборTests(ПереносBase):
    """Чужой архив, чужой путь или битый файл — отказ всего файла."""

    def test_наш_файл_разбирается(self):
        self._настоящая_установка()
        разбор = transfer.inspect(self._наш_архив(["settings", "persona", "keys"]))
        self.assertTrue(разбор["ok"])
        self.assertEqual(разбор["version"], config.VERSION)
        self.assertTrue(разбор["created"])
        части = {часть["id"]: часть for часть in разбор["parts"]}
        self.assertEqual(sorted(части), ["keys", "persona", "settings"])
        self.assertEqual(части["settings"]["title"], "Настройки")
        self.assertEqual(части["settings"]["files"], 1)
        self.assertGreater(части["settings"]["bytes"], 0)
        self.assertEqual(разбор["files"], 3)

    def test_чужой_zip_и_не_zip(self):
        буфер = io.BytesIO()
        with zipfile.ZipFile(буфер, "w") as коробка:
            коробка.writestr("settings.json", "{}")
            коробка.writestr("документ.txt", "просто архив")
        with self.assertRaises(ValueError):
            transfer.inspect(буфер.getvalue())
        with self.assertRaises(ValueError):
            transfer.inspect("это вообще не zip".encode("utf-8"))
        with self.assertRaises(ValueError):
            transfer.inspect(b"")
        with self.assertRaises(ValueError):
            transfer.inspect("не байты")

    def test_чужой_формат(self):
        # Формат другой версии Трубы — не наш: сначала обновиться, потом переносить.
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({"settings.json": "{}"}, формат=99))
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({}, манифест="не словарь"))
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({}, манифест=False))

    def test_путь_с_две_точки_и_абсолютный(self):
        # Подсовывать пути умеет всё, что скачано со стороны: такой файл не
        # применяется целиком, а не «пропустим этот файл».
        for имя in ("../settings.json", "data/../../настройки.json",
                    "C:\\Windows\\win.ini", "/etc/passwd", "..\\settings.json"):
            with self.subTest(имя=имя):
                with self.assertRaises(ValueError):
                    transfer.inspect(self._архив({имя: "{}"}))

    def test_обратный_слэш_не_проходит_мимо_белого_списка(self):
        # `zipfile` разворачивает слэш при чтении сам, поэтому такой архив
        # собираем руками: подменяем имя прямо в байтах (длина та же).
        # `data\keys.json` после разворота — `data/keys.json`, а такого файла
        # в переносе нет, и архив отклоняется целиком.
        наш = self._архив({"data/keys.json": "{}"})
        подмена = наш.replace(b"data/keys.json", b"data\\keys.json")
        self.assertEqual(len(наш), len(подмена))
        with self.assertRaises(ValueError):
            transfer.inspect(подмена)

    def test_лишний_файл_отказывает_весь_архив(self):
        # Рядом с нашими лежит чужой `data/keys.json` — значит, архив собран не
        # нами, и доверять ему нельзя ни одну часть.
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({"settings.json": "{}",
                                          "data/keys.json": "{}"}))
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({"data/session.log": "журнал"}))

    def test_битый_json_и_не_тот_вид(self):
        for имя, содержимое in (("settings.json", "{бито"),
                                ("apps.json", '{"не список"}'),
                                ("data/memory.json", "[]"),
                                ("data/history.json", "ерунда"),
                                ("data/reminders.json", "{}")):
            with self.subTest(имя=имя):
                with self.assertRaises(ValueError):
                    transfer.inspect(self._архив({имя: содержимое}))

    def test_не_json_а_json_читается(self):
        # Настоящий архив разбирается: наши файлы нужного вида.
        разбор = transfer.inspect(self._архив({
            "settings.json": json.dumps({"first_run_done": True}),
            "apps.json": json.dumps([{"id": "firefox"}]),
            "data/memory.json": json.dumps({"facts": []}),
            "data/history.json": json.dumps({"turns": []}),
            "data/reminders.json": json.dumps([])}))
        self.assertEqual(len(разбор["parts"]), 4)

    def test_слишком_большой_архив(self):
        # Потолок задаёт модуль, а сам вес не придумываем: подставляем
        # копеечный, иначе тесту пришлось бы писать 300 МБ на диск.
        with mock.patch.object(transfer, "MAX_BYTES", 200):
            with self.assertRaises(ValueError):
                transfer.inspect(self._архив({"settings.json": "x" * 4000}))
        # и по числу файлов
        with mock.patch.object(transfer, "MAX_FILES", 2):
            with self.assertRaises(ValueError):
                transfer.inspect(self._архив({"settings.json": "{}",
                                              "apps.json": "[]",
                                              "prompts/persona.md": "я"}))

    def test_в_архиве_нет_ни_одной_нашей_части(self):
        with self.assertRaises(ValueError):
            transfer.inspect(self._архив({}))
class ПрименениеTests(ПереносBase):
    """Применение пишет файлы, снимает копию «до переноса» и не вычёркивает."""

    def test_файлы_на_месте_и_копия_до_переноса(self):
        self._настоящая_установка()
        # Приезжает файл с других настроек: своё содержимое чистим, чтобы
        # проверить, что применилось именно из архива.
        приехало = self._архив({
            "settings.json": json.dumps({"voice_name": "чужой"}),
            "prompts/persona.md": "Я другой характер.\n",
            "data/memory.json": json.dumps({"facts": [{"text": "пёс"}]})})
        shutil.rmtree(self.корень, ignore_errors=True)
        self.данные.mkdir(parents=True)
        (self.корень / "prompts").mkdir(parents=True)
        (self.корень / ".env.example").write_text("LLM_PROVIDER=deepseek\n",
                                                  encoding="utf-8")
        self._файл("settings.json", json.dumps({"voice_name": "мой"}))
        self._файл("prompts/persona.md", "Я Труба.\n")
        self._файл("data/memory.json", json.dumps({"facts": [{"text": "кот"}]}))

        итог = transfer.apply(приехало, ["settings", "persona", "memory"])
        self.assertTrue(итог["ok"])
        self.assertEqual(sorted(итог["parts"]), ["memory", "persona", "settings"])
        настройки = json.loads((self.корень / "settings.json")
                               .read_text(encoding="utf-8"))
        self.assertEqual(настройки["voice_name"], "чужой")
        self.assertEqual((self.корень / "prompts" / "persona.md")
                         .read_text(encoding="utf-8"), "Я другой характер.\n")
        self.assertEqual(json.loads((self.данные / "memory.json")
                                    .read_text(encoding="utf-8"))["facts"],
                         [{"text": "пёс"}])
        # мастер после переноса не нужен
        self.assertIs(настройки["first_run_done"], True)

        # копия «до переноса» — в data/перенос, с прежним содержимым
        копия = Path(итог["backup"])
        self.assertTrue(копия.is_file(), итог)
        self.assertEqual(копия.parent, self.данные / transfer.BACKUP_FOLDER)
        self.assertIn("до-переноса", копия.name)
        with zipfile.ZipFile(копия) as коробка:
            прежние = json.loads(коробка.read("settings.json"))
        self.assertEqual(прежние["voice_name"], "мой")
    def test_ключи_сливаются_а_остальное_остаётся(self):
        self._файл(".env", "# мои ключи\nLLM_PROVIDER=deepseek\n"
                           "DEEPSEEK_API_KEY=старый\nМОЙ_ПАРОЛЬ=остаётся\n")
        приехало = self._архив({".env": "DEEPSEEK_API_KEY=новый\n"
                                        "OPENROUTER_API_KEY=приехал\n"})
        итог = transfer.apply(приехало, ["keys"])
        self.assertEqual(итог["parts"], ["keys"])
        env = (self.корень / ".env").read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY=новый", env)
        self.assertIn("OPENROUTER_API_KEY=приехал", env)
        self.assertNotIn("DEEPSEEK_API_KEY=старый", env)
        # своё не вычеркнуто, и настройка провайдера на месте
        self.assertIn("МОЙ_ПАРОЛЬ=остаётся", env)
        self.assertIn("LLM_PROVIDER=deepseek", env)

    def test_ключи_приходят_только_известные(self):
        self._файл(".env", "DEEPSEEK_API_KEY=старый\n")
        приехало = self._архив({".env": "DEEPSEEK_API_KEY=новый\n"
                                        "ЧУЖОЙ_КЛЮЧ=не брать\n"})
        transfer.apply(приехало, ["keys"])
        env = (self.корень / ".env").read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY=новый", env)
        self.assertNotIn("ЧУЖОЙ_КЛЮЧ", env)

    def test_битый_файл_не_применяется(self):
        with self.assertRaises(ValueError):
            transfer.apply(self._архив({"settings.json": "{бито"}), ["settings"])
        with self.assertRaises(ValueError):
            transfer.apply(self._архив({"settings.json": "{}"}), ["apps"])
        with self.assertRaises(ValueError):
            transfer.apply(self._архив({"settings.json": "{}"}), [])

    def test_настройки_читаются_пультом(self):
        # Проверка мягкая, как при старте: `load_settings` не должен падать, а
        # чужое значение — не должно открыть пульт с несуществующего шага.
        приехало = self._архив({"settings.json": json.dumps(
            {"first_run_done": True, "wizard_step": 99, "voices_wanted": ["ерунда"]})})
        с = mock.patch.object(settings, "SETTINGS_PATH",
                              self.корень / "settings.json")
        с.start()
        self.addCleanup(с.stop)
        итог = transfer.apply(приехало, ["settings"])
        self.assertTrue(итог["ok"])
        self.assertEqual(settings.load_settings()["wizard_step"],
                         settings.WIZARD_STEP_DEFAULT)
class ПроводникTests(ПереносBase):
    """`reveal` открывает только файлы из папок переноса."""

    def test_свой_файл_показывается(self):
        self._файл("settings.json", json.dumps({"first_run_done": True}))
        свой = transfer.export(["settings"], folder=self.документы
                               / transfer.EXPORT_FOLDER)["path"]
        with mock.patch("subprocess.Popen") as проводник:
            итог = transfer.reveal(свой)
        self.assertEqual(итог["path"], str(Path(свой).resolve()))
        self.assertEqual(проводник.call_args[0][0][0], "explorer")
        self.assertTrue(проводник.call_args[0][0][1].startswith("/select,"))

    def test_вне_папок_переноса_отказ(self):
        self._файл("settings.json", "{}")
        чужой = self.база / "чужой.zip"
        чужой.write_bytes(b"zip")
        for путь in (чужой, self.корень / "settings.json",
                     self.данные / "перенос" / "нет-такого.zip"):
            with self.subTest(путь=путь):
                with mock.patch("subprocess.Popen") as проводник:
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        transfer.reveal(путь)
                проводник.assert_not_called()

    def test_и_в_data_перенос(self):
        self._файл("settings.json", "{}")
        копия = transfer._собрать(["settings"],
                                  self.данные / transfer.BACKUP_FOLDER,
                                  transfer.BACKUP_LABEL)["path"]
        with mock.patch("subprocess.Popen") as проводник:
            self.assertTrue(transfer.reveal(копия)["ok"])
        self.assertTrue(проводник.called)


class ApiTests(ПереносBase):
    """Маршруты переноса: с компьютера да, с телефона нет."""

    @classmethod
    def setUpClass(cls):
        from core import weather

        подмена = mock.patch.object(weather, "Watcher")
        подмена.start()
        cls.addClassCleanup(подмена.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)
        ПОРТ.append(cls.server.port)

    def setUp(self):
        super().setUp()
        self._файл("settings.json", json.dumps({"first_run_done": True}))
        self.server.runtime = _пульт(self.server)
        self.client = TestClient(self.server._app,
                                 base_url=f"http://127.0.0.1:{ПОРТ[0]}")
        # Тот же сервер, но клиент из сети: телефон хозяина.
        self.вдали = TestClient(self.server._app,
                                base_url=f"http://127.0.0.1:{ПОРТ[0]}",
                                client=("192.168.1.50", 5555))
    def test_части_отдаются_с_подписью(self):
        тело = self.client.get("/api/transfer/parts").json()
        self.assertTrue(тело["ok"], тело)
        части = {часть["id"]: часть for часть in тело["parts"]}
        self.assertEqual(части["settings"]["title"], "Настройки")
        self.assertTrue(части["settings"]["ready"])

    def test_экспорт_и_применение(self):
        приехало = self._архив({"settings.json": json.dumps(
            {"voice_name": "приехал"})})
        # 1. экспорт с компьютера
        тело = self.client.post("/api/transfer/export",
                                json={"parts": ["settings"]}).json()
        self.assertTrue(тело["ok"], тело)
        self.assertTrue(Path(тело["path"]).is_file())
        self.assertEqual(тело["parts"], ["settings"])

        # 2. разбор принесённого файла: тело запроса — сам ZIP
        разбор = self.client.post("/api/transfer/inspect", content=приехало,
                                  headers={"Content-Type": "application/zip"})
        разбор = разбор.json()
        self.assertTrue(разбор["ok"], разбор)
        self.assertEqual(разбор["parts"][0]["id"], "settings")
        self.assertTrue(разбор["token"])

        # 3. применение — и перезапуск пульта тем же путём, что после обновления
        with mock.patch.object(self.server.runtime, "update_restart",
                               return_value={"ok": True, "restart": True}) as рестарт:
            итог = self.client.post("/api/transfer/apply",
                                    json={"token": разбор["token"],
                                          "parts": ["settings"]}).json()
        self.assertTrue(итог["ok"], итог)
        self.assertTrue(итог["restart"])
        рестарт.assert_called_once_with()
        self.assertEqual(json.loads((self.корень / "settings.json")
                                    .read_text(encoding="utf-8"))["voice_name"],
                         "приехал")

    def test_чужой_токен_и_чужая_часть(self):
        разбор = self.client.post("/api/transfer/inspect",
                                  content=self._архив({"settings.json": "{}"}),
                                  headers={"Content-Type": "application/zip"}).json()
        ответ = self.client.post("/api/transfer/apply",
                                 json={"token": "не тот", "parts": ["settings"]})
        self.assertEqual(ответ.status_code, 400)
        self.assertIn("заново", ответ.json()["error"])
        ответ = self.client.post("/api/transfer/apply",
                                 json={"token": разбор["token"], "parts": []})
        self.assertEqual(ответ.status_code, 400)
        ответ = self.client.post("/api/transfer/apply",
                                 json={"token": разбор["token"],
                                       "parts": ["ерунда"]})
        self.assertEqual(ответ.status_code, 400)

    def test_битый_файл_400_с_честным_текстом(self):
        ответ = self.client.post("/api/transfer/inspect",
                                 content="не zip".encode("utf-8"),
                                 headers={"Content-Type": "application/zip"})
        self.assertEqual(ответ.status_code, 400)
        self.assertIn("перенос", ответ.json()["error"])

    def test_показать_вне_папок_отказ(self):
        чужой = self.база / "чужой.zip"
        чужой.write_bytes(b"zip")
        with mock.patch("subprocess.Popen") as проводник:
            ответ = self.client.post("/api/transfer/reveal",
                                     json={"path": str(чужой)})
        self.assertEqual(ответ.status_code, 400)
        проводник.assert_not_called()
        свой = transfer.export(["settings"], folder=self.документы
                               / transfer.EXPORT_FOLDER)["path"]
        with mock.patch("subprocess.Popen") as проводник:
            тело = self.client.post("/api/transfer/reveal",
                                    json={"path": свой}).json()
        self.assertTrue(тело["ok"], тело)
        self.assertTrue(проводник.called)

    def test_с_телефона_закрыто(self):
        # В файле переноса ключи и память хозяина: телефон, лежащий в сети,
        # адрес пульта знает, а переносить ничего не должен.
        self.assertEqual(self.вдали.get("/api/transfer/parts").status_code, 403)
        for маршрут in ("/api/transfer/export", "/api/transfer/reveal",
                        "/api/transfer/inspect", "/api/transfer/apply"):
            with self.subTest(маршрут=маршрут):
                self.assertEqual(self.вдали.post(маршрут, json={},
                                                content=b"").status_code, 403)


if __name__ == "__main__":
    unittest.main()
