"""Облачный голос: запрос к сервису, звук потоком, Silero при отказе, настройки.

Сеть подменена `httpx.MockTransport`: настоящий сервис не вызывается, ключи
выдуманные. Запасной Silero — подделка, модель не грузится. settings.json и
.env — во временной папке, живые не трогаются.
"""

import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import httpx
import numpy as np

import config
from core import cloud_voice, settings, transfer, voice_loop
from core.cloud_voice import CloudVoice
from ui.web_runtime import WebRuntime

PULT_JS = Path(config.ROOT) / "ui" / "web" / "pult.js"
КЛЮЧ = "sk-test-fake"


def _pcm(секунд: float, частота: int = 44100) -> bytes:
    звук = (np.sin(np.linspace(0, 200, int(секунд * частота))) * 8000).astype("<i2")
    return звук.tobytes()


def _wav(секунд: float, частота: int = 24000) -> bytes:
    буфер = io.BytesIO()
    with wave.open(буфер, "wb") as файл:
        файл.setnchannels(1)
        файл.setsampwidth(2)
        файл.setframerate(частота)
        файл.writeframes(_pcm(секунд, частота))
    return буфер.getvalue()


class _Запасной:
    def __init__(self):
        self.фразы = []

    def say(self, text, speed=1.0):
        self.фразы.append(text)
        return np.ones(480, dtype=np.float32) * 0.1, 48000


def _голос(обработчик, **kw) -> CloudVoice:
    голос = CloudVoice(service=kw.pop("service", "openrouter"), key=kw.pop("key", КЛЮЧ),
                       model=kw.pop("model", ""), voice=kw.pop("voice", ""), **kw)
    голос._client = httpx.Client(transport=httpx.MockTransport(обработчик))
    if голос._spare_wanted:
        голос._spare = _Запасной()
        голос._spare_ready.set()
    return голос


class СинтезTests(unittest.TestCase):
    def setUp(self):
        self.запросы = []

    def _ответ(self, статус=200, тело=b"", вид="audio/pcm;rate=44100;channels=1"):
        def обработчик(запрос):
            self.запросы.append(json.loads(запрос.content))
            return httpx.Response(статус, content=тело, headers={"content-type": вид})
        return обработчик

    def test_сырой_звук_с_частотой_из_заголовка(self):
        голос = _голос(self._ответ(тело=_pcm(1.0)))
        звук, частота = голос.say("Привет, это проверка.")
        self.assertEqual(частота, 44100)
        self.assertEqual(len(звук), 44100)
        self.assertEqual(звук.dtype, np.float32)
        запрос = self.запросы[0]
        self.assertEqual(запрос["response_format"], "pcm")
        self.assertEqual(запрос["model"], "heygen/heygen-voice-1-turbo:free")
        self.assertEqual(запрос["voice"], "noelle")

    def test_числа_словами_латиница_как_есть(self):
        голос = _голос(self._ответ(тело=_pcm(0.2)))
        голос.say("В 18:42 открою Discord")
        текст = self.запросы[0]["input"]
        self.assertNotRegex(текст, r"\d")
        self.assertIn("Discord", текст)

    def test_другая_модель_без_чужого_голоса(self):
        голос = _голос(self._ответ(тело=_pcm(0.2)), model="fish-audio/s2.1-pro-free:free")
        голос.say("Привет")
        self.assertNotIn("voice", self.запросы[0])

    def test_у_openai_голос_по_умолчанию_у_любой_модели(self):
        голос = _голос(self._ответ(тело=_pcm(0.2, 24000), вид="audio/pcm"),
                       service="openai", model="tts-1")
        звук, частота = голос.say("Привет")
        self.assertEqual(self.запросы[0]["voice"], "nova")
        self.assertEqual(частота, 24000)

    def test_не_принял_pcm_просим_wav_и_запоминаем(self):
        def обработчик(запрос):
            тело = json.loads(запрос.content)
            self.запросы.append(тело)
            if тело["response_format"] == "pcm":
                return httpx.Response(400, json={"error": "unsupported response_format"})
            return httpx.Response(200, content=_wav(0.5), headers={"content-type": "audio/wav"})
        голос = _голос(обработчик)
        звук, частота = голос.say("Привет")
        self.assertEqual((частота, len(звук)), (24000, 12000))
        голос.say("Ещё раз")
        self.assertEqual([з["response_format"] for з in self.запросы], ["pcm", "wav", "wav"])

    def test_битый_сжатый_звук_silero(self):
        голос = _голос(self._ответ(тело=b"ID3....", вид="audio/mpeg"))
        звук, частота = голос.say("Привет")
        self.assertEqual(частота, 48000)  # сказал Silero
        self.assertEqual(голос._spare.фразы, ["Привет"])

    def test_только_mp3_сразу_к_нему_и_запоминаем(self):
        import soundfile

        буфер = io.BytesIO()
        soundfile.write(буфер, (np.sin(np.linspace(0, 300, 32000)) * 0.3).astype("float32"),
                        32000, format="MP3")
        mp3 = буфер.getvalue()

        def обработчик(запрос):
            тело = json.loads(запрос.content)
            self.запросы.append(тело)
            if тело["response_format"] != "mp3":
                return httpx.Response(400, json={"error": {"message":
                    'MiniMax TTS only supports response_format="mp3" for streaming'}})
            return httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg"})
        голос = _голос(обработчик, model="minimax/speech-2.8-turbo", voice="Russian_BrightHeroine")
        звук, частота = голос.say("Привет")
        self.assertEqual(частота, 32000)
        self.assertGreater(len(звук), 30000)
        # pcm отвергнут, wav пропущен — сервис сам назвал mp3.
        self.assertEqual([з["response_format"] for з in self.запросы], ["pcm", "mp3"])
        голос.say("Ещё")
        self.assertEqual(self.запросы[-1]["response_format"], "mp3")

    def test_отказ_silero_и_пауза_без_облака(self):
        заметки = []
        голос = _голос(self._ответ(статус=401, тело=b'{"error":"bad key"}'))
        голос.on_note = заметки.append
        звук, частота = голос.say("Первая")
        self.assertEqual(частота, 48000)
        голос.say("Вторая")
        self.assertEqual(len(self.запросы), 1, "в паузе облако не трогаем")
        self.assertEqual(голос._spare.фразы, ["Первая", "Вторая"])
        self.assertEqual(len(заметки), 1)
        self.assertIn("ключ не подошёл", заметки[0])
        self.assertIn("Silero", заметки[0])

    def test_после_паузы_снова_облако_и_запись_в_журнал(self):
        ответы = iter([httpx.Response(503, content=b"down"),
                       httpx.Response(200, content=_pcm(0.2),
                                      headers={"content-type": "audio/pcm;rate=44100"})])
        заметки = []
        голос = _голос(lambda запрос: next(ответы))
        голос.on_note = заметки.append
        голос.say("Раз")
        голос._down_until = 0.0
        звук, частота = голос.say("Два")
        self.assertEqual(частота, 44100)
        self.assertEqual(заметки[-1], "снова отвечает")

    def test_нет_связи_тоже_silero(self):
        def обработчик(запрос):
            raise httpx.ConnectError("нет сети")
        голос = _голос(обработчик)
        звук, частота = голос.say("Привет")
        self.assertEqual(частота, 48000)

    def test_ключ_с_русской_буквой_не_роняет(self):
        заметки = []
        голос = _голос(self._ответ(тело=_pcm(0.2)), key="sk-ключ")
        голос.on_note = заметки.append
        звук, частота = голос.say("Привет")
        self.assertEqual(частота, 48000)
        self.assertEqual(self.запросы, [])
        self.assertIn("посторонние символы", заметки[0])

    def test_проба_без_запасного_честно_падает(self):
        голос = _голос(self._ответ(статус=402, тело=b"no money"), spare=False)
        with self.assertRaises(RuntimeError) as ошибка:
            голос.say("Привет")
        self.assertIn("кончились деньги", str(ошибка.exception))

    def test_поток_кусками(self):
        голос = _голос(self._ответ(тело=_pcm(2.0)))
        куски = list(голос.stream("Длинная фраза для потока"))
        self.assertGreater(len(куски), 3)
        self.assertEqual(sum(len(к) for к, _ in куски), 88200)
        self.assertTrue(all(ч == 44100 for _, ч in куски))

    def test_поток_при_отказе_до_звука_целиком_silero(self):
        голос = _голос(self._ответ(статус=500, тело=b"boom"))
        куски = list(голос.stream("Фраза"))
        self.assertEqual(len(куски), 1)
        self.assertEqual(куски[0][1], 48000)

    def test_скорость_меняет_длину(self):
        голос = _голос(self._ответ(тело=_pcm(2.0)))
        звук, _ = голос.say("Быстрее", speed=1.25)
        self.assertLess(len(звук), 88200 * 0.9)

    def test_пустой_текст_не_ходит_в_сеть(self):
        голос = _голос(self._ответ(тело=_pcm(0.2)))
        звук, _ = голос.say("  ... ")
        self.assertEqual(len(звук), 0)
        self.assertEqual(self.запросы, [])


class ЗагрузкаTests(unittest.TestCase):
    def test_без_ключа_понятная_ошибка(self):
        with mock.patch.object(settings, "cloud_voice_key", return_value=""):
            with self.assertRaises(RuntimeError) as ошибка:
                CloudVoice(service="openai").load()
        self.assertIn("нет ключа", str(ошибка.exception))

    def test_свой_адрес_без_адреса(self):
        with self.assertRaises(RuntimeError) as ошибка:
            CloudVoice(service="custom", url="", model="x", key="").load()
        self.assertIn("адрес", str(ошибка.exception))

    def test_свой_адрес_без_ключа_можно(self):
        голос = CloudVoice(service="custom", url="http://127.0.0.1:8880/v1", model="kokoro",
                           key="", spare=False)
        голос.load()
        self.assertNotIn("Authorization", голос._headers())

    def test_движок_cloud_даёт_облачный_голос(self):
        with mock.patch.object(config, "TTS_ENGINE", "cloud"):
            self.assertIsInstance(voice_loop.make_voice(), CloudVoice)

    def test_видеопамять_не_занимает(self):
        self.assertEqual(CloudVoice(key=КЛЮЧ).vram_used_mb(), 0.0)


class КаталогTests(unittest.TestCase):
    def test_openai_без_сети(self):
        with mock.patch.object(httpx, "get", side_effect=AssertionError("сеть")):
            найдено = cloud_voice.catalog("openai")
        self.assertIn("gpt-4o-mini-tts", [м["id"] for м in найдено["models"]])
        self.assertIn("nova", найдено["voices"])

    def test_openrouter_бесплатные_наверху(self):
        данные = {"data": [
            {"id": "paid/voice", "name": "Paid", "pricing": {"prompt": "0.000004"},
             "supported_voices": ["a"]},
            {"id": "free/voice:free", "name": "Free", "pricing": {"prompt": "0", "completion": "0"},
             "supported_voices": ["b", "c"]},
        ]}
        ответ = httpx.Response(200, json=данные, request=httpx.Request("GET", "https://x"))
        with mock.patch.object(httpx, "get", return_value=ответ):
            найдено = cloud_voice.catalog("openrouter")
        self.assertEqual([м["id"] for м in найдено["models"]], ["free/voice:free", "paid/voice"])
        self.assertTrue(найдено["models"][0]["free"])
        self.assertEqual(найдено["voices"], ["a", "b", "c"])
        self.assertEqual(найдено["models"][1]["price"], 0.000004)
        self.assertFalse(найдено["models"][1]["any_voice"])
        self.assertEqual(найдено["models"][0]["price"], 0.0)

    def test_русские_голоса_вперёд(self):
        данные = {"data": [{"id": "ms/mai", "name": "MAI", "pricing": {"prompt": "0.000015"},
                            "supported_voices": ["en-US-Harper:x", "ru-RU-Masha:x", "de-DE-Mia:x",
                                                 "ru-RU-Lev:x"]}]}
        ответ = httpx.Response(200, json=данные, request=httpx.Request("GET", "https://x"))
        with mock.patch.object(httpx, "get", return_value=ответ):
            найдено = cloud_voice.catalog("openrouter")
        self.assertEqual(найдено["models"][0]["voices"],
                         ["ru-RU-Masha:x", "ru-RU-Lev:x", "en-US-Harper:x", "de-DE-Mia:x"])


class НастройкиTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-cloud-voice-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        подмена = mock.patch.multiple(settings, SETTINGS_PATH=self.папка / "settings.json",
                                      ENV_PATH=self.папка / ".env")
        подмена.start()
        self.addCleanup(подмена.stop)
        окружение = mock.patch.dict(os.environ, {}, clear=False)
        окружение.start()
        self.addCleanup(окружение.stop)
        было = {имя: getattr(config, имя) for имя in (
            "TTS_ENGINE", "CLOUD_VOICE_SERVICE", "CLOUD_VOICE_MODEL", "CLOUD_VOICE_VOICE",
            "CLOUD_VOICE_URL", "NOTES_DIR")}
        self.addCleanup(lambda: [setattr(config, к, з) for к, з in было.items()])
        settings.ENV_PATH.write_text("LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=sk-мозг\n",
                                     encoding="utf-8")
        self.звонки = []
        rt = object.__new__(WebRuntime)
        rt._lock = threading.Lock()
        rt._provider_test_lock = threading.Lock()
        rt._audio_stale = False
        rt.brain = None
        rt.voice = NS()
        rt._remember = lambda kind, payload: None
        rt._bg = lambda func, *args: func(*args)
        rt._voice_busy = lambda: False
        rt._drop_audio = lambda: self.звонки.append("drop")
        self.rt = rt

    def test_свой_ключ_живёт_рядом_с_ключами_мозга(self):
        settings.set_cloud_voice_key("custom", "sk-голос")
        settings.set_api_key("openai", "sk-openai")
        текст = settings.ENV_PATH.read_text(encoding="utf-8")
        self.assertIn("CLOUD_VOICE_API_KEY=sk-голос", текст)
        self.assertIn("DEEPSEEK_API_KEY=sk-мозг", текст)
        self.assertEqual(settings.cloud_voice_key("custom"), "sk-голос")
        self.assertEqual(settings.cloud_voice_key("openai"), "sk-openai")

    def test_ключ_openrouter_общий_с_мозгом(self):
        settings.set_cloud_voice_key("openrouter", "sk-or-общий")
        self.assertEqual(settings.get_api_key("openrouter"), "sk-or-общий")

    def test_сохранение_облачного_голоса(self):
        ответ = self.rt.save_settings({
            "tts_engine": "cloud", "cloud_voice_service": "custom",
            "cloud_voice_url": "http://127.0.0.1:8880/v1/", "cloud_voice_model": "kokoro",
            "cloud_voice_voice": "af_bella", "cloud_voice_key": "sk-свой"})
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual((config.TTS_ENGINE, config.CLOUD_VOICE_SERVICE, config.CLOUD_VOICE_URL),
                         ("cloud", "custom", "http://127.0.0.1:8880/v1"))
        self.assertEqual(settings.cloud_voice_key("custom"), "sk-свой")
        self.assertIn("drop", self.звонки)
        снимок = ответ["settings"]
        self.assertTrue(снимок["cloud_voice_keys"]["custom"])
        self.assertNotIn("sk-свой", json.dumps(снимок, ensure_ascii=False))

    def test_чужой_сервис_и_кривой_адрес_отвергаются(self):
        ответ = self.rt.save_settings({"cloud_voice_service": "elevenlabs"})
        self.assertFalse(ответ["ok"])
        ответ = self.rt.save_settings({"cloud_voice_url": "ftp://x"})
        self.assertFalse(ответ["ok"])
        self.assertTrue(any(о.startswith("cloud_voice_url") for о in ответ["errors"]))

    def test_мусор_в_settings_json_не_ломает_запуск(self):
        settings.save_settings({"cloud_voice_service": "ерунда", "cloud_voice_model": ["x"],
                                "cloud_voice_url": "не адрес"})
        settings.apply_to_config()
        self.assertEqual(config.CLOUD_VOICE_SERVICE, "openrouter")
        self.assertEqual((config.CLOUD_VOICE_MODEL, config.CLOUD_VOICE_URL), ("", ""))

    def test_перенос_настроек_берёт_ключ_голоса(self):
        self.assertIn("CLOUD_VOICE_API_KEY", transfer.известные_ключи())


class ПультTests(unittest.TestCase):
    def test_пункт_и_поля_в_озвучивании(self):
        пульт = PULT_JS.read_text(encoding="utf-8")
        self.assertIn("['cloud', 'Облачный голос · через интернет']", пульт)
        self.assertIn("'/api/cloud-voice/catalog?service='", пульт)
        for поле in ("cloud_voice_service", "cloud_voice_url", "cloud_voice_key",
                     "cloud_voice_model", "cloud_voice_voice"):
            self.assertIn(f"{поле}: эл.{поле}.value", пульт)

    def test_голоса_только_выбранной_модели_и_цена(self):
        пульт = PULT_JS.read_text(encoding="utf-8")
        self.assertIn("const голоса = (модель && модель.voices) || [];", пульт)
        self.assertIn("У этой модели нет списка голосов", пульт)
        self.assertIn("' · платная, $' + облакоЦена(модель.price)", пульт)

    def test_голос_проверяется_перед_сохранением(self):
        пульт = PULT_JS.read_text(encoding="utf-8")
        self.assertIn("const голосМимо = облакоГолосНеПодходит(эл);", пульт)
        self.assertIn("надо выбрать голос из списка", пульт)
        self.assertIn("!голоса.includes(голос) && !модель.any_voice", пульт)


if __name__ == "__main__":
    unittest.main()
