"""Сколько стоит разговор: токены, деньги, три периода. Без сети.

Хозяин 26 сентября попросил вывести в Панель расход за сеанс, за день и за
неделю, а если получится — с деньгами. Считаем это здесь на подставных
записях: в сеть не ходим ни разу, кеш цен и курса подменяем файлами (их
пути уже перевёл tests/test_data_guard.py).
"""

import json
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import usage

ЛУНА = "openai/gpt-6-luna"
ФЛЕШ = "deepseek/deepseek-flash"


def _записать(at, **поля):
    """Кладёт строку в журнал так же, как её кладёт мозг через record."""
    if isinstance(at, datetime):
        at = at.isoformat()
    запись = {"at": at, "calls": 1, "cached": 0}
    запись.update(поля)
    usage.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with usage.USAGE_PATH.open("a", encoding="utf-8") as файл:
        файл.write(json.dumps(запись, ensure_ascii=False) + "\n")


class UsageBase(unittest.TestCase):
    """Каждый тест пишет расход в свою временную папку.

    Пути модуля подменяются ещё в tests/test_data_guard.py, но он попадает
    в прогон только через `unittest discover`. Этот класс закрывает и одиночный
    запуск одного файла: иначе тест дописал бы в живой usage.jsonl рядом
    с config.py.
    """

    def setUp(self):
        self._папка = Path(tempfile.mkdtemp(prefix="truba-usage-"))
        self.addCleanup(shutil.rmtree, self._папка, True)
        self._было = (usage.USAGE_PATH, usage.OPENROUTER_PRICES_PATH,
                      usage.USD_RUB_PATH)

        def вернуть():
            (usage.USAGE_PATH, usage.OPENROUTER_PRICES_PATH,
             usage.USD_RUB_PATH) = self._было

        self.addCleanup(вернуть)
        usage.USAGE_PATH = self._папка / "usage.jsonl"
        usage.OPENROUTER_PRICES_PATH = self._папка / "openrouter_prices.json"
        usage.USD_RUB_PATH = self._папка / "usd_rub.json"

    def _сводка(self, session, now=None):
        return usage.summary(session, now)


class ЗаписьTests(UsageBase):
    def test_record_writes_a_line_we_can_read_back(self):
        usage.record({"prompt": 3120, "cached": 2800, "completion": 45,
                      "calls": 1, "model": ЛУНА})
        usage.record({"prompt": 400, "cached": 0, "completion": 20,
                      "calls": 1, "model": ЛУНА, "note": "память"})
        строки = usage.USAGE_PATH.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(строки), 2)
        первая = json.loads(строки[0])
        self.assertEqual(первая["model"], ЛУНА)
        self.assertEqual((первая["prompt"], первая["cached"],
                          первая["completion"], первая["calls"]),
                         (3120, 2800, 45, 1))
        self.assertNotIn("note", первая)
        self.assertEqual(json.loads(строки[1])["note"], "память")
        datetime.fromisoformat(первая["at"])  # время разбирается

    def test_record_never_breaks_over_junk(self):
        usage.record(None)
        usage.record({"prompt": "много", "cached": -5, "completion": None,
                      "model": 12})
        запись = json.loads(usage.USAGE_PATH.read_text(encoding="utf-8").strip())
        self.assertEqual(запись["prompt"], 0)
        self.assertEqual(запись["cached"], 0)
        self.assertEqual(запись["model"], "12")

    def test_broken_lines_do_not_stop_the_rest(self):
        usage.record({"prompt": 100, "completion": 1, "model": ЛУНА})
        with usage.USAGE_PATH.open("a", encoding="utf-8") as файл:
            файл.write("не json вовсе\n")
        сейчас = datetime.now()
        сводка = self._сводка(сейчас - timedelta(hours=1), сейчас)
        self.assertEqual(сводка["periods"]["session"]["prompt"], 100)


class ПериодыTests(UsageBase):
    def test_session_today_and_week_split_records_by_time(self):
        сейчас = datetime.now()
        утро = сейчас.replace(hour=8, minute=0, second=0, microsecond=0)
        # Пульт подняли в 8 утра — запись за час до этого в сеанс не входит,
        # но в «сегодня» и «неделю» входит.
        _записать(утро - timedelta(hours=1), model=ЛУНА, prompt=1000, completion=10)
        _записать(утро, model=ЛУНА, prompt=2000, completion=20)
        _записать(утро - timedelta(days=3), model=ЛУНА, prompt=3000, completion=30)
        _записать(утро - timedelta(days=10), model=ЛУНА, prompt=4000, completion=40)

        периоды = self._сводка(утро, сейчас)["periods"]
        self.assertEqual(периоды["session"]["prompt"], 2000)
        self.assertEqual(периоды["session"]["calls"], 1)
        self.assertEqual(периоды["today"]["prompt"], 3000)
        self.assertEqual(периоды["week"]["prompt"], 6000)

    def test_old_record_never_enters_the_week(self):
        сейчас = datetime.now()
        _записать(сейчас - timedelta(days=10), model=ЛУНА, prompt=4000, completion=1)
        _записать(сейчас - timedelta(days=3), model=ЛУНА, prompt=3000, completion=2)
        периоды = self._сводка(сейчас, сейчас)["periods"]
        self.assertEqual(периоды["week"]["prompt"], 3000)
        self.assertEqual(периоды["session"]["prompt"], 0)
        self.assertEqual(периоды["session"]["calls"], 0)

    def test_empty_file_gives_zeroes_not_a_crash(self):
        периоды = self._сводка(datetime.now())["periods"]
        for имя in ("session", "today", "week"):
            self.assertEqual(периоды[имя]["prompt"], 0)
            self.assertEqual(периоды[имя]["usd"], 0.0)
            self.assertFalse(периоды[имя]["unpriced"])
            self.assertEqual(периоды[имя]["models"], [])


class ДеньгиTests(UsageBase):
    def test_luna_prices(self):
        # 1 млн входа, из них 900 тыс. из кеша, 100 тыс. выхода:
        #   100 000 * 0.10 + 900 000 * 0.01 + 100 000 * 0.50
        # = 10 000 + 9 000 + 50 000 = 69 000 на миллион → $0.069
        сейчас = datetime.now()
        _записать(сейчас, model=ЛУНА, prompt=1_000_000, cached=900_000,
                  completion=100_000)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        self.assertAlmostEqual(период["usd"], 0.069, places=6)
        self.assertEqual(период["cached"], 900_000)

    def test_no_cache_costs_full_price(self):
        сейчас = datetime.now()
        _записать(сейчас, model=ЛУНА, prompt=1_000_000, completion=1_000_000)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        self.assertAlmostEqual(период["usd"], 0.10 + 0.50, places=6)

    def test_deepseek_price_doubles_only_in_peak_hours(self):
        # Часы пика DeepSeek: 01:00–04:00 и 06:00–10:00 UTC, пн–пт.
        пик = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)   # понедельник
        self.assertTrue(usage._в_пике(пик))
        self.assertTrue(usage._в_пике(datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)))
        self.assertFalse(usage._в_пике(datetime(2026, 9, 21, 0, 30, tzinfo=timezone.utc)))
        self.assertFalse(usage._в_пике(datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)))
        # Суббота в те же часы — уже не пик.
        суббота = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(суббота.weekday(), 5)
        self.assertFalse(usage._в_пике(суббота))

        # В price_for момент с часовым поясом трактуется как он и есть —
        # так проверка не зависит от того, в каком поясе стоит компьютер.
        обычная = usage.price_for(ФЛЕШ, datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc))
        дорогая = usage.price_for(ФЛЕШ, datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc))
        self.assertAlmostEqual(обычная["input"], 0.15, places=6)
        self.assertAlmostEqual(обычная["cached"], 0.003, places=6)
        self.assertAlmostEqual(дорогая["input"], 0.30, places=6)
        self.assertAlmostEqual(дорогая["cached"], 0.006, places=6)
        self.assertAlmostEqual(дорогая["output"], 1.20, places=6)

    def test_peak_is_counted_by_the_time_of_the_record(self):
        # Одна и та же запись в 02:00 UTC понедельника стоит вдвое дороже
        # той же записи в час ночи вне пика.
        пик = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)
        ночь = datetime(2026, 9, 21, 0, 30, tzinfo=timezone.utc)
        _записать(пик.isoformat(), model=ФЛЕШ, prompt=1_000_000, completion=0)
        _записать(ночь.isoformat(), model=ФЛЕШ, prompt=1_000_000, completion=0)
        # «Сейчас» — на следующий день, а не настоящее: 28.09 тест упал, когда
        # 21.09 выпал из недели.
        период = self._сводка(ночь - timedelta(hours=1), datetime(2026, 9, 22, 12, 0))
        # 0.15 ночью + 0.30 в пик.
        self.assertAlmostEqual(период["periods"]["week"]["usd"], 0.45, places=6)

    def test_openai_price_never_doubles(self):
        # Пик — только DeepSeek; у Луны цена одна и та же.
        первое = usage.price_for(ЛУНА, datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc))
        второе = usage.price_for(ЛУНА, datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(первое, второе)
        self.assertAlmostEqual(первое["output"], 0.50, places=6)


class МоделиTests(UsageBase):
    def test_openrouter_price_comes_from_the_cached_file(self):
        usage.OPENROUTER_PRICES_PATH.write_text(json.dumps({
            "at": "2026-09-26T08:00:00",
            # В кеше цены уже за миллион токенов, как и в config.PRICES.
            "prices": {"deepseek/deepseek-v4-flash": {
                "input": 0.15, "cached": 0.003, "output": 0.60}},
        }, ensure_ascii=False), encoding="utf-8")
        сейчас = datetime.now()
        # Brain.model для провайдера openrouter — «openrouter/» плюс id модели.
        _записать(сейчас, model="openrouter/deepseek/deepseek-v4-flash",
                  prompt=1_000_000, cached=1_000_000, completion=0)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        self.assertAlmostEqual(период["usd"], 0.003, places=6)
        self.assertFalse(период["unpriced"])
        self.assertEqual(период["models"][0]["model"],
                         "openrouter/deepseek/deepseek-v4-flash")

    def test_unknown_model_is_flagged_and_left_out_of_the_sum(self):
        сейчас = datetime.now()
        _записать(сейчас, model="openrouter/какая-то-незнакомая", prompt=5000,
                  completion=500)
        _записать(сейчас, model=ЛУНА, prompt=1_000_000, completion=0)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        self.assertTrue(период["unpriced"])
        self.assertAlmostEqual(период["usd"], 0.10, places=6)
        безценная = [m for m in период["models"] if m["usd"] is None]
        self.assertEqual(len(безценная), 1)
        self.assertEqual(безценная[0]["model"], "openrouter/какая-то-незнакомая")
        # Токены безценной модели в периоде остаются: их видно.
        self.assertEqual(период["prompt"], 1_005_000)

    def test_models_are_split_and_the_dearest_comes_first(self):
        # Суббота днём — вне часов пика: 28.09 утром в понедельник тест
        # получил двойную цену Флеша и упал.
        сейчас = datetime(2026, 9, 26, 15, 0)
        _записать(сейчас, model=ФЛЕШ, prompt=1_000_000, completion=0)
        _записать(сейчас, model=ЛУНА, prompt=1_000_000, completion=1_000_000)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        self.assertEqual([m["model"] for m in период["models"]], [ЛУНА, ФЛЕШ])
        self.assertEqual(период["models"][0]["completion"], 1_000_000)
        self.assertAlmostEqual(период["models"][1]["usd"], 0.15, places=6)

    def test_cached_never_exceeds_prompt_in_the_money(self):
        сейчас = datetime.now()
        _записать(сейчас, model=ЛУНА, prompt=1_000_000, cached=1_500_000,
                  completion=0)
        период = self._сводка(сейчас, сейчас)["periods"]["today"]
        # Кеш не может стоить больше всего входа: лишнее не вычитается в минус.
        self.assertAlmostEqual(период["usd"], 0.01, places=6)
        self.assertEqual(период["cached"], 1_000_000)


class ОбрезкаTests(UsageBase):
    def test_old_records_are_forgotten_and_new_ones_kept(self):
        сейчас = datetime.now()
        usage.USAGE_PATH.write_text("\n".join([
            json.dumps({"at": (сейчас - timedelta(days=40)).isoformat(timespec="seconds"),
                        "model": ЛУНА, "prompt": 1, "cached": 0,
                        "completion": 0, "calls": 1}),
            json.dumps({"at": (сейчас - timedelta(days=34)).isoformat(timespec="seconds"),
                        "model": ЛУНА, "prompt": 2, "cached": 0,
                        "completion": 0, "calls": 1}),
            json.dumps({"at": сейчас.isoformat(timespec="seconds"),
                        "model": ЛУНА, "prompt": 4, "cached": 0,
                        "completion": 0, "calls": 1}),
        ]) + "\n", encoding="utf-8")
        self.assertEqual(usage.prune(now=сейчас), 1)
        self.assertEqual([e["prompt"] for e in usage._читать_файл()], [2, 4])
        период = self._сводка(сейчас - timedelta(hours=1), сейчас)["periods"]
        self.assertEqual(период["session"]["prompt"], 4)
        # Записи 34-дневной давности в неделю не входят, но файл она остаётся.
        self.assertEqual(период["week"]["prompt"], 4)

    def test_pruning_nothing_keeps_the_file_whole(self):
        usage.record({"prompt": 7, "completion": 1, "model": ЛУНА})
        self.assertEqual(usage.prune(), 0)
        self.assertEqual(len(usage._читать_файл()), 1)
        self.assertFalse(usage.USAGE_PATH.with_name(
            usage.USAGE_PATH.name + ".new").exists())

    def test_unreadable_time_is_treated_as_the_oldest(self):
        usage.USAGE_PATH.write_text(json.dumps(
            {"at": "не дата", "model": ЛУНА, "prompt": 9, "cached": 0,
             "completion": 0, "calls": 1}) + "\n", encoding="utf-8")
        self.assertEqual(usage.prune(), 1)
        self.assertEqual(usage._читать_файл(), [])


class БезСетиTests(UsageBase):
    """Ни одного настоящего запроса: сеть подменена заглушкой, которая орёт."""

    def setUp(self):
        super().setUp()
        self.запросы = []

    def _заглушка(self, url):
        self.запросы.append(url)
        raise AssertionError("в тестах сети быть не должно: " + url)

    def test_caches_are_not_fetched_when_they_are_fresh(self):
        from unittest import mock

        usage.OPENROUTER_PRICES_PATH.write_text(json.dumps(
            {"at": "2026-09-26T08:00:00", "prices": {}}), encoding="utf-8")
        usage.USD_RUB_PATH.write_text(json.dumps(
            {"at": "2026-09-26T08:00:00", "rub": 95.0}), encoding="utf-8")
        with mock.patch.object(usage, "_скачать", self._заглушка):
            usage.refresh_prices()
            self.assertEqual(usage.usd_rub(), 95.0)
        self.assertEqual(self.запросы, [])

    def test_live_catalog_is_parsed_into_the_cache(self):
        from unittest import mock

        каталог = {"data": [
            {"id": "deepseek/deepseek-v4-flash",
             "pricing": {"prompt": "0.00000015", "completion": "0.0000006",
                         "input_cache_read": "0.000000003"}},
            {"id": "пустая/без-цены", "pricing": {"prompt": None,
                                                  "completion": None}},
            "мусор",
        ]}
        with mock.patch.object(usage, "_скачать", lambda url: каталог):
            usage.refresh_prices()
        # Каталог отдаёт цены за токен строкой — в кеше они за миллион.
        self.assertEqual(list(usage.openrouter_prices()),
                         ["deepseek/deepseek-v4-flash"])
        цена = usage.openrouter_prices()["deepseek/deepseek-v4-flash"]
        self.assertAlmostEqual(цена["input"], 0.15, places=6)
        self.assertAlmostEqual(цена["cached"], 0.003, places=6)
        self.assertAlmostEqual(цена["output"], 0.60, places=6)
        # Дата обновления цен — сегодняшняя. Было зашито «26.09.26», и с
        # полуночи 27 сентября тест падал сам по себе.
        self.assertEqual(usage.summary(datetime.now())["prices"]["openrouter"],
                         datetime.now().strftime("%d.%m.%y"))

    def test_catalog_without_cache_price_falls_back_to_input(self):
        from unittest import mock

        каталог = {"data": [{"id": "x/y", "pricing": {
            "prompt": "0.000001", "completion": "0.000002"}}]}
        with mock.patch.object(usage, "_скачать", lambda url: каталог):
            usage.refresh_prices()
        self.assertAlmostEqual(usage.openrouter_prices()["x/y"]["cached"],
                               1.0, places=6)

    def test_cbr_rate_is_taken_from_valute_usd(self):
        from unittest import mock

        ответы = {usage.CBR_URL: {"Valute": {"USD": {"Value": 92.51,
                                                      "Nominal": 1}}}}
        with mock.patch.object(usage, "_скачать", lambda url: ответы[url]):
            usage.refresh_prices()
        self.assertAlmostEqual(usage.usd_rub(), 92.51, places=2)

    def test_dead_network_leaves_the_previous_cache_in_place(self):
        from unittest import mock

        usage.OPENROUTER_PRICES_PATH.write_text(json.dumps(
            {"at": "2026-09-25T08:00:00", "prices": {}}), encoding="utf-8")
        usage.USD_RUB_PATH.write_text(json.dumps(
            {"at": "2026-09-25T08:00:00", "rub": 90.0}), encoding="utf-8")
        # Кеш вчерашний — за ним и пойдём в сеть, и не дойдём.
        import os
        вчера = time.time() - 2 * 24 * 3600
        for путь in (usage.OPENROUTER_PRICES_PATH, usage.USD_RUB_PATH):
            os.utime(путь, (вчера, вчера))
        with mock.patch.object(usage, "_скачать", self._заглушка):
            usage.refresh_prices()
        # Панель продолжит считать рубли по прошлому курсу.
        self.assertAlmostEqual(usage.usd_rub(), 90.0, places=2)
        self.assertEqual(self.запросы, [usage.OPENROUTER_URL, usage.CBR_URL])

    def test_summary_without_caches_is_still_readable(self):
        from unittest import mock

        with mock.patch.object(usage, "_скачать", self._заглушка):
            сводка = usage.summary(datetime.now())
        self.assertIsNone(сводка["rub"])
        self.assertIsNone(сводка["prices"]["openrouter"])
        self.assertEqual(сводка["prices"]["openai"], "26.09.2026")
        self.assertEqual(set(сводка["periods"]), {"session", "today", "week"})

    def test_only_the_two_known_addresses_are_ever_asked(self):
        self.assertEqual(usage.OPENROUTER_URL,
                         "https://openrouter.ai/api/v1/models")
        self.assertEqual(usage.CBR_URL,
                         "https://www.cbr-xml-daily.ru/daily_json.js")
        self.assertEqual(usage.NET_TIMEOUT, 8.0)


class ПультTests(UsageBase):
    """Пульт пишет событие мозга в журнал расхода и отдаёт сводку."""

    def _runtime(self):
        import threading
        from collections import deque
        from ui.web_runtime import WebRuntime

        runtime = object.__new__(WebRuntime)
        runtime._lock = threading.Lock()
        runtime._events = deque(maxlen=10)
        runtime._ids = iter(range(1, 100))
        runtime._overview = {"heard": 0, "ignored": 0, "interrupted": 0,
                             "spoken": 0, "last_first_sound": None,
                             "last_stt_time": None,
                             "last_ignored_reason": None, "last_activity": None}
        runtime._log_path = self._папка / "session.log"
        return runtime

    def test_tokens_event_reaches_the_usage_journal(self):
        runtime = self._runtime()
        runtime._remember("tokens", {"prompt": 3120, "cached": 2800,
                                     "completion": 45, "calls": 1,
                                     "model": ЛУНА})
        записи = usage._читать_файл()
        self.assertEqual(len(записи), 1)
        self.assertEqual(записи[0]["prompt"], 3120)
        self.assertEqual(записи[0]["model"], ЛУНА)

    def test_another_event_does_not_get_into_the_journal(self):
        runtime = self._runtime()
        runtime._remember("heard", {"text": "привет", "seconds": 1.0})
        self.assertEqual(usage._читать_файл(), [])

    def test_summary_counts_from_the_start_of_the_session(self):
        runtime = self._runtime()
        runtime.started_at = datetime.now()
        usage.record({"prompt": 500, "completion": 10, "model": ЛУНА})
        периоды = runtime.usage_summary()["periods"]
        self.assertEqual(периоды["session"]["prompt"], 500)
        self.assertEqual(периоды["today"]["prompt"], 500)


if __name__ == "__main__":
    unittest.main()
