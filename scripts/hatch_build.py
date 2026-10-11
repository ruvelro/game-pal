"""Hook de hatchling: el wheel lleva la GUI compilada, así quien lo instala no necesita Node.js.

En las instalaciones editables (`uv sync`) no hace nada: ahí la GUI se compila al arrancar game-pal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class UiBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        # Se carga por ruta: el entorno de build no tiene las dependencias del paquete gamepal.
        spec = importlib.util.spec_from_file_location("gamepal_ui_build", root / "gamepal" / "ui_build.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("No se encuentra gamepal/ui_build.py")
        ui_build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui_build)

        source = ui_build.source_dir()
        if source is not None and ui_build.needs_build(source):
            ui_build.build(source, print)
        static = ui_build.STATIC_DIR
        if not (static / "index.html").is_file():
            raise RuntimeError("La GUI no está compilada y no se puede compilar aquí: " + ui_build.NODE_MISSING)
        # La carpeta compilada está en .gitignore: los artefactos se incluyen aunque git los ignore.
        build_data["artifacts"].append("/gamepal/gui_server/static/")
