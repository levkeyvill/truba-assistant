"""Звук мимо голоса — только своими потоками (02.10, падение test5).

`sd.play`, `sd.rec`, `sd.wait`, `sd.playrec` — одни на весь процесс: новый
вызов из другого потока обрывает предыдущий посреди работы. В test5
«Прослушать» на шаге 4 (`sd.play`) наложилось на замер полоски (`sd.rec`), и
Windows закрыла пульт с повреждением памяти (0xc0000374). Повтор на живом
звуке: старый способ — зависание и два аварийных выхода из трёх прогонов,
свои потоки — три из трёх до конца.

Общими функциями пользуется только голос (`core/audio_out.py::Speaker`):
он один, и его `sd.stop()` обрывает ровно его же речь.
"""

import re
import unittest
from pathlib import Path

import config

ROOT = Path(config.ROOT)
ОБЩИЕ = re.compile(r"\bsd\.(play|rec|wait|playrec)\(")


def _код_без_комментариев(текст: str) -> str:
    """Строки кода без комментариев: упоминание в пояснении — не вызов."""
    return "\n".join(строка.split("#", 1)[0] for строка in текст.splitlines())


class OwnStreamsTests(unittest.TestCase):
    def test_only_the_voice_uses_the_shared_functions(self):
        нарушители = []
        for папка in ("core", "ui"):
            for путь in (ROOT / папка).rglob("*.py"):
                if путь.name == "audio_out.py":
                    continue
                код = _код_без_комментариев(путь.read_text(encoding="utf-8"))
                if ОБЩИЕ.search(код):
                    нарушители.append(str(путь.relative_to(ROOT)))
        self.assertEqual(нарушители, [], "общие sd.play/sd.rec роняют пульт")

    def test_helpers_open_their_own_streams(self):
        вывод = (ROOT / "core" / "audio_out.py").read_text(encoding="utf-8")
        ввод = (ROOT / "core" / "audio_in.py").read_text(encoding="utf-8")
        self.assertIn("def play_own(", вывод)
        self.assertIn("sd.OutputStream(", вывод)
        self.assertIn("def record_own(", ввод)
        self.assertIn("sd.InputStream(", ввод)
        # Замер полоски мастера и прослушивание идут через них.
        self.assertIn("record_own(device, channels, rate", ввод)
        среда = (ROOT / "ui" / "web_runtime.py").read_text(encoding="utf-8")
        self.assertIn("play_own(np.asarray(трек, dtype=np.float32)", среда)


if __name__ == "__main__":
    unittest.main()
