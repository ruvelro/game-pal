# Especificación de PyInstaller para la app de escritorio. Se usa desde scripts/build.py.
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
NAME = "game-pal"
sys.path.insert(0, str(ROOT))
from gamepal import __version__ as VERSION  # noqa: E402

datas = [
    (str(ROOT / "gamepal" / "gui_server" / "static"), "gamepal/gui_server/static"),
    (str(ROOT / "profiles"), "gamepal/_bundled_profiles"),
    (str(ROOT / "models" / "catalog.yaml"), "gamepal/_bundled_models"),
    (str(ROOT / "assets" / "icons" / "icon-64.png"), "gamepal/_assets"),
]
binaries = []
hiddenimports = collect_submodules("gamepal") + ["defusedxml", "defusedxml.ElementTree"]

# Paquetes con datos, modelos o bibliotecas nativas que PyInstaller no ve solo.
for package in ("rapidocr", "piper", "ctranslate2", "sdl2dll", "onnxruntime", "tokenizers"):
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
for package in ("faster_whisper", "trafilatura", "justext", "courlan", "htmldate"):
    datas += collect_data_files(package)
for package in ("uvicorn", "pynput", "pystray", "keyring.backends", "anthropic"):
    hiddenimports += collect_submodules(package)
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")
for package in ("keyring", "rapidocr", "faster_whisper", "onnxruntime", "huggingface_hub", "anthropic", "fastapi"):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

icon = {
    "win32": str(ROOT / "packaging" / "icon.ico"),
    "darwin": str(ROOT / "packaging" / "icon.icns"),
}.get(sys.platform, str(ROOT / "assets" / "icons" / "icon-256.png"))

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "mypy", "ruff", "IPython", "matplotlib", "notebook"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=icon,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collected = COLLECT(exe, analysis.binaries, analysis.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        collected,
        name="game-pal.app",
        icon=icon,
        bundle_identifier="io.github.ruvelro.game-pal",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,  # vive en la barra de menús, sin icono en el Dock
            "LSMinimumSystemVersion": "13.0",
            "NSMicrophoneUsageDescription": "game-pal escucha tus preguntas cuando usas la voz.",
            "NSHighResolutionCapable": True,
        },
    )
