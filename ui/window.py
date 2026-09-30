r"""Пульт в окне: страница внутри настоящего окна Windows.

Почему не как раньше. Старый пульт на CustomTkinter лагал при
перетаскивании и растягивании: тулкит пересобирает виджеты на каждое
событие размера, а все пять вкладок жили одновременно. Лечится только
сменой основания — см. coordination/ПУЛЬТ_ВЕБ.md.

Окно даёт WebView2, он в Windows 10 уже стоит (проверено, версия 153).
Рамка у окна родная: рисовать свою значит таскать и тянуть окно кодом,
а это ровно те лаги, от которых уходим.

Сервер тот же самый, что раздаёт страницу телефону. Процесс один,
порт один, страницы две: телефонная под палец, эта под мышь.
"""

from __future__ import annotations

import os
import site
import sys

# Труба.vbs запускает системный pythonw и подкладывает пакеты через
# PYTHONPATH, а так Python не читает .pth-файлы. Без pywin32.pth нет
# win32api, и 26 сентября снимок экрана падал: «No module named 'win32api'».
for _путь in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    if _путь.lower().endswith("site-packages"):
        site.addsitedir(_путь)

import config  # noqa: E402

ЗАГОЛОВОК = "Труба — пульт"
# Сколько новый пульт ждёт, пока закроется прежний (выгрузка голоса и потоков).
ЖДАТЬ_СТАРЫЙ = 20.0


def разобрать_аргументы(аргументы) -> dict:
    """Ключи запуска окна. Отдельно от запуска, чтобы тестировать без окна.

    `--tray` приходит от ярлыка автозагрузки: при входе в Windows окно
    пульта не должно выскакивать поверх всего — только значок в трее.
    """
    аргументы = [str(а) for а in (аргументы or [])]
    return {"в_трей": "--tray" in аргументы}


class PultBridge:
    """Нативный выбор файла: браузер не раскрывает путь к чужому exe."""

    def __init__(self, runtime) -> None:
        # pywebview проходит рекурсивно по открытым атрибутам js_api.
        # Window/Runtime здесь должны оставаться приватными, иначе обход
        # уходит в WinForms и подвешивает весь пульт.
        self._runtime = runtime
        self._window = None

    def pick_icon(self, app_id: str) -> dict:
        import webview

        if self._window is None:
            return {"ok": False, "error": "окно ещё не готово"}
        files = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=(
                "Программы и картинки (*.exe;*.dll;*.ico;*.lnk;*.png;*.jpg;*.jpeg;*.webp;*.bmp)",
                "Все файлы (*.*)",
            ),
        )
        if not files:
            return {"ok": False, "cancelled": True}
        return self._runtime.apps_icon_from_path(app_id, files[0])

    def pick_folder(self, start: str) -> dict:
        """Выбор папки заметок: в браузере такую папку не выбрать."""
        from pathlib import Path

        import webview

        if self._window is None:
            return {"ok": False, "error": "окно ещё не готово"}
        # Стартовать диалог можно только из настоящей папки: на пустом или
        # несуществующем пути pywebview открыл бы его от корня диска.
        каталог = ""
        if isinstance(start, str) and start.strip():
            путь = Path(start.strip())
            if путь.is_dir():
                каталог = str(путь)
        files = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory=каталог
        )
        if not files:
            return {"ok": False, "cancelled": True}
        return {"ok": True, "path": str(files[0])}

    def pick_program(self, taken: list[str]) -> dict:
        """Выбор программы как в старом пульте, без ручного ввода пути."""
        from pathlib import Path

        import webview

        from core import launcher

        if self._window is None:
            return {"ok": False, "error": "окно ещё не готово"}
        if not isinstance(taken, list) or len(taken) > 100 or any(
            not isinstance(item, str) or len(item) > 64 for item in taken
        ):
            return {"ok": False, "error": "неверный список кнопок"}
        files = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=(
                "Программы (*.exe;*.bat;*.cmd;*.lnk)",
                "Все файлы (*.*)",
            ),
        )
        if not files:
            return {"ok": False, "cancelled": True}
        path = Path(files[0])
        if not path.is_file() or path.suffix.lower() not in {".exe", ".bat", ".cmd", ".lnk"}:
            return {"ok": False, "error": "выбери файл программы (.exe, .bat, .cmd или .lnk)"}
        title = (path.stem.replace("_", " ").strip() or "Программа")[:80]
        title = title[:1].upper() + title[1:]
        return {"ok": True, "app": {
            "id": launcher.make_id(title[:60], taken), "title": title,
            "kind": "app", "path": str(path), "args": [], "how": "shell",
            "icon": "app", "icon_source": "exe",
        }}


class TrayWindowController:
    """Крестик и сворачивание прячут окно, выход из трея завершает процесс."""

    def __init__(self, window) -> None:
        self._window = window
        self._tray = None
        self._quitting = False
        self._notified = False

    def bind(self) -> bool:
        from ui.tray import Tray

        tray = Tray(on_show=self.show, on_quit=self.quit)
        if not tray.available:
            return False
        try:
            tray.start()
        except Exception:
            tray.stop()
            return False
        self._tray = tray
        self._window.events.closing += self.closing
        self._window.events.minimized += self.minimized
        return True

    def _hide(self) -> None:
        if self._tray is None or self._quitting:
            return
        self._window.hide()
        if not self._notified:
            self._tray.notify("Труба работает в фоне. Открой пульт через значок рядом с часами.")
            self._notified = True

    def closing(self) -> bool | None:
        if self._tray is None or self._quitting:
            return None
        self._hide()
        return False  # pywebview отменяет закрытие, процесс и телефон остаются живы

    def minimized(self) -> None:
        self._hide()

    def show(self) -> None:
        self._window.show()
        self._window.restore()
        self._notified = False

    def quit(self) -> None:
        self._quitting = True
        self._window.destroy()

    def stop(self) -> None:
        if self._tray is not None:
            self._tray.stop()


def показать_уже_запущенный() -> bool:
    """Повторный запуск поднимает окно из трея, не открывая второй процесс."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, ЗАГОЛОВОК)
    if not hwnd:
        return False
    user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE: показывает и разворачивает
    user32.SetForegroundWindow(hwnd)
    return True


def запустить(в_трей: bool = False) -> None:
    import webview

    from core import power, settings
    from core.phone import PhoneServer

    settings.apply_to_config()

    сервер = PhoneServer()
    if PhoneServer.port_taken(сервер.port):
        if показать_уже_запущенный():
            return
        # Окна нет, а порт занят — прежний пульт ещё закрывается: окно уже
        # исчезло, а он выгружает голос и гасит потоки. 25 сентября хозяин
        # нажал ярлык сразу после закрытия, новый пульт молча вышел, и ярлык
        # трижды сказал «не запустился». Ждём, пока старый доберётся до конца.
        import time

        срок = time.monotonic() + ЖДАТЬ_СТАРЫЙ
        while PhoneServer.port_taken(сервер.port) and time.monotonic() < срок:
            time.sleep(0.5)
    if PhoneServer.port_taken(сервер.port):
        raise SystemExit(
            f"Порт {сервер.port} занят. Похоже, работает старый пульт — "
            "закрой его окно и запусти снова."
        )

    from ui.web_runtime import WebRuntime

    среда = WebRuntime(сервер)
    сервер.attach(среда.handle_event)

    сервер.start()

    # Только настоящий запущенный пульт может вызвать системное питание.
    # Отдельные проверки и файловая копия Cline импортируют тот же модуль,
    # но не проходят эту точку входа.
    if __name__ == "__main__":
        power.enable_live_runtime()

    # Нагрузка железа для карточек и для телефона. Тот же поток, что и
    # раньше: он шлёт снимок в страницу телефона, а пульт спрашивает сам.
    from core.sysinfo import Monitor

    наблюдатель = Monitor(сервер)
    наблюдатель.start()

    # Голос — сразу, если так настроено (Голос → Слух). Своим потоком:
    # модели грузятся до полуминуты, окно ждать не должно.
    import threading

    threading.Thread(target=среда.voice_autostart, daemon=True).start()

    bridge = PultBridge(среда)
    окно = webview.create_window(
        ЗАГОЛОВОК,
        f"http://127.0.0.1:{сервер.port}/pult",
        js_api=bridge,
        width=1280,
        height=820,
        min_size=(1000, 640),
        # Фон окна — под тему из настроек: тёмное окно под светлую страницу
        # моргает чёрным, и это видно каждый раз при запуске.
        background_color=config.фон_окна(config.THEME),
        # Старт с Windows (/tray): окно сразу скрыто, пульт живёт в трее и
        # показывается только по «Открыть пульт» — иначе при входе в Windows
        # окно выскакивало бы поверх всего.
        hidden=в_трей,
    )
    bridge._window = окно

    управление_окном = TrayWindowController(окно)
    # Пульт умеет закрывать себя сам (перезапуск после обновления). Здесь
    # он знает, чем именно окно гасится: `quit` — это выход из трея, а
    # `closing` только прячет окно.
    среда._pult_close = управление_окном.quit

    # Сервер поднимается в своём потоке и уже слушает; окно ждать его не
    # должно — страница сама переспросит состояние через две секунды.
    def прибраться():
        управление_окном.stop()
        try:
            среда.close()
        except Exception:
            pass
        наблюдатель.stop()

    try:
        трей_есть = управление_окном.bind()
        if в_трей and not трей_есть:
            # Трея нет — показать пульт было бы нечем, а скрытое окно хозяин
            # уже не откроет. Лучше обычное окно, чем пульт в никуда.
            # Не `окно.show()`: до `webview.start()` окна ещё нет, и show
            # ждал бы его 20 с. Флаг pywebview читает при создании окна.
            окно.hidden = False
        webview.start(icon=значок_окна())
    finally:
        power.disable_live_runtime()
        прибраться()


def значок_окна() -> str | None:
    """Значок Трубы для заголовка окна и панели задач вместо значка Python.

    pywebview берёт значок окна из файла `.ico`, а панель задач Windows
    группирует окна по «приложению» — без своего AppUserModelID пульт
    числился бы за pythonw.exe и показывал его змею.
    """
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("levkeyvill.Truba")
    except Exception:
        pass
    from core import app_icon

    путь = app_icon.ensure()
    return str(путь) if путь else None


if __name__ == "__main__":
    config.setup_console()
    запустить(**разобрать_аргументы(sys.argv[1:]))
