"""Tipos e interfaz común de captura."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Protocol

import numpy as np
import numpy.typing as npt

RGBImage = npt.NDArray[np.uint8]


class CaptureUnavailableError(RuntimeError):
    """La captura no es posible en este sistema o falta un permiso."""


@dataclass(frozen=True, slots=True)
class WindowInfo:
    id: int
    title: str
    process: str
    pid: int
    width: int
    height: int
    left: int = 0
    top: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def process_stem(self) -> str:
        """Nombre del ejecutable sin ruta ni extensión, en minúsculas."""
        return PurePath(self.process.replace("\\", "/")).stem.lower()


@dataclass(frozen=True, slots=True)
class Frame:
    id: int
    image: RGBImage
    window: WindowInfo
    captured_at: float = field(default_factory=time.monotonic)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class CaptureBackend(Protocol):
    name: str

    def list_windows(self) -> list[WindowInfo]:
        """Ventanas visibles de aplicaciones, de la más cercana al frente a la más lejana."""
        ...

    def foreground_pid(self) -> int | None:
        """PID de la aplicación en primer plano, si se puede saber."""
        ...

    def grab(self, window: WindowInfo) -> RGBImage | None:
        """Captura el contenido de una ventana. None si ya no existe."""
        ...


def match_window(
    windows: list[WindowInfo],
    processes: list[str],
    title: str | None,
    foreground_pid: int | None = None,
) -> WindowInfo | None:
    """Elige la ventana del juego.

    Coincide por nombre de proceso (sin extensión, sin mayúsculas) o por subcadena del título.
    Entre varias candidatas prefiere la de primer plano y luego la más grande.
    """
    wanted = {PurePath(p.replace("\\", "/")).stem.lower() for p in processes if p.strip()}
    needle = title.strip().lower() if title and title.strip() else None
    if not wanted and needle is None:
        return None

    def matches(w: WindowInfo) -> bool:
        if w.width < 200 or w.height < 150:
            return False
        if wanted and w.process_stem in wanted:
            return True
        return needle is not None and needle in w.title.lower()

    candidates = [w for w in windows if matches(w)]
    if not candidates:
        return None
    return max(candidates, key=lambda w: (w.pid == foreground_pid, w.area))


def downscale(image: RGBImage, max_width: int) -> RGBImage:
    """Reduce por muestreo de bloques para no depender de librerías pesadas."""
    height, width = image.shape[:2]
    if width <= max_width:
        return image
    factor = -(-width // max_width)  # techo
    new_h, new_w = height // factor, width // factor
    trimmed = image[: new_h * factor, : new_w * factor]
    reduced = trimmed.reshape(new_h, factor, new_w, factor, 3).mean(axis=(1, 3))
    return reduced.astype(np.uint8)
