"""Значок Трубы для окна пульта, ярлыков и панели задач.

Янтарное кольцо на тёмном круге — как логотип в шапке пульта
(ui/web/pult.css, `.значок`). Один рисунок используется в окне и ярлыках.

Файл рисуется один раз в `data/truba.ico`; уже лежащий не трогаем — вдруг
человек положил туда свою картинку.
"""

from pathlib import Path

import config

ICO_NAME = "truba.ico"
# Windows сама выберет из файла размер, какой нужен окну и панели задач,
# поэтому кладём все ходовые.
ICO_SIZES = (256, 64, 48, 32, 16)

# Акцент пульта (ui/web/pult.css) и его тёмная подложка.
ЦВЕТ_КОЛЬЦА = (232, 145, 58, 255)
ЦВЕТ_ФОНА = (20, 22, 28, 255)


def ico_path() -> Path:
    return Path(config.DATA_DIR) / ICO_NAME


def draw(path: Path) -> None:
    """Рисует значок в файл `.ico` со всеми размерами."""
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    # Рисуем крупно и уменьшаем: на 16 точках кольцо в один пиксель
    # превратилось бы в рваную кашу.
    сторона = 1024
    картинка = Image.new("RGBA", (сторона, сторона), (0, 0, 0, 0))
    рисовалка = ImageDraw.Draw(картинка)
    рисовалка.ellipse((0, 0, сторона - 1, сторона - 1), fill=ЦВЕТ_ФОНА)
    # Толщина кольца — примерно шестая часть радиуса, как у .значок в пульте.
    толщина = сторона // 12
    рисовалка.ellipse(
        (толщина // 2, толщина // 2, сторона - 1 - толщина // 2, сторона - 1 - толщина // 2),
        outline=ЦВЕТ_КОЛЬЦА,
        width=толщина,
    )
    картинка = картинка.resize((256, 256), Image.LANCZOS)
    картинка.save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])


def ensure() -> Path | None:
    """Путь к значку; нет файла — рисует. Не вышло — None, окно обойдётся."""
    path = ico_path()
    if path.exists():
        return path
    try:
        draw(path)
    except Exception:
        return None
    return path if path.exists() else None
