r"""Чтение и запись текста в буфере обмена Windows (`CF_UNICODETEXT`).

Буфер общий для программ: открытие повторяется после короткой паузы, захваченный
блок памяти освобождается через `GlobalUnlock`, а буфер закрывается в `finally`.
Типы WinAPI объявлены явно: без `restype` адрес на 64-битной Windows обрезается
до 32 бит. Чтение не меняет буфер; запись через `set_text` допускается только
по прямой просьбе через `put_clipboard` (`core/hands.py`).
"""

import ctypes
import time

# Поддерживается только текст Unicode; другие форматы дают явный отказ.
CF_UNICODETEXT = 13

# Предел чтения вслух совпадает с `core/documents.py::TEXT_LIMIT`;
# усечённый текст помечается `cut`.
MAX_CHARS = 12000
# Для разбора и перевода используется предел `MODEL_TEXT_LIMIT` документов.
MODEL_CHARS = 30000

# Запись свыше предела отклоняется: тихая обрезка изменила бы вставляемый текст.
MAX_PUT = 100000

# `GMEM_MOVEABLE` — память, блок которой можно отдать системе: после
# `SetClipboardData` им владеет уже буфер обмена, а не мы. Обычная память
# (`GMEM_FIXED`) системе отдать нельзя, `SetClipboardData` её не примет.
GMEM_MOVEABLE = 0x0002

# Число попыток и пауза при временной блокировке общего буфера.
ПОВТОРОВ = 5
ПАУЗА = 0.02


def _user32():
    """Системная библиотека окон. Отдельной функцией — её подменяют тесты."""
    return ctypes.windll.user32


def _kernel32():
    """Системная библиотека памяти: ею берётся и отпускается блок буфера."""
    return ctypes.windll.kernel32


# `HANDLE` и `HGLOBAL` — указатели. Без `restype` ctypes считает возврат
# 32-битным `c_int` и обрезает адрес на 64-битной Windows.
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
UINT = ctypes.c_uint


def _подписи(user32, kernel32) -> None:
    """Объявить `argtypes` и `restype` всем вызовам, что тут используются.

    Типы задаются перед использованием; сама настройка не вызывает WinAPI.

    `GetClipboardData` и `GlobalLock` возвращают **адрес**, поэтому `restype`
    у них — `c_void_p` (`HANDLE`/`HGLOBAL`), а не `c_int`. Первым аргументом
    `GlobalLock`/`GlobalUnlock` идёт дескриптор — он тоже указательный.
    """
    user32.OpenClipboard.argtypes = (ctypes.c_void_p,)  # HWND окна
    user32.OpenClipboard.restype = BOOL
    user32.CloseClipboard.argtypes = ()
    user32.CloseClipboard.restype = BOOL
    user32.IsClipboardFormatAvailable.argtypes = (UINT,)
    user32.IsClipboardFormatAvailable.restype = BOOL
    # Возврат — дескриптор блока памяти, а не текст: 8 байт на 64 битах.
    user32.GetClipboardData.argtypes = (UINT,)
    user32.GetClipboardData.restype = HANDLE
    # Возврат — адрес внутри блока: тот самый, который обрезать нельзя.
    kernel32.GlobalLock.argtypes = (HANDLE,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (HANDLE,)
    kernel32.GlobalUnlock.restype = BOOL


def _подписи_записи(user32, kernel32) -> None:
    """Объявить типы вызовов, которыми пишут в буфер (`set_text`).

    Отдельной функцией от `_подписи` — чтобы чтение осталось ровно тем же,
    чем было: у него лишних вызовов не появилось. Типы здесь важны так же,
    как при чтении, и по той же причине: `GlobalAlloc` и `SetClipboardData`
    возвращают и принимают **дескрипторы блока памяти**, а без объявленного
    указательного `restype` `ctypes` обрежет адрес до 32 бит, и на
    64-битной машине система получит не тот блок.
    """
    user32.EmptyClipboard.argtypes = ()
    user32.EmptyClipboard.restype = BOOL
    # Второй аргумент — наш блок памяти (HGLOBAL), первый — формат.
    user32.SetClipboardData.argtypes = (UINT, HANDLE)
    user32.SetClipboardData.restype = HANDLE
    kernel32.GlobalAlloc.argtypes = (UINT, ctypes.c_size_t)
    # Возврат — HGLOBAL: тот же самый дескриптор, что потом отдаём системе.
    kernel32.GlobalAlloc.restype = HANDLE
    kernel32.GlobalFree.argtypes = (HANDLE,)
    kernel32.GlobalFree.restype = HANDLE
    # Запираем мы свой собственный блок, но объявлять типы надо и здесь:
    # без объявленного `restype` `GlobalLock` отдал бы адрес, обрезанный до
    # 32 бит, и `memmove` записал бы текст мимо блока.
    kernel32.GlobalLock.argtypes = (HANDLE,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (HANDLE,)
    kernel32.GlobalUnlock.restype = BOOL


def _открыть(user32, повторов: int = ПОВТОРОВ, пауза: float = ПАУЗА) -> bool:
    """Открыть буфер с повторами: он общий на всю систему.

    Другой процесс может удерживать общий буфер короткое время.
    """
    for номер in range(max(1, int(повторов))):
        try:
            if user32.OpenClipboard(None):
                return True
        except Exception:
            return False
        if номер + 1 < повторов:
            time.sleep(пауза)
    return False


def _отказ(why: str) -> dict:
    """Честный отказ: текста нет, но причина — есть."""
    return {"ok": False, "text": "", "chars": 0, "cut": False, "why": str(why)}


def _готово(text: str, chars: int, cut: bool) -> dict:
    return {"ok": True, "text": text, "chars": int(chars), "cut": bool(cut)}


def text(limit: int = MAX_CHARS) -> dict:
    """Текст буфера обмена. Ответ — тот же словарь, что у `core/documents.py`.

    `{"ok", "text", "chars" (знаков всего, до обрезки), "cut"}`, а при отказе
    — ещё `why` и пустой текст. Чтение не меняет буфер.
    """
    try:
        user32 = _user32()
        kernel32 = _kernel32()
    except Exception as exc:
        return _отказ(f"буфер обмена недоступен: {type(exc).__name__}: {exc}")

    # Сигнатуры объявляем на настоящих вызовах перед первым обращением:
    # адрес блока памяти должен прийти целиком, а не обрезанным до 32 бит.
    _подписи(user32, kernel32)

    if not _открыть(user32):
        return _отказ("буфер обмена держит другая программа — скажи ещё раз")

    try:
        try:
            есть = user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
        except Exception:
            есть = False
        if not есть:
            return _отказ("в буфере обмена нет текста — там картинка или файл")
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return _отказ("в буфере обмена нет текста")
        адрес = kernel32.GlobalLock(handle)
        if not адрес:
            return _отказ("не смогла прочитать буфер обмена")
        try:
            # Блок памяти — это null-строка в UTF-16, а не строка Python:
            # `c_wchar_p` сам остановится на нуле, хвост мусора не попадёт.
            значение = ctypes.c_wchar_p(адрес).value or ""
        finally:
            # Блок отпускается в любом случае: запертая память чужого
            # процесса — это его полноценная утечка, а не мелочь.
            try:
                kernel32.GlobalUnlock(handle)
            except Exception:
                pass
    except Exception as exc:
        return _отказ(f"не прочитала буфер обмена: {type(exc).__name__}: {exc}")
    finally:
        # Буфер закрывается всегда — иначе следующее чтение его уже не откроет.
        try:
            user32.CloseClipboard()
        except Exception:
            pass

    # Переносы строк приходят из Windows как CRLF, а синтез и модель ждут
    # обычный перенос: без этого «хватит» посреди строки ломается.
    значение = значение.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not значение:
        return _отказ("в буфере обмена пусто — читать нечего")

    всего = len(значение)
    обрезан = False
    if 0 < limit < всего:
        значение = значение[:limit].rstrip()
        обрезан = True
    return _готово(значение, всего, обрезан)


def set_text(text: str) -> dict:
    """Положить готовый текст в буфер обмена вместо старого.

    `{"ok": True, "chars": N}` — знаков сколько положили; при отказе
    `{"ok": False, "why": …}`. Исключений наружу не бывает: зовут голосовой
    инструмент и тесты.

    Порядок вызовов WinAPI:

        OpenClipboard (с повторами) → EmptyClipboard → GlobalAlloc →
        GlobalLock → memmove текста с нулём → GlobalUnlock →
        SetClipboardData → CloseClipboard (в `finally`)

    Что здесь легко сделать не так и почему:

      * `EmptyClipboard` обязателен перед `SetClipboardData`, чтобы убрать
        прежнее содержимое;
      * блок памяти — `GMEM_MOVEABLE`: только такой системе можно отдать;
      * размер `(len + 1) * 2` — UTF-16 плюс завершающий ноль. Без нуля
        `GetClipboardData` прочитает по мусору из хвоста блока;
      * при неудаче `SetClipboardData` блок освобождаем (`GlobalFree`) — он
        наш, иначе это утечка памяти процесса. А при успехе **не**
        освобождаем: им теперь владеет система, и `GlobalFree` здесь
        забрал бы у буфера ровно то, что мы в него положили;
      * `CloseClipboard` — в `finally`, как и при чтении.

    Запись допускается только по прямой просьбе через `put_clipboard`
    (`core/hands.py`), который требует основание `because`.
    """
    значение = str(text or "")
    if not значение.strip():
        return _не_положено("положить нечего — текст пустой")
    if len(значение) > MAX_PUT:
        return _не_положено(f"текст слишком длинный: {len(значение)} знаков, "
                            f"а в буфер кладу до {MAX_PUT}. Попроси разбить его "
                            f"на части — молча обрезать нельзя")

    try:
        user32 = _user32()
        kernel32 = _kernel32()
    except Exception as exc:
        return _не_положено(f"буфер обмена недоступен: {type(exc).__name__}: {exc}")

    # Сигнатуры — до первого обращения: адрес блока должен прийти целиком.
    _подписи_записи(user32, kernel32)

    if not _открыть(user32):
        return _не_положено("буфер обмена держит другая программа — скажи ещё раз")

    # Блок наш — значит, пока `наш` не сброшен, его надо освободить при
    # уходе. На успехе `SetClipboardData` обнуляем: дальше им владеет
    # система, и освобождать его нельзя.
    наш = 0
    try:
        try:
            if not user32.EmptyClipboard():
                return _не_положено("не смогла очистить буфер обмена")
            # Размер — по готовому буферу UTF-16, а не по `len`: эмодзи и
            # прочее за пределами BMP Python считает одним знаком, а в UTF-16
            # это два. По `len` блок вышел бы короче, ноль в конце не влез бы,
            # и вставка прочла бы мусор из-за блока.
            исходник = ctypes.create_unicode_buffer(значение)
            размер = ctypes.sizeof(исходник)
            наш = kernel32.GlobalAlloc(GMEM_MOVEABLE, размер)
            if not наш:
                return _не_положено("не хватило памяти под текст")
            адрес = kernel32.GlobalLock(наш)
            if not адрес:
                return _не_положено("не смогла подготовить текст для буфера")
            try:
                # UTF-16 с завершающим нулём: `create_unicode_buffer` сам
                # добавляет `\0`, и копия — ровно весь буфер.
                ctypes.memmove(адрес, исходник, размер)
            finally:
                try:
                    kernel32.GlobalUnlock(наш)
                except Exception:
                    pass
            if not user32.SetClipboardData(CF_UNICODETEXT, наш):
                return _не_положено("буфер обмена не принял текст")
            # Принял — им владеет система. Больше не освобождаем: см. докстроку.
            наш = 0
        except Exception as exc:
            return _не_положено(f"не положила в буфер обмена: "
                                f"{type(exc).__name__}: {exc}")
        finally:
            if наш:
                # Отказ на любом шаге: блок остался нашим, иначе утечка.
                try:
                    kernel32.GlobalFree(наш)
                except Exception:
                    pass
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass
    return {"ok": True, "chars": len(значение)}


def _не_положено(why: str) -> dict:
    """Честный отказ записи: в буфере ничего не поменялось."""
    return {"ok": False, "chars": 0, "why": str(why)}


def journal_put_line(положено: dict) -> str:
    """Строка в журнал после записи: «буфер обмена: положено 840 знаков».

    Содержимое буфера в журнал не записывается.
    """
    знаков = int(положено.get("chars") or 0)
    from core.speech_text import plural

    return (f"буфер обмена: положено {знаков} "
            f"{plural(знаков, ('знак', 'знака', 'знаков'))}")


def journal_line(прочитан: dict) -> str:
    """Строка в журнал: «буфер обмена: 840 знаков». Без текста.

    Содержимое буфера в журнал не записывается.
    """
    знаков = int(прочитан.get("chars") or 0)
    from core.speech_text import plural

    куски = [f"буфер обмена: {знаков} "
             f"{plural(знаков, ('знак', 'знака', 'знаков'))}"]
    if прочитан.get("cut"):
        куски.append("только начало")
    return ", ".join(куски)
