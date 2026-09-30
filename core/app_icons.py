r"""Значки для кнопок на телефоне.

Рисованные иконки не узнаются с первого взгляда, а привычный логотип
Firefox или Discord читается мгновенно. Берём значок прямо из программы
и отдаём телефону картинкой.

Откуда брать — решает человек в пульте, поле `icon_source` в apps.json:

    "exe"   — из самой программы, как показывает проводник
    "file"  — из картинки, которую выбрали руками
    "drawn" — рисованный; страница телефона нарисует его сама

Про размер. Windows отдаёт значок несколькими способами, и самый
очевидный — самый плохой: DrawIcon рисует жёстко 32 на 32, сколько ему
холста ни дай. Отсюда брался логотип вдвое меньше кнопки, прижатый к
левому верхнему углу. Настоящий размер даёт системный список образов:
у него есть набор на 256 точек, тот же, что проводник показывает в
режиме крупных значков.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
from ctypes import wintypes
from pathlib import Path

import config

CACHE_DIR = config.DATA_DIR / "icons"
# Выбранные руками картинки держим в проекте: чужой файл могут удалить,
# а кнопка на телефоне должна пережить уборку на рабочем столе.
CUSTOM_DIR = CACHE_DIR / "custom"

# Сторона картинки, которая уходит на телефон. Кнопка там 44 точки CSS,
# но экран плотный — в настоящих точках это около ста двадцати.
SIZE = 128

# Наборы значков системы: 256, 48 и 32 точки.
SHIL_JUMBO = 0x4
SHIL_EXTRALARGE = 0x2
SHIL_LARGE = 0x0

SHGFI_SYSICONINDEX = 0x4000
ILD_TRANSPARENT = 0x1
# IImageList::GetIcon — одиннадцатый метод таблицы, считая три от IUnknown.
# Соседний девятый — Remove, и перепутать их дорого: вместо чтения значка
# выйдет правка системного списка.
GET_ICON_SLOT = 10

# {46EB5926-582E-4017-9FDF-E8998DAA0950}
IID_IMAGE_LIST = ctypes.c_char_p(
    b"\x26\x59\xeb\x46\x2e\x58\x17\x40\x9f\xdf\xe8\x99\x8d\xaa\x09\x50"
)

# Файлы, из которых значок вынимается, а не читается как картинка.
PROGRAMS = {".exe", ".ico", ".dll", ".lnk", ".msc", ".cpl"}


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def _win32():
    """Модули для работы со значками Windows. None — их нет в окружении."""
    try:
        import win32con
        import win32gui
        import win32ui

        return win32con, win32gui, win32ui
    except ImportError:
        return None


# --- Чтение значка из Windows -------------------------------------------


def _shell_icon(path: Path, which: int):
    """Просит у системы значок файла из набора нужного размера."""
    info = _FileInfo()
    ok = ctypes.windll.shell32.SHGetFileInfoW(
        str(path), 0, ctypes.byref(info), ctypes.sizeof(info), SHGFI_SYSICONINDEX
    )
    if not ok:
        return None

    images = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetImageList(
        which, IID_IMAGE_LIST, ctypes.byref(images)
    ):
        return None

    table = ctypes.cast(images, ctypes.POINTER(ctypes.c_void_p))[0]
    slot = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))[GET_ICON_SLOT]
    call = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    )(slot)

    handle = ctypes.c_void_p()
    if call(images, info.iIcon, ILD_TRANSPARENT, ctypes.byref(handle)):
        return None
    return handle.value or None


def _paint(handle, side: int):
    """Переносит значок Windows в картинку заданной стороны."""
    win32con, win32gui, win32ui = _win32()
    from PIL import Image

    screen = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    memory = screen.CreateCompatibleDC()

    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(screen, side, side)
    memory.SelectObject(bitmap)
    # Холст из CreateCompatibleBitmap не обнулён: там мусор из видеопамяти,
    # и он проступит всюду, куда значок не дорисует.
    memory.FillSolidRect((0, 0, side, side), 0)

    # Именно DrawIconEx: у DrawIcon размер прибит к системной метрике.
    win32gui.DrawIconEx(
        memory.GetSafeHdc(), 0, 0, handle, side, side, 0, None, win32con.DI_NORMAL
    )

    bits = bitmap.GetBitmapBits(True)
    return Image.frombuffer("RGBA", (side, side), bits, "raw", "BGRA", 0, 1)


def _from_program(path: Path):
    """Значок программы, начиная с самого крупного, что даёт система."""
    parts = _win32()
    if parts is None:
        return None
    _, win32gui, _ = parts

    for which, side in (
        (SHIL_JUMBO, 256),
        (SHIL_EXTRALARGE, 48),
        (SHIL_LARGE, 32),
    ):
        try:
            handle = _shell_icon(path, which)
        except Exception:
            handle = None
        if not handle:
            continue
        try:
            return _paint(handle, side)
        except Exception:
            continue
        finally:
            try:
                win32gui.DestroyIcon(handle)
            except Exception:
                pass

    # Системный список иногда молчит — тогда вынимаем значок прямо из файла.
    try:
        large, small = win32gui.ExtractIconEx(str(path), 0)
    except Exception:
        return None

    handles = large + small
    if not handles:
        return None
    try:
        return _paint(handles[0], 64)
    except Exception:
        return None
    finally:
        for handle in handles:
            try:
                win32gui.DestroyIcon(handle)
            except Exception:
                pass


def _from_picture(path: Path):
    """Обычный файл картинки."""
    try:
        from PIL import Image

        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def read_any(path: Path):
    """Картинка откуда угодно: из программы, из значка, из обычного файла.

    Для обычного файла запасного пути нет намеренно. Windows охотно даёт
    значок чему угодно, включая испорченный png, — но это пустой лист
    бумаги. Молча подсунуть его вместо выбранной картинки хуже, чем
    честно сказать, что файл не читается.

    Папка — тоже: значок у неё свой, оболочка рисует его так же, как рисует
    для программы, а расширения у папки нет и в `PROGRAMS` её не ждёт.
    """
    if not path.exists():
        return None
    if path.is_dir():
        return _from_program(path)
    if path.suffix.lower() in PROGRAMS:
        return _from_program(path)
    return _from_picture(path)


# --- Подгонка ------------------------------------------------------------


def _fit(image):
    """Обрезает пустые поля и ставит значок в середину одинакового холста.

    Источники дают разную долю занятого места, и рядом на телефоне это
    читается как разнобой в размерах кнопок. После подгонки все ровные.
    """
    from PIL import Image

    image = image.convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box:
        image = image.crop(box)
    if not image.width or not image.height:
        return None

    scale = (SIZE * 0.94) / max(image.size)
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.LANCZOS,
    )

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2))
    return canvas


# --- Хранилище -----------------------------------------------------------


def capture(source: Path, app_id: str) -> str | None:
    """Забирает выбранную картинку в проект.

    Возвращает путь относительно корня — он и пишется в apps.json.
    Копируем, а не запоминаем чужой путь: выбранный файл может лежать
    в загрузках, которые вычистят на следующей неделе.
    """
    image = read_any(Path(source))
    if image is None:
        return None
    image = _fit(image)
    if image is None:
        return None

    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    target = CUSTOM_DIR / f"{app_id}.png"
    try:
        image.save(target, "PNG")
    except OSError:
        return None
    return target.relative_to(config.ROOT).as_posix()


def capture_bytes(blob: bytes, app_id: str) -> str:
    """Сохраняет картинку, выбранную в веб-пульте, без временного файла."""
    import io
    import re
    import uuid
    from PIL import Image

    if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
        raise ValueError("некорректный id кнопки")
    if not blob or len(blob) > 5 * 1024 * 1024:
        raise ValueError("картинка должна быть не больше 5 МБ")
    try:
        with Image.open(io.BytesIO(blob)) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError("слишком большое изображение")
            source.load()
            image = _fit(source)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("не смог прочитать картинку") from exc
    if image is None:
        raise ValueError("картинка пустая")
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    target = CUSTOM_DIR / f"{app_id}-{uuid.uuid4().hex[:8]}.png"
    image.save(target, "PNG")
    return target.relative_to(config.ROOT).as_posix()


def forget(app_id: str) -> None:
    """Выбрасывает запомненные картинки кнопки — значок ей сменили."""
    for folder in (CACHE_DIR, CUSTOM_DIR):
        for stale in folder.glob(f"{app_id}-*.png"):
            stale.unlink(missing_ok=True)
        (folder / f"{app_id}.png").unlink(missing_ok=True)


def picture_for(app: dict) -> Path | None:
    """Готовый файл значка кнопки. None — страница нарисует его сама.

    Пульт показывает в списке ровно то же, что уедет на телефон.
    """
    source = app.get("icon_source") or ("exe" if app.get("path") else "drawn")

    if source == "drawn":
        return None

    if source == "file":
        chosen = app.get("icon_file")
        if not chosen:
            return None
        picture = Path(chosen)
        if not picture.is_absolute():
            picture = config.ROOT / picture
        return picture if picture.exists() else None

    program = app.get("path")
    if not program:
        return None

    # Ключ зависит и от файла: смена пути или обновление программы не
    # должны показывать прежний логотип из кеша этой кнопки.
    program_path = Path(program)
    try:
        stamp = program_path.stat()
    except OSError:
        return None
    identity = f"{program_path.resolve()}|{stamp.st_size}|{stamp.st_mtime_ns}"
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{app.get('id', 'app')}-{SIZE}-{fingerprint}.png"
    if cached.exists():
        return cached

    image = read_any(program_path)
    if image is None:
        return None
    image = _fit(image)
    if image is None:
        return None
    try:
        image.save(cached, "PNG")
    except OSError:
        return None
    return cached


def as_data_url(picture: Path) -> str | None:
    """Картинка в виде строки, которую страница вставит прямо в <img>."""
    try:
        data = base64.b64encode(picture.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{data}"


def decorate(apps: list[dict]) -> list[dict]:
    """Добавляет значки тем кнопкам, у которых их удалось собрать."""
    for app in apps:
        picture = picture_for(app)
        if picture is None:
            continue
        url = as_data_url(picture)
        if url:
            app["image"] = url
    return apps
