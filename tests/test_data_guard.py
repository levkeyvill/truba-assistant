"""Страховка: тесты не пишут в настоящие данные хозяина.

26 сентября тест выжимки памяти звал настоящий `Brain._add_turn` и затёр
историю разговора хозяина выдуманными репликами про кота. `unittest discover`
сначала импортирует все модули и только потом гоняет тесты — поэтому пути,
подменённые здесь на уровне модуля, действуют на весь прогон. Тест, которому
нужен файл, подменяет путь сам, как и раньше.

Настоящий `.env` тоже убран: тест, которому нужен живой ключ, должен падать,
а не молча ходить с ключом хозяина.
"""

import atexit
import shutil
import tempfile
import unittest
from pathlib import Path

import config
from core import (app_icons, autostart, bookmarks, launcher, memory, notes, phone,
                  replay, reminders, settings, speaker, usage, voices_install,
                  weather)

SAFE = Path(tempfile.mkdtemp(prefix="truba-tests-"))
atexit.register(shutil.rmtree, SAFE, True)

memory.HISTORY_PATH = SAFE / "history.json"
memory.MEMORY_PATH = SAFE / "memory.json"
settings.ENV_PATH = SAFE / ".env"
settings.SETTINGS_PATH = SAFE / "settings.json"
settings.PERSONA_PATH = SAFE / "persona.md"
# На свежей установке своего характера нет (он появляется, когда его
# сохраняют в пульте) — тогда тестам достаётся готовый. 28.09 страховка падала
# на чистой установке, и остальные тесты шли без неё.
_СВОЙ_ХАРАКТЕР = config.ROOT / "prompts" / "persona.md"
if _СВОЙ_ХАРАКТЕР.exists():
    shutil.copy(_СВОЙ_ХАРАКТЕР, settings.PERSONA_PATH)
else:
    from core import personas

    settings.PERSONA_PATH.write_text(personas.text(personas.DEFAULT), encoding="utf-8")
replay.MEMO = SAFE / "replay_state.json"
speaker.PRINT_FILE = SAFE / "voiceprint.npz"
launcher.CACHE_FILE = SAFE / "apps_cache.json"
app_icons.CACHE_DIR = SAFE / "icons"
# Значки сайтов из закладок Firefox — тоже кеш на диске.
bookmarks.CACHE_DIR = SAFE / "sites"
# Расход: журнал ответов, кеш цен OpenRouter и кеш курса доллара.
usage.USAGE_PATH = SAFE / "usage.jsonl"
usage.OPENROUTER_PRICES_PATH = SAFE / "openrouter_prices.json"
usage.USD_RUB_PATH = SAFE / "usd_rub.json"
# Погода на телефоне: кеш прогноза на диске.
weather.CACHE_PATH = SAFE / "weather.json"
# Папка автозагрузки Windows: настоящую тесты не трогают — ярлык создаётся и
# удаляется только в этой временной папке.
autostart.STARTUP_DIR = SAFE / "startup"
# Заметки по умолчанию легли бы в «Документы» — настоящие, хозяина. На весь
# прогон папка уезжает во временную, иначе тест записи создал бы файл там.
config.NOTES_DIR = str(SAFE / "notes")
# Журнал установки качественных голосов: в живой `data/` его дописывает пульт.
voices_install.LOG_PATH = SAFE / "voices_install.log"
# Размер экрана телефона: макет в пульте читает его при каждом запуске.
phone.VIEWPORT_FILE = SAFE / "phone_viewport.json"
# Ключ привязки телефона: тест не должен ни прочитать настоящий, ни завести
# свой в живой `data/` — иначе телефон хозяина разом стал бы чужим.
phone.KEY_FILE = SAFE / "phone_key.txt"
# Список программ хозяина: с 29.09 битый файл уезжает в карантин, и тест с
# испорченным списком не должен добраться до настоящего.
launcher.APPS_FILE = SAFE / "apps.json"
# Напоминания и таймеры: тест ставит их пачками, и файл в живой `data/`
# после прогона означал бы, что хозяину остались чужие напоминания.
reminders.PATH = SAFE / "reminders.json"


class DataGuardTests(unittest.TestCase):
    def test_real_data_is_out_of_reach(self):
        for path in (memory.HISTORY_PATH, memory.MEMORY_PATH, settings.ENV_PATH,
                     settings.SETTINGS_PATH, settings.PERSONA_PATH, replay.MEMO,
                     speaker.PRINT_FILE, launcher.CACHE_FILE, app_icons.CACHE_DIR,
                     bookmarks.CACHE_DIR,
                     usage.USAGE_PATH, usage.OPENROUTER_PRICES_PATH,
                     usage.USD_RUB_PATH, weather.CACHE_PATH,
                     voices_install.LOG_PATH, phone.VIEWPORT_FILE, phone.KEY_FILE,
                     launcher.APPS_FILE, reminders.PATH,
                     autostart.STARTUP_DIR, notes.root()):
            self.assertTrue(str(path).startswith(str(SAFE)), path)


if __name__ == "__main__":
    unittest.main()
