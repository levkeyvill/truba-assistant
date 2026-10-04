"""Звук «как в играх»: устройство — из настроек в момент вызова, частота
микрофона — любая.

Выбор устройства должен читаться из настроек при вызове: значения аргумента
по умолчанию и поля класса могут быть вычислены при загрузке модуля.
Микрофон на 44 100 Гц требует пересчёта в 16 кГц.
"""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import audio_in


def _устройства():
    return [
        {"name": "Динамики (Realtek)", "max_input_channels": 0, "max_output_channels": 2,
         "hostapi": 0, "default_samplerate": 48000},
        {"name": "Микрофон (Гарнитура)", "max_input_channels": 1, "max_output_channels": 0,
         "hostapi": 0, "default_samplerate": 44100},
        {"name": "Линейный вход (Scarlett 2i2)", "max_input_channels": 2, "max_output_channels": 0,
         "hostapi": 0, "default_samplerate": 48000},
    ]


class _SD:
    def __init__(self, check_ok=(48000,)):
        self.check_ok = check_ok
        self.default = NS(device=[1, 0])

    def query_devices(self, index=None):
        return _устройства() if index is None else _устройства()[index]

    def query_hostapis(self, index):
        return {"name": "Windows WASAPI"}

    def check_input_settings(self, **kw):
        if kw["samplerate"] not in self.check_ok:
            raise ValueError("не та частота")

    class WasapiSettings:
        def __init__(self, auto_convert=False):
            self.auto_convert = auto_convert


class ВыборИзНастроекTests(unittest.TestCase):
    def setUp(self):
        было = (config.MIC_NAME, config.MIC_CHANNEL, config.SPEAKER_NAME)
        self.addCleanup(self._вернуть, было)

    def _вернуть(self, было):
        config.MIC_NAME, config.MIC_CHANNEL, config.SPEAKER_NAME = было

    def test_the_mic_chosen_later_is_found_without_arguments(self):
        config.MIC_NAME = "Scarlett"
        with mock.patch.dict("sys.modules", {"sounddevice": _SD()}):
            индекс, описание = config.find_input_device()
        self.assertEqual(индекс, 2)
        self.assertIn("Scarlett", описание)

    def test_empty_name_is_the_system_default(self):
        config.MIC_NAME = ""
        with mock.patch.dict("sys.modules", {"sounddevice": _SD()}):
            индекс, _ = config.find_input_device()
        self.assertEqual(индекс, 1)

    def test_the_channel_comes_from_settings_at_start(self):
        config.MIC_CHANNEL = 1
        слушатель = audio_in.Listener.__new__(audio_in.Listener)
        self.assertIsNone(audio_in.Listener.channel)
        # Класс больше не помнит вход с момента загрузки модуля.
        self.assertNotEqual(audio_in.Listener.channel, 0)


class ЧастотаTests(unittest.TestCase):
    def test_44100_asks_windows_for_48000_with_auto_convert(self):
        sd = _SD(check_ok=(48000,))
        слушатель = audio_in.Listener.__new__(audio_in.Listener)
        слушатель.device = 1
        слушатель.channels = 1
        with mock.patch.object(audio_in, "sd", sd):
            частота, extra = слушатель._pick_rate(_устройства()[1])
        self.assertEqual(частота, 48000)
        self.assertTrue(extra.auto_convert)

    def test_16000_is_the_last_resort(self):
        sd = _SD(check_ok=(16000,))
        слушатель = audio_in.Listener.__new__(audio_in.Listener)
        слушатель.device = 1
        слушатель.channels = 1
        with mock.patch.object(audio_in, "sd", sd):
            частота, _ = слушатель._pick_rate(_устройства()[1])
        self.assertEqual(частота, 16000)

    def test_nothing_fits_is_said_in_words(self):
        sd = _SD(check_ok=())
        слушатель = audio_in.Listener.__new__(audio_in.Listener)
        слушатель.device = 1
        слушатель.channels = 1
        with mock.patch.object(audio_in, "sd", sd):
            with self.assertRaises(RuntimeError) as ошибка:
                слушатель._pick_rate(_устройства()[1])
        self.assertIn("Гарнитура", str(ошибка.exception))


class СписокTests(unittest.TestCase):
    def test_inputs_and_outputs_do_not_mix(self):
        with mock.patch.dict("sys.modules", {"sounddevice": _SD()}):
            входы = config.list_inputs()
            выходы = config.list_outputs()
        self.assertEqual([д["name"] for д in входы],
                         ["Микрофон (Гарнитура)", "Линейный вход (Scarlett 2i2)"])
        self.assertEqual([д["name"] for д in выходы], ["Динамики (Realtek)"])
        self.assertTrue(входы[0]["default"])


# --- Живой список Windows: одно устройство в четырёх API ----------------------
#
# Windows показывает одно устройство в MME, DirectSound, WASAPI и WDM-KS;
# MME обрезает имя до 31 знака, поэтому дубликаты трудно различить.
# Список берётся только из WASAPI — там имена полные, ровно как в «Параметрах
# звука», и Windows их не дублирует.


def _четыре_api(имя, входы=0, выходы=0):
    """Одно устройство в MME, DirectSound, WASAPI и WDM-KS."""
    return [
        {"name": f"{имя} (Virtual Audio Cable)", "hostapi": 0,
         "max_input_channels": входы, "max_output_channels": выходы,
         "default_samplerate": 44100.0},
        {"name": имя[:31], "hostapi": 1, "max_input_channels": входы,
         "max_output_channels": выходы, "default_samplerate": 48000.0},
        {"name": имя, "hostapi": 2, "max_input_channels": входы,
         "max_output_channels": выходы, "default_samplerate": 48000.0},
        {"name": f"{имя} (WDM)", "hostapi": 3, "max_input_channels": входы,
         "max_output_channels": выходы, "default_samplerate": 44100.0},
    ]


ЖИВЫЕ = (
    [{"name": "Microsoft Sound Mapper", "hostapi": 0, "max_input_channels": 1,
      "max_output_channels": 0, "default_samplerate": 44100.0}]
    + _четыре_api("Яндекс музыка (Virtual Audio Cable)", входы=1)
    + _четыре_api("Микрофон (2- й Mic Port) Scarlett 2i2", входы=2)
    + _четыре_api("Динамики (Realtek High Definition Audio)", выходы=2)
)

API = [
    {"name": "MME", "default_input_device": 0, "default_output_device": None},
    {"name": "DirectSound", "default_input_device": None, "default_output_device": None},
    # По умолчанию Windows называет именно WASAPI-устройства: у микрофона
    # индекс зависит от набора API и порядка устройств.
    {"name": "Windows WASAPI", "default_input_device": 7, "default_output_device": 11},
    {"name": "Windows WDM-KS", "default_input_device": None,
     "default_output_device": None},
]


def _sd(устройства=ЖИВЫЕ, api=API, hostapi=2, умолчание=None):
    """Поддельный sounddevice с несколькими API одного устройства."""
    sd = mock.MagicMock()
    sd.query_devices = lambda *a, **k: (
        устройства[a[0]] if a and isinstance(a[0], int) else устройства)
    sd.query_hostapis = lambda *a, **k: (api if not a else api[a[0]])
    sd.default = NS(device=умолчание or [-1, -1], hostapi=hostapi)
    return sd


# --- Тесты ниже в этом файле ---


class ЖивойСписокTests(unittest.TestCase):
    """Список из одного API: как в «Параметрах звука» Windows."""

    def _список(self, **kwargs):
        with mock.patch.dict("sys.modules", {"sounddevice": _sd(**kwargs)}):
            return config.list_inputs()

    def test_the_same_device_in_four_apis_is_one_line(self):
        имена = [у["name"] for у in self._список()]
        self.assertEqual(имена, ["Яндекс музыка (Virtual Audio Cable)",
                                 "Микрофон (2- й Mic Port) Scarlett 2i2"])
        # Обрезанное имя MME в список не попало.
        self.assertNotIn("Microsoft Sound Mapper", имена)

    def test_the_list_is_taken_from_the_preferred_api(self):
        for у in self._список():
            self.assertEqual(у["api"], "Windows WASAPI")
            self.assertEqual(у["rate"], 48000)

    def test_exactly_one_device_is_the_default(self):
        отмеченные = [у["name"] for у in self._список() if у["default"]]
        self.assertEqual(отмеченные, ["Микрофон (2- й Mic Port) Scarlett 2i2"])

    def test_no_api_says_anything_and_nobody_is_default(self):
        # Система молчит (всё `-1`) — молчать должны и мы: правды нет.
        пусто = [{"name": f"Mic {n}", "hostapi": 0, "max_input_channels": 1,
                  "max_output_channels": 0, "default_samplerate": 48000.0}
                 for n in range(3)]
        with mock.patch.dict("sys.modules", {"sounddevice": _sd(
                устройства=пусто, api=[{"name": "MME"}], hostapi=None)}):
            входы = config.list_inputs()
        self.assertEqual(len(входы), 3)
        self.assertFalse(any(у["default"] for у in входы))

    def test_the_default_comes_from_the_api_not_from_sd_default(self):
        # При `sd.default.device == -1` имя берётся через звуковой API Windows.
        with mock.patch.dict("sys.modules", {"sounddevice": _sd()}):
            индекс, описание = config.find_input_device("")
        self.assertEqual(индекс, 7)
        self.assertIn("Scarlett 2i2", описание)

    def test_outputs_take_their_default_from_the_api_too(self):
        with mock.patch.dict("sys.modules", {"sounddevice": _sd()}):
            выходы = config.list_outputs()
        self.assertEqual([у["name"] for у in выходы],
                         ["Динамики (Realtek High Definition Audio)"])
        self.assertEqual([у["name"] for у in выходы if у["default"]],
                         ["Динамики (Realtek High Definition Audio)"])


class БезПредпочтительногоApiTests(unittest.TestCase):
    """Без WASAPI список собирается из доступных API без дублей."""

    УСТРОЙСТВА = [
        {"name": "Микрофон (Гарнитура)", "hostapi": 0, "max_input_channels": 1,
         "max_output_channels": 0, "default_samplerate": 44100.0},
        {"name": "Микрофон (Гарнитура)", "hostapi": 1, "max_input_channels": 1,
         "max_output_channels": 0, "default_samplerate": 48000.0},
    ]
    API = [{"name": "MME", "default_input_device": 0, "default_output_device": None},
           {"name": "DirectSound", "default_input_device": 1,
            "default_output_device": None}]

    def test_both_apis_stay_in_the_list_but_without_duplicates(self):
        sd = _sd(устройства=self.УСТРОЙСТВА, api=self.API, hostapi=0)
        with mock.patch.dict("sys.modules", {"sounddevice": sd}):
            входы = config.list_inputs()
        self.assertEqual([у["name"] for у in входы], ["Микрофон (Гарнитура)"])
        # Без WASAPI и равных кандидатов остаётся первый по счёту.
        self.assertEqual([у["index"] for у in входы], [0])
        self.assertTrue(входы[0]["default"])

    def test_the_system_api_says_which_device_is_default(self):
        # WASAPI нет — по умолчанию спрашиваем у того API, которым система
        # сама пользуется (`sd.default.hostapi`), и берём его ответ.
        sd = _sd(устройства=self.УСТРОЙСТВА, api=self.API, hostapi=1)
        with mock.patch.dict("sys.modules", {"sounddevice": sd}):
            индекс, _ = config.find_input_device("")
        self.assertEqual(индекс, 1)


if __name__ == "__main__":
    unittest.main()
