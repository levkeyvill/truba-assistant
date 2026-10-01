"""Ярлык «На экран „Домой“» на iPhone: ключ, полный экран, звук (01.10).

Хозяин ставил ярлык с айфона и получал пустую Трубу. Причина в том, что
ярлык — отдельное веб-приложение со своим хранилищем: `localStorage` из
Safari в него не попадает, а Safari открывает адрес запуска из манифеста,
где был написан `/` — ключ терялся в обоих местах. Теперь ключ попадает и
в манифест, и в ссылку манифеста внутри страницы, но только тому, кто его
уже прислал.

Здесь же то, что на айфоне устроено иначе: полного экрана у страниц нет
(ни Safari, ни Firefox), а звук глушится ещё и состоянием 'interrupted'.

Сервер поднимается по-настоящему на свободном порту, без погоды; ключ
лежит во временной папке (tests/test_data_guard.py подменяет путь).
"""

import json
import socket
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import config
from core import phone, weather
from core.phone import PhoneServer

ROOT = Path(config.ROOT)


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ManifestCarriesTheKeyTests(unittest.TestCase):
    """`start_url` с ключом — иначе ярлык открывается непривязанным."""

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
        self.ключ = phone.phone_key()

    def _манифест(self, запрос=""):
        ответ = self.здесь.get("/manifest.json" + запрос)
        self.assertEqual(ответ.status_code, 200, запрос)
        return ответ, json.loads(ответ.text)

    def test_the_right_key_goes_into_the_start_url(self):
        _, данные = self._манифест("?k=" + self.ключ)
        self.assertEqual(данные["start_url"], "/?k=" + self.ключ)

    def test_iphone_gets_a_png_home_screen_icon(self):
        # iPhone не берёт SVG для ярлыка «Домой» и без PNG кладёт снимок
        # страницы (01.10). Ссылка — в странице, файл — на сервере.
        ответ = self.здесь.get("/apple-touch-icon.png")
        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(ответ.headers["content-type"], "image/png")
        self.assertEqual(ответ.content[1:4], b"PNG")
        страница = self.здесь.get("/").text
        self.assertIn('<link rel="apple-touch-icon" href="/apple-touch-icon.png">', страница)

    def test_a_wrong_or_missing_key_gets_a_plain_manifest(self):
        # Не ошибка, а обычный манифест: чужим показывать отказ незачем.
        for запрос in ("", "?k=", "?k=чужой-ключ-подбором"):
            with self.subTest(запрос=запрос):
                _, данные = self._манифест(запрос)
                self.assertEqual(данные["start_url"], "/")

    def test_the_manifest_stays_the_same_file_otherwise(self):
        свой = json.loads((ROOT / "web" / "manifest.json").read_text(encoding="utf-8"))
        _, данные = self._манифест("?k=" + self.ключ)
        for поле in ("name", "short_name", "display", "orientation",
                     "background_color", "theme_color", "icons"):
            self.assertEqual(данные[поле], свой[поле], поле)

    def test_the_key_is_escaped_for_the_address(self):
        # Ключ — urlsafe-строка, но в адрес он идёт закодированным: пробел
        # или `&` в нём иначе разрезали бы запрос.
        with mock.patch.object(phone, "phone_key", return_value="ключ & пробел"):
            ответ = self.здесь.get("/manifest.json", params={"k": "ключ & пробел"})
        self.assertEqual(ответ.status_code, 200)
        self.assertEqual(json.loads(ответ.text)["start_url"],
                         "/?k=%D0%BA%D0%BB%D1%8E%D1%87%20%26%20%D0%BF%D1%80%D0%BE%D0%B1%D0%B5%D0%BB")

    def test_the_phone_page_carries_the_key_into_its_manifest_link(self):
        с_ключом = self.здесь.get("/?k=" + self.ключ)
        self.assertEqual(с_ключом.status_code, 200)
        self.assertIn('<link rel="manifest" href="/manifest.json?k=' + self.ключ + '">',
                      с_ключом.text)
        # Обычный адрес — обычная ссылка: ключа в ней быть не должно.
        без = self.здесь.get("/")
        self.assertEqual(без.status_code, 200)
        self.assertIn('<link rel="manifest" href="/manifest.json">', без.text)
        self.assertNotIn('<link rel="manifest" href="/manifest.json?k=', без.text)

    def test_a_wrong_key_in_the_address_stays_plain(self):
        без = self.здесь.get("/?k=чужой-ключ-подбором")
        self.assertEqual(без.status_code, 200)
        self.assertIn('<link rel="manifest" href="/manifest.json">', без.text)
        self.assertNotIn('<link rel="manifest" href="/manifest.json?k=', без.text)

    def test_the_key_check_is_the_same_as_for_the_socket(self):
        # Проверка ключа одна: `key_matches` и то, чем отбивает `/ws`.
        self.assertTrue(phone.key_matches(phone.phone_key()))
        self.assertFalse(phone.key_matches("чужой-ключ-подбором"))
        self.assertFalse(phone.key_matches(""))
        self.assertFalse(phone.key_matches(None))
class PhonePageSourceTests(unittest.TestCase):
    """Разбор страницы глазами: полный экран и звук на айфоне."""

    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_the_page_puts_the_key_into_the_manifest_link(self):
        self.assertIn("function дописатьКлючВМанифест()", self.index)
        self.assertIn(
            "link.setAttribute('href', '/manifest.json?k=' + encodeURIComponent(ключ))",
            self.index)
        # В макете пульта (`?edit=1`) ничего не ставим.
        self.assertIn("function дописатьКлючВМанифест() {\n  if (EDIT) return;",
                      self.index)

    def test_the_fullscreen_button_is_checked_before_it_is_shown(self):
        # На iPhone у `document.documentElement` нет ни того, ни другого —
        # кнопка молча ничего не делала.
        self.assertIn("function полныйЭкранУмеется()", self.index)
        self.assertIn("el.requestFullscreen || el.webkitRequestFullscreen", self.index)
        self.assertIn("if (полныйЭкранУмеется())", self.index)

    def test_a_standalone_web_app_does_not_show_the_button(self):
        for кусок in ("navigator.standalone", "(display-mode: standalone)",
                      "(display-mode: fullscreen)", "открытоКакПриложение()"):
            self.assertIn(кусок, self.index)

    def test_ios_gets_a_plaque_instead_of_a_useless_button(self):
        self.assertIn("/iPhone|iPad|iPod/i.test(navigator.userAgent || '')", self.index)
        self.assertIn("navigator.standalone !== undefined", self.index)
        self.assertIn("'как на весь экран?'", self.index)
        self.assertIn("Открывать как веб-приложение", self.index)

    def test_sound_is_woken_on_ios_interrupted_too(self):
        # iOS глушит звук ещё и состоянием 'interrupted' — будить надо оба.
        self.assertIn("function разбудитьЗвук()", self.index)
        self.assertIn(
            "audioCtx.state !== 'suspended' && audioCtx.state !== 'interrupted'",
            self.index)
        self.assertIn("navigator.audioSession", self.index)
        self.assertIn("navigator.audioSession.type = 'playback'", self.index)
        # Ни одного места с голым условием по одному состоянию.
        self.assertNotIn("audioCtx.state === 'suspended'", self.index)

    def test_the_unpaired_shortcut_says_what_to_do(self):
        self.assertIn("#offline.крупно", self.index)
        self.assertIn("font-size: 19px", self.index)
        self.assertIn("Этот ярлык не привязан к компьютеру", self.index)
        self.assertIn("Старый ярлык удали.", self.index)

    def test_the_page_script_parses(self):
        import re
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        скрипты = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", self.index, re.S)
        self.assertTrue(скрипты)
        for номер, код in enumerate(скрипты):
            with tempfile.TemporaryDirectory() as папка:
                файл = Path(папка) / "page.js"
                файл.write_text(код, encoding="utf-8")
                итог = subprocess.run([node, "--check", str(файл)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(итог.returncode, 0, f"скрипт {номер}: {итог.stderr[:400]}")


class PultAndReadmeTests(unittest.TestCase):
    """Тот же смысл — в подсказке пульта и в README."""

    @classmethod
    def setUpClass(cls):
        cls.pult = (ROOT / "ui" / "web" / "pult.js").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_iphone_step_is_named_for_both_browsers(self):
        for кусок in ("iPhone (Safari или Firefox)", "Firefox",
                      "Открывать как веб-приложение", "тогда он привязан к компьютеру",
                      "по QR-коду"):
            self.assertIn(кусок, self.pult)
            self.assertIn(кусок, self.readme)

    def test_the_useless_button_is_admitted(self):
        for кусок in ("кнопка «во весь экран»", "ничего не делает"):
            self.assertIn(кусок, self.pult)
            self.assertIn(кусок, self.readme)


if __name__ == "__main__":
    unittest.main()