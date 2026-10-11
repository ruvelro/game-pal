"""Punto de entrada de la app empaquetada con PyInstaller."""

import contextlib
import os
import sys

# En una app sin consola (Windows, .app de macOS) no hay salida estándar: se descarta en lugar de fallar.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

# En Windows, onnxruntime necesita un msvcp140.dll reciente: se carga antes que otros paquetes (winrt…) que
# traen uno más antiguo y que, cargado primero, lo haría caer.
if sys.platform == "win32":
    with contextlib.suppress(ImportError):
        import onnxruntime  # noqa: F401

# Los procesos auxiliares (el overlay) se lanzan con el mismo ejecutable.
MODULES = {"gamepal.overlay": "gamepal.overlay.__main__"}

if len(sys.argv) > 2 and sys.argv[1] == "--run-module":
    module = MODULES.get(sys.argv[2])
    if module is None:
        sys.exit(f"Módulo no permitido: {sys.argv[2]}")
    import importlib

    sys.exit(importlib.import_module(module).main())

from gamepal.cli import main  # noqa: E402

sys.exit(main())
