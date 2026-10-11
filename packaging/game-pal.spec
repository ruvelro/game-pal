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

# Solo se usa la inferencia de onnxruntime y de piper. Sus herramientas (cuantización, transformers,
# conversión, entrenamiento) importan onnx o torch, que no se instalan, y en Windows el análisis de
# PyInstaller se cae al cargarlas.
TOOLS = (
    "onnxruntime.quantization",
    "onnxruntime.transformers",
    "onnxruntime.tools",
    "onnxruntime.datasets",
    "piper.train",
)

# Paquetes con datos, modelos o bibliotecas nativas que PyInstaller no ve solo.
for package in ("rapidocr", "piper", "ctranslate2", "sdl2dll", "onnxruntime", "tokenizers"):
    try:
        d, b, h = collect_all(package)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += [name for name in h if not name.startswith(TOOLS)]
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

# En Windows, PyInstaller importa todos los paquetes recogidos en un mismo proceso para ver qué carpetas de
# DLL añaden. onnxruntime se cae ahí (0xC0000005) si otro paquete (los de winrt, por ejemplo) cargó antes un
# msvcp140.dll más antiguo que el que necesita. Como PyInstaller hace con Qt, se importa el primero.
if sys.platform == "win32":
    import PyInstaller.building.build_main as build_main

    _find_binary_dependencies = build_main.find_binary_dependencies

    def _onnxruntime_first(binaries, import_packages, *args):
        first = [p for p in import_packages if p == "onnxruntime" or p.startswith("onnxruntime.")]
        rest = [p for p in import_packages if p not in first]
        return _find_binary_dependencies(binaries, first + rest, *args)

    build_main.find_binary_dependencies = _onnxruntime_first

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
    excludes=["pytest", "mypy", "ruff", "IPython", "matplotlib", "notebook", *TOOLS],
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
