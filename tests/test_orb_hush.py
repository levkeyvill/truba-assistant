"""Касание круга на телефоне — только «замолчи».

27.09 хозяин: касание выключало ей слух, и потом приходилось звать по имени
заново, хотя окно разговора ещё не кончилось. Теперь касание шлёт `hush`:
речь и недописанный ответ обрываются, режим слуха и разговор не трогаются.
"""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from ui.web_runtime import WebRuntime

PHONE = Path(config.ROOT) / "web" / "index.html"


class OrbHushTests(unittest.TestCase):
    def _pult(self):
        pult = object.__new__(WebRuntime)
        pult.voice = NS(shut_up=mock.Mock(), _close_conversation=mock.Mock(),
                        running=True, ready=True, in_conversation=True)
        pult._remember = lambda kind, payload: None
        pult.voice_state = lambda: {}
        pult.voice_mode = mock.Mock()
        return pult

    def test_hush_only_silences(self):
        pult = self._pult()
        before = config.LISTEN_MODE
        pult.handle_event("hush", {"type": "hush"})
        pult.voice.shut_up.assert_called_once()
        # «Мне хватит»: разговор закрыт, дальше — по имени.
        pult.voice._close_conversation.assert_called_once()
        pult.voice_mode.assert_not_called()
        self.assertEqual(config.LISTEN_MODE, before)

    def test_the_phone_tap_sends_hush_not_mode_off(self):
        page = PHONE.read_text(encoding="utf-8")
        # Тело обработчика отрыва пальца от круга — до конца обработчика.
        tap = page.split("orb.addEventListener('pointerup'")[1].split("\n});")[0]
        self.assertIn("send({ type: 'hush' })", tap)
        self.assertNotIn("'off'", tap)
        self.assertIsNone(re.search(r"type:\s*'mode'", tap))


if __name__ == "__main__":
    unittest.main()
