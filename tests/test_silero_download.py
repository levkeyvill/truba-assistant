"""Скачивание модели Silero со сроком ожидания и повторами.

28.09 на пробной установке silero качал модель через torch.hub без срока:
соединение оборвалось на 138 МБ из 139, и установщик висел два часа.
Теперь `core.silero_voice` качает сам: кусками с таймаутом, в `.part`, три
попытки. Сеть в тестах подменена; пишем только во временную папку.
"""

import shutil
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from core import silero_voice


class _Ответ:
    def __init__(self, куски, упасть_после=None):
        self.куски = куски
        self.упасть_после = упасть_после

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        for номер, кусок in enumerate(self.куски):
            if self.упасть_после is not None and номер >= self.упасть_после:
                raise httpx.ReadTimeout("обрыв на последнем мегабайте")
            yield кусок

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Клиент:
    def __init__(self, ответы):
        self.ответы = ответы

    def stream(self, method, url):
        return self.ответы.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-silero-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self.файл = self.папка / "v5_5_ru.pt"

    def _подменить(self, ответы):
        return mock.patch.object(httpx, "Client", side_effect=lambda **kw: _Клиент(ответы))

    def test_a_break_is_retried_and_the_file_is_whole(self):
        ответы = [_Ответ([b"aa", b"bb", b"cc"], упасть_после=2), _Ответ([b"aa", b"bb", b"cc"])]
        with self._подменить(ответы):
            silero_voice._download("https://example/v5_5_ru.pt", self.файл)
        self.assertEqual(self.файл.read_bytes(), b"aabbcc")
        self.assertFalse(Path(str(self.файл) + ".part").exists())

    def test_three_breaks_give_up_with_words_not_a_hang(self):
        ответы = [_Ответ([b"a"], упасть_после=0) for _ in range(3)]
        with self._подменить(ответы):
            with self.assertRaises(RuntimeError) as ошибка:
                silero_voice._download("https://example/v5_5_ru.pt", self.файл)
        self.assertIn("не скачалось", str(ошибка.exception))
        self.assertFalse(self.файл.exists())
        self.assertFalse(Path(str(self.файл) + ".part").exists())

    def test_the_read_timeout_is_finite(self):
        self.assertGreater(silero_voice.READ_TIMEOUT, 0)
        self.assertLessEqual(silero_voice.READ_TIMEOUT, 120)

    def test_load_downloads_through_us_before_silero(self):
        # Модель грузится только после нашей загрузки: silero сам не качает.
        порядок = []
        голос = silero_voice.SileroVoice(model="v5_5_ru")
        with mock.patch.object(silero_voice, "ensure_model",
                               side_effect=lambda имя: порядок.append(("наша", имя))), \
                mock.patch("silero.silero_tts",
                           side_effect=lambda **kw: порядок.append(("silero", kw["speaker"]))
                           or (mock.Mock(), None)):
            голос.load()
        self.assertEqual(порядок, [("наша", "v5_5_ru"), ("silero", "v5_5_ru")])

    def test_bundled_model_installs_without_model_server(self):
        import silero.silero as package

        bundle = self.папка / "models" / "silero" / "v5_5_ru.pt"
        bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b"complete official model fixture")
        package_file = self.папка / "site-packages" / "silero" / "silero.py"
        target = package_file.parent / "model" / "v5_5_ru.pt"
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        with mock.patch.object(silero_voice.config, "MODELS_DIR", self.папка / "models"), \
                mock.patch.object(silero_voice, "BUNDLED_MODEL_SHA256", digest), \
                mock.patch.object(package, "__file__", str(package_file)), \
                mock.patch.object(silero_voice, "_models_list",
                                  side_effect=AssertionError("сеть не нужна")), \
                mock.patch.object(silero_voice, "_download",
                                  side_effect=AssertionError("сеть не нужна")):
            silero_voice.ensure_model("v5_5_ru")
        self.assertEqual(target.read_bytes(), bundle.read_bytes())
        self.assertFalse(target.with_suffix(".pt.part").exists())

    def test_corrupted_bundled_model_is_rejected(self):
        bundle = self.папка / "models" / "silero" / "v5_5_ru.pt"
        bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b"incomplete")
        model_dir = self.папка / "site-packages" / "silero" / "model"
        with mock.patch.object(silero_voice.config, "MODELS_DIR", self.папка / "models"):
            with self.assertRaisesRegex(RuntimeError, "архиве повреждён"):
                silero_voice._install_bundled_model("v5_5_ru", model_dir)
        self.assertFalse((model_dir / "v5_5_ru.pt").exists())


if __name__ == "__main__":
    unittest.main()
