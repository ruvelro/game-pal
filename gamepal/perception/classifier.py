"""Clasificación de pantallas y seguimiento con histéresis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gamepal.capture.base import RGBImage
from gamepal.events import TextLine
from gamepal.perception.extract import extract
from gamepal.perception.signals import Context, evaluate
from gamepal.perception.vocabulary import Vocabulary
from gamepal.profiles.model import GameProfile, Screen

CERTAIN_SCORE = 0.8
CERTAIN_MARGIN = 0.1


@dataclass(slots=True)
class Candidate:
    screen: Screen
    matched: bool
    score: float
    evidence: list[str]
    failed: list[str]


@dataclass(slots=True)
class Classification:
    screen: Screen | None
    confidence: float
    certain: bool
    complete: bool
    data: dict[str, Any] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def screen_id(self) -> str | None:
        return self.screen.id if self.screen else None


def classify(
    profile: GameProfile,
    vocabulary: Vocabulary,
    lines: list[TextLine],
    language: str,
    image: RGBImage | None = None,
) -> Classification:
    ctx = Context(lines, vocabulary, image)
    candidates: list[Candidate] = []
    for screen in profile.screens:
        outcome = evaluate(screen.signals, ctx)
        candidates.append(Candidate(screen, outcome.matched, outcome.score, outcome.evidence, outcome.failed))
    candidates.sort(key=lambda c: (c.matched, c.score), reverse=True)
    matched = [c for c in candidates if c.matched]
    if not matched:
        return Classification(None, 0.0, True, True, candidates=candidates)
    best = matched[0]
    runner_up = matched[1].score if len(matched) > 1 else 0.0
    data, complete = extract(ctx, best.screen.extract, language)
    certain = best.score >= CERTAIN_SCORE and best.score - runner_up >= CERTAIN_MARGIN
    return Classification(best.screen, best.score, certain, complete, data, candidates)


@dataclass(slots=True)
class Transition:
    kind: str  # "enter", "update", "leave"
    screen: Screen
    classification: Classification


class ScreenTracker:
    """Confirma pantallas con dos lecturas coincidentes y las cierra con dos lecturas vacías.

    Una clasificación segura entra a la primera. `wants_reread` indica si conviene leer otra vez
    la pantalla aunque no haya cambiado, para confirmar o descartar.
    """

    def __init__(self, confirmations: int = 2) -> None:
        self.confirmations = confirmations
        self.current: Classification | None = None
        self._pending: str | None = None
        self._pending_hits = 0
        self._misses = 0
        self.wants_reread = False

    def feed(self, result: Classification) -> list[Transition]:
        transitions: list[Transition] = []
        current_id = self.current.screen_id if self.current else None
        self.wants_reread = False

        if result.screen is not None and result.screen_id == current_id:
            self._misses = 0
            self._pending = None
            changed = self.current is not None and result.data != self.current.data
            self.current = result
            if changed:
                transitions.append(Transition("update", result.screen, result))
            return transitions

        if result.screen is not None:
            if result.screen_id == self._pending:
                self._pending_hits += 1
            else:
                self._pending, self._pending_hits = result.screen_id, 1
            if result.certain or self._pending_hits >= self.confirmations:
                if self.current is not None and self.current.screen is not None:
                    transitions.append(Transition("leave", self.current.screen, self.current))
                self.current = result
                self._pending, self._pending_hits, self._misses = None, 0, 0
                transitions.append(Transition("enter", result.screen, result))
            else:
                self.wants_reread = True
            return transitions

        # Ninguna pantalla reconocida.
        self._pending, self._pending_hits = None, 0
        if self.current is not None and self.current.screen is not None:
            self._misses += 1
            if self._misses >= self.confirmations:
                transitions.append(Transition("leave", self.current.screen, self.current))
                self.current, self._misses = None, 0
            else:
                self.wants_reread = True
        return transitions
