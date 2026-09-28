"""Сервер телефона правда поднимается и отвечает.

26 сентября правка вставила два метода посреди функции запуска сервера,
и сам запуск оказался внутри одного из них: все тесты проходили, а пульт
перестал бы открываться. Этот тест запускает настоящий сервер.
"""

import socket
import time
import unittest
import urllib.request
from unittest import mock

from core import weather
from core.phone import PhoneServer


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ServerStartsTests(unittest.TestCase):
    def test_server_answers_version(self):
        port = _free_port()
        # Погода ходит в сеть — в тесте она не нужна.
        with mock.patch.object(weather, "Watcher") as watcher:
            watcher.return_value.start = lambda: None
            server = PhoneServer(port=port)
            server.start()
            answer = None
            for _ in range(50):
                try:
                    answer = urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/version", timeout=1).status
                    break
                except OSError:
                    time.sleep(0.1)
        self.assertEqual(answer, 200)


if __name__ == "__main__":
    unittest.main()
