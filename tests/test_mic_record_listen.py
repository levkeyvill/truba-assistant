"""«Записать 3 секунды» и «Прослушать»: запись не играет, слушать — отдельно.

`audio_test` хранит запись до нажатия «Прослушать». Рядом с кнопками
показывается оценка уровня записи; автоматическое проигрывание не требуется.

Микрофон, колонки, устройства и голос тут — подмены: настоящих звуковых
устройств тест не открывает (`sounddevice` подменён в `sys.modules`).
"""

import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
from core.audio_in import peak_level, voice_level
from ui.web_runtime import WebRuntime

PULT = Path(config.ROOT) / "ui" / "web"
ЧАСТОТА = 16000


def _речь(секунды: float = 3.0, громкость: float = 0.3, кусок: float = 0.2):
    """Трек как речь с паузами: куски звука, между ними тишина.

    Из-за пауз средний уровень записи ниже громкости речи; нужен максимум
    по кускам.
    """
    шаг = int(ЧАСТОТА * кусок)
    кусок_звука = np.full(шаг, громкость, dtype=np.float32)
    тишина = np.zeros(шаг, dtype=np.float32)
    трек = np.concatenate([кусок_звука, тишина] * 12)
    return трек[:int(ЧАСТОТА * секунды)]


def _звук(события: list):
    """Подмена `sounddevice`: пишет в `события`, что и с какой частотой играли."""
    return types.SimpleNamespace(
        play=lambda трек, частота, device=None: события.append(
            ("play", np.asarray(трек), int(частота), device)),
        wait=lambda: события.append(("wait",)),
    )


def _свой_поток(события: list):
    """Подмена `audio_out.play_own`: прослушивание использует свой поток."""
    return lambda трек, частота, device=None: события.append(
        ("play", np.asarray(трек), int(частота), device))


def _пульт() -> WebRuntime:
    """Настоящий runtime без конструктора: тот тянет сервер и голос."""
    среда = object.__new__(WebRuntime)
    среда.voice = types.SimpleNamespace(running=False)
    среда._проба_звука = None
    среда._audio_play_lock = threading.Lock()
    return среда


class ЗаписьTests(unittest.TestCase):
    """`_audio_record_once`: только пишет и говорит, что микрофон услышал."""

    def setUp(self):
        self.звук: list = []
        self.rt = _пульт()

    def _ответ(self, трек):
        with mock.patch("config.find_input_device", return_value=(0, "Микрофон (USB)")), \
                mock.patch("core.selftest.record", return_value=(трек, ЧАСТОТА)), \
                mock.patch.dict("sys.modules", {"sounddevice": _звук(self.звук)}), \
                mock.patch("config.OUTPUT", "speakers"):
            return WebRuntime._audio_record_once(self.rt)

    def test_запись_не_играет_в_колонки(self):
        # Главное: `sd.play` из записи исчез. Иначе запись играет один раз мимо
        # пользователя, и «Прослушать» будет нечего.
        ответ = self._ответ(_речь())
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(self.звук, [], "запись не должна звучать сама")

    def test_тишина_видна_как_тишина(self):
        # Микрофон выключен или выбран не тот: пик ниже порога.
        ответ = self._ответ(np.zeros(ЧАСТОТА, dtype=np.float32))
        self.assertEqual(ответ["verdict"], "silent")
        self.assertIn("ничего не услышал", ответ["verdict_text"])

    def test_тихий_микрофон_не_перепутан_с_молчащим(self):
        # Пик выше порога, но громкость мала: это «тихо», а не «тишина» —
        # нужен совет подойти ближе или говорить громче, а не менять устройство.
        трек = np.full(ЧАСТОТА, 0.002, dtype=np.float32)
        трек[::50] = 0.02
        ответ = self._ответ(трек)
        self.assertEqual(ответ["verdict"], "quiet")
        self.assertIn("ближе или громче", ответ["verdict_text"])
        self.assertGreater(ответ["peak"], 0.01, "пик всё равно должен быть виден")

    def test_нормальная_речь_с_паузами_не_тихо(self):
        # Паузы снижают средний уровень записи. Считаем по самому громкому куску —
        # уровень обязан быть выше среднего по всему треку.
        трек = _речь(громкость=0.1)
        ответ = self._ответ(трек)
        self.assertEqual(ответ["verdict"], "ok", ответ)
        self.assertLess(voice_level(трек), ответ["level"],
                        "уровень записи обязан быть выше среднего по треку")

    def test_пик_считается_по_громчайшему_куску(self):
        # Та же арифметика, но без сервера: `peak_level` и `measure_level` должны
        # считать одинаково, иначе полоска и запись показывали бы разное.
        трек = _речь(громкость=0.1)
        self.assertGreater(peak_level(трек, ЧАСТОТА), voice_level(трек))
        # Один короткий громкий кусок и длинная пауза: среднего тут почти нет,
        # а громкий кусок всё равно должен найтись — иначе тихая речь с паузами
        # выглядела бы на шкале как шум комнаты.
        редкий = np.concatenate([np.full(int(ЧАСТОТА * 0.05), 0.2, dtype=np.float32),
                                 np.zeros(int(ЧАСТОТА * 3.0), dtype=np.float32)])
        self.assertGreater(peak_level(редкий, ЧАСТОТА), 0.9)
        self.assertGreater(peak_level(редкий, ЧАСТОТА), voice_level(редкий))
        self.assertEqual(peak_level(np.zeros(10, dtype=np.float32), ЧАСТОТА), 0.0)

    def test_уровень_и_пик_в_ответе(self):
        ответ = self._ответ(_речь(громкость=0.4))
        self.assertAlmostEqual(ответ["peak"], 0.4, places=3)
        self.assertGreater(ответ["level"], 0.25)
        self.assertAlmostEqual(ответ["seconds"], 3.0, places=1)

    def test_запись_лежит_в_памяти_для_прослушивания(self):
        # «Прослушать» играет ровно то, что записали, с той же частотой.
        трек = _речь()
        self._ответ(трек)
        self.assertIsNotNone(self.rt._проба_звука)
        записанное, частота = self.rt._проба_звука
        self.assertEqual(частота, ЧАСТОТА)
        np.testing.assert_array_equal(записанное, трек.astype(np.float32))


class ПрослушиваниеTests(unittest.TestCase):
    """`audio_test_play`: играет запомненную запись, и только её."""

    def setUp(self):
        self.звук: list = []
        self.rt = _пульт()
        for патч in (mock.patch.dict("sys.modules", {"sounddevice": _звук(self.звук)}),
                    mock.patch("core.audio_out.play_own", _свой_поток(self.звук)),
                    mock.patch("config.OUTPUT", "speakers")):
            патч.start()
            self.addCleanup(патч.stop)

    def _запомнить(self, трек):
        self.rt._проба_звука = (np.asarray(трек, dtype=np.float32), ЧАСТОТА)

    def test_без_записи_отказ(self):
        # Сначала записать, потом слушать: иначе нечего играть.
        ответ = self.rt.audio_test_play()
        self.assertFalse(ответ["ok"])
        self.assertIn("Записать 3 секунды", ответ["error"])
        self.assertEqual(self.звук, [])

    def test_играет_ту_же_запись_ту_же_частотой_в_выбранные_колонки(self):
        трек = _речь(громкость=0.2)
        self._запомнить(трек)
        with mock.patch("config.find_output_device",
                        return_value=(7, "Колонки (USB)")) as вывод:
            ответ = self.rt.audio_test_play()
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["speaker"], "Колонки (USB)")
        вывод.assert_called_once()
        вид, переданное, частота, устройство = self.звук[0]
        self.assertEqual(вид, "play")
        self.assertEqual(частота, ЧАСТОТА)
        self.assertEqual(устройство, 7)
        np.testing.assert_array_equal(переданное, трек.astype(np.float32))
        # Общий `sd.play` мог бы прервать другой звуковой поток.
        self.assertNotIn(("wait",), self.звук)

    def test_в_телефон_запись_микрофона_не_идёт(self):
        # В телефон шлёт только синтезированная речь: подмешивать туда чужой
        # микрофон — верный способ сорвать разговор.
        self._запомнить(_речь())
        with mock.patch("config.OUTPUT", "phone"), \
                mock.patch("config.find_output_device",
                           return_value=(7, "Колонки (USB)")):
            ответ = self.rt.audio_test_play()
        self.assertTrue(ответ["ok"], ответ)
        self.assertEqual(ответ["played_in"], "speakers")
        self.assertIn("в телефон не отправить", ответ.get("note", ""))

    def test_сбой_звука_честная_ошибка(self):
        self._запомнить(_речь())
        сломанный = mock.Mock(side_effect=RuntimeError("колонки исчезли"))
        with mock.patch("core.audio_out.play_own", сломанный), \
                mock.patch("config.find_output_device",
                           return_value=(7, "Колонки (USB)")):
            ответ = self.rt.audio_test_play()
        self.assertFalse(ответ["ok"])
        self.assertIn("колонки исчезли", ответ["error"])

    def test_две_кнопки_подряд_не_играют_одну_запись(self):
        self._запомнить(_речь())
        self.rt._audio_play_lock.acquire()
        try:
            ответ = self.rt.audio_test_play()
        finally:
            self.rt._audio_play_lock.release()
        self.assertFalse(ответ["ok"])
        self.assertIn("уже играет", ответ["error"])
        self.assertEqual(self.звук, [])

    def test_замок_освобождается_даже_при_сбое(self):
        # Иначе после одной неудачи «Прослушать» не нажималась бы никогда.
        self._запомнить(_речь())
        сломанный = mock.Mock(side_effect=RuntimeError("нет"))
        with mock.patch("core.audio_out.play_own", сломанный), \
                mock.patch("config.find_output_device", return_value=(7, "Колонки")):
            self.rt.audio_test_play()
        self.assertFalse(self.rt._audio_play_lock.locked())


class _Голос:
    """Голос с настоящим потоком: `stop()` просит поток выйти, но сразу не выходит.

    `running` — «поток жив», как у настоящего голоса: он становится False не в
    момент `stop()`, а когда поток действительно дошёл до конца. Именно на этом
    проба и спотыкалась: `stop()` прошёл, а поток ещё жил.
    """

    def __init__(self, события, уходит_через=0.3, совсем_не_уходит=False,
                 stop_сбрасывает=False):
        self.события = события
        self.уходит_через = уходит_через
        self.совсем_не_уходит = совсем_не_уходит
        self.stop_сбрасывает = stop_сбрасывает
        self.остановить = threading.Event()
        self.покинуть = threading.Event()
        self._поток_ушёл = threading.Event()
        self._поток = threading.Thread(target=self._работать, daemon=True)
        self._поток.start()

    def _работать(self):
        self.остановить.wait(5)
        if not self.совсем_не_уходит:
            time.sleep(self.уходит_через)
        else:
            # Зависшая выгрузка модели: поток жив и не станет мёртвым.
            self.покинуть.wait(30)
        self._поток_ушёл.set()

    @property
    def running(self):
        if self.stop_сбрасывает and self.остановить.is_set():
            # `stop` отработал и голос считает себя выключенным, хотя поток ещё
            # ждёт: так ведёт себя голос, у которого `stop` успел погасить флаг,
            # а цикл ещё выходит.
            return False
        return not self._поток_ушёл.is_set()

    @property
    def _thread(self):
        return self._поток

    def stop(self):
        self.события.append("stop")
        self.остановить.set()

    def start(self):
        # Настоящий голос поднимает новый поток: без этого `running` после
        # `start()` остался бы False и проба сочла бы голос не поднятым.
        self.события.append("start")
        self.остановить.clear()
        self.покинуть.clear()
        self._поток_ушёл.clear()
        self._поток = threading.Thread(target=self._работать, daemon=True)
        self._поток.start()


class ПробаЖдётГолосTests(unittest.TestCase):
    """Проба мастера: запись начинается только когда голос реально ушёл.

    `running` у голоса — «поток жив», а не «микрофон закрыт»: после `stop()`
    поток ещё выходит и выгружает модель. Проба ждёт освобождения микрофона.
    """

    def setUp(self):
        self.события: list = []
        self.rt = object.__new__(WebRuntime)
        self.rt._audio_probe_lock = threading.Lock()
        self.rt._level_capture_lock = threading.Lock()
        self.rt._audio_stale = False
        self.rt._warm_stop = lambda: self.события.append("warm_stop")
        self.rt._warm_start = lambda: self.события.append("warm_start")
        # Ожидание потока короткое: тест не должен висеть 15 настоящих секунд.
        self.rt.ПОЖДАТЬ_ГОЛОС = 1.0

    def _голос(self, **kwargs):
        голос = _Голос(self.события, **kwargs)
        self.rt.voice = голос
        self.addCleanup(голос.покинуть.set)
        self.addCleanup(голос.остановить.set)
        return голос

    def _записать(self, голос_остановлен=False):
        self.события.append(("record", голос_остановлен))
        return {"ok": True, "seconds": 3.0, "level": 0.5, "verdict": "ok"}

    def _были_записи(self):
        return [событие for событие in self.события if isinstance(событие, tuple)]

    def test_проба_дожидается_конца_потока_и_пишет(self):
        # `running` становится False через 0,3 с после `stop()`; проба ждёт.
        голос = self._голос(уходит_через=0.3)
        остановленный = голос._поток
        self.rt._audio_record_once = self._записать
        ответ = self.rt.audio_test(True)
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(self._были_записи(), "проба должна была записать")
        self.assertFalse(остановленный.is_alive(),
                         "проба должна была дождаться потока, а не писать в микрофон")
        self.assertIn("start", self.события, "голос возвращают как был")

    def test_проба_не_смотрит_на_окно_разговора(self):
        # `in_conversation` — про разговор, а не про микрофон: при живом окне
        # разговора проба мастера всё равно обязана записать.
        self._голос(уходит_через=0.1)
        self.rt.voice.in_conversation = True
        self.rt.voice._speaker_idle = lambda: False
        self.rt._audio_record_once = self._записать
        ответ = self.rt.audio_test(True)
        self.assertTrue(ответ["ok"], ответ)
        self.assertTrue(self._были_записи(), "запись должна была состояться")
        self.assertTrue(self._были_записи()[0][1],
                        "проба обязана сказать, что голос остановила она")

    def test_не_остановился_за_время_честный_отказ(self):
        # Голос завис: писать нельзя, но сказать «попробуй ещё раз» можно, а
        # голос вернуть — обязательно, иначе он останется выключенным.
        self._голос(совсем_не_уходит=True, stop_сбрасывает=True)
        self.rt._audio_record_once = self._записать
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertIn("не остановился", ответ["error"])
        self.assertIn("попробуй ещё раз", ответ["error"])
        self.assertIn("start", self.события, "голос всё равно поднимают")
        self.assertEqual(self._были_записи(), [],
                         "при живом голосе микрофон не трогают")

    def test_висящий_поток_при_живом_голосе_тоже_останавливает_пробу(self):
        # Голос и сам считает себя работающим: поднимать второй раз нельзя, но
        # отказ должен быть честным, а не «записала 3 с».
        self._голос(совсем_не_уходит=True)
        self.rt._audio_record_once = self._записать
        ответ = self.rt.audio_test(True)
        self.assertFalse(ответ["ok"])
        self.assertIn("не остановился", ответ["error"])
        self.assertNotIn("start", self.события, "работающий голос не стартуем снова")
        self.assertEqual(self._были_записи(), [])


class МаршрутИПультTests(unittest.TestCase):
    """Серверный маршрут и обе формы пульта."""

    @classmethod
    def setUpClass(cls):
        cls.скрипт = (PULT / "pult.js").read_text(encoding="utf-8")
        cls.сервер = (Path(config.ROOT) / "core" / "phone.py").read_text(encoding="utf-8")

    def test_маршрут_закрыт_так_же_как_сама_запись(self):
        # Запись микрофона не отдаём без локального доступа, как у
        # `/api/audio/test`.
        блок = self.сервер.split('@app.post("/api/audio/test/play")')[1].split("\n\n")[0]
        self.assertIn("_local_secret(request)", блок)
        self.assertIn("rt.audio_test_play", блок)

    def test_форма_настроек_умеет_прослушивать(self):
        self.assertIn("async function прослушатьЗапись(", self.скрипт)
        self.assertIn("fetch('/api/audio/test/play', { method: 'POST' })", self.скрипт)
        self.assertIn("звукПрослушать.addEventListener('click',"
                      " () => прослушатьЗапись(настрЭлементы));", self.скрипт)

    def test_мастер_умеет_прослушивать(self):
        # Шаг 4 рисует свои кнопки, но слушает общей функцией — второй копии
        # быть не должно, иначе правила разъедутся.
        self.assertIn("звукПрослушать.addEventListener('click',"
                      " () => мастерПрослушать(эл));", self.скрипт)
        # Мастер слушает той же общей функцией, на время звука глушит полоску.
        мастер = self.скрипт.split("async function мастерПрослушать(")[1].split("\n}\n")[0]
        self.assertIn("await прослушатьЗапись(эл, мастерЖива);", мастер)
        self.assertIn("мастерУровеньВыключить();", мастер)
        блок = self.скрипт.split("function мастерШагЗвук(")[1].split("\n}\n")[0]
        self.assertIn("мастерКнопка('Записать 3 секунды')", блок)
        self.assertIn("мастерКнопка('Прослушать')", блок)
        self.assertIn("звукПрослушать, звукЗапись, звукСтатус: статус", блок)
        self.assertEqual(self.скрипт.count("function прослушатьЗапись("), 1)
        self.assertNotIn("Записать и послушать", self.скрипт)

    def test_обратный_отсчёт_и_строка_записи(self):
        блок = self.скрипт.split("async function звукЗаписать(")[1].split("\n}\n")[0]
        self.assertIn("'Говори… ' + осталось", блок)
        # Проба мастера — без отсчёта: запись начнётся после остановки голоса.
        self.assertIn("includes('probe=1')", блок)
        self.assertIn("finally {", блок)
        self.assertIn("clearTimeout(таймер)", блок)
        self.assertIn("данные.verdict_text", блок)
        self.assertIn("эл.звукПрослушать.disabled = false", блок)
        # Пять делений по уровню — видно, насколько микрофон услышал.
        строка = self.скрипт.split("function звукЗаписьПоказать(")[1].split("\n}\n")[0]
        self.assertIn("'Запись: ' + знаки", строка)
        self.assertIn("'▮'", строка)
        self.assertIn("'▯'", строка)
        # Пометка «плохо» — про текущую запись, а не навсегда.
        self.assertIn("classList.toggle('плохо', данные.verdict === 'silent')", строка)


if __name__ == "__main__":
    unittest.main()
