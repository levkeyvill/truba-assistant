"""Железо компьютера: что определилось и что ему по силам.

Настоящего железа здесь нет ничего: `subprocess.run` (nvidia-smi), `psutil` и
`winreg` подменены. Строки `nvidia-smi` — ровно такие, какие он отдаёт
27.09.2026, плюс варианты «программы нет», «драйвер старый» и «карты две».
"""

import unittest
from unittest import mock

from core import hardware

# Настоящие строки вывода nvidia-smi на машине хозяина.
СМОТРИ_5070 = "NVIDIA GeForce RTX 5070 Ti, 16303, 616.56, 12.0"
СМОТРИ_3060 = "NVIDIA GeForce RTX 3060, 12288, 572.10, 8.6"
СМОТРИ_ДВЕ = СМОТРИ_5070 + "\n" + СМОТРИ_3060


class Итог:
    """То, что возвращает подменённый `subprocess.run`."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _смоти(вывод, код=0):
    """Подмена `subprocess.run`: отдаёт заданную строку nvidia-smi."""
    return mock.patch.object(hardware.subprocess, "run",
                             return_value=Итог(вывод, код))


def _psutil(ядра=8, потоки=16, гигабайты=32.0):
    """Подмена psutil: ядра, потоки и объём памяти."""
    модуль = mock.MagicMock()
    модуль.cpu_count = lambda logical=True: потоки if logical else ядра
    модуль.virtual_memory = lambda: mock.MagicMock(
        total=int(гигабайты * 1024**3))
    return mock.patch.dict("sys.modules", {"psutil": модуль})


def _железо(карты=(), **поле):
    """Готовый `hw` для `recommend` — без похода в систему."""
    hw = {
        "gpus": list(карты),
        "cpu": {"name": "", "cores": 8, "threads": 16},
        "ram_gb": 32.0,
        "disk_free_gb": 300.0,
        "windows": {"release": "10", "version": "", "build": "22631"},
    }
    hw.update(поле)
    return hw


def _карта(имя, гигабайты, драйвер, вычисления):
    return {"name": имя, "vram_gb": гигабайты, "driver": драйвер,
            "compute_cap": вычисления}


class CudaIndexTests(unittest.TestCase):
    """Какая сборка torch подходит драйверу. Границы — главное."""

    def test_boundaries(self):
        self.assertEqual(hardware.cuda_index("559"), None)
        self.assertEqual(hardware.cuda_index("560.00"), "cu126")
        self.assertEqual(hardware.cuda_index("569.99"), "cu126")
        self.assertEqual(hardware.cuda_index("570.10"), "cu128")
        self.assertEqual(hardware.cuda_index("579.99"), "cu128")
        self.assertEqual(hardware.cuda_index("580.0"), "cu130")
        self.assertEqual(hardware.cuda_index("616.56"), "cu130")

    def test_musor_is_not_taken_for_a_number(self):
        for значение in ("", None, "N/A", "неизвестно"):
            self.assertIsNone(hardware.cuda_index(значение), значение)

    def test_family_tail_does_not_break_it(self):
        # Драйвер приходит с хвостом вида `531.14 (Family)`.
        self.assertEqual(hardware.cuda_index("580.12 (Family)"), "cu130")
        self.assertIsNone(hardware.cuda_index("531.14 (Family)"))


class GpuTests(unittest.TestCase):
    """Разбор вывода nvidia-smi."""

    def test_one_card_is_parsed(self):
        with _смоти(СМОТРИ_5070):
            карты = hardware.gpus()
        self.assertEqual(len(карты), 1)
        self.assertEqual(карты[0]["name"], "NVIDIA GeForce RTX 5070 Ti")
        self.assertEqual(карты[0]["vram_gb"], 15.9)
        self.assertEqual(карты[0]["driver"], "616.56")
        self.assertEqual(карты[0]["compute_cap"], 12.0)

    def test_two_cards_keep_their_order(self):
        with _смоти(СМОТРИ_ДВЕ):
            карты = hardware.gpus()
        self.assertEqual([к["name"] for к in карты],
                         ["NVIDIA GeForce RTX 5070 Ti",
                          "NVIDIA GeForce RTX 3060"])
        self.assertEqual(карты[1]["vram_gb"], 12.0)

    def test_no_nvidia_smi_is_no_cards(self):
        with mock.patch.object(hardware.subprocess, "run",
                               side_effect=FileNotFoundError("нет nvidia-smi")):
            self.assertEqual(hardware.gpus(), [])

    def test_driver_error_is_no_cards(self):
        with _смоти("Failed to initialize NVML", код=1):
            self.assertEqual(hardware.gpus(), [])

    def test_hung_driver_does_not_hang_us(self):
        with mock.patch.object(hardware.subprocess, "run",
                               side_effect=hardware.subprocess.TimeoutExpired(
                                   "nvidia-smi", 5)):
            self.assertEqual(hardware.gpus(), [])

    def test_not_supported_compute_cap_is_none(self):
        # Старая карта отдаёт `[N/A]`, и совет по ней не должен падать.
        with _смоти("NVIDIA GeForce GT 1030, 2048, 551.23, [N/A]"):
            карты = hardware.gpus()
        self.assertIsNone(карты[0]["compute_cap"])
        self.assertEqual(карты[0]["vram_gb"], 2.0)


class DetectTests(unittest.TestCase):
    """`detect` не должен ни падать, ни выдумывать."""

    def test_nothing_known_is_none_not_a_crash(self):
        # psutil тоже может не ответить (необычная сборка Windows) — тогда
        # поля пустые, но `detect` обязан вернуться, а не упасть: его зовёт
        # мастер у кого угодно.
        сломанный = mock.MagicMock()
        сломанный.cpu_count.side_effect = OSError("нет psutil")
        сломанный.virtual_memory.side_effect = OSError("нет psutil")
        with _смоти(""), mock.patch.dict("sys.modules", {"psutil": сломанный}), \
                mock.patch.object(hardware, "_cpu_name", return_value=""), \
                mock.patch.object(hardware, "_disk_free_gb",
                                  return_value=None):
            hw = hardware.detect()
        self.assertEqual(hw["gpus"], [])
        self.assertIsNone(hw["ram_gb"])
        self.assertIsNone(hw["disk_free_gb"])
        self.assertIsNone(hw["cpu"]["threads"])
        self.assertIsNone(hw["cpu"]["cores"])

    def test_cpu_and_ram_are_read(self):
        with _смоти(СМОТРИ_5070), _psutil(ядра=8, потоки=16, гигабайты=32.0), \
                mock.patch.object(hardware, "_cpu_name",
                                  return_value="AMD Ryzen 7 9800X3D"), \
                mock.patch.object(hardware, "_disk_free_gb",
                                  return_value=412.5):
            hw = hardware.detect()
        self.assertEqual(hw["cpu"]["name"], "AMD Ryzen 7 9800X3D")
        self.assertEqual(hw["cpu"]["cores"], 8)
        self.assertEqual(hw["cpu"]["threads"], 16)
        self.assertEqual(hw["ram_gb"], 32.0)
        self.assertEqual(hw["disk_free_gb"], 412.5)
        self.assertEqual(len(hw["gpus"]), 1)

    def test_torch_version_reads_metadata_and_not_the_package(self):
        # torch импортировать нельзя: он весит под гигабайт и грузится
        # секунд двадцать. Версия сборки лежит прямо в имени пакета.
        with mock.patch.object(hardware.importlib.metadata, "version",
                               return_value="2.10.0+cu130"):
            self.assertTrue(hardware.torch_cuda())
        with mock.patch.object(hardware.importlib.metadata, "version",
                               return_value="2.10.0+cpu"):
            self.assertFalse(hardware.torch_cuda())
        with mock.patch.object(
                hardware.importlib.metadata, "version",
                side_effect=hardware.importlib.metadata.PackageNotFoundError):
            self.assertFalse(hardware.torch_cuda())

    def test_voices_installed_needs_all_three(self):
        for имя in ("transformers", "f5_tts", "vocos"):
            with mock.patch.object(hardware.importlib.util, "find_spec",
                                   return_value=object()):
                self.assertTrue(hardware.voices_installed(), имя)
            with mock.patch.object(hardware.importlib.util, "find_spec",
                                   return_value=None):
                self.assertFalse(hardware.voices_installed(), имя)


class RecommendTests(unittest.TestCase):
    """Совет по железу: что выбрать и что сказать человеку."""

    def test_5070_ti_gets_higgs(self):
        hw = _железо([_карта("NVIDIA GeForce RTX 5070 Ti", 15.9, "616.56", 12.0)])
        ответ = hardware.recommend(hw)
        self.assertEqual(ответ["voice"], "higgs")
        self.assertIn("RTX 5070 Ti", ответ["voice_why"])
        self.assertIn("Higgs", ответ["voice_why"])

    def test_3060_gets_espeech(self):
        # Вычисления 8.6 — ниже порога Higgs, но памяти хватает на ESpeech.
        hw = _железо([_карта("NVIDIA GeForce RTX 3060", 12.0, "572.10", 8.6)])
        ответ = hardware.recommend(hw)
        self.assertEqual(ответ["voice"], "espeech")
        self.assertIn("ESpeech", ответ["voice_why"])

    def test_no_nvidia_gets_silero(self):
        ответ = hardware.recommend(_железо([]))
        self.assertEqual(ответ["voice"], "silero")
        self.assertIn("NVIDIA", ответ["voice_why"])

    def test_old_driver_gets_silero_and_says_so(self):
        hw = _железо([_карта("NVIDIA GeForce RTX 3060", 12.0, "531.14", 8.6)])
        ответ = hardware.recommend(hw)
        self.assertEqual(ответ["voice"], "silero")
        self.assertIn("драйвер", ответ["voice_why"])
        self.assertIn("531", ответ["voice_why"])

    def test_little_vram_stays_silero(self):
        # Памяти 3 ГБ: на такую карту ни Higgs, ни ESpeech не помещаются,
        # и совет обязан оставить Silero, а не обещать качественный голос.
        hw = _железо([_карта("NVIDIA GeForce GTX 1650", 3.0, "580.0", 7.5)])
        self.assertEqual(hardware.recommend(hw)["voice"], "silero")

    def test_full_stt_on_a_big_cpu(self):
        hw = _железо([], ram_gb=32.0,
                     cpu={"name": "", "cores": 8, "threads": 16})
        ответ = hardware.recommend(hw)
        self.assertEqual(ответ["stt_quantization"], "none")
        self.assertIn("16 потоков", ответ["stt_why"])

    def test_small_cpu_gets_int8(self):
        hw = _железо([], ram_gb=8.0,
                     cpu={"name": "", "cores": 2, "threads": 4})
        self.assertEqual(hardware.recommend(hw)["stt_quantization"], "int8")

    def test_many_threads_but_little_ram_is_int8(self):
        # Одного из двух условий мало: иначе на 4 ГБ порекомендуем полную
        # точность, а она там не влезет.
        hw = _железо([], ram_gb=8.0,
                     cpu={"name": "", "cores": 8, "threads": 16})
        self.assertEqual(hardware.recommend(hw)["stt_quantization"], "int8")


class WarningTests(unittest.TestCase):
    """О чём предупредить."""

    def test_quiet_computer_has_no_warnings(self):
        self.assertEqual(hardware.warnings(_железо([])), [])

    def test_little_disk_ram_and_old_windows(self):
        hw = _железо([], disk_free_gb=8.0, ram_gb=4.0,
                     windows={"release": "10", "version": "", "build": "17763"})
        текст = " ".join(hardware.warnings(hw))
        self.assertIn("места", текст)
        self.assertIn("памяти", текст)
        self.assertIn("Windows", текст)

    def test_unknown_numbers_are_not_warnings(self):
        # Не определилось — это не «мало». Иначе на любом нестандартном
        # компьютере мастер ругался бы на то, чего не знает.
        hw = _железо([], disk_free_gb=None, ram_gb=None)
        self.assertEqual(hardware.warnings(hw), [])


if __name__ == "__main__":
    unittest.main()
