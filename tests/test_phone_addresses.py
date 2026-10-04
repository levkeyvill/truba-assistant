"""Адреса для телефона — только доступные сетевые карты.

Туннели VPN и выключенные карты телефону недоступны. Список без них и без
«адрес не выдан» (169.254). Сеть и psutil здесь подменены.
"""

import socket
import types
import unittest
from unittest import mock

from core import phone


def _адрес(ip, маска):
    return types.SimpleNamespace(family=socket.AF_INET, address=ip, netmask=маска)


def _psutil(карты):
    """карты: имя → (поднята, [(ip, маска)])."""
    return types.SimpleNamespace(
        net_if_addrs=lambda: {имя: [_адрес(*a) for a in адреса]
                              for имя, (_up, адреса) in карты.items()},
        net_if_stats=lambda: {имя: types.SimpleNamespace(isup=up)
                              for имя, (up, _a) in карты.items()},
    )


КАК_У_ХОЗЯИНА = {
    "Ethernet 2": (True, [("192.168.1.50", "255.255.255.0")]),
    "Ethernet": (False, [("169.254.10.20", "255.255.0.0")]),
    "happ-default-tun": (True, [("10.8.0.1", "255.255.255.252")]),
    "Loopback Pseudo-Interface 1": (True, [("127.0.0.1", "255.0.0.0")]),
}


class PhoneAddressTests(unittest.TestCase):
    def test_vpn_tunnel_and_dead_cards_are_dropped(self):
        with mock.patch.dict("sys.modules", {"psutil": _psutil(КАК_У_ХОЗЯИНА)}):
            self.assertEqual(phone._адреса_для_телефона(), {"192.168.1.50"})

    def test_the_list_for_the_phone_has_only_the_home_address(self):
        найдено = ("host", [], ["192.168.1.50", "10.8.0.1", "169.254.10.20"])
        with mock.patch.dict("sys.modules", {"psutil": _psutil(КАК_У_ХОЗЯИНА)}), \
                mock.patch.object(socket, "gethostbyname_ex", return_value=найдено), \
                mock.patch.object(socket, "getaddrinfo", return_value=[]):
            self.assertEqual(phone.local_addresses(), ["192.168.1.50"])

    def test_tunnel_is_dropped_by_its_tiny_network_even_with_a_plain_name(self):
        карты = {"Сеть 3": (True, [("10.8.0.2", "255.255.255.252")]),
                 "Wi-Fi": (True, [("192.168.1.5", "255.255.255.0")])}
        with mock.patch.dict("sys.modules", {"psutil": _psutil(карты)}):
            self.assertEqual(phone._адреса_для_телефона(), {"192.168.1.5"})

    def test_without_psutil_the_old_list_stays(self):
        # Не вышло разобрать карты — лучше прежний список, чем пустой.
        найдено = ("host", [], ["192.168.1.50", "10.8.0.1"])
        with mock.patch.object(phone, "_адреса_для_телефона", return_value=None), \
                mock.patch.object(socket, "gethostbyname_ex", return_value=найдено), \
                mock.patch.object(socket, "getaddrinfo", return_value=[]):
            self.assertEqual(phone.local_addresses(), ["192.168.1.50", "10.8.0.1"])

    def test_nothing_left_after_filtering_keeps_the_old_list(self):
        найдено = ("host", [], ["10.8.0.1"])
        with mock.patch.object(phone, "_адреса_для_телефона", return_value=set()), \
                mock.patch.object(socket, "gethostbyname_ex", return_value=найдено), \
                mock.patch.object(socket, "getaddrinfo", return_value=[]):
            self.assertEqual(phone.local_addresses(), ["10.8.0.1"])


if __name__ == "__main__":
    unittest.main()
