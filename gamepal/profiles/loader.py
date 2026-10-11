"""Descubrimiento y carga de perfiles desde carpetas."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from gamepal.profiles.model import GameProfile

log = logging.getLogger(__name__)

PROFILE_FILE = "profile.yaml"
MAX_PROFILE_BYTES = 512 * 1024


class ProfileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    profile: GameProfile
    root: Path
    bundled: bool

    def read_prompt(self, role: str) -> str | None:
        relative = self.profile.prompts.get(role)
        if relative is None:
            return None
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ProfileError(f"El prompt '{role}' sale de la carpeta del perfil")
        return path.read_text(encoding="utf-8")


def load_profile(folder: Path, bundled: bool = False) -> LoadedProfile:
    file = folder / PROFILE_FILE
    if not file.is_file():
        raise ProfileError(f"No existe {file}")
    if file.stat().st_size > MAX_PROFILE_BYTES:
        raise ProfileError(f"{file} es demasiado grande")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"YAML no válido en {file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"{file} debe contener un objeto")
    try:
        profile = GameProfile.model_validate(raw)
    except ValidationError as exc:
        raise ProfileError(f"Perfil no válido en {file}:\n{exc}") from exc
    if profile.id != folder.name:
        raise ProfileError(f"El id '{profile.id}' debe coincidir con el nombre de la carpeta '{folder.name}'")
    return LoadedProfile(profile, folder, bundled)


class ProfileStore:
    """Perfiles incluidos más los del usuario. Un perfil de usuario con el mismo id sustituye al incluido."""

    def __init__(self, bundled_dir: Path, user_dir: Path) -> None:
        self._bundled_dir = bundled_dir
        self._user_dir = user_dir

    def discover(self) -> tuple[dict[str, LoadedProfile], dict[str, str]]:
        found: dict[str, LoadedProfile] = {}
        errors: dict[str, str] = {}
        for base, bundled in ((self._bundled_dir, True), (self._user_dir, False)):
            if not base.is_dir():
                continue
            for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
                try:
                    loaded = load_profile(folder, bundled)
                except ProfileError as exc:
                    errors[f"{folder.name}{'' if bundled else ' (usuario)'}"] = str(exc)
                    log.warning("%s", exc)
                    continue
                found[loaded.profile.id] = loaded
        return found, errors

    def get(self, profile_id: str) -> LoadedProfile:
        profiles, _ = self.discover()
        if profile_id not in profiles:
            raise ProfileError(f"No existe el perfil '{profile_id}'")
        return profiles[profile_id]
