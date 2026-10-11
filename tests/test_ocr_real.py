"""Prueba el motor OCR real con imágenes sintéticas, sin material de ningún juego."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from gamepal.config import OcrConfig, rapidocr_available
from gamepal.perception.ocr import create_engine

pytestmark = [
    pytest.mark.ocr,
    pytest.mark.skipif(not rapidocr_available(), reason="RapidOCR no está instalado (Windows ARM64)"),
]

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def test_rapidocr_reads_multilingual_menu() -> None:
    image = Image.new("RGB", (1280, 720), (18, 28, 34))
    draw = ImageDraw.Draw(image)
    draw.text((420, 80), "Elige una bendición", fill="white", font=_font(48))
    draw.text((200, 300), "Rayo encadenado", fill=(240, 220, 160), font=_font(40))
    draw.text((200, 400), "Choose a Boon", fill="white", font=_font(40))
    lines = create_engine(OcrConfig(backend="rapidocr"), ["es"]).read(np.asarray(image))
    texts = [line.text for line in lines]
    assert texts[0] == "Elige una bendición"
    assert "Rayo encadenado" in texts
    assert "Choose a Boon" in texts
    title = lines[0].box
    assert 0.2 < title.x < 0.45
    assert title.y < 0.2


def test_hades_boon_menu_end_to_end(tmp_path: Path, repo_profiles: Path) -> None:
    from gamepal.perception.classifier import classify
    from gamepal.perception.vocabulary import build_vocabulary
    from gamepal.profiles import load_profile
    from tests.fixtures.synthetic import boon_menu, write_game_text

    loaded = load_profile(repo_profiles / "hades-2", bundled=True)
    vocab = build_vocabulary(loaded.profile, loaded.root, write_game_text(tmp_path), ["en", "es"])
    image = boon_menu(
        "Boons of Zeus",
        [
            ("Storm Lash", "Rare", "Your Attack deals +20% damage"),
            ("Royal Spite", "Epic", "Your Special inflicts Spite"),
        ],
    )
    lines = create_engine(OcrConfig(backend="rapidocr"), ["en"]).read(image)
    result = classify(loaded.profile, vocab, lines, "es", image)
    assert result.screen_id == "boon_choice"
    assert result.complete
    names = [option["name"]["name"] for option in result.data["options"]]
    assert names == ["Latigazo de Tormenta", "Rencor Regio"]
