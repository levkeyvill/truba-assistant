"""Кнопка «Где взять ключ» мастера: `POST /api/open/key-page`.

Адрес открывается только из `config.KEY_PAGES` и только с этого компьютера:
с телефона и со страницы с чужим Host кнопка не должна ничего открывать.
`os.startfile` подменён — в тесте браузер на живом компе не открывается.
"""

import socket
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import config
from core.phone import PhoneServer
from ui.web_runtime import WebRuntime


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _пульт() -> WebRuntime:
    среда = object.__new__(WebRuntime)
    среда._lock = threading.Lock()
    среда._remember = lambda kind, payload: None
    среда.voice = NS(running=False)
    return среда


class СтраницаКлючаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = PhoneServer(port=_свободный_порт())
        cls.server.start()
        for _ in range(50):
            if getattr(cls.server, "_app", None) is not None:
                break
            time.sleep(0.1)
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        self.server.runtime = _пульт()
        порт = self.server.port
        self.client = TestClient(self.server._app, base_url=f"http://127.0.0.1:{порт}")
        self.вдали = TestClient(self.server._app, base_url=f"http://127.0.0.1:{порт}",
                                client=("192.168.1.50", 5555))

    def test_deepseek_opens_the_own_address(self):
        открыто = []
        with mock.patch("os.startfile", side_effect=lambda url: открыто.append(url)):
            ответ = self.client.post("/api/open/key-page",
                                     json={"provider": "deepseek"})
        self.assertTrue(ответ.json()["ok"], ответ.text)
        self.assertEqual(открыто, [config.KEY_PAGES["deepseek"]])

    def test_unknown_service_is_refused_and_nothing_opens(self):
        открыто = []
        with mock.patch("os.startfile", side_effect=lambda url: открыто.append(url)):
            ответ = self.client.post("/api/open/key-page",
                                     json={"provider": "чужой"})
        self.assertEqual(ответ.status_code, 400)
        self.assertFalse(ответ.json()["ok"])
        self.assertEqual(открыто, [])
        # Локальному серверу страницы ключей нет — это не ошибка пульта.
        self.assertEqual(self.client.post("/api/open/key-page",
                                          json={"provider": "local"}).status_code, 400)

    def test_the_page_has_a_key_page_for_every_cloud_service(self):
        for имя in ("deepseek", "openrouter", "minimax", "openai"):
            self.assertIn(имя, config.KEY_PAGES)
            self.assertTrue(config.KEY_PAGES[имя].startswith("https://"))

    def test_from_the_phone_or_a_stranger_host_it_is_refused(self):
        открыто = []
        with mock.patch("os.startfile", side_effect=lambda url: открыто.append(url)):
            self.assertEqual(self.вдали.post("/api/open/key-page",
                                             json={"provider": "deepseek"}).status_code, 403)
            чужой = TestClient(self.server._app, base_url="http://127.0.0.1:1",
                               headers={"host": "troopa.example"})
            self.assertEqual(чужой.post("/api/open/key-page",
                                       json={"provider": "deepseek"}).status_code, 403)
        self.assertEqual(открыто, [])


if __name__ == "__main__":
    unittest.main()
