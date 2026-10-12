"""OCR con RapidOCR (modelos PP-OCR en ONNX Runtime). Funciona en los tres sistemas y es multiidioma."""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from gamepal.capture.base import RGBImage
from gamepal.events import TextLine
from gamepal.perception.ocr.base import normalized_box, reading_order

OCR_THREADS = 2


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self, min_confidence: float = 0.5) -> None:
        from rapidocr import RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        # El texto de los juegos va en horizontal: el clasificador de orientación solo añade latencia.
        self._engine: Any = RapidOCR(
            params={
                "Global.log_level": "error",
                "Global.use_cls": False,
                # Por defecto usa todos los núcleos: con el juego en marcha dejaba sin CPU a la transcripción y a
                # la voz. Con dos hilos lee igual de bien, algo más despacio.
                "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        self._min_confidence = min_confidence
        # ONNX Runtime admite llamadas concurrentes, pero el pipeline de RapidOCR guarda estado.
        self._lock = threading.Lock()
        self._warm_up()

    def _warm_up(self) -> None:
        """La primera inferencia de ONNX Runtime es lenta; se paga al cargar y no en la primera pantalla."""
        from PIL import Image, ImageDraw

        sample = Image.new("RGB", (240, 64), (0, 0, 0))
        ImageDraw.Draw(sample).text((12, 20), "game-pal 123", fill=(255, 255, 255))
        self.read(np.asarray(sample))

    def read(self, image: RGBImage) -> list[TextLine]:
        height, width = image.shape[:2]
        # RapidOCR trabaja en BGR como OpenCV.
        with self._lock:
            result = self._engine(image[:, :, ::-1])
        if result.boxes is None or result.txts is None:
            return []
        lines = [
            TextLine(text=text.strip(), confidence=float(score), box=normalized_box(box.tolist(), width, height))
            for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True)
            if text.strip() and float(score) >= self._min_confidence
        ]
        return reading_order(lines)
