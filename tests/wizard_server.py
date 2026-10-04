"""Копия пульта на 8775 для браузерной проверки мастера первого запуска.

Голоса нет, настройки не пишутся: ответы `/api/settings`, `/api/audio` и
`/api/hardware` подменены в памяти, `save_settings` — заглушка. Живой
settings.json, микрофон, сеть и облако в проверке не участвуют.
"""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS

# Запуск из папки проекта: `python tests\wizard_server.py 8775`, поэтому корень
# добавляем сами — иначе `import config` не найдёт модуль.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from core.phone import PhoneServer  # noqa: E402
from ui.web_runtime import WebRuntime  # noqa: E402


def пульт() -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер, голос и облако."""
    среда = object.__new__(WebRuntime)
    среда._lock = threading.Lock()
    среда._update_lock = threading.Lock()
    среда._bg = lambda func, *args: None
    среда._remember = lambda kind, payload: None
    среда._hw_cache = None
    среда._jobs = {}
    среда._enroll = None
    среда.voice = NS(running=False, ready=False, in_conversation=False,
                    _listener=None, _speaker_idle=lambda: True)
    return среда


def main() -> int:
    порт = int(sys.argv[1]) if len(sys.argv) > 1 else 8775
    сервер = PhoneServer(port=порт)
    сервер.start()
    for _ in range(50):
        if getattr(сервер, "_app", None) is not None:
            break
        threading.Event().wait(0.1)
    if not сервер._ready.wait(timeout=5):
        print("сервер не поднялся", flush=True)
        return 1
    runtime = пульт()
    runtime.settings_snapshot = lambda: {
        "ok": True, "provider": "deepseek",
        "providers": {имя: {"model": часть.get("model", ""), "has_key": False,
                            "local": bool(часть.get("local"))}
                      for имя, часть in config.PROVIDERS.items()},
        "settings": {"provider": "deepseek", "persona_preset": "friendly",
                     "weather_city": "", "weather_lat": None, "weather_lon": None,
                     "update_check": True, "first_run_done": False},
        "persona": "Привет", "memory": "", "addresses": ["192.168.1.50"],
        "voices": [], "voice_samples": [], "owner_known": False, "autostart": False,
    }
    runtime.save_settings = lambda тело: {"ok": True, "settings": тело}
    runtime.voice_state = lambda: {"running": False, "ready": False, "mode": "name",
                                   "in_conversation": False, "volume": 10}
    runtime.hardware_info = lambda: {
        "ok": True,
        "hw": {"windows": {"release": "Тестовая Windows"},
               "cpu": {"name": "Тестовый процессор", "cores": 8, "threads": 16},
               "ram_gb": 32.0, "disk_free_gb": 200.0,
               "gpus": [{"name": "Тестовая видеокарта", "vram_gb": 12.0}]},
        "recommend": {"voice": "silero", "voice_why": "Голос на процессоре."},
    }
    runtime.voices_status = lambda: {"ok": True, "running": False, "steps": [],
                                     "result": None, "possible": False, "why": "",
                                     "installed": False}
    runtime.audio_settings = lambda: {
        "ok": True, "inputs": [], "outputs": [],
        "current": {"mic_name": "", "mic_channel": 0, "speaker_name": "",
                    "output": "speakers"}}
    runtime.audio_level = lambda: {"ok": False, "error": "голос выключен"}
    runtime.voices_install = lambda models=None: {
        "ok": False, "error": "в проверке не ставим"}
    runtime.update_state = lambda: {"last": None, "running": False, "steps": [],
                                    "result": None}
    runtime.update_check = lambda: {"ok": False, "error": "в проверке не ходим в сеть"}
    runtime.update_install = lambda: {"ok": False, "error": "в проверке не ставим"}
    runtime.update_restart = lambda: {"ok": False, "error": "в проверке не перезапускаем"}
    runtime.voice_toggle = lambda: {"ok": True, "voice": runtime.voice_state()}
    runtime.voice_hush = lambda: {"ok": True, "voice": runtime.voice_state()}
    runtime.test_provider = lambda provider, key="", model=None: {
        "ok": False, "error": "в проверке облако не зовём"}
    runtime.weather_find = lambda query: {"ok": True, "places": []}
    runtime._обновить_погоду = lambda: None
    runtime.close_pult = lambda: None
    runtime.events = lambda after=0: []
    runtime.overview = lambda: {}
    runtime.logs = lambda limit=200: []
    runtime.usage_summary = lambda: {"ok": True, "periods": []}
    runtime.apps_snapshot = lambda: []
    runtime.stt_state = lambda: {"ok": True, "loading": False, "model": "tiny",
                                 "quantization": "int8", "title": "Маленькая модель"}
    runtime.cloud_latency = lambda: {"ok": False, "error": "в проверке не меряем"}
    сервер.runtime = runtime
    print(f"копия пульта на http://127.0.0.1:{порт}/pult", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
