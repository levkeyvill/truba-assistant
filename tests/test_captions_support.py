"""Подписи одинаковым закладкам на телефоне и кнопка «Поддержать автора».

27.09: две закладки mobalytics (гайды PoE 2) на телефоне были одинаковыми
плитками — подпись получают только закладки с одним сайтом. Кнопка
«Поддержать» открывает только адрес из config.SUPPORT_URL и только с компа.
Без сети и без браузера: `os.startfile` подменён.
"""

import socket
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import launcher, weather
from core.phone import PhoneServer

MOBA_1 = {"id": "b1", "title": "[0.5.5] Hellfire Gemling - Flameblast / Oil Grenade - PoE 2",
          "url": "https://mobalytics.gg/poe-2/builds/hellfire"}
MOBA_2 = {"id": "b2", "title": "[0.5.5] Grenades Gemling League Starter (Level 1) - PoE 2",
          "url": "https://mobalytics.gg/poe-2/builds/grenades"}
OTHER = {"id": "b3", "title": "OpenRouter", "url": "http://openrouter.com/"}


class CaptionTests(unittest.TestCase):
    def test_same_site_gets_short_distinct_captions(self):
        caps = launcher.mark_captions([MOBA_1, MOBA_2, OTHER])
        self.assertEqual(caps, {"b1": "Hellfire", "b2": "Grenades"})

    def test_same_first_word_takes_one_more(self):
        a = {"id": "a", "title": "Гайд Ведьма", "url": "https://site.gg/1"}
        b = {"id": "b", "title": "Гайд Наёмник", "url": "https://www.site.gg/2"}
        self.assertEqual(launcher.mark_captions([a, b]),
                         {"a": "Гайд Ведьма", "b": "Гайд Наёмник"})

    def test_single_bookmark_of_a_site_stays_without_caption(self):
        self.assertEqual(launcher.mark_captions([MOBA_1, OTHER]), {})

    def test_menu_passes_the_caption_to_the_phone(self):
        item = {"id": "firefox", "title": "Firefox", "bookmarks": "firefox"}
        items = launcher.build_menu(item, [MOBA_1, MOBA_2, OTHER])
        by_key = {entry["key"]: entry for entry in items if entry.get("kind") == "bookmark"}
        self.assertEqual(by_key["b1"]["caption"], "Hellfire")
        self.assertEqual(by_key["b2"]["caption"], "Grenades")
        self.assertNotIn("caption", by_key["b3"])

    def test_phone_page_draws_the_caption_as_text(self):
        page = Path(launcher.__file__).resolve().parents[1] / "web" / "index.html"
        html = page.read_text(encoding="utf-8")
        self.assertIn("tag.textContent = item.caption", html)


def _порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class SupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patcher = mock.patch.object(weather, "Watcher")
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.server = PhoneServer(port=_порт())
        cls.server.start()
        cls.server._ready.wait(timeout=5)

    def setUp(self):
        saved = config.SUPPORT_URL
        self.addCleanup(lambda: setattr(config, "SUPPORT_URL", saved))
        self.client = TestClient(self.server._app)
        self.вдали = TestClient(self.server._app, client=("192.168.1.50", 5555))

    def test_without_a_link_it_says_later_and_opens_nothing(self):
        config.SUPPORT_URL = ""
        with mock.patch("core.phone.os.startfile") as started:
            body = self.client.post("/api/support/open").json()
        self.assertFalse(body["ok"])
        self.assertIn("позже", body["error"])
        started.assert_not_called()

    def test_link_opens_in_the_browser(self):
        config.SUPPORT_URL = "https://example.org/donate"
        with mock.patch("core.phone.os.startfile") as started:
            body = self.client.post("/api/support/open").json()
        self.assertTrue(body["ok"])
        started.assert_called_once_with("https://example.org/donate")

    def test_not_a_web_link_is_not_opened(self):
        config.SUPPORT_URL = r"C:\Windows\System32\calc.exe"
        with mock.patch("core.phone.os.startfile") as started:
            body = self.client.post("/api/support/open").json()
        self.assertFalse(body["ok"])
        started.assert_not_called()

    def test_phone_on_the_network_cannot_open_it(self):
        config.SUPPORT_URL = "https://example.org/donate"
        with mock.patch("core.phone.os.startfile") as started:
            answer = self.вдали.post("/api/support/open")
        self.assertNotEqual(answer.status_code, 200)
        started.assert_not_called()

    def test_each_button_opens_only_its_own_link(self):
        config.SUPPORT_URL = "https://example.org/donate"
        with mock.patch.object(config, "AUTHOR_TELEGRAM", "https://t.me/example"), \
                mock.patch("core.phone.os.startfile") as started:
            body = self.client.post("/api/support/open", json={"what": "telegram"}).json()
            self.assertTrue(body["ok"])
            started.assert_called_once_with("https://t.me/example")
            started.reset_mock()
            body = self.client.post("/api/support/open", json={"what": "C:/x"}).json()
            self.assertFalse(body["ok"])
            started.assert_not_called()

    def test_page_data_says_who_and_which_links_exist(self):
        with mock.patch.object(config, "AUTHOR_YOUTUBE", ""):
            body = self.client.get("/api/support").json()
        self.assertEqual(body["name"], config.AUTHOR_NAME)
        self.assertTrue(body["text"])
        self.assertFalse(body["links"]["youtube"])
        self.assertNotIn("http", str(body["links"]))

    def test_pult_has_the_button(self):
        page = Path(launcher.__file__).resolve().parents[1] / "ui" / "web" / "pult.html"
        self.assertIn('id="поддержать"', page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
