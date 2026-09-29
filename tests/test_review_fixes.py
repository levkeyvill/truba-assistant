"""Находки внешней проверки кода (29.09), каждая — своим тестом.

1. Разбор памяти терял кусок разговора, если облако не ответило.
2. Номера фактов из ответа модели применялись к памяти, которую за это время
   могли поменять; ручная правка давно открытой страницы стирала свежее.
3. Файлы писались поверх: оборванная запись оставляла пустой файл, а битый
   JSON молча превращался в «ничего» и затирался.
4. Откат обновления оставлял новые файлы, а убранные из выпуска — не убирались.
"""

import io
import shutil
import tempfile
import threading
import unittest
import zipfile
from collections import deque
from pathlib import Path
from unittest import mock

import config
from core import brain as brain_module
from core import memory, safe_files, settings, updater

# Помощники — свои, а не из tests.test_updater: импорт соседнего теста по
# имени пакета запускал страховку данных второй раз, с другой временной
# папкой, и её собственная проверка падала.


def _качать(архив: bytes):
    def скачать(url, куда, timeout=300.0):
        Path(куда).write_bytes(архив)
    return mock.patch.object(updater, "_скачать", скачать)


def _zip(файлы: dict) -> bytes:
    поток = io.BytesIO()
    with zipfile.ZipFile(поток, "w", zipfile.ZIP_DEFLATED) as коробка:
        for имя, текст in файлы.items():
            коробка.writestr(f"levkeyvill-truba-abc123/{имя}", текст)
    return поток.getvalue()


class ОбщиеУсловия(unittest.TestCase):
    """Временный проект; pip и проверка запуска — заглушки."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-review-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "truba"
        for папка in ("core", "ui", "data"):
            (self.root / папка).mkdir(parents=True, exist_ok=True)
        (self.root / "core" / "phone.py").write_text("старая версия\n", encoding="utf-8")
        (self.root / "ui" / "window.py").write_text("старое окно\n", encoding="utf-8")
        (self.root / "config.py").write_text("VERSION = '0.9.0'\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("httpx\n", encoding="utf-8")
        pip_patch = mock.patch.object(updater, "_pip_install", mock.Mock(return_value=True))
        smoke_patch = mock.patch.object(updater, "_smoke", mock.Mock(return_value=True))
        self.pip = pip_patch.start()
        self.smoke = smoke_patch.start()
        self.addCleanup(pip_patch.stop)
        self.addCleanup(smoke_patch.stop)
        self.выпуск = {"state": "newer", "latest": "9.9.9",
                       "zip": "https://api.github.com/repos/levkeyvill/truba/zipball/v9.9.9"}

    def _ставить(self, файлы):
        with _качать(_zip(файлы)):
            return updater.install(self.выпуск, root=self.root)


# --- 1. Разбор памяти при сбое облака ------------------------------------


def _мозг(реплики: int) -> brain_module.Brain:
    мозг = object.__new__(brain_module.Brain)
    мозг._reply_lock = threading.RLock()
    мозг._history = deque()
    for номер in range(реплики // 2):
        мозг._history.append({"role": "user", "content": f"Я купил новый монитор номер {номер}, он большой и удобный для работы."})
        мозг._history.append({"role": "assistant", "content": "Поздравляю, хороший выбор."})
    мозг._undigested = реплики
    мозг.did_ask = False
    return мозг


class DigestFailureTests(unittest.TestCase):
    def test_a_failed_request_puts_the_talk_back_in_line(self):
        мозг = _мозг(4)
        with mock.patch.object(brain_module.Brain, "_ask_plainly",
                               side_effect=TimeoutError("облако молчит")), \
                mock.patch.object(memory, "load_facts", return_value=[]):
            with self.assertRaises(TimeoutError):
                мозг.digest()
        self.assertEqual(мозг._undigested, 4)

    def test_new_talk_during_the_failure_is_kept_too(self):
        мозг = _мозг(4)

        def падает(*a, **k):
            # Пока облако думало, прошёл ещё один обмен репликами.
            мозг._history.append({"role": "user", "content": "ещё"})
            мозг._history.append({"role": "assistant", "content": "да"})
            мозг._undigested += 2
            raise ConnectionError("сеть")

        with mock.patch.object(brain_module.Brain, "_ask_plainly", side_effect=падает), \
                mock.patch.object(memory, "load_facts", return_value=[]):
            with self.assertRaises(ConnectionError):
                мозг.digest()
        self.assertEqual(мозг._undigested, 6)


# --- 2. Память: номера по снимку и ручная правка -------------------------


class MemoryRaceTests(unittest.TestCase):
    def setUp(self):
        memory.MEMORY_PATH.unlink(missing_ok=True)
        self.addCleanup(memory.MEMORY_PATH.unlink, True)

    def _факты(self, *тексты):
        memory.save_facts([{"text": т, "weight": 2} for т in тексты], [])

    def test_ids_follow_the_snapshot_the_model_saw(self):
        self._факты("Играет в шахматы", "Живёт у моря", "Любит кофе")
        снимок = [f["text"] for f in memory.load_facts()]
        # Пока модель думала, в начало памяти встал новый факт.
        self._факты("Завёл кота", "Играет в шахматы", "Живёт у моря", "Любит кофе")
        итог = memory.apply_changes(
            [{"op": "update", "id": 2, "text": "Живёт у озера"}], seen=снимок)
        self.assertEqual(итог["updated"], [["Живёт у моря", "Живёт у озера"]])
        тексты = [f["text"] for f in memory.load_facts()]
        self.assertEqual(тексты, ["Завёл кота", "Играет в шахматы", "Живёт у озера", "Любит кофе"])

    def test_a_fact_removed_meanwhile_is_not_hit_by_mistake(self):
        self._факты("Играет в шахматы", "Живёт у моря")
        снимок = [f["text"] for f in memory.load_facts()]
        self._факты("Живёт у моря")  # «Играет в шахматы» человек стёр руками
        итог = memory.apply_changes([{"op": "remove", "id": 1}], seen=снимок)
        self.assertEqual(итог, {})
        self.assertEqual([f["text"] for f in memory.load_facts()], ["Живёт у моря"])

    def test_hand_edit_of_an_old_page_keeps_what_came_later(self):
        self._факты("Играет в шахматы", "Живёт у моря")
        открыл = memory.as_text()
        # После открытия страницы разбор разговора дописал факт.
        memory.apply_changes([{"op": "add", "text": "Завёл кота"}])
        memory.from_text("Играет в шахматы\nЖивёт у озера", base=открыл)
        self.assertEqual([f["text"] for f in memory.load_facts()],
                         ["Играет в шахматы", "Живёт у озера", "Завёл кота"])

    def test_hand_edit_without_base_is_as_before(self):
        self._факты("Играет в шахматы", "Живёт у моря")
        memory.from_text("Живёт у моря")
        self.assertEqual([f["text"] for f in memory.load_facts()], ["Живёт у моря"])


# --- 3. Запись файлов и битые файлы ---------------------------------------


class SafeFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="truba-safe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_file_is_written_whole_and_no_part_is_left(self):
        путь = self.tmp / "a.json"
        safe_files.write_text(путь, '{"a": 1}')
        self.assertEqual(путь.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.json"])

    def test_a_failed_write_leaves_the_old_file_as_it_was(self):
        путь = self.tmp / "a.json"
        путь.write_text("старое", encoding="utf-8")
        with mock.patch.object(safe_files.os, "replace", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                safe_files.write_text(путь, "новое")
        self.assertEqual(путь.read_text(encoding="utf-8"), "старое")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.json"])

    def test_a_broken_memory_file_is_put_aside_not_overwritten(self):
        memory.MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        memory.MEMORY_PATH.write_text('{"facts": [{"text": "обрыв', encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in memory.MEMORY_PATH.parent.glob(
            memory.MEMORY_PATH.name + ".broken-*")])
        self.assertEqual(memory.load_facts(), [])
        отложенные = list(memory.MEMORY_PATH.parent.glob(memory.MEMORY_PATH.name + ".broken-*"))
        self.assertEqual(len(отложенные), 1)
        self.assertIn("обрыв", отложенные[0].read_text(encoding="utf-8"))
        self.assertFalse(memory.MEMORY_PATH.exists())

    def test_a_broken_settings_file_is_put_aside(self):
        было = settings.SETTINGS_PATH.read_bytes() if settings.SETTINGS_PATH.exists() else None

        def вернуть():
            for p in settings.SETTINGS_PATH.parent.glob(settings.SETTINGS_PATH.name + ".broken-*"):
                p.unlink()
            if было is None:
                settings.SETTINGS_PATH.unlink(missing_ok=True)
            else:
                settings.SETTINGS_PATH.write_bytes(было)

        self.addCleanup(вернуть)
        settings.SETTINGS_PATH.write_text('{"mic_name": "обрыв', encoding="utf-8")
        данные = settings.load_settings()
        self.assertEqual(данные["mic_name"], config.MIC_NAME)
        self.assertTrue(list(settings.SETTINGS_PATH.parent.glob(
            settings.SETTINGS_PATH.name + ".broken-*")))


# --- 4. Обновление: откат и убранные файлы --------------------------------


class UpdateFilesTests(ОбщиеУсловия):
    def test_a_failed_update_takes_its_new_files_away(self):
        self.smoke.return_value = False
        итог = self._ставить({"core/phone.py": "новая\n", "core/новый.py": "новый модуль\n"})
        self.assertFalse(итог["ok"])
        self.assertTrue(итог.get("rolled_back"))
        self.assertFalse((self.root / "core" / "новый.py").exists())
        self.assertEqual((self.root / "core" / "phone.py").read_text(encoding="utf-8"),
                         "старая версия\n")

    def test_a_file_dropped_from_the_release_goes_away(self):
        первый = self._ставить({"core/phone.py": "1\n", "core/старый.py": "был\n"})
        self.assertTrue(первый["ok"], первый)
        (self.root / "core" / "своё.py").write_text("не из выпуска\n", encoding="utf-8")
        второй = self._ставить({"core/phone.py": "2\n"})
        self.assertTrue(второй["ok"], второй)
        self.assertFalse((self.root / "core" / "старый.py").exists())
        # Своё у человека — не из выпуска, его не трогаем никогда.
        self.assertTrue((self.root / "core" / "своё.py").exists())

    def test_without_a_list_of_the_last_release_nothing_is_removed(self):
        # Первое обновление после установки из ZIP: списка ещё нет.
        итог = self._ставить({"core/phone.py": "новая\n"})
        self.assertTrue(итог["ok"], итог)
        self.assertTrue((self.root / "ui" / "window.py").exists())

    def test_a_failed_update_brings_a_removed_file_back(self):
        self._ставить({"core/phone.py": "1\n", "core/старый.py": "был\n"})
        self.smoke.return_value = False
        итог = self._ставить({"core/phone.py": "2\n"})
        self.assertFalse(итог["ok"])
        self.assertEqual((self.root / "core" / "старый.py").read_text(encoding="utf-8"), "был\n")


if __name__ == "__main__":
    unittest.main()
