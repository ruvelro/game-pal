"""Icono en la bandeja del sistema (barra de menús en macOS) para la app instalada.

Ofrece abrir la GUI, pausar o reanudar la observación, silenciar los avisos y salir. Corre en el
hilo principal, como exigen macOS y algunos escritorios de Linux; el servidor va en otro hilo.
"""

from __future__ import annotations

import locale
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TEXTS = {
    "es": {"open": "Abrir game-pal", "pause": "Pausar la observación", "mute": "Silenciar avisos", "quit": "Salir"},
    "en": {"open": "Open game-pal", "pause": "Pause watching", "mute": "Mute tips", "quit": "Quit"},
}


@dataclass
class TrayActions:
    open_gui: Callable[[], None]
    toggle_pause: Callable[[], None]
    toggle_mute: Callable[[], None]
    quit: Callable[[], None]
    paused: Callable[[], bool]
    muted: Callable[[], bool]


def _texts() -> dict[str, str]:
    language = (locale.getlocale()[0] or "en").lower()
    return TEXTS["es" if language.startswith("es") else "en"]


def icon_path() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "_assets" / "icon-64.png", here.parent / "assets" / "icons" / "icon-64.png"):
        if candidate.is_file():
            return candidate
    return None


class Tray:
    def __init__(self, actions: TrayActions) -> None:
        import pystray
        from PIL import Image

        texts = _texts()
        path = icon_path()
        image = Image.open(path) if path else Image.new("RGBA", (64, 64), (126, 224, 185, 255))

        def run(action: Callable[[], None]) -> Callable[[object, object], None]:
            return lambda _icon, _item: action()

        menu = pystray.Menu(
            pystray.MenuItem(texts["open"], run(actions.open_gui), default=True),
            pystray.MenuItem(texts["pause"], run(actions.toggle_pause), checked=lambda _i: actions.paused()),
            pystray.MenuItem(texts["mute"], run(actions.toggle_mute), checked=lambda _i: actions.muted()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(texts["quit"], run(actions.quit)),
        )
        self._icon = pystray.Icon("game-pal", image, "game-pal", menu)

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()
