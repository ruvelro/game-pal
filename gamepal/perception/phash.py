"""Hash perceptual por diferencias (dHash) para detectar cambios de pantalla de forma barata."""

from __future__ import annotations

import numpy as np

from gamepal.capture.base import RGBImage

HASH_SIDE = 16
HASH_BITS = HASH_SIDE * HASH_SIDE


def _resize_gray(image: RGBImage, width: int, height: int) -> np.ndarray:
    # Luma BT.601 y reducción por promedio de bloques: estable frente a ruido y compresión.
    gray = image[..., 0] * 0.299 + image[..., 1] * 0.587 + image[..., 2] * 0.114
    rows = np.linspace(0, gray.shape[0], height + 1).astype(int)
    cols = np.linspace(0, gray.shape[1], width + 1).astype(int)
    row_sums = np.add.reduceat(gray, rows[:-1], axis=0) / np.diff(rows)[:, None]
    return np.add.reduceat(row_sums, cols[:-1], axis=1) / np.diff(cols)[None, :]


def dhash(image: RGBImage) -> int:
    """Hash de 256 bits: compara cada celda con su vecina derecha en una rejilla 17×16."""
    if image.ndim != 3 or image.shape[2] < 3 or image.shape[0] < 2 or image.shape[1] < 2:
        raise ValueError("Se esperaba una imagen RGB de al menos 2×2")
    small = _resize_gray(image[..., :3].astype(np.float32), HASH_SIDE + 1, HASH_SIDE)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()
