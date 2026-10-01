"""На компьютере работает только одна Труба.

Решение хозяина от 01.10: папки установки независимы, но пульт может быть
только один. Проверяем ровно это — без соседних портов:

- своя копия работает → повторный запуск показывает своё окно;
- работает другая копия → вторая отказывает и чужое окно не трогает;
- работает старая версия (нет `/api/install`) → тоже отказ;
- гонка двух новых копий → общий mutex отдаётся одной;
- после закрытия первой вторая стартует;
- установщик второй копии не поднимает пульт.

Настоящих окон, процессов Windows и ярлыков тут нет: mutex подменяется,
окна ищутся подменой `ctypes`, сервер — локальный сокет на свободном
порту, VBS только читается как текст и никогда не исполняется.
"""

import base64
import ctypes
import http.server
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import config
from core import instance, weather
from core.phone import PhoneServer
from ui import window as window_mod


def _свободный_порт() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _ЧужойПульт:
    """Локальный сервер, отвечающий как пульт Трубы (любой версии)."""

    def __init__(self, отпечаток: str | None, runtime: bool = True,
                 runtime_body: bytes = b'{"ok":true,"voice":{},"events":[],"overview":{}}') -> None:
        self.отпечаток = отпечаток
        self.runtime = runtime
        self.runtime_body = runtime_body
        self.порт = _свободный_порт()
        вид = self

        class Обработчик(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — так требует http.server
                if self.path == "/api/install" and вид.отпечаток:
                    тело = вид.отпечаток.encode("utf-8")
                elif self.path == "/api/runtime" and вид.runtime:
                    тело = вид.runtime_body
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(тело)))
                self.end_headers()
                self.wfile.write(тело)

            def log_message(self, *args):
                pass

        self._сервер = http.server.HTTPServer(("127.0.0.1", self.порт), Обработчик)
        self._поток = threading.Thread(target=self._сервер.serve_forever, daemon=True)
        self._поток.start()

    def остановить(self):
        self._сервер.shutdown()
        self._сервер.server_close()


def _подменить_отпечаток(тест, значение: str):
    """Свой отпечаток во временном файле: живой data/ не трогаем."""
    папка = Path(tempfile.mkdtemp(prefix="truba-id-"))
    тест.addCleanup(shutil.rmtree, папка, True)
    файл = папка / "install_id.txt"
    файл.write_text(значение, encoding="ascii")
    патч = mock.patch.object(instance, "ID_FILE", файл)
    патч.start()
    тест.addCleanup(патч.stop)
    return значение


class _Вызов:
    """Поддельная функция kernel32: callable и с полями argtypes/restype.

    Именно экземпляр, а не метод: ctypes присваивает `argtypes/restype`
    атрибуту функции, а обычный bound method их не принимает.
    """

    def __init__(self, имя: str, вызовы, аргументы, результат=1) -> None:
        self.имя = имя
        self._вызовы = вызовы
        self._аргументы = аргументы
        self.результат = результат

    def __call__(self, *args):
        self._вызовы.append(self.имя)
        if args:
            self._аргументы[self.имя] = args[0] if len(args) == 1 else args
        return self.результат


class _ПодменныйKernel32:
    """kernel32 без Windows: настоящий mutex не создаётся и не трогается.

    Порядок вызовов пишется в `порядок`, аргументы последнего вызова —
    в `аргументы` (у CreateMutexW сразу все три). Объявленные
    `argtypes/restype` остаются на самих функциях.
    """

    def __init__(self, handle: int, last_error: int) -> None:
        self.порядок: list[str] = []
        self.аргументы: dict[str, object] = {}
        self.CreateMutexW = _Вызов("CreateMutexW", self.порядок, self.аргументы,
                                   результат=handle)
        self.ReleaseMutex = _Вызов("ReleaseMutex", self.порядок, self.аргументы)
        self.CloseHandle = _Вызов("CloseHandle", self.порядок, self.аргументы)
        self.GetLastError = _Вызов("GetLastError", self.порядок, self.аргументы,
                                   результат=last_error)


class ОбщийПортTests(unittest.TestCase):
    """Портов больше нет: у всех копий один, привычный."""

    def test_порт_у_всех_копий_привычный(self):
        self.assertEqual(instance.порт(), int(config.PHONE_PORT))
        self.assertEqual(instance.порт(), 8765)

    def test_свободный_порт_никем_не_занят(self):
        self.assertEqual(instance.кто_на_порте(_свободный_порт()), "свободно")

    def test_свой_пульт_узнаётся_по_отпечатку(self):
        отпечаток = _подменить_отпечаток(self, "my-install-id-0123456789ab")
        чужой = _ЧужойПульт(отпечаток)
        self.addCleanup(чужой.остановить)
        self.assertEqual(instance.кто_на_порте(чужой.порт), "свой")

    def test_другая_копия_отличима_от_своей(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        чужая = _ЧужойПульт("other-install-id-9876543210zz")
        self.addCleanup(чужая.остановить)
        self.assertEqual(instance.кто_на_порте(чужая.порт), "другая копия")

    def test_старая_версия_без_api_install_тоже_видна(self):
        # 0.9.6 `/api/install` не знает, но `/api/runtime` локально доступен:
        # иначе старая Труба выглядела бы как посторонняя программа.
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        старая = _ЧужойПульт(None)
        self.addCleanup(старая.остановить)
        self.assertEqual(instance.кто_на_порте(старая.порт), "другая копия")

    def test_чужая_программа_объясняется_отдельно(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        посторонняя = _ЧужойПульт(None, runtime=False)
        self.addCleanup(посторонняя.остановить)
        self.assertEqual(instance.кто_на_порте(посторонняя.порт),
                         "чужая программа")

    def test_посторонний_http_с_runtime_не_считается_трубой(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        посторонняя = _ЧужойПульт(None, runtime_body=b'{"ok":true}')
        self.addCleanup(посторонняя.остановить)
        self.assertEqual(instance.кто_на_порте(посторонняя.порт),
                         "чужая программа")

    def test_без_аргумента_берётся_общий_порт(self):
        # Раньше аргумент назывался `порт` и затенял функцию `порт()`:
        # вызов без аргумента падал с TypeError. Проверяем именно вызов.
        with mock.patch.object(instance, "port_busy", return_value=False) as занято:
            self.assertEqual(instance.кто_на_порте(), "свободно")
        занято.assert_called_once_with(int(config.PHONE_PORT))

    def test_пустой_отпечаток_не_делает_чужий_пульт_своим(self):
        # Если install_id() не дал отпечатка (сбой файла), «свой» на порту
        # утверждаться не должен: лучше «другая копия», чем ложное окно.
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        чужая = _ЧужойПульт("other-install-id-9876543210zz")
        self.addCleanup(чужая.остановить)
        with mock.patch.object(instance, "install_id", return_value=""):
            self.assertNotEqual(instance.кто_на_порте(чужая.порт), "свой")


class ЗапретВторойКопииTests(unittest.TestCase):
    """Общий запрет: работает одна Труба, и гонка старта разрешается."""

    def setUp(self):
        self.папка = Path(tempfile.mkdtemp(prefix="truba-lock-"))
        self.addCleanup(shutil.rmtree, self.папка, True)
        # Настоящий Windows-mutex живой Трубы хозяина тесты не трогают:
        # ветку выбора подменяем аргументом, а не подменой os.name.

    def _занять(self, имя):
        # Файл-замок кладём в папку теста: в tempdir ничего не остаётся.
        with mock.patch.object(instance, "_путь_замка",
                               lambda и: self.папка / f"{и}.lock"):
            return instance.занять(имя, windows=False)

    def test_первый_занимает_второй_не_может(self):
        первый = self._занять("test-a")
        self.assertIsNotNone(первый)
        self.addCleanup(первый.отпустить)
        self.assertIsNone(self._занять("test-a"))

    def test_после_освобождения_второй_стартует(self):
        первый = self._занять("test-b")
        self.assertIsNotNone(первый)
        self.assertIsNone(self._занять("test-b"))
        первый.отпустить()
        второй = self._занять("test-b")
        self.assertIsNotNone(второй)
        второй.отпустить()

    def test_разные_имена_не_мешают_друг_другу(self):
        # Нужно тестам, а не Трубе: у копий имя общее, намеренно.
        первый = self._занять("test-c")
        второй = self._занять("test-d")
        self.assertIsNotNone(первый)
        self.assertIsNotNone(второй)
        первый.отпустить()
        второй.отпустить()


class НеWindowsЗапретTests(unittest.TestCase):
    """Вне Windows запрет держится файлом через fcntl, а не через msvcrt."""

    def test_на_unix_берётся_fcntl_без_msvcrt(self):
        # `msvcrt` на не-Windows нет: его импорт был бы ImportError.
        # Проверяем ветку выбора, не подменяя os.name у pathlib.
        вызван = []
        файл = mock.MagicMock()
        with mock.patch.object(instance.os, "name", "posix"), \
                mock.patch.object(instance, "_заблокировать_unix",
                                  side_effect=lambda ф: вызван.append(ф)):
            instance._заблокировать(файл)
        self.assertEqual(вызван, [файл])

    def test_разблокировка_тоже_по_своей_ветке(self):
        файл = mock.MagicMock()
        with mock.patch.object(instance.os, "name", "posix"), \
                mock.patch.object(instance, "_разблокировать_unix") as unix:
            instance.Замок(файл=файл).отпустить()
        unix.assert_called_once_with(файл)

    def test_файл_замка_не_лежит_в_папке_установки(self):
        # Постоянного блокирующего файла в папке хозяина быть не должно:
        # удаление Трубы не должно ничего оставлять и мешать следующему
        # запуску.
        путь = instance._путь_замка("test-x")
        self.assertIn(tempfile.gettempdir(), str(путь.parent))
        self.assertNotIn(str(config.ROOT), str(путь))


class MutexWindowsTests(unittest.TestCase):
    """Windows-ветка: один общий mutex, который освобождает ОС."""

    def test_на_windows_берётся_mutex_а_не_файл(self):
        # Общий mutex — единственное, что работает без следа на диске:
        # удаление папки установки ничего после себя не оставляет.
        с_mutex = mock.Mock(return_value="замок")
        с_файлом = mock.Mock(return_value="файл")
        with mock.patch.object(instance, "_занять_mutex", с_mutex), \
             mock.patch.object(instance, "_занять_файл", с_файлом):
            self.assertEqual(instance.занять(windows=True), "замок")
        с_mutex.assert_called_once()
        с_файлом.assert_not_called()

    def test_дескрипторы_mutex_64_битные(self):
        # Настоящий mutex не трогаем: kernel32 подменён. Проверяем, что у
        # CreateMutexW/ReleaseMutex/CloseHandle объявлены argtypes/restype —
        # иначе HANDLE усекается до int и закрывается не тот дескриптор.
        kernel32 = _ПодменныйKernel32(handle=0x7FF812345678, last_error=0)
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            instance._kernel32()
        # CreateMutexW отдаёт HANDLE — он и обязан быть c_void_p.
        self.assertEqual(kernel32.CreateMutexW.restype, ctypes.c_void_p)
        self.assertTrue(kernel32.CreateMutexW.argtypes)
        # ReleaseMutex/CloseHandle возвращают BOOL, но дескриптор принимают
        # 64-битным — иначе CloseHandle закроет обрезанное значение.
        for имя in ("ReleaseMutex", "CloseHandle"):
            функция = getattr(kernel32, имя)
            self.assertTrue(функция.argtypes, f"{имя}: нет argtypes")
            self.assertEqual(функция.argtypes, (ctypes.c_void_p,))
            self.assertIsNotNone(функция.restype, f"{имя}: нет restype")

    def test_свободный_mutex_берётся_и_отдаётся_по_документации(self):
        # Владелец (bInitialOwner) обязан сначала ReleaseMutex, потом
        # CloseHandle. Порядок важен и проверяется по порядку вызовов.
        kernel32 = _ПодменныйKernel32(handle=0x7FF812345678, last_error=0)
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            замок = instance._занять_mutex()
        self.assertIsNotNone(замок)
        kernel32.порядок.clear()
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            замок.отпустить()
        self.assertEqual(kernel32.порядок, ["ReleaseMutex", "CloseHandle"])
        # Дескриптор не усекается: ушло то же 64-битное число.
        self.assertEqual(kernel32.аргументы["CloseHandle"], 0x7FF812345678)
        kernel32.аргументы.clear()
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            instance._kernel32()
            instance._занять_mutex()
        self.assertEqual(kernel32.аргументы["CreateMutexW"][0], None)
        self.assertTrue(kernel32.аргументы["CreateMutexW"][1], "bInitialOwner")

    def test_занятый_mutex_возвращает_none_и_закрывает_дескриптор(self):
        kernel32 = _ПодменныйKernel32(handle=0x7FF812345678,
                                      last_error=instance.ERROR_ALREADY_EXISTS)
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            self.assertIsNone(instance._занять_mutex())
        self.assertEqual(kernel32.порядок,
                         ["CreateMutexW", "GetLastError", "CloseHandle"])
        # Мы не владелец (mutex уже занят), поэтому ReleaseMutex не зовём.
        self.assertNotIn("ReleaseMutex", kernel32.порядок)

    def test_неудачный_CreateMutexW_не_выдаётся_за_вторую_копию(self):
        # Нет mutex — это не «кто-то занял», а поломка. Молчать и идти
        # дальше без защиты нельзя: поднялся бы второй пульт.
        kernel32 = _ПодменныйKernel32(handle=0, last_error=5)
        with mock.patch("ctypes.windll", mock.MagicMock(kernel32=kernel32)):
            with self.assertRaises(instance.ОшибкаЗапрета) as выход:
                instance._занять_mutex()
        текст = str(выход.exception)
        self.assertIn(instance.MUTEX_NAME, текст)
        self.assertIn("5", текст)
        self.assertNotEqual(kernel32.порядок, ["CloseHandle"])

    def test_имя_mutex_общее_для_всех_папок(self):
        # Имя не зависит от папки установки — иначе запрет не общий.
        self.assertNotIn(str(config.ROOT), instance.MUTEX_NAME)
        self.assertTrue(instance.MUTEX_NAME.startswith("Global" + chr(92)))


class ЗапускПультаTests(unittest.TestCase):
    """Решение при старте: показать своё, стартовать или отказать."""

    def setUp(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        window_mod.ОТКАЗ.clear()
        window_mod._ЗАМОК = None
        self.addCleanup(setattr, window_mod, "_ЗАМОК", None)
        self.addCleanup(window_mod.ОТКАЗ.clear)

    def _решить(self, состояние, порт_свободен=False, замок=object(),
                окно_показано=False):
        with mock.patch.object(instance, "кто_на_порте", return_value=состояние), \
             mock.patch.object(instance, "занять", return_value=замок), \
             mock.patch.object(instance, "our_copy",
                               return_value=состояние == "свой"), \
             mock.patch.object(PhoneServer, "port_taken",
                               return_value=порт_свободен), \
             mock.patch.object(window_mod, "показать_уже_запущенный",
                               return_value=окно_показано) as показать:
            решение = window_mod.решить_запуск()
        return решение, показать

    def test_свободно_пульт_стартует(self):
        решение, показать = self._решить("свободно")
        self.assertEqual(решение, "стартовать")
        показать.assert_not_called()

    def test_свой_пульт_показывает_своё_окно(self):
        # Повторный щелчок по своему ярлыку: второго процесса не будет.
        решение, показать = self._решить("свой", окно_показано=True)
        self.assertEqual(решение, "показать своё")
        показать.assert_called_once()

    def test_чужой_пульт_даёт_отказ_и_окно_не_трогает(self):
        решение, показать = self._решить("другая копия")
        self.assertEqual(решение, "отказ")
        показать.assert_not_called()
        self.assertIn("Уже открыт пульт Трубы из другой папки",
                      window_mod.текст_отказа())

    def test_старая_версия_тоже_даёт_отказ(self):
        # Та же ветка, но про другое (у 0.9.6 нет /api/install): текст
        # обязан объяснять и это, а не молчать.
        решение, _ = self._решить("другая копия")
        self.assertEqual(решение, "отказ")
        self.assertIn("Закрой его и повтори запуск", window_mod.текст_отказа())

    def test_чужая_программа_объясняется_отдельно(self):
        решение, _ = self._решить("чужая программа")
        self.assertEqual(решение, "отказ")
        self.assertIn("не Трубой", window_mod.текст_отказа())

    def test_гонка_новых_копий_решается_mutex_ом(self):
        # Порт свободен у обеих, и общий mutex уже занят первой.
        with mock.patch.object(window_mod, "ЖДАТЬ_СТАРЫЙ", 0), \
             mock.patch.object(time, "sleep"):
            решение, _ = self._решить("свободно", замок=None)
        self.assertEqual(решение, "отказ")
        self.assertIn("закрывается или запускается", window_mod.текст_отказа())

    def test_обновление_дожидается_освобождения_mutex(self):
        # При перезапуске прежний процесс сперва закрывает порт и только
        # потом отпускает общий запрет. Новый должен дождаться его.
        замок = mock.MagicMock()
        with mock.patch.object(instance, "кто_на_порте", return_value="свободно"), \
             mock.patch.object(instance, "занять", side_effect=[None, замок]) as занять, \
             mock.patch.object(PhoneServer, "port_taken", return_value=False), \
             mock.patch.object(time, "sleep") as пауза:
            решение = window_mod.решить_запуск()
        self.assertEqual(решение, "стартовать")
        self.assertEqual(занять.call_count, 2)
        пауза.assert_called_once()
        window_mod._освободить_замок()

    def test_после_закрытия_первой_вторая_стартует(self):
        # Порт свободен и запрет свободен — обычный успешный старт.
        решение, _ = self._решить("свободно")
        self.assertEqual(решение, "стартовать")

    def test_поломка_запрета_объясняется_а_не_молчит(self):
        # mutex не создался: это не «работает из другой папки», а поломка.
        # Текст обязан быть конкретным, иначе хозяин ищет несуществующую
        # вторую Трубу.
        with mock.patch.object(instance, "кто_на_порте", return_value="свободно"), \
             mock.patch.object(instance, "занять",
                               side_effect=instance.ОшибкаЗапрета("код 5")):
            решение = window_mod.решить_запуск()
        self.assertEqual(решение, "отказ")
        текст = window_mod.текст_отказа()
        self.assertIn("код 5", текст)
        self.assertNotIn("из другой папки", текст)

    def test_окно_всегда_прежнее(self):
        # Второй копии с портом рядом больше нет, значит и различать окна
        # нечем: заголовок у всех один, как до 01.10.
        self.assertEqual(window_mod.заголовок_окна(), window_mod.ЗАГОЛОВОК)

class ПускОкнаTests(unittest.TestCase):
    """`запустить` целиком: окно не создаётся, но NameError ловится.

    Раньше здесь была переменная `заголовок`, которую рефакторинг удалил:
    пульт падал с `NameError` уже на `webview.create_window`. Поиск текста
    такую ошибку не видит, поэтому путь старта прогоняется с подменами.
    """

    def setUp(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")
        window_mod.ОТКАЗ.clear()
        window_mod._ЗАМОК = None
        self.addCleanup(setattr, window_mod, "_ЗАМОК", None)
        self.addCleanup(window_mod.ОТКАЗ.clear)

    def _запустить(self, решение, порт_свободен=False):
        webview = mock.MagicMock()
        webview.FileDialog.OPEN = 1
        окно = mock.MagicMock()
        webview.create_window.return_value = окно
        окно.events = mock.MagicMock()

        сервер = mock.MagicMock(port=8765)
        среда = mock.MagicMock()
        наблюдатель = mock.MagicMock()

        import ui.web_runtime as web_runtime_mod
        from core import power, settings, sysinfo

        with mock.patch.dict(sys.modules, {"webview": webview}), \
             mock.patch.object(window_mod, "решить_запуск",
                               return_value=решение), \
             mock.patch.object(window_mod, "TrayWindowController") as трей, \
             mock.patch.object(window_mod, "значок_окна", return_value=None), \
             mock.patch.object(PhoneServer, "port_taken",
                               return_value=порт_свободен), \
             mock.patch("core.phone.PhoneServer", return_value=сервер), \
             mock.patch.object(web_runtime_mod, "WebRuntime",
                               return_value=среда), \
             mock.patch.object(sysinfo, "Monitor", return_value=наблюдатель), \
             mock.patch.object(settings, "apply_to_config"), \
             mock.patch.object(power, "disable_live_runtime"):
            трей.return_value.bind.return_value = False
            window_mod.запустить()
        return webview

    def test_старт_поднимает_окно_с_правильным_заголовком(self):
        webview = self._запустить("стартовать")
        self.assertEqual(webview.create_window.call_count, 1)
        заголовок = webview.create_window.call_args.args[0]
        self.assertEqual(заголовок, window_mod.ЗАГОЛОВОК)
        url = webview.create_window.call_args.args[1]
        self.assertEqual(url, "http://127.0.0.1:8765/pult")
        webview.start.assert_called_once()

    def test_после_закрытия_окна_общий_запрет_освобождён(self):
        замок = mock.MagicMock()
        window_mod._ЗАМОК = замок
        self._запустить("стартовать")
        замок.отпустить.assert_called_once()
        self.assertIsNone(window_mod._ЗАМОК)

    def test_повторный_щелчок_выходит_молча_без_окна(self):
        # «Показать своё» — это не отказ: второй пульт не поднимаем и
        # ложного «работает из другой папки» не говорим.
        webview = self._запустить("показать своё")
        webview.create_window.assert_not_called()
        webview.start.assert_not_called()

    def test_отказ_выходит_с_текстом_для_хозяина(self):
        webview = mock.MagicMock()
        from core import settings
        with mock.patch.dict(sys.modules, {"webview": webview}), \
             mock.patch.object(settings, "apply_to_config"), \
             mock.patch.object(window_mod, "решить_запуск",
                               return_value="отказ"):
            with self.assertRaises(SystemExit) as выход:
                window_mod.запустить()
        webview.create_window.assert_not_called()
        self.assertTrue(выход.exception.code)


class ОкноTests(unittest.TestCase):
    """Окно ищется только своё; чужим заголовком не подмениться."""

    def test_ищется_окно_по_своему_заголовку(self):
        # Настоящее окно не создаём: подменяем ctypes и смотрим, что в
        # FindWindowW ушёл заголовок этой копии.
        вызовы = []

        class _User32:
            def __getattr__(self, имя):
                def функция(*args):
                    вызовы.append((имя, args))
                    return 0
                return функция

        подмена = mock.MagicMock()
        подмена.user32 = _User32()
        with mock.patch("ctypes.windll", подмена, create=True):
            self.assertFalse(window_mod.показать_уже_запущенный("Свой заголовок"))
        найдено = [args for имя, args in вызовы if имя == "FindWindowW"]
        self.assertTrue(найдено, вызовы)
        self.assertEqual(найдено[-1][-1], "Свой заголовок")


class НастоящийПультTests(unittest.TestCase):
    """Проверка на настоящем PhoneServer: только localhost и свободный порт."""

    def setUp(self):
        _подменить_отпечаток(self, "my-install-id-0123456789ab")

    def test_свой_пульт_отдаёт_свой_отпечаток(self):
        порт = _свободный_порт()
        with mock.patch.object(weather, "Watcher"):
            сервер = PhoneServer(port=порт)
            сервер.start()
            self.addCleanup(сервер._thread.join, 0.1)
            self.assertTrue(self._дождался_ответа(порт))
            self.assertEqual(instance.кто_на_порте(порт), "свой")

    def _дождался_ответа(self, порт) -> bool:
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{порт}/api/install", timeout=1) as ответ:
                    if ответ.status == 200 and ответ.read().decode().strip():
                        return True
            except OSError:
                time.sleep(0.1)
        return False


class УстановщикTests(unittest.TestCase):
    """Установщик и файлы запуска — по тексту: ничего не исполняем."""

    @classmethod
    def setUpClass(cls):
        cls.ps1 = (Path(config.ROOT) / "tools" / "install.ps1").read_bytes()[3:].decode(
            "utf-8")
        cls.bat = (Path(config.ROOT) / "razreshit_telefon.bat").read_bytes().decode(
            "ascii")
        cls.vbs = Path(config.ROOT) / "Труба.vbs"

    def test_установщик_не_поднимает_второй_пульт(self):
        # Установка заканчивается, ярлык сохраняется, но при занятом порте
        # пульт не запускается — с понятным текстом.
        self.assertIn("пульт не запускаю", self.ps1)
        # Start-Process стоит после всех проверок и выхода при отказе:
        # иначе вторая копия подняла бы второй пульт.
        начало = self.ps1.index("# Второй пульт не поднимаем")
        ветка = self.ps1[начало:]
        self.assertEqual(ветка.count("Start-Process"), 1)
        # Отказ при занятом порте выходит раньше запуска.
        self.assertLess(ветка.index("exit 0"), ветка.index("Start-Process"))
        self.assertLess(ветка.index("elseif (ПортЗанят"),
                        ветка.index("Start-Process"))

    def test_установщик_не_считает_любой_занятый_порт_трубой(self):
        # Занятый 8765 может держать посторонняя программа: винить Трубу
        # нельзя. Сообщение нейтральное, варианты перечислены.
        self.assertIn("порт 8765 уже занят кем-то другим", self.ps1)
        self.assertIn("или посторонняя программа", self.ps1)
        self.assertNotIn("Порт 8765 занят другой Трубой", self.ps1)

    def test_vbs_проверяет_чужую_копию_до_конца_ожидания(self):
        # Проверка была только на второй итерации: чужая копия, занявшая
        # порт на десятой секунде, дала бы 30 с ожидания и неверное сообщение.
        текст = self.vbs.read_bytes().decode("utf-16")
        self.assertNotIn("If attempt = 2 Then", текст)
        self.assertIn("attempt Mod 8 = 0", текст)
        # И по-прежнему без фокусировки чужого окна.
        self.assertNotIn("AppActivate", текст)

    def test_установщик_не_ищет_соседний_порт(self):
        self.assertNotIn("install_port.txt", self.ps1)
        self.assertNotIn("свободного порта рядом", self.ps1)
        self.assertIn("$порт = 8765", self.ps1)

    def test_установщик_сверяет_отпечаток_а_не_только_порт(self):
        self.assertIn("НашПультОтвечает", self.ps1)
        self.assertIn("/api/install", self.ps1)
        self.assertIn("install_id.txt", self.ps1)

    def test_установщик_не_перезаписывает_чужой_ярлык(self):
        self.assertIn("ЯрлыкЧужой", self.ps1)
        self.assertIn("оставляю его", self.ps1)
        # Вторая папка — не сбой: жёлтым и словом «принадлежит» хозяин
        # принял это за ошибку установки (01.10).
        self.assertIn("Это не ошибка", self.ps1)
        self.assertNotIn("принадлежит другой установке", self.ps1)

    def test_установщик_проверяет_окружение_до_обеих_веток_успеха(self):
        # «Готово» при неработающем окружении — ровно тот случай, из-за
        # которого пульт отвечал «Папка .venv повреждена или отсутствует».
        вызов = self.ps1.index("Проверить-Окружение")
        self.assertLess(вызов, self.ps1.index("if ($NoLaunch) {"))
        self.assertIn("pyvenv.cfg", self.ps1)
        self.assertIn("site-packages", self.ps1)
        self.assertIn("import fastapi", self.ps1)

    def test_установщик_пишет_отпечаток_без_bom(self):
        # Set-Content -Encoding UTF8 в PowerShell 5.1 пишет BOM: VBS читает
        # файл как ANSI и получает мусор вместо отпечатка.
        self.assertNotIn("Set-Content -LiteralPath $файлОтпечатка", self.ps1)
        self.assertIn("[Text.Encoding]::ASCII", self.ps1)

    def test_сверка_с_живым_локальным_http_ответом(self):
        # Проверяем именно PowerShell-функцию на настоящем локальном сокете.
        # Статическая проверка не поймала бы ошибку: функция декодирует
        # целиком буфер вместо числа прочитанных байтов.
        powershell = shutil.which("powershell.exe")
        if not powershell:
            self.skipTest("Windows PowerShell недоступен")
        отпечаток = "test-local-install-id-123456789"
        сервер = _ЧужойПульт(отпечаток)
        self.addCleanup(сервер.остановить)
        функция = self.ps1.split("function НашПультОтвечает", 1)[1]
        функция = "function НашПультОтвечает" + функция.split("# Дальше", 1)[0]
        for ожидаем, проверяемый in ((0, отпечаток), (1, "другая-копия")):
            script = (функция + f"\nif (НашПультОтвечает {сервер.порт} "
                      f"'{проверяемый}') {{ exit 0 }} else {{ exit 1 }}")
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True, timeout=10)
            self.assertEqual(result.returncode, ожидаем,
                             result.stderr.decode("utf-8", "replace"))

    def test_сверка_принимает_только_статус_200(self):
        # Отпечаток как подстрока всего ответа приниматься не должен.
        self.assertIn("-notmatch '^HTTP/1\\.[01] 200'", self.ps1)
        self.assertIn("-ceq $Отпечаток", self.ps1)

    def test_vbs_быстро_отличает_свою_от_чужой(self):
        байты = self.vbs.read_bytes()
        self.assertEqual(байты[:2], b"\xff\xfe", "VBScript читает только UTF-16 с BOM")
        текст = байты.decode("utf-16")
        self.assertIn("PortOwner", текст)
        self.assertIn("Уже открыт пульт Трубы из другой папки.",
                      текст)
        # Проверка обязана быть до запуска Python, иначе будет 30 с ожидания.
        self.assertLess(текст.index("state = PortOwner()"),
                        текст.index("shell.Run command"))
        # Никаких соседних портов и никакой фокусировки чужого окна.
        self.assertNotIn("install_port.txt", текст)
        self.assertNotIn("AppActivate", текст)
        self.assertNotIn("Win32_Process", текст)

    def test_vbs_видит_старую_версию_по_api_runtime(self):
        # 0.9.6 не знает /api/install, но /api/runtime локально доступен.
        текст = self.vbs.read_bytes().decode("utf-16")
        self.assertIn("/api/install", текст)
        self.assertIn("/api/runtime", текст)

    def _vbs_состояние_порта(self, порт: int, отпечаток: str) -> str:
        cscript = shutil.which("cscript.exe")
        if not cscript:
            self.skipTest("Windows Script Host недоступен")
        текст = self.vbs.read_bytes().decode("utf-16")
        функции = []
        for имя in ("ValidInstallId", "PortOwner"):
            начало = текст.index(f"Function {имя}(")
            конец = текст.index("End Function", начало) + len("End Function")
            функции.append(текст[начало:конец])
        проба = ("Option Explicit\r\nDim myPort, myId\r\n"
                 f"myPort = {порт}\r\nmyId = \"{отпечаток}\"\r\n"
                 + "\r\n".join(функции) + "\r\n"
                 "Select Case PortOwner()\r\n"
                 "Case \"никто\": WScript.Echo \"NONE\"\r\n"
                 "Case \"свой\": WScript.Echo \"OWN\"\r\n"
                 "Case \"чужая\": WScript.Echo \"OTHER\"\r\n"
                 "Case \"порт занят\": WScript.Echo \"BUSY\"\r\n"
                 "End Select\r\n")
        with tempfile.TemporaryDirectory() as папка:
            путь = Path(папка) / "port_probe.vbs"
            путь.write_bytes(b"\xff\xfe" + проба.encode("utf-16-le"))
            результат = subprocess.run([cscript, "//NoLogo", str(путь)],
                                       capture_output=True, timeout=12)
        self.assertEqual(результат.returncode, 0,
                         (результат.stdout + результат.stderr)
                         .decode("utf-8", "replace"))
        return результат.stdout.decode("ascii").strip()

    def test_vbs_свободный_порт_не_называет_другой_трубой(self):
        # Регрессия 01.10: при ошибке подключения к пустому порту выражение
        # `Err.Number = 0 And request.status = 200` возвращало «чужая».
        self.assertEqual(self._vbs_состояние_порта(_свободный_порт(), "a" * 64),
                         "NONE")

    def test_vbs_различает_свою_чужую_и_посторонний_http(self):
        свой = _ЧужойПульт("a" * 64)
        чужой = _ЧужойПульт("b" * 64)
        старый = _ЧужойПульт(None)
        посторонний = _ЧужойПульт(None, runtime_body=b'{"ok":true}')
        for сервер in (свой, чужой, старый, посторонний):
            self.addCleanup(сервер.остановить)
        for сервер, ожидание in ((свой, "OWN"), (чужой, "OTHER"),
                                 (старый, "OTHER"), (посторонний, "BUSY")):
            self.assertEqual(self._vbs_состояние_порта(сервер.порт, "a" * 64),
                             ожидание)

    def test_vbs_не_утверждает_что_установка_не_дошла_до_конца(self):
        текст = self.vbs.read_bytes().decode("utf-16")
        self.assertNotIn("установка не дошла до конца", текст)
        self.assertIn("install.log", текст)

    def test_брандмауэр_открывает_прежний_порт(self):
        # Никаких новых правил и никаких диапазонов портов.
        self.assertIn("set PORT=8765", self.bat)
        self.assertIn("localport=%PORT%", self.bat)
        self.assertNotIn("install_port.txt", self.bat)

    def test_vbs_остался_в_своей_кодировке(self):
        # VBS не исполняем (запуск Трубы запрещён), но структуру проверяем:
        # VBScript не переживёт, если файл переедет в UTF-8 или потеряет
        # парность Sub/Function.
        текст = self.vbs.read_bytes().decode("utf-16")
        self.assertTrue(текст.startswith("Option Explicit"))
        self.assertEqual(текст.count("\nSub "), текст.count("\nEnd Sub"))
        self.assertEqual(текст.count("\nFunction "), текст.count("\nEnd Function"))
        self.assertNotIn("install_port.txt", текст)

    def test_vbs_компилируется_без_запуска_трубы(self):
        cscript = shutil.which("cscript.exe")
        if not cscript:
            self.skipTest("Windows Script Host недоступен")
        текст = self.vbs.read_bytes().decode("utf-16")
        первая, остаток = текст.split("\r\n", 1)
        self.assertEqual(первая, "Option Explicit")
        # Первая исполняемая строка завершает пробу. Движок всё равно
        # компилирует весь файл и ловит ошибки даже в недостижимых функциях.
        with tempfile.TemporaryDirectory() as папка:
            проба = Path(папка) / "compile_only.vbs"
            проба.write_bytes(b"\xff\xfe" +
                              (первая + "\r\nWScript.Quit\r\n" + остаток)
                              .encode("utf-16-le"))
            результат = subprocess.run([cscript, "//NoLogo", str(проба)],
                                       capture_output=True, timeout=10)
        self.assertEqual(результат.returncode, 0,
                         (результат.stdout + результат.stderr)
                         .decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
