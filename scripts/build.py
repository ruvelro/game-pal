"""Empaqueta game-pal como app de escritorio para el sistema actual.

    uv sync --group build && uv run python scripts/build.py

Resultado en dist/release/:
- macOS: game-pal-<versión>-macos-arm64.dmg (app de la barra de menús).
- Windows: instalador .exe (Inno Setup, si está instalado) y .zip portable.
- Linux: .AppImage (si hay appimagetool) y .tar.gz.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RELEASE = DIST / "release"
sys.path.insert(0, str(ROOT))
from gamepal import __version__  # noqa: E402


def arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in {"arm64", "aarch64"} else "x64"


def run(args: list[str], **kwargs: object) -> None:
    print("$", " ".join(args), flush=True)
    subprocess.run(args, check=True, **kwargs)  # type: ignore[call-overload]


def build_ui() -> None:
    static = ROOT / "gamepal" / "gui_server" / "static" / "index.html"
    if static.is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        sys.exit("Falta la GUI compilada y no hay npm para compilarla")
    run([npm, "ci"], cwd=ROOT / "ui")
    run([npm, "run", "build"], cwd=ROOT / "ui")


def pyinstaller() -> Path:
    work = ROOT / "build" / "pyinstaller"
    spec = ROOT / "packaging" / "game-pal.spec"
    args = ["--noconfirm", "--clean", "--workpath", str(work), "--distpath", str(DIST), str(spec)]
    run([sys.executable, "-m", "PyInstaller", *args])
    return DIST / ("game-pal.app" if sys.platform == "darwin" else "game-pal")


def package_macos(app: Path) -> list[Path]:
    staging = DIST / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    out = RELEASE / f"game-pal-{__version__}-macos-{arch()}.dmg"
    out.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", "game-pal", "-srcfolder", str(staging), "-format", "UDZO", "-ov", str(out)])
    return [out]


INNO = r"""
#define AppVersion "{version}"
[Setup]
AppId={{{{6C1B5E52-8A7E-4F5B-9E5C-2B9D0F1C6A11}}
AppName=game-pal
AppVersion={{#AppVersion}}
AppPublisher=game-pal
AppPublisherURL=https://github.com/ruvelro/game-pal
DefaultDirName={{autopf}}\game-pal
DefaultGroupName=game-pal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={out_dir}
OutputBaseFilename=game-pal-{{#AppVersion}}-windows-{arch}-setup
SetupIconFile={icon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed={allowed}
ArchitecturesInstallIn64BitMode={allowed}
LicenseFile={license}

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; Flags: unchecked

[Files]
Source: "{source}\*"; DestDir: "{{app}}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{{autoprograms}}\game-pal"; Filename: "{{app}}\game-pal.exe"
Name: "{{autodesktop}}\game-pal"; Filename: "{{app}}\game-pal.exe"; Tasks: desktopicon

[Run]
Filename: "{{app}}\game-pal.exe"; Description: "{{cm:LaunchProgram,game-pal}}"; Flags: nowait postinstall skipifsilent
"""


def package_windows(folder: Path) -> list[Path]:
    outputs = []
    portable = RELEASE / f"game-pal-{__version__}-windows-{arch()}-portable.zip"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(folder.rglob("*")):
            bundle.write(path, Path("game-pal") / path.relative_to(folder))
    outputs.append(portable)
    iscc = shutil.which("iscc") or next(
        (
            str(p)
            for p in Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")).glob("Inno Setup*/ISCC.exe")
        ),
        None,
    )
    if iscc is None:
        print("Inno Setup no está instalado: solo se genera el zip portable")
        return outputs
    script = DIST / "game-pal.iss"
    script.write_text(
        INNO.format(
            version=__version__, arch=arch(), out_dir=RELEASE, source=folder, icon=ROOT / "packaging" / "icon.ico",
            license=ROOT / "LICENSE", allowed="arm64" if arch() == "arm64" else "x64compatible",
        ),
        encoding="utf-8",
    )  # fmt: skip
    run([iscc, str(script)])
    outputs.append(RELEASE / f"game-pal-{__version__}-windows-{arch()}-setup.exe")
    return outputs


DESKTOP = """[Desktop Entry]
Type=Application
Name=game-pal
Comment=Compañero de juego con IA, local y privado
Exec=game-pal
Icon=game-pal
Categories=Game;Utility;
Terminal=false
"""

APPRUN = """#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/game-pal/game-pal" "$@"
"""


def package_linux(folder: Path) -> list[Path]:
    outputs = []
    archive = RELEASE / f"game-pal-{__version__}-linux-{arch()}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(folder, arcname="game-pal")
    outputs.append(archive)
    tool = shutil.which("appimagetool")
    if tool is None:
        print("appimagetool no está disponible: solo se genera el tar.gz")
        return outputs
    appdir = DIST / "game-pal.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(folder, appdir / "usr" / "lib" / "game-pal", symlinks=True)
    (appdir / "game-pal.desktop").write_text(DESKTOP, encoding="utf-8")
    shutil.copy(ROOT / "assets" / "icons" / "icon-256.png", appdir / "game-pal.png")
    apprun = appdir / "AppRun"
    apprun.write_text(APPRUN, encoding="utf-8")
    apprun.chmod(0o755)
    image = RELEASE / f"game-pal-{__version__}-linux-{arch()}.AppImage"
    run(
        [tool, "--no-appstream", str(appdir), str(image)],
        env={**os.environ, "ARCH": "x86_64" if arch() == "x64" else "aarch64"},
    )
    outputs.append(image)
    return outputs


def checksums(files: list[Path]) -> Path:
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    out = RELEASE / f"SHA256SUMS-{sys.platform}-{arch()}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reutiliza dist/ de una compilación anterior")
    args = parser.parse_args()
    build_ui()
    RELEASE.mkdir(parents=True, exist_ok=True)
    target = DIST / ("game-pal.app" if sys.platform == "darwin" else "game-pal")
    if not args.skip_pyinstaller:
        target = pyinstaller()
    if sys.platform == "darwin":
        files = package_macos(target)
    elif sys.platform == "win32":
        files = package_windows(target)
    else:
        files = package_linux(target)
    sums = checksums(files)
    for path in [*files, sums]:
        print(f"{path.stat().st_size / 1e6:8.1f} MB  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
