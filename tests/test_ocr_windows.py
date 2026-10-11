"""Windows OCR: elección de idioma y backend en todos los sistemas; lectura real solo en Windows."""

import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gamepal import config as config_module
from gamepal.config import OcrConfig
from gamepal.perception.ocr import create_engine
from gamepal.perception.ocr.windows_ocr import pick_language_tag
from tests.fixtures.synthetic import font


def test_pick_language_prefers_exact_tag() -> None:
    assert pick_language_tag(["es"], ["en-US", "es-MX", "es-ES"]) == "es-ES"


def test_pick_language_accepts_regional_variant() -> None:
    assert pick_language_tag(["es"], ["en-US", "es-MX"]) == "es-MX"


def test_pick_language_follows_profile_order() -> None:
    assert pick_language_tag(["ja", "en"], ["en-GB"]) == "en-GB"


def test_pick_language_without_match() -> None:
    assert pick_language_tag(["ko"], ["en-US"]) is None


def test_default_backend_without_rapidocr_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.sys, "platform", "win32")
    monkeypatch.setattr(config_module, "rapidocr_available", lambda: False)
    assert OcrConfig().backend == "windows-ocr"


def test_default_backend_with_rapidocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "rapidocr_available", lambda: True)
    assert OcrConfig().backend == "rapidocr"


@pytest.mark.skipif(sys.platform == "win32", reason="comprueba el error fuera de Windows")
def test_windows_ocr_outside_windows() -> None:
    with pytest.raises(RuntimeError, match="solo está disponible en Windows"):
        create_engine(OcrConfig(backend="windows-ocr"), ["en"])


@pytest.mark.windows_ocr
@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR solo existe en Windows")
def test_windows_ocr_reads_synthetic_menu() -> None:
    image = Image.new("RGB", (1280, 720), (18, 28, 34))
    draw = ImageDraw.Draw(image)
    draw.text((420, 80), "Choose a Boon", fill="white", font=font(56))
    draw.text((200, 320), "Storm Lash", fill=(240, 220, 160), font=font(44))
    draw.text((200, 440), "Royal Spite", fill="white", font=font(44))
    engine = create_engine(OcrConfig(backend="windows-ocr"), ["en"])
    assert engine.name == "windows-ocr"
    lines = engine.read(np.asarray(image))
    texts = [line.text for line in lines]
    assert texts == ["Choose a Boon", "Storm Lash", "Royal Spite"]
    title = lines[0].box
    assert 0.25 < title.x < 0.4
    assert title.y < 0.2
    assert all(0 <= line.box.x <= 1 and 0 <= line.box.y <= 1 for line in lines)
