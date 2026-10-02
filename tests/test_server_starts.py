"""Сервер телефона правда поднимается и отвечает.

26 сентября правка вставила два метода посреди функции запуска сервера,
и сам запуск оказался внутри одного из них: все тесты проходили, а пульт
перестал бы открываться. Этот тест запускает настоящий сервер.
"""

import socket
import struct
import threading
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

    def test_server_survives_reset_connections(self):
        """02.10: окно пульта «127.0.0.1 отказано в подключении», процесс жив.

        На Windows цикл Proactor закрывал слушающий порт при первом же
        подключении, оборванном в момент приёма (на замере — в первой сотне).
        Здесь 400 подключений рвутся сразу (RST), сервер должен отвечать дальше.
        """
        port = _free_port()
        with mock.patch.object(weather, "Watcher") as watcher:
            watcher.return_value.start = lambda: None
            server = PhoneServer(port=port)
            server.start()
            self.assertEqual(self._version(port), 200)

            def рвать():
                for _ in range(100):
                    s = socket.socket()
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                 struct.pack("ii", 1, 0))
                    try:
                        s.connect(("127.0.0.1", port))
                    except OSError:
                        pass
                    s.close()

            потоки = [threading.Thread(target=рвать) for _ in range(4)]
            for t in потоки:
                t.start()
            for t in потоки:
                t.join(timeout=60)
            self.assertEqual(self._version(port), 200)

    @staticmethod
    def _version(port):
        for _ in range(50):
            try:
                return urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/version", timeout=1).status
            except OSError:
                time.sleep(0.1)
        return None


if __name__ == "__main__":
    unittest.main()
