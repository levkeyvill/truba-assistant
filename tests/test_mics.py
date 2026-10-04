"""Микрофон и колонки: любой компьютер, а не компьютер автора.

Звуковых карт в тестах нет: `sounddevice` подменён списком устройств, каким
Windows его отдаёт. Проверяем ровно то, что ломалось у другого человека:
пустое имя и несуществующее имя должны вести к системному микрофону, а не
к отказу, а список для пульта не должен показывать колонки и повторяться.
"""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

import config


def _устройство(имя, api, входы=1, выходы=0, частота=48000.0):
    """Запись устройства в том виде, в каком её отдаёт sounddevice."""
    return {"name": имя, "hostapi": api, "max_input_channels": входы,
            "max_output_channels": выходы, "default_samplerate": частота}


# Список как его видит Windows: одна и та же карта в трёх API, плюс выходы.
УСТРОЙСТВА = [
    _устройство("Микрофон (2- й Mic Port)", 0, входы=2),   # MME
    _устройство("Speakers (Realtek High Definition Audio)", 0, выходы=2),
    _устройство("Микрофон (2- й Mic Port)", 1, входы=2),   # WASAPI
    _устройство("Микрофон (Realtek High Definition Audio)", 1, входы=1),
    _устройство("Микрофон (Realtek High Definition Audio)", 2, входы=1),
    _устройство("Speakers (Realtek High Definition Audio)", 1, выходы=2),
    _устройство("Headset Microphone (Строительный микрофон)", 1, входы=1),
]
# Устройства, у которых нет НИ входа, НИ выхода: в списках их быть не должно.
ТОЛЬКО_ВЫХОД = [_устройство("Колонки (USB)", 1, входы=0, выходы=2)]
ТОЛЬКО_ВХОД = [_устройство("Микрофон (USB)", 1, входы=1, выходы=0)]

# Звуковые API в том порядке, в каком их нумерует sounddevice.
API = [{"name": "MME"}, {"name": "WASAPI"}, {"name": "DirectSound"}]
# По умолчанию Windows отдаёт: вход — 3 (микрофон Realtek), выход — 5.
ПО_УМОЛЧАНИЮ = [3, 5]


def _sd(*, default=ПО_УМОЛЧАНИЮ, устройства=УСТРОЙСТВА):
    """Поддельный sounddevice с заданным списком устройств."""
    sd = mock.MagicMock()
    sd.query_devices = lambda *a, **k: (
        устройства[a[0]] if a and isinstance(a[0], int) else устройства)
    sd.query_hostapis = lambda idx: API[idx]
    sd.default = NS(device=list(default))
    return sd


def _подменить(sd):
    """Подмена sounddevice в `sys.modules` — импортируется он лениво."""
    return mock.patch.dict("sys.modules", {"sounddevice": sd})


class FindInputTests(unittest.TestCase):
    """`find_input_device`: имя, пустое имя и имя, которого нет."""

    def test_empty_name_is_the_system_microphone(self):
        # Так стоит у каждого, кто не вбивал ничего: голос обязан работать.
        sd = _sd()
        with _подменить(sd):
            индекс, описание = config.find_input_device("")
        self.assertEqual(индекс, 3)
        self.assertIn("Realtek", описание)

    def test_name_which_is_not_there_is_the_system_microphone(self):
        # Устройство отключили или переименовали — падать нельзя.
        sd = _sd()
        with _подменить(sd):
            индекс, _ = config.find_input_device("Рубикон")
        self.assertEqual(индекс, 3)

    def test_found_by_name(self):
        sd = _sd()
        with _подменить(sd):
            индекс, описание = config.find_input_device("Строительный")
        self.assertEqual(индекс, 6)
        self.assertIn("Строительный", описание)

    def test_wasapi_wins_over_other_apis(self):
        # Одно и то же имя в MME и в WASAPI — берём WASAPI: у него родные
        # 48 кГц и низкая задержка, а MME пересобирает звук заново.
        sd = _sd()
        with _подменить(sd):
            индекс, описание = config.find_input_device("2- й Mic Port")
        self.assertEqual(индекс, 2)
        self.assertIn("WASAPI", описание)

    def test_no_input_at_all_is_an_honest_refusal(self):
        # Колонки умеют только играть. Записать с них нечем, и врать, что
        # микрофон есть, нельзя — голос честно скажет, что не найден.
        sd = _sd(default=[-1, -1], устройства=ТОЛЬКО_ВЫХОД)
        with _подменить(sd):
            self.assertEqual(config.find_input_device(""), (None, None))


class ListInputTests(unittest.TestCase):
    """`list_inputs`: для пульта, без колонок и без дублей."""

    def _список(self, **kwargs):
        with _подменить(_sd(**kwargs)):
            return config.list_inputs()

    def test_outputs_are_not_in_the_list(self):
        # Иначе в выборе микрофона была бы строка «Динамики», из которой
        # ничего записать нельзя.
        with _подменить(_sd(устройства=ТОЛЬКО_ВЫХОД + ТОЛЬКО_ВХОД)):
            входы = config.list_inputs()
        self.assertEqual([у["name"] for у in входы], ["Микрофон (USB)"])

    def test_only_outputs_leave_the_input_list_empty(self):
        with _подменить(_sd(устройства=ТОЛЬКО_ВЫХОД)):
            self.assertEqual(config.list_inputs(), [])

    def test_same_name_in_two_apis_is_one_line(self):
        # Windows показывает одну карту в трёх API. В списке это одна строка —
        # иначе выбор микрофона превратился бы в три одинаковых пункта.
        имена = [у["name"] for у in self._список()]
        self.assertEqual(len(имена), len(set(имена)))

    def test_wasapi_survives_the_deduplication(self):
        найдено = {у["name"]: у for у in self._список()}
        порт = найдено["Микрофон (2- й Mic Port)"]
        self.assertEqual(порт["api"], "WASAPI")
        self.assertEqual(порт["index"], 2)

    def test_system_microphone_is_marked_as_default(self):
        найдено = {у["name"]: у for у in self._список()}
        обычный = найдено["Микрофон (Realtek High Definition Audio)"]
        self.assertTrue(обычный["default"])
        self.assertFalse(
            найдено["Headset Microphone (Строительный микрофон)"]["default"])

    def test_channels_and_rate_are_there(self):
        порт = next(у for у in self._список()
                    if у["name"] == "Микрофон (2- й Mic Port)")
        self.assertEqual(порт["channels"], 2)
        self.assertEqual(порт["rate"], 48000)

    def test_nothing_to_choose_from_is_an_empty_list(self):
        self.assertEqual(self._список(устройства=[]), [])


class OutputTests(unittest.TestCase):
    """Колонки: тот же поиск и тот же список, только по выходу."""

    def _список(self, **kwargs):
        with _подменить(_sd(**kwargs)):
            return config.list_outputs()

    def test_only_outputs(self):
        for устройство in self._список():
            self.assertIn("Speakers", устройство["name"])

    def test_no_duplicates(self):
        имена = [у["name"] for у in self._список()]
        self.assertEqual(len(имена), len(set(имена)))

    def test_empty_name_is_the_system_output(self):
        with _подменить(_sd()):
            индекс, описание = config.find_output_device("")
        self.assertEqual(индекс, 5)
        self.assertIn("Speakers", описание)

    def test_found_by_name(self):
        with _подменить(_sd()):
            индекс, _ = config.find_output_device("Realtek")
        self.assertEqual(индекс, 5)

    def test_name_which_is_not_there_is_the_system_output(self):
        with _подменить(_sd()):
            индекс, _ = config.find_output_device("Наушники JBL")
        self.assertEqual(индекс, 5)


class SpeakerNameTests(unittest.TestCase):
    """Выбранное имя доходит до настоящего динамика."""

    def test_speaker_gets_the_chosen_device(self):
        # Голосовой цикл поднимает колонки через `_make_speaker`; проверяем
        # именно там — иначе выбор колонок в пульте ничего бы не значил.
        import core.voice_loop as voice_loop

        сделанные = []

        class _Говорящий:
            def __init__(self, device=None, gap=0.0):
                сделанные.append(device)

        был = config.SPEAKER_NAME
        config.SPEAKER_NAME = "Realtek"
        self.addCleanup(setattr, config, "SPEAKER_NAME", был)

        цикл = object.__new__(voice_loop.VoiceLoop)
        sd = _sd()
        with _подменить(sd), \
                mock.patch("core.audio_out.Speaker", _Говорящий):
            цикл._make_speaker()
        self.assertEqual(сделанные, [5])

    def test_no_name_means_the_system_output(self):
        import core.voice_loop as voice_loop

        сделанные = []

        class _Говорящий:
            def __init__(self, device=None, gap=0.0):
                сделанные.append(device)

        был = config.SPEAKER_NAME
        config.SPEAKER_NAME = ""
        self.addCleanup(setattr, config, "SPEAKER_NAME", был)

        # Система назвала устройством по умолчанию микрофон (входа у него
        # нет, играть через него нечем). Настоящий `Speaker` в такой ситуации
        # получил бы непригодный индекс; `_make_speaker` обязан взять
        # первое подходящее, а не поверить системе вслепую.
        цикл = object.__new__(voice_loop.VoiceLoop)
        сд = _sd(default=[3, 3])
        with _подменить(сд), \
                mock.patch("core.audio_out.Speaker", _Говорящий):
            цикл._make_speaker()
        self.assertEqual(сделанные, [5])


class СохранениеТест(unittest.TestCase):
    """Выбор микрофона и колонок сохраняется и перезапускает прослушивание."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        from core import settings

        self.папка = Path(tempfile.mkdtemp(prefix="truba-mic-save-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        self._файл = settings.SETTINGS_PATH
        settings.SETTINGS_PATH = self.папка / "settings.json"
        self.addCleanup(self._вернуть, settings)

    def _вернуть(self, settings):
        settings.SETTINGS_PATH = self._файл

    def test_the_pult_saves_the_choice_and_the_config_sees_it(self):
        import config

        from core import settings

        settings.save_settings({"mic_name": "Строительный",
                                "mic_channel": 1,
                                "speaker_name": "Realtek"})
        значения = settings.load_settings()
        self.assertEqual(значения["mic_name"], "Строительный")
        self.assertEqual(значения["mic_channel"], 1)
        self.assertEqual(значения["speaker_name"], "Realtek")
        settings.apply_to_config()
        self.assertEqual(config.MIC_NAME, "Строительный")
        self.assertEqual(config.MIC_CHANNEL, 1)
        self.assertEqual(config.SPEAKER_NAME, "Realtek")

    def test_a_mistyped_channel_falls_back_to_the_first(self):
        # В настройки может попасть мусор (правка руками, старый пульт), и
        # голос без микрофона хуже голоса с первым входом. Поэтому проверка
        # живёт в `apply_to_config`, а не только в пульте.
        import config

        from core import settings

        settings.save_settings({"mic_channel": "микрофон",
                                "weather_lat": "вот тут"})
        settings.apply_to_config()
        self.assertEqual(config.MIC_CHANNEL, 0)
        self.assertIsNone(config.WEATHER_LAT)

    def test_a_device_change_drops_the_sound(self):
        # Смена микрофона или колонок требует поднять звук заново: иначе
        # голос продолжит слушать старую карту до перезапуска пульта.
        import threading
        from types import SimpleNamespace as NS

        from ui.web_runtime import WebRuntime

        среды = object.__new__(WebRuntime)
        среды._lock = threading.Lock()
        среды._remember = lambda kind, payload: None
        среды._bg = lambda func, *args: None
        среды._audio_stale = False
        среды.voice = NS(_apps=None, _loaded=True, _voice="старая модель",
                         _ref="старый образец", _speaker="старые колонки",
                         _listener="старый микрофон", reload_stt=lambda: None,
                         _close_conversation=lambda: None)
        среды.server = NS(send_mode=lambda режим: None,
                          send_volume=lambda уровень: None)
        сброшено = []
        среды._drop_audio = lambda: сброшено.append(1)
        ответ = среды.save_settings({"mic_name": "Строительный"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(сброшено, "звук должны были поднять заново")

    def test_a_device_change_while_the_voice_works_waits_for_a_restart(self):
        # Голос работает — рвать его посреди разговора нельзя. Тогда хозяину
        # честно говорим, когда новый микрофон вступит в дело.
        import threading
        from types import SimpleNamespace as NS

        from ui.web_runtime import WebRuntime

        среды = object.__new__(WebRuntime)
        среды._lock = threading.Lock()
        среды._remember = lambda kind, payload: None
        среды._bg = lambda func, *args: None
        среды._audio_stale = False
        среды.voice = NS(_apps=None, _loaded=True, running=True,
                         _voice="старая модель", _ref=None, _speaker=None,
                         _listener="старый микрофон", in_conversation=False,
                         _speaker_idle=lambda: False, reload_stt=lambda: None,
                         _close_conversation=lambda: None)
        среды.server = NS(send_mode=lambda режим: None,
                          send_volume=lambda уровень: None)
        ответ = среды.save_settings({"speaker_name": "Realtek"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertIn("note", ответ)


if __name__ == "__main__":
    unittest.main()
