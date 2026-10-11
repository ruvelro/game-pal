"""Comprobaciones de coherencia de un perfil que el esquema no puede hacer por sí solo."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import yaml
from pydantic import BaseModel

from gamepal.profiles.loader import LoadedProfile


def _references(node: Any) -> Iterator[str]:
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name)
            if name == "vocabulary" and isinstance(value, str):
                yield value
            else:
                yield from _references(value)
    elif isinstance(node, list | tuple):
        for item in node:
            yield from _references(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _references(item)


def declared_sets(loaded: LoadedProfile) -> set[str]:
    sets = {source.set for source in loaded.profile.vocabulary.game_text}
    for relative in loaded.profile.vocabulary.files:
        path = (loaded.root / relative).resolve()
        if path.is_relative_to(loaded.root.resolve()) and path.is_file():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and isinstance(raw.get("sets"), dict):
                sets.update(str(name) for name in raw["sets"])
    return sets


def lint(loaded: LoadedProfile) -> list[str]:
    """Avisos legibles; una lista vacía significa que el perfil es coherente."""
    warnings: list[str] = []
    profile = loaded.profile
    try:
        declared = declared_sets(loaded)
    except (OSError, yaml.YAMLError) as exc:
        return [f"No se pudo leer el vocabulario: {exc}"]
    for relative in profile.vocabulary.files:
        if not (loaded.root / relative).is_file():
            warnings.append(f"Falta el fichero de vocabulario '{relative}'")
    for screen in profile.screens:
        for name in sorted(set(_references(screen))):
            if name not in declared:
                warnings.append(f"La pantalla '{screen.id}' usa el vocabulario '{name}', que no está declarado")
    for role in profile.prompts:
        try:
            loaded.read_prompt(role)
        except OSError:
            warnings.append(f"Falta el prompt '{role}'")
    return warnings
