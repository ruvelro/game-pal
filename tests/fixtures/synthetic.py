"""Pantallas y textos de juego sintéticos para probar perfiles sin material de ningún juego."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# Nombres inventados con forma de bendición: no son textos del juego.
BOONS = {
    "ZeusWeaponBoon": {"en": "Storm Lash", "es": "Latigazo de Tormenta"},
    "HeraSpecialBoon": {"en": "Royal Spite", "es": "Rencor Regio"},
    "ApolloCastBoon": {"en": "Solar Ring", "es": "Anillo Solar"},
    "PoseidonSprintBoon": {"en": "Tidal Dash", "es": "Embestida Marea"},
}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def write_game_text(root: Path) -> Path:
    """Crea una instalación falsa con textos en SJSON para en y es."""
    for lang in ("en", "es"):
        folder = root / "Content" / "Game" / "Text" / lang
        folder.mkdir(parents=True, exist_ok=True)
        entries = "\n".join(
            f'    {{ Id = "{boon_id}" DisplayName = "{names[lang]}" Description = "{{$Keywords.Attack}} +20%" }}'
            for boon_id, names in BOONS.items()
        )
        keepsake = '    { Id = "LuckyCoinKeepsake" DisplayName = "Lucky Coin" }'
        (folder / f"TraitText.{lang}.sjson").write_text(
            f"/* sintético */\nTexts = [\n{entries}\n{keepsake}\n]\n", encoding="utf-8"
        )
    return root


def boon_menu(title: str, options: list[tuple[str, str, str]], size: tuple[int, int] = (1600, 900)) -> np.ndarray:
    """Menú de elección: título arriba y tarjetas con nombre, rareza a la derecha y efecto debajo."""
    image = Image.new("RGB", size, (16, 22, 30))
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.text((int(w * 0.36), int(h * 0.06)), title, fill=(240, 230, 200), font=font(46))
    for i, (name, rarity, effect) in enumerate(options):
        top = int(h * (0.24 + i * 0.22))
        draw.rectangle([int(w * 0.24), top - 12, int(w * 0.86), top + int(h * 0.17)], outline=(90, 110, 120), width=2)
        draw.text((int(w * 0.28), top), name, fill="white", font=font(36))
        draw.text((int(w * 0.72), top), rarity, fill=(120, 170, 255), font=font(30))
        draw.text((int(w * 0.28), top + 58), effect, fill=(200, 200, 200), font=font(26))
    return np.asarray(image)


def boss_intro(name: str, subtitle: str, size: tuple[int, int] = (1600, 900)) -> np.ndarray:
    image = Image.new("RGB", size, (8, 8, 12))
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.text((int(w * 0.40), int(h * 0.52)), name, fill="white", font=font(54))
    draw.text((int(w * 0.36), int(h * 0.62)), subtitle, fill=(220, 200, 150), font=font(34))
    return np.asarray(image)
