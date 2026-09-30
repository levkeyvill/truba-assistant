r"""Управление компьютером голосом: раскладка, музыка, звук Windows.

Три дела, которые иначе пришлось бы делать руками, а хозяин ими пользуется
каждую минуту: «переключи на английский», «пауза», «следующий трек», «звук
компа на тридцать», «выключи звук».

Честность здесь важнее удобства. Про то, играет ли сейчас музыка, Windows не
скажет: ответ «Нажала паузу» — правда, «Поставила музыку на паузу» — выдумка,
после которой хозяин будет искать, почему ничего не остановилось. Поэтому
каждая функция отвечает тем, что действительно сделала, и ничего не
приписывает.

Раскладку берём у оконной системы: `GetKeyboardLayoutList` перечисляет
установленные, нужная ищется по основному языку (`en` 0x09, `ru` 0x19), так
что en-GB тоже подходит. Просьбу о смене раскладки шлём в то окно, в котором
печатают, — то есть в `hwndFocus`, иначе игра или терминал её проигнорируют.
Через полторы сотни миллисекунд перечитываем раскладку и говорим правду:
переключилось или нет.

Громкость здесь — громкость Windows (`pycaw`, `IAudioEndpointVolume`), а не
собственный голос ассистента (`voice_volume`, команда `volume` в
`core/commands.py`). Это разные вещи, и путать их нельзя.

Ни одна функция не бросает исключения наружу: ошибка — это `{"ok": False,
"text": …}`, потому что зовут их и голосовая команда, и инструмент модели, и
ошибка вместо ответа уронила бы разговор.
"""

import ctypes
import json
import time
from ctypes import wintypes

from core import hotkeys
from core.speech_text import cardinal, plural

# --- Раскладка ---------------------------------------------------------------
#
# WM_INPUTLANGCHANGEREQUEST — просьба сменить раскладку, которую понимает и
# окно, и его поток ввода; HKL_NEXT (1) в lParam означает «следующая из
# установленных», а сам HKL — «вот эта».
WM_INPUTLANGCHANGEREQUEST = 0x0050
HKL_NEXT = 1
# Основной язык из LANGID в HKL. Английский и русский различаются именно им:
# у en-US (0x04090409) и en-GB (0x08090809) он один.
LANG_MASK = 0x3FF
LANG_EN = 0x09
LANG_RU = 0x19
LAYOUT_LANG = {"en": LANG_EN, "ru": LANG_RU}
# Как зовём язык по-человечески — в двух падежах: «И так английская» и
# «английской раскладки в системе нет».
LAYOUT_NAME = {"en": "английская", "ru": "русская"}
LAYOUT_ROD = {"en": "английской", "ru": "русской"}
# Сколько ждать, пока окно возьмёт сообщение и переключит раскладку.
SETTLE = 0.15

# --- Музыка ------------------------------------------------------------------
#
# Медиа-клавиши. Нажимаются готовым `core.hotkeys.press` — свой SendInput здесь
# был бы второй копией уже выверенного кода (см. ГРАБЛИ про размер структуры).
MEDIA_VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}
# Что сказать вслух. Проигрывание мы не проверяем, поэтому обещаем только
# нажатую клавишу.
MEDIA_TEXT = {
    "play_pause": "Нажала паузу.",
    "next": "Следующий трек.",
    "previous": "Предыдущий трек.",
    "stop": "Нажала стоп.",
}
MEDIA_JOURNAL = {
    "play_pause": "музыка: пауза",
    "next": "музыка: следующий трек",
    "previous": "музыка: предыдущий трек",
    "stop": "музыка: стоп",
}

# --- Громкость Windows -------------------------------------------------------
#
# Шаг в процентах берём крупнее щелчка ползунка: на десять процентов
# голосом разумно управлять одним словом.
VOLUME_STEP = 10
VOLUME_ACTIONS = ("set", "up", "down", "mute", "unmute", "get")
PERCENT = ("процент", "процента", "процентов")



class _GuiThreadInfo(ctypes.Structure):
    """Заполняется `GetGUIThreadInfo`: из неё берём окно ввода (hwndFocus)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _user32():
    return ctypes.windll.user32


# Каждая обёртка WinAPI — отдельной функцией, а не строкой в коде: тесты
# подменяют их подменой, и настоящие окна на машине хозяина не трогаются.


def GetForegroundWindow() -> int:
    """Окно, в котором печатают прямо сейчас."""
    return int(_user32().GetForegroundWindow())


def GetWindowThreadProcessId(hwnd: int) -> int:
    """Поток окна: раскладка принадлежит потоку, а не окну."""
    return int(_user32().GetWindowThreadProcessId(int(hwnd), None))


def GetKeyboardLayoutList() -> list:
    """Установленные раскладки, как их вернула система."""
    user32 = _user32()
    count = int(user32.GetKeyboardLayoutList(0, None, 0))
    if count <= 0:
        return []
    buffer = (ctypes.c_void_p * count)()
    got = int(user32.GetKeyboardLayoutList(count, ctypes.byref(buffer), 0))
    return [int(buffer[i] or 0) for i in range(max(0, min(count, got)))]


def GetKeyboardLayout(thread_id: int) -> int:
    """Раскладка потока прямо сейчас."""
    return int(_user32().GetKeyboardLayout(int(thread_id)))


def GetGUIThreadInfo(thread_id: int) -> int:
    """Окно ввода внутри потока (hwndFocus). 0 — его нет."""
    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    if not _user32().GetGUIThreadInfo(int(thread_id), ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


def PostMessageW(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
    """Отдать сообщение окну. False — окно его не приняло."""
    return bool(_user32().PostMessageW(int(hwnd), int(message), int(wparam),
                                      int(lparam)))


# --- Ответы ------------------------------------------------------------------


def _ok(text: str, journal: str = "", **fields) -> dict:
    """Ответ дела. `journal` — строка одной строкой в журнал пульта."""
    return dict(fields, ok=True, text=text, journal=journal or text)



# --- Раскладка ---------------------------------------------------------------


def _primary(hkl: int) -> int:
    """Основной язык раскладки: у en-US и en-GB он один и тот же."""
    return int(hkl) & LANG_MASK


def _lang_of(hkl: int) -> str:
    for name, code in LAYOUT_LANG.items():
        if _primary(hkl) == code:
            return name
    return ""


def _find_layout(code: int) -> int:
    """HKL раскладки с основным языком `code`. 0 — такой в системе нет."""
    for hkl in GetKeyboardLayoutList():
        if _primary(hkl) == code:
            return hkl
    return 0


def _next_layout(current: int) -> int:
    """Следующая за `current` из установленных, по кругу. 0 — других нет."""
    layouts = GetKeyboardLayoutList()
    others = [hkl for hkl in layouts if hkl != current]
    if not others:
        return 0
    if current in layouts:
        return layouts[(layouts.index(current) + 1) % len(layouts)]
    return others[0]


def current_layout() -> str:
    """Раскладка сейчас: `en`, `ru` или пусто, если она другая."""
    try:
        hwnd = GetForegroundWindow()
        if not hwnd:
            return ""
        return _lang_of(GetKeyboardLayout(GetWindowThreadProcessId(hwnd)))
    except Exception:
        return ""


def switch_layout(lang: str) -> dict:
    """Переключает раскладку на `en`, `ru` или `next` (следующая)."""
    try:
        lang = str(lang or "next").strip().lower()
        if lang not in LAYOUT_LANG and lang != "next":
            return _fail(f"не понимаю, на какой язык переключать: {lang}")
        hwnd = GetForegroundWindow()
        if not hwnd:
            return _fail("не вижу активного окна — раскладку переключать некуда")
        thread = GetWindowThreadProcessId(hwnd)
        current = GetKeyboardLayout(thread)
        before = _lang_of(current)
        if lang == "next":
            # Следующую берём из списка сами: HKL_NEXT в lParam у
            # WM_INPUTLANGCHANGEREQUEST не описан (он для
            # ActivateKeyboardLayout), и окна понимают его через раз.
            wanted = _next_layout(current)
            if not wanted:
                return _fail("в системе одна раскладка — переключать не на что")
        else:
            if before == lang:
                return _ok(f"И так {LAYOUT_NAME[lang]}.",
                           f"{_layout_journal(lang)} — не меняла",
                           lang=lang, changed=False)
            wanted = _find_layout(LAYOUT_LANG[lang])
            if not wanted:
                return _fail(f"{LAYOUT_ROD[lang]} раскладки в системе нет")
        # Сначала — самому окну верхнего уровня: 30.09 просьба в hwndFocus
        # окна пульта (WebView2) не сработала («окно её не взяло»), а окну
        # целиком — сработала сразу (проверено живьём). Не взяло — второй
        # попыткой окну ввода: некоторые программы слушают только его.
        focus = GetGUIThreadInfo(thread)
        targets = [hwnd] + ([focus] if focus and focus != hwnd else [])
        after = before
        posted = False
        for target in targets:
            if not PostMessageW(target, WM_INPUTLANGCHANGEREQUEST, 0, wanted):
                continue
            posted = True
            time.sleep(SETTLE)
            after = _lang_of(GetKeyboardLayout(thread))
            if after != before:
                break
        if not posted:
            return _fail("окно не приняло просьбу сменить раскладку")
        if after == before:
            # Проверить честно нечем: система молчит, а хозяин видит одну и ту
            # же раскладку. Лучше сказать об этом, чем обещать переключение.
            return _fail("раскладка не переключилась — окно её не взяло",
                         f"{_layout_journal(before)} — не переключилась")
        return _ok(_said_layout(after), _layout_journal(after), lang=after,
                   changed=True)
    except Exception as exc:
        return _fail(f"раскладку не переключить: {type(exc).__name__}: {exc}")


# --- Музыка ------------------------------------------------------------------


# --- Громкость компьютера ---------------------------------------------------


def _endpoint_volume():
    """IAudioEndpointVolume динамиков по умолчанию.

    COM в этом потоке не поднят: вызов идёт из голосового цикла и из мозга, то
    есть не из главного потока, а без `CoInitialize` первый же вызов pycaw
    падает (как в `core/ducking.py` рядом с `GetAllSessions`).
    """
    import comtypes

    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities

    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise OSError("в системе нет устройства вывода звука")
    return device.EndpointVolume


def _percent(endpoint) -> int:
    return int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))


def _as_level(level) -> int | None:
    """Уровень 0–100 из того, что прислали. None — это не процент."""
    try:
        value = int(level)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def pc_volume(action: str, level: int | None = None,
              step: int = VOLUME_STEP) -> dict:
    """Громкость Windows: `set`, `up`, `down`, `mute`, `unmute`, `get`."""
    action = str(action or "").strip().lower()
    if action not in VOLUME_ACTIONS:
        return _fail(f"не понимаю такую команду громкости: {action or 'пусто'}")
    try:
        endpoint = _endpoint_volume()
    except Exception as exc:
        return _fail(
            f"громкость компьютера недоступна: {type(exc).__name__}: {exc}")
    try:
        current = _percent(endpoint)
        if action == "get":
            muted = bool(endpoint.GetMute())
            return _ok(_said_volume(current, muted), _volume_journal(current),
                       level=current, muted=muted)
        if action == "mute":
            endpoint.SetMute(1, None)
            return _ok("Выключила звук.", "звук компьютера: выключен",
                       level=current, muted=True)
        if action == "unmute":
            endpoint.SetMute(0, None)
            return _ok("Включила звук.", _volume_journal(current),
                       level=current, muted=False)
        if action == "set":
            value = _as_level(level)
            if value is None:
                return _fail("не сказано, сколько процентов оставить")
        else:
            step = _as_level(step) or VOLUME_STEP
            if action == "up":
                if current >= 100:
                    return _ok("Громче некуда.", _volume_journal(current),
                               level=current, muted=bool(endpoint.GetMute()))
                value = min(100, current + step)
            else:
                if current <= 0:
                    return _ok("Тише некуда.", _volume_journal(current),
                               level=current, muted=bool(endpoint.GetMute()))
                value = max(0, current - step)
        endpoint.SetMasterVolumeLevelScalar(value / 100.0, None)
        # Смена громкости снимает mute: иначе хозяин крутит ползунок и не
        # слышит ничего, а потом говорит, что громкость не работает.
        endpoint.SetMute(0, None)
        return _ok(_said_volume(value), _volume_journal(value), level=value,
                   muted=False)
    except Exception as exc:
        return _fail(f"громкость не поменялась: {type(exc).__name__}: {exc}")


# --- Вызов модели ------------------------------------------------------------


def _json(fields: dict) -> str:
    return json.dumps(fields, ensure_ascii=False)


def run_tool(name: str, arguments: str) -> str:
    """Выполняет вызов инструмента модели. Строка — для сообщения tool.

    Ответ того же вида, что у `core/hands.py`: у удачи есть `text` (готовая
    фраза вслух) и `journal` (строка в журнал), у неудачи — `error` впереди, и
    тогда голосовой цикл не станет подтверждать невыполненное.
    """
    from core import hands

    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return _json({"error": "аргументы не разобрались как JSON",
                      "text": "Не поняла, что именно сделать."})
    try:
        if name == hands.LAYOUT_NAME:
            what = switch_layout(str(args.get("lang") or "next"))
        elif name == hands.MEDIA_NAME:
            what = media(str(args.get("action") or ""))
        elif name == hands.PCVOL_NAME:
            what = pc_volume(str(args.get("action") or "get"),
                             level=args.get("level"),
                             step=args.get("step") or VOLUME_STEP)
        else:
            return _json({"error": f"не знаю такого действия: {name}",
                          "text": "Не знаю такого действия."})
    except Exception as exc:
        return _json({"error": f"не получилось: {type(exc).__name__}: {exc}",
                      "text": "Не получилось."})
    if not what.get("ok"):
        return _json({"error": str(what.get("text") or "не вышло"),
                      "text": str(what.get("text") or "не вышло"),
                      "journal": str(what.get("journal") or "")})
    return _json(what)



def media(action: str) -> dict:
    """Жмёт медиа-клавишу: пауза, следующий, предыдущий, стоп."""
    key = str(action or "").strip().lower()
    if key not in MEDIA_VK:
        return _fail(f"не понимаю такую команду музыки: {action or 'пусто'}")
    try:
        hotkeys.press([MEDIA_VK[key]])
    except Exception as exc:
        return _fail(f"клавиша не нажалась: {type(exc).__name__}: {exc}")
    return _ok(MEDIA_TEXT[key], MEDIA_JOURNAL[key], action=key)



def _fail(text: str, journal: str = "") -> dict:
    """Ответ неудачи: слова те же, что вслух, только без обещания."""
    return {"ok": False, "text": text, "journal": journal or text}


def _layout_journal(name: str) -> str:
    return f"раскладка: {LAYOUT_NAME.get(name, 'другая')}"


def _said_layout(name: str) -> str:
    return f"Теперь {LAYOUT_NAME.get(name, 'другая')} раскладка."


def _volume_journal(level: int) -> str:
    return f"звук компьютера: {level} %"


def _said_volume(level: int, muted: bool = False) -> str:
    """Громкость словами: синтез не читает цифры, а «процентов» — с падежом."""
    if muted:
        return "Звук компьютера выключен."
    return (f"Звук компьютера — {cardinal(level, 'm')} "
            f"{plural(level, PERCENT)}.")
