"""Plugins de perfil: código opcional que acompaña a un perfil para lo que no se puede declarar en YAML.

Hoy el único punto de extensión es el lector de guardados. Un plugin es código Python que se ejecuta
dentro de game-pal, así que:
- los perfiles incluidos se consideran de confianza;
- los del usuario solo se cargan si este confía expresamente en ese fichero concreto (por su hash);
  si el fichero cambia, la confianza se pierde.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gamepal.profiles import LoadedProfile

log = logging.getLogger(__name__)

PLUGIN_FILE = "plugin.py"


class PluginError(RuntimeError):
    pass


class UntrustedPluginError(PluginError):
    def __init__(self, profile_id: str, digest: str) -> None:
        super().__init__(f"El perfil '{profile_id}' trae código propio y aún no has confiado en él")
        self.profile_id = profile_id
        self.digest = digest


@dataclass(frozen=True, slots=True)
class SaveFact:
    """Un hecho leído del guardado. `key` identifica el hueco (p. ej. «weapon» o «boon:ZeusWeaponBoon»)."""

    key: str
    kind: str
    subject: str
    value: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SaveSnapshot:
    path: str
    saved_at: float
    run_id: str | None
    in_run: bool
    facts: list[SaveFact]
    summary: dict[str, Any] = field(default_factory=dict)


class SaveReader(Protocol):
    def find(self, folder: Path) -> Path | None:
        """El guardado en uso dentro de la carpeta, o None."""
        ...

    def read(self, path: Path) -> SaveSnapshot: ...


def plugin_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_plugin(loaded: LoadedProfile, trusted: dict[str, str]) -> Any | None:
    """Importa el plugin del perfil si existe y es de confianza."""
    path = loaded.root / PLUGIN_FILE
    if not path.is_file():
        return None
    digest = plugin_digest(path)
    if not loaded.bundled and trusted.get(loaded.profile.id) != digest:
        raise UntrustedPluginError(loaded.profile.id, digest)
    name = f"gamepal_profile_{loaded.profile.id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"No se pudo cargar {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise PluginError(f"El plugin de '{loaded.profile.id}' falló al cargarse: {exc}") from exc
    return module


def save_reader(loaded: LoadedProfile, trusted: dict[str, str]) -> SaveReader | None:
    reference = loaded.profile.evidence.save_reader
    if not reference:
        return None
    if not reference.startswith("plugin:"):
        raise PluginError(f"Referencia de lector no válida: {reference}")
    module = load_plugin(loaded, trusted)
    if module is None:
        raise PluginError(f"El perfil declara {reference} pero no trae {PLUGIN_FILE}")
    factory = getattr(module, reference.removeprefix("plugin:"), None)
    if factory is None:
        raise PluginError(f"El plugin no define {reference.removeprefix('plugin:')}")
    reader: SaveReader = factory()
    return reader
