"""Rutas de datos por sistema operativo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "game-pal"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def user_profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    def ensure(self) -> None:
        for path in (self.config_dir, self.data_dir, self.cache_dir, self.log_dir, self.user_profiles_dir):
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                # Solo el usuario actual puede leer sus datos.
                path.chmod(0o700)


def default_paths() -> AppPaths:
    override = os.environ.get("GAMEPAL_HOME")
    if override:
        root = Path(override).expanduser().resolve()
        return AppPaths(root / "config", root / "data", root / "cache", root / "logs")
    dirs = PlatformDirs(APP_NAME, appauthor=False)
    return AppPaths(
        Path(dirs.user_config_dir),
        Path(dirs.user_data_dir),
        Path(dirs.user_cache_dir),
        Path(dirs.user_log_dir),
    )


def bundled_profiles_dir() -> Path:
    """Perfiles incluidos: en el repo están en ./profiles; instalados, dentro del paquete."""
    package_dir = Path(__file__).resolve().parent
    installed = package_dir / "_bundled_profiles"
    if installed.is_dir():
        return installed
    return package_dir.parent / "profiles"
