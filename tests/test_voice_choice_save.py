"""Выбор движка и образца: сохранение, загрузка, причина вместо молчаливого Silero.

Отзыв о 0.9.6: поставил Higgs, выбрал Higgs, пульт сказал «модели скачаны»,
в списке образцов только «demo», после перезапуска снова Silero. Здесь
проверяется ровно та цепочка, что ломалась: «выбрал → сохранил → перезапустил
→ тот же движок». Ни голоса, ни микрофона, ни сети: движок и предпрослушивание
подменены, настройки и папка образцов — временные.

Ни одного частного случая под слово «demo»: пустой образец и отсутствующий
файл — это одно и то же состояние, и оно описано словами, а не отменой
сохранения.
"""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import config
from core import settings, state, voice_prep, voices_install
from ui.web_runtime import WebRuntime


def _пустой_образец(rt) -> None:
    """Голос выключен, звук грузить не нужно — только сохранение настроек."""
    rt.brain = None
    rt.events = []
    rt._remember = lambda kind, payload: rt.events.append(kind)
    rt._audio_stale = False
    rt._audio_probe_lock = threading.Lock()
    rt._level_capture_lock = threading.Lock()
    rt.voice = SimpleNamespace(
        _brain=None, _loaded=False, _voice=None, _speaker=None, _listener=None,
        _ref=None, running=False, in_conversation=False, _speaker_idle=lambda: True,
        _close_conversation=lambda: None, reload_stt=lambda: None,
    )


class VoiceChoiceSaveTests(unittest.TestCase):
    """Выбор движка переживает перезапуск — с образцом и без него."""

    def setUp(self):
        self._сохранено = {k: getattr(config, k) for k in dir(config) if k.isupper()}
        self.addCleanup(self._вернуть)
        self._папка = tempfile.TemporaryDirectory()
        self.addCleanup(self._папка.cleanup)
        корень = Path(self._папка.name)
        (корень / "voice").mkdir()
        for патч in (
            mock.patch.object(settings, "SETTINGS_PATH", корень / "settings.json"),
            mock.patch.object(settings, "ENV_PATH", корень / ".env"),
            mock.patch.object(voice_prep, "voices_dir", lambda: корень / "voice"),
            mock.patch.object(voice_prep, "read_text", lambda имя: f"текст {имя}"),
        ):
            патч.start()
            self.addCleanup(патч.stop)
        self.корень = корень
        self.rt = object.__new__(WebRuntime)
        _пустой_образец(self.rt)

    def _вернуть(self):
        for имя, значение in self._сохранено.items():
            setattr(config, имя, значение)

    def _образец(self, имя: str) -> None:
        (self.корень / "voice" / f"{имя}.wav").write_bytes(b"")

    def _сохранить(self, тело: dict) -> dict:
        ответ = self.rt.save_settings(тело)
        # «Повторный запуск» — настройки читаются заново и кладутся в config.
        settings.apply_to_config()
        return ответ

    def test_higgs_without_a_sample_survives_a_restart(self):
        ответ = self._сохранить({"tts_engine": "higgs", "voice_name": ""})

        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(config.TTS_ENGINE, "higgs")
        # Причина названа словами и назван путь, а не выдан тихий Silero.
        self.assertIn("образц", ответ["note"].lower())
        self.assertIn("wav", ответ["note"].lower())

    def test_espeech_without_a_sample_survives_a_restart_too(self):
        # Общая причина, а не частность Higgs: движок один на оба.
        self._сохранить({"tts_engine": "silero"})
        self._сохранить({"tts_engine": "espeech", "voice_name": ""})

        self.assertEqual(config.TTS_ENGINE, "espeech")

    def test_a_sample_is_not_a_reason_to_drop_the_engine(self):
        self._сохранить({"tts_engine": "silero"})
        ответ = self._сохранить({"tts_engine": "higgs", "voice_name": ""})
        сохранено = settings.load_settings()

        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(сохранено["tts_engine"], "higgs")
        self.assertEqual(сохранено["voice_name"], "")

    def test_a_named_sample_survives_a_restart(self):
        self._образец("мой")
        self._сохранить({"tts_engine": "higgs", "voice_name": "мой"})

        self.assertEqual(config.TTS_ENGINE, "higgs")
        self.assertEqual(config.VOICE_NAME, "мой")

    def test_a_silero_switch_still_clears_the_sample(self):
        self._образец("мой")
        self._сохранить({"tts_engine": "higgs", "voice_name": "мой"})
        self._сохранить({"tts_engine": "silero"})

        self.assertEqual(config.TTS_ENGINE, "silero")

    def test_a_missing_file_is_still_refused_and_where_to_fix_it(self):
        # Образца нет на диске, хотя имя сохранено: голос должна сказать, что
        # именно не так, а не замолчать и не сменить движок сама.
        from core import voice_loop

        self._сохранить({"tts_engine": "higgs", "voice_name": "нет-такого"})
        голос = object.__new__(voice_loop.VoiceLoop)
        голос._loaded = False
        голос._emit = lambda вид, текст: None
        with mock.patch("core.audio_in.Listener"), \
                mock.patch.object(voice_loop, "load_stt"), \
                mock.patch.object(voice_loop, "make_voice"):
            with self.assertRaisesRegex(RuntimeError, "нет-такого"):
                голос._load()

        self.assertEqual(config.TTS_ENGINE, "higgs")  # не откатился в Silero

    def test_the_card_does_not_claim_good_news_without_a_sample(self):
        with mock.patch.object(config, "TTS_ENGINE", "higgs"), \
                mock.patch.object(config, "VOICE_NAME", ""):
            карточка = state._voice()

        self.assertFalse(карточка["хорошо"])
        self.assertIn("образец не выбран", карточка["внизу"])

    def test_the_card_names_the_sample_when_there_is_one(self):
        with mock.patch.object(config, "TTS_ENGINE", "espeech"), \
                mock.patch.object(config, "VOICE_NAME", "мой"):
            карточка = state._voice()

        self.assertTrue(карточка["хорошо"])
        self.assertIn("мой", карточка["внизу"])

class WeightsStageTests(unittest.TestCase):
    """Стадия установки: библиотеки и веса — разные вещи."""

    def test_the_status_tells_both_stages_apart(self):
        rt = object.__new__(WebRuntime)
        with mock.patch("core.hardware.voices_installed", return_value=True), \
                mock.patch.object(voices_install, "check", return_value={"ok": True}), \
                mock.patch.object(voices_install, "weights_installed",
                                  return_value={"espeech": True, "higgs": False}):
            статус = rt.voices_status()

        # Библиотеки стоят, но Higgs ещё не скачан — и пульт это различает.
        self.assertTrue(статус["installed"])
        self.assertEqual(статус["weights"], {"espeech": True, "higgs": False})

    def test_a_missing_weight_file_is_reported_and_never_downloads(self):
        with mock.patch.object(voices_install, "_скачан_файл", return_value=False):
            веса = voices_install.weights_installed()

        self.assertEqual(веса, {"espeech": False, "higgs": False})

    def test_the_check_asks_the_local_cache_only(self):
        вызовы = []

        def кеш(*args, **kwargs):
            вызовы.append(kwargs)
            raise FileNotFoundError("нет в кеше")

        with mock.patch.dict("sys.modules", {"huggingface_hub": SimpleNamespace(
                hf_hub_download=кеш, snapshot_download=кеш)}):
            self.assertFalse(voices_install._скачан_файл("repo", "file.bin"))

        self.assertTrue(вызовы)
        for вызов in вызовы:
            self.assertTrue(вызов.get("local_files_only"))

    def test_higgs_needs_every_file_used_by_the_loader(self):
        with tempfile.TemporaryDirectory() as папка:
            корень = Path(папка)
            (корень / "config.json").write_text("{}", encoding="utf-8")
            (корень / "model.safetensors").write_bytes(b"weight")
            hub = SimpleNamespace(snapshot_download=lambda *a, **k: папка,
                                  hf_hub_download=mock.Mock())
            with mock.patch.dict("sys.modules", {"huggingface_hub": hub}):
                self.assertFalse(voices_install._скачан_файл(
                    "repo", нужны=("config.json", "model.safetensors", "tokenizer.json")))
                (корень / "tokenizer.json").write_text("{}", encoding="utf-8")
                self.assertTrue(voices_install._скачан_файл(
                    "repo", нужны=("config.json", "model.safetensors", "tokenizer.json")))

    def test_espeech_needs_the_vocabulary_as_well_as_weights(self):
        from core import tts

        def есть(репо, имя=None, нужны=()):
            if репо == tts.MODEL_REPO:
                return имя == tts.MODEL_FILE
            return True

        with mock.patch.object(voices_install, "_скачан_файл", side_effect=есть):
            self.assertFalse(voices_install.weights_installed()["espeech"])


class PultTextTests(unittest.TestCase):
    """Тексты пульта: правда о стадии и никаких «пресетов модели»."""

    def setUp(self):
        self.скрипт = (Path(config.ROOT) / "ui" / "web" / "pult.js").read_text(
            encoding="utf-8")

    def test_the_pult_does_not_cancel_the_save_over_a_missing_sample(self):
        # Раньше здесь стоял `return`, и выбор Higgs без образца не писался.
        self.assertNotIn("Добавь или выбери образец голоса", self.скрипт)

    def test_the_sample_field_says_it_is_a_recording_not_a_preset(self):
        # Подпись поля: запись .wav/.mp3, а не «пресет модели». Проверяем саму
        # подпись, а не весь файл: «пресеты» в нём есть — про быстрые действия.
        подпись = self.скрипт.split("  voice_name: [")[1].split("],")[0]
        self.assertIn("Запись .wav или .mp3", подпись)
        self.assertNotIn("пресет", подпись.lower())

    def test_an_empty_sample_list_says_what_to_do(self):
        self.assertIn("образцов нет — добавь запись", self.скрипт)

    def test_the_install_line_does_not_promise_downloaded_models(self):
        # «Скачаны» обещание, которого на стадии библиотек ещё нет.
        блок = self.скрипт.split("function показатьГолоса(")[1].split("\n}\n")[0]
        self.assertIn("голосаВеса(данные.weights)", блок)
        self.assertNotIn("модели скачаны'", блок.lower())


if __name__ == "__main__":
    unittest.main()
