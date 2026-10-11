"""OCR con Windows.Media.Ocr (solo Windows). Viene con el sistema y funciona en x64 y en ARM64.

Cada motor reconoce un único idioma y necesita su paquete de idioma instalado en Windows.
No da puntuación de confianza: todas las líneas llegan con confianza 1.0.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from gamepal.capture.base import RGBImage
from gamepal.events import Box, TextLine
from gamepal.perception.ocr.base import reading_order

# Códigos de idioma del perfil (ISO 639-1) a la etiqueta de Windows preferida.
_LANGS = {
    "en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT", "pt": "pt-BR",
    "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-Hans-CN", "ru": "ru-RU", "pl": "pl-PL", "nl": "nl-NL",
    "tr": "tr-TR", "uk": "uk-UA", "sv": "sv-SE",
}  # fmt: skip

# Tiempo máximo por reconocimiento; Windows suele tardar decenas de milisegundos.
_TIMEOUT_SECONDS = 30.0


def _primary(tag: str) -> str:
    return tag.split("-", 1)[0].lower()


def pick_language_tag(languages: list[str], available: list[str]) -> str | None:
    """Elige el primer idioma del perfil que Windows sabe reconocer.

    Prefiere la etiqueta exacta de `_LANGS` y, si no está instalada, cualquier variante
    regional del mismo idioma (por ejemplo `es-MX` para `es`).
    """
    by_lower = {tag.lower(): tag for tag in available}
    for code in languages:
        preferred = _LANGS.get(code.lower(), code)
        if preferred.lower() in by_lower:
            return by_lower[preferred.lower()]
        for tag in available:
            if _primary(tag) == _primary(code):
                return tag
    return None


class WindowsOcrEngine:
    name = "windows-ocr"

    def __init__(self, languages: list[str], min_confidence: float = 0.5) -> None:
        try:
            from winrt.windows.foundation import AsyncStatus
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
            from winrt.windows.media.ocr import OcrEngine
        except ImportError as exc:
            raise RuntimeError("Windows OCR necesita Windows 10 o posterior y los paquetes winrt") from exc
        self._async_status: Any = AsyncStatus
        self._bitmap: Any = SoftwareBitmap
        self._bgra8: Any = BitmapPixelFormat.BGRA8
        available = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
        tag = pick_language_tag(languages, available)
        engine = OcrEngine.try_create_from_language(Language(tag)) if tag else None
        # Sin coincidencia, Windows usa los idiomas de la cuenta del usuario.
        engine = engine or OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(
                "Windows no tiene ningún idioma de OCR instalado. Añade uno en Configuración › Hora e idioma."
            )
        self._engine: Any = engine
        self.language = str(engine.recognizer_language.language_tag)
        self._max_dimension = int(OcrEngine.max_image_dimension)
        # Windows no da confianza por línea: se guarda solo por compatibilidad con OcrConfig.
        self._min_confidence = min_confidence
        self._lock = threading.Lock()

    def _software_bitmap(self, image: RGBImage) -> Any:
        height, width = image.shape[:2]
        scale = self._max_dimension / max(height, width)
        if scale < 1:
            from PIL import Image

            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = np.asarray(Image.fromarray(image[..., :3]).resize(size, Image.Resampling.LANCZOS))
            height, width = image.shape[:2]
        bgra = np.empty((height, width, 4), dtype=np.uint8)
        bgra[..., :3] = image[..., 2::-1]
        bgra[..., 3] = 255
        return self._bitmap.create_copy_from_buffer(bgra.tobytes(), self._bgra8, width, height)

    def read(self, image: RGBImage) -> list[TextLine]:
        bitmap = self._software_bitmap(image)
        height, width = int(bitmap.pixel_height), int(bitmap.pixel_width)
        with self._lock:
            operation = self._engine.recognize_async(bitmap)
            status = operation.wait(_TIMEOUT_SECONDS)
            if status == self._async_status.STARTED:
                operation.cancel()
                raise RuntimeError("Windows OCR no respondió a tiempo")
            result = operation.get_results()
        lines: list[TextLine] = []
        for ocr_line in result.lines:
            text = str(ocr_line.text).strip()
            rects = [word.bounding_rect for word in ocr_line.words]
            if not text or not rects:
                continue
            left = max(0.0, min(float(r.x) for r in rects))
            top = max(0.0, min(float(r.y) for r in rects))
            right = min(float(width), max(float(r.x + r.width) for r in rects))
            bottom = min(float(height), max(float(r.y + r.height) for r in rects))
            box = Box(
                x=left / width,
                y=top / height,
                width=max(0.0, right - left) / width,
                height=max(0.0, bottom - top) / height,
            )
            lines.append(TextLine(text=text, confidence=1.0, box=box))
        return reading_order(lines)
