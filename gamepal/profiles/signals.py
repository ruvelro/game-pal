"""Lenguaje declarativo de señales y extracción.

Nada usa coordenadas: solo bandas relativas de la pantalla (arriba, abajo, izquierda…) y
relaciones entre líneas de texto (debajo de, en la misma fila). Un nodo de señales es el AND
de todas las condiciones que declara; `any_of` y `none_of` permiten OR y negación.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Band = Literal["top", "middle", "bottom", "left", "center", "right", "any"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextQuery(_Model):
    """Qué texto buscar: una expresión regular, un conjunto del vocabulario, o ambos."""

    pattern: str | None = Field(default=None, description="Expresión regular sobre el texto en minúsculas")
    vocabulary: str | None = Field(default=None, description="Nombre de un conjunto del vocabulario")
    region: Band | list[Band] = Field(default="any", description="Bandas de pantalla; una lista es su intersección")
    fuzzy: int | None = Field(
        default=None, ge=0, le=4, description="Errores de OCR tolerados; por defecto según longitud"
    )

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 300:
            raise ValueError("La expresión regular es demasiado larga")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"Expresión regular no válida: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _needs_something(self) -> TextQuery:
        if self.pattern is None and self.vocabulary is None:
            raise ValueError("Indica 'pattern', 'vocabulary' o ambos")
        return self

    @property
    def bands(self) -> tuple[Band, ...]:
        return tuple(self.region) if isinstance(self.region, list) else (self.region,)


class TextMatches(TextQuery):
    min_count: int = Field(default=1, ge=1, le=50)


class RepeatedRows(TextQuery):
    """Varias filas distintas que contienen el texto buscado: listas de opciones, tiendas, inventarios."""

    min: int = Field(default=2, ge=1, le=50)


class Relation(_Model):
    """Una línea `target` situada respecto a una línea `anchor`."""

    anchor: TextQuery
    target: TextQuery
    max_distance: float = Field(default=0.25, gt=0, le=1, description="Distancia máxima en fracción de pantalla")


class CountAtLeast(_Model):
    lines: int = Field(ge=1, le=500)
    region: Band | list[Band] = "any"


class ColorDominant(_Model):
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    min_fraction: float = Field(default=0.3, gt=0, le=1)
    tolerance: int = Field(default=40, ge=1, le=255)
    region: Band | list[Band] = "any"


class SignalNode(_Model):
    all_of: list[SignalNode] | None = None
    any_of: list[SignalNode] | None = None
    none_of: list[SignalNode] | None = None
    text_matches: TextMatches | None = None
    repeated_rows: RepeatedRows | None = None
    text_below: Relation | None = None
    text_above: Relation | None = None
    same_row: Relation | None = None
    count_at_least: CountAtLeast | None = None
    absent: TextQuery | None = None
    color_dominant: ColorDominant | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> SignalNode:
        if not any(getattr(self, name) is not None for name in type(self).model_fields):
            raise ValueError("Un nodo de señales debe tener al menos una condición")
        return self


class FieldRule(_Model):
    """Cómo obtener un campo de cada elemento de una lista.

    - anchor: el texto de la propia línea ancla.
    - same_row: otra línea en la misma fila que el ancla.
    - below: las líneas bajo el ancla, hasta la siguiente ancla.
    """

    source: Literal["anchor", "same_row", "below"] = "anchor"
    vocabulary: str | None = None
    pattern: str | None = None
    max_lines: int = Field(default=3, ge=1, le=12)


class ListRule(_Model):
    anchor: TextQuery
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    min_items: int = Field(default=1, ge=0, le=20)
    max_items: int = Field(default=8, ge=1, le=40)


class Extract(_Model):
    lists: dict[str, ListRule] = Field(default_factory=dict)
    values: dict[str, TextQuery] = Field(default_factory=dict)
