"""Страховка: тесты не пишут в настоящие данные хозяина.

`unittest discover`
сначала импортирует все модули и только потом гоняет тесты — поэтому пути,
подменённые здесь на уровне модуля, действуют на весь прогон. Тест, которому
нужен файл, подменяет путь отдельно.

Настоящий `.env` тоже убран: тест, которому нужен живой ключ, должен падать,
а не молча ходить с ключом хозяина.
"""

import atexit
import shutil
import tempfile
import unittest
from pathlib import Path

import config
from core import (app_icons, autostart, bookmarks, instance, launcher, memory,
                  notes, phone, replay, reminders, search_history, settings,
                  speaker, usage, voices_install, weather)

SAFE = Path(tempfile.mkdtemp(prefix="truba-tests-"))
atexit.register(shutil.rmtree, SAFE, True)

memory.HISTORY_PATH = SAFE / "history.json"
memory.MEMORY_PATH = SAFE / "memory.json"
settings.ENV_PATH = SAFE / ".env"
settings.SETTINGS_PATH = SAFE / "settings.json"
settings.PERSONA_PATH = SAFE / "persona.md"
# На свежей установке своего характера нет (он появляется, когда его
# сохраняют в пульте) — тогда тестам достаётся готовый.
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
# Список программ: битый файл уезжает в карантин, и тест с
# испорченным списком не должен добраться до настоящего.
launcher.APPS_FILE = SAFE / "apps.json"
# Напоминания и таймеры: тест ставит их пачками, и файл в живой `data/`
# после прогона означал бы, что хозяину остались чужие напоминания.
reminders.PATH = SAFE / "reminders.json"
# История поиска: тест наполняет её запросами, и файл в живой `data/` после
# прогона означал бы, что у хозяина в истории чужие фразы.
search_history.PATH = SAFE / "search_history.json"
# Отпечаток установки: тест личности пульта пишет его, а в живой `data/`
# попадание сюда означало бы, что вторая копия получила личность основной.
instance.ID_FILE = SAFE / "install_id.txt"


class DataGuardTests(unittest.TestCase):
    def test_real_data_is_out_of_reach(self):
        for path in (memory.HISTORY_PATH, memory.MEMORY_PATH, settings.ENV_PATH,
                     settings.SETTINGS_PATH, settings.PERSONA_PATH, replay.MEMO,
                     speaker.PRINT_FILE, launcher.CACHE_FILE, app_icons.CACHE_DIR,
                     bookmarks.CACHE_DIR,
                     usage.USAGE_PATH, usage.OPENROUTER_PRICES_PATH,
                     usage.USD_RUB_PATH, weather.CACHE_PATH,
                     voices_install.LOG_PATH, phone.VIEWPORT_FILE, phone.KEY_FILE,
                     launcher.APPS_FILE, reminders.PATH, search_history.PATH,
                     instance.ID_FILE,
                     autostart.STARTUP_DIR, notes.root()):
            self.assertTrue(str(path).startswith(str(SAFE)), path)

    def test_история_ищется_и_чистится_только_в_своей_папке(self):
        # С телефона теперь приходит `id` записи, и его нельзя превратить в
        # путь: `delete`/`clear` обязаны трогать ровно один файл — подменённый
        # выше. Ничего рядом не создаётся и не открывается.
        прежний_путь = search_history.PATH
        with tempfile.TemporaryDirectory(dir=SAFE) as folder:
            search_history.PATH = Path(folder) / "search_history.json"
            try:
                поиск = search_history.add("курс доллара")
                self.assertIsNotNone(поиск)
                self.assertTrue(search_history.delete(поиск["id"]))
                search_history.add("ещё раз")
                self.assertEqual(search_history.clear(), 1)
                self.assertEqual(set(Path(folder).iterdir()), {search_history.PATH},
                                 "поиск задела не только свой файл")
            finally:
                search_history.PATH = прежний_путь


if __name__ == "__main__":
    unittest.main()
