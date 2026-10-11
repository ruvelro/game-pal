"""Catálogo de modelos descargables y del motor llama.cpp (models/catalog.yaml)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gamepal.hardware import Arch, HardwareInfo, OsName

SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CATALOG_FILE = "catalog.yaml"

RoleName = Literal["reasoning", "fast", "vision"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_sha(value: str) -> str:
    if not SHA256.match(value):
        raise ValueError("sha256 debe tener 64 caracteres hexadecimales en minúsculas")
    return value


def _check_name(value: str) -> str:
    if not SAFE_NAME.match(value) or ".." in value:
        raise ValueError(f"Nombre de fichero no válido: {value}")
    return value


class RuntimeVariant(_Model):
    id: str
    os: OsName
    arch: Arch
    accel: Literal["cpu", "metal", "vulkan", "cuda", "rocm"]
    asset: str
    size: int = Field(gt=0)
    sha256: str

    _sha = field_validator("sha256")(_check_sha)
    _asset = field_validator("asset")(_check_name)


class RuntimeSpec(_Model):
    name: str
    tag: str = Field(pattern=r"^b\d+$")
    url: str
    license: str
    variants: list[RuntimeVariant]

    def asset_url(self, variant: RuntimeVariant) -> str:
        return self.url.format(tag=self.tag, asset=variant.asset)

    def variant(self, variant_id: str) -> RuntimeVariant | None:
        return next((v for v in self.variants if v.id == variant_id), None)

    def choose(self, info: HardwareInfo, preferred: str = "auto") -> RuntimeVariant | None:
        """La variante para este equipo: la elegida a mano o la mejor que pueda funcionar."""
        if preferred != "auto":
            chosen = self.variant(preferred)
            if chosen and chosen.os == info.os and chosen.arch == info.arch:
                return chosen
        options = [v for v in self.variants if v.os == info.os and v.arch == info.arch]
        if not options:
            return None
        # En ARM64 de Windows la CPU va mejor que Vulkan con la mayoría de controladores Adreno.
        wants_vulkan = info.vulkan and info.arch == "x64" and any(not g.integrated for g in info.gpus)
        order = {"metal": 0, "cuda": 1, "vulkan": 2 if wants_vulkan else 9, "cpu": 5, "rocm": 8}
        return min(options, key=lambda v: order.get(v.accel, 10))


class ModelFile(_Model):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str
    size: int = Field(gt=0)
    sha256: str
    kind: Literal["model", "mmproj"] = "model"

    _sha = field_validator("sha256")(_check_sha)
    _path = field_validator("path")(_check_name)

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.path}"


class ModelEntry(_Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    name: str
    family: str
    license: str
    roles: list[RoleName]
    description: str = ""
    files: list[ModelFile] = Field(min_length=1)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def vision(self) -> bool:
        return any(f.kind == "mmproj" for f in self.files)


class TierPreset(_Model):
    tier: int = Field(ge=0, le=3)
    reasoning: str
    fast: str
    vision: str = ""
    stt: str
    context: int = Field(ge=2048, le=262144)
    models_max: int = Field(ge=1, le=8)


class Catalog(_Model):
    version: int
    runtime: RuntimeSpec
    models: list[ModelEntry]
    tiers: list[TierPreset]

    def model(self, model_id: str) -> ModelEntry | None:
        return next((m for m in self.models if m.id == model_id), None)

    def tier(self, tier: int) -> TierPreset:
        return next(t for t in self.tiers if t.tier == tier)

    def tier_models(self, tier: int) -> list[ModelEntry]:
        preset = self.tier(tier)
        ids = dict.fromkeys(i for i in (preset.reasoning, preset.fast, preset.vision) if i)
        return [m for i in ids if (m := self.model(i)) is not None]


def bundled_catalog_path() -> Path:
    package_dir = Path(__file__).resolve().parent.parent
    installed = package_dir / "_bundled_models" / CATALOG_FILE
    return installed if installed.is_file() else package_dir.parent / "models" / CATALOG_FILE


def load_catalog(path: Path) -> Catalog:
    catalog = Catalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    ids = [m.id for m in catalog.models]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay modelos repetidos en el catálogo")
    for preset in catalog.tiers:
        for role in ("reasoning", "fast", "vision"):
            model_id = getattr(preset, role)
            if model_id and catalog.model(model_id) is None:
                raise ValueError(f"El nivel {preset.tier} usa un modelo que no existe: {model_id}")
            if role == "vision" and model_id and not catalog.model(model_id).vision:  # type: ignore[union-attr]
                raise ValueError(f"El nivel {preset.tier} usa para visión un modelo sin visión: {model_id}")
    return catalog


@lru_cache(maxsize=1)
def bundled_catalog() -> Catalog:
    return load_catalog(bundled_catalog_path())
