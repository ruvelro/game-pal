"""OCR con Apple Vision (solo macOS). Muy preciso y multiidioma; la primera carga puede tardar."""

from __future__ import annotations

from typing import Any

from gamepal.capture.base import RGBImage
from gamepal.events import Box, TextLine
from gamepal.perception.ocr.base import reading_order

# Códigos de idioma del perfil (ISO 639-1) a identificadores de Vision.
_LANGS = {
    "en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT", "pt": "pt-BR",
    "ja": "ja-JP", "ko": "ko-KR", "zh": "zh-Hans", "ru": "ru-RU", "pl": "pl-PL", "nl": "nl-NL",
    "tr": "tr-TR", "uk": "uk-UA", "sv": "sv-SE",
}  # fmt: skip


class AppleVisionOcrEngine:
    name = "apple-vision"

    def __init__(self, languages: list[str], min_confidence: float = 0.5) -> None:
        try:
            import Quartz
            import Vision
        except ImportError as exc:
            raise RuntimeError("Instala el extra macos-vision para usar Apple Vision") from exc
        self._vision: Any = Vision
        self._quartz: Any = Quartz
        self._languages = [_LANGS[code] for code in languages if code in _LANGS] or ["en-US"]
        self._min_confidence = min_confidence

    def _cgimage(self, image: RGBImage) -> Any:
        q = self._quartz
        height, width = image.shape[:2]
        rgba = bytearray(height * width * 4)
        view = memoryview(rgba)
        import numpy as np

        buffer = np.frombuffer(view, dtype=np.uint8).reshape(height, width, 4)
        buffer[..., :3] = image[..., :3]
        buffer[..., 3] = 255
        provider = q.CGDataProviderCreateWithData(None, bytes(rgba), len(rgba), None)
        return q.CGImageCreate(
            width, height, 8, 32, width * 4, q.CGColorSpaceCreateDeviceRGB(),
            q.kCGImageAlphaNoneSkipLast, provider, None, False, q.kCGRenderingIntentDefault,
        )  # fmt: skip

    def read(self, image: RGBImage) -> list[TextLine]:
        v = self._vision
        request = v.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(v.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(self._languages)
        request.setUsesLanguageCorrection_(True)
        handler = v.VNImageRequestHandler.alloc().initWithCGImage_options_(self._cgimage(image), None)
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Apple Vision falló: {error}")
        lines: list[TextLine] = []
        for observation in request.results() or []:
            candidate = observation.topCandidates_(1)[0]
            confidence = float(candidate.confidence())
            text = str(candidate.string()).strip()
            if not text or confidence < self._min_confidence:
                continue
            bb = observation.boundingBox()  # origen abajo a la izquierda
            box = Box(
                x=float(bb.origin.x),
                y=1.0 - float(bb.origin.y) - float(bb.size.height),
                width=float(bb.size.width),
                height=float(bb.size.height),
            )
            lines.append(TextLine(text=text, confidence=confidence, box=box))
        return reading_order(lines)
