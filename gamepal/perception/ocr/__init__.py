"""Motores OCR enchufables."""

from __future__ import annotations

import logging
import sys

from gamepal.capture.base import RGBImage
from gamepal.config import OcrConfig, rapidocr_available
from gamepal.events import TextLine
from gamepal.perception.ocr.base import OcrEngine

__all__ = ["NullOcrEngine", "OcrEngine", "create_engine"]

log = logging.getLogger(__name__)


class NullOcrEngine:
    """Desactiva el OCR. Útil para pruebas o para perfiles que solo usan visión."""

    name = "none"

    def read(self, image: RGBImage) -> list[TextLine]:
        return []


def create_engine(config: OcrConfig, languages: list[str]) -> OcrEngine:
    if config.backend == "none":
        return NullOcrEngine()
    if config.backend == "apple-vision":
        if sys.platform != "darwin":
            raise RuntimeError("Apple Vision solo está disponible en macOS")
        from gamepal.perception.ocr.apple_vision import AppleVisionOcrEngine

        return AppleVisionOcrEngine(languages, config.min_confidence)
    backend = config.backend
    if backend == "rapidocr" and sys.platform == "win32" and not rapidocr_available():
        # Configuración traída de otro equipo: en Windows ARM64 no hay RapidOCR.
        log.warning("RapidOCR no está instalado; se usa Windows OCR")
        backend = "windows-ocr"
    if backend == "windows-ocr":
        if sys.platform != "win32":
            raise RuntimeError("Windows OCR solo está disponible en Windows")
        from gamepal.perception.ocr.windows_ocr import WindowsOcrEngine

        return WindowsOcrEngine(languages, config.min_confidence)
    from gamepal.perception.ocr.rapid import RapidOcrEngine

    return RapidOcrEngine(config.min_confidence)
