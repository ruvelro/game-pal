"""Normalización de texto común a OCR, vocabulario y señales."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Minúsculas y espacios simples, conservando tildes."""
    return _SPACES.sub(" ", text.casefold()).strip()


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni puntuación. 'Elige una Bendición!' → 'elige una bendicion'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", _NON_WORD.sub(" ", stripped).replace("_", " ")).strip()


def max_edits(length: int) -> int:
    """Errores de OCR tolerados según la longitud: las palabras cortas deben coincidir exactas."""
    if length <= 4:
        return 0
    if length <= 9:
        return 1
    return 2 if length <= 18 else 3
