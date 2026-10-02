"""Веб-пульт: runtime без Tk. Владеет Brain и VoiceLoop."""

from __future__ import annotations

import io
import itertools
import math
import re
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime

# Ниже этого процента без зарядки телефон считается «пора зарядить».
LOW_BATTERY = 20

# Настройки вида сетки программ на телефоне. Меняет их пульт, а телефон
# берёт из сообщения со списком — поэтому после их сохранения список
# уходит заново (см. `WebRuntime.save_settings`).
GRID_KEYS = frozenset({"phone_cols", "phone_rows", "phone_icon_style",
                       "phone_labels"})

# Модель распознавания речи и её сжатие. Сменилась — голос перезагружает
# модель на ходу (см. `WebRuntime.save_settings` и `VoiceLoop.reload_stt`).
STT_KEYS = frozenset({"stt_model", "stt_quantization"})

# Город погоды. Сменился — обновляем прогноз и раздаём его телефонам сразу,
# а не через полчаса по таймеру: иначе на экране остаётся старый город.
WEATHER_KEYS = frozenset({"weather_city", "weather_lat", "weather_lon"})


def action_items(cells: list) -> list:
    """Четыре ячейки настроек → то, что рисует телефон.

    Кнопки `none` в список не попадают: телефон просто сдвинет остальные
    вправо, и пустого места на экране не будет. `slot` — номер места, а не
    номер в списке, поэтому нажатие всегда означает ту же ячейку, что и до
    сдвига.

    Программы и пункты меню подписаны и нарисованы тем же, чем подменю на
    телефоне: заголовок из apps.json, значок — картинка программы или значок
    пункта. Чего тут нет: сочетаний клавиш и адресов. Их телефон знать не
    должен — он всё равно просит сервер.
    """
    from core import app_icons, launcher, settings

    items = []
    for slot, cell in enumerate(cells or []):
        kind = (cell or {}).get("kind")
        if kind == "none":
            continue
        item = {"slot": slot, "kind": kind}
        if kind == "builtin":
            one = cell.get("id", "")
            item["id"] = one
            item["title"] = settings.BUILTIN_ACTION_TITLES.get(one, one)
            item["icon"] = settings.BUILTIN_ACTION_ICONS.get(one, "app")
        elif kind == "hotkey":
            item["title"] = cell.get("title", "")
            item["icon"] = cell.get("icon", settings.PHONE_ACTION_FALLBACK_ICON)
        elif kind == "app":
            app_id = cell.get("app", "")
            record = launcher.find_app(app_id)
            # Программы может не быть: apps.json хозяин правит руками. Кнопка
            # остаётся, подписью станет её id, а нажатие сервер объяснит.
            # `id` программы сюда не кладём: телефон всё равно знает только
            # номер места, а лишнее имя — это лишнее знание на чужом экране.
            item["title"] = (record or {}).get("title") or app_id
            image = _app_picture(app_icons, record)
            if image:
                item["image"] = image
            else:
                item["icon"] = (record or {}).get("icon") or "app"
        elif kind == "menu":
            ready = launcher.find_menu(cell.get("app", ""), cell.get("item", ""))
            item["id"] = cell.get("item", "")
            # Программа пункта телефону нужна: по ней приходит `menu_state`
            # (подсветка после нажатия) и уходит `menu_flip` при удержании.
            # Без неё переключатель внизу застывал в том виде, каким пришёл.
            item["app"] = cell.get("app", "")
            item["title"] = (ready or {}).get("title") or cell.get("item", "")
            if ready and ready.get("image"):
                item["image"] = ready["image"]
            else:
                item["icon"] = (ready or {}).get("icon") or "keyboard"
            # Подсветка переключателя: тот же счёт нажатий, что у пункта
            # подменю, поэтому пункт и кнопка показывают одно и то же.
            if ready and ready.get("toggle"):
                item["toggle"] = True
                item["on"] = bool(ready.get("on"))
        items.append(item)
    return items


def _app_picture(app_icons, record) -> str:
    """Картинка программы строкой для `<img>`. Пусто — телефон нарисует сам."""
    if not record:
        return ""
    try:
        picture = app_icons.picture_for(record)
        if picture is None:
            return ""
        return app_icons.as_data_url(picture) or ""
    except Exception:
        # Значок — украшение: его не удалось собрать, кнопка всё равно нужна.
        return ""


def _validated_model(provider: str, value: str) -> str:
    """Принимаем любой ID модели сервиса, не ограничивая его нашим списком."""
    import config

    if provider not in config.PROVIDERS:
        raise ValueError("Сначала выбери сервис")
    if not isinstance(value, str) or not re.fullmatch(r"[^\s\x00-\x1f]{1,200}", value.strip()):
        raise ValueError("Укажи название модели без пробелов")
    return value.strip()


def _abs_path(value: str) -> bool:
    """Полный ли путь. «Заметки» или «папка/заметки» — нет: такой зависит
    от того, откуда запущен пульт, и заметки осели бы в папке проекта."""
    from pathlib import PurePath, PureWindowsPath

    text = (value or "").strip()
    if not text:
        return True
    # Windows-путь вида `D:\Заметки` или `\\сервер\папка`, а ещё и posix —
    # вдруг папку он указал слешем, как в привычном ему Линуксе.
    if PureWindowsPath(text).is_absolute() or PureWindowsPath(text).anchor:
        return True
    return PurePath(text).is_absolute()


def _сек(value) -> str:
    """Секунды из замера двумя знаками. Нет числа — прочерк, а не ноль.

    Ноль означал бы «этот этап занял ноль», а прочерк — «замера не было»:
    например, облако молчало и до первого слова не дошло вовсе.
    """
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _reminder_item(record: dict) -> dict:
    """Запись напоминания для Панели: ровно те поля, что рисует строка.

    `say` сюда не идёт намеренно: это фраза, которую она произнесёт, а
    Панели она не нужна и показывать её незачем. Время — строкой ISO с
    поясом, как в файле: браузер разбирает такой формат сам, а часовой
    пояс у пульта и у будильника один.
    """
    return {
        "id": str(record.get("id", "")),
        "due": str(record.get("due", "")),
        "text": str(record.get("text", "") or ""),
        "kind": str(record.get("kind", "") or ""),
        "minutes": int(record.get("minutes") or 0),
    }


def _reminder_line(record: dict, что: str) -> str:
    """Строка в журнал: «напоминание отменено: 17:00 — вытащить пиццу».

    Тот же вид, что у `core/hands.py::journal_line`, но со своим глаголом:
    там «напоминание: …» — это поставили, здесь «отменено» — сняли.
    """
    from core import reminders

    try:
        when = reminders.parse(record.get("due")).strftime("%H:%M")
    except ValueError:
        when = "?"
    if str(record.get("kind") or "") == reminders.KIND_TIMER:
        return f"таймер {что}: {when}"
    text = str(record.get("text") or "").strip()
    return f"напоминание {что}: {when} — {text}" if text else \
        f"напоминание {что}: {when}"


def _local_error(provider: str, base_url: str, exc: Exception) -> Exception:
    """Ошибка связи с локальным сервером — понятная хозяину, а не из сокета.

    Облаку возвращаем то, что было: там по тексту ошибки видно, что именно
    сервис недоволен (ключ, страна, лимит). Локальный сервер недоволен быть
    не может — он либо отвечает, либо выключен, и вот это хозяин и должен
    понять из одного сообщения.
    """
    from core import settings

    if settings.is_local(provider):
        return ValueError(
            f"локальный сервер не отвечает по адресу {base_url}: "
            f"запусти Ollama / LM Studio ({type(exc).__name__})"
        )
    return exc


def _update_что(итог: dict) -> str:
    """Человеческое объяснение результата проверки — для журнала."""
    if not isinstance(итог, dict):
        return "не получилось"
    состояние = str(итог.get("state", ""))
    if состояние == "newer":
        return f"вышла версия {итог.get('latest', '?')}"
    if состояние == "no_repo":
        return "репозиторий ещё не опубликован"
    if состояние == "latest":
        return f"текущая версия {итог.get('current', '?')} свежая"
    if состояние == "ahead":
        return f"локальная версия {итог.get('current', '?')} новее, чем на GitHub"
    return str(итог.get("error", "не получилось"))


def _пустое_скачивание() -> dict:
    """Состояние скачивания моделей, когда ничего не качается.

    Ключи те же, что ждёт пульт в `voices_status()["download"]`, иначе
    страница ловила бы `undefined` на пустом поле.
    """
    return {"active": False, "model": "", "title": "", "done": 0, "total": 0,
            "queue": [], "error": "", "finished": []}


def _пустое_библиотеки() -> dict:
    """Состояние установки библиотек, когда ничего не ставится.

    Ключи те же, что ждёт пульт в `voices_status()["libs"]`: `active` — идёт ли
    установка, `step`/`steps`/`title` — где она сейчас, `downloaded` — прирост
    кеша uv в байтах (столько хозяин скачал), `error` — честный текст отказа,
    `done` — поставилось.
    """
    from core import voices_install

    # `total` — сколько всего ляжет на диск: по нему полоса считает процент.
    return {"active": False, "step": 0, "steps": 2, "title": "",
            "downloaded": 0, "total": sum(voices_install.STEP_BYTES),
            "error": "", "done": False}


class WebRuntime:
    """Связывает PhoneServer, Brain и VoiceLoop для веб-пульта."""

    def __init__(self, server, brain=None, voice=None) -> None:
        from core import settings
        from core.brain import Brain
        from core.voice_loop import VoiceLoop

        self.server = server
        settings.apply_to_config()
        if brain is not None:
            self.brain = brain
        elif settings.key_for(settings.get_provider()):
            self.brain = Brain(provider=settings.get_provider())
        else:
            self.brain = None
        # VoiceLoop не подписываем на сервер напрямую: все события идут
        # через handle_event, иначе кнопки сработают дважды.
        self.voice = voice if voice is not None else VoiceLoop(
            self.brain,
            self.handle_event,
            server=server,
            handle_phone_events=False,
        )

        self._lock = threading.Lock()
        import config

        # Обновление с GitHub: последняя проверка, шаги установки, итог.
        # Всё это состояние живёт здесь, чтобы пульт спрашивал `/status`,
        # а установка шла отдельным потоком и не блокировала страницу.
        self._update_lock = threading.Lock()
        self._update_check: dict | None = None
        self._update_running = False
        self._update_steps: deque = deque(maxlen=30)
        self._update_result: dict | None = None
        # Качественные голоса ставятся кнопкой: `running`, шаги и итог — как
        # у обновления с GitHub (тот же замок, та же фоновая нить).
        self._voices_running = False
        self._voices_steps: deque = deque(maxlen=30)
        self._voices_result: dict | None = None
        # Скачивание моделей качественных голосов. Одна загрузка за раз,
        # по очереди; состояние живёт здесь же, чтобы пульт спрашивал его в
        # `/api/voices/status` и рисовал проценты, а не ждал событий (02.10:
        # панель стояла на «0,0 ГБ», пока качалось 257 МБ).
        self._download_lock = threading.Lock()
        self._download_state: dict = _пустое_скачивание()
        self._download_stop: threading.Event | None = None
        # Установка библиотек качественных голосов — та же схема: одна за раз,
        # в фоне, с отменой и шагами (`voices_status()["libs"]`). Отдельно от
        # скачивания моделей: там хозяин тянет веса, здесь uv ставит пакеты с
        # torch (02.10).
        self._libs_lock = threading.Lock()
        self._libs_state: dict = _пустое_библиотеки()
        self._libs_stop: threading.Event | None = None
        # Перенос настроек: принесённый файл держим в памяти до применения.
        # На диск его раньше времени класть нельзя (а на «Документы» он и не
        # просился), и второй файл не нужен — хозяин переносит по одному.
        self._transfer_lock = threading.Lock()
        self._transfer_blob: bytes | None = None
        self._transfer_token = ""
        # Железо спрашивают часто, а `nvidia-smi` занимает до пяти секунд:
        # ответ держим минуту. Формат — (время, {"hw", "recommend"}).
        self._hw_cache = None
        # Как закрыть окно пульта. Ставит `ui/window.py` — он знает про трей.
        self._pult_close = None

        self._log_path = config.DATA_DIR / "session.log"
        self._rotate_log()
        # Когда поднялся пульт: от этого момента считается период «Сеанс»
        # в блоке «Расход». Расход копится с этого же запуска.
        self.started_at = datetime.now()
        self._prepare_usage()
        self._events: deque[dict] = deque(maxlen=300)
        self._overview = {
            "heard": 0, "ignored": 0, "interrupted": 0, "spoken": 0,
            "last_first_sound": None, "last_stt_time": None,
            "last_ignored_reason": None, "last_activity": None,
        }
        self._jobs: dict = {}
        self._ids = itertools.count(1)
        self._provider_test_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._web_test_lock = threading.Lock()
        # Проба звука мастера: снимает голос, пишет и возвращает. Замок
        # создаём здесь, а не на лету — иначе два первых параллельных запроса
        # завели бы по своему замку и прошли бы одновременно.
        self._audio_probe_lock = threading.Lock()
        # Захват микрофона на шаге 4: короткий замер полоски и проба «Записать 3
        # секунды» — **один** замок на время открытия устройства. Иначе замер
        # на 0,25 с и проба на 3 с открывают один микрофон разом, и проба
        # получает «устройство занято». Проба берёт его с ожиданием до секунды
        # (замер короткий, он скоро кончится), замер — без ожидания: полоска
        # может подождать следующего своего прохода.
        self._level_capture_lock = threading.Lock()
        # Прослушивание записи микрофона: две кнопки подряд не должны играть
        # одну запись в колонки одновременно.
        self._audio_play_lock = threading.Lock()
        # Последняя запись микрофона для «Прослушать»: (массив float32, частота).
        # Живёт в памяти до следующей записи — проверять звук приходится, когда
        # хозяин подумает, а не ровно в момент нажатия.
        self._проба_звука = None
        # Предварительный уровень шага 4 мастера: кеш последнего замера и
        # ограничение частоты. Захват он **не** защищает — его держит
        # `_level_capture_lock`, и во время `sounddevice` этот замок свободен.
        self._level_preview_lock = threading.Lock()
        self._level_preview_at = 0.0
        self._level_preview_key = None
        self._level_preview_level = None
        # Активная запись образца хозяина: {"id", "recorder"} или None.
        self._enroll = None
        # Звук меняли при работающем голосе — поднять заново при включении.
        self._audio_stale = False
        # Сколько писать: как в ui/check_tab.py — текст читается полминуты
        # плюс запас на раскачку.
        self._enroll_seconds = 40.0
        # Заряд телефона: телефон шлёт его раз в минуту, в ленту это не
        # годится, поэтому держим только последнее значение и время.
        self._battery_level: int | None = None
        self._battery_charging = False
        self._battery_low = False
        self._battery_at = 0.0
        # Минимальный способ отдать себя серверу: поле runtime.
        try:
            server.runtime = self
        except Exception:
            pass
        self._remember("pult_started", None)
        self._wire_brain()
        from core import replay

        self.replay_guard = replay.Guard(self._on_replay)
        self._bg(self.replay_guard.check)
        self.replay_guard.start()
        # Напоминания и таймеры — будильник поднимается вместе с пультом, а
        # не с голосом: напоминание должно позвать и при выключенном голосе
        # (телефон с тостом и журнал остаются в любом случае). Гасится в
        # `close`. Сработавшее, пока пульт был выключен, будильник отдаст
        # сразу же при старте — с пометкой опоздания.
        from core import reminders

        self.alarm = reminders.Alarm(self._on_reminder)
        self.alarm.start()
        # Закладки Firefox могли добавиться, пока пульт был закрыт. Здесь
        # перечитываем насильно: в фоне, чтобы не тянуть старт окна.
        self._bg(self._refresh_bookmarks)
        self._bg(self._check_updates_soon)

    def _on_reminder(self, record: dict, late: float) -> None:
        """Сработавшее напоминание: телефон, журнал и голос.

        Порядок здесь один на все три выхода, и порядок важен: строка в
        журнал идёт **первой**, потому что журнал — то, что хозяин увидит
        и в ленте, и в файле, даже если телефон выключен.

        `late` — на сколько секунд опоздали. Больше `reminders.MISSED_AFTER`
        означает «пока меня не было» (пульт был выключен), и тогда фраза
        начинается с извинения: иначе «Напоминаю: пицца» через три часа
        после обеда звучало бы как издевательство.
        """
        from core import reminders

        late = float(late or 0.0)
        try:
            what = str(record.get("text") or "").strip()
            timer = str(record.get("kind") or "") == reminders.KIND_TIMER
        except Exception:
            return
        if timer:
            line = "таймер вышел"
        else:
            line = f"напоминание: {what}" if what else "напоминание"
        self._remember("reminder_fired", line)
        missed = late >= reminders.MISSED_AFTER
        if missed:
            # Пропущенное, пока пульт был выключен, срабатывает через секунду
            # после старта — телефон ещё не подключился, голос не поднялся, и
            # тост со звуком ушли бы в пустоту. Звучит так: «Пока я была
            # выключена, вышел таймер» / «…пропустила напоминание: пицца».
            words = ("Пока я была выключена, вышел таймер." if timer else
                     f"Пока я была выключена, пропустила напоминание: {what}."
                     if what else "Пока я была выключена, пропустила напоминание.")
        else:
            words = reminders.phrase(record)
        threading.Thread(target=self._say_reminder, args=(words, missed),
                         daemon=True, name="reminder-say").start()

    # Сколько пропущенное напоминание ждёт голос после старта пульта.
    REMINDER_WAIT_VOICE = 90.0

    def _say_reminder(self, words: str, missed: bool) -> None:
        """Телефон (звук и тост) и голос — своим потоком, не держа будильник."""
        if missed:
            deadline = time.monotonic() + self.REMINDER_WAIT_VOICE
            while time.monotonic() < deadline:
                voice = self.voice
                if voice is not None and getattr(voice, "running", False):
                    break
                time.sleep(1.0)
        try:
            self.server.send_sound("moment")
        except Exception:
            pass
        # Тост — коротко и по делу: телефон стоит сбоку, хозяин смотрит в
        # монитор, и ему нужно понять, что это она позвала.
        try:
            self.server.send_toast(words)
        except Exception:
            pass
        # Голос — только если он включён. `announce` сам дождётся свободного
        # хода и не перебьёт хозяина.
        voice = self.voice
        if voice is None or not getattr(voice, "running", False):
            return
        voice.announce(words)

    def _check_updates_soon(self) -> None:
        """Проверить обновления через 20 секунд после старта пульта.

        Не сразу: сначала должно открыться окно. И молча — про то, что новой
        версии нет, в журнале писать нечего.
        """
        import config
        from core import updater

        if not getattr(config, "UPDATE_CHECK", True):
            return
        updater.check_at_start(
            on_result=lambda итог: self._remember_check(итог, quiet=True))

    def _refresh_bookmarks(self) -> None:
        """Перечитать панель закладок Firefox при старте пульта."""
        from core import bookmarks

        try:
            bookmarks.read(
                force=True,
                on_error=lambda text: self._remember("bookmarks_failed", text),
            )
        except Exception:
            pass

    def _rotate_log(self) -> None:
        """Журнал разросся — отложить его в session.1.log и начать новый.

        Держим ровно два файла: прежний session.1.log удаляется, сколько бы
        ему ни было месяцев. Сделано один раз при старте пульта, поэтому
        вкладка «Логи» и скачивание журнала дальше работают с новым файлом
        и в этот момент уже ничего не теряют из свежего.
        """
        import config

        try:
            limit = float(config.LOG_LIMIT_MB) * 1024 * 1024
        except (AttributeError, TypeError, ValueError):
            return
        try:
            if not self._log_path.is_file():
                return
            if self._log_path.stat().st_size <= limit:
                return
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            previous = self._log_path.with_name("session.1.log")
            previous.unlink(missing_ok=True)
            self._log_path.replace(previous)
            self._log_path.touch()
        except OSError:
            # Не разбираемся с журналом — молча продолжаем писать в него.
            return

    def _prepare_usage(self) -> None:
        """Расход: выкинуть старое и обновить цены в фоне.

        Обрезка файла — на старте пульта: месяц с лишним расхода Панели не
        покажет, а файл растёт. Цены OpenRouter и курс доллара — в
        отдельном потоке: сети в потоке окна быть не должно.
        """
        from core import usage

        try:
            убрано = usage.prune()
            if убрано:
                self.log_message(f"расход: забыл {убрано} записей старше месяца")
        except Exception:
            pass
        usage.refresh_prices_soon()

    def usage_summary(self) -> dict:
        """Сводка расхода для Панели: три периода, курс и откуда цены."""
        from core import usage

        return usage.summary(getattr(self, "started_at", datetime.now()))

    def _on_replay(self, kind: str, payload: dict) -> None:
        """Сторож повтора: в журнал и, если важно, на телефон."""
        self._remember(kind, payload)
        try:
            if kind == "replay_restored":
                self.server.send_toast("Мгновенный повтор снова включён", ok=True)
            elif kind == "replay_failed":
                self.server.send_toast("Мгновенный повтор не включился", ok=False)
        except Exception:
            pass

    def _wire_brain(self) -> None:
        """Поиски и действия — в журнал и ленту «Голос», как остальные события.

        Здесь же мозг получает действия на компе (core/hands.py): снимок, клип и
        взгляд на экран. Чат пульта ходит в тот же мозг, поэтому «а сделай
        скриншот» в чате работает так же, как голосом. Словарь собирается
        заново при каждом пересоздании мозга — поэтому он и заполняется здесь,
        в единственном месте, где мозг появляется на свет.
        """
        from core import hands
        from core import settings

        if self.brain is not None:
            self.brain.on_event = self._remember
            actions = hands.actions_for(self._remember, lambda: self.server)
            # Диктовку голос умеет: без этого действия у модели нет способа
            # начать запись, и на «сделаешь заметку?» она отвечала словами
            # «диктуй», а записывать было некому.
            actions["dictation"] = lambda hint="": self.voice.begin_dictation(hint)
            # Чтение вслух — тем же путём: без голоса читать некому, и модель
            # должна получить честный отказ, а не обещание.
            actions["read_aloud"] = (
                lambda text=None, name="", resume=False: self.voice.read_aloud(
                    text, name, resume))
            self.brain.actions = actions
            # Локальный мозг появляется в журнале с честной пометкой: с Трубой
            # он не проверялся, и хозяин должен знать об этом сразу.
            try:
                заметка = settings.local_notice(
                    getattr(self.brain, "provider", ""),
                    getattr(self.brain, "_model", ""))
            except Exception:
                заметка = ""
            if заметка:
                self._remember("brain_local", заметка)

    @staticmethod
    def _log_messages(kind: str, payload) -> list[str]:
        """Короткая хроника без шумных кусков речи и сырых данных телефона."""
        data = payload if isinstance(payload, dict) else {}
        simple = {
            "pult_started": "веб-пульт запущен",
            "ready": "модели подняты, слушаю микрофон",
            "ws_attempt": "телефон пытается открыть соединение",
            "page_opened": "телефон открыл страницу",
            "connected": "телефон подключился",
            "audio_ready": "звук на телефоне включён",
            "audio_off": "звук на телефоне выключен — жду касания",
            "disconnected": "телефон отключился",
            "no_phone": "телефон не на связи — говорю в колонки компьютера",
            "phone_wait": "телефон пропал — подожду его",
            "listen_now": "кнопка телефона: слушаю без имени",
            "search": "кнопка телефона: жду поисковый запрос",
            "note_start": "кнопка телефона: диктовка заметки",
            "search_fail": "поиск с телефона не вышел",
            "stopped": "голосовой режим выключен",
            "screenshot": "телефон запросил снимок экрана",
            "moment": "телефон запросил сохранение момента",
            "notes_list": "телефон открыл заметки",
        }
        if kind in simple:
            return [simple[kind]]
        if kind == "notes_topic":
            return [f"телефон читает «{str(data.get('topic', ''))[:60]}»"]
        if kind == "ws_refused":
            if payload == "нет ключа":
                return ["телефон без ключа не пустила — наведи его камеру на "
                        "QR-код в «Настройки → Телефон»"]
            return ["чужую страницу к телефонному каналу не пустила"]
        if kind == "loading":
            return [f"загружаю: {payload}"]
        if kind == "stt_loading":
            return [f"распознавание: загружаю {payload}"]
        if kind == "stt_ready":
            return [f"распознавание: готово — {payload}"]
        if kind == "stt_failed":
            return [f"распознавание: не загрузилась — {data.get('model', '?')}: "
                    f"{str(data.get('error', ''))[:120]} (оставила прежнюю)"]
        if kind == "phone_ready":
            return [f"телефон ждёт по адресу {payload}"]
        if kind == "heard":
            # С 25 сентября журнал — единое место и для разговора (лента
            # «Голоса» переехала сюда), поэтому фраза пишется целиком.
            return [f"услышала: {str(data.get('text', '')).strip()[:300]} "
                    f"({float(data.get('seconds', 0)):.1f} с речи, "
                    f"распознано за {float(data.get('stt_time', 0)):.2f} с)"]
        if kind == "autostart":
            return [f"автозапуск с Windows: {'включён' if payload else 'выключен'}"]
        if kind == "sentence":
            return [f"ответила: {str(payload or '').strip()[:400]}"]
        if kind == "ended_by_model":
            return [f"закрыла разговор по смыслу: {str(payload or '').strip()[:200]}"]
        if kind == "first":
            # Заход первой. Слово про экран — отдельно, иначе строка в
            # журнале читается так, будто она снимала кадр.
            minutes = int(data.get("minutes", 0))
            return [f"заговорила сама (молчал {minutes} мин)"
                    + (", глянув на экран" if data.get("look") else "")]
        if kind == "brain_local":
            return [f"мозг: {payload}"]
        if kind == "update_check":
            return [f"проверка обновлений: {str(data.get('what', ''))[:120]}"]
        if kind == "update_step":
            return [f"обновление: {str(data.get('what', ''))[:120]}"]
        if kind == "update_available":
            return [f"вышла новая версия {payload} — Настройки → О программе"]
        if kind == "updated":
            return [f"обновлено до {str(data.get('to', '?') or '?')}"]
        if kind == "update_failed":
            return [f"обновление не удалось: {str(data.get('error', ''))[:160]}"
                    + (" (откатила)" if data.get("rolled_back") else "")]
        if kind == "voices_step":
            return [f"качественные голоса: {str(payload or '')[:120]}"]
        if kind == "voices_done":
            return ["качественные голоса поставлены — перезапусти пульт"]
        if kind == "voices_failed":
            return [f"качественные голоса не поставились: "
                    f"{str(data.get('error', ''))[:160]}"]
        if kind == "voices_download":
            return [f"качественные голоса: {str(payload or '')[:120]}"]
        if kind == "voices_download_failed":
            return [f"модель не скачалась: {str(data.get('error', ''))[:160]}"]
        if kind == "provider_fallback":
            why = ("не пускает из этой страны" if data.get("why") == "region"
                   else "не отвечает")
            return [f"{data.get('from', '?')} {why} (VPN отвалился?) — "
                    f"отвечаю через {data.get('to', '?')}, свой попробую через 10 минут"]
        if kind == "provider_back":
            return [f"снова пробую {data.get('to', '?')} вместо {data.get('from', '?')}"]
        if kind == "hedge":
            # Срабатывание страховки. Хозяин должен видеть, как часто это
            # бывает: если каждую реплику — облако заминается, и дешевле
            # поменять провайдера, чем платить за два запроса.
            after = float(data.get("after", 0))
            if data.get("winner") == "spare":
                return [f"страховка сработала: {data.get('from', '?')} молчал "
                        f"{after:.1f} с, отвечает {data.get('to', '?')} — "
                        f"запрос {data.get('from', '?')} тоже оплачен"]
            return [f"страховка: {data.get('from', '?')} всё-таки ответил, "
                    f"{data.get('to', '?')} отменён — он уже оплачен"]
        if kind == "web_start":
            return [f"решила искать в интернете через {float(data.get('after', 0)):.1f} с"]
        if kind == "continue":
            # Ответ упёрся в потолок токенов. Хозяин должен видеть, что молчание
            # было не пустотой, а обрезанным ответом, который мы договариваем.
            return [f"ответ упёрся в потолок {int(data.get('limit', 0))} токенов — "
                    f"продолжаю ({int(data.get('step', 0))} из "
                    f"{int(data.get('of', 0))})"]
        if kind == "action_check":
            # Хозяин должен видеть, что программа открывалась не сама: судил
            # отдельный короткий вопрос, и сколько он занял.
            return [f"проверка просьбы: {str(data.get('action', '?')).strip()} — "
                    f"{str(data.get('answer', 'ошибка')).strip()} "
                    f"({float(data.get('took', 0)):.1f} с)"]
        if kind == "tokens":
            # Хозяин увидел в кабинете OpenAI ~814 тыс. входных токенов за
            # сутки и спросил почему. В журнале теперь видно, сколько стоил
            # каждый ответ, и сколько из этого провайдер взял из кеша.
            text = (f"токены: {int(data.get('prompt', 0))} на входе "
                    f"({int(data.get('cached', 0))} из кеша), "
                    f"{int(data.get('completion', 0))} на выходе")
            if data.get("note") == "память":
                return [f"выжимка памяти — {text}"]
            return [text]
        if kind == "request":
            return [f"голосовой запрос: {payload}"]
        if kind == "request_failed":
            return [f"голосовой запрос не удался: {payload}"]
        if kind == "menu_flipped":
            return [f"подсветка переключателя: {data.get('line', 'поправлена')}"]
        if kind in ("apps_ready", "apps_failed", "launched", "launch_failed",
                    "closed", "close_failed", "failed", "error", "shot_ready",
                    "shot_failed", "moment_sent", "moment_failed",
                    "bookmarks_failed", "youtube"):
            labels = {
                "apps_ready": "кнопок запуска найдено", "apps_failed": "список программ не собран",
                "launched": "программа запущена", "launch_failed": "программа не запустилась",
                "closed": "программа закрыта", "close_failed": "программа не закрылась",
                "failed": "сервер телефона упал", "error": "ошибка голоса",
                "shot_ready": "снимок экрана", "shot_failed": "снимок не вышел",
                "moment_sent": "момент сохранён", "moment_failed": "момент не сохранён",
                "bookmarks_failed": "закладки Firefox", "youtube": "YouTube",
            }
            return [f"{labels[kind]}: {payload}"]
        if kind == "ignored":
            return [f"пропустил [{data.get('why', '?')}]: {str(data.get('text', ''))[:70]}"]
        if kind == "cloud_down":
            return [f"облако не отвечает: {str(payload or '')[:120]} — "
                    "буду молчать, пока связь не вернётся"]
        if kind == "cloud_silent":
            return [f"облако недоступно — промолчала: {str(payload or '')[:70]}"]
        if kind == "cloud_back":
            return ["облако снова отвечает"]
        if kind == "voice_score":
            return [f"голос похож на {float(data.get('score', 0)):.2f} "
                    f"({float(data.get('seconds', 0)):.1f} с речи)"]
        if kind == "remembered":
            if isinstance(payload, list):
                return [f"запомнила: {fact}" for fact in payload]
            lines = [f"запомнила: {fact}" for fact in data.get("added", [])]
            lines += [f"поправила память: {pair[0]} → {pair[1]}"
                      for pair in data.get("updated", []) if len(pair) == 2]
            lines += [f"забыла: {fact}" for fact in data.get("removed", [])]
            return lines
        if kind == "memory_checked":
            return ["память: разобрала разговор, нового о тебе нет"]
        if kind == "note":
            return [f"заметка: {payload}"]
        if kind == "analysis_saved":
            # Разбор самой Трубы в заметки — по её собственному вопросу. В
            # журнал идёт тема, а не текст разбора: разбор это слова о чужом
            # документе, и читать его тут незачем.
            return [f"разбор сохранён в заметки: {str(payload or '').strip()[:80]}"]
        if kind == "reminder_fired":
            # Строка уже с названием: «напоминание: вытащить пиццу» или
            # «таймер вышел». Журнал — то, что хозяин увидит и без телефона.
            return [str(payload or "напоминание")]
        if kind == "reminder_cancel":
            # Отмена из Панели: строка уже готовая, как у сработавшего.
            return [str(payload or "напоминание отменено")]
        if kind in ("folder", "folder_failed"):
            # Стандартная папка: строка уже готова в core/folders.py —
            # «папка: Загрузки».
            return [f"{'папка' if kind == 'folder' else 'папка не открылась'}: "
                    f"{payload}"]
        if kind in ("file_found", "file_opened", "file_missing"):
            # Поиск файла по названию: строки готовые, как у папок и
            # документов. В журнал идёт имя файла и папка, но не текст
            # самого файла: его тут быть не должно.
            надпись = {"file_found": "файл найден",
                       "file_opened": "файл открыт",
                       "file_missing": "файл не найден"}[kind]
            return [f"{надпись}: {payload}"]
        if kind in ("document", "document_failed"):
            # Документ: строка уже готова в core/documents.py — «отчёт.pdf,
            # 12 стр., 8400 знаков». Текст документа в журнал не уходит
            # никогда: журнал хозяин читает сам, без телефона.
            return [f"{'документ' if kind == 'document' else 'документ не прочитан'}: "
                    f"{payload}"]
        if kind in ("document_aloud", "document_stopped"):
            # Чтение вслух: строки уже готовые — «документ вслух: отчёт.pdf» и
            # «чтение остановлено на 14 из 80». Текст документа и здесь не
            # уходит: в журнале его быть не должно.
            return [str(payload or "")] if payload else []
        if kind in ("clipboard", "clipboard_analyze", "clipboard_aloud",
                      "clipboard_failed"):
            # Буфер обмена: строки готовые — «буфер обмена: 840 знаков» и
            # «продолжаю вслух: буфер обмена». Сам скопированный текст сюда
            # не уходит никогда: в буфере у хозяина всё, что он нашёл в сети.
            # `clipboard_analyze` — тот же текст на разбор, не на перевод, но
            # строка от него такая же: хозяин видит, что буфер взяли, и не
            # видит, что в нём было.
            if kind == "clipboard_failed":
                return [f"буфер не прочитан: {payload}"]
            return [str(payload or "")] if payload else []
        if kind in ("clipboard_put", "clipboard_put_failed"):
            # Запись в буфер по просьбе: «буфер обмена: положено 840 знаков».
            # Сам текст — ни здесь, ни в журнале.
            if kind == "clipboard_put_failed":
                return [f"в буфер не положено: {payload}"]
            return [str(payload or "")] if payload else []
        if kind == "pc":
            # Раскладка, музыка, звук компьютера: строка уже готова в
            # core/pc_control.py — «звук компьютера: 30 %».
            return [str(payload)] if payload else []
        if str(kind or "").startswith("power"):
            # Питание компьютера. Строки готовые — «питание: жду подтверждения
            # — выключение», «питание: выключение принято системой, отсчёт
            # 60 с», «питание: выключение не вышло — …». Отдельная ветка и
            # отдельный префикс, а не `pc`: хозяин читает журнал сам, и по
            # «звуку компьютера» он бы не понял, что речь о выключении.
            return [str(payload)] if payload else []
        if kind == "command":
            return [f"команда голосом [{data.get('action', '?')}]: {data.get('text', '')}"]
        if kind == "replay_waiting":
            return [f"мгновенный повтор выключила NVIDIA — мешает {data.get('app') or 'защищённый экран'}; "
                    "включу, когда освободит экран"]
        if kind == "replay_restored":
            return [f"мгновенный повтор включён обратно (выключал {data.get('app') or 'защищённый экран'})"]
        if kind == "replay_failed":
            retry = data.get("retry")
            return [f"мгновенный повтор не включился: {data.get('error', '?')}"
                    + (f", попробую через {retry} с" if retry else "")]
        if kind == "web_search":
            return [f"поиск в интернете [{data.get('backend', '?')}, "
                    f"{float(data.get('took', 0)):.1f} с, найдено {data.get('found', 0)}]: "
                    f"{str(data.get('query', ''))[:120]}"]
        if kind == "web_queries":
            # Что модель сделала из его фразы для поисковика — иначе в журнале
            # видны только запросы и непонятно, откуда они взялись.
            запросы = "; ".join(str(q) for q in data.get("queries") or [])[:200]
            if data.get("error"):
                return [f"запросы для поиска не составила ({float(data.get('took', 0)):.1f} с, "
                        f"{str(data.get('error'))[:80]}) — ищу как сказано: {запросы}"]
            return [f"запросы для поиска ({float(data.get('took', 0)):.1f} с): {запросы}"]
        if kind == "web_page":
            return [f"прочитала страницу ({data.get('chars', 0)} знаков, "
                    f"{float(data.get('took', 0)):.1f} с): {str(data.get('url', ''))[:160]}"]
        if kind == "web_failed":
            return [f"интернет не помог [{data.get('tool', '?')}]: {str(data.get('error', ''))[:200]}"]
        if kind == "interrupted":
            return [f"перебили на полуслове (громкость {float(data.get('peak', 0)):.3f}): "
                    f"{str(data.get('text', ''))[:60]}"]
        if kind == "barge":
            return [f"перебил сразу: его голос {float(data.get('level', 0)):.3f} "
                    f"при фоне эха {float(data.get('floor', 0)):.3f}"]
        if kind == "barge_false":
            return [f"перебивание было ложным (голос {float(data.get('level', 0)):.3f} "
                    f"при фоне {float(data.get('floor', 0)):.3f}): "
                    f"{str(data.get('text', ''))[:60]}"]
        if kind == "echo_level":
            return [f"эхо во время речи: фон {float(data.get('floor', 0)):.3f}, "
                    f"пик {float(data.get('peak', 0)):.3f}"]
        if kind == "launch":
            return [f"телефон попросил открыть программу: {data.get('id', '')}"]
        if kind in ("menu_done", "menu_failed", "action_done", "action_failed"):
            return [str(data.get("line", ""))]
        if kind == "mode":
            return [f"режим слуха с телефона: {data.get('value', '?')}"]
        if kind == "hush":
            return ["касание круга на телефоне: замолчать и закрыть разговор"]
        if kind == "volume":
            # payload — само число, не словарь: уровень приходит и от
            # телефона (шаг), и от голосовой команды (готовое значение).
            try:
                return [f"громкость голоса: {int(payload)}"]
            except (TypeError, ValueError):
                return []
        if kind == "conversation":
            return ["разговор начат — слушаю без имени" if payload else
                    "разговор закрыт — жду обращения по имени"]
        if kind == "timing":
            # 26 сентября хозяин записал, сколько он ждёт звук: медиана 3.3 с
            # на обычном ответе, 4.2 с с поиском, 5–9 с на действии. Эта
            # строка и отвечает, куда из этих секунд ушло время.
            части = [
                f"слух {_сек(data.get('stt'))}",
                f"приглушение {_сек(data.get('duck'))}",
                f"модель до 1-го слова {_сек(data.get('word'))}",
                f"до 1-го предложения {_сек(data.get('sentence'))}",
                f"синтез 1-го куска {_сек(data.get('synth'))}",
                f"в телефон {_сек(data.get('say'))}",
                f"инструменты {int(data.get('rounds', 0))} круг",
            ]
            return [f"задержка {_сек(data.get('total'))} с: " + " · ".join(части)]
        if kind == "spoken":
            first = data.get("first_sound")
            delay = f"{float(first):.2f}" if first else "?"
            return [f"ответил голосом: звук через {delay} с, "
                    f"всего {float(data.get('total', 0)):.2f} с, "
                    f"предложений {data.get('sentences', 0)}"]
        return []

    def log_message(self, message: str) -> None:
        """Пишет строку в общий журнал; сбой диска не ломает пульт."""
        clean = str(message).replace("\r", " ").replace("\n", " ")[:1000]
        line = f"[{time.strftime('%H:%M:%S')}] {clean}\n"
        try:
            with self._lock:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as file:
                    file.write(line)
        except OSError:
            pass

    def _remember(self, kind: str, payload) -> dict:
        item = {"id": next(self._ids), "time": time.strftime("%H:%M:%S"),
                "kind": kind, "payload": payload}
        if kind == "tokens" and isinstance(payload, dict):
            # Мозг один и для чата, и для голоса, поэтому и пишем здесь:
            # событие проходит через _remember в обоих случаях.
            try:
                from core import usage

                usage.record(payload)
            except Exception:
                pass
        with self._lock:
            self._events.append(item)
            if kind in ("heard", "ignored", "interrupted", "spoken"):
                self._overview[kind] += 1
                detail = {
                    "heard": "Услышала фразу", "ignored": "Пропустила фразу",
                    "interrupted": "Её перебили", "spoken": "Ответила голосом",
                }[kind]
                self._overview["last_activity"] = {"time": item["time"], "text": detail}
            if kind == "heard" and isinstance(payload, dict):
                self._overview["last_stt_time"] = payload.get("stt_time")
            elif kind == "ignored" and isinstance(payload, dict):
                self._overview["last_ignored_reason"] = str(payload.get("why", ""))[:80]
            elif kind == "spoken" and isinstance(payload, dict):
                self._overview["last_first_sound"] = payload.get("first_sound")
        try:
            for message in self._log_messages(kind, payload):
                self.log_message(message)
        except (TypeError, ValueError):
            pass
        return item

    def events(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in list(self._events) if e["id"] > after]

    def overview(self) -> dict:
        """Короткие счётчики за этот запуск, без текста разговоров."""
        with self._lock:
            result = self._overview.copy()
            if result["last_activity"]:
                result["last_activity"] = result["last_activity"].copy()
        try:
            import json
            import config
            updated = json.loads((config.DATA_DIR / "history.json").read_text(encoding="utf-8")).get("updated")
            result["last_saved_at"] = updated if isinstance(updated, str) else None
        except (OSError, ValueError, TypeError):
            result["last_saved_at"] = None
        return result

    def voice_state(self) -> dict:
        import config

        running = bool(getattr(self.voice, "running", False))
        try:
            in_conv = bool(self.voice.in_conversation)
        except Exception:
            in_conv = False
        ready = running and bool(getattr(self.voice, "ready", False))
        # Уровень громкости едет вместе с состоянием: ползунок в «Голосе» и
        # регулятор на телефоне показывают одно и то же, даже если уровень
        # поменяли голосом или с телефона.
        # Что грузится (с процентами скачивания Higgs) и «выключается»: пока
        # загрузка не дошла до точки, где её можно оборвать, голос ещё
        # `running`, и без этого кнопка «Выключить» выглядела бы мёртвой.
        import threading

        текст = getattr(self.voice, "loading_text", "")
        loading = текст if running and not ready and isinstance(текст, str) else ""
        stop = getattr(self.voice, "_stop", None)
        stopping = running and isinstance(stop, threading.Event) and stop.is_set()
        return {"running": running, "ready": ready, "mode": config.LISTEN_MODE,
                "in_conversation": in_conv,
                "volume": int(getattr(config, "VOICE_VOLUME", 10)),
                "loading_text": loading, "stopping": stopping}

    def voice_autostart(self) -> None:
        """Голос — сразу при запуске пульта, если так настроено.

        «Выкл» в режиме слуха при этом превращается в «по имени», как у
        кнопки на Панели: включённый голос, который ничего не слушает, —
        ровно то «запустил, а она молчит», от которого уходим.
        """
        import config

        if not getattr(config, "VOICE_AUTOSTART", False) or self.voice.running:
            return
        try:
            if config.LISTEN_MODE == "off":
                self.voice_mode("name")
                # Слух был выключен — значит, он занят (игра, созвон), и
                # «На связи» из телефона там лишнее: включаемся молча.
                self.voice.quiet_start = True
            self.voice_toggle()
        except Exception as exc:
            self._remember("error", f"голос не включился сам: {exc}")

    def voices_wanted_startup(self) -> None:
        """Продолжить начатое при запуске пульта.

        Если хозяин выбрал модели, но скачивание не кончилось (закрыл пульт,
        выключил компьютер, сбилась сеть), докачиваем сами. Это не
        самовольство: выбор он сделал сам, и кнопка обещала, что выбранное
        скачается. Если библиотек нет — модель всё равно не заработает, и
        ждать её тут нечего: об этом уже сказала кнопка установки.
        """
        from core import hardware, settings, voice_models

        try:
            if not hardware.voices_installed():
                return
            выбрано = settings.voices_wanted()
            if not выбрано:
                return
            очередь = voice_models.needed(выбрано)
            if очередь:
                self.voices_download(очередь)
        except Exception as exc:
            # Плохой settings.json или сломанный импорт не должны ронять пульт
            # при старте: голос и так работает, а модели можно скачать кнопкой.
            self._remember("voices_download_failed", {"error": str(exc)})

    def _phone_state_now(self) -> str:
        """Что показать на только что подключившемся телефоне."""
        voice = self.voice
        if not getattr(voice, "running", False):
            return "voiceoff"
        if not getattr(voice, "ready", False):
            return "loading"
        try:
            return voice._idle_state()
        except Exception:
            return "asleep"

    def voice_toggle(self) -> dict:
        if self.voice.running:
            self.voice.stop()
            self._warm_stop()
        else:
            # Голос загрузил бы torch, который uv сейчас меняет на диске
            # (`voices_install.запрет_torch`) — говорим словами сразу.
            if self._libs().get("active"):
                raise RuntimeError("Ставлю библиотеки голосов — голос включу "
                                   "после установки")
            with self._lock:
                if self._enroll is not None:
                    raise RuntimeError("Сначала закончи запись образца голоса")
                if any(j.get("kind") in ("run_all", "voice_level", "cloud") and
                       j.get("state") == "running" for j in self._jobs.values()):
                    raise RuntimeError("Дождись окончания проверки")
                if self.brain is None:
                    from core import settings
                    from core.brain import Brain

                    self.brain = Brain(provider=settings.get_provider())
                    self.voice._brain = self.brain
                    self._wire_brain()
                # Звук меняли, пока голос работал, — поднимаем заново.
                if getattr(self, "_audio_stale", False):
                    self._drop_audio()
                self.voice.start()
                self._warm_start()
        return self.voice_state()

    def _warm_start(self) -> None:
        """Греем соединение с облаком, пока голос включён.

        Отдельный поток внутри мозга (core/brain.py::KeepWarm): заминки
        облака на 6 с до первого слова убирает тёплое соединение. Голос
        выключили — прогрев остановился.
        """
        if self.brain is None:
            return
        try:
            self.brain.warm_start()
        except Exception as exc:
            self._remember("error", f"прогрев связи не включился: {exc}")

    def _warm_stop(self) -> None:
        if self.brain is None:
            return
        try:
            self.brain.warm_stop()
        except Exception:
            pass

    def voice_hush(self) -> dict:
        try:
            self.voice.shut_up()
        except Exception:
            pass
        # «Хватит» значит хватит и для недоговорённой фразы: ждать
        # продолжения, на которое он уже не собирается, незачем.
        try:
            self.voice._forget_held()
        except Exception:
            pass
        return self.voice_state()

    def voice_mode(self, value: str) -> dict:
        import config
        from core import settings

        if value in ("always", "name", "off"):
            settings.save_settings({"listen_mode": value})
            config.LISTEN_MODE = value
            if value == "off":
                try:
                    self.voice._close_conversation()
                except Exception:
                    pass
            try:
                self.server.send_mode(value)
            except Exception:
                pass
        return self.voice_state()
    def handle_event(self, kind: str, payload) -> None:
        # Заряд идёт мимо ленты: телефон присылает его раз в минуту, и
        # такая стока в «Голосе» только вытесняла бы живые события.
        if kind == "battery":
            self._battery(payload)
            return
        if kind == "viewport":
            # Размер экрана телефона нужен только макету в «Программах» — он
            # берёт его из `/api/apps`. В ленте событий это был бы мусор.
            return
        if kind == "volume":
            # От телефона приходит словарь с шагом, от голосовой команды —
            # сам уровень (`"тише"`, «громкость пять»). Второе уже применено
            # голосовым циклом, поэтому только пишем его в журнал; иначе
            # строчка «громкость голоса: 7» не появлялась бы вовсе.
            if isinstance(payload, dict):
                self._phone_volume(payload)
                return
            self._remember(kind, payload)
            return
        if kind in ("open_search", "open_source", "reminders_cancel",
                    "search_history_delete", "search_history_clear"):
            # Касания карточек и действия из списков: это поступки хозяина с
            # телефона, обрабатываем здесь (пульт живёт всё время), а в ленту
            # «Голоса» они не идут — там только то, что она сама сказала.
            # Сами напишем в журнал тем, что вышло.
            if kind == "open_search":
                self._open_search(payload)
            elif kind == "open_source":
                self._open_source(payload)
            elif kind == "reminders_cancel":
                self._reminders_cancel_phone(payload)
            elif kind == "search_history_delete":
                self._search_history_delete(payload)
            else:
                self._search_history_clear()
            return
        self._remember(kind, payload)
        if kind == "connected":
            self._on_connected()
        elif kind == "launch":
            self._launch(payload)
        elif kind == "menu":
            self._menu(payload)
        elif kind == "menu_flip":
            self._menu_flip(payload)
        elif kind == "action":
            # Нижняя кнопка телефона: пришёл только номер места.
            self._action(payload)
        elif kind == "screenshot":
            self._screenshot()
        elif kind == "moment":
            self._moment()
        elif kind == "mode":
            value = (payload or {}).get("value") if isinstance(payload, dict) else None
            if value in ("always", "name", "off"):
                self.voice_mode(value)
        elif kind == "listen_now":
            self._listen_now()
        elif kind == "hush":
            # Касание круга на телефоне: замолчать и закрыть разговор — «мне
            # хватит». Режим слуха не трогаем: дальше её зовут по имени, как
            # обычно, а не включают слух заново (хозяин 27.09).
            self.voice_hush()
            try:
                self.voice._close_conversation()
            except Exception:
                pass
        elif kind == "search":
            self._search()
        elif kind == "note_start":
            self._note_start()
        elif kind == "notes_list":
            self._notes_list()
        elif kind == "notes_topic":
            self._notes_topic(payload)
        elif kind == "reminders_list":
            self._reminders_phone()
        elif kind == "search_history_list":
            self._search_history_phone()

    def _phone_volume(self, payload) -> None:
        """Регулятор громкости на телефоне: `{"type": "volume", "step": ±1}`.

        Число приходит от кнопок «+» и «−», а не абсолютное значение: телефон
        не знает текущего уровня, он его просто меняет. Поэтому шаг и
        отсечение по краям — на сервере, как и у голосовой команды.
        """
        data = payload if isinstance(payload, dict) else {}
        try:
            step = int(data.get("step", 0))
        except (TypeError, ValueError):
            return
        if not step:
            return
        result = self.voice_volume(step=step)
        if not result.get("ok"):
            self._remember("error", f"громкость: {result.get('error', '')}")
        elif result.get("edge"):
            try:
                self.server.send_toast(
                    "громче некуда" if result["edge"] == "max" else "тише некуда", True)
            except Exception:
                pass

    def _battery(self, payload) -> None:
        """Заряд телефона: `{"type": "battery", "level", "charging"}`.

        Телефон шлёт это раз в минуту и при каждом изменении, поэтому в
        ленту и в журнал такое не пускаем — только переход ниже пятой
        части без зарядки, это уже «пора зарядить», и хозяин у компа
        телефона не видит. Само значение просто запоминаем: из него
        собирается подпись карточки «Телефон».
        """
        data = payload if isinstance(payload, dict) else {}
        level = data.get("level")
        # True — это тоже int, и float() от него даёт 1%: молчало бы «1%».
        if isinstance(level, bool):
            return
        try:
            level = float(level)
        except (TypeError, ValueError):
            return
        if level != level or not (0 <= level <= 100):
            return
        level = int(round(level))
        charging = data.get("charging") is True
        # Значение читает панель из своего потока, поэтому под замком.
        with self._lock:
            was_low = self._battery_low
            self._battery_level = level
            self._battery_charging = charging
            self._battery_at = time.time()
            self._battery_low = not charging and level < LOW_BATTERY
            тревога = self._battery_low and not was_low
        # Замок отпущен: log_message берёт его же, а взять дважды нельзя.
        if тревога:
            self.log_message(f"заряд телефона {level}% — пора зарядить")

    def battery_note(self) -> str:
        """Подпись для карточки «Телефон»: `82% ⚡`, `18%` или пусто.

        Пусто, когда телефон ещё ни разу не прислал заряд: в защищённом
        контексте он есть, но в обычном браузере — нет, и врать не надо.
        """
        with self._lock:
            level, charging = self._battery_level, self._battery_charging
        if level is None:
            return ""
        return f"{level}% ⚡" if charging else f"{level}%"

    def _bg(self, func, *args) -> None:
        threading.Thread(target=func, args=args, daemon=True).start()

    def voice_volume(self, step: int = 0, level: int | None = None) -> dict:
        """Новая громкость голоса с телефона: `level` — сразу, `step` — на ступень.

        Те же правила, что у голосовой команды: уровень 1–10, на краях не
        двигаемся, а хозяину говорим почему. Настройка сохраняется сразу —
        телефоном он пользуется на ходу, и переживать перезапуск пульта не
        должен.
        """
        import config
        from core import settings

        edge = ""
        if level is None:
            level = int(config.VOICE_VOLUME) + int(step or 0)
            # Шаг за край — не ошибка: «+» на десятке просто некуда. 27.09
            # это попадало в журнал как «ошибка голоса». Край — отдельным
            # полем, телефон скажет «громче некуда».
            if level > config.VOICE_VOLUME_MAX:
                level, edge = config.VOICE_VOLUME_MAX, "max"
            elif level < config.VOICE_VOLUME_MIN:
                level, edge = config.VOICE_VOLUME_MIN, "min"
        level = int(level)
        if not config.VOICE_VOLUME_MIN <= level <= config.VOICE_VOLUME_MAX:
            return {"ok": False, "error": f"уровень должен быть "
                                          f"{config.VOICE_VOLUME_MIN}…{config.VOICE_VOLUME_MAX}"}
        if level == int(config.VOICE_VOLUME):
            answer = {"ok": True, "value": level}
            if edge:
                answer["edge"] = edge
            return answer
        try:
            settings.save_settings({"voice_volume": level})
            settings.apply_to_config()
        except OSError as exc:
            return {"ok": False, "error": f"не сохранила: {exc}"}
        self._remember("volume", level)
        try:
            self.server.send_volume(level)
        except Exception:
            pass
        return {"ok": True, "value": level}

    def _on_connected(self) -> None:
        import config
        from core import sysinfo

        try:
            self.server.send_mode(config.LISTEN_MODE)
        except Exception:
            pass
        try:
            self.server.send_volume(int(getattr(config, "VOICE_VOLUME", 10)))
        except Exception:
            pass
        # Честное состояние сразу: раньше телефон при подключении рисовал
        # «слушаю», даже когда голос в пульте выключен или ещё грузится.
        try:
            self.server.send_state(self._phone_state_now(), "")
        except Exception:
            pass
        try:
            self.server.send_system(sysinfo.snapshot())
        except Exception:
            pass
        self._bg(self._send_apps)
        # Нижние кнопки — отдельным сообщением: телефон рисует их по своим
        # настройкам, и без него низ экрана остаётся пустым.
        self._bg(self._send_actions)

    def _send_apps(self) -> None:
        from core import app_icons, launcher

        try:
            apps = launcher.decorate_menus(
                launcher.load(), on_error=lambda text: self._remember("bookmarks_failed", text)
            )
        except Exception as exc:
            self._remember("apps_failed", f"{type(exc).__name__}: {exc}")
            return
        try:
            self.server.send_apps(app_icons.decorate(apps))
        except Exception:
            pass

    def _send_actions(self) -> None:
        """Разослать телефонам нижние кнопки: четыре ячейки из настроек.

        Телефон сам ничего не выполняет — он знает только номер кнопки, а что
        она делает, решает сервер по **своим** настройкам. Поэтому в
        сообщении нет ни сочетаний клавиш, ни имён программ: только то, что
        нужно нарисовать (подпись, значок) и для подсветки переключателя.
        """
        from core import settings

        try:
            cells = settings.phone_actions()
        except Exception as exc:
            self._remember("actions_failed", f"{type(exc).__name__}: {exc}")
            return
        try:
            self.server.send_actions(action_items(cells))
        except Exception:
            pass

    def _action(self, payload) -> None:
        """Нажата нижняя кнопка: `{"type": "action", "slot": n}`.

        С телефона приходит только номер. Ячейку берём из своих настроек, так
        что подменить кнопку чужим сообщением нельзя: мусор в номере — это
        ничего не сделать, а не выполнить что попало.
        """
        from core import settings

        data = payload if isinstance(payload, dict) else {}
        try:
            slot = int(data.get("slot"))
        except (TypeError, ValueError):
            return
        # Дробный номер — тоже мусор: `int(1.5)` молча стал бы 1 и выполнил
        # чужую кнопку. Настоящий номер — целый.
        try:
            if float(data.get("slot")) != slot:
                return
        except (TypeError, ValueError):
            return
        try:
            cells = settings.phone_actions()
        except Exception as exc:
            self._remember("actions_failed", f"{type(exc).__name__}: {exc}")
            return
        if not 0 <= slot < len(cells):
            return
        cell = cells[slot]
        kind = cell.get("kind")
        if kind == "builtin":
            # Встроенные кнопки телефон обычно зовёт сам (они ведут себя как
            # раньше), но если вдруг придёт сюда — выполняем тот же путь.
            self._builtin(cell.get("id", ""))
        elif kind == "hotkey":
            self._hotkey(cell)
        elif kind == "app":
            self._app_action(cell.get("app", ""))
        elif kind == "menu":
            self._menu({"app": cell.get("app", ""), "item": cell.get("item", "")})
        # `none` — кнопки нет, нажимать нечего.

    def _builtin(self, name: str) -> None:
        """Встроенная кнопка — ровно тот же путь, что звал телефон раньше."""
        if name == "screenshot":
            self._screenshot()
        elif name == "moment":
            self._moment()
        elif name == "search":
            self._search()
        elif name == "note_start":
            self._note_start()

    def _hotkey(self, cell: dict) -> None:
        """Своё сочетание клавиш на нижней кнопке.

        Нажимается тем же `hotkeys.press`, что и пункт подменю, но состояние
        переключателя здесь не переворачивается: пункт подменю о нём знает, а
        у кнопки такого пункта нет.
        """
        from core import hotkeys

        title = cell.get("title", "") or "кнопка"

        def run() -> None:
            try:
                hotkeys.press(hotkeys.parse(cell.get("keys", "")))
            except Exception as exc:
                self._remember("action_failed", {
                    "line": f"кнопка телефона: {title} → {exc}"})
                try:
                    self.server.send_toast(str(exc)[:80], ok=False)
                    self.server.send_sound("fail")
                except Exception:
                    pass
                return
            self._remember("action_done", {"line": f"кнопка телефона: {title}"})
            try:
                self.server.send_toast(title, ok=True)
                self.server.send_sound("open")
            except Exception:
                pass

        self._bg(run)

    def _app_action(self, app_id: str) -> None:
        """Нижняя кнопка «открыть программу».

        Программы может не быть: apps.json хозяин правит руками и мог убрать
        запись. Тогда не падаем, а говорим прямо — иначе на экране была бы
        кнопка, которая молча ничего не делает.
        """
        from core import launcher

        try:
            запись = launcher.find_app(app_id)
        except Exception:
            запись = None
        if not запись:
            self._remember("action_failed", {
                "line": f"кнопка телефона: такой программы больше нет ({app_id})"})
            try:
                self.server.send_toast("такой программы больше нет", ok=False)
                self.server.send_sound("fail")
            except Exception:
                pass
            return
        self._launch({"id": app_id})

    def _launch(self, payload) -> None:
        app_id = (payload or {}).get("id", "") if isinstance(payload, dict) else ""

        def run() -> None:
            from core import launcher

            try:
                ok, what = launcher.launch(app_id)
            except Exception as exc:
                ok, what = False, f"{type(exc).__name__}: {exc}"
            self._remember("launched" if ok else "launch_failed", what)
            try:
                self.server.send_toast(what, ok=ok)
                self.server.send_sound("open" if ok else "fail")
            except Exception:
                pass

        self._bg(run)

    def _menu(self, payload) -> None:
        """Пункт подменю с телефона: `{"type": "menu", "app", "item"}`.

        Ключ приходит с телефона, поэтому искать его надо только в apps.json
        и в закладках — из пришедшего ничего не исполняем.
        """
        data = payload if isinstance(payload, dict) else {}
        app_id = str(data.get("app", ""))[:80]
        item = str(data.get("item", ""))[:80]

        def run() -> None:
            from core import bookmarks, launcher

            marks: list = []
            try:
                marks = bookmarks.read(
                    on_error=lambda text: self._remember("bookmarks_failed", text)
                )
            except Exception:
                marks = []
            try:
                ok, what = launcher.run_menu(app_id, item, marks)
            except Exception as exc:
                ok, what = False, f"{type(exc).__name__}: {exc}"
            title = next(
                (a.get("title", app_id) for a in launcher.read_list()
                 if (a.get("id") or a.get("title", "")) == app_id),
                app_id,
            )
            # Для пункта «Открыть программу» стрелки не нужно: названия
            # программы и результата запуска совпадают.
            if item == launcher.MENU_LAUNCH:
                line = f"кнопка телефона: {what}" if ok else \
                    f"кнопка телефона: {what} не вышло"
            else:
                line = f"кнопка телефона: {title} → {what}" if ok else \
                    f"кнопка телефона: {title} → {what} не вышло"
            self._remember("menu_done" if ok else "menu_failed", {"line": line})
            try:
                self.server.send_toast(what, ok=ok)
                self.server.send_sound("open" if ok else "fail")
            except Exception:
                pass
            # Подсветка переключателей едет после нажатия: телефон не должен
            # догадываться о перевороте — сервер сказал, значит нарисовано.
            if ok and item != launcher.MENU_LAUNCH:
                self._send_menu_state(app_id)

        self._bg(run)

    def _send_menu_state(self, app_id: str) -> None:
        """Рассылает телефонам состояние переключателей программы."""
        from core import launcher

        try:
            on = launcher.menu_states(app_id)
        except Exception:
            return
        if not on:
            return
        try:
            self.server.send_menu_state(app_id, on)
        except Exception:
            pass

    def _menu_flip(self, payload) -> None:
        """Поправить подсветку без нажатия: `{"type": "menu_flip", "app", "item"}`.

        Хозяин переключил микрофон мышкой в самом Discord, телефон об этом не
        узнал и показывал не то. Сочетание здесь не жмётся, поэтому и
        запускать программу не нужно.
        """
        data = payload if isinstance(payload, dict) else {}
        app_id = str(data.get("app", ""))[:80]
        item = str(data.get("item", ""))[:80]

        def run() -> None:
            from core import launcher

            try:
                ok, what = launcher.flip_menu(app_id, item)
            except Exception as exc:
                ok, what = False, f"{type(exc).__name__}: {exc}"
            if ok:
                self._remember("menu_flipped", {"line": f"подсветка: {what}"})
            else:
                self._remember("menu_failed", {"line": f"подсветка: {what} не вышло"})
            try:
                self.server.send_toast("подсветка поправлена" if ok else what, ok=ok)
            except Exception:
                pass
            if ok:
                self._send_menu_state(app_id)

        self._bg(run)

    def _screenshot(self) -> None:
        """Кнопка «экран» на телефоне — тот же `hands.shoot`, что и у модели."""

        def run() -> None:
            from core import hands

            try:
                hands.shoot(self._remember, lambda: self.server)
            except Exception as exc:
                self._remember("shot_failed", f"{type(exc).__name__}: {exc}")
                try:
                    self.server.send_toast(f"{type(exc).__name__}: {exc}", ok=False)
                    self.server.send_sound("fail")
                except Exception:
                    pass

        self._bg(run)

    def _moment(self) -> None:
        """Кнопка «момент» на телефоне — тот же `hands.clip`, что и у модели."""

        def run() -> None:
            from core import hands

            try:
                what, off = hands.clip(self._remember, lambda: self.server)
            except Exception as exc:
                # Событие и звук отказа hands.clip уже отправил — только надпись.
                try:
                    self.server.send_toast(f"{type(exc).__name__}: {exc}", ok=False)
                except Exception:
                    pass
                return
            # Раз нажал «момент» — повтор ему нужен: включаем сразу.
            self.server.send_toast(
                "Повтор был выключен — момент не сохранился. Включаю" if off else what,
                ok=not off,
            )

        self._bg(run)

    def _listen_now(self) -> None:
        voice = self.voice
        try:
            voice.arm_button()
        except Exception:
            pass

    def _search(self) -> None:
        """Кнопка поиска на телефоне: следующая фраза — запрос в интернет.

        Как кнопка «слушаю без имени», но с одной разницей: взводится флаг
        в VoiceLoop, и следующая услышанная фраза уйдёт в модель с
        обязательным интернетом. Проверки — здесь: без голоса и без
        включённого в настройках интернета телефон ждал бы ответа, которого
        не будет, поэтому отвечаем сразу и коротко.
        """
        import config

        voice = self.voice
        if not (getattr(voice, "running", False) and getattr(voice, "ready", False)):
            self._search_fail("голос выключен")
            return
        if not getattr(config, "WEB_SEARCH", False):
            self._search_fail("интернет выключен в настройках")
            return

        try:
            voice.arm_button(search=True)
        except Exception:
            self._search_fail("поиск не включился")

    def _note_start(self) -> None:
        """Кнопка «заметка» на телефоне: открыть диктовку, как голосом.

        Голос выключен — сказать об этом телефону сразу: иначе он ждал бы
        «Диктуй», которого не будет.
        """
        voice = self.voice
        started = False
        try:
            started = bool(voice.start_note())
        except Exception as exc:
            self._remember("error", f"заметка с телефона: {exc}")
        if not started:
            try:
                self.server.send_toast("голос выключен — диктовать некому", False)
            except Exception:
                pass

    def _reminders_phone(self) -> None:
        """Телефон открыл карточку «Напоминания»: отдать список.

        Только чтение и только по уже авторизованному сокету телефона: сам
        список — личный файл хозяина, а локальный `GET /api/reminders` с
        телефона недоступен. Ничего на компьютере не выполняется — только
        чтение и ответ тому, кто спросил.

        Список уже собирает `reminders_list()` — тем же, что и Панель, чтобы
        телефон и пульт не показывали разное.
        """
        self._bg(self._отдать_напоминания)

    def _отдать_напоминания(self, ошибка: str = "") -> None:
        """Ответить телефону списком напоминаний (или понятной ошибкой).

        Отдельный метод, потому что тем же ответом пульта заканчивает и отмена
        с телефона: хозяин должен увидеть, что осталось стоять, даже когда
        отменять было нечего.
        """
        try:
            items = self.reminders_list().get("items") or []
        except Exception as exc:
            self._remember("error", f"напоминания на телефон: {exc}")
            items, ошибка = [], ошибка or "не получила список"
        try:
            self.server.send_reminders(items, error=ошибка)
        except Exception as exc:
            self._remember("error", f"напоминания на телефон: {exc}")

    def _reminders_cancel_phone(self, payload) -> None:
        """Телефон попросил снять одно напоминание: `{"type":
        "reminders_cancel", "id": "r1"}`.

        Отмена ровно одного — тем же `reminders_cancel`, что зовёт Панель: он
        проверяет вид id и сам берёт `pending` после отмены. Вторую проверку
        писать не надо, иначе телефон и пульт разойдутся, кому можно сносить.
        «Отменить всё» с телефона нельзя: `id` вида `all` Панель и не принимает.

        Отказ — честным текстом, а не пустым списком молча.
        """
        body = payload if isinstance(payload, dict) else {}

        def run() -> None:
            try:
                self.reminders_cancel(body)
            except Exception as exc:
                self._remember("reminder_cancel_error", str(exc))
                self._отдать_напоминания(str(exc) or "не получилось отменить")
                return
            self._отдать_напоминания()

        self._bg(run)

    def _search_history_phone(self) -> None:
        """Телефон открыл «Историю поиска»: отдать запросы и время.

        Читается `core/search_history.py` — тот же файл, куда кладутся
        настоящие успешные поиски. Ничего не выполняется и никуда не
        открывается: тап по строке телефон шлёт отдельно, и там уже
        `open_search` (соберёт адрес сервер).
        """
        self._bg(self._отдать_историю)

    def _отдать_историю(self, ошибка: str = "") -> None:
        """Ответить телефону историей поиска (или понятной ошибкой).

        Отдельный метод для тех же двух причин, что и у напоминаний: после
        удаления записи или очистки всего хозяин должен увидеть, что осталось.
        """
        from core import search_history

        try:
            записи = search_history.entries()
        except Exception as exc:
            self._remember("error", f"история поиска на телефон: {exc}")
            записи, ошибка = [], ошибка or "не получила список"
        try:
            self.server.send_search_history(записи, error=ошибка)
        except Exception as exc:
            self._remember("error", f"история поиска на телефон: {exc}")

    def _search_history_delete(self, payload) -> None:
        """Телефон попросил убрать одну запись: `{"type":
        "search_history_delete", "id": …}`.

        Убирается строго по `id` из `core/search_history.py`: тот же вопрос
        может лежать в истории дважды, и снести все копии по тексту — значило
        бы снести лишнее. Ни пути, ни адреса телефон не присылает и получить
        не может — `id` это ключ списка, а не имя файла.
        """
        data = payload if isinstance(payload, dict) else {}
        что = str(data.get("id", "") or "").strip()

        def run() -> None:
            from core import search_history

            try:
                убрано = search_history.delete(что)
            except Exception as exc:
                self._remember("error", f"история поиска: удаление не вышло — {exc}")
                self._отдать_историю("не получилось убрать")
                return
            if not убрано:
                # Честный отказ: молчаливое «готово» хозяин бы принял за
                # удаление, а запись осталась бы на месте.
                self._remember("search_history_delete", f"нет такой записи: {что}")
                self._отдать_историю("этой записи уже нет")
                return
            self._remember("search_history_delete", что)
            self._отдать_историю()

        self._bg(run)

    def _search_history_clear(self) -> None:
        """Телефон попросил очистить всю историю поиска.

        Файл один и общий с пультом, поэтому чистится целиком — «очистить
        историю» и значит «всю». Ответ — то, что осталось (то есть ничего),
        а не тишина: хозяин должен увидеть, что команда сработала.
        """
        def run() -> None:
            from core import search_history

            try:
                сколько = search_history.clear()
            except Exception as exc:
                self._remember("error", f"история поиска: очистка не вышла — {exc}")
                self._отдать_историю("не получилось очистить")
                return
            self._remember("search_history_clear", f"убрано записей: {сколько}")
            self._отдать_историю()

        self._bg(run)

    def _notes_list(self) -> None:
        """Телефон открыл заметки: список разделов и тем.

        Читаем папку в фоне — события с телефона идут из потока сервера, и
        чтение файлов там застряло бы все кнопки. На телефон уходят только
        имена, счётчики и даты: пути к файлам ему не нужны и показывали бы
        хозяину устройство его собственной папки.
        """
        from core import notes

        def run() -> None:
            try:
                разделы = notes.sections()
            except Exception as exc:
                self._remember("error", f"заметки для телефона: {exc}")
                разделы = []
            список = [
                {
                    "name": str(раздел.get("name", "")),
                    "updated": str(раздел.get("updated", "")),
                    "topics": [
                        {
                            "name": str(тема.get("name", "")),
                            "entries": int(тема.get("entries", 0) or 0),
                            "updated": str(тема.get("updated", "")),
                        }
                        for тема in раздел.get("topics", []) or []
                    ],
                }
                for раздел in разделы
            ]
            try:
                self.server.send_notes(список)
            except Exception as exc:
                self._remember("error", f"заметки на телефон: {exc}")

        self._bg(run)

    def _notes_topic(self, payload) -> None:
        """Телефон открыл тему: отдаём её записи.

        Только чтение: удалять с телефона нельзя, телефон — не пульт. Имя
        темы приходит с телефона, а `notes.read` проверяет, что оно не выводит
        за папку заметок; не прочиталось — говорим об этом телефону и молчим,
        а не роняем поток событий.
        """
        from core import notes

        data = payload if isinstance(payload, dict) else {}
        раздел = str(data.get("section", "") or "")
        тема = str(data.get("topic", "") or "")

        def run() -> None:
            try:
                прочитано = notes.read(раздел, тема)
            except Exception as exc:
                self._remember("error", f"тема с телефона: {exc}")
                try:
                    self.server.send_note_topic(
                        {"section": раздел, "topic": тема, "error": "не прочитала"})
                except Exception:
                    pass
                return
            # Сырое (`raw`) телефон не получает: читать ему причёсанный текст.
            записи = [
                {
                    "when": str(запись.get("when", "")),
                    "title": str(запись.get("title", "")),
                    "text": str(запись.get("text", "")),
                }
                for запись in прочитано.get("entries", []) or []
            ]
            try:
                self.server.send_note_topic(
                    {"section": раздел, "topic": тема, "entries": записи})
            except Exception as exc:
                self._remember("error", f"тема на телефон: {exc}")

        self._bg(run)

    def _open_search(self, payload) -> None:
        """Телефон попросил открыть запрос из карточки: `{"type": "open_search",
        "query": …}`.

        Адрес строит сервер: телефон присылает только сам запрос. Поисковик —
        `config.SEARCH_OPEN_URL` (строка с `{q}`, чтобы сменить на Яндекс,
        достаточно поменять одну строку в config).

        Касание не должно уводить телефон из карточки молча: ждём открытия в
        браузере и говорим хозяину в тост и в журнал.
        """
        import config
        from urllib.parse import quote_plus

        data = payload if isinstance(payload, dict) else {}
        запрос = " ".join(str(data.get("query") or "").split())[:200]
        if not запрос:
            self._browser_fail("пустой запрос")
            return
        try:
            url = str(getattr(config, "SEARCH_OPEN_URL", "") or "").replace(
                "{q}", quote_plus(запрос))
        except Exception as exc:
            self._browser_fail(f"{type(exc).__name__}: {exc}")
            return
        if not url.startswith(("http://", "https://")):
            self._browser_fail("неправильный адрес поисковика")
            return

        def run() -> None:
            from core import launcher

            try:
                ok, what = launcher.open_web(url)
            except Exception as exc:
                ok, what = False, f"{type(exc).__name__}: {exc}"
            self.log_message(f"поиск открыт в браузере: {запрос}" if ok
                             else f"поиск не открылся: {what}")
            try:
                self.server.send_toast("Открыла на компе" if ok else what, ok=ok)
            except Exception:
                pass

        self._bg(run)

    def _open_source(self, payload) -> None:
        """Телефон попросил открыть источник из карточки: `{"type":
        "open_source", "index": N}`.

        Только **номер** в последнем отправленном списке, не адрес. Иначе
        страница с телефона заставила бы комп открыть что угодно — свой
        пульт, файловый адрес, скрипт. Адрес берём из того списка, который
        сам же телефону отправили (`PhoneServer.send_search_result`), и
        открываем только `http(s)` — `launcher.open_web` проверяет.

        Номер за пределами списка — отказ, а не «последний источник»: иначе
        опечатка на телефоне открыла бы не то.
        """
        data = payload if isinstance(payload, dict) else {}
        try:
            номер = int(data.get("index"))
        except (TypeError, ValueError):
            self._browser_fail("нет такого источника")
            return
        # Дробный номер — тоже мусор: `int(1.5)` молча стал бы 1 и открыл бы
        # чужой источник. Настоящий номер — целый (как в `_action`).
        try:
            if float(data.get("index")) != номер:
                self._browser_fail("нет такого источника")
                return
        except (TypeError, ValueError):
            self._browser_fail("нет такого источника")
            return
        источники = list(getattr(self.server, "last_sources", None) or [])
        if not 0 <= номер < len(источники):
            self._browser_fail("нет такого источника")
            return
        источник = источники[номер]
        if not isinstance(источник, dict):
            self._browser_fail("нет такого источника")
            return
        адрес = str(источник.get("url") or "")
        сайт = str(источник.get("host") or "")

        def run() -> None:
            from core import launcher

            try:
                ok, what = launcher.open_web(адрес)
            except Exception as exc:
                ok, what = False, f"{type(exc).__name__}: {exc}"
            self.log_message(f"источник открыт: {сайт or адрес}" if ok
                             else f"источник не открылся: {сайт or адрес} — {what}")
            try:
                self.server.send_toast("Открыла на компе" if ok else what, ok=ok)
            except Exception:
                pass

        self._bg(run)

    def _browser_fail(self, text: str) -> None:
        """Касание карточки, которому открыть нечего: говорим честно."""
        self.log_message(f"с телефона: {text}")
        try:
            self.server.send_toast(text, ok=False)
            self.server.send_sound("fail")
        except Exception:
            pass

    def _search_fail(self, text: str) -> None:
        """Поиск не вышел: сказать об этом телефону и в журнал."""
        self._remember("search_fail", text)
        try:
            self.server.send_search_fail(text)
        except Exception:
            pass

    def _порт(self) -> int:
        """Порт, на котором работает эта копия пульта.

        У двух Труб на одном компьютере он разный, поэтому ссылка телефона,
        QR и Origin берут его отсюда, а не из `config.PHONE_PORT`. Сервера
        нет (проверки в тестах) — привычный порт из настроек.
        """
        import config

        сервер = getattr(self, "server", None)
        порт = getattr(сервер, "port", None)
        return int(порт) if isinstance(порт, int) else int(config.PHONE_PORT)

    def settings_snapshot(self) -> dict:
        import config
        from core import autostart, memory, settings
        from core.phone import local_addresses

        values = settings.load_settings()
        provider = settings.get_provider()
        providers = {}
        for name, spec in config.PROVIDERS.items():
            providers[name] = {
                "model": spec.get("model", "?"),
                # «Ключ есть» спрашиваем у settings: локальному серверу ключ
                # не нужен, и без него он всё равно годен к хождению.
                "has_key": settings.has_key(name),
                "local": settings.is_local(name),
            }
        try:
            persona = settings.load_persona()
        except Exception:
            persona = ""
        try:
            mem_text = memory.as_text()
        except Exception:
            mem_text = ""
        try:
            voices = self._available_voices()
        except Exception:
            voices = []
        try:
            samples = self.voice_samples()
        except Exception:
            samples = []
        try:
            from core.speaker import Voiceprint

            owner_known = bool(Voiceprint().known)
        except Exception:
            owner_known = False
        # Автозапуск не в settings.json: истина — ярлык в папке автозагрузки.
        try:
            автозапуск = autostart.enabled()
        except Exception:
            автозапуск = False
        # Ключ привязки телефона: пульт вставляет его в ссылку и QR-код.
        # Отдаётся только сюда — /api/settings закрыт для сети.
        try:
            from core.phone import phone_key

            ключ_телефона = phone_key()
        except Exception:
            ключ_телефона = ""
        return {
            "provider": provider, "providers": providers,
            "settings": values, "persona": persona, "phone_key": ключ_телефона,
            "memory": mem_text, "addresses": local_addresses(),
            # Порт этого пульта: у второй копии Трубы он свой, и адрес
            # телефона в QR должен вести именно к ней.
            "phone_port": self._порт(),
            "voices": voices, "voice_samples": samples,
            "owner_known": owner_known, "autostart": автозапуск,
        }

    # --- Звук: микрофон, колонки, проверка ---------------------------------
    #
    # Один маршрут на всё звуковое (`/api/audio`), как это сделано в играх:
    # человек видит список микрофононов и список устройств вывода рядом с
    # текущим выбором и меняет его одним списком. Никаких индексов: индексы
    # между переподключениями скачут, а имя и канал — нет.

    AUDIO_TEST_SECONDS = 3.0
    # Пороги вердикта записи микрофона.
    #
    # `ПИК_ТИШИНЫ` — ниже этого максимума в записи нет ничего, кроме шума
    # комнаты: такой микрофон не слышит, и виноват не голос хозяина, а выбор
    # устройства или его выключатель. Порог тот же, что у живой речи
    # (`core.audio_in.BARGE_MIN_RMS`): тише этого человек не говорил.
    # `УРОВЕНЬ_ТИХО` — уровень записи (та же шкала, что у полоски) ниже трети
    # шкалы: голос слышен, но приходится подносить микрофон вплотную.
    ПИК_ТИШИНЫ = 0.01
    УРОВЕНЬ_ТИХО = 0.25
    # Сколько ждём конца потоку голоса после `stop()` в пробе мастера.
    #
    # `stop()` только просит поток выйти: тот дорабатывает текущую фразу,
    # выгружает модель и микрофон держит ещё секунды. Запись в эти секунды
    # получила бы «выключи голос» — и проба была бы негодной ровно там, где
    # нужнее всего. Ждём поток, но не дольше этого: зависшая выгрузка модели
    # не должна держать хозяина на шаге мастера.
    ПОЖДАТЬ_ГОЛОС = 15.0

    def audio_settings(self) -> dict:
        """Списки устройств и что выбрано сейчас. Пустой список — не поломка."""
        import config

        try:
            входы = config.list_inputs()
        except Exception:
            входы = []
        try:
            выходы = config.list_outputs()
        except Exception:
            выходы = []
        return {
            "ok": True,
            "inputs": входы,
            "outputs": выходы,
            "current": {
                "mic_name": str(getattr(config, "MIC_NAME", "") or ""),
                "mic_channel": int(getattr(config, "MIC_CHANNEL", 0) or 0),
                "output": str(getattr(config, "OUTPUT", "speakers") or "speakers"),
                "speaker_name": str(getattr(config, "SPEAKER_NAME", "") or ""),
            },
        }

    def audio_level(self) -> dict:
        """Громкость микрофона прямо сейчас: 0…1, для полоски в пульте.

        Голос выключен — микрофон не открыт, и уровня нет. Говорить «0» здесь
        было бы враньём: полоска показала бы «микрофон молчит», а на деле он
        просто не включён.
        """
        listener = getattr(getattr(self, "voice", None), "_listener", None)
        if listener is None or not bool(getattr(self.voice, "running", False)):
            return {"ok": False, "error": "голос выключен"}
        try:
            уровень = float(getattr(listener, "last_level", 0.0) or 0.0)
        except (TypeError, ValueError):
            уровень = 0.0
        return {"ok": True, "level": max(0.0, min(1.0, уровень))}

    # Шаг 4 мастера смотрит на **выбранный** микрофон, а не на тот, который
    # сейчас держит голос. Частые запросы к `/api/audio/level` там шлёт
    # страница, поэтому короткий замер ограничиваем: 0.4 с между ними — это
    # две-три полоски в секунду, глазом достаточно, а микрофон не дёргается.
    PREVIEW_MIN_GAP = 0.4
    # Сам замер короткий: полоска должна жить, но не держать устройство.
    PREVIEW_SECONDS = 0.25

    def audio_level_preview(self, mic_name: str = "",
                            mic_channel: int = 0) -> dict:
        """Уровень **выбранного** микрофона для шага 4 мастера.

        Обычный `audio_level` отвечает уровнем работающего голоса: на шаге
        мастера голоса может ещё не быть, а если он есть — держит прошлый
        микрофон, и полоска врала бы. Поэтому здесь запрос сам называет
        устройство, а сервер отвечает ровно по нему.

        Работающий слушатель при этом не трогаем: если он слушает ровно этот
        микрофон и вход, его уровень и есть ответ, второй захват не нужен.
        Иначе меряем коротко и сразу закрываем; если Windows не даёт
        открыть устройство, так и говорим — уровень чужого микрофона здесь
        был бы враньём.
        """
        import config

        имя = str(mic_name or "").strip()
        try:
            вход = int(mic_channel or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"вход должен быть числом, а не «{mic_channel}»"}
        if вход < 0:
            return {"ok": False, "error": f"вход должен быть не меньше нуля, а не {вход}"}

        # Список устройств — тот же, что видит страница: замерять того, чего
        # в списке нет, нельзя, иначе это будет тихий обход выбора.
        try:
            устройства = config.list_inputs()
        except Exception as exc:
            return {"ok": False, "error": f"не удалось получить список микрофонов: {exc}"}
        if not имя:
            # Ничего не выбрано — значит системный, как и в голосе.
            устройство = next((у for у in устройства if у.get("default")), None)
            if устройство is None and устройства:
                устройство = устройства[0]
            if устройство is None:
                return {"ok": False, "error": "микрофон не найден — подключи микрофон"}
        else:
            устройство = next((у for у in устройства
                               if str(у.get("name") or "") == имя), None)
            if устройство is None:
                близкие = [у for у in устройства if имя.lower() in str(у.get("name", "")).lower()]
                if близкие:
                    устройство = близкие[0]
                else:
                    return {"ok": False,
                            "error": f"микрофон «{имя}» сейчас не подключён"}
        каналов = int(устройство.get("channels") or 1)
        if вход >= каналов:
            return {"ok": False,
                    "error": f"у микрофона «{устройство.get('name', '')}» только "
                             f"{каналов} вход(а), а выбран {вход + 1}"}

        # Голос слушает ровно это устройство — берём его уровень, не открывая
        # микрофон второй раз (Windows второго захвата не даёт).
        слушает = self._слушает_устройство(устройство.get("index"), вход)
        if слушает is not None:
            return {"ok": True, "level": слушает, "source": "voice"}

        # Тот же голос, но другой микрофон или другой вход: его уровень здесь
        # не годится, меряем нужное устройство сами.
        индекс = устройство.get("index")
        if индекс is None:
            return {"ok": False, "error": "микрофон не найден в списке устройств"}
        # Захват микрофона — общий с пробой. Идёт проба: устройство сейчас
        # занято на три секунды, честно говорим об этом и **не** открываем его
        # вторым захватом. Полоска вернётся сама на следующем проходе.
        захват = self._level_capture_lock
        if not захват.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "идёт проверка звука — полоска вернётся сразу после неё"}
        try:
            now = time.monotonic()
            with self._level_preview_lock:
                прошло = now - self._level_preview_at
                if прошло < self.PREVIEW_MIN_GAP and self._level_preview_key == (индекс, вход):
                    # Частые запросы не открывают микрофон снова.
                    if self._level_preview_level is not None:
                        return {"ok": True, "level": self._level_preview_level, "source": "measure"}
                    return {"ok": False, "error": "микрофон меряется — подожди долю секунды"}
                self._level_preview_at = now
                self._level_preview_key = (индекс, вход)
                self._level_preview_level = None
            try:
                from core.audio_in import measure_level

                уровень = float(measure_level(индекс, вход, self.PREVIEW_SECONDS))
            except Exception as exc:
                # Устройство занято или не открывается — это ответ «почему полоска
                # молчит», а не тишина и не уровень чужого микрофона.
                return {"ok": False, "error": f"микрофон «{устройство.get('name', '')}» "
                                              f"не даёт замерить уровень: {exc}"}
            уровень = max(0.0, min(1.0, уровень))
            with self._level_preview_lock:
                self._level_preview_level = уровень
            return {"ok": True, "level": уровень, "source": "measure"}
        finally:
            захват.release()

    def _слушает_устройство(self, индекс, вход: int):
        """Уровень работающего голоса, если он слушает ровно это устройство.

        `None` — голос выключен или слушает другое: тогда нужен свой замер.
        """
        listener = getattr(getattr(self, "voice", None), "_listener", None)
        if listener is None or not bool(getattr(self.voice, "running", False)):
            return None
        if индекс is None:
            return None
        try:
            if int(getattr(listener, "device", -1)) != int(индекс):
                return None
            if int(getattr(listener, "channel", 0) or 0) != вход:
                return None
            уровень = float(getattr(listener, "last_level", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, уровень))

    def audio_test(self, проба: bool = False) -> dict:
        """Записать 3 секунды с выбранного микрофона и сказать, что он услышал.

        Запись **не играется**: она лежит в памяти до нажатия «Прослушать»
        (`audio_test_play`), потому что проигранная один раз мимо хозяина
        запись ничего не проверяет.

        Работающий голос держит микрофон, поэтому второй раз его открыть
        нельзя — вернём просьбу выключить голос. Так же сделано в «Проверке
        распознавания» и в записи образца: микрофон занят им.

        `проба` — запрос мастера первого запуска. Он отличается от обычной
        кнопки настроек тем, что голос на время пробы снимает и возвращает
        **сервер**: показывать это просьбой «выключи голос» мастеру нельзя,
        у него кнопки «Дальше» ждут ответа, а хозяин может закрыть вкладку
        на середине. Остановка, запись и включение обратно идут под одним
        замком и в `finally`, поэтому голос не остаётся выключенным — даже
        если запись или проигрывание сорвалось.
        """
        if проба:
            return self._audio_probe()
        return self._audio_record_once()

    def _голос_поток_жив(self) -> bool:
        """Жив ли поток голоса. Для пробы это и есть «микрофон занят»."""
        try:
            return bool(getattr(self.voice, "running", False))
        except Exception:
            return False

    def _дождаться_остановки_голоса(self) -> bool:
        """Дождаться конца потока голоса после `stop()`. True — поток вышел.

        `stop()` сам поток не ждёт, а у голоса `running` — это «поток жив», и
        он остаётся живым ещё какое-то время, пока цикл выходит и модель
        выгружается. Без этого ожидания проба мастера отвечала бы «выключи
        голос на время проверки» на голос, который уже выключен. Ждём не дольше
        `ПОЖДАТЬ_ГОЛОС`: зависшая выгрузка модели не должна держать хозяина.

        Потока нет (`_thread` не у подмены) — судим по `running`.
        """
        поток = getattr(self.voice, "_thread", None)
        ждать = getattr(поток, "join", None)
        if callable(ждать):
            try:
                ждать(timeout=self.ПОЖДАТЬ_ГОЛОС)
            except Exception:
                return not self._голос_поток_жив()
            return not bool(getattr(поток, "is_alive", lambda: False)())
        return not self._голос_поток_жив()

    def _поднять_голос_после_пробы(self) -> None:
        """Вернуть голос после пробы: как `voice_toggle`, но без проверок.

        Если звук меняли при работающем голосе, `_audio_stale` поднят — старые
        микрофон и колонки поднимать нельзя, сначала `_drop_audio()`.
        Уже работающий голос второй раз не запускаем.
        """
        if getattr(self.voice, "running", False):
            return
        if getattr(self, "_audio_stale", False):
            self._drop_audio()
        self.voice.start()
        self._warm_start()

    def _audio_probe(self) -> dict:
        """Проба микрофона из мастера: голос снимаем, пишем, возвращаем как было.

        Голос включаем обратно только если он был включён: выключенный остаётся
        выключенным. Не поднялся — возвращаем честный отказ с причиной, молчать
        после пробы нельзя.

        Остановка голоса тоже под защитой: если она погасила голос наполовину и
        упала, восстановление всё равно должно произойти, а проба — честно
        отклониться, не выдавая записанный текст за успех.
        """
        замок = self._audio_probe_lock
        if not замок.acquire(blocking=False):
            return {"ok": False, "error": "проба звука уже идёт — подожди пару секунд"}
        # Захват микрофона — общий с коротким замером полоски (`_level_capture_lock`).
        # Замер длится 0,25 с, поэтому проба ждёт его до секунды и затем пишет
        # спокойно. Сама проба ждёт не больше этого: если микрофон всё ещё занят
        # (кто-то другой пишет), честно отказываем, а не висим.
        захват = self._level_capture_lock
        if not захват.acquire(timeout=1.0):
            замок.release()
            return {"ok": False, "error": "микрофон занят другой записью — подожди пару секунд и попробуй снова"}
        try:
            return self._проба_под_захватом()
        finally:
            захват.release()
            замок.release()

    def _проба_под_захватом(self) -> dict:
        """Сама проба. Замки (проба и захват микрофона) уже взяты вызывающим."""
        былГолос = bool(getattr(self.voice, "running", False))
        сбойСтопа = None
        # Поток голоса не вышел за отведённое время: записывать всё равно нельзя,
        # микрофон ещё занят. Отдельным флагом, а не исключением `stop`:
        # причина тут другая и текст должен быть свой.
        не_дождались = False
        if былГолос:
            try:
                self.voice.stop()
                self._warm_stop()
            except Exception as exc:
                # Голос мог уже погаснуть — восстановление всё равно нужно.
                сбойСтопа = exc
            if сбойСтопа is None and not self._дождаться_остановки_голоса():
                не_дождались = True
        сбойГолоса = None
        сбойЗаписи = None
        ответ = None
        try:
            if сбойСтопа is None and not не_дождались:
                try:
                    ответ = self._audio_record_once(голос_остановлен=былГолос)
                except Exception as exc:
                    сбойЗаписи = exc
        finally:
            if былГолос:
                try:
                    self._поднять_голос_после_пробы()
                except Exception as exc:
                    сбойГолоса = exc
        if не_дождались:
            # Голос уходит дольше, чем можно ждать: запись не начиналась, и
            # хозяин должен знать, что повторить можно, а не что сломалось.
            вернулся = (сбойГолоса is None
                        and bool(getattr(self.voice, "running", False)))
            причина = (f"голос не остановился за {int(self.ПОЖДАТЬ_ГОЛОС)} секунд, "
                       "попробуй ещё раз")
            if сбойГолоса is not None:
                причина += (f"; и вернуть не вышло: {type(сбойГолоса).__name__}: "
                            f"{сбойГолоса}")
            return {"ok": False, "voice_restored": вернулся, "error": причина
                    + ("" if вернулся else
                       " — включи его сам, пультом или «Голос» в настройках")}
        if сбойСтопа is not None:
            текст = f"{type(сбойСтопа).__name__}: {сбойСтопа}"
            if сбойГолоса is not None:
                текст += f"; и вернуть не вышло: {type(сбойГолоса).__name__}: {сбойГолоса}"
            вернулся = (сбойГолоса is None
                        and bool(getattr(self.voice, "running", False)))
            return {"ok": False, "voice_restored": вернулся,
                    "error": f"проба не началась, голос не остановился: {текст}"
                             + ("" if вернулся else
                                " — включи его сам, пультом или «Голос» в настройках")}
        if сбойГолоса is not None:
            текст = f"{type(сбойГолоса).__name__}: {сбойГолоса}"
            # Даже при одновременной ошибке записи главная новость —
            # голос не вернулся. И о причине записи тоже скажем.
            запись = (f"Запись тоже сорвалась: {type(сбойЗаписи).__name__}: "
                       f"{сбойЗаписи}. " if сбойЗаписи is not None else "")
            return {"ok": False, "voice_restored": False,
                    "error": f"{запись}Голос не поднялся: {текст} — "
                             "включи его сам, пультом или «Голос» в настройках"}
        if сбойЗаписи is not None:
            raise сбойЗаписи
        if not ответ.get("ok"):
            # Проба сорвалась, а голос вернулся: отказ остаётся отказом,
            # просто «голос на месте» хозяину знать не обязательно.
            return ответ
        if былГолос and not getattr(self.voice, "running", False):
            return {"ok": False, "voice_restored": False,
                    "error": "проба прошла, но голос не поднялся — "
                             "включи его сам, пультом или «Голос» в настройках"}
        ответ["voice_restored"] = былГолос
        return ответ

    def _audio_record_once(self, голос_остановлен: bool = False) -> dict:
        """Одна запись с микрофона. Голосом не управляет.

        Играть запись здесь больше нельзя: она проигрывалась один раз, мимо
        хозяина, и проверить звук было нечем. Теперь запись просто лежит в
        памяти (`_проба_звука`), а слушает её отдельное нажатие
        (`audio_test_play`). Вместе с ней считаем, насколько микрофон вообще
        услышал: без этого по экрану не видно, что микрофон молчал.

        `голос_остановлен` — проба мастера уже погасила голос и дождалась конца
        его потока. Тогда «занято» значит ровно одно: поток всё ещё жив.
        Смотреть на `in_conversation` и голос колонок тут незачем — окно
        разговора к проверке микрофона отношения не имеет, а «выключи голос»
        мастеру сказать нельзя: он сейчас не в разговоре, он в настройках.
        """
        import numpy as np

        import config
        from core import selftest
        from core.audio_in import peak_level

        if (self._voice_busy() if not голос_остановлен else self._голос_поток_жив()):
            return {"ok": False,
                    "error": "выключи голос на время проверки — он держит микрофон"}
        try:
            _индекс, описание = config.find_input_device()
        except Exception as exc:
            return {"ok": False, "error": f"микрофон: {type(exc).__name__}: {exc}"}
        if _индекс is None:
            return {"ok": False, "error": "микрофон не найден — "
                                          "выбери его в настройках звука"}
        try:
            запись, частота = selftest.record(self.AUDIO_TEST_SECONDS)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not len(запись):
            return {"ok": False, "error": "микрофон молчит — проверь, включён ли он"}

        трек = np.asarray(запись, dtype=np.float32)
        # Запоминаем ровно то, что записали, с той же частотой: «Прослушать»
        # должен играть ту же запись, а не новую.
        self._проба_звука = (трек, int(частота))
        пик = float(np.abs(трек).max())
        # Уровень — самая громкая часть записи, а не средняя: три секунды с
        # паузами между словами иначе выглядели бы «тихо».
        уровень = peak_level(трек, частота)
        вердикт, текст = self._вердикт_записи(пик, уровень)
        return {"ok": True, "seconds": round(len(трек) / float(частота), 1),
                 "mic": описание, "peak": round(пик, 4),
                 "level": round(уровень, 3), "verdict": вердикт,
                 "verdict_text": текст}

    def _вердикт_записи(self, пик: float, уровень: float):
        """Что запись сказала о микрофоне: (код, текст для хозяина)."""
        if пик < self.ПИК_ТИШИНЫ:
            return ("silent", "Тишина — микрофон ничего не услышал. "
                              "Проверь, тот ли выбран и не выключен ли он")
        if уровень < self.УРОВЕНЬ_ТИХО:
            return ("quiet", "Тихо — говори ближе или громче")
        return ("ok", "Слышно хорошо")

    def audio_test_play(self) -> dict:
        """Проиграть последнюю запись микрофона в выбранные колонки.

        Отдельное нажатие, а не продолжение записи: проиграть можно сколько угодно
        и когда угодно, пока запись лежит в памяти, — ровно то, чего не хватало
        проверке, когда запись игралась сразу и один раз.

        Голосом не управляем: колонки он не держит монопольно, а гасить голос
        ради прослушивания — значит оставить хозяину молчащую трубу, если
        вкладка закроется. В телефон запись не отдаём никогда: там шлёт только
        синтезированная речь, и подмешивать чужой микрофон в её поток — верный
        способ сорвать разговор. Играем колонками и говорим об этом в ответе.
        """
        import numpy as np

        import config

        запись = getattr(self, "_проба_звука", None)
        if not запись:
            return {"ok": False, "error": "Сначала нажми «Записать 3 секунды»"}
        if not self._audio_play_lock.acquire(blocking=False):
            return {"ok": False, "error": "запись уже играет — подожди пару секунд"}
        try:
            трек, частота = запись
            индекс, описание = config.find_output_device()
            from core.audio_out import play_own

            # Своим потоком: общий `sd.play` 02.10 уронил пульт, наложившись
            # на замер полоски мастера (см. `audio_out.play_own`).
            play_own(np.asarray(трек, dtype=np.float32), int(частота), индекс)
        except Exception as exc:
            return {"ok": False, "error": "проиграть не вышло: "
                                          f"{type(exc).__name__}: {exc}"}
        finally:
            self._audio_play_lock.release()
        ответ = {"ok": True, "speaker": описание,
                 "seconds": round(len(трек) / float(частота), 1),
                 "played_in": "speakers"}
        if str(getattr(config, "OUTPUT", "speakers") or "speakers") == "phone":
            ответ["note"] = ("проверку послушано в колонках: запись с микрофона "
                              "в телефон не отправить")
        return ответ

    # --- Железо -------------------------------------------------------------

    # Определение занимает до пяти секунд (спросили nvidia-smi), а открытая
    # вкладка железа спрашивает его часто. Результат кешируем на минуту.
    HARDWARE_TTL = 60.0

    def hardware_info(self) -> dict:
        """Что есть на компьютере и что ему по силам."""
        from core import hardware

        now = time.monotonic()
        with self._lock:
            кеш = getattr(self, "_hw_cache", None)
            if кеш and now - кеш[0] < self.HARDWARE_TTL:
                return {"ok": True, **кеш[1]}
        hw = hardware.detect()
        ответ = {"hw": hw, "recommend": hardware.recommend(hw)}
        with self._lock:
            self._hw_cache = (now, ответ)
        return {"ok": True, **ответ}

    # --- Качественные голоса кнопкой ---------------------------------------
    #
    # Установка идёт минут двадцать, поэтому она фоновая, а страница спрашивает
    # `/status` и видит шаги. Устроено ровно как обновление с GitHub: тот же
    # `_bg`, тот же «уже идёт» при повторном нажатии.

    def voices_status(self) -> dict:
        from core import hardware, settings, voices_install

        проверка = voices_install.check()
        return {
            "ok": True,
            "running": bool(getattr(self, "_voices_running", False)),
            "steps": list(getattr(self, "_voices_steps", None) or []),
            "result": getattr(self, "_voices_result", None),
            "possible": bool(проверка.get("ok")),
            "why": "" if проверка.get("ok") else str(проверка.get("error") or ""),
            "reason": "" if проверка.get("ok") else str(проверка.get("reason") or ""),
            # `installed` — только библиотеки. Веса моделей качает пульт и
            # только по выбору хозяина, поэтому пульт обязан говорить о них
            # отдельно, иначе «модели скачаны» будет означать то, чего не было.
            "installed": bool(hardware.voices_installed()),
            "weights": voices_install.weights_installed(),
            "wanted": list(settings.voices_wanted()),
            # Ход скачивания: пульт спрашивает это по таймеру, а проценты шли
            # без событий, и панель стояла на «0,0 ГБ» (02.10).
            "download": self._download(),
            # Ход установки библиотек: та же полоса в пульте, только вместо
            # процентов — шаг («1 из 2») и сколько скачано на диск.
            "libs": self._libs(),
        }

    # --- Скачивание моделей качественных голосов ----------------------------

    def _download(self) -> dict:
        """Копия состояния загрузки — под замком, иначе пульт ловит половину.

        Замок и состояние заводятся здесь же, а не только в конструкторе: в
        тестах и в заглушках мастера runtime собирается без `__init__`, и
        спрашивание статуса не должно из-за этого падать.
        """
        with self._замок_скачивания():
            return dict(self._download_state,
                        queue=list(self._download_state["queue"]),
                        finished=list(self._download_state["finished"]))

    @contextmanager
    def _замок_скачивания(self):
        """Замок состояния загрузки, заводя его при первом обращении."""
        lock = getattr(self, "_download_lock", None)
        if lock is None:
            lock = self._download_lock = threading.Lock()
        lock.acquire()
        try:
            if getattr(self, "_download_state", None) is None:
                self._download_state = _пустое_скачивание()
            yield
        finally:
            lock.release()

    def voices_download(self, models=None) -> dict:
        """Скачать выбранные модели — по очереди, в фоне.

        Уже скачанные пропускаем, повторный запуск во время загрузки — отказ
        («уже качаю»), а место на диске проверяем до старта: обрыв в середине
        9 ГБ хозяину понравился бы меньше, чем честный отказ с цифрами.
        """
        from core import settings, voice_models

        выбрано = settings.validate_voices_wanted(models if models is not None
                                                  else settings.voices_wanted())
        if not выбрано:
            return {"ok": False, "error": "выбери модель: Higgs или ESpeech"}
        очередь = voice_models.needed(выбрано)
        if not очередь:
            return {"ok": True, "already": выбрано}
        with self._замок_скачивания():
            if self._download_state["active"]:
                return {"ok": False, "error": "уже качаю — дождись окончания"}
            место = voice_models.disk_check(очередь)
            if not место.get("ok"):
                return {"ok": False, "error": место["error"]}
            self._download_stop = threading.Event()
            self._download_state = _пустое_скачивание()
            self._download_state.update({
                "active": True, "model": очередь[0],
                "title": voice_models.title(очередь[0]),
                "total": voice_models.size_bytes(очередь[0]),
                "queue": очередь[1:],
            })
            стоп = self._download_stop
        # Отдельным потоком: скачивание идёт минутами, а страница спрашивает
        # состояние по таймеру и не должна висеть.
        threading.Thread(target=self._качать_модели, args=(очередь, стоп),
                         daemon=True).start()
        self._remember("voices_download",
                       f"качаю {', '.join(voice_models.title(и) for и in очередь)}")
        return {"ok": True, "queue": очередь}

    def _голос_после_скачивания(self, имя: str) -> None:
        """Скачалась модель выбранного голоса — голос включается сам.

        Только если он и так должен был включиться при запуске пульта
        (`voice_autostart`) и сейчас выключен: при запуске он не поднялся
        ровно потому, что модели ещё не было. Выключенный хозяином голос с
        выключенным автостартом не трогаем.
        """
        import config

        try:
            if str(getattr(config, "TTS_ENGINE", "")) != имя:
                return
            if not getattr(config, "VOICE_AUTOSTART", False):
                return
            if getattr(self.voice, "running", False):
                return
            self.voice_autostart()
        except Exception as exc:
            self._remember("error", f"голос после скачивания не включился: {exc}")

    def voices_download_cancel(self) -> dict:
        """Остановить скачивание. Процесс гасится вместе с потомками."""
        стоп = getattr(self, "_download_stop", None)
        if стоп is not None:
            стоп.set()
        return {"ok": True}

    def _качать_модели(self, очередь: list, стоп: threading.Event) -> None:
        """Фоновая загрузка: по очереди, с отменой и честной ошибкой."""
        from core import voice_models

        for имя in очередь:
            if стоп.is_set():
                break
            всего = voice_models.size_bytes(имя)
            голос = voice_models.title(имя)
            with self._замок_скачивания():
                self._download_state.update({"model": имя, "title": голос,
                                             "done": 0, "total": всего})
            voice_models.set_progress(имя, 0, всего)
            последний = [-1]

            def показать(байт: int, _имя=имя, _всего=всего, _голос=голос,
                         _последний=последний) -> None:
                с = voice_models.процент(байт, _всего)
                with self._замок_скачивания():
                    self._download_state["done"] = int(байт)
                voice_models.set_progress(_имя, байт, _всего)
                # В журнал — раз в 5 %: панель спрашивает состояние сама, а
                # журнал не должен забиваться сотнями одинаковых строк.
                if с // 5 != _последний[0]:
                    _последний[0] = с // 5
                    self._remember(
                        "voices_download",
                        f"{_голос}: {voice_models.гб(байт)} из "
                        f"~{voice_models.size_text(_имя)} ({с} %)")

            try:
                сошлось = voice_models.download(имя, стоп, показать)
            except Exception as exc:
                voice_models.clear_progress(имя)
                with self._замок_скачивания():
                    self._download_state["error"] = f"{type(exc).__name__}: {exc}"
                self._remember("voices_download_failed", {"error": str(exc)})
                break
            voice_models.clear_progress(имя)
            if not сошлось:
                # Отменили: тишина, а не ошибка, и очередь остаётся на месте —
                # в следующий раз докачается с того же места.
                self._remember("voices_download", f"{голос}: остановил")
                break
            with self._замок_скачивания():
                self._download_state["finished"].append(имя)
                self._download_state["done"] = всего
            self._remember("voices_download", f"{голос} скачана")
            self._голос_после_скачивания(имя)
        with self._замок_скачивания():
            self._download_state.update({"active": False, "model": "",
                                         "title": "", "done": 0, "queue": []})

    # --- Установка библиотек качественных голосов --------------------------
    #
    # Ставит `core/voices_install.install` — тот же uv и те же команды, что у
    # установщика, но отдельными процессами без окна и с выводом в
    # `data/voices_install.log`. Отличие от скачивания моделей одно и главное:
    # библиотеки ставит сам пульт, а не хозяин в чёрном окне (02.10).

    def _libs(self) -> dict:
        """Копия состояния установки — под замком, иначе полоса ловит половину."""
        with self._замок_библиотек():
            return dict(self._libs_state)

    @contextmanager
    def _замок_библиотек(self):
        """Замок установки, заводя его при первом обращении — как у загрузки."""
        lock = getattr(self, "_libs_lock", None)
        if lock is None:
            lock = self._libs_lock = threading.Lock()
        lock.acquire()
        try:
            if getattr(self, "_libs_state", None) is None:
                self._libs_state = _пустое_библиотеки()
            yield
        finally:
            lock.release()

    def voices_libs_cancel(self) -> dict:
        """Остановить установку библиотек. Процесс гасится вместе с потомками."""
        стоп = getattr(self, "_libs_stop", None)
        if стоп is not None:
            стоп.set()
        return {"ok": True}

    def voices_libs_start(self, models=None) -> dict:
        """Поставить библиотеки в фоне — оттуда же, где работает пульт.

        Так можно ровно тогда, когда torch ещё не загружен в этом процессе:
        пока его файлы не открыты, Windows даёт их заменить. Если голос уже
        работал — torch в памяти, и пульт один раз перезапускается «без
        голоса», а установку начинает уже он (см. `voices_install`).
        """
        from core import settings

        with self._замок_библиотек():
            if self._libs_state["active"]:
                return {"ok": False, "error": "библиотеки уже ставятся — "
                                             "дождись окончания"}
            self._libs_stop = threading.Event()
            self._libs_state = _пустое_библиотеки()
            self._libs_state["active"] = True
            стоп = self._libs_stop
        self._bg(self._ставить_библиотеки, стоп,
                 list(models or settings.voices_wanted()))
        return {"ok": True, "started": True}

    def _ставить_библиотеки(self, стоп: threading.Event, модели: list) -> None:
        """Фоновая установка: два шага, с честной ошибкой и тихим отказом."""
        import config
        from core import settings, voices_install

        def шаг(**п) -> None:
            with self._замок_библиотек():
                if п.get("step"):
                    self._libs_state.update({
                        "step": int(п.get("step") or 0),
                        "steps": int(п.get("steps") or 2),
                        "title": str(п.get("title") or "")})
                текст = str(п.get("text") or "")
            if текст:
                self._remember("voices_step", текст)

        def прогресс(байт) -> None:
            with self._замок_библиотек():
                self._libs_state["downloaded"] = max(0, int(байт))

        try:
            итог = voices_install.install(on_step=шаг, on_progress=прогресс,
                                          stop=стоп)
        except Exception as exc:
            итог = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        with self._замок_библиотек():
            self._libs_state["active"] = False
            self._libs_state["done"] = bool(итог.get("ok"))
            # Отменённый шаг — не ошибка: хозяин сам нажал «Отменить», и красная
            # строка сбила бы его с толку.
            self._libs_state["error"] = ("" if итог.get("cancelled")
                                         else str(итог.get("error") or ""))
        if итог.get("cancelled"):
            self._remember("voices_step", "остановил установку библиотек")
            return
        if not итог.get("ok"):
            self._remember("voices_failed", итог)
            return

        self._remember("voices_step",
                       "библиотеки качественных голосов поставлены")
        # Дальше — то, ради чего хозяин и нажимал кнопку: скачать выбранные
        # модели. Голос после этого включится сам (`_голос_после_скачивания`).
        выбрано = list(модели) or list(settings.voices_wanted())
        if выбрано:
            итог_качки = self.voices_download(выбрано)
            if not итог_качки.get("ok"):
                self._remember("voices_failed", итог_качки)
        # Пульт мог подняться «без голоса» ради этой установки (перезапуск из
        # `voices_install`). Если хозяин так и оставил обычный Silero, включаем
        # его как обычно: иначе голос молчал бы до следующего перезапуска.
        if str(getattr(config, "TTS_ENGINE", "")) == "silero":
            self.voice_autostart()

    def voices_libs_startup(self) -> bool:
        """Продолжить установку после перезапуска пульта.

        Пульт, который не смог поставить torch сам (тот был в памяти), оставил
        флаг и перезапустился. Новый пульт читает флаг, убирает его и ставит
        библиотеки сам: голос в этот раз он не запускал, поэтому torch в
        процессе нет и его файлы можно менять. Началось ли что-то — `True`:
        `ui/window.py` по этому знает, что голос в этот раз не включать.
        """
        from core import voices_install

        флаг = voices_install.read_pending()
        if not флаг:
            return False
        voices_install.clear_pending()
        self._remember("voices_step", "ставлю библиотеки после перезапуска")
        if not voices_install.uv_есть():
            # uv в папке не оказалось, а окно установщика мы только что закрыли
            # вместе с пультом. Честно говорим и ждём кнопки хозяина.
            отказ = {"ok": False, "error": "в папке Трубы нет uv — библиотеки "
                                           "придётся поставить установщиком"}
            with self._замок_библиотек():
                self._libs_state["error"] = отказ["error"]
            self._remember("voices_failed", отказ)
            return True
        self.voices_libs_start(list(флаг.get("models") or []))
        return True

    def voices_install(self, models=None) -> dict:
        """Кнопка «Установить качественные голоса».

        Выбор хозяина запоминается (`voices_wanted`), и дальше по обстановке:
        библиотеки уже стоят → качаем модели прямо здесь, пульт не закрываем
        (хозяин нажал «установить» и ждёт голоса, а не окно установщика);
        не стоят → ставим их тут же, в фоне, с полосой и отменой. Прямо отсюда
        ставить можно, пока torch не загружен в процессе и `uv` нашёлся в папке;
        иначе — перезапуск пульта «без голоса» и установка уже в нём, а если нет
        и `uv`, остаётся старый путь с отдельным окном установщика
        (`core/voices_install.launch_external`).
        """
        from core import hardware, settings, voices_install

        выбрано = settings.save_voices_wanted(
            models if models is not None else settings.voices_wanted())
        if not выбрано:
            return {"ok": False, "error": "выбери модель: Higgs или ESpeech"}
        if hardware.voices_installed():
            # Библиотеки на месте — установщик не нужен, качаем сами.
            итог = self.voices_download(выбрано)
            if not итог.get("ok"):
                self._remember("voices_failed", итог)
            return итог

        if not voices_install.uv_есть():
            # Ставить нечем: отдаём хозяину прежнее окно установщика.
            итог = voices_install.launch_external()
            if not итог.get("ok"):
                self._remember("voices_failed", итог)
                return итог
            self._remember("voices_step", "открыла установщик — пульт "
                                          "закрывается, после установки "
                                          "откроется сам")
            # Ответ странице — раньше, чем окно исчезнет.
            threading.Timer(1.5, self.close_pult).start()
            return итог

        if voices_install.torch_в_процессе():
            # torch уже в памяти (голос работал) — его файлы не дадут менять.
            # Оставляем флаг и перезапускаем пульт: новый пульт стартует без
            # голоса, и поставить библиотеки сможет уже он.
            if not voices_install.write_pending(выбрано):
                return {"ok": False, "error": "не получилось запомнить, что "
                                              "поставить — попробуй ещё раз"}
            self._remember("voices_step", "перезапускаю пульт без голоса — "
                                          "потом поставлю библиотеки")
            перезапуск = self.update_restart()
            if not перезапуск.get("ok"):
                voices_install.clear_pending()
                return перезапуск
            # Ответ странице уходит раньше, чем окно исчезнет (таймер внутри
            # `update_restart`), и хозяин видит, что это не ошибка.
            return {"ok": True, "restart": True}

        итог = self.voices_libs_start(выбрано)
        if not итог.get("ok"):
            self._remember("voices_failed", итог)
        return итог

    # --- Погода: поиск города и QR телефона --------------------------------

    def _обновить_погоду(self) -> None:
        """Пересобирает погоду после смены города и раздаёт её телефонам.

        Устроено как периодический цикл `core/weather.py::Watcher`, только
        по поводу смены города: сам по себе он всплыл бы только через полчаса,
        и всё это время на телефоне висел бы прогноз того места, которое
        хозяин уже поменял. Город убран — телефону уходит «погоды нет».
        """
        from core import settings, weather

        сообщение = weather.refresh() if settings.weather_ready() else None
        try:
            if сообщение is not None:
                self.server.send_weather(сообщение)
            else:
                # Город сняли — или новый город сеть не отдала: плашку
                # прячем. Старый прогноз на экране — это прогноз не того
                # места, хуже, чем никакого. Появится со следующим заходом.
                self.server.send_weather({"off": True})
        except Exception:
            pass

    def weather_find(self, query: str) -> dict:
        """Города по названию. Сети нет — пустой список, а не ошибка 500."""
        from core import weather

        return {"ok": True, "places": weather.find_places(query)}

    def phone_qr(self, url: str):
        """QR-код адреса телефона для мастера первого запуска.

        Только `http://` на адрес из `local_addresses()`: маршрут рисует адрес
        на экране телефона, и генератор QR для произвольной ссылки здесь был бы
        способом отправить человека на чужой сайт от имени Трубы. Адрес
        проверяем до импорта segno, чтобы чужой адрес отбивался одинаково
        независимо от того, стоит ли библиотека.
        """
        from urllib.parse import parse_qs, urlsplit

        import config
        from core.phone import local_addresses, phone_key

        # Порт — тот, на котором работает *этот* пульт, а не константа из
        # config: у второй копии Трубы он свой, и адрес телефона в QR должен
        # вести именно к ней.
        allowed = {f"http://{ip}:{self._порт()}" for ip in local_addresses()}
        части = urlsplit(str(url or "").strip())
        основа = f"{части.scheme}://{части.netloc}"
        # С 29.09 в ссылке ещё ключ привязки телефона (`/?k=…`) — и ничего,
        # кроме него: ни другого пути, ни чужих параметров.
        запрос = parse_qs(части.query, keep_blank_values=True)
        ключ_свой = not части.query or (set(запрос) == {"k"} and запрос["k"] == [phone_key()])
        if (основа not in allowed or части.path not in ("", "/") or части.fragment
                or not ключ_свой):
            raise ValueError("это не адрес этого компьютера в локальной сети")

        import segno
        from starlette.responses import Response

        # Цвета и поля — у save(), а не у make(): make их не знает и падает.
        qr = segno.make(str(url or "").strip())
        буфер = io.BytesIO()
        qr.save(буфер, kind="svg", scale=4, dark="#000000", light=None, border=2)
        return Response(
            content=буфер.getvalue(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    # --- О программе и обновление с GitHub ------------------------------

    def about(self) -> dict:
        """Версия, автор и репозиторий — для раздела «О программе»."""
        import config

        return {
            "version": config.VERSION,
            "repo": config.UPDATE_REPO,
            "author": config.AUTHOR,
            "update_check": bool(getattr(config, "UPDATE_CHECK", True)),
            "checked": self.update_state(),
        }

    def update_state(self) -> dict:
        """Последняя проверка, шаги установки и её итог."""
        with self._update_lock:
            return {
                "last": self._update_check,
                "running": self._update_running,
                "steps": list(self._update_steps),
                "result": self._update_result,
            }

    def update_check(self) -> dict:
        """Спросить GitHub о новой версии. Результат запоминается."""
        from core import updater

        self._remember("update_check", "спрашиваю GitHub…")
        return self._remember_check(updater.check())

    def _remember_check(self, итог: dict, quiet: bool = False) -> dict:
        """Запомнить результат проверки и сказать о нём в журнал.

        `quiet` — проверка при старте пульта: в журнал только «вышла новая
        версия», иначе каждый запуск писал бы «репозиторий ещё не
        опубликован».
        """
        with self._update_lock:
            self._update_check = итог
        if not quiet:
            self._remember("update_check", _update_что(итог))
        if итог.get("state") == "newer":
            self._remember("update_available", итог.get("latest", ""))
        # Прочие состояния в журнал не пишем: новой версии нет — и делать
        # хозяину нечего.
        return итог

    def update_install(self) -> dict:
        """Поставить найденный выпуск. Идёт в фоне: страница не должна
        висеть, пока качается архив."""
        from core import updater

        with self._update_lock:
            if self._update_running:
                return {"ok": False, "error": "обновление уже идёт"}
            выпуск = self._update_check
            if not выпуск or выпуск.get("state") != "newer" \
                    or not выпуск.get("zip"):
                return {"ok": False, "error": "сначала проверь обновления"}
            self._update_running = True
            self._update_steps.clear()
            self._update_result = None

        def шаг(текст: str) -> None:
            with self._update_lock:
                self._update_steps.append(str(текст)[:200])
            self._remember("update_step", текст)

        def бежать() -> None:
            try:
                итог = updater.install(выпуск, on_step=шаг)
            except Exception as exc:
                итог = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            with self._update_lock:
                self._update_running = False
                self._update_result = итог
            if итог.get("ok"):
                self._remember("updated", итог)
            else:
                self._remember("update_failed", итог)

        self._bg(бежать)
        return {"ok": True, "started": True}

    def update_restart(self) -> dict:
        """Перезапустить пульт: сначала отложенный запуск, потом закрыть."""
        from core import updater

        if not updater.restart_later():
            return {"ok": False, "error": "перезапуск не получился"}
        self._remember("update_step", "перезапускаю пульт")
        # Закрываем из своего потока: ответ странице надо отдать раньше, чем
        # окно исчезнет, иначе пульт покажет хозяину ошибку вместо успеха.
        threading.Timer(1.0, self.close_pult).start()
        return {"ok": True, "restart": True}

    # --- Перенос настроек: экспорт, разбор и применение --------------------
    #
    # Всё в `core/transfer.py`, а здесь только то, что знает пульт: файл,
    # принесённый хозяином, лежит в памяти (не на диске) и ждёт применения.
    # Токен — чтобы файл применился тот, который хозяин только что выбрал, а не
    # тот, что остался от прошлого раза.

    def transfer_parts(self) -> dict:
        """Список частей с подписью и тем, что есть на диске сейчас."""
        from core import transfer

        return {"ok": True, "parts": transfer.части()}

    def transfer_export(self, parts) -> dict:
        from core import transfer

        итог = transfer.export(parts)
        self._remember("transfer_export", {"parts": итог.get("parts", [])})
        self.log_message(f"перенос: {итог['path']}")
        return итог

    def transfer_reveal(self, path: str) -> dict:
        from core import transfer

        return transfer.reveal(path)

    def transfer_inspect(self, blob: bytes) -> dict:
        """Разобрать принесённый файл и запомнить его до применения."""
        from core import transfer

        разбор = transfer.inspect(blob)
        with self._transfer_lock:
            self._transfer_blob = bytes(blob)
            self._transfer_token = uuid.uuid4().hex
        return {**разбор, "token": self._transfer_token}

    def transfer_apply(self, token: str, parts) -> dict:
        """Применить файл, который хозяин только что разобрал."""
        from core import transfer

        with self._transfer_lock:
            blob = self._transfer_blob
            if not blob or str(token) != self._transfer_token:
                raise ValueError("этот файл переноса не помню — выбери его заново")
        итог = transfer.apply(blob, parts)
        with self._transfer_lock:
            self._transfer_blob = None
            self._transfer_token = ""
        self._remember("transfer_apply", {"parts": итог.get("parts", [])})
        self.log_message("перенос применён: " + ", ".join(итог.get("parts", [])))
        return итог

    def close_pult(self) -> None:
        """Закрыть пульт штатно, как по крестику.

        Само окно знает `ui/window.py`: с треем крестик прячет окно, а
        выход — гасит. Здесь мы зовём именно то, чем он гасит.
        """
        if callable(self._pult_close):
            try:
                self._pult_close()
            except Exception:
                pass

    def _available_voices(self) -> list:
        import config

        out = []
        try:
            from core import voice_prep

            items = voice_prep.available()
            for it in items or []:
                if isinstance(it, dict) and it.get("name"):
                    out.append(it["name"])
                elif isinstance(it, str):
                    out.append(it)
        except Exception:
            pass
        if out:
            return sorted(set(out))
        try:
            folder = config.ROOT / "voice"
            if folder.is_dir():
                for wav in sorted(folder.glob("*.wav")):
                    if (folder / (wav.stem + ".txt")).exists():
                        out.append(wav.stem)
        except Exception:
            pass
        return sorted(set(out))

    def voice_samples(self) -> list[dict]:
        from core import voice_prep

        return [
            {"name": name, "text": voice_prep.read_text(name),
             "seconds": round(voice_prep.duration(name), 1)}
            for name in voice_prep.available()
        ]

    def voice_sample_path(self, name: str):
        from core import voice_prep

        if name not in voice_prep.available():
            raise ValueError("Такого образца голоса нет")
        return voice_prep.voices_dir() / f"{name}.wav"

    def voice_sample_add(self, source, start=None, seconds=None) -> dict:
        import math

        from core import voice_prep

        if self._voice_busy():
            raise ValueError("Выключи голос перед добавлением образца")
        if start is not None:
            start = float(start)
            seconds = float(seconds)
            if not math.isfinite(start) or not math.isfinite(seconds) or \
               start < 0 or not 0 < seconds <= voice_prep.MAX_SECONDS:
                raise ValueError("Начало должно быть от нуля, длина — от 0 до 12 секунд")
        name = "".join(c for c in source.stem if c.isalnum() or c in "-_").strip("-_")
        if not name:
            raise ValueError("Дай записи имя из букв или цифр")
        original = name
        index = 2
        while (voice_prep.voices_dir() / f"{name}.wav").exists() or \
              (voice_prep.voices_dir() / f"{name}.txt").exists():
            name = f"{original}-{index}"
            index += 1
        how = (f"вручную с {start:g} с, длина {seconds:g} с" if start is not None
               else "кусок найден сам")
        try:
            name, text = voice_prep.prepare(source, name=name, start=start,
                                            seconds=seconds or voice_prep.MAX_SECONDS)
        except Exception as exc:
            # prepare может успеть записать WAV до ошибки распознавания.
            voice_prep.delete(name)
            self.log_message(f"образец голоса не добавлен ({source.name}, {how}): {exc}")
            raise
        length = round(voice_prep.duration(name), 1)
        self.log_message(f"добавлен образец голоса {name}: {length} с, {how}")
        return {"name": name, "text": text, "seconds": length}

    def voice_sample_delete(self, name: str) -> dict:
        from core import settings, voice_prep

        if self._voice_busy():
            raise ValueError("Выключи голос перед удалением образца")
        names = voice_prep.available()
        if name not in names:
            raise ValueError("Такого образца голоса нет")
        left = [item for item in names if item != name]
        if not left:
            raise ValueError("Это последний голос — сначала добавь другой")
        selected = settings.load_settings().get("voice_name")
        if selected == name:
            settings.save_settings({"voice_name": left[0]})
            settings.apply_to_config()
        voice_prep.delete(name)
        return {"selected": left[0] if selected == name else selected,
                "samples": self.voice_samples()}

    def _voice_busy(self) -> bool:
        try:
            if bool(getattr(self.voice, "running", False)):
                return True
        except Exception:
            pass
        try:
            if not self.voice._speaker_idle():
                return True
        except Exception:
            pass
        try:
            if bool(self.voice.in_conversation):
                return True
        except Exception:
            pass
        return False

    def save_settings(self, payload: dict) -> dict:
        import config
        from core import autostart, memory, proactive, settings

        payload = payload if isinstance(payload, dict) else {}
        errors = []
        to_save: dict = {}
        nums = [("temperature", 0.0, 2.0, False), ("max_tokens", 16, 8000, True),
                ("history_turns", 0, 100, True), ("tts_speed", 0.5, 2.0, False),
                ("tts_nfe", 4, 64, True), ("tts_gap", 0.0, 2.0, False),
                ("duck_level", 0.0, 1.0, False), ("barge_in_level", 0.01, 1.0, False),
                ("owner_threshold", 0.0, 1.0, False),
                ("voice_volume", config.VOICE_VOLUME_MIN, config.VOICE_VOLUME_MAX, True),
                ("follow_up_window", 0.0, 3600.0, False),
                ("web_search_budget", 4.0, 60.0, False)]
        for key, low, high, integer in nums:
            if key not in payload:
                continue
            try:
                val = int(payload[key]) if integer else float(payload[key])
            except (TypeError, ValueError):
                errors.append(f"{key}: нужно число")
                continue
            if integer and isinstance(payload[key], float) and payload[key] != val:
                # 7.5 — не целое, а молча превращать его в 7 значило бы
                # сохранить не то, что задали: хозяин потом удивлялся бы,
                # откуда взялось семь. Ползунок в пульте таких значений и не
                # присылает, а вот ручной запрос — может.
                errors.append(f"{key}: нужно целое число")
                continue
            if not (low <= val <= high):
                errors.append(f"{key}: держись в пределах {low}…{high}")
                continue
            to_save[key] = val
        # Шаг мастера первого запуска. Строго целое 1…6: строкой «5», десятичной
        # 5.5 или bool это не номер шага, а опечатка — такой запрос отвергаем
        # целиком, как просил бы любой другой неверный ключ.
        if "wizard_step" in payload:
            значение = payload["wizard_step"]
            if isinstance(значение, bool) or not isinstance(значение, int) \
                    or not (settings.WIZARD_STEP_MIN <= значение
                            <= settings.WIZARD_STEP_MAX):
                errors.append("wizard_step: целое число от 1 до 6")
            else:
                to_save["wizard_step"] = значение
        choices = {"tts_engine": ("silero", "espeech", "higgs"),
                   "listen_mode": ("always", "name", "off"),
                   "web_search_mode": ("free", "paid", "auto"),
                   "proactive": tuple(proactive.FREQUENCIES),
                   "output": ("speakers", "phone")}
        for key, variants in choices.items():
            if key in payload:
                if payload[key] not in variants:
                    errors.append(f"{key}: так нельзя")
                else:
                    to_save[key] = payload[key]
        # Тема оформления. Проверка мягкая, как у сетки телефона: чужое
        # значение — это тёмная тема, а не отказ сохранять остальное.
        if "theme" in payload:
            to_save["theme"] = settings.validate_theme(payload["theme"])
        # Последняя включённая частота пишется тут же, куда и сам `proactive`:
        # Панели нужно знать, к какой частоте возвращать галочку. «Никогда» её
        # не пишет — включать обратно нечем, и прошлая частота остаётся.
        # Из запроса `proactive_last` не берём: хозяин это поле не видит.
        if to_save.get("proactive", "never") != "never":
            to_save["proactive_last"] = to_save["proactive"]
        for key in ("silero_speaker", "silero_model"):
            if key in payload:
                val = payload[key]
                if not isinstance(val, str) or not val.strip() or len(val) > 64:
                    errors.append(f"{key}: короткое имя строкой")
                else:
                    to_save[key] = val.strip()
        # Образец голоса — исключение: пустым он бывает законно («образца ещё
        # нет»), и это не причина отвергать весь запрос. Иначе выбор Higgs или
        # ESpeech без готового образца не сохранялся бы вовсе, хозяин уходил
        # со старым Silero и не понимал почему (29.09, отзыв о 0.9.6).
        if "voice_name" in payload:
            val = payload["voice_name"]
            if not isinstance(val, str) or len(val.strip()) > 64:
                errors.append("voice_name: короткое имя строкой")
            else:
                to_save["voice_name"] = val.strip()
        for key in ("require_name_when_noisy", "voice_app_guard", "owner_only",
                    "barge_instant", "web_search", "search_sound", "replay_guard",
                    "voice_autostart", "higgs_gentle", "proactive_look", "hedge",
                    "update_check", "first_run_done", "offer_analysis_note"):
            if key in payload:
                if not isinstance(payload[key], bool):
                    errors.append(f"{key}: нужно true/false")
                else:
                    to_save[key] = payload[key]
        # Сетка программ на телефоне. Проверки мягкие: кривое значение — это
        # значение по умолчанию, а не отказ сохранять остальное.
        if "phone_cols" in payload:
            to_save["phone_cols"] = settings.validate_phone_cols(payload["phone_cols"])
        if "phone_rows" in payload:
            to_save["phone_rows"] = settings.validate_phone_rows(payload["phone_rows"])
        if "phone_icon_style" in payload:
            to_save["phone_icon_style"] = settings.validate_phone_icon_style(
                payload["phone_icon_style"])
        if "phone_labels" in payload:
            to_save["phone_labels"] = settings.validate_phone_labels(
                payload["phone_labels"])
        # Нижние кнопки телефона. Проверка мягкая, как у сетки: кривая ячейка
        # становится ячейкой по умолчанию на своём месте, а не отказом.
        if "phone_actions" in payload:
            to_save["phone_actions"] = settings.validate_phone_actions(
                payload["phone_actions"])
        if "persona_preset" in payload:
            # Незнакомый id — фирменный характер, а не отказ сохранять.
            from core import personas

            to_save["persona_preset"] = personas.valid(payload["persona_preset"])
        # Модель распознавания и её сжатие. Кривое значение — значение по
        # умолчанию: опечатка в названии модели не должна оставить голос без
        # слуха, и сохранять остальное она тоже не должна мешать.
        if "stt_model" in payload:
            to_save["stt_model"] = settings.validate_stt_model(payload["stt_model"])
        if "stt_quantization" in payload:
            to_save["stt_quantization"] = settings.validate_stt_quantization(
                payload["stt_quantization"])
        # Звуковые устройства. Проверки мягкие: чужой микрофон не должен
        # оставлять человека без голоса из-за лишнего пробела в имени.
        for key in ("mic_name", "speaker_name", "weather_city"):
            if key in payload:
                to_save[key] = settings.validate_device_name(payload[key])
        if "mic_channel" in payload:
            to_save["mic_channel"] = settings.validate_mic_channel(
                payload["mic_channel"])
        # Широта и долгота проверяются по-своему: у Владивостока долгота
        # 131.9, и общая проверка на ±90 тихо превращала её в «мусор».
        if "weather_lat" in payload:
            to_save["weather_lat"] = settings.validate_lat(payload["weather_lat"])
        if "weather_lon" in payload:
            to_save["weather_lon"] = settings.validate_lon(payload["weather_lon"])
        provider = payload.get("provider", None)
        api_key = payload.get("api_key", None)
        if provider is not None:
            import config

            if provider not in config.PROVIDERS:
                errors.append("provider: нет такого провайдера")
                provider = None
        selected_model = None
        # Пустая модель у локального сервера — своя ошибка: список моделей
        # берётся у самого сервера, и хозяину надо сказать, что делать,
        # а не ругаться на формат. Облаку пустая модель не подходит вовсе.
        локальный = provider is not None and settings.is_local(provider)
        пустая_модель = not str(payload.get("model") or "").strip()
        if локальный and "model" in payload and пустая_модель:
            errors.append("model: впиши модель или обнови список")
        elif "model" in payload:
            try:
                selected_model = _validated_model(provider, payload["model"])
            except ValueError as exc:
                errors.append(f"model: {exc}")
        # Адрес локального сервера. Проверяем в момент сохранения, чтобы
        # ошибка встала у поля, а не в общий список ошибок в конце.
        if "local_url" in payload:
            try:
                to_save["local_url"] = settings.validate_local_url(payload["local_url"])
            except ValueError as exc:
                errors.append(f"local_url: {exc}")
        if локальный and not (to_save.get("local_url")
                              or str(settings.load_settings().get("local_url") or "").strip()):
            errors.append("local_url: впиши адрес локального сервера")
        if api_key is not None and provider is None:
            errors.append("api_key: укажи и provider")
        for key in ("persona", "memory"):
            if key in payload and (
                not isinstance(payload[key], str) or len(payload[key]) > 20000
            ):
                errors.append(f"{key}: текст до 20000")
        if "clear_history" in payload and not isinstance(payload["clear_history"], bool):
            errors.append("clear_history: нужно true/false")
        # Автозапуск в settings.json не пишется: у него своё место — ярлык в
        # папке автозагрузки. Здесь только проверка типа, применение ниже.
        if "autostart" in payload and not isinstance(payload["autostart"], bool):
            errors.append("autostart: нужно true/false")
        # Папка заметок. Пусто — «Документы\Заметки Трубы», это сам notes.py
        # разберётся. Непустая обязана быть абсолютным путём: относительный
        # путь зависит от того, откуда запущен пульт, и заметки уехали бы
        # в папку проекта, а хозяин бы их там не нашёл.
        if "notes_dir" in payload:
            value = payload["notes_dir"]
            if not isinstance(value, str):
                errors.append("notes_dir: строкой")
            elif value.strip() and not _abs_path(value.strip()):
                errors.append("notes_dir: нужен полный путь, например D:\\Заметки")
            else:
                to_save["notes_dir"] = value.strip()
        if errors:
            return {"ok": False, "errors": errors}
        old_provider = settings.get_provider()
        old_settings = settings.load_settings()
        changed_settings = {
            key: value for key, value in to_save.items()
            if old_settings.get(key) != value
        }
        if selected_model is not None and selected_model != old_settings["models"].get(provider):
            updated_models = dict(old_settings["models"])
            updated_models[provider] = selected_model
            changed_settings["models"] = updated_models
        persona_changed = (
            "persona" in payload and payload["persona"] != settings.load_persona()
        )
        if api_key is not None and provider is not None:
            if not isinstance(api_key, str):
                return {"ok": False, "errors": ["api_key: строкой"]}
            if api_key.strip():
                settings.set_api_key(provider, api_key.strip())
        if provider is not None and provider != old_provider:
            settings.set_provider(provider)
        if changed_settings:
            settings.save_settings(changed_settings)
            settings.apply_to_config()
            if "listen_mode" in changed_settings:
                if changed_settings["listen_mode"] == "off":
                    self.voice._close_conversation()
                self.server.send_mode(changed_settings["listen_mode"])
            if "voice_volume" in changed_settings:
                # Телефоны показывают тот же уровень: регулятор на телефоне и
                # ползунок в пульте — одно и то же значение.
                self.server.send_volume(int(changed_settings["voice_volume"]))
            if "theme" in changed_settings:
                # Телефоны перекрашиваются сразу: переключателя темы на
                # телефоне нет, сообщение отсюда — единственный способ узнать
                # о смене, не перезагружая страницу.
                self.server.send_theme(changed_settings["theme"])
            if GRID_KEYS & set(changed_settings):
                # Вид сетки телефон рисует по сообщению со списком, а сам
                # список не менялся — значит, список надо переслать заново.
                self._bg(self._send_apps)
            if "phone_actions" in changed_settings:
                # Нижние кнопки телефон тоже рисует по сообщению, поэтому
                # смена в пульте должна быть видна сразу, без перезагрузки
                # страницы телефона.
                self._bg(self._send_actions)
            if STT_KEYS & set(changed_settings):
                # Голос сам разберётся, что он выключен, и промолчит: при
                # включении модель и так встанет из config.
                self._bg(self.voice.reload_stt)
            if WEATHER_KEYS & set(changed_settings):
                # Город сменили — телефон не должен ещё полчаса показывать
                # старый прогноз, а снятый город — совсем его.
                self._bg(self._обновить_погоду)
        if persona_changed:
            settings.save_persona(payload["persona"])
        if "memory" in payload:
            # `memory_base` — какой память была, когда страницу открыли: факты,
            # дописанные разбором разговора после этого, правка не сотрёт.
            base = payload.get("memory_base")
            memory.from_text(payload["memory"], base if isinstance(base, str) else None)
        if payload.get("clear_history"):
            if self.brain is not None:
                self.brain.forget()
            else:
                memory.clear_history()
        rebuild_brain = (
            (provider is not None and provider != old_provider)
            or (isinstance(api_key, str) and bool(api_key.strip()))
            or persona_changed
            or "history_turns" in changed_settings
            or "models" in changed_settings
            # Адрес локального сервера — часть того, куда ходит мозг:
            # сменили адрес, значит нужен новый клиент со старым адресом.
            or "local_url" in changed_settings
        )
        if rebuild_brain:
            try:
                from core.brain import Brain

                current = settings.get_provider()
                # Прогрев старого мозга гасим: иначе он так и стучался бы к
                # прежнему провайдеру, а новый остался бы холодным.
                self._warm_stop()
                # Ключ берём через settings.key_for: локальному серверу он
                # не нужен, и мозг без ключа у него — обычное дело.
                self.brain = Brain(provider=current) if settings.key_for(current) else None
                self.voice._brain = self.brain
                self._wire_brain()
                if getattr(self.voice, "running", False):
                    self._warm_start()
            except Exception as exc:
                return {"ok": False, "errors": [
                    f"мозг: {type(exc).__name__}: {exc}"
                ]}
        audio_keys = {"tts_engine", "silero_speaker", "silero_model",
                      "voice_name", "tts_speed", "tts_nfe", "tts_gap",
                      "output", "mic_name", "mic_channel", "speaker_name"}
        # Скорость и качество синтез берёт из config на каждой фразе, образец
        # меняем на ходу. Остальное требует заново поднять звук.
        heavy = (audio_keys - {"voice_name", "tts_speed", "tts_nfe"}) & set(changed_settings)
        note = ""
        if audio_keys & set(changed_settings):
            if not self._voice_busy():
                self._drop_audio()
            else:
                # 26 сентября: голос работал, новый образец сохранялся в
                # настройки и молча не применялся даже после выключения и
                # включения голоса — модели уже числились загруженными.
                if "voice_name" in changed_settings:
                    self._swap_voice_sample()
                if heavy:
                    self._audio_stale = True
                    note = "Сохранено. Новый звук включится, когда выключишь и включишь голос"
        # Голос по образцу без образца: выбор сохранён (иначе он бы потерялся
        # молча), но говорить пока нечем. Говорим это словами и называем путь,
        # а не возвращаем молча Silero.
        if "tts_engine" in changed_settings and changed_settings["tts_engine"] in (
                "espeech", "higgs") and not str(config.VOICE_NAME or "").strip():
            note = ("Сохранено. Образца голоса ещё нет — добавь запись .wav или .mp3: "
                    "«Голос → Озвучивание → Новый голос из записи». Пока образца нет, "
                    "она молчит, а не переходит на Silero сама")
        # Ярлык трогаем, только когда галочку переключили: «Сохранить» жмут
        # ради любой настройки. Ошибка — у самой галочки («autostart: …»),
        # остальное к этому моменту уже сохранено.
        if "autostart" in payload and payload["autostart"] != autostart.enabled():
            try:
                if payload["autostart"]:
                    autostart.enable()
                else:
                    autostart.disable()
            except Exception as exc:
                return {"ok": False, "errors": [f"autostart: {exc}"],
                        "settings": self.settings_snapshot()}
            # В журнал — факт: что лежит в папке автозагрузки на самом деле.
            self._remember("autostart", autostart.enabled())
        answer = {"ok": True, "settings": self.settings_snapshot()}
        if note:
            answer["note"] = note
        return answer

    # --- Заметки ---------------------------------------------------------
    #
    # Страница «Заметки» придёт в следующей части, а эти четыре метода уже
    # разложены по рукам API: чтоб страница не лезла на диск сама. Имена
    # разделов и тем идут только через `core/notes.py` — там проверка, что
    # имя не выводит за папку заметок, и в обход неё добраться нельзя.

    def notes_root(self) -> str:
        from core import notes

        return str(notes.root())

    def notes_list(self) -> dict:
        import config

        from core import notes

        # `obsidian` — путь хранилища, внутри которого лежат заметки, или
        # null: тогда пульт покажет «Открыть файл» и подсказку про хранилище.
        vault = notes.obsidian_vault()
        # `custom` — задана ли своя папка. Пустая строка в NOTES_DIR означает
        # папку по умолчанию, и пульту незачем предлагать «По умолчанию».
        return {"ok": True, "root": str(notes.root()),
                "custom": bool(str(getattr(config, "NOTES_DIR", "") or "").strip()),
                "obsidian": str(vault) if vault else None,
                "sections": notes.sections()}

    def notes_topic(self, section: str, topic: str) -> dict:
        from core import notes

        data = notes.read(section, topic)
        return {"ok": True, "path": data["path"], "section": data["section"],
                "topic": data["topic"], "front": data["front"],
                "entries": data["entries"]}

    def notes_delete(self, body: dict) -> dict:
        from core import notes

        body = body if isinstance(body, dict) else {}
        section = str(body.get("section", "")).strip()
        topic = str(body.get("topic", "")).strip()
        if body.get("all"):
            path = notes.delete_topic(section, topic)
        else:
            path = notes.delete_entry(section, topic, body.get("index", -1),
                                      str(body.get("heading", "")))
        self._remember("note", f"удалила «{section} / {topic}»")
        return {"ok": True, "path": str(path)}

    def notes_edit(self, body: dict) -> dict:
        """Правит заголовок и текст одной записи. Дату записи не трогаем."""
        from core import notes

        body = body if isinstance(body, dict) else {}
        section = str(body.get("section", "")).strip()
        topic = str(body.get("topic", "")).strip()
        path = notes.edit_entry(section, topic, body.get("index", -1),
                                str(body.get("heading", "")),
                                str(body.get("title", "")),
                                str(body.get("text", "")))
        self._remember("note", f"поправила запись в «{section} / {topic}»")
        return {"ok": True, "path": str(path)}

    def notes_rename(self, body: dict) -> dict:
        """Переименовывает тему и/или переносит её в другой раздел."""
        from core import notes

        body = body if isinstance(body, dict) else {}
        section = str(body.get("section", "")).strip()
        topic = str(body.get("topic", "")).strip()
        new_section = str(body.get("new_section", "")).strip()
        path = notes.rename_topic(section, topic,
                                  str(body.get("new_topic", "")), new_section)
        # Раздел и тема — как их потом увидит список: имя папки и имя файла
        # без `.md`, а не то, что хозяин напечатал в поле.
        раздел = path.parent.name
        имя = path.stem
        self._remember("note", f"переименовала «{section} / {topic}» в "
                               f"«{раздел} / {имя}»")
        return {"ok": True, "path": str(path), "section": раздел, "topic": имя}

    def notes_add(self, body: dict) -> dict:
        """Дописывает запись руками — тем же `add`, что и голосовая."""
        from core import notes

        body = body if isinstance(body, dict) else {}
        section = str(body.get("section", "")).strip()
        topic = str(body.get("topic", "")).strip()
        text = str(body.get("text", "")).strip()
        if not text:
            raise ValueError("пустой текст")
        path = notes.add(section, topic, str(body.get("title", "")), text)
        self._remember("note", f"дописала запись в «{section} / {topic}»")
        return {"ok": True, "path": str(path)}

    def notes_open(self, body: dict) -> dict:
        """Открывает папку заметок или файл темы — в проводнике или Obsidian."""
        import os

        from core import notes

        body = body if isinstance(body, dict) else {}
        section = str(body.get("section", "")).strip()
        topic = str(body.get("topic", "")).strip()
        if body.get("obsidian"):
            # Ссылка собирается только из проверенного `topic_path`: имя из
            # запроса не выводит её за папку заметок.
            if not (section and topic):
                raise ValueError("Obsidian открывает файл темы, а не папку")
            link = notes.obsidian_link(section, topic)
            os.startfile(link)
            return {"ok": True, "path": str(notes.topic_path(section, topic)),
                    "link": link}
        target = notes.root()
        if section and topic:
            target = notes.topic_path(section, topic)
            # Файл темы не создаём: `mkdir` сделал бы на его месте папку
            # «Тема.md», и Obsidian её уже не открыл бы.
            if not target.is_file():
                raise FileNotFoundError(f"нет такой темы: {topic}")
        else:
            if section:
                # Проверкой `topic_path`, а не `folder_name`: та на отказ
                # отдаёт имя как есть, и «..» открыло бы чужую папку.
                target = notes.topic_path(section, "x").parent
            target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))
        return {"ok": True, "path": str(target)}

    # --- Напоминания и таймеры для Панели ---------------------------------
    #
    # Только чтение и отмена. Ставит напоминание модель голосом
    # (`core/hands.py::set_reminder`), и пульт не должен подменять её: время
    # понимает она, а не код. Панели нужен список, чтобы хозяин видел, что
    # стоит, и «✕», чтобы снять не заходя в разговор.
    #
    # `due` уходит ISO-строкой с часовым поясом, а «через N мин» пульт
    # считает у себя: его часы и часы будильника — одни и те же, а разбирать
    # время здесь было бы тем же регулярками, которых в проекте нет.

    def reminders_list(self) -> dict:
        from core import reminders

        items = [_reminder_item(one) for one in reminders.pending()]
        return {"ok": True, "items": items}

    def reminders_cancel(self, body: dict) -> dict:
        from core import reminders

        body = body if isinstance(body, dict) else {}
        what = str(body.get("id", "")).strip()
        # Только id вида `r1`. «all» — это команда голоса («отмени всё»), и
        # пусть бы кнопка «✕» на одной строке сносила весь список: одно
        # нажатие не должно уносить то, чего хозяин не видел.
        if not re.fullmatch(r"r\d+", what):
            raise ValueError("нужен id напоминания вида r1")
        gone = reminders.cancel(what)
        if not gone:
            # Честный отказ: пустой список — это «уже не отменено», а не
            # «отменила». Молчаливое «готово» хозяин бы поверил.
            raise ValueError(f"нечего отменять: нет напоминания {what}")
        for one in gone:
            self._remember("reminder_cancel", _reminder_line(one, "отменено"))
        return {"ok": True, "items": [_reminder_item(one)
                                      for one in reminders.pending()]}

    def _drop_audio(self) -> None:
        """Звук поднимется заново при следующем включении голоса."""
        import config

        self._audio_stale = False
        self.voice._loaded = False
        self.voice._voice = None
        self.voice._ref = None
        self.voice._speaker = None
        self.voice._listener = None
        if config.TTS_ENGINE != "higgs":
            from core import higgs_voice

            higgs_voice.release()

    def _swap_voice_sample(self) -> None:
        """Новый образец голоса — сразу, со следующей фразы, без перезагрузки.

        Модели те же, меняется только образец: `_ref` читается на каждом
        предложении, а Higgs разбирает новый файл при первой встрече.
        """
        import config
        from core import voice_prep
        from core.tts import Reference

        if config.TTS_ENGINE not in ("espeech", "higgs") or self.voice._ref is None:
            return
        name = config.VOICE_NAME
        try:
            self.voice._ref = Reference(audio=voice_prep.voices_dir() / f"{name}.wav",
                                        text=voice_prep.read_text(name))
        except Exception as exc:
            self._remember("error", f"образец {name} не подхватился: {exc}")

    def test_provider(self, provider: str, key: str = "", model: str | None = None) -> dict:
        import config
        from core import settings
        from core.brain import completion_limits

        if provider not in config.PROVIDERS:
            raise ValueError("Выбери провайдера из списка")
        # Модель у локального сервера может быть ещё не выбрана: список
        # приходит с него же, поэтому говорим, что делать, а не что формат
        # неверный.
        if settings.is_local(provider) and not str(
                model or config.PROVIDERS[provider]["model"] or "").strip():
            raise ValueError("Впиши модель или обнови список")
        selected_model = _validated_model(provider, model or config.PROVIDERS[provider]["model"])
        if not isinstance(key, str) or len(key) > 500:
            raise ValueError("Неверный ключ")
        # Ключ берём через settings.key_for: локальному серверу он не нужен,
        # у него вместо ключа заглушка.
        api_key = key.strip() or settings.key_for(provider)
        if not api_key:
            raise ValueError("Сначала введи ключ API")
        if not self._provider_test_lock.acquire(blocking=False):
            raise ValueError("Проверка связи уже идёт")
        try:
            from openai import OpenAI

            spec = config.PROVIDERS[provider]
            client = OpenAI(api_key=api_key, base_url=spec["base_url"],
                            timeout=20.0, max_retries=0)
            try:
                response = client.chat.completions.create(
                    model=selected_model,
                    messages=[{"role": "user", "content": "Ответь одним словом: работает"}],
                    **completion_limits(provider, 256),
                )
            except Exception as exc:
                raise _local_error(provider, spec["base_url"], exc) from exc
            answer = (response.choices[0].message.content or "").strip()
            return {"ok": True, "answer": answer[:100] or "Ответ получен"}
        finally:
            self._provider_test_lock.release()

    def list_models(self, provider: str, key: str = "") -> dict:
        """Актуальные ID из каталога самого сервиса; ручной ввод остаётся всегда."""
        import config
        from core import settings
        from openai import OpenAI

        if provider not in config.PROVIDERS:
            raise ValueError("Выбери сервис из списка")
        if not isinstance(key, str) or len(key) > 500:
            raise ValueError("Неверный ключ")
        # Локальному серверу ключ не нужен — заглушку даёт settings.key_for,
        # а адрес приходит из настроек (см. settings.local_url).
        api_key = key.strip() or settings.key_for(provider)
        if not api_key:
            raise ValueError("Сначала введи ключ API")
        base_url = config.PROVIDERS[provider]["base_url"]
        client = OpenAI(api_key=api_key, base_url=base_url,
                        timeout=15.0, max_retries=0)
        try:
            models = sorted({item.id for item in client.models.list()
                             if isinstance(item.id, str) and item.id})
        except Exception as exc:
            raise _local_error(provider, base_url, exc) from exc
        finally:
            client.close()
        return {"ok": True, "models": models}

    def web_test(self, mode: str | None = None) -> dict:
        """Один поисковый запрос по кнопке «Проверить поиск». Настройки не меняет."""
        from core import web

        if mode is not None and mode not in web.MODES:
            raise ValueError("режим поиска бывает free, paid или auto")
        if not self._web_test_lock.acquire(blocking=False):
            raise ValueError("Проверка поиска уже идёт")
        try:
            started = time.perf_counter()
            query = "официальный курс доллара ЦБ сегодня"
            results, backend = web.search(query, mode=mode)
            took = round(time.perf_counter() - started, 2)
            first = results[0] if results else {}
            self.log_message(f"проверка поиска: {backend}, {took} с, найдено {len(results)}")
            return {"ok": True, "backend": backend, "took": took, "found": len(results),
                    "title": str(first.get("title", ""))[:120],
                    "snippet": str(first.get("snippet", ""))[:300]}
        except Exception as exc:
            self.log_message(f"проверка поиска не прошла: {exc}")
            raise
        finally:
            self._web_test_lock.release()

    def reveal_api_key(self, provider: str) -> dict:
        import config
        from core import settings

        if provider not in config.PROVIDERS:
            raise ValueError("Выбери сервис из списка")
        return {"ok": True, "key": settings.get_api_key(provider)}

    def cloud_latency(self) -> dict:
        """Один потоковый запрос для замера; историю разговора не трогает."""
        import config
        from core import settings
        from core.brain import completion_limits
        from openai import OpenAI

        provider = settings.get_provider()
        if provider not in config.PROVIDERS:
            raise ValueError("В Настройках выбери модель")
        api_key = settings.get_api_key(provider)
        if not api_key:
            raise ValueError("Сначала добавь ключ модели в Настройках")
        if not self._provider_test_lock.acquire(blocking=False):
            raise ValueError("Проверка связи уже идёт")
        try:
            spec = config.PROVIDERS[provider]
            client = OpenAI(api_key=api_key, base_url=spec["base_url"],
                            timeout=20.0, max_retries=0)
            try:
                started = time.perf_counter()
                stream = client.chat.completions.create(
                    model=spec["model"],
                    messages=[{"role": "user", "content": "Ответь одним коротким предложением: связь работает."}],
                    **completion_limits(provider, 256),
                    stream=True,
                )
                first = None
                try:
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content and first is None:
                            first = time.perf_counter() - started
                    total = time.perf_counter() - started
                finally:
                    stream.close()
            finally:
                client.close()
            if first is None:
                raise RuntimeError("Модель не прислала текст ответа")
            return {"provider": provider, "model": spec["model"],
                    "first": round(first, 2), "total": round(total, 2)}
        finally:
            self._provider_test_lock.release()

    def preview_voice(self, payload: dict) -> bytes:
        import math

        if self._voice_busy():
            raise ValueError("Сначала выключи голос")
        if not self._preview_lock.acquire(blocking=False):
            raise ValueError("Проба голоса уже готовится")
        try:
            engine = payload.get("tts_engine")
            if engine not in ("silero", "espeech", "higgs"):
                raise ValueError("Выбери движок голоса")
            speed = float(payload.get("tts_speed", 1.0))
            if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
                raise ValueError("Скорость речи должна быть от 0.5 до 2")
            phrase = "Ну чё, как тебе такой голос? Нормально звучу или поменяем?"
            if engine == "silero":
                from core.silero_voice import SileroVoice

                speaker = payload.get("silero_speaker")
                model = payload.get("silero_model")
                if not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 64:
                    raise ValueError("Выбери диктора")
                if not isinstance(model, str) or not model.strip() or len(model) > 64:
                    raise ValueError("Выбери модель голоса")
                wave, rate = SileroVoice(speaker=speaker.strip(), model=model.strip()).say(
                    phrase, speed=speed)
            elif engine == "higgs":
                from core import higgs_voice, voice_prep
                from core.tts import Reference

                name = payload.get("voice_name")
                if name not in voice_prep.available():
                    # Не «выбери образец», когда их нет: в пульте есть кнопка
                    # добавления, и причина должна называть её.
                    raise ValueError(
                        "Образца голоса нет — добавь запись .wav или .mp3: "
                        "«Голос → Озвучивание → Новый голос из записи»")
                # Голос выключен (проверено выше), значит видеопамять после
                # пробы надо отдать обратно — до 9 ГБ.
                voice = higgs_voice.shared()
                try:
                    wave, rate = voice.say(
                        phrase,
                        Reference(audio=voice_prep.voices_dir() / f"{name}.wav",
                                  text=voice_prep.read_text(name)),
                        speed=speed,
                    )
                finally:
                    voice.unload()
            else:
                from core import voice_prep
                from core.tts import Reference, Voice

                name = payload.get("voice_name")
                if name not in voice_prep.available():
                    raise ValueError(
                        "Образца голоса нет — добавь запись .wav или .mp3: "
                        "«Голос → Озвучивание → Новый голос из записи»")
                nfe = int(payload.get("tts_nfe", 32))
                if not 4 <= nfe <= 64:
                    raise ValueError("Качество голоса должно быть от 4 до 64")
                voice = Voice()
                voice.load()
                wave, rate = voice.say(
                    phrase,
                    Reference(audio=voice_prep.voices_dir() / f"{name}.wav",
                              text=voice_prep.read_text(name)),
                    nfe_step=nfe,
                    speed=speed,
                )
            from core.phone import to_wav

            return to_wav(wave, rate)
        finally:
            self._preview_lock.release()

    def logs(self, limit: int = 200) -> list:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 2000))
        path = self._log_path
        try:
            with path.open("r", encoding="utf-8", errors="replace") as file:
                return [line.rstrip("\r\n") for line in deque(file, maxlen=limit)]
        except OSError:
            return []

    def logs_from(self, after: int, limit: int = 500) -> dict:
        """Новые строки после очистки экрана; файл журнала не меняем."""
        try:
            limit = max(1, min(int(limit), 2000))
            after = max(0, int(after))
        except (TypeError, ValueError):
            limit, after = 500, 0
        try:
            with self._log_path.open("rb") as file:
                file.seek(0, 2)
                end = file.tell()
                file.seek(after if after <= end else 0)
                lines = deque(file, maxlen=limit)
            return {"lines": [line.decode("utf-8", errors="replace").rstrip("\r\n")
                              for line in lines], "offset": end}
        except OSError:
            return {"lines": [], "offset": 0}

    def apps_snapshot(self) -> list:
        from core import launcher
        items = launcher.read_list()
        previews = {item["id"]: item.get("image", "") for item in self.apps_preview(items)}
        return [{**item, "image": previews.get(item.get("id"), "")} for item in items]

    def apps_preview(self, items) -> list:
        """Значки для проекции телефона; список и настройки не меняет."""
        import config
        from core import app_icons, launcher

        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("нужен список до 100 кнопок")
        result = []
        folder = (config.DATA_DIR / "icons" / "custom").resolve()
        for source in items:
            if not isinstance(source, dict):
                raise ValueError("неверная строка программы")
            entry = {"id": str(source.get("id", "")),
                     "icon_source": source.get("icon_source") or
                     ("exe" if source.get("path") else "drawn")}
            if entry["icon_source"] == "file":
                name = source.get("icon_file", "")
                if isinstance(name, str) and name:
                    target = (config.ROOT / name).resolve()
                    if target.is_relative_to(folder) and target.suffix.lower() == ".png":
                        entry["icon_file"] = str(target)
            elif entry["icon_source"] == "exe" and source.get("kind", "app") == "app":
                path = source.get("path", "")
                if isinstance(path, str) and 0 < len(path) <= 500:
                    found = launcher.resolve({"path": path})
                    if found:
                        entry["path"] = found
            picture = app_icons.picture_for(entry)
            result.append({"id": entry["id"], "image": app_icons.as_data_url(picture) if picture else ""})
        return result

    def apps_check(self, items) -> dict:
        from core import launcher

        if not isinstance(items, list) or len(items) > 100:
            return {"ok": False, "error": "нужен список до 100 кнопок"}
        result = []
        for item in items:
            if not isinstance(item, dict):
                return {"ok": False, "error": "неверная строка программы"}
            if item.get("kind", "app") != "app":
                continue
            path = item.get("path", "")
            if not isinstance(path, str) or len(path) > 500:
                return {"ok": False, "error": "неверный путь программы"}
            found = launcher.resolve({"path": path}) if path.strip() else None
            result.append({"id": item.get("id"), "title": item.get("title"),
                           "found": bool(found), "resolved": found or ""})
        return {"ok": True, "results": result}

    def apps_icon_upload(self, app_id: str, data_url: str) -> dict:
        import base64
        import binascii
        from core import app_icons

        if not isinstance(data_url, str) or len(data_url) > 7_500_000:
            return {"ok": False, "error": "картинка должна быть не больше 5 МБ"}
        header, sep, encoded = data_url.partition(",")
        if not sep or not header.startswith("data:image/") or not header.endswith(";base64"):
            return {"ok": False, "error": "нужен файл изображения"}
        try:
            blob = base64.b64decode(encoded, validate=True)
            saved = app_icons.capture_bytes(blob, app_id)
        except (ValueError, binascii.Error) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "icon_source": "file", "icon_file": saved}

    def apps_icon_from_path(self, app_id: str, source_path: str) -> dict:
        import re
        import uuid
        from pathlib import Path

        from core import app_icons

        if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
            return {"ok": False, "error": "сначала задай кнопке id латиницей"}
        source = Path(source_path)
        allowed = app_icons.PROGRAMS | {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        if not source.is_file() or source.suffix.lower() not in allowed:
            return {"ok": False, "error": "выбери программу или файл картинки"}
        # Новая копия не заменяет текущий значок до нажатия «Сохранить».
        unique_id = f"{app_id}-{uuid.uuid4().hex[:8]}"
        saved = app_icons.capture(source, unique_id)
        if not saved:
            return {"ok": False, "error": "не удалось взять значок из этого файла"}
        return {"ok": True, "icon_source": "file", "icon_file": saved}

    def apps_save(self, items) -> dict:
        from core import launcher

        if not isinstance(items, list) or len(items) > 100:
            return {"ok": False, "error": "нужен список до 100 кнопок"}
        clean = []
        seen = set()
        # Поля, которых пульт может не прислать (старая страница, ручная
        # правка apps.json): `process` — по нему программу закрывают,
        # `aliases` — прозвища для голоса. Не прислали — берём из прежней
        # записи той же кнопки. 28.09 у ChatGPT так пропал `process`, и
        # «закрой ChatGPT» перестало его находить.
        прежние = {str(a.get("id")): a for a in launcher.read_list()
                   if isinstance(a, dict)}
        for entry in items:
            if not isinstance(entry, dict):
                return {"ok": False, "error": "каждая кнопка — объект"}
            title = entry.get("title", "")
            kind = entry.get("kind", "app")
            if not isinstance(title, str) or not title.strip() or len(title) > 80:
                return {"ok": False, "error": "у кнопки нужен title до 80"}
            title = title.strip()
            # Служебное имя (`id`) хозяин вправе не писать: название он задаёт
            # обычное русское, а имя кнопки придумываем сами из него
            # (`launcher.make_id` раскладывает кириллицу в латиницу). Раньше
            # пустое или русское `id` отвергалось, и добавить ссылку, не
            # переключаясь в латиницу, было нельзя вовсе.
            # Написанное руками латинское имя уважаем — по нему телефон помнит
            # кнопку и положение плитки.
            app_id = launcher.чистое_id(entry.get("id"), title, seen)
            if app_id is None:
                return {"ok": False, "error": "не получилось придумать id из названия"}
            if app_id in seen:
                return {"ok": False, "error": f"повторный id: {app_id}"}
            seen.add(app_id)
            if kind not in launcher.APP_KINDS:
                return {"ok": False, "error": f"вид бывает app/url/store/folder: {app_id}"}
            item = {"id": app_id, "title": title, "kind": kind}
            if kind == "folder":
                # Своя папка хозяина: у неё, как у программы, есть только путь,
                # и открывается она проводником. Без пути кнопка была бы
                # кнопкой, которая ничего не открывает.
                path = entry.get("path", "")
                if not isinstance(path, str) or not path.strip() or len(path) > 500:
                    return {"ok": False, "error": f"у {app_id} нужен путь к папке"}
                item["path"] = path.strip()
            elif kind == "url":
                url = entry.get("url", "")
                if not isinstance(url, str):
                    return {"ok": False, "error": f"у {app_id} нужна ссылка"}
                url = url.strip()
                if not url.startswith(("http://", "https://")) or len(url) > 500:
                    return {"ok": False, "error": f"у {app_id} нужна ссылка http(s)"}
                item["url"] = url
            elif kind == "store":
                aid = entry.get("app_id", "")
                if not isinstance(aid, str) or not aid.strip() or len(aid) > 200:
                    return {"ok": False, "error": f"у {app_id} нужен app_id"}
                item["app_id"] = aid.strip()
            else:
                path = entry.get("path", "")
                if not isinstance(path, str) or not path.strip() or len(path) > 500:
                    return {"ok": False, "error": f"у {app_id} нужен path"}
                item["path"] = path.strip()
                args = entry.get("args", [])
                if args is not None:
                    bad = not isinstance(args, list) or len(args) > 20
                    bad = bad or any(not isinstance(a, str) or len(a) > 300 for a in args)
                    if bad:
                        return {"ok": False, "error": f"у {app_id} аргументы — строки до 20"}
                    item["args"] = args
                if entry.get("how") is not None:
                    if entry.get("how") not in ("shell", "direct"):
                        return {"ok": False, "error": f"у {app_id} how бывает shell/direct"}
                    item["how"] = entry["how"]
                процесс = entry.get("process", (прежние.get(app_id) or {}).get("process"))
                if процесс is not None:
                    if (not isinstance(процесс, str) or len(процесс.strip()) > 64
                            or not re.fullmatch(r"[^\\/:*?\"<>|]*", процесс.strip())):
                        return {"ok": False, "error": f"у {app_id} process — имя файла"}
                    if процесс.strip():
                        item["process"] = процесс.strip()
            if entry.get("icon") is not None:
                if not isinstance(entry["icon"], str) or len(entry["icon"]) > 40:
                    return {"ok": False, "error": f"у {app_id} значок строкой"}
                item["icon"] = entry["icon"]
            # Цвет рисованного значка (`#rrggbb`). Кривой — просто не пишем:
            # телефон всё равно знает свой цвет каждой программы по id.
            if entry.get("color") is not None:
                value = entry["color"]
                if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
                    item["color"] = value.strip().lower()
            source = entry.get("icon_source")
            if source is not None:
                if source not in ("drawn", "exe", "file"):
                    return {"ok": False, "error": f"у {app_id} неверный источник значка"}
                item["icon_source"] = source
                if source == "file":
                    import config

                    name = entry.get("icon_file")
                    if not isinstance(name, str) or not name:
                        return {"ok": False, "error": f"у {app_id} нет файла значка"}
                    target = (config.ROOT / name).resolve()
                    folder = (config.DATA_DIR / "icons" / "custom").resolve()
                    if not target.is_relative_to(folder) or target.suffix.lower() != ".png":
                        return {"ok": False, "error": f"у {app_id} недопустимый файл значка"}
                    if not target.is_file():
                        return {"ok": False, "error": f"у {app_id} файл значка не найден"}
                    item["icon_file"] = target.relative_to(config.ROOT).as_posix()
            прозвища = entry.get("aliases", (прежние.get(app_id) or {}).get("aliases"))
            if прозвища is not None:
                if (not isinstance(прозвища, list) or len(прозвища) > 20
                        or any(not isinstance(a, str) or len(a) > 40 for a in прозвища)):
                    return {"ok": False, "error": f"у {app_id} прозвища — до 20 строк"}
                прозвища = [a.strip() for a in прозвища if a.strip()]
                if прозвища:
                    item["aliases"] = прозвища
            menu = entry.get("menu")
            if menu is not None:
                why, разобранное = self._check_menu(app_id, menu)
                if why:
                    return {"ok": False, "error": why}
                item["menu"] = launcher.clean_menu(разобранное)
            if entry.get("bookmarks") is not None:
                if entry["bookmarks"] not in ("", "firefox"):
                    return {"ok": False, "error": f"у {app_id} bookmarks бывает firefox"}
                if entry["bookmarks"]:
                    item["bookmarks"] = "firefox"
            clean.append(item)
        launcher.save_list(clean)
        self.voice._apps = None
        self._bg(self._send_apps)
        return {"ok": True, "apps": clean}

    @staticmethod
    def _check_menu(app_id: str, raw):
        """Проверяет меню перед записью. `(пустая строка, пункты)` — всё в порядке.

        Номер пункта попадает в текст ошибки, чтобы пульт показал ошибку у
        нужного поля, а не просто «что-то не так».

        Вторым возвращаемым значением идут сами пункты с уже придуманными
        именами: имя пункта хозяин может не писать (берётся из русского
        названия), и записывать надо то, что сервер придумал, а не пустое
        поле из формы.
        """
        from core import hotkeys, launcher

        if not isinstance(raw, list):
            return f"у {app_id} меню — список пунктов", None
        if len(raw) > 24:
            return f"у {app_id} пунктов меню не больше 24", None

        seen = set()
        разобранные = []
        for number, entry in enumerate(raw, 1):
            where = f"меню {number}: "
            if not isinstance(entry, dict):
                return f"{where}пункт — объект", None
            kind = entry.get("kind")
            if kind not in ("hotkey", "site"):
                return f"{where}вид бывает hotkey или site", None
            title = entry.get("title", "")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 60:
                return f"{where}нужно название до 60 знаков", None
            # Имя пункта — как и у кнопки, придумываем из русского названия:
            # хозяину «Мой профиль» не нужно переводить в `profile` руками.
            name = launcher.чистое_id(entry.get("id"), title.strip(), seen, 40)
            if not name:
                return f"{where}не получилось придумать имя пункта из названия", None
            if name in seen:
                return f"{where}повтор имени «{name}»", None
            seen.add(name)
            готов = dict(entry)
            готов["id"] = name
            разобранные.append(готов)
            if kind == "hotkey":
                keys = entry.get("keys", "")
                try:
                    hotkeys.parse(keys if isinstance(keys, str) else "")
                except ValueError as exc:
                    return f"{where}{exc}", None
            else:
                url = entry.get("url", "")
                if not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
                    return f"{where}нужен адрес, начинающийся с http:// или https://", None
                if len(url.strip()) > 500:
                    return f"{where}адрес длиннее 500 знаков", None
            icon = entry.get("icon")
            if icon is not None and (not isinstance(icon, str) or len(icon) > 40):
                return f"{where}значок — строкой до 40", None
            if "toggle" in entry and entry["toggle"] is not None:
                if not isinstance(entry["toggle"], bool):
                    return f"{where}переключатель — галочка (true) или ничего", None
        # `implies` смотрит на весь список: связать можно только с пунктом
        # этой же программы, иначе подсветка была бы обещанием впустую.
        for number, (entry, готов) in enumerate(zip(raw, разобранные), 1):
            if not isinstance(entry, dict):
                continue
            other = entry.get("implies")
            if other in (None, ""):
                continue
            if not isinstance(other, str) or not re.fullmatch(
                r"[A-Za-z0-9._-]{1,40}", other.strip()
            ):
                return f"меню {number}: «включает также» — имя другого пункта", None
            if other.strip() not in seen:
                return (f"меню {number}: «включает также» — нет пункта "
                        f"«{other.strip()}» в этой программе"), None
            if other.strip() == готов["id"]:
                return f"меню {number}: пункт не может включать сам себя", None
        return "", разобранные

    def apps_launch(self, app_id: str) -> dict:
        from core import launcher

        if not isinstance(app_id, str) or not app_id.strip():
            return {"ok": False, "error": "нужен id кнопки"}
        ok, what = launcher.launch(app_id.strip())
        self._remember("launched" if ok else "launch_failed", what)
        return {"ok": bool(ok), "what": what}

    def job_state(self, jid: str):
        with self._lock:
            job = self._jobs.get(jid)
            job = dict(job) if job else None
            cur = self._enroll
        if job is None:
            return None
        # Во время записи отдаём живые секунды, как тиканье в check_tab.
        if job.get("state") == "running" and cur is not None \
                and cur.get("id") == jid:
            try:
                job["seconds"] = round(float(cur["recorder"].seconds), 1)
            except Exception:
                pass
        return job

    def check_start(self, kind: str) -> dict:
        if kind not in ("run_all", "voice_level", "cloud"):
            return {"error": "вид бывает run_all/voice_level/cloud"}
        if self._voice_busy():
            return {"error": "Останови голосовой режим — микрофон занят"}
        jid = uuid.uuid4().hex[:12]
        with self._lock:
            if self._enroll is not None or any(
                j.get("kind") in ("run_all", "voice_level", "cloud") and
                j.get("state") == "running" for j in self._jobs.values()
            ):
                return {"error": "Другая проверка или запись уже идёт"}
            self._jobs[jid] = {"id": jid, "state": "running", "kind": kind}

        def run():
            try:
                from core import selftest

                if kind == "run_all":
                    report = selftest.run_all(with_cloud=False)
                    payload = {"results": [
                        {"name": r.name, "value": r.value,
                         "ok": r.ok, "note": r.note} for r in report.results]}
                elif kind == "cloud":
                    payload = {"result": self.cloud_latency()}
                else:
                    data = selftest.voice_level()
                    data = dict(data) if isinstance(data, dict) else {}
                    data.setdefault("ok", True)
                    payload = {"result": data}
            except Exception as exc:
                with self._lock:
                    self._jobs[jid] = {"id": jid, "state": "error",
                                       "error": f"{type(exc).__name__}: {exc}"}
                return
            with self._lock:
                self._jobs[jid] = {"id": jid, "state": "done", **payload}

        threading.Thread(target=run, daemon=True).start()
        return {"id": jid, "state": "running"}

    # --- Распознавание речи: состояние и проба голосом ---------------------
    #
    # Пишем с микрофона так же, как образец хозяина в «Слухе»: тот же
    # `selftest.record` и тот же отказ, когда микрофон занят голосом. Иначе
    # запись посреди работы цикла забирала бы звук у слушающего.

    STT_TEST_SECONDS = 4.0
    # Сколько ждать фразу, когда голос работает и микрофон занят им.
    STT_WAIT_SECONDS = 10.0

    def stt_state(self) -> dict:
        """Кто сейчас разбирает речь и не грузится ли другой.

        Пока голос выключен, работающей модели нет — тогда показываем ту,
        что в config: после включения встанет именно она.
        """
        import config

        from core import stt_models, voice_loop

        state = {}
        try:
            state = dict(self.voice.stt_state() or {})
        except Exception:
            state = {}
        if not state.get("model"):
            state["model"] = str(config.STT_MODEL)
            state["title"] = voice_loop.stt_label(
                config.STT_MODEL, config.STT_QUANTIZATION)
            state["quantization"] = stt_models.quantization_name(
                config.STT_QUANTIZATION)
            state.setdefault("loading", False)
            state.setdefault("error", "")
        state["models"] = stt_models.models_payload()
        state["quantizations"] = stt_models.quantizations_payload()
        return {"ok": True, **state}

    def stt_test(self) -> dict:
        """Записать с микрофона и разобрать текущей моделью."""
        import config

        from core import selftest

        if getattr(self.voice, "_stt", None) is None:
            return {"ok": False, "error": "голос ещё не загружен"}
        if bool(getattr(self.voice, "running", False)):
            # Голос слушает — микрофон занят им. Ждём следующую фразу,
            # которую разберёт он сам, и показываем её: это и есть
            # «как она тебя слышит», без второй записи.
            import time

            начало = time.monotonic()
            срок = начало + self.STT_WAIT_SECONDS
            while time.monotonic() < срок:
                последняя = getattr(self.voice, "_last_stt", None)
                if последняя and последняя[0] > начало:
                    return {"ok": True, "text": str(последняя[1] or "").strip(),
                            "seconds": последняя[2] if len(последняя) > 2 else None}
                time.sleep(0.2)
            return {"ok": False,
                    "error": "за 10 секунд ничего не услышала — скажи погромче"}
        if self._voice_busy():
            return {"ok": False, "error": "она сейчас говорит — попробуй через пару секунд"}
        try:
            track, rate = selftest.record(self.STT_TEST_SECONDS)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not len(track):
            return {"ok": False, "error": "микрофон молчит — проверь, включён ли он"}
        seconds = round(len(track) / float(rate or config.SAMPLE_RATE), 1)
        try:
            # Тем же замком, что и фраза: проба не должна вклиниться в
            # перезагрузку модели.
            with self.voice._stt_locked():
                text = self.voice._stt.recognize(track, sample_rate=rate)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "text": str(text or "").strip(), "seconds": seconds}

    def enroll_start(self) -> dict:
        from core import selftest

        if self._voice_busy():
            return {"error": "останови голосовой режим — микрофон занят"}
        from core import settings

        settings.apply_to_config()
        recorder = selftest.Recorder()
        jid = uuid.uuid4().hex[:12]
        started = time.monotonic()
        limit = float(self._enroll_seconds)
        with self._lock:
            if self._enroll is not None or any(
                j.get("kind") in ("run_all", "voice_level", "cloud") and
                j.get("state") == "running" for j in self._jobs.values()
            ):
                return {"error": "Другая проверка или запись уже идёт"}
            try:
                recorder.start()
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
            self._enroll = {"id": jid, "recorder": recorder}
            self._jobs[jid] = {"id": jid, "state": "running",
                               "kind": "enroll", "started": started}

        def watch() -> None:
            recorder.wait(limit)
            self.enroll_stop(auto=True)

        threading.Thread(target=watch, daemon=True).start()
        return {"id": jid, "state": "running"}

    def enroll_stop(self, auto: bool = False) -> dict:
        with self._lock:
            cur = self._enroll
            self._enroll = None
            job = self._jobs.get(cur["id"]) if cur else None
            if job is not None and job.get("state") != "running":
                return {"id": cur["id"], "state": job.get("state")}
            if job is not None:
                job = dict(job)
                job["state"] = "processing"
                self._jobs[cur["id"]] = job
        if cur is None:
            return {"error": "запись не идёт"}
        recorder = cur["recorder"]
        jid = cur["id"]

        def run() -> None:
            try:
                try:
                    recorder.finish()
                except Exception:
                    pass
                track, rate = recorder.stop()
                if len(track) < rate * 3:
                    raise RuntimeError("записалось меньше трёх секунд")
                from core import selftest
                from core.speaker import Voiceprint

                pieces = selftest.split_speech(track, rate)
                if len(pieces) < 2:
                    raise RuntimeError(
                        "речи почти не слышно — проверь микрофон и говори погромче")
                taken = Voiceprint().enroll(pieces, rate)
                # Если голосовой цикл уже работал раньше, старый отпечаток
                # закеширован в нём — при следующем включении нужен новый.
                self.voice._voiceprint = None
                try:
                    spread = Voiceprint().spread()
                except Exception:
                    spread = None
                suggested = self._owner_threshold_from_spread(spread)
                payload = {"taken": taken, "spread": spread,
                           "seconds": round(len(track) / rate, 1),
                           "parts": taken}
                if suggested is not None:
                    from core import settings

                    try:
                        settings.save_settings({"owner_threshold": suggested})
                        settings.apply_to_config()
                        payload["owner_threshold"] = suggested
                    except Exception as exc:
                        payload["threshold_error"] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                with self._lock:
                    cur_job = self._jobs.get(jid)
                    if cur_job is not None and cur_job.get("state") != "processing":
                        return
                    self._jobs[jid] = {
                        "id": jid, "state": "error",
                        "error": f"{type(exc).__name__}: {exc}"}
                return
            finally:
                try:
                    recorder.stop()
                except Exception:
                    pass
            with self._lock:
                cur_job = self._jobs.get(jid)
                if cur_job is not None and cur_job.get("state") != "processing":
                    return
                self._jobs[jid] = {"id": jid, "state": "done", **payload}

        threading.Thread(target=run, daemon=True).start()
        return {"id": jid, "state": "processing"}

    @staticmethod
    def _owner_threshold_from_spread(spread) -> float | None:
        """Как в старом пульте: ниже худшей пары собственного образца."""
        if not isinstance(spread, dict):
            return None
        try:
            worst = float(spread["worst"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(worst):
            return None
        return round(max(0.35, min(0.80, worst - 0.08)), 2)

    def apply_threshold(self, value) -> dict:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": "порог — число"}
        if not 0.01 <= num <= 1.0:
            return {"ok": False, "error": "порог держи в пределах 0.01…1.0"}
        try:
            from core import settings

            settings.save_settings({"barge_in_level": num})
            settings.apply_to_config()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "barge_in_level": num}

    def close(self) -> None:
        with self._lock:
            cur = self._enroll
            self._enroll = None
            if cur is not None:
                job = self._jobs.get(cur["id"])
                if job is not None and job.get("state") in ("running", "processing"):
                    self._jobs[cur["id"]] = {
                        "id": cur["id"], "state": "error",
                        "error": "запись прервана — пульт закрыт"}
        if cur is not None:
            try:
                cur["recorder"].stop()
            except Exception:
                pass
        try:
            self.voice.stop()
        except Exception:
            pass
        self._warm_stop()
        try:
            self.replay_guard.stop()
        except Exception:
            pass
        # Будильник напоминаний — вместе с пультом. Пока он жив, закрытие
        # окна оставило бы фоновый поток, который через секунду после ухода
        # хозяина всё равно позвонил бы ему в спикер.
        alarm = getattr(self, "alarm", None)
        if alarm is not None:
            try:
                alarm.stop()
            except Exception:
                pass
        try:
            self.server.detach(self.handle_event)
        except Exception:
            pass
