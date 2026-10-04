# Сторонние компоненты и их лицензии

Код в этом репозитории — сама Труба (её лицензия — в файле `LICENSE`).
**Модели в репозиторий не входят:** установщик и пульт скачивают их с
Hugging Face и серверов авторов. Полный установочный ZIP может включать
неизменённую модель голоса Silero, чтобы установка не зависела от доступности
её сервера. У каждой модели своя лицензия, и её условия действуют на тебя,
когда ты ей пользуешься.

## Главное

- **Голос по умолчанию (Silero) и голос Higgs — только для некоммерческого
  использования.** Труба бесплатная, донаты автору добровольные и ничего не
  открывают. Если хочешь использовать Трубу для заработка, нужны отдельные
  лицензии правообладателей этих моделей.
- **Копировать чужой голос нельзя без согласия его владельца.** Это прямой
  запрет лицензии Higgs, и это просто честно по отношению к людям. В
  «Голос → Озвучивание → Новый голос из записи» — только свой голос или
  голос человека, который разрешил.

## Модели

| Что | Зачем в Трубе | Лицензия |
|---|---|---|
| [Silero TTS](https://github.com/snakers4/silero-models) (`v5_5_ru`) | голос по умолчанию, на процессоре; в полном ZIP — неизменённая модель Silero Team | [CC BY-NC-SA 4.0](https://github.com/snakers4/silero-models/blob/master/LICENSE) — некоммерческое использование с указанием автора |
| [Silero VAD](https://github.com/snakers4/silero-vad) | детектор речи в микрофоне | MIT |
| [GigaAM v3](https://github.com/salute-developers/GigaAM) (ONNX-сборка [istupakov/gigaam-v3-onnx](https://huggingface.co/istupakov/gigaam-v3-onnx)) | распознавание речи | MIT |
| [WeSpeaker ResNet34-LM](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM) | отпечаток голоса («только хозяин») | CC BY 4.0 |
| [ESpeech TTS 1 RL V2](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2) | качественный голос (NVIDIA) | Apache 2.0 |
| [Boson Higgs TTS 3](https://huggingface.co/bosonai/higgs-tts-3-4b) | качественный голос (NVIDIA) | Boson Higgs TTS 3 Research and Non-Commercial License — текст в [`licenses/Boson-Higgs-TTS-3-LICENSE.txt`](licenses/Boson-Higgs-TTS-3-LICENSE.txt) |
| [Boson Higgs Audio 2 tokenizer](https://huggingface.co/bosonai/higgs-audio-v2-tokenizer) | часть голоса Higgs | Boson Higgs Audio 2 Community License — текст в [`licenses/Boson-Higgs-Audio-2-LICENSE.txt`](licenses/Boson-Higgs-Audio-2-LICENSE.txt) |

Boson Higgs TTS 3 is licensed under the Boson Higgs TTS 3 Research and
Non-Commercial License, Copyright (c) Boson AI USA, Inc.

## Код и шрифты

- `core/higgs_voice.py` повторяет алгоритм Higgs TTS 3 на обычном PyTorch по
  исходникам [SGLang-Omni](https://github.com/sgl-project/sglang-omni)
  (`sglang_omni/models/higgs_tts`), Apache License 2.0, © авторы SGLang-Omni.
- Шрифт [Montserrat](https://github.com/JulietaUla/Montserrat) в `web/fonts`
  — SIL Open Font License 1.1, текст в [`web/fonts/OFL.txt`](web/fonts/OFL.txt).
- Библиотеки Python ставятся установщиком из PyPI, у каждой своя лицензия
  (список — `requirements.txt`): PyTorch (BSD), onnxruntime и onnx-asr (MIT),
  FastAPI и Uvicorn (MIT / BSD), pywebview (BSD), sounddevice (MIT), pycaw
  (MIT), psutil (BSD), segno (BSD), openai (Apache 2.0), ddgs (MIT) и другие.
