"""Esquema de los perfiles de juego.

Un perfil describe un juego de forma declarativa. No contiene coordenadas: las pantallas se
reconocen por su texto y por relaciones relativas (las señales se evalúan a partir del hito M2).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from gamepal.profiles.signals import Extract, SignalNode

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")

Region = Literal["top", "center", "bottom", "left", "right", "any"]
Output = Literal["overlay", "text", "voice"]


def _default_outputs() -> list[Output]:
    return ["overlay", "text"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlatformPaths(_Model):
    windows: list[str] = Field(default_factory=list)
    macos: list[str] = Field(default_factory=list)
    linux: list[str] = Field(default_factory=list)


class GameInfo(_Model):
    process: list[str] = Field(default_factory=list, description="Nombres del ejecutable, sin ruta")
    window_title: str | None = Field(default=None, description="Subcadena del título de la ventana")
    languages_supported: list[str] = Field(default_factory=lambda: ["en"])
    install_paths: PlatformPaths = Field(
        default_factory=PlatformPaths, description="Carpetas típicas de instalación; admiten ~ y %VARIABLES%"
    )

    @field_validator("process")
    @classmethod
    def _no_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            if "/" in value or "\\" in value or not value.strip():
                raise ValueError(f"'{value}' debe ser solo el nombre del ejecutable")
        return values

    @field_validator("languages_supported")
    @classmethod
    def _langs(cls, values: list[str]) -> list[str]:
        for value in values:
            if not _LANG_RE.match(value):
                raise ValueError(f"Código de idioma no válido: {value}")
        return values


class ProactiveRule(_Model):
    enabled: bool = False
    cooldown_seconds: float = Field(default=10, ge=0, le=3600)
    task: str | None = None
    once_per_encounter: bool = False


class Screen(_Model):
    id: str
    description: str = ""
    signals: SignalNode
    extract: Extract = Field(default_factory=Extract)
    vision: Literal["never", "on_uncertain", "always"] = "on_uncertain"
    proactive: ProactiveRule = Field(default_factory=ProactiveRule)
    outputs: list[Output] = Field(default_factory=_default_outputs)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not re.match(r"^[a-z0-9_]{2,64}$", value):
            raise ValueError("El id de pantalla solo admite minúsculas, dígitos y guion bajo")
        return value


class GameTextSource(_Model):
    """Un fichero de texto del propio juego del que sacar nombres en todos sus idiomas."""

    set: str = Field(pattern=r"^[a-z0-9_]{2,64}$", description="Conjunto del vocabulario que alimenta")
    path: str = Field(description="Ruta relativa a la instalación; {lang} se sustituye por el idioma")
    format: Literal["sjson"] = "sjson"
    id_key: str = "Id"
    name_key: str = "DisplayName"
    id_pattern: str | None = Field(default=None, description="Solo se incluyen ids que casen con esta regex")

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/") or ":" in value:
            raise ValueError("La ruta debe ser relativa a la carpeta del juego")
        return value


class VocabularyConfig(_Model):
    files: list[str] = Field(default_factory=list, description="Ficheros YAML del perfil")
    game_text: list[GameTextSource] = Field(default_factory=list)
    language_codes: dict[str, str] = Field(
        default_factory=dict, description="Código de idioma de game-pal → código que usa el juego en sus rutas"
    )


class SavePaths(_Model):
    windows: str | None = None
    macos: str | None = None
    linux: str | None = None


class Evidence(_Model):
    save_reader: str | None = Field(default=None, description="Referencia 'plugin:Clase' del lector")
    save_paths: SavePaths = Field(default_factory=SavePaths)


class KnowledgeSource(_Model):
    url: HttpUrl
    depth: int = Field(default=1, ge=0, le=5)
    max_pages: int | None = Field(default=None, ge=1, le=2000)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    language: str | None = None


class Knowledge(_Model):
    sources: list[KnowledgeSource] = Field(default_factory=list)
    refresh: Literal["manual", "daily", "weekly", "monthly"] = "manual"


class GameProfile(_Model):
    id: str
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    description: str = ""
    game: GameInfo = Field(default_factory=GameInfo)
    screens: list[Screen] = Field(default_factory=list)
    vocabulary: VocabularyConfig = Field(default_factory=VocabularyConfig)
    evidence: Evidence = Field(default_factory=Evidence)
    knowledge: Knowledge = Field(default_factory=Knowledge)
    prompts: dict[str, str] = Field(default_factory=dict, description="Rol → fichero Markdown relativo")

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError("El id del perfil solo admite minúsculas, dígitos y guiones")
        return value

    @field_validator("screens")
    @classmethod
    def _unique_screens(cls, screens: list[Screen]) -> list[Screen]:
        ids = [s.id for s in screens]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"Pantallas duplicadas: {', '.join(sorted(duplicates))}")
        return screens

    @field_validator("vocabulary")
    @classmethod
    def _relative_vocab(cls, vocabulary: VocabularyConfig) -> VocabularyConfig:
        for path in vocabulary.files:
            if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/") or ":" in path:
                raise ValueError(f"El vocabulario '{path}' debe ser una ruta relativa dentro del perfil")
        return vocabulary

    @field_validator("prompts")
    @classmethod
    def _relative_prompts(cls, prompts: dict[str, str]) -> dict[str, str]:
        for role, path in prompts.items():
            if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/") or ":" in path:
                raise ValueError(f"El prompt '{role}' debe ser una ruta relativa dentro del perfil")
        return prompts
