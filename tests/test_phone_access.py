"""Кто может подключиться к Трубе из сети (29.09, проверка кода).

До этого с любого устройства в том же Wi-Fi можно было прочитать настройки
с памятью о хозяине, журнал с дословными фразами, программы и сводку, а по
WebSocket — снять экран, читать заметки и жать кнопки. Теперь:

- данные пульта — только с этого компьютера;
- телефон подключается по ключу из ссылки в QR-коде;
- чужая страница (Origin не наш) к WebSocket не подключится даже отсюда.

Сервер поднимается по-настоящему на свободном порту, без погоды; ключ
лежит во временной папке (tests/test_data_guard.py подменяет путь).
"""

import socket
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
from core import phone, power, weather
from core.phone import PhoneServer

ROOT = Path(config.ROOT)


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _ws(client="192.168.1.50", origin=None, host="192.168.0.10:8765", key=None):
    """Подделка входящего WebSocket для `ws_refusal`: только то, что он читает."""
    headers = {"host": host}
    if origin is not None:
        headers["origin"] = origin
    query = {"k": key} if key is not None else {}
    return NS(headers=headers, query_params=query, client=NS(host=client))


class PhoneKeyTests(unittest.TestCase):
    def test_the_key_is_made_once_and_kept(self):
        первый = phone.phone_key()
        self.assertGreaterEqual(len(первый), phone.KEY_MIN)
        self.assertEqual(phone.phone_key(), первый)
        self.assertEqual(phone.KEY_FILE.read_text(encoding="utf-8").strip(), первый)

    def test_a_broken_key_file_is_replaced_by_a_new_key(self):
        phone.KEY_FILE.write_text("коротко", encoding="utf-8")
        новый = phone.phone_key()
        self.assertGreaterEqual(len(новый), phone.KEY_MIN)
        self.assertNotEqual(новый, "коротко")


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.ключ = phone.phone_key()

    def test_a_phone_from_the_network_needs_the_key(self):
        self.assertEqual(phone.ws_refusal(_ws()), "нет ключа")
        self.assertEqual(phone.ws_refusal(_ws(key="чужой-ключ-подбором")), "нет ключа")
        self.assertEqual(phone.ws_refusal(_ws(key=self.ключ)), "")

    def test_the_phone_page_origin_is_its_own_host(self):
        # Страницу телефон взял с этого же сервера — Origin совпадает с Host.
        свой = _ws(origin="http://192.168.0.10:8765", key=self.ключ)
        self.assertEqual(phone.ws_refusal(свой), "")

    def test_a_foreign_page_is_refused_even_from_this_computer(self):
        # Чужой сайт в браузере на этом же компьютере: адрес клиента свой,
        # а Origin — чужой. Ключ ему неоткуда взять, но и без ключа — нет.
        чужой = _ws(client="127.0.0.1", origin="https://evil.example",
                    host="127.0.0.1:8765")
        self.assertEqual(phone.ws_refusal(чужой), "чужая страница")

    def test_this_computer_needs_no_key(self):
        # Пульт и его макет телефона работают отсюда.
        self.assertEqual(phone.ws_refusal(_ws(client="127.0.0.1", host="127.0.0.1:8765")), "")
        макет = _ws(client="127.0.0.1", origin="http://127.0.0.1:8765", host="127.0.0.1:8765")
        self.assertEqual(phone.ws_refusal(макет), "")


class ServerAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            import time
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        self.здесь = TestClient(self.server._app)
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_pult_data_is_not_given_to_the_network(self):
        for путь in ("/state", "/api/runtime", "/api/voice/events",
                     "/api/settings", "/api/apps"):
            with self.subTest(путь=путь):
                ответ = self.вдали.get(путь)
                self.assertEqual(ответ.status_code, 403, путь)
                self.assertNotIn("memory", ответ.text)

    def test_this_computer_still_gets_the_state(self):
        self.assertEqual(self.здесь.get("/state").status_code, 200)

    def test_power_status_is_read_only_and_local(self):
        power.reset()
        self.assertEqual(self.вдали.get("/api/power/status").status_code, 403)
        ответ = self.здесь.get("/api/power/status")
        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(ответ.json(), {"ok": True, "runtime": False})
        self.assertEqual(self.здесь.post("/api/power/arm").status_code, 404)
        self.assertEqual(self.здесь.post("/api/power/disarm").status_code, 404)
        self.assertFalse(power.live_runtime())

    def test_the_phone_page_itself_is_open(self):
        # Страница и версия нужны телефону до всякого ключа.
        self.assertEqual(self.вдали.get("/").status_code, 200)
        self.assertEqual(self.вдали.get("/version").status_code, 200)

    def test_a_phone_without_the_key_is_told_so_and_dropped(self):
        with self.вдали.websocket_connect("/ws") as соединение:
            self.assertEqual(соединение.receive_json(), {"type": "need_key"})
            with self.assertRaises(WebSocketDisconnect):
                соединение.receive_text()

    def test_a_phone_with_the_key_gets_in(self):
        ключ = phone.phone_key()
        with self.вдали.websocket_connect("/ws?k=" + ключ) as соединение:
            первое = соединение.receive_json()
        self.assertEqual(первое["type"], "version")


class QrWithTheKeyTests(unittest.TestCase):
    """QR рисует ссылку с ключом — и только её (29.09: код не рисовался)."""

    def _qr(self, ссылка):
        from ui.web_runtime import WebRuntime

        пульт = object.__new__(WebRuntime)
        with mock.patch.object(phone, "local_addresses", return_value=["192.168.0.10"]):
            return пульт.phone_qr(ссылка)

    def test_the_link_with_our_key_is_drawn(self):
        ключ = phone.phone_key()
        ответ = self._qr(f"http://192.168.0.10:{config.PHONE_PORT}/?k={ключ}")
        self.assertIn(b"<svg", ответ.body)
        self.assertIn(b"<svg", self._qr(f"http://192.168.0.10:{config.PHONE_PORT}").body)

    def test_anything_else_is_refused(self):
        порт = config.PHONE_PORT
        for ссылка in (f"http://192.168.0.10:{порт}/?k=чужой",
                       f"http://192.168.0.10:{порт}/?k={phone.phone_key()}&x=1",
                       f"http://192.168.0.10:{порт}/другое",
                       f"http://evil.example:{порт}/",
                       "https://evil.example/?k=1"):
            with self.subTest(ссылка=ссылка):
                with self.assertRaises(ValueError):
                    self._qr(ссылка)

    def test_the_settings_page_shows_the_code_too(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("function настрПоказатьКод(", пульт)
        self.assertIn("телАдрес.addEventListener('change', () => настрПоказатьКод(телАдрес, телКод))", пульт)
        self.assertIn("if (эл.телКод) настрПоказатьКод(эл.телАдрес, эл.телКод);", пульт)


class PagesCarryTheKeyTests(unittest.TestCase):
    def test_the_phone_page_remembers_and_sends_the_key(self):
        страница = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("new URLSearchParams(location.search).get('k')", страница)
        self.assertIn("localStorage.setItem('ключ', изСсылки)", страница)
        self.assertIn("'/ws' + ключ", страница)
        self.assertIn("msg.type === 'need_key'", страница)
        self.assertIn("телефон не привязан", страница)

    def test_the_key_is_read_when_connecting_not_before(self):
        # 29.09 в браузере: `connect()` зовётся при загрузке выше по тексту,
        # а `let phoneKey` стоял ниже — ReferenceError, и страница телефона
        # падала целиком. Ключ читает функция, а не `let` на уровне файла.
        страница = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function ключПривязки()", страница)
        self.assertNotIn("let phoneKey", страница)
        self.assertNotIn("let needKey", страница)

    def test_the_phone_page_script_parses(self):
        # Там же: текст комментария оказался снаружи `/* */` — SyntaxError, и
        # телефон показывал пустую страницу. Проверяем разбором node.
        import re
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        страница = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        скрипты = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", страница, re.S)
        self.assertTrue(скрипты)
        for номер, код in enumerate(скрипты):
            with tempfile.TemporaryDirectory() as папка:
                файл = Path(папка) / "page.js"
                файл.write_text(код, encoding="utf-8")
                итог = subprocess.run([node, "--check", str(файл)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(итог.returncode, 0, f"скрипт {номер}: {итог.stderr[:400]}")

    def test_the_pult_puts_the_key_into_the_link_and_the_qr(self):
        пульт = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        self.assertIn("'?k=' + encodeURIComponent(телефонКлюч)", пульт)
        # И в настройках, и в мастере ключ берётся из ответа сервера.
        self.assertEqual(пульт.count("телефонКлюч = typeof данные.phone_key === 'string'"), 2)


if __name__ == "__main__":
    unittest.main()
