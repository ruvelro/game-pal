"""Registro de hechos de la partida con su origen y su rango.

Rangos (menor es más fiable):
1. Guardado del juego.
2. Lo que dice el jugador.
3. Lo que se ve repetidamente en una pantalla que muestra lo que se tiene.
4. Lo que se ofrece en pantalla (nunca implica posesión).
5. Inferencias de un modelo (no se guardan como hechos).

Un hecho más fiable nunca se sobrescribe con uno menos fiable de la misma partida. Al cambiar de
partida se olvida todo lo que no venga del guardado.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field, replace
from enum import IntEnum
from typing import Any


class Rank(IntEnum):
    SAVE = 1
    PLAYER = 2
    SEEN = 3
    OFFERED = 4


ORIGIN = {Rank.SAVE: "save", Rank.PLAYER: "player", Rank.SEEN: "screen", Rank.OFFERED: "offer"}
# Orden de presentación: lo que más pesa en una decisión va primero.
KIND_ORDER = ["weapon", "aspect", "keepsake", "familiar", "boon", "hammer", "item", "room", "stat", "arcana"]


def _order(fact: Fact) -> tuple[int, int, str]:
    kind = KIND_ORDER.index(fact.kind) if fact.kind in KIND_ORDER else len(KIND_ORDER)
    return kind, fact.rank, fact.key


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    kind: str
    subject: str
    label: str
    rank: Rank
    value: str | None = None
    evidence: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    run: str | None = None
    at: float = field(default_factory=time.time)

    @property
    def origin(self) -> str:
        return ORIGIN[self.rank]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rank"] = int(self.rank)
        data["origin"] = self.origin
        return data


class Ledger:
    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}
        self._offers: list[Fact] = []
        self.run: str | None = None
        self.save_summary: dict[str, Any] = {}
        self.save_at: float | None = None
        self.save_path: str | None = None
        self._lock = threading.Lock()

    def _start_run(self, run: str | None) -> None:
        if run is not None and run != self.run:
            # Partida nueva: lo dicho o visto en la anterior ya no vale.
            self._facts = {k: f for k, f in self._facts.items() if f.rank == Rank.SAVE}
            self._offers = []
            self.run = run

    def add(self, fact: Fact) -> bool:
        """Añade un hecho si no contradice uno más fiable. Devuelve si se aceptó."""
        with self._lock:
            if fact.rank == Rank.OFFERED:
                raise ValueError("Las ofertas se registran con set_offers")
            current = self._facts.get(fact.key)
            if current is not None and current.rank < fact.rank and current.at >= fact.at - 3600:
                return False
            self._facts[fact.key] = fact if fact.run else replace(fact, run=self.run)
            return True

    def apply_save(self, run: str | None, facts: list[Fact], summary: dict[str, Any], saved_at: float,
                   path: str) -> None:  # fmt: skip
        """Sustituye todo lo que venía del guardado por la foto nueva."""
        with self._lock:
            self._start_run(run)
            keep = {k: f for k, f in self._facts.items() if f.rank != Rank.SAVE}
            for fact in facts:
                # El guardado manda: lo que el jugador dijo sobre lo mismo queda superado.
                keep.pop(fact.key, None)
            self._facts = {**keep, **{f.key: f for f in facts}}
            self.save_summary, self.save_at, self.save_path = summary, saved_at, path

    def set_offers(self, offers: list[Fact]) -> None:
        with self._lock:
            self._offers = list(offers)

    def offers(self) -> list[Fact]:
        with self._lock:
            return list(self._offers)

    def facts(self) -> list[Fact]:
        with self._lock:
            return sorted(self._facts.values(), key=_order)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run": self.run,
                "facts": [f.to_dict() for f in sorted(self._facts.values(), key=_order)],
                "offers": [f.to_dict() for f in self._offers],
                "save": {"at": self.save_at, "path": self.save_path, "summary": self.save_summary},
            }

    def prompt(self, max_choices: int = 12) -> str:
        """Texto para el modelo, con el origen de cada dato a la vista."""
        snap = self.snapshot()
        if not snap["facts"] and not snap["offers"]:
            return ""
        labels = {"save": "guardado", "player": "lo dijo el jugador", "screen": "visto en pantalla"}
        lines = ["<estado_partida>"]
        for fact in snap["facts"]:
            extra = ", ".join(f"{k}={v}" for k, v in fact["details"].items() if v not in (None, ""))
            value = f": {fact['value']}" if fact["value"] else ""
            lines.append(
                f"- {fact['kind']} {fact['label']}{value}{f' ({extra})' if extra else ''} [{labels[fact['origin']]}]"
            )
        if snap["offers"]:
            lines.append("Se ofrece ahora (todavía no lo tiene): " + ", ".join(f["label"] for f in snap["offers"]))
        summary = snap["save"]["summary"] or {}
        choices = summary.get("choices") or []
        if choices:
            picked = [c.get("label") or c.get("chosen") for c in choices if c.get("chosen")][-max_choices:]
            lines.append(f"Elecciones ({summary.get('choices_belong_to', '')}): " + ", ".join(map(str, picked)))
        lines.append("</estado_partida>")
        return "\n".join(lines)
