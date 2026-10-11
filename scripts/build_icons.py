#!/usr/bin/env python3
"""Genera icon.png, icon.ico e icon.icns a partir de assets/icon.svg.

Solo usa herramientas del sistema (qlmanage, sips, iconutil en macOS) y la
librería estándar. En otros sistemas basta con tener rsvg-convert o inkscape
para el primer paso; el .ico se construye a mano porque el formato ICO admite
PNG incrustado.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVG = ASSETS / "icon.svg"
SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


def render_master(out: Path, size: int = 1024) -> None:
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(SVG), "-o", str(out)], check=True)
        return
    if shutil.which("qlmanage"):
        tmp = out.parent / "_ql"
        tmp.mkdir(exist_ok=True)
        subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", str(tmp), str(SVG)], check=True, capture_output=True)
        shutil.move(str(tmp / (SVG.name + ".png")), str(out))
        shutil.rmtree(tmp)
        return
    sys.exit("Necesito rsvg-convert o qlmanage (macOS) para rasterizar el SVG.")


def resize(src: Path, dst: Path, size: int) -> None:
    if shutil.which("sips"):
        subprocess.run(
            ["sips", "-z", str(size), str(size), str(src), "--out", str(dst)], check=True, capture_output=True
        )
    elif shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(SVG), "-o", str(dst)], check=True)
    else:
        sys.exit("Necesito sips (macOS) o rsvg-convert para redimensionar.")


def build_ico(pngs: dict[int, Path], out: Path) -> None:
    entries = [(s, pngs[s].read_bytes()) for s in (16, 32, 48, 64, 128, 256)]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    payload = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    out.write_bytes(header + directory + payload)


def build_icns(pngs: dict[int, Path], out: Path) -> None:
    if not shutil.which("iconutil"):
        print("iconutil no disponible; se omite el .icns")
        return
    iconset = ASSETS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        shutil.copy(pngs[size], iconset / f"icon_{size}x{size}.png")
        shutil.copy(pngs[size * 2], iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    shutil.rmtree(iconset)


def main() -> None:
    master = ASSETS / "icon.png"
    render_master(master, 1024)
    pngs: dict[int, Path] = {1024: master}
    sized = ASSETS / "icons"
    sized.mkdir(exist_ok=True)
    for size in SIZES[:-1]:
        dst = sized / f"icon-{size}.png"
        resize(master, dst, size)
        pngs[size] = dst
    build_ico(pngs, ASSETS / "icon.ico")
    build_icns(pngs, ASSETS / "icon.icns")
    print("Iconos generados en", ASSETS)


if __name__ == "__main__":
    main()
