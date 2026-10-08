"""Рамка своего окна исчезает вместе с управлением главной страницы."""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)
FRAME_BITS = 0x00C00000 | 0x00040000  # WS_CAPTION | WS_THICKFRAME
FRAME_REFRESH = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010


class WindowsFrame:
    def __init__(self):
        self._api = ctypes.WinDLL('user32', use_last_error=True)
        suffix = 'PtrW' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'W'
        self._get = getattr(self._api, 'GetWindowLong' + suffix)
        self._set = getattr(self._api, 'SetWindowLong' + suffix)
        self._get.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get.restype = ctypes.c_ssize_t
        self._set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._set.restype = ctypes.c_ssize_t
        self._api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self._api.SetWindowPos.restype = wintypes.BOOL
        self._api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    def style(self, handle):
        ctypes.set_last_error(0)
        value = self._get(handle, -16)
        if value == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def apply(self, handle, style):
        previous = self.style(handle)
        ctypes.set_last_error(0)
        if self._set(handle, -16, style) == 0 and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if not self._api.SetWindowPos(handle, None, 0, 0, 0, 0, FRAME_REFRESH):
            error = ctypes.get_last_error()
            self._set(handle, -16, previous)
            self._api.SetWindowPos(handle, None, 0, 0, 0, 0, FRAME_REFRESH)
            raise ctypes.WinError(error)

    def pointer_inside(self, handle):
        cursor, outer = wintypes.POINT(), wintypes.RECT()
        if not (self._api.GetCursorPos(ctypes.byref(cursor))
                and self._api.GetWindowRect(handle, ctypes.byref(outer))):
            return True
        return outer.left <= cursor.x < outer.right and outer.top <= cursor.y < outer.bottom


def on_window_thread(native, operation):
    if not native.InvokeRequired:
        return operation()
    from System import Action

    result, errors = [], []
    def call():
        try:
            result.append(operation())
        except Exception as exc:
            errors.append(exc)
    native.Invoke(Action(call))
    if errors:
        raise errors[0]
    return result[0]


class WindowChrome:
    def __init__(self, window, backend=None):
        self._window = window
        self._backend = backend
        self._frame_bits = None
        self._handle = None

    def set_quiet(self, quiet):
        native = self._window.native
        if native is None:
            return {'ok': False, 'error': 'окно ещё не готово'}
        if sys.platform != 'win32' and self._backend is None:
            return {'ok': True, 'supported': False}
        try:
            return on_window_thread(native, lambda: self._apply(native, quiet))
        except Exception:
            log.exception('не удалось изменить рамку пульта')
            return {'ok': False, 'error': 'рамка окна недоступна'}

    def _apply(self, native, quiet):
        # В обычном окне рамка постоянная: её смена двигает содержимое под курсором.
        quiet = quiet and str(native.WindowState) == 'Maximized'
        if quiet and not native.Enabled:
            return {'ok': True, 'deferred': True}
        if self._backend is None:
            self._backend = WindowsFrame()
        handle = native.Handle.ToInt64()
        if self._handle != handle:
            self._frame_bits = None
            self._handle = handle
        style = self._backend.style(handle)
        if quiet:
            if self._frame_bits is None and style & FRAME_BITS:
                if self._backend.pointer_inside(handle):
                    return {'ok': True, 'deferred': True}
                self._frame_bits = style & FRAME_BITS
                try:
                    self._backend.apply(handle, style & ~FRAME_BITS)
                except Exception:
                    self._frame_bits = None
                    raise
        elif self._frame_bits is not None:
            self._backend.apply(handle, (style & ~FRAME_BITS) | self._frame_bits)
            self._frame_bits = None
        return {'ok': True}


def confirm_close(window):
    message = 'Закрыть Трубу?\nАссистент и подключение телефона остановятся.'
    if sys.platform != 'win32':
        return window.create_confirmation_dialog('Закрыть Трубу?', message)
    native = window.native
    if native is None:
        return False
    def ask():
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        api.MessageBoxW.restype = ctypes.c_int
        # Yes/No, вопрос, по умолчанию «Нет»; диалог принадлежит своему окну.
        return api.MessageBoxW(native.Handle.ToInt64(), message, 'Закрыть Трубу?', 0x04 | 0x20 | 0x100) == 6
    return on_window_thread(native, ask)
