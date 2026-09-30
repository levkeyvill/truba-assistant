"""Таймеры и напоминания: хранилище, будильник, инструменты, срабатывание.

Всё во временной папке, время — подменой (`now` параметром), потоки будильника
живут доли секунды, ни звука, ни телефона, ни облака. Проверяется одно: что
напоминание, поставленное моделью, действительно позвонит хозяину — и что
сработавшее не позвонит дважды.

Помощники от соседних тестов не импортируются (ГРАБЛИ: импорт соседнего
теста грузит его вторым экземпляром и запускает его `test_data_guard` с
другой временной папкой).
"""

import atexit
import itertools
import json
import shutil
import tempfile
import threading
import time
import unittest
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import config
from core import abilities, hands, reminders
from core.brain import Brain
from ui.web_runtime import WebRuntime

SAFE = Path(tempfile.mkdtemp(prefix="truba-reminders-"))
# Общая папка на весь файл: отдельные подпапки каждый тест убирает сам, а
# корневую — на выходе из процесса (как в `tests/test_data_guard.py`).
atexit.register(shutil.rmtree, SAFE, True)


def _now() -> datetime:
    return datetime(2026, 9, 30, 12, 0, 0).astimezone()


class _Тест(unittest.TestCase):
    """Общая подмена: свой файл, своё время, никакого наследия."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=str(SAFE)))
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = mock.patch.object(
            reminders, "PATH", self.folder / "reminders.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Будильник берёт время отсюда: подмена нужна и ему, а не только
        # вызовам с `now`.
        self.clock = mock.Mock(return_value=_now())
        patcher2 = mock.patch.object(reminders, "local_now", self.clock)
        patcher2.start()
        self.addCleanup(patcher2.stop)
        # Счётчик id живёт в модуле (он и должен жить в процессе, а не в
        # файле), поэтому между тестами его возвращаем к нулю — иначе второй
        # тест начал бы не с `r1`.
        self.addCleanup(setattr, reminders, "_LAST_ID", 0)
        reminders._LAST_ID = 0
        self.now = _now()


class Хранилище(_Тест):
    """add / cancel / pending / due_now и отказы."""

    def test_запись_переживает_перезапуск(self):
        # «Перезапуск» — это новое чтение файла: то же самое делает будильник
        # после того, как пульт закрыли и открыли заново.
        first = reminders.add(self.now + timedelta(minutes=20),
                              "вытащить пиццу", now=self.now)
        self.assertEqual(first["id"], "r1")
        self.assertEqual(first["kind"], "reminder")
        self.assertEqual(first["minutes"], 0)
        # Всё, что нужно будильнику, лежит в самой записи: он не ходит за
        # моделью и не помнит ничего сверх файла.
        for поле in ("id", "due", "created", "text", "say", "kind", "minutes"):
            self.assertIn(поле, first)
        again = reminders.pending()
        self.assertEqual([one["id"] for one in again], ["r1"])
        self.assertEqual(again[0]["text"], "вытащить пиццу")
        self.assertIsNotNone(reminders.parse(again[0]["due"]).tzinfo)

    def test_id_идут_по_порядку_и_не_переиспользуются(self):
        first = reminders.add(self.now + timedelta(minutes=1), now=self.now)
        second = reminders.add(self.now + timedelta(minutes=2), now=self.now)
        self.assertEqual([first["id"], second["id"]], ["r1", "r2"])
        reminders.cancel("r1")
        third = reminders.add(self.now + timedelta(minutes=3), now=self.now)
        # Освободившийся r1 лучше не выдавать: в пульте (часть B) и в истории
        # такой id могли бы уже показывать чему-то другому.
        self.assertEqual(third["id"], "r3")

    def test_pending_сортирован_по_времени(self):
        reminders.add(self.now + timedelta(minutes=30), "позже", now=self.now)
        reminders.add(self.now + timedelta(minutes=5), "раньше", now=self.now)
        reminders.add(self.now + timedelta(minutes=15), "середина", now=self.now)
        self.assertEqual([one["text"] for one in reminders.pending()],
                         ["раньше", "середина", "позже"])

    def test_due_now_отдаёт_только_пришедшие(self):
        # Запись «пора» кладём прямо в файл: `add` такую и не примет (время в
        # прошлом — отказ), а будильник встречает и записи, оставшиеся с
        # прошлого запуска, когда срок уже прошёл.
        reminders.add(self.now + timedelta(minutes=5), "ещё нет", now=self.now)
        reminders.PATH.write_text(json.dumps([
            {"id": "r1", "due": (self.now - timedelta(minutes=1)).isoformat(),
             "text": "пора", "kind": "reminder", "minutes": 0},
            {"id": "r2", "due": (self.now + timedelta(minutes=5)).isoformat(),
             "text": "ещё нет", "kind": "reminder", "minutes": 0},
        ]), encoding="utf-8")
        self.assertEqual([one["text"] for one in reminders.due_now(self.now)],
                         ["пора"])
        # Ровно в срок — тоже пора: будильник не должен ждать лишней секунды.
        later = self.now + timedelta(minutes=5)
        self.assertEqual([one["text"] for one in reminders.due_now(later)],
                         ["пора", "ещё нет"])

    def test_отмена_по_идентификатору(self):
        first = reminders.add(self.now + timedelta(minutes=1), "первое", now=self.now)
        reminders.add(self.now + timedelta(minutes=2), "второе", now=self.now)
        gone = reminders.cancel(first["id"])
        self.assertEqual([one["text"] for one in gone], ["первое"])
        self.assertEqual([one["text"] for one in reminders.pending()], ["второе"])

    def test_отмена_всего(self):
        for i in range(3):
            reminders.add(self.now + timedelta(minutes=i + 1), f"запись {i}",
                          now=self.now)
        self.assertEqual(len(reminders.cancel("all")), 3)
        self.assertEqual(reminders.pending(), [])

    def test_отмена_несуществующего_пуста(self):
        reminders.add(self.now + timedelta(minutes=1), "живая", now=self.now)
        self.assertEqual(reminders.cancel("r99"), [])
        # Живая запись при этом не тронута: промах по id не должен чистить всё.
        self.assertEqual(len(reminders.pending()), 1)

    def test_прошедшее_время_отказ(self):
        with self.assertRaises(ValueError) as caught:
            reminders.add(self.now - timedelta(minutes=1), "поздно", now=self.now)
        self.assertIn("прошло", str(caught.exception))
        self.assertEqual(reminders.pending(), [])

    def test_дальше_месяца_отказ(self):
        with self.assertRaises(ValueError):
            reminders.add(self.now + timedelta(days=reminders.MAX_DAYS + 1),
                          "далеко", now=self.now)
        # Ровно месяц — можно: граница не должна отказывать.
        reminders.add(self.now + timedelta(days=reminders.MAX_DAYS),
                      "на границе", now=self.now)
        self.assertEqual(len(reminders.pending()), 1)

    def test_больше_двадцати_отказ(self):
        for i in range(reminders.MAX_ACTIVE):
            reminders.add(self.now + timedelta(minutes=i + 1), f"запись {i}",
                          now=self.now)
        with self.assertRaises(ValueError):
            reminders.add(self.now + timedelta(minutes=99), "лишняя", now=self.now)
        self.assertEqual(len(reminders.pending()), reminders.MAX_ACTIVE)

    def test_битый_файл_не_роняет_чтение(self):
        reminders.PATH.write_text("{это не json", encoding="utf-8")
        self.assertEqual(reminders.pending(), [])
        self.assertEqual(reminders.due_now(self.now), [])
        # И запись в такой файл идёт: старая битая не должна быть навсегда.
        reminders.add(self.now + timedelta(minutes=1), "новая", now=self.now)
        self.assertEqual(len(reminders.pending()), 1)

    def test_файл_пишется_через_временный(self):
        reminders.add(self.now + timedelta(minutes=1), "пицца", now=self.now)
        # `.part` после записи не остаётся: оборванная запись выглядела бы
        # как настоящее напоминание, если бы её увидел будильник.
        self.assertEqual(list(self.folder.glob("*.part")), [])
        on_disk = json.loads(reminders.PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk[0]["text"], "пицца")



    def test_фраза_при_срабатывании(self):
        # Своя фраза модели — как есть.
        self.assertEqual(reminders.phrase({"say": "Пицца!", "text": "пицца"}),
                         "Пицца!")
        # Без неё — «Напоминаю: …», а таймеру «Таймер на пять минут вышел.»
        # с числом словами: цифру синтез не прочитает.
        self.assertEqual(
            reminders.phrase({"say": "", "kind": "reminder", "text": "пицца"}),
            "Напоминаю: пицца.")
        self.assertEqual(
            reminders.phrase({"say": "", "kind": "timer", "minutes": 5}),
            "Таймер на пять минут вышел.")
        self.assertEqual(
            reminders.phrase({"say": "", "kind": "timer", "minutes": 20}),
            "Таймер на двадцать минут вышел.")
        # Вообще без слов — тоже осмысленно, а не пустая строка.
        self.assertEqual(reminders.phrase({"say": "", "kind": "timer"}),
                         "Таймер вышел.")
        self.assertEqual(reminders.phrase({"say": "", "kind": "reminder"}),
                         "Напоминаю.")

    def test_время_без_пояса_читается_как_местное(self):
        # Модель присылает «2026-09-30T17:00» без пояса. Считать его
        # московским молча нельзя: часы компьютера — UTC+5 (ГРАБЛИ).
        moment = reminders.parse("2026-09-30T17:00")
        self.assertEqual(moment.hour, 17)
        self.assertIsNotNone(moment.tzinfo)
        self.assertEqual(reminders.parse(moment), moment)

    def test_мусор_во_времени_не_роняет_будильник(self):
        # Запись с нечитаемым `due` не должна ни поднять будильник, ни уронить
        # его: сработать «неизвестно когда» — худшее, что можно сделать.
        reminders.PATH.write_text(
            json.dumps([{"id": "r1", "due": "не время", "text": "мусор"}]),
            encoding="utf-8")
        self.assertEqual(reminders.due_now(self.now), [])
        self.assertEqual(len(reminders.pending()), 1)


class Будильник(_Тест):
    """`Alarm` с подменённым временем: ни одного настоящего потока."""

    def test_срабатывает_один_раз_и_убирает_запись(self):
        fired = []
        alarm = reminders.Alarm(
            lambda запись, опоздание: fired.append((запись["id"], опоздание)),
            now=lambda: self.now + timedelta(minutes=5))
        reminders.add(self.now + timedelta(minutes=5), "пицца", now=self.now)
        self.assertEqual(len(alarm.check()), 1)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0], "r1")
        # Запись ушла из файла ДО вызова: второй раз она не придёт, даже если
        # пульт упадёт в ту же секунду.
        self.assertEqual(reminders.pending(), [])
        self.assertEqual(alarm.check(), [])
        self.assertEqual(len(fired), 1)

    def test_опоздание_считается(self):
        alarm = reminders.Alarm(lambda запись, опоздание: None, now=self.now)
        reminders.add(self.now + timedelta(minutes=1), "пицца", now=self.now)
        # Ровно в срок — опоздания нет, а не минус двадцать секунд.
        self.assertEqual(alarm.check(self.now + timedelta(minutes=1))[0][1], 0.0)
        reminders.add(self.now + timedelta(minutes=1), "чайник", now=self.now)
        self.assertEqual(alarm.check(self.now + timedelta(minutes=3))[0][1], 120.0)

    def test_пропущенное_пока_пульт_был_выключен(self):
        # Напоминание, срок которого прошёл при выключенном пульте, срабатывает
        # один раз и с опозданием — на том и пометка «пока меня не было».
        fired = []
        alarm = reminders.Alarm(
            lambda запись, опоздание: fired.append(опоздание), now=self.now)
        reminders.add(self.now + timedelta(minutes=5), "пицца", now=self.now)
        later = self.now + timedelta(minutes=45)
        self.assertEqual(alarm.check(later)[0][1], 2400.0)
        self.assertGreaterEqual(fired[0], reminders.MISSED_AFTER)
        self.assertEqual(reminders.pending(), [])


    def test_сначала_пришедшие(self):
        # Будильник зовёт в порядке времени: короткий таймер не должен ждать,
        # пока дойдёт очередь длинного напоминания.
        alarm = reminders.Alarm(lambda *args: None, now=self.now)
        reminders.add(self.now + timedelta(minutes=30), "позже", now=self.now)
        reminders.add(self.now + timedelta(minutes=1), "раньше", now=self.now)
        self.assertEqual([one["text"] for one, _ in alarm.check(
            self.now + timedelta(minutes=60))], ["раньше", "позже"])

    def test_сбой_обработчика_не_убивает_будильник(self):
        # Телефон выключен, пульт падает — будильник обязан пережить это:
        # иначе второе напоминание не позвонит уже никогда.
        seen = []

        def зовёт(запись, опоздание):
            seen.append(запись["id"])
            raise RuntimeError("телефон сгорел")

        alarm = reminders.Alarm(зовёт, now=self.now)
        reminders.add(self.now + timedelta(minutes=1), "первое", now=self.now)
        reminders.add(self.now + timedelta(minutes=2), "второе", now=self.now)
        self.assertEqual(len(alarm.check(self.now + timedelta(minutes=5))), 2)
        self.assertEqual(seen, ["r1", "r2"])
        # Обе убраны из файла: сбой телефона не должен вернуть их в очередь.
        self.assertEqual(reminders.pending(), [])

    def test_файл_не_читается_пока_не_менялся(self):
        # Дёшево в простое: пока файл не трогали, диск не читается. Это тот же
        # довод, что в ГРАБЛИ про onnxruntime, крутящий четыре ядра вхолостую.
        alarm = reminders.Alarm(lambda *args: None, now=self.now)
        reminders.add(self.now + timedelta(minutes=60), "далеко", now=self.now)
        alarm.check(self.now)
        with mock.patch.object(reminders, "_read",
                               side_effect=AssertionError("зря на диск")) as read:
            for _ in range(5):
                self.assertEqual(alarm.check(self.now), [])
        read.assert_not_called()

    def test_поток_живёт_и_гасится(self):
        # Настоящий поток — но на доли секунды: TICK тут 0.01 с.
        fired = []
        alarm = reminders.Alarm(
            lambda запись, опоздание: fired.append(запись["id"]), tick=0.01,
            now=self.clock)
        reminders.add(self.now + timedelta(minutes=1), "пицца", now=self.now)
        alarm.start()
        try:
            self.clock.return_value = self.now + timedelta(minutes=1)
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            alarm.stop()
        self.assertEqual(fired, ["r1"])
        # `stop()` гасит поток: он не должен подняться заново.
        self.clock.return_value = self.now + timedelta(minutes=9)
        time.sleep(0.1)
        self.assertEqual(len(fired), 1)



def _ans(строка) -> dict:
    return json.loads(строка)


def _отказ(строка) -> str:
    """Текст отказа инструмента. Пустой — отказа не было."""
    return _ans(строка).get("error", "")


class Инструменты(_Тест):
    """Три инструмента модели: set / list / cancel."""

    def test_таймер_через_seconds(self):
        # «Поставь таймер на пять минут» — модель передаёт 300 секунд.
        events, on_event = _события()
        answer = _ans(hands.run_set_reminder(json.dumps({
            "seconds": 300, "kind": "timer",
            "because": "поставь таймер на пять минут",
        }), on_event))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["kind"], "timer")
        self.assertEqual(answer["id"], "r1")
        # Ответ — готовая фраза с числом словами: цифру синтез не прочитает.
        self.assertEqual(answer["text"], "Таймер на пять минут пошёл.")
        self.assertNotRegex(answer["text"], r"\d")
        self.assertEqual([вид for вид, _ in events], ["reminder"])
        self.assertEqual(events[0][1], "таймер: 5 мин")
        stored = reminders.pending()[0]
        self.assertEqual(stored["minutes"], 5)
        # Из `seconds` модель не выдумывает время: хранится ровно то, что она
        # посчитала, и будильнику этого достаточно.
        self.assertAlmostEqual(
            (reminders.parse(stored["due"]) - self.now).total_seconds(), 300,
            delta=2)

    def test_напоминание_через_at(self):
        # «Напомни в семь утра» — модель передаёт местное время без пояса.
        events, on_event = _события()
        at = (self.now + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
        answer = _ans(hands.run_set_reminder(json.dumps({
            "at": at, "kind": "reminder", "text": "вытащить пиццу",
            "say": "Пицца!", "because": "напомни вытащить пиццу",
        }), on_event))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["kind"], "reminder")
        # Фраза тоже словами: «17:00» синтез прочёл бы как «семнадцать ноль
        # ноль», а `speech_text` умеет «в семнадцать часов ровно».
        self.assertEqual(answer["text"], "Напомню в семнадцать часов ровно.")
        self.assertNotRegex(answer["text"], r"\d")
        stored = reminders.pending()[0]
        self.assertEqual(stored["text"], "вытащить пиццу")
        self.assertEqual(stored["say"], "Пицца!")
        # Строка в журнал — как у голосовых команд.
        self.assertEqual([вид for вид, _ in events], ["reminder"])
        self.assertIn("напоминание: ", events[0][1])
        self.assertIn("вытащить пиццу", events[0][1])

    def test_таймер_без_слов_проходит(self):
        # «Поставь таймер на десять минут» — о чём напоминать, тут не сказано,
        # и это нормально: таймеру текст не нужен.
        answer = _ans(hands.run_set_reminder(json.dumps({
            "seconds": 600, "kind": "timer"})))
        self.assertTrue(answer["ok"])
        self.assertEqual(reminders.pending()[0]["text"], "")

    def test_напоминание_без_текста_отказ(self):
        # «Напомни не о чём» — такого напоминания быть не должно: хозяин будет
        # ждать звонка, который не о чем.
        self.assertIn("не сказано", _отказ(hands.run_set_reminder(json.dumps({
            "seconds": 600, "kind": "reminder"}))))
        self.assertEqual(reminders.pending(), [])

    def test_плохой_json(self):
        for кусок in ("не json", "[1, 2]", '"строка"'):
            with self.subTest(аргументы=кусок):
                self.assertIn("не разобрались",
                              _отказ(hands.run_set_reminder(кусок)))
        self.assertEqual(reminders.pending(), [])

    def test_без_времени_отказ(self):
        # Модель не поняла, когда напоминать: лучше спросить, чем назначить
        # время наугад — хозяин ждал бы несуществующего звонка.
        self.assertIn("время не названо", _отказ(
            hands.run_set_reminder(json.dumps({"kind": "reminder",
                                               "text": "пицца"}))))
        self.assertEqual(reminders.pending(), [])

    def test_кривой_вид_отказ(self):
        self.assertIn("не знаю", _отказ(hands.run_set_reminder(json.dumps({
            "seconds": 60, "kind": "будильник"}))))

    def test_прошедшее_время_доходит_до_модели(self):
        # Отказ хранилища уходит модели строкой, а не глохнет: она перескажет
        # хозяину, что так нельзя.
        events, on_event = _события()
        self.assertIn("прошло", _отказ(hands.run_set_reminder(json.dumps({
            "seconds": -600, "kind": "timer"}), on_event)))
        self.assertEqual([вид for вид, _ in events], ["reminder_failed"])
        self.assertEqual(reminders.pending(), [])



    def test_список_того_что_стоит(self):
        self.assertEqual(_ans(hands.run_list_reminders("{}"))["items"], [])
        _ans(hands.run_set_reminder(json.dumps({
            "seconds": 1200, "kind": "reminder", "text": "пицца"})))
        second = _ans(hands.run_set_reminder(json.dumps({
            "seconds": 300, "kind": "timer"})))
        answer = _ans(hands.run_list_reminders("{}"))
        self.assertEqual(answer["count"], 2)
        # Список по времени, а не по id: хозяину нужны «ближайшие сверху».
        self.assertEqual([one["in_seconds"] for one in answer["items"]], [300, 1200])
        self.assertEqual(answer["items"][1]["text"], "пицца")
        # Модель знает id отсюда — без них `cancel_reminder` бесполезен.
        self.assertEqual({one["id"] for one in answer["items"]},
                         {"r1", second["id"]})
        self.assertIn("пицца", answer["text"])

    def test_отмена_по_идентификатору(self):
        first = _ans(hands.run_set_reminder(json.dumps({
            "seconds": 1200, "kind": "reminder", "text": "вытащить пиццу"})))
        _ans(hands.run_set_reminder(json.dumps({
            "seconds": 300, "kind": "timer"})))
        events, on_event = _события()
        answer = _ans(hands.run_cancel_reminder(
            json.dumps({"id": first["id"]}), on_event))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["id"], first["id"])
        # Фраза готовая: модель за ней не говорит, а проговаривает голос.
        self.assertEqual(answer["text"],
                         "Отменила напоминание: вытащить пиццу.")
        self.assertEqual([one["id"] for one in reminders.pending()], ["r2"])
        self.assertEqual([вид for вид, _ in events], ["reminder_cancel"])

    def test_отмена_всего(self):
        _ans(hands.run_set_reminder(json.dumps({
            "seconds": 1200, "kind": "reminder", "text": "пицца"})))
        _ans(hands.run_set_reminder(json.dumps({"seconds": 300, "kind": "timer"})))
        answer = _ans(hands.run_cancel_reminder(json.dumps({"all": True})))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["count"], 2)
        self.assertEqual(reminders.pending(), [])

    def test_отмена_таймера_словами(self):
        _ans(hands.run_set_reminder(json.dumps({"seconds": 300, "kind": "timer"})))
        answer = _ans(hands.run_cancel_reminder(json.dumps({"id": "r1"})))
        self.assertEqual(answer["text"], "Отменила таймер.")

    def test_отмена_чужого_идентификатора_честно_отказ(self):
        _ans(hands.run_set_reminder(json.dumps({
            "seconds": 1200, "kind": "reminder", "text": "пицца"})))
        self.assertIn("не отменено", _отказ(
            hands.run_cancel_reminder(json.dumps({"id": "r99"}))))
        # Живое напоминание при этом цело: промах по id не должен всё убрать.
        self.assertEqual(len(reminders.pending()), 1)

    def test_отмена_без_идентификатора_отказ(self):
        # Без id модель должна сначала посмотреть список, а не выдумывать.
        self.assertIn("list_reminders", _отказ(hands.run_cancel_reminder("{}")))
        self.assertIn("не разобрались",
                      _отказ(hands.run_cancel_reminder("не json")))


class НаборыИнструментов(unittest.TestCase):
    """Принадлежность наборам — как у остальных действий (`open_folder`)."""

    def test_принадлежат_наборам(self):
        for имя in (hands.SET_REM_NAME, hands.LIST_REM_NAME,
                    hands.CANCEL_REM_NAME):
            with self.subTest(инструмент=имя):
                # `because` обязателен у всех: без цитаты модель повторяла бы
                # прошлую просьбу (см. `hands.asked_for`).
                self.assertIn(имя, hands.GUARDED)
        # Постановка и отмена — без второго круга: ответ уже готовой фразой.
        self.assertIn(hands.SET_REM_NAME, hands.LOCAL)
        self.assertIn(hands.CANCEL_REM_NAME, hands.LOCAL)
        # Список — модель нужна, чтобы пересказать хозяину своими словами.
        self.assertNotIn(hands.LIST_REM_NAME, hands.LOCAL)
        self.assertEqual(hands.CONFIRM[hands.SET_REM_NAME], ("{text}",))
        self.assertEqual(hands.CONFIRM[hands.CANCEL_REM_NAME], ("{text}",))
        self.assertNotIn(hands.LIST_REM_NAME, hands.CONFIRM)
        # В судью не идут: наружу от напоминания не уходит ничего, а лишняя
        # секунда проверки задержала бы ясную просьбу.
        for имя in (hands.SET_REM_NAME, hands.LIST_REM_NAME,
                    hands.CANCEL_REM_NAME):
            self.assertNotIn(имя, hands.JUDGED)

    def test_because_обязателен(self):
        for spec in hands.REMINDER_TOOLS:
            with self.subTest(инструмент=spec["function"]["name"]):
                params = spec["function"]["parameters"]
                self.assertIn("because", params["required"])
                self.assertIn("because", params["properties"])

    def test_время_считает_модель(self):
        # Описание обязано говорить, что время — по часам из системного
        # сообщения: иначе модель начнёт разбирать «через полчаса» сама.
        описание = hands.SET_REMINDER_TOOL["function"]["description"]
        self.assertIn("часам", описание)
        self.assertIn("seconds", описание)
        self.assertIn("at", описание)
        self.assertIn("переспроси", описание)
        # У отмены сказано, что без id надо сначала посмотреть список.
        self.assertIn("list_reminders",
                      hands.CANCEL_REMINDER_TOOL["function"]["description"])

    def test_способности_промпта_упоминают_напоминания(self):
        # Без строки в `abilities` модель на «напомни через двадцать минут»
        # отвечала бы «я не умею», ровно как с заметками до их инструментов.
        self.assertTrue(any("напоминания" in строка and "таймер" in строка
                            for строка in abilities.ACTIONS))



class НаборУМозга(unittest.TestCase):
    """Три инструмента в наборе `brain` — всегда, порядок стабилен.

    Мозг собирается вручную, как в `tests/test_action_tools.py`: с настоящим
    клиентом и сетью тут нечего проверять — только список имён, который
    уходит в запрос.
    """

    def setUp(self):
        self._saved = config.WEB_SEARCH
        config.WEB_SEARCH = False
        patcher = mock.patch.object(hands, "read_list", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, config, "WEB_SEARCH", self._saved)

    def _names(self):
        brain = object.__new__(Brain)
        brain.provider = "deepseek"
        brain._home = "deepseek"
        brain._home_at = 0.0
        brain._model = "test"
        brain._reply_lock = threading.RLock()
        brain._history = deque(maxlen=10)
        brain._persona = "тест"
        brain._abilities = lambda: ""
        brain._memory = lambda: ""
        brain._keep_history = lambda: None
        brain._undigested = 0
        brain._tools_ok = None
        brain._quiet = None
        brain._usage_ok = None
        brain.on_event = None
        brain.actions = {}
        return [spec["function"]["name"] for spec in brain._tool_list("как дела?")]

    def test_три_инструмента_в_наборе(self):
        names = self._names()
        for имя in (hands.SET_REM_NAME, hands.LIST_REM_NAME,
                    hands.CANCEL_REM_NAME):
            self.assertIn(имя, names)

    def test_стоят_сразу_за_папками_и_в_стабильном_порядке(self):
        names = self._names()
        # Набор не должен зависеть от того, что в apps.json, от настроек голоса
        # и от наличия действий: напомнить он может и при пустом списке.
        self.assertEqual(self._names(), names)
        after = names[names.index(hands.FOLDER_NAME) + 1:]
        self.assertEqual(after[:3], [hands.SET_REM_NAME, hands.LIST_REM_NAME,
                                     hands.CANCEL_REM_NAME])

    def test_не_зависят_от_действий(self):
        # Снимок, клип и взгляд зависят от `brain.actions`, а напоминания — нет.
        brain_actions = {}
        self.assertEqual(len(hands.action_tools(brain_actions)), 0)
        self.assertEqual(len(hands.REMINDER_TOOLS), 3)


class _Телефон:
    """Подмена телефона: помнит звуки и тосты, ничего не играет."""

    def __init__(self):
        self.sounds = []
        self.toasts = []

    def send_sound(self, name):
        self.sounds.append(name)

    def send_toast(self, text, ok=True):
        self.toasts.append(text)

    def detach(self, *args):
        pass


def _события():
    """Подмена `on_event`: помнит пары (вид, что)."""
    seen = []
    return seen, (lambda вид, что: seen.append((вид, что)))


class Срабатывание(_Тест):
    """`on_fire`: телефон, журнал, голос. Голос — подменённый `announce`."""

    def _среда(self, голос_включён=False):
        среда = object.__new__(WebRuntime)
        среда._lock = threading.Lock()
        среда._events = deque(maxlen=300)
        среда._ids = itertools.count(1)
        среда._overview = {"last_activity": None}
        среда._log_path = SAFE / "session.log"
        среда.server = _Телефон()
        сказанное = []
        среда.voice = NS(running=голос_включён, said=сказанное,
                         announce=lambda слова: сказанное.append(слова))
        return среда

    def _позвонить(self, среда, *, что="вытащить пиццу", say="", когда=None):
        reminders.add(self.now + timedelta(minutes=5), что, say=say, now=self.now)
        reminders.Alarm(среда._on_reminder, now=self.now).check(
            когда or self.now + timedelta(minutes=5))

    def _подождать_голос(self, среда, сколько=200):
        # Голос говорит в своём потоке: ждём, но недолго.
        for _ in range(сколько):
            if среда.voice.said:
                return
            time.sleep(0.01)

    def test_телефон_и_журнал_без_голоса(self):
        # Голос выключен — напоминание всё равно должно позвать: телефон стоит
        # сбоку, а журнал хозяин откроет и так.
        среда = self._среда(голос_включён=False)
        self._позвонить(среда)
        self.assertEqual(среда.server.sounds, ["moment"])
        self.assertEqual(среда.server.toasts, ["Напоминаю: вытащить пиццу."])
        строки = [e for e in среда.events() if e["kind"] == "reminder_fired"]
        self.assertEqual(len(строки), 1)
        self.assertIn("вытащить пиццу", строки[0]["payload"])
        # Голос выключен — вслух не сказано, но это не помеха.
        self.assertEqual(среда.voice.said, [])

    def test_голос_включён_говорит(self):
        среда = self._среда(голос_включён=True)
        self._позвонить(среда)
        self._подождать_голос(среда)
        self.assertEqual(среда.voice.said, ["Напоминаю: вытащить пиццу."])

    def test_пропущенное_пока_пульт_был_выключен(self):
        # Пульт закрыли на час: «Напоминаю: пицца» через час после обеда —
        # это издевательство, а не напоминание.
        среда = self._среда(голос_включён=True)
        self._позвонить(среда, когда=self.now + timedelta(minutes=65))
        self._подождать_голос(среда)
        self.assertTrue(среда.voice.said)
        self.assertTrue(среда.voice.said[0].startswith(
            "Пока я была выключена, пропустила напоминание:"))

    def test_своя_фраза_модели_доходит_до_голоса(self):
        среда = self._среда(голос_включён=True)
        self._позвонить(среда, say="Пицца, хозяин, пицца!")
        self._подождать_голос(среда)
        self.assertEqual(среда.voice.said, ["Пицца, хозяин, пицца!"])

    def test_упавший_телефон_не_роняет_будильник(self):
        # Телефон выключен или пульт в полусне: сработавшее всё равно должно
        # уйти из файла, иначе оно позвонит ещё и ещё.
        среда = self._среда(голос_включён=False)

        def _падает(*args, **kwargs):
            raise RuntimeError("телефон сгорел")

        среда.server.send_sound = _падает
        среда.server.send_toast = _падает
        self._позвонить(среда, что="пицца")
        self.assertEqual(reminders.pending(), [])

    def test_таймер_и_пустое_в_тосте(self):
        среда = self._среда()
        self._позвонить(среда, что="")
        self.assertEqual(среда.server.toasts, ["Напоминаю."])

    def test_строка_в_журнал_для_таймера(self):
        self.assertEqual(
            WebRuntime._log_messages("reminder_fired", "таймер вышел"),
            ["таймер вышел"])

    def test_будильник_гасится_с_пультом(self):
        # `close()` останавливает будильник: закрытое окно не должно через
        # секунду позвонить хозяину в спикер.
        среда = self._среда()
        среда.alarm = mock.Mock()
        среда._enroll = None
        среда._jobs = {}
        среда._warm_stop = lambda: None
        среда.replay_guard = NS(stop=lambda: None)
        среда.server.detach = lambda *args: None
        среда.close()
        среда.alarm.stop.assert_called_once()




class Голос(unittest.TestCase):
    """`VoiceLoop.announce`: ждёт хода, говорит и не лезет в чужую речь."""

    def _цикл(self, голос_есть=True, занят=False):
        from core import voice_loop

        цикл = object.__new__(voice_loop.VoiceLoop)
        цикл._brain = None
        цикл._emit = lambda *args: None
        цикл._voice = NS() if голос_есть else None
        цикл._stop = threading.Event()
        цикл._turn = None
        цикл._listener = None
        цикл._ducker = NS(duck=lambda: None, restore=lambda: None)
        цикл._server = None
        сказанное = []
        цикл._say_back = lambda слова: сказанное.append(слова)
        if занят:
            цикл._turn_lock().acquire()
        return цикл, сказанное

    def test_говорит_когда_свободно(self):
        цикл, сказанное = self._цикл()
        цикл.announce("Пицца!")
        self.assertEqual(сказанное, ["Пицца!"])
        # Замок отпущен: следующий ход хозяина не должен ждать.
        self.assertFalse(цикл._turn_lock().locked())

    def test_пустое_не_говорится(self):
        цикл, сказанное = self._цикл()
        for пусто in ("", "   ", None):
            цикл.announce(пусто)
        self.assertEqual(сказанное, [])

    def test_без_голоса_молчит(self):
        # Голос выключен — говорить нечем. Будильник при этом жив: телефон
        # и журнал уже отработали.
        цикл, сказанное = self._цикл(голос_есть=False)
        цикл.announce("Пицца!")
        self.assertEqual(сказанное, [])

    def test_занятый_хозяином_не_перебивается(self):
        # Хозяин говорит — мы его не перебиваем, даже если ход длинный.
        цикл, сказанное = self._цикл(занят=True)
        with mock.patch("core.voice_loop.ANNOUNCE_WAIT", 0.05):
            цикл.announce("Пицца!")
        try:
            self.assertEqual(сказанное, [])
        finally:
            цикл._turn_lock().release()

    def test_история_помечена_как_её_реплика(self):
        # В историю кладётся `first`: без него строка выглядела бы как
        # реплика хозяина и уехала бы в память о нём.
        сказанное = []
        цикл, _ = self._цикл()
        цикл._brain = NS(announce=lambda пометка, слова: сказанное.append(
            (пометка, слова)))
        цикл.announce("Пицца!")
        self.assertEqual(сказанное, [("(напоминание сработало)", "Пицца!")])

    def test_ошибка_голоса_не_убивает_будильник(self):
        # Синтез упал — будильник от этого пережить должен: он зовёт не
        # голосом, а файлом, и следующее напоминание ещё впереди.
        цикл, _ = self._цикл()

        def _падает(_слова):
            raise RuntimeError("синтез умер")

        цикл._say_back = _падает
        цикл.announce("Пицца!")  # не должно бросить наружу
        self.assertFalse(цикл._turn_lock().locked())


class СтраховкаДанных(_Тест):
    """Живая `data/` тестами не трогается."""

    def test_живой_файл_не_создаётся(self):
        # Путь на весь прогон подменяет `tests/test_data_guard.py`; этот тест
        # ловит случай, когда подмена забыта (например, новый модуль). Импортом
        # соседнего теста проверять нельзя: при `discover -s tests` он грузится
        # вторым экземпляром и запускает свой `test_data_guard` с другой
        # временной папкой (ГРАБЛИ).
        self.assertTrue(str(reminders.PATH).startswith(str(self.folder)))
        # Не «живого файла нет»: 30.09, 08:23 хозяин сам поставил голосом
        # «через 15 минут вызвать такси», файл настоящий, и тест падал. Живой
        # файл пишет его пульт — тестам важно лишь, что их путь не живой.
        self.assertNotEqual(reminders.PATH, config.DATA_DIR / "reminders.json")


class Произношение(unittest.TestCase):
    """После «таймер на» — винительный: «одну минуту», а не «одна минута»."""

    def test_минуты_и_секунды(self):
        self.assertEqual(reminders.minutes_said(1), "одну минуту")
        self.assertEqual(reminders.minutes_said(2), "две минуты")
        self.assertEqual(reminders.minutes_said(5), "пять минут")
        self.assertEqual(reminders.minutes_said(21), "двадцать одну минуту")
        self.assertEqual(reminders.seconds_said(30), "тридцать секунд")

    def test_когда(self):
        now = datetime(2026, 9, 30, 12, 0).astimezone()
        self.assertEqual(reminders.when_said(now.replace(hour=17), now),
                         "в семнадцать часов ровно")
        self.assertTrue(reminders.when_said(now + timedelta(days=1), now)
                        .startswith("завтра в "))
        self.assertTrue(reminders.when_said(now + timedelta(days=5), now)
                        .startswith("5 октября в "))

    def test_таймер_меньше_минуты_секундами(self):
        now = reminders.local_now()
        record = {"kind": "timer", "minutes": 1,
                  "created": now.isoformat(timespec="seconds"),
                  "due": (now + timedelta(seconds=30)).isoformat(timespec="seconds")}
        self.assertEqual(hands.confirm_phrase(record), "Таймер на тридцать секунд пошёл.")
