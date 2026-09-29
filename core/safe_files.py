"""Запись файлов без полупустых остатков и карантин битых.

Обычная `write_text` сначала обнуляет файл, потом пишет. Умер процесс
посередине (выключили свет, пульт сняли диспетчером, Windows ушла в
перезагрузку ради обновления) — на диске пустой или обрезанный файл. А
битый JSON мы читаем как «ничего», и следующая запись затирает остатки:
так тихо пропала бы вся память или все настройки (29.09, проверка кода).

Поэтому пишем во временный файл рядом и подменяем им старый одним
действием — `os.replace` на одном диске атомарен. Так уже пишут заметки
(core/notes.py::_write), погода и учёт расходов; здесь то же для всех.
"""

import os
import time
from datetime import datetime
from pathlib import Path

# Антивирус или индексатор Windows держит только что записанный файл долю
# секунды, и подмена в этот миг падает с «доступ запрещён». Пара повторов
# это лечит, а настоящий запрет всё равно всплывёт ошибкой.
REPLACE_TRIES = 5
REPLACE_PAUSE = 0.05


def write_text(path, text: str, encoding: str = "utf-8", newline: str | None = None) -> None:
    """Пишет файл целиком или не трогает его вовсе."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        with open(partial, "w", encoding=encoding, newline=newline) as поток:
            поток.write(text)
            поток.flush()
            os.fsync(поток.fileno())
        for попытка in range(REPLACE_TRIES):
            try:
                os.replace(partial, path)
                break
            except PermissionError:
                if попытка == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_PAUSE)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise


def quarantine(path) -> Path | None:
    """Битый файл — в сторону, а не под затирание. Возвращает новый путь.

    Зовётся там, где файл не разобрался и программа сейчас начнёт с чистого
    листа: без этого первая же запись затёрла бы то, что ещё можно вытащить
    руками. Имя — `<имя>.broken-ГГГГММДД-ЧЧММСС` рядом с прежним.
    """
    path = Path(path)
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        path.replace(target)
    except OSError:
        return None
    return target
