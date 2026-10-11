"""Punto de entrada de la app empaquetada con PyInstaller."""

import os
import sys

# En una app sin consola (Windows, .app de macOS) no hay salida estándar: se descarta en lugar de fallar.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

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
