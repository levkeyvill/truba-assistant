r"""Телефон как тело Трубы: отдельный динамик и лицо на экране.

Сервер раздаёт страницу и гонит на неё звук по WebSocket. Телефон только
проигрывает и рисует — микрофон остаётся на компе, поэтому ограничение
браузеров на запись звука по обычному HTTP нас не касается.

PhoneSpeaker повторяет интерфейс колонок из audio_out, чтобы голосовой цикл
не знал, куда именно уходит речь.
"""

import asyncio
import hmac
import io
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from fastapi import WebSocket

import config
from core import safe_files

# ВАЖНО: без "from __future__ import annotations".
# С ним аннотации превращаются в строки, и FastAPI не может разобрать
# обработчик WebSocket — параметр `ws: WebSocket` он принимает за обычный
# параметр запроса и отбивает соединение с ошибкой 403. По той же причине
# WebSocket импортируется здесь, а не внутри метода: разбор аннотаций идёт
# по пространству имён модуля.


def _rank(address: str) -> int:
    """Чем меньше число, тем вероятнее, что это домашняя сеть.

    Спрашивать систему «какой адрес наружу» нельзя: при включённом VPN она
    вернёт адрес туннеля, а телефон в домашнем Wi-Fi по нему не достучится.
    Поэтому оцениваем сами — 192.168 почти всегда домашний роутер, 172.16-31
    обычно раздают Docker и Hyper-V, 169.254 означает, что адрес не выдан.
    """
    if address.startswith("192.168."):
        return 0
    if address.startswith("10."):
        return 1  # бывает и дома, и в VPN
    if address.startswith("172."):
        try:
            second = int(address.split(".")[1])
        except (IndexError, ValueError):
            return 4
        return 3 if 16 <= second <= 31 else 2
    if address.startswith("169.254.") or address.startswith("127."):
        return 9
    return 2


def local_addresses() -> list[str]:
    """Все адреса компа, самый вероятный для телефона — первым."""
    found = set()
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        found.update(addresses)
    except Exception:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass

    usable = [a for a in found if not a.startswith("127.")]
    return sorted(usable, key=lambda a: (_rank(a), a)) or ["127.0.0.1"]


def local_ip() -> str:
    """Адрес компа в домашней сети — его набирают на телефоне."""
    return local_addresses()[0]


def _тема() -> str:
    """Тема оформления из настроек. Читаем молча: файла настроек может ещё
    не быть, а страницы обязаны отдаться в любом случае."""
    try:
        from core import settings

        return settings.validate_theme(settings.load_settings().get("theme"))
    except Exception:
        return config.THEME


def _с_темой(разметка: str) -> str:
    """Вписать `data-theme` в `<html>` — и пульта, и страницы телефона.

    Так тема попадает в первую же отрисовку: странице не нужно ничего
    догадываться, и ни пульт, ни телефон не мигают тёмным при запуске.
    Если в разметке нет открывающего `<html ...>` (файл правили руками) —
    возвращаем как было: страница просто покажется в тёмной теме.
    """
    начало = разметка.find("<html")
    if начало < 0:
        return разметка
    конец = разметка.find(">", начало)
    if конец < 0:
        return разметка
    тег = разметка[начало:конец]
    if "data-theme" in тег:
        return разметка
    return разметка[:конец] + f' data-theme="{_тема()}"' + разметка[конец:]


# --- Кто может подключаться ------------------------------------------------
#
# Пульт открыт на этом компьютере, телефон — из домашней сети. До 29.09
# сервер пускал в сеть без спроса: с любого устройства в том же Wi-Fi можно
# было прочитать память и заметки, снять экран и нажимать кнопки. Теперь:
#   - данные пульта (настройки, журнал, программы, /state) — только отсюда;
#   - телефон подключается по ключу из ссылки в QR-коде (один раз: дальше
#     страница помнит его сама);
#   - WebSocket не пускает чужие страницы (Origin), даже из браузера на
#     этом же компьютере.

# Адреса «этого компьютера». Тесты FastAPI стучатся как testserver/testclient.
LOCAL_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "testserver", "testclient")

# Ключ привязки телефона. Создаётся один раз и живёт в данных, а не в
# настройках: его не показывают и не правят руками. Тесты подменяют путь.
KEY_FILE = config.DATA_DIR / "phone_key.txt"
KEY_MIN = 16


def client_is_local(conn) -> bool:
    """Запрос или WebSocket с этого компьютера."""
    client = getattr(conn, "client", None)
    return (client.host if client else "") in LOCAL_HOSTS


def phone_key() -> str:
    """Ключ привязки телефона; нет — заводится сразу."""
    try:
        ключ = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        ключ = ""
    if len(ключ) >= KEY_MIN:
        return ключ
    ключ = secrets.token_urlsafe(18)
    safe_files.write_text(KEY_FILE, ключ)
    return ключ


def ws_refusal(ws) -> str:
    """Почему не пускаем это соединение. Пустая строка — пускаем.

    Origin проверяется у всех: страница телефона и макет в пульте приходят с
    этого же сервера, и их Origin совпадает с Host. Не совпал — это чужой
    сайт, который пытается дотянуться до Трубы из браузера. Ключ — только у
    соединений из сети: пульт и его макет телефона работают отсюда.
    """
    origin = ws.headers.get("origin")
    host = (ws.headers.get("host") or "").lower()
    if origin and urlsplit(origin).netloc.lower() != host:
        return "чужая страница"
    if client_is_local(ws):
        return ""
    ключ = str(ws.query_params.get("k") or "")
    # Байтами: строку с не-ASCII знаками `compare_digest` не сравнивает, а
    # падает — и присланный кириллицей «ключ» ронял бы проверку.
    if not ключ or not hmac.compare_digest(ключ.encode("utf-8"), phone_key().encode("utf-8")):
        return "нет ключа"
    return ""


# Размер экрана последнего телефона. Отдельный файл, а не поле в настройках:
# он не про вид сетки, а про то, как пульт рисует макет, и меняется сам по
# себе, когда телефон повернулся. Пульт подменяет путь (tests/test_data_guard.py).
VIEWPORT_FILE = config.DATA_DIR / "phone_viewport.json"
# Таких размеров на экране не бывает, а телефон может прислать что угодно:
# проверяем на границах, иначе макет в пульте стал бы неприлично мелким.
VIEWPORT_MIN = 200
VIEWPORT_MAX = 4000


def _read_viewport() -> dict | None:
    """Последний размер экрана телефона: `{w, h}` или `None`.

    Читаем молча: файла ещё нет (телефон ни разу не подключался) — это не
    поломка, а обычное состояние. Мусор в файле — тоже не поломка: макет
    просто возьмёт запасные 851×393.
    """
    try:
        данные = json.loads(VIEWPORT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(данные, dict):
        return None
    return _viewport_pair(данные.get("w"), данные.get("h"))


def _write_viewport(width: int, height: int) -> None:
    """Запомнить размер экрана. Ошибка записи — молча, как и чтение.

    Файл только подсказка для макета; телефон всё равно пришлёт свой размер
    заново при следующем подключении.
    """
    try:
        VIEWPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        VIEWPORT_FILE.write_text(
            json.dumps({"w": width, "h": height}), encoding="utf-8"
        )
    except OSError:
        pass


def _viewport_pair(width, height) -> dict | None:
    """Целые 200…4000 по обе стороны — иначе размер не принимается."""
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    if not (VIEWPORT_MIN <= width <= VIEWPORT_MAX):
        return None
    if not (VIEWPORT_MIN <= height <= VIEWPORT_MAX):
        return None
    return {"w": width, "h": height}


def to_wav(wave: np.ndarray, sample_rate: int) -> bytes:
    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(buffer, wave, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class PhoneServer:
    """Держит страницу и соединение с телефоном."""

    def __init__(self, port: int = None, on_event=None):
        # Порт по умолчанию — общий привычный (`core/instance.py`): у всех
        # копий он такой же, второй пульт на компьютере не поднимается.
        # Явный порт (тесты, макеты) по-прежнему главнее.
        if port is None:
            from core import instance

            port = instance.порт()
        self.port = int(port)
        self.url = f"http://{local_ip()}:{port}"
        # Слушателей может быть несколько: пульт показывает статус подключения,
        # голосовой цикл ловит отчёты о проигранной речи.
        self._listeners = [on_event] if on_event else []

        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set = set()
        # Соединения, у которых включён звук. Страница поднимается сразу при
        # загрузке, а `AudioContext` браузер без касания не запускает, поэтому
        # «на связи» и «звук готов» — разные вещи, и речь мы шлём только туда,
        # где второе.
        self._audio: set = set()
        # Размер экрана последнего телефона: `{w, h}` в пикселях. Пульт рисует
        # макет в iframe этого размера, иначе при четырёх колонках сетка в макете
        # врёт. Хранится и на диске — после перезапуска пульта макет не прыгает.
        self._viewport: dict | None = _read_viewport()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

        # Источники последней карточки поиска. По номеру из этого списка
        # телефон просит открыть источник на компьютере — адрес он не
        # присылает и прислать не может (см. `send_search_result`).
        self.last_sources: list[dict] = []

        # Обновление погоды. Заполняется при старте, см. `_start_weather`.
        self._weather = None

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    @property
    def audio_ready(self) -> bool:
        """Хотя бы у одного телефона запущен звук. Речь идёт только туда."""
        return bool(self._audio)

    @property
    def viewport(self) -> dict | None:
        """Размер экрана последнего телефона: `{w, h}` или `None`.

        Пульт рисует макет в iframe этого размера — иначе значки в макете не
        совпали бы с телефоном. `None` — телефон ещё не прислал размер, и макет
        берёт запасные 851×393.
        """
        текущий = getattr(self, "_viewport", None)
        return dict(текущий) if текущий else None

    def page_version(self) -> str:
        """Отпечаток страницы. Меняется при любой правке файла."""
        import hashlib

        page = config.ROOT / "web" / "index.html"
        try:
            return hashlib.md5(page.read_bytes()).hexdigest()[:8]
        except OSError:
            return "нет"

    # --- Запуск -----------------------------------------------------------

    @staticmethod
    def port_taken(port: int) -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            return probe.connect_ex(("127.0.0.1", port)) == 0
        finally:
            probe.close()

    def start(self, wait: float = 0.0) -> None:
        """Поднимает сервер в фоне.

        Ждать готовности в потоке интерфейса нельзя: если порт занят, окно
        замрёт на всё время ожидания, а сервера всё равно не будет.
        """
        if self._thread and self._thread.is_alive():
            return

        if self.port_taken(self.port):
            raise RuntimeError(
                f"Порт {self.port} уже занят — видимо, пульт запущен дважды. "
                "Закрой лишнее окно или смени порт в настройках."
            )

        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._start_weather()
        if wait:
            self._ready.wait(timeout=wait)

    def _start_weather(self) -> None:
        """Поднимает обновление погоды.

        Отдельный поток и никакого ожидания: сеть в потоке окна запрещена, а
        телефон приходит уже после старта пульта и должен увидеть погоду сразу
        (из кеша её шлёт обработчик соединения).
        """
        from core import weather

        self._weather = weather.Watcher(self)
        self._weather.start()

    def _serve(self) -> None:
        import uvicorn
        from fastapi import FastAPI, Request, WebSocketDisconnect
        from fastapi.responses import (
            FileResponse,
            HTMLResponse,
            JSONResponse,
            PlainTextResponse,
            Response,
            StreamingResponse,
        )

        app = FastAPI()
        # Держим ссылку, чтобы можно было посмотреть зарегистрированные
        # маршруты снаружи — иначе непонятно, что видит сервер.
        self._app = app
        web = config.ROOT / "web"

        # ВАЖНО: не добавлять сюда @app.middleware("http").
        # Starlette оборачивает им всё приложение целиком, включая
        # WebSocket-запросы, обрабатывать которые оно не умеет — соединение
        # отбивается с 403 ещё до обработчика. Логировать запросы можно
        # только внутри самих обработчиков.

        # Страница меняется часто, а браузер телефона охотно держит старую
        # версию — из-за этого правки будто не применяются.
        NO_CACHE = {
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        @app.get("/")
        async def index():
            # Отмечаем, что телефон хотя бы дошёл до страницы: если дальше
            # соединение не появится, значит дело в самой странице.
            self._emit("page_opened", None)
            html = (web / "index.html").read_text(encoding="utf-8")
            # Вшиваем отпечаток файла: страница сверит его с присланным
            # при подключении и перезагрузится сама, если устарела.
            # Тему вписываем туда же — до первой отрисовки, иначе телефон
            # моргает тёмным, пока не пришло сообщение по связи.
            return HTMLResponse(
                _с_темой(html.replace("__VERSION__", self.page_version())),
                headers=NO_CACHE,
            )

        # Манифест и иконка нужны, чтобы страница ставилась на телефон
        # как приложение и открывалась без адресной строки.
        @app.get("/manifest.json")
        async def manifest():
            return FileResponse(
                web / "manifest.json",
                media_type="application/manifest+json",
                headers=NO_CACHE,
            )

        @app.get("/icon.svg")
        async def icon():
            return FileResponse(
                web / "icon.svg", media_type="image/svg+xml", headers=NO_CACHE
            )

        @app.get("/search_hum.wav")
        async def search_hum_wav():
            """Звук фона поиска для телефона.

            Без ключа, как иконка: это просто звук, в нём ничего нет, что
            стоило бы прятать от чужого в сети.
            """
            from core import search_hum

            данные = search_hum.wav_bytes()
            if данные is None:
                # Файла звука нет — телефон просто промолчит.
                return PlainTextResponse("нет звука", status_code=404)
            return Response(content=данные, media_type="audio/wav", headers=NO_CACHE)

        @app.get("/fonts/{name}")
        async def font(name: str):
            """Раздаёт шрифт странице телефона.

            Объявить шрифт в стилях мало: в браузере телефона его нет, и
            страница молча падает на системный. Поэтому файл лежит в
            проекте и отдаётся отсюда.
            """
            # Имя приходит снаружи — забираем только сам файл, без путей.
            target = web / "fonts" / Path(name).name
            if target.suffix.lower() != ".ttf" or not target.is_file():
                return PlainTextResponse("нет такого шрифта", status_code=404)
            # Шрифт не меняется — пусть телефон держит его у себя, а не
            # тянет четверть мегабайта при каждом открытии страницы.
            return FileResponse(
                target,
                media_type="font/ttf",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

        # --- Пульт на компе ---------------------------------------------
        #
        # Живёт на том же сервере, что и телефон: процесс один, и второй
        # поднимать незачем. Страница своя — телефонная горизонтальная и
        # под палец, а эта под мышь и большое окно.
        pult = config.ROOT / "ui" / "web"

        @app.get("/pult")
        async def pult_page():
            # Тему вписываем прямо в `<html>`: иначе страница успевает
            # отрисоваться тёмной и мигает, пока скрипт её перекрашивает.
            return HTMLResponse(
                _с_темой((pult / "pult.html").read_text(encoding="utf-8")),
                headers=NO_CACHE,
            )

        @app.get("/pult/{name}")
        async def pult_file(name: str):
            """Стили и скрипты пульта. Наружу отдаём только своё."""
            target = pult / Path(name).name
            types = {".css": "text/css", ".js": "text/javascript"}
            kind = types.get(target.suffix.lower())
            if kind is None or not target.is_file():
                return PlainTextResponse("нет такого файла", status_code=404)
            return FileResponse(target, media_type=kind, headers=NO_CACHE)

        @app.get("/state")
        async def state(request: Request):
            """Сводка состояния для карточек наверху пульта. Только отсюда."""
            from core import state as snapshot

            if not client_is_local(request):
                return JSONResponse(
                    {"ok": False, "error": "доступно только с этого компьютера"},
                    status_code=403)
            return JSONResponse(snapshot.everything(self), headers=NO_CACHE)

        # --- Чат пульта ---------------------------------------------------
        # В веб-пульте голос и чат разделяют один Brain и общую историю.
        # Без веб-пульта новый Brain подхватывает историю с диска на запрос.
        _chat_lock = asyncio.Lock()

        def _ensure_chat_brain():
            from core import settings
            from core.brain import Brain

            rt = getattr(self, "runtime", None)
            if rt is not None and rt.brain is not None:
                return rt.brain
            settings.apply_to_config()
            return Brain(provider=settings.get_provider())

        def _ask_brain(text: str) -> str:
            brain = _ensure_chat_brain()
            pieces = [p.strip() for p in brain.reply(text) if p and p.strip()]
            return " ".join(pieces).strip()

        @app.post("/chat")
        async def chat(request: Request):
            """Текстовый чат пульта. Только с этого же компа."""
            if not client_is_local(request):
                return JSONResponse(
                    {"ok": False, "error": "чат доступен только с этого компьютера"},
                    status_code=403,
                )
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с полем text"},
                    status_code=400,
                )
            text = body.get("text", "") if isinstance(body, dict) else ""
            if not isinstance(text, str):
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с полем text"},
                    status_code=400,
                )
            text = text.strip()
            if not text:
                return JSONResponse(
                    {"ok": False, "error": "пустое сообщение отправлять нечего"},
                    status_code=400,
                )
            if len(text) > 4000:
                return JSONResponse(
                    {"ok": False, "error": "слишком длинно: держись в пределах 4000 символов"},
                    status_code=400,
                )
            if _chat_lock.locked():
                return JSONResponse(
                    {"ok": False, "error": "она ещё думает над прошлым — подожди ответ"},
                    status_code=429,
                )
            async with _chat_lock:
                rt = getattr(self, "runtime", None)
                started = time.monotonic()
                if rt is not None:
                    rt.log_message("чат: запрос отправлен")
                try:
                    answer = await asyncio.to_thread(_ask_brain, text)
                except Exception as exc:
                    # Честно, но без внутренностей: тип + короткое сообщение.
                    msg = str(exc).strip().replace("\n", " ")
                    if len(msg) > 300:
                        msg = msg[:300] + "…"
                    detail = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
                    if rt is not None:
                        rt.log_message(f"чат: ошибка — {detail}")
                    return JSONResponse({"ok": False, "error": detail}, status_code=500)
            if not answer:
                if rt is not None:
                    rt.log_message("чат: модель вернула пустой ответ")
                return JSONResponse(
                    {"ok": False, "error": "пришёл пустой ответ — попробуй ещё раз"},
                    status_code=500,
                )
            if rt is not None:
                rt.log_message(f"чат: ответ за {time.monotonic() - started:.2f} с")
            return JSONResponse({"ok": True, "reply": answer})

        @app.post("/chat/stream")
        async def chat_stream(request: Request):
            """Части ответа по мере готовности; история остаётся общей с голосом."""
            if not client_is_local(request):
                return JSONResponse({"ok": False, "error": "чат доступен только с этого компьютера"}, status_code=403)
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "нужен JSON с полем text"}, status_code=400)
            text = body.get("text", "") if isinstance(body, dict) else ""
            if not isinstance(text, str) or not text.strip():
                return JSONResponse({"ok": False, "error": "пустое сообщение отправлять нечего"}, status_code=400)
            text = text.strip()
            if len(text) > 4000:
                return JSONResponse({"ok": False, "error": "слишком длинно: держись в пределах 4000 символов"}, status_code=400)
            if _chat_lock.locked():
                return JSONResponse({"ok": False, "error": "она ещё думает над прошлым — подожди ответ"}, status_code=429)

            async def chunks():
                await _chat_lock.acquire()
                worker = None
                try:
                    rt = getattr(self, "runtime", None)
                    started = time.monotonic()
                    if rt is not None:
                        rt.log_message("чат: запрос отправлен")
                    loop = asyncio.get_running_loop()
                    queue: asyncio.Queue = asyncio.Queue()

                    def emit(kind: str, value: str = "") -> None:
                        item = {"type": kind}
                        if kind == "chunk":
                            item["text"] = value
                        elif kind == "error":
                            item["error"] = value
                        loop.call_soon_threadsafe(queue.put_nowait, item)

                    def produce() -> None:
                        had_text = False
                        try:
                            brain = _ensure_chat_brain()
                            for part in brain.reply(text):
                                clean = part.strip() if isinstance(part, str) else ""
                                if clean:
                                    had_text = True
                                    emit("chunk", clean)
                            if not had_text:
                                raise RuntimeError("пришёл пустой ответ — попробуй ещё раз")
                            if rt is not None:
                                rt.log_message(f"чат: ответ за {time.monotonic() - started:.2f} с")
                            emit("done")
                        except Exception as exc:
                            msg = str(exc).strip().replace("\n", " ")[:300]
                            detail = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
                            if rt is not None:
                                rt.log_message(f"чат: ошибка — {detail}")
                            emit("error", detail)

                    worker = asyncio.create_task(asyncio.to_thread(produce))
                    while True:
                        item = await queue.get()
                        yield json.dumps(item, ensure_ascii=False) + "\n"
                        if item["type"] in ("done", "error"):
                            break
                finally:
                    # Если вкладку закрыли на полуслове, модель ещё работает.
                    # Не пускаем другой чат, пока её поток не закончил общую историю.
                    if worker is None or worker.done():
                        _chat_lock.release()
                    else:
                        worker.add_done_callback(lambda _: _chat_lock.release())

            return StreamingResponse(chunks(), media_type="application/x-ndjson", headers=NO_CACHE)

        @app.get("/api/install")
        async def api_install(request: Request):
            """Отпечаток этой установки — только с этого компьютера.

            Им сверяются две копии Трубы на одном компьютере: «сервер на
            порту отвечает» мало, надо «отвечает *моя* копия»
            (`core/instance.py::our_copy`). Заодно им `Труба.vbs` узнаёт,
            что пульт на этом порту — его собственный, и показывает окно,
            а не заводит второй процесс.
            """
            if not _local_secret(request):
                return JSONResponse(
                    {"ok": False, "error": "доступен только с этого компьютера"},
                    status_code=403)
            from core import instance

            return PlainTextResponse(instance.install_id(), headers=NO_CACHE)

        @app.get("/version")
        async def version():
            # Страница дёргает этот адрес раз в полминуты: так она заметит
            # правку, даже если соединение висит с прошлого запуска.
            return PlainTextResponse(self.page_version(), headers=NO_CACHE)

        # --- Веб-пульт: локальный REST ------------------------------------
        def _local(request: Request) -> bool:
            return client_is_local(request)

        def _local_secret(request: Request) -> bool:
            """Не отдаём ключи странице с чужим Host/Origin (DNS rebinding)."""
            from urllib.parse import urlsplit

            if not _local(request):
                return False
            host = request.headers.get("host", "").lower()
            allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}",
                       f"[::1]:{self.port}", "testserver", "testclient"}
            if host not in allowed:
                return False
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != "http" or parsed.netloc.lower() != host:
                    return False
            return True

        def _deny():
            return JSONResponse(
                {"ok": False, "error": "доступно только с этого компьютера"},
                status_code=403)

        def _no_runtime():
            return JSONResponse(
                {"ok": False, "error": "пульт не запущен"},
                status_code=503)

        def _fail(exc: Exception, code: int = 500):
            msg = str(exc).strip().replace("\n", " ")
            if len(msg) > 300:
                msg = msg[:300] + "…"
            detail = f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__
            return JSONResponse({"ok": False, "error": detail}, status_code=code)

        # Журнал событий — это дословно расслышанные фразы: только отсюда.
        @app.get("/api/runtime")
        async def api_runtime(request: Request, after: int = 0):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                state = await asyncio.to_thread(rt.voice_state)
                items = await asyncio.to_thread(rt.events, int(after or 0))
                overview = await asyncio.to_thread(rt.overview)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "voice": state, "events": items, "overview": overview})

        @app.get("/api/voice/events")
        async def api_voice_events(request: Request, after: int = 0):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                items = await asyncio.to_thread(rt.events, int(after or 0))
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "events": items})

        @app.post("/api/voice/toggle")
        async def api_voice_toggle(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                state = await asyncio.to_thread(rt.voice_toggle)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "voice": state})

        @app.post("/api/voice/hush")
        async def api_voice_hush(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                state = await asyncio.to_thread(rt.voice_hush)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "voice": state})

        @app.post("/api/voice/mode")
        async def api_voice_mode(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с полем value"},
                    status_code=400)
            value = body.get("value", "") if isinstance(body, dict) else ""
            if value not in ("always", "name", "off"):
                return JSONResponse(
                    {"ok": False, "error": "режим бывает always/name/off"},
                    status_code=400)
            try:
                state = await asyncio.to_thread(rt.voice_mode, value)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "voice": state})

        @app.get("/api/logs")
        async def api_logs(request: Request, limit: int = 200, after: int = 0):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                snapshot = await asyncio.to_thread(rt.logs_from, after, limit)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **snapshot})

        @app.get("/api/logs/download")
        async def api_logs_download(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            path = rt._log_path
            if not path.is_file():
                return JSONResponse(
                    {"ok": False, "error": "журнал пока пуст"}, status_code=404)
            return FileResponse(
                path, media_type="text/plain; charset=utf-8",
                filename=f"truba-log-{time.strftime('%Y-%m-%d')}.txt",
                headers=NO_CACHE,
            )

        @app.get("/api/usage")
        async def api_usage(request: Request):
            """Расход токенов и денег: сеанс, сегодня, неделя. Только с компа."""
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                сводка = await asyncio.to_thread(rt.usage_summary)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **сводка}, headers=NO_CACHE)

        # В снимке настроек — характер, память о хозяине и ключ телефона.
        @app.get("/api/settings")
        async def api_settings_get(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                snap = await asyncio.to_thread(rt.settings_snapshot)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **snap})

        @app.post("/api/settings")
        async def api_settings_post(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON-объект"},
                    status_code=400)
            if not isinstance(body, dict):
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON-объект"},
                    status_code=400)
            try:
                res = await asyncio.to_thread(rt.save_settings, body)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=400)
            return JSONResponse(res)

        # Только чтение: проверить, что это настоящий процесс пульта.
        # Никакого HTTP-пути для включения питания нет.
        @app.get("/api/power/status")
        async def api_power_status(request: Request):
            if not _local_secret(request):
                return _deny()
            from core import power
            return JSONResponse({"ok": True, "runtime": power.live_runtime()},
                                headers=NO_CACHE)

        # Только локальный пульт читает последние реплики.
        # Служебные поля и произвольные пути наружу не отдаём.
        @app.get("/api/settings/history")
        async def api_settings_history(request: Request):
            if not _local_secret(request):
                return _deny()
            from core import memory

            try:
                turns = await asyncio.to_thread(memory.load_history, 0)
            except Exception as exc:
                return _fail(exc)
            items = [
                {"role": one.get("role"), "content": one.get("content"),
                 "at": one.get("at", "")}
                for one in turns
                if isinstance(one.get("content"), str)
            ]
            return JSONResponse(
                {"ok": True, "turns": items, "limit": config.HISTORY_TURNS},
                headers=NO_CACHE)

        @app.post("/api/settings/clear-history")
        async def api_settings_clear(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(
                    rt.save_settings, {"clear_history": True})
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.post("/api/settings/test-provider")
        async def api_settings_test_provider(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен провайдер")
                result = await asyncio.to_thread(
                    rt.test_provider, body.get("provider"), body.get("key", ""),
                    body.get("model"))
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(result)

        @app.post("/api/web/test")
        async def api_web_test(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                mode = body.get("mode") if isinstance(body, dict) else None
                result = await asyncio.to_thread(rt.web_test, mode)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(result)

        @app.post("/api/settings/models")
        async def api_settings_models(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Нужен сервис")
                result = await asyncio.to_thread(
                    rt.list_models, body.get("provider"), body.get("key", ""))
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(result, headers=NO_CACHE)

        @app.post("/api/settings/key/reveal")
        async def api_settings_key_reveal(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Нужен сервис")
                result = await asyncio.to_thread(rt.reveal_api_key, body.get("provider"))
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(result, headers=NO_CACHE)

        @app.post("/api/settings/voice-preview")
        async def api_settings_voice_preview(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужны настройки голоса")
                wav = await asyncio.to_thread(rt.preview_voice, body)
            except (ValueError, TypeError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return Response(wav, media_type="audio/wav", headers=NO_CACHE)

        @app.get("/api/voice-samples")
        async def api_voice_samples(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                samples = await asyncio.to_thread(rt.voice_samples)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "samples": samples})

        @app.get("/api/voice-samples/{name}/audio")
        async def api_voice_sample_audio(name: str, request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                path = await asyncio.to_thread(rt.voice_sample_path, name)
            except ValueError as exc:
                return _fail(exc, 404)
            except Exception as exc:
                return _fail(exc)
            return FileResponse(path, media_type="audio/wav", headers=NO_CACHE)

        @app.post("/api/voice-samples")
        async def api_voice_sample_add(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            import tempfile

            from starlette.datastructures import UploadFile

            try:
                form = await request.form()
                upload = form.get("file")
                if not isinstance(upload, UploadFile):
                    raise ValueError("Выбери файл с записью голоса")
                filename = Path(str(upload.filename or "").replace("\\", "/")).name
                extension = Path(filename).suffix.lower()
                if extension not in {".wav", ".mp3", ".flac", ".ogg", ".m4a"}:
                    raise ValueError("Подойдёт WAV, MP3, FLAC, OGG или M4A")
                manual = form.get("manual") == "1"
                start = float(form.get("start")) if manual else None
                seconds = float(form.get("seconds")) if manual else None
                with tempfile.TemporaryDirectory(prefix="truba-voice-") as folder:
                    source = Path(folder) / filename
                    size = 0
                    with source.open("wb") as output:
                        while chunk := await upload.read(1024 * 1024):
                            size += len(chunk)
                            if size > 50 * 1024 * 1024:
                                raise ValueError("Файл больше 50 МБ — возьми запись покороче")
                            output.write(chunk)
                    if not size:
                        raise ValueError("Файл пустой")
                    result = await asyncio.to_thread(
                        rt.voice_sample_add, source, start, seconds)
            except (ValueError, TypeError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "sample": result})

        @app.delete("/api/voice-samples/{name}")
        async def api_voice_sample_delete(name: str, request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                result = await asyncio.to_thread(rt.voice_sample_delete, name)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **result})

        @app.get("/api/apps")
        async def api_apps_get(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                items = await asyncio.to_thread(rt.apps_snapshot)
            except Exception as exc:
                return _fail(exc)
            # Размер экрана телефона — рядом со списком: пульт грузит их одной
            # подгрузкой, а макет без него рисует значки не того размера.
            return JSONResponse({"ok": True, "apps": items,
                                 "viewport": self.viewport})

        @app.get("/api/apps/running")
        async def api_apps_running(request: Request):
            if not _local(request):
                return _deny()
            from core import running_apps

            try:
                items = await asyncio.to_thread(running_apps.list_running)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "apps": items})

        @app.post("/api/apps/preview")
        async def api_apps_preview(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                items = body.get("apps") if isinstance(body, dict) else body
                preview = await asyncio.to_thread(rt.apps_preview, items)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "items": preview})

        @app.post("/api/apps")
        async def api_apps_post(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON-список"},
                    status_code=400)
            items = body.get("apps", body) if isinstance(body, dict) else body
            try:
                res = await asyncio.to_thread(rt.apps_save, items)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=400)
            return JSONResponse(res)

        @app.post("/api/apps/icon")
        async def api_apps_icon(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с картинкой"},
                    status_code=400,
                )
            if not isinstance(body, dict):
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с картинкой"},
                    status_code=400,
                )
            try:
                res = await asyncio.to_thread(
                    rt.apps_icon_upload, body.get("id"), body.get("data")
                )
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, status_code=200 if res.get("ok") else 400)

        @app.post("/api/apps/check")
        async def api_apps_check(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "нужен список программ"}, status_code=400)
            items = body.get("apps") if isinstance(body, dict) else body
            try:
                res = await asyncio.to_thread(rt.apps_check, items)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, status_code=200 if res.get("ok") else 400)

        @app.post("/api/apps/launch")
        async def api_apps_launch(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с полем id"},
                    status_code=400)
            app_id = body.get("id", "") if isinstance(body, dict) else ""
            try:
                res = await asyncio.to_thread(rt.apps_launch, app_id)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=404)
            return JSONResponse(res)

        @app.post("/api/check/{kind}")
        async def api_check_start(kind: str, request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.check_start, kind)
            except Exception as exc:
                return _fail(exc)
            if "error" in res:
                return JSONResponse(
                    {"ok": False, "error": res["error"]}, status_code=400)
            return JSONResponse({"ok": True, **res})

        # --- Распознавание речи -------------------------------------------
        # Просмотр состояния — `_local`, проба голосом — `_local_secret`:
        # запись с микрофона хозяина не должна включаться со страницы с
        # чужим Host.

        @app.get("/api/stt")
        async def api_stt(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.stt_state)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.post("/api/stt/test")
        async def api_stt_test(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.stt_test)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=400)
            return JSONResponse(res, headers=NO_CACHE)

        # --- Звук: микрофон, колонки, проверка ----------------------------
        #
        # Списки и уровень — `_local`, запись с микрофона и прослушивание —
        # `_local_secret`: запись голоса хозяина не должна включаться со
        # страницы с чужим Host.

        @app.get("/api/audio")
        async def api_audio(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.audio_settings)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.get("/api/audio/level")
        async def api_audio_level(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            # `preview=1` — шаг 4 мастера: полоска показывает уровень того
            # микрофона, который выбран в списке, даже если голос выключен.
            # Обычная вкладка «Звук» параметр не шлёт и голос не трогает.
            if request.query_params.get("preview") in ("1", "true"):
                имя = request.query_params.get("mic_name", "")
                try:
                    вход = int(request.query_params.get("mic_channel", 0) or 0)
                except ValueError:
                    вход = -1
                try:
                    res = await asyncio.to_thread(rt.audio_level_preview, имя, вход)
                except Exception as exc:
                    return _fail(exc)
                if not res.get("ok"):
                    return JSONResponse(res, status_code=200)
                return JSONResponse(res, headers=NO_CACHE)
            try:
                res = await asyncio.to_thread(rt.audio_level)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=200)
            return JSONResponse(res, headers=NO_CACHE)

        @app.post("/api/audio/test")
        async def api_audio_test(request: Request, probe: int = 0):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                # `probe=1` — проба мастера: сервер сам гасит голос на запись и
                # включает обратно. Обычная форма настроек параметр не шлёт и
                # голос не трогает.
                res = await asyncio.to_thread(rt.audio_test, bool(probe))
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=400)
            return JSONResponse(res, headers=NO_CACHE)

        @app.get("/api/hardware")
        async def api_hardware(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.hardware_info)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.get("/api/weather/find")
        async def api_weather_find(request: Request, q: str = ""):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.weather_find, q)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.post("/api/voices/install")
        async def api_voices_install(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                result = await asyncio.to_thread(rt.voices_install)
            except Exception as exc:
                return _fail(exc)
            if not result.get("ok"):
                return JSONResponse(result, status_code=409)
            return JSONResponse(result)

        @app.get("/api/voices/status")
        async def api_voices_status(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.voices_status)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.get("/api/phone/qr")
        async def api_phone_qr(request: Request, url: str = ""):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                return await asyncio.to_thread(rt.phone_qr, url)
            except ImportError:
                # segno ставится вместе с пультом, но пульт должен работать и
                # без него: мастер покажет адрес текстом.
                return JSONResponse({"ok": False,
                                     "error": "нет библиотеки segno"},
                                    status_code=501)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)

        @app.post("/api/check/enroll/start")
        async def api_check_enroll_start(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.enroll_start)
            except Exception as exc:
                return _fail(exc)
            if "error" in res:
                code = 409 if "уже идёт" in res["error"] else 400
                if "микрофон занят" in res["error"]:
                    code = 409
                return JSONResponse(
                    {"ok": False, "error": res["error"]}, status_code=code)
            return JSONResponse({"ok": True, **res})

        @app.post("/api/check/enroll/stop")
        async def api_check_enroll_stop(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.enroll_stop)
            except Exception as exc:
                return _fail(exc)
            if "error" in res:
                return JSONResponse(
                    {"ok": False, "error": res["error"]}, status_code=400)
            return JSONResponse({"ok": True, **res})

        @app.get("/api/check/job/{jid}")
        async def api_check_job(jid: str):
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                job = await asyncio.to_thread(rt.job_state, jid)
            except Exception as exc:
                return _fail(exc)
            if job is None:
                return JSONResponse(
                    {"ok": False, "error": "нет такой проверки"},
                    status_code=404)
            return JSONResponse({"ok": True, "job": job})

        @app.post("/api/check/threshold")
        async def api_check_threshold(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "нужен JSON с полем value"},
                    status_code=400)
            value = body.get("value") if isinstance(body, dict) else None
            try:
                res = await asyncio.to_thread(rt.apply_threshold, value)
            except Exception as exc:
                return _fail(exc)
            if not res.get("ok"):
                return JSONResponse(res, status_code=400)
            return JSONResponse(res)

        # --- Поддержать автора ------------------------------------------
        # Открывается только адрес из config.SUPPORT_URL и только http(s):
        # со страницы приходит одно нажатие, а не адрес.

        def _support_links() -> dict:
            return {
                "donate": str(getattr(config, "SUPPORT_URL", "") or "").strip(),
                "telegram": str(getattr(config, "AUTHOR_TELEGRAM", "") or "").strip(),
                "youtube": str(getattr(config, "AUTHOR_YOUTUBE", "") or "").strip(),
            }

        @app.get("/api/support")
        async def api_support():
            links = _support_links()
            return JSONResponse({
                "ok": True,
                "name": str(getattr(config, "AUTHOR_NAME", "") or ""),
                "text": str(getattr(config, "SUPPORT_TEXT", "") or ""),
                "links": {key: bool(value) for key, value in links.items()},
            }, headers=NO_CACHE)

        @app.post("/api/support/open")
        async def api_support_open(request: Request):
            if not _local(request):
                return _deny()
            try:
                body = await request.json()
            except Exception:
                body = {}
            what = body.get("what", "donate") if isinstance(body, dict) else "donate"
            url = _support_links().get(str(what), "")
            if not url.lower().startswith(("https://", "http://")):
                return JSONResponse({"ok": False, "error": "ссылка появится позже"})
            try:
                await asyncio.to_thread(os.startfile, url)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True})

        # --- Страница ключей сервиса (мастер первого запуска) ---------------
        #
        # По образцу `/api/support/open`: адрес берётся только из таблицы
        # `config.KEY_PAGES`, а не из запроса — иначе страница с телефона
        # (или с чужого Host) открыла бы что угодно от имени Трубы.

        @app.post("/api/open/key-page")
        async def api_open_key_page(request: Request):
            if not _local_secret(request):
                return _deny()
            try:
                body = await request.json()
            except Exception:
                body = {}
            what = body.get("provider", "") if isinstance(body, dict) else ""
            url = config.KEY_PAGES.get(str(what), "")
            if not url:
                return JSONResponse(
                    {"ok": False,
                     "error": "у этого сервиса нет страницы ключей"},
                    status_code=400)
            try:
                await asyncio.to_thread(os.startfile, url)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True})

        # --- О программе и обновление с GitHub -------------------------
        #
        # Проверка и чтение — `_local`, установка и перезапуск — только
        # `_local_secret`: с телефона и со страницы с чужим Host обновление
        # запускать нельзя: это запись в файлы проекта.

        @app.get("/api/about")
        async def api_about(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                about = await asyncio.to_thread(rt.about)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **about}, headers=NO_CACHE)

        @app.post("/api/update/check")
        async def api_update_check(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                result = await asyncio.to_thread(rt.update_check)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **result}, headers=NO_CACHE)

        @app.post("/api/update/install")
        async def api_update_install(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                result = await asyncio.to_thread(rt.update_install)
            except Exception as exc:
                return _fail(exc)
            if not result.get("ok"):
                return JSONResponse(result, status_code=409)
            return JSONResponse(result)

        @app.get("/api/update/status")
        async def api_update_status(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                state = await asyncio.to_thread(rt.update_state)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({
                "ok": True,
                "running": bool(state.get("running")),
                "steps": state.get("steps", []),
                "result": state.get("result"),
                "last": state.get("last"),
            }, headers=NO_CACHE)

        @app.post("/api/update/restart")
        async def api_update_restart(request: Request):
            if not _local_secret(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                result = await asyncio.to_thread(rt.update_restart)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(result)

        # --- Команды: инструкция, что сказать и что будет ---------------
        # Страница «Команды» в пульте. Данные собирает `commands_guide`
        # из той же таблицы, по которой Труба понимает речь, — здесь их
        # только отдаём, иначе инструкция разойдётся с поведением.

        @app.get("/api/commands")
        async def api_commands():
            from core import commands as голосовые
            try:
                сводка = голосовые.commands_guide()
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, **сводка}, headers=NO_CACHE)

        # Готовые характеры для «Настройки → Характер»: название, пояснение и
        # текст — пульт подставляет его в поле характера по нажатию.
        @app.get("/api/personas")
        async def api_personas(request: Request):
            if not _local(request):
                return _deny()
            from core import personas
            try:
                список = await asyncio.to_thread(personas.catalog, True)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse({"ok": True, "presets": список,
                                 "default": personas.DEFAULT}, headers=NO_CACHE)

        # --- Заметки: чтение, удаление, открыть в проводнике -------------
        # Только с компа (`_local`): папка заметок — это его личные мысли,
        # а телефон лежит в сети, и адрес пульта виден всем в ней.
        # Имена из запроса идут только через core/notes.py: путь с `..`
        # или абсолютный путь в имени там отклоняются.

        @app.get("/api/notes")
        async def api_notes(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.notes_list)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.get("/api/notes/topic")
        async def api_notes_topic(request: Request, section: str = "", topic: str = ""):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.notes_topic, section, topic)
            except ValueError as exc:
                return _fail(exc, 400)
            except FileNotFoundError:
                return JSONResponse(
                    {"ok": False, "error": "такой темы нет"}, status_code=404)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.post("/api/notes/delete")
        async def api_notes_delete(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен JSON-объект")
                res = await asyncio.to_thread(rt.notes_delete, body)
            except (ValueError, FileNotFoundError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.post("/api/notes/edit")
        async def api_notes_edit(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен JSON-объект")
                res = await asyncio.to_thread(rt.notes_edit, body)
            except (ValueError, FileNotFoundError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.post("/api/notes/rename")
        async def api_notes_rename(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен JSON-объект")
                res = await asyncio.to_thread(rt.notes_rename, body)
            except (ValueError, FileNotFoundError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.post("/api/notes/add")
        async def api_notes_add(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен JSON-объект")
                res = await asyncio.to_thread(rt.notes_add, body)
            except (ValueError, FileNotFoundError) as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.post("/api/notes/open")
        async def api_notes_open(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    body = {}
                res = await asyncio.to_thread(rt.notes_open, body)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        # --- Напоминания и таймеры: что стоит и чем отменить ---------------
        # Только с компа (`_local`) — как заметки: расписание хозяина видно
        # всему, кто знает адрес пульта, а телефон лежит в сети.
        #
        # Ставит их модель голосом; здесь только чтение и отмена. Никаких
        # регулярок времени: `due` уезжает ISO-строкой с поясом, а «через
        # N мин» пульт считает у себя.

        @app.get("/api/reminders")
        async def api_reminders(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                res = await asyncio.to_thread(rt.reminders_list)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res, headers=NO_CACHE)

        @app.post("/api/reminders/cancel")
        async def api_reminders_cancel(request: Request):
            if not _local(request):
                return _deny()
            rt = getattr(self, "runtime", None)
            if rt is None:
                return _no_runtime()
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("нужен JSON-объект")
                res = await asyncio.to_thread(rt.reminders_cancel, body)
            except ValueError as exc:
                return _fail(exc, 400)
            except Exception as exc:
                return _fail(exc)
            return JSONResponse(res)

        @app.websocket("/ws")
        async def socket(ws: WebSocket):
            self._emit("ws_attempt", None)
            отказ = ws_refusal(ws)
            if отказ:
                self._emit("ws_refused", отказ)
                if отказ == "нет ключа":
                    # Телефону — внятный ответ: страница скажет, что его надо
                    # привязать заново по QR-коду, а не будет молча
                    # переподключаться.
                    await ws.accept()
                    await ws.send_text(json.dumps({"type": "need_key"}))
                    await ws.close(code=4401)
                else:
                    await ws.close(code=1008)
                return
            await ws.accept()
            self._clients.add(ws)
            self._emit("connected", None)
            # Первым делом сообщаем актуальную версию страницы: если телефон
            # держит старую из памяти браузера, он перезагрузится сам.
            await ws.send_text(
                json.dumps({"type": "version", "value": self.page_version()})
            )
            # Сразу за ним — погода из кеша: телефон рисует её, не дожидаясь
            # первого обновления. Из сети здесь ничего не берётся: файл маленький,
            # а поход в Open-Meteo из потока сервера заморозил бы все телефоны.
            from core import weather

            заготовка = weather.cached()
            if заготовка is not None:
                await ws.send_text(json.dumps(заготовка))
            # И текущую громкость её голоса — как погоду: телефон поднялся с
            # телефонным звонком, и показывать наугад нельзя. Уровень
            # меняют ещё и голосом, и ползунком, так что берём его у нас.
            await ws.send_text(json.dumps({
                "type": "volume",
                "value": int(getattr(config, "VOICE_VOLUME", 10)),
            }))
            # И тему оформления: телефон открыли из закладки или он перезагрузил
            # страницу, а перекрасить его без сервера нечем. Меняет тему пульт,
            # на телефоне кнопок нет.
            await ws.send_text(json.dumps({
                "type": "theme",
                "value": _тема(),
            }))
            try:
                while True:
                    message = await ws.receive_text()
                    data = json.loads(message)
                    self._on_message(ws, data)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                self._forget(ws)

        async def run():
            self._loop = asyncio.get_running_loop()
            self._ready.set()
            server = uvicorn.Server(
                uvicorn.Config(
                    app, host="0.0.0.0", port=self.port, log_level="warning"
                )
            )
            await server.serve()

        try:
            asyncio.run(run())
        except Exception as exc:
            # Поток умирает молча, а окно продолжает ждать телефон — сообщаем.
            self._ready.set()
            self._emit("failed", str(exc))

    def _on_message(self, ws, data) -> None:
        """Сообщение от телефона.

        `{"type": "audio", "ready": true}` — палец коснулся экрана и звук
        запустился. До этого момента соединение считается на связи (кнопки,
        режимы, погода идут), но речь в него не отправляем: играть там
        некому. Наружу уходят два разных события: `audio_ready` и
        `audio_off`, чтобы журнал пульта писал, что именно изменилось.
        """
        if not isinstance(data, dict):
            self._emit("unknown", data)
            return
        kind = data.get("type", "unknown")
        if kind == "viewport":
            # `{"type": "viewport", "w": ..., "h": ...}` — телефон сообщил свой
            # размер экрана. Мусор и неправдоподобные числа просто игнорируем:
            # из-за чужого сообщения макет в пульте прыгать не должен.
            размер = _viewport_pair(data.get("w"), data.get("h"))
            if размер is not None and размер != self.viewport:
                self._viewport = размер
                _write_viewport(размер["w"], размер["h"])
                self._emit("viewport", размер)
            return
        if kind == "audio":
            ready = data.get("ready") is True
            if ready:
                self._audio.add(ws)
            else:
                self._audio.discard(ws)
            self._emit("audio_ready" if ready else "audio_off", ready)
            return
        self._emit(kind, data)

    def _forget(self, ws) -> None:
        """Телефон отвалился. Звук на его месте тоже кончился."""
        self._clients.discard(ws)
        self._audio.discard(ws)
        self._emit("disconnected", None)

    def attach(self, listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def detach(self, listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, kind: str, payload) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                continue

    # --- Отправка ---------------------------------------------------------

    def _broadcast(self, sender, targets=None) -> None:
        """Шлёт всем подключённым телефонам из потока сервера.

        `targets` — конкретный список получателей. Речь так отправляется
        только тем, у кого включён звук (`_audio`): соединение без звука
        не отчитается о проигранном, и `PhoneSpeaker` ждал бы его до
        запасного таймера, оставив микрофон заглушенным.
        """
        clients = self._clients if targets is None else targets
        if self._loop is None or not clients:
            return
        for client in list(clients):
            asyncio.run_coroutine_threadsafe(sender(client), self._loop)

    def send_state(self, state: str, text: str = "") -> None:
        payload = json.dumps({"type": "state", "state": state, "text": text})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_line(self, who: str, text: str) -> None:
        payload = json.dumps({"type": "line", "who": who, "text": text})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_audio(self, wave: np.ndarray, sample_rate: int) -> None:
        data = to_wav(wave, sample_rate)
        self._broadcast(lambda ws: ws.send_bytes(data), self._audio)

    def send_stop(self) -> None:
        payload = json.dumps({"type": "stop"})
        # Останавливаем и тех, кто звук уже выключил: у них в очереди могли
        # остаться куски, иначе они зазвучат после касания.
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_mode(self, mode: str) -> None:
        """Подсвечивает на телефоне текущий режим слуха."""
        payload = json.dumps({"type": "mode", "value": mode})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_volume(self, value: int) -> None:
        """Новый уровень громкости её голоса — всем телефонам сразу.

        Источник правды один: уровень меняют голосом, ползунком в пульте и
        кнопками на телефоне, и все три должны показывать одно и то же.
        """
        payload = json.dumps({"type": "volume", "value": int(value)})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_theme(self, value: str) -> None:
        """Новая тема оформления — всем телефонам сразу.

        Источник правды один: тему меняют в пульте, и телефон только показывает
        её. На самом телефоне кнопок темы нет, поэтому сообщение — единственный
        способ узнать о смене, не перезагружая страницу.
        """
        тема = value if value in ("dark", "light") else config.THEME
        payload = json.dumps({"type": "theme", "value": тема})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_system(self, data: dict) -> None:
        """Нагрузка железа для панели на телефоне."""
        payload = json.dumps({"type": "system", "data": data})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_apps(self, apps: list, layout: dict | None = None) -> None:
        """Список программ, которые телефон может запустить.

        Вместе со списком уходит вид сетки (`core/settings.py::grid_layout`):
        колонки, ряды, стиль плитки и подписи. Без него телефон считал бы
        сам и рисовал бы не так, как хозяин просил в пульте.
        """
        if layout is None:
            from core import settings

            layout = settings.grid_layout()
        payload = json.dumps({"type": "apps", "items": apps, "layout": layout})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_actions(self, items: list) -> None:
        """Нижние кнопки телефона: `{slot, kind, title, icon|image, …}`.

        Кнопок всегда четыре, но `none` в список не попадает — телефон сдвинет
        остальные вправо. Ни сочетаний клавиш, ни имён программ в сообщении
        нет: телефон знает только номер кнопки, а что она делает, решает
        сервер по своим настройкам.
        """
        payload = json.dumps({"type": "actions", "items": list(items or [])})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_menu_state(self, app: str, on: dict) -> None:
        """Подсветка переключателей подменю: что сейчас «выключено».

        Источник правды — сервер: телефон по этому сообщению рисует плитки и
        не решает сам, что включено. `on` — словарь `{ключ пункта: bool}`.
        """
        payload = json.dumps({"type": "menu_state", "app": app, "on": on})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_weather(self, data: dict) -> None:
        """Погода в городе для верхней полосы телефона.

        `{"off": True}` — погоды больше нет (город сняли): страница по этой
        метке прячет плашку, иначе старый прогноз висел бы до следующего
        захода.
        """
        payload = json.dumps({"type": "weather", **data})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_shot(self, image: str, caption: str, hide: float = 15.0) -> None:
        """Снимок экрана компа — телефон покажет его поверх всего.

        `hide` — сколько секунд он там лежит. Раньше снимок висел, пока хозяин
        не коснётся (28.09: «скрин не скрывается сам»), и телефон молчал:
        секунды шлёт сервер, страница по ним ставит таймер.
        """
        payload = json.dumps({"type": "shot", "image": image, "caption": caption,
                              "hide": float(hide)})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_toast(self, text: str, ok: bool = True) -> None:
        """Короткая надпись на телефоне: получилось или нет."""
        payload = json.dumps({"type": "toast", "text": text, "ok": ok})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_search_result(
        self, query: str, answer: str, sources: list | None = None,
        asked: str = "",
    ) -> None:
        """Карточка поиска на телефоне: запрос, сказанное вслух и откуда.

        `query` — то, что реально ушло в поисковик (запросы пишет модель
        сама), `asked` — слова хозяина. Телефон открывает в браузере `query`,
        а без него показывает `asked`: касание строки должно приводить к тому
        же поиску, что и сделала она.

        Список источников запоминаем: по нему телефон потом просит открыть
        источник **по номеру** (`open_source`) — адрес с телефона не принимаем,
        иначе он заставил бы комп открыть что угодно (см. `WebRuntime`).
        """
        список = [s for s in (sources or []) if isinstance(s, dict)]
        self.last_sources = список
        payload = json.dumps({
            "type": "search_result",
            "query": query,
            "asked": asked or query,
            "answer": answer,
            "sources": список,
        })
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_search_fail(self, text: str) -> None:
        """Поиск не вышел: телефон должен сказать об этом, а не молчать."""
        payload = json.dumps({"type": "search_fail", "text": text})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_sound(self, name: str) -> None:
        """Короткий сигнал на телефоне.

        Надписи он не видит: телефон стоит сбоку, а человек смотрит в
        монитор и в игру. Звук доходит всегда.

        Имена: shutter — снимок экрана, moment — момент записан,
        open — разговор открыт, close — разговор закрыт, fail — не вышло.
        Сами звуки телефон синтезирует на месте, файлов не нужно.
        """
        payload = json.dumps({"type": "sound", "name": name})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_search_hum(self, on: bool, gain: float = 1.0) -> None:
        """Тихий фон, пока она ищет в интернете.

        Телефон играет его у себя по кругу, пока не придёт `on: false`:
        держать соединение ради звука незачем, а сам звук он уже скачал
        (`GET /search_hum.wav`). Громкость — как у её голоса.
        """
        payload = json.dumps({"type": "search_hum", "on": bool(on),
                              "gain": float(gain)})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_dictation(self, on: bool) -> None:
        """Идёт ли запись диктовки: край телефона тлеет янтарём."""
        payload = json.dumps({"type": "dictation", "on": bool(on)})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_notes(self, sections: list) -> None:
        """Разделы и темы для телефона: имена, счётчики, даты — и ничего больше.

        Путей к файлам тут нет намеренно: телефон — это вторая копия
        содержимого, а не второй доступ к папке хозяина.
        """
        payload = json.dumps({"type": "notes", "sections": list(sections or [])})
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_note_topic(self, data: dict) -> None:
        """Одна тема целиком — её телефон рисует справа.

        Раздел и тема возвращаются вместе с записями: телефон сверяет их со
        своей выбранной темой и молча игнорирует запоздавший ответ. При
        ошибке чтения вместо записей едет `error` — телефон напишет об этом
        словами, а не останется с пустым экраном.
        """
        data = data or {}
        if data.get("error"):
            payload = json.dumps({
                "type": "note_topic",
                "section": data.get("section", ""),
                "topic": data.get("topic", ""),
                "error": str(data["error"]),
            })
        else:
            payload = json.dumps({
                "type": "note_topic",
                "section": data.get("section", ""),
                "topic": data.get("topic", ""),
                "entries": list(data.get("entries", []) or []),
            })
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_reminders(self, items: list, error: str = "") -> None:
        """Напоминания и таймеры для телефона: то же, что видит Панель.

        Только то, что рисует строка, и **только авторизованным телефонам**:
        список лежит в личном файле хозяина, и в сеть он попадает лишь после
        проверки Origin и ключа в `ws_refusal`. Телефоны, которые её не прошли,
        сокета не имеют вовсе, а локальный `GET /api/reminders` с телефона
        недоступен — поэтому путь один и закрытый.

        Ошибка чтения едет тем же сообщением: телефон напишет об этом словами,
        а не останется с пустым списком.
        """
        payload = json.dumps({
            "type": "reminders",
            "items": list(items or []),
            "error": str(error or ""),
        })
        self._broadcast(lambda ws: ws.send_text(payload))

    def send_search_history(self, items: list, error: str = "") -> None:
        """История реальных поисков для телефона: запросы и время.

        Строки приходят из `core/search_history.py` и несут только слова,
        ушедшие в поисковик, с датой: ни источников, ни ответов, ни ключей
        там быть не может и не должно. Телефон по строке шлёт `open_search`
        с самим запросом — адрес собирает сервер.

        Как и напоминания, только авторизованным телефонам.
        """
        payload = json.dumps({
            "type": "search_history",
            "items": list(items or []),
            "error": str(error or ""),
        })
        self._broadcast(lambda ws: ws.send_text(payload))


class PhoneSpeaker:
    """Рот, вынесенный в телефон. Интерфейс тот же, что у колонок.

    Ждать окончания речи приходится по отчёту телефона: сервер не знает,
    сколько на самом деле играет звук на той стороне. Если телефон молчит
    дольше разумного — не ждём вечно, иначе микрофон останется заглушенным.
    """

    # Телефон ставит куски встык по своим часам — можно слать предложение
    # частями, пока оно ещё синтезируется.
    gapless = True

    def __init__(self, server: PhoneServer, gap: float = 0.0):
        self.server = server
        self.gap = gap
        self.idle = threading.Event()
        self.idle.set()

        self._pending = 0
        self._lock = threading.Lock()
        self._last_sent = 0.0
        self._total_audio = 0.0

        # Подписываемся на сервер сами. Раньше отчёты телефона проксировал
        # голосовой цикл, и стоило забыть эту связь — речь считалась
        # незаконченной, а микрофон оставался заглушенным.
        server.attach(self._on_server_event)

    def _on_server_event(self, kind: str, payload) -> None:
        if kind == "played":
            self.on_played()
        elif kind in ("disconnected", "audio_off") and not self.server.audio_ready:
            # Телефон ушёл посреди речи или выключил звук — ждать отчёта
            # больше не от кого. Иначе микрофон остался бы заглушенным до
            # запасного таймера в `wait()`.
            with self._lock:
                self._pending = 0
                self.idle.set()

    def say(self, wave: np.ndarray, sample_rate: int, gap: bool = True) -> None:
        """Шлёт кусок речи. gap=False — кусок не последний в предложении.

        Паузу после предложения теперь добавляем сами: телефон играет куски
        встык, а раньше щель между ними давала сама его очередь.

        Если на телефоне не включён звук, кусок всё равно не считаем: отчёта
        `played` от него не будет (страница не играет), и микрофон остался бы
        заглушенным до запасного таймера в `wait()`.
        """
        if not self.server.audio_ready:
            return
        if gap and self.gap > 0:
            silence = np.zeros(int(self.gap * sample_rate), dtype=np.float32)
            wave = np.concatenate([np.asarray(wave, dtype=np.float32), silence])
        with self._lock:
            self._pending += 1
            self._total_audio += len(wave) / sample_rate
            self._last_sent = time.monotonic()
            self.idle.clear()
        self.server.send_audio(wave, sample_rate)

    def pause(self, seconds: float, sample_rate: int = 24000) -> None:
        """Тишина после предложения, присланного кусками."""
        if seconds > 0:
            self.say(np.zeros(int(seconds * sample_rate), dtype=np.float32), sample_rate, gap=False)

    def on_played(self) -> None:
        """Телефон отчитался, что доиграл очередной кусок."""
        with self._lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self.idle.set()

    def interrupt(self) -> None:
        with self._lock:
            self._pending = 0
            self._total_audio = 0.0
            self.idle.set()
        self.server.send_stop()

    def wait(self, timeout: float | None = None) -> bool:
        """Ждёт, пока телефон доиграет речь.

        Верхняя граница — реальная длительность звука плюс небольшой запас
        на сеть. Раньше запас был в пять секунд, и если отчёт от телефона
        терялся, человек каждый раз ждал эти секунды молча.
        """
        with self._lock:
            limit = self._total_audio + 1.5
        done = self.idle.wait(timeout if timeout is not None else limit)
        with self._lock:
            self._total_audio = 0.0
            self._pending = 0
            self.idle.set()
        return done

    def close(self) -> None:
        self.server.detach(self._on_server_event)
        self.interrupt()
