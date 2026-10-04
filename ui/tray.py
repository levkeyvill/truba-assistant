r"""Значок в трее: пульт прячется туда вместо закрытия.

Ассистент должен работать фоном, а окно нужно редко — только чтобы
поменять настройки или почитать логи. Крестик прячет, выход — через меню.
"""

from __future__ import annotations

import threading

from ui import theme


def _make_icon(color: str = theme.ACCENT):
    """Рисует кружок в цвет акцента — тот же, что у лица на телефоне."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.ellipse((4, 4, size - 4, size - 4), fill=theme.BG)
    draw.ellipse((16, 16, size - 16, size - 16), fill=color)
    return image


class Tray:
    """Значок рядом с часами. Работает в своём потоке."""

    def __init__(self, on_show, on_quit):
        self.on_show = on_show
        self.on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401

            return True
        except Exception:
            return False

    def start(self) -> None:
        if not self.available or (self._thread and self._thread.is_alive()):
            return

        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Открыть пульт", lambda: self.on_show(), default=True),
            pystray.MenuItem("Выход", lambda: self._quit()),
        )
        self._icon = pystray.Icon("truba", _make_icon(), "Труба", menu)

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def _quit(self) -> None:
        self.stop()
        self.on_quit()

    def notify(self, message: str, title: str = "Труба") -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
