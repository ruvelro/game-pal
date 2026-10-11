"""Interfaz común de los motores OCR."""

from __future__ import annotations

from typing import Protocol

from gamepal.capture.base import RGBImage
from gamepal.events import Box, TextLine


class OcrEngine(Protocol):
    name: str

    def read(self, image: RGBImage) -> list[TextLine]:
        """Devuelve líneas con cajas normalizadas a [0, 1], ordenadas de arriba abajo."""
        ...


def normalized_box(points: list[list[float]], width: int, height: int) -> Box:
    """Convierte un polígono en píxeles a una caja alineada y normalizada."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    left, right = max(0.0, min(xs)), min(float(width), max(xs))
    top, bottom = max(0.0, min(ys)), min(float(height), max(ys))
    return Box(
        x=left / width,
        y=top / height,
        width=max(0.0, right - left) / width,
        height=max(0.0, bottom - top) / height,
    )


def reading_order(lines: list[TextLine]) -> list[TextLine]:
    """Ordena por filas (con tolerancia de media altura) y luego de izquierda a derecha."""
    if not lines:
        return []
    by_top = sorted(lines, key=lambda line: line.box.center_y)
    rows: list[list[TextLine]] = [[by_top[0]]]
    for line in by_top[1:]:
        anchor = rows[-1][0].box
        if abs(line.box.center_y - anchor.center_y) <= max(anchor.height, line.box.height) / 2:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item.box.x)]
