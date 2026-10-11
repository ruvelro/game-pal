"""Evaluación de señales declarativas sobre las líneas de texto (y opcionalmente la imagen)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from gamepal.capture.base import RGBImage
from gamepal.events import Box, TextLine
from gamepal.perception.text import fold, normalize
from gamepal.perception.vocabulary import Match, Vocabulary
from gamepal.profiles.signals import Band, ColorDominant, Relation, SignalNode, TextQuery

THIRD = 1 / 3
ROW_TOLERANCE = 0.6  # fracción de la altura de línea para considerar dos líneas en la misma fila


@dataclass(frozen=True, slots=True)
class Hit:
    index: int
    line: TextLine
    score: float
    match: Match | None = None


@dataclass(slots=True)
class Outcome:
    matched: bool
    score: float
    leaves: int
    evidence: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@lru_cache(maxsize=512)
def regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def in_bands(box: Box, bands: tuple[Band, ...]) -> bool:
    cx, cy = box.center_x, box.center_y
    for band in bands:
        ok = {
            "any": True,
            "top": cy < THIRD,
            "middle": THIRD <= cy <= 2 * THIRD,
            "bottom": cy > 2 * THIRD,
            "left": cx < THIRD,
            "center": THIRD <= cx <= 2 * THIRD,
            "right": cx > 2 * THIRD,
        }[band]
        if not ok:
            return False
    return True


def _bands(region: Band | list[Band]) -> tuple[Band, ...]:
    return tuple(region) if isinstance(region, list) else (region,)


class Context:
    """Líneas de una pantalla más todo lo necesario para evaluar señales sobre ellas."""

    def __init__(self, lines: list[TextLine], vocabulary: Vocabulary, image: RGBImage | None = None) -> None:
        self.lines = lines
        self.vocabulary = vocabulary
        self.image = image
        self._folded = [fold(line.text) for line in lines]
        self._normalized = [normalize(line.text) for line in lines]
        # Clave estable por contenido: las consultas pueden contener listas y no son hashables.
        self._hits: dict[str, list[Hit]] = {}

    def hits(self, query: TextQuery) -> list[Hit]:
        key = query.model_dump_json()
        cached = self._hits.get(key)
        if cached is not None:
            return cached
        bands = query.bands
        if query.vocabulary and not self.vocabulary.has_set(query.vocabulary) and query.pattern is None:
            self._hits[key] = []
            return []
        result: list[Hit] = []
        for index, line in enumerate(self.lines):
            if not in_bands(line.box, bands):
                continue
            score = 1.0
            match: Match | None = None
            if query.pattern is not None:
                compiled = regex(query.pattern)
                if not (compiled.search(self._folded[index]) or compiled.search(self._normalized[index])):
                    continue
            if query.vocabulary is not None and self.vocabulary.has_set(query.vocabulary):
                found = self.vocabulary.find(query.vocabulary, line.text, query.fuzzy)
                if not found:
                    continue
                match = found[0]
                score = match.score
            result.append(Hit(index, line, score * min(1.0, 0.5 + line.confidence / 2), match))
        self._hits[key] = result
        return result


def _rows(hits: list[Hit]) -> list[list[Hit]]:
    rows: list[list[Hit]] = []
    for hit in sorted(hits, key=lambda h: h.line.box.center_y):
        if rows and same_row(rows[-1][0].line.box, hit.line.box):
            rows[-1].append(hit)
        else:
            rows.append([hit])
    return rows


def same_row(a: Box, b: Box) -> bool:
    return abs(a.center_y - b.center_y) <= max(a.height, b.height) * ROW_TOLERANCE


def _overlap_x(a: Box, b: Box) -> bool:
    return min(a.x + a.width, b.x + b.width) > max(a.x, b.x) - 0.02


def _relation(ctx: Context, rel: Relation, kind: str) -> tuple[bool, float, list[str]]:
    best = 0.0
    evidence: list[str] = []
    for anchor in ctx.hits(rel.anchor):
        for target in ctx.hits(rel.target):
            if target.index == anchor.index:
                continue
            a, t = anchor.line.box, target.line.box
            if kind == "below":
                ok = 0 < t.y - (a.y + a.height / 2) <= rel.max_distance and _overlap_x(a, t)
            elif kind == "above":
                ok = 0 < a.y - (t.y + t.height / 2) <= rel.max_distance and _overlap_x(a, t)
            else:
                ok = same_row(a, t) and abs(t.center_x - a.center_x) <= max(rel.max_distance, 0.5)
            if ok:
                score = (anchor.score + target.score) / 2
                if score > best:
                    best, evidence = score, [anchor.line.text, target.line.text]
    return best > 0, best, evidence


def _color(ctx: Context, rule: ColorDominant) -> tuple[bool, float]:
    if ctx.image is None:
        return False, 0.0
    image = ctx.image
    h, w = image.shape[:2]
    ys = slice(0, h)
    xs = slice(0, w)
    for band in _bands(rule.region):
        if band == "top":
            ys = slice(0, h // 3)
        elif band == "middle":
            ys = slice(h // 3, 2 * h // 3)
        elif band == "bottom":
            ys = slice(2 * h // 3, h)
        elif band == "left":
            xs = slice(0, w // 3)
        elif band == "center":
            xs = slice(w // 3, 2 * w // 3)
        elif band == "right":
            xs = slice(2 * w // 3, w)
    region = image[ys, xs][::4, ::4].astype(np.int16)
    target = np.array([int(rule.color[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
    close = np.abs(region - target).max(axis=2) <= rule.tolerance
    fraction = float(close.mean()) if close.size else 0.0
    return fraction >= rule.min_fraction, min(1.0, fraction / rule.min_fraction)


def evaluate(node: SignalNode, ctx: Context) -> Outcome:
    """AND de todas las condiciones del nodo. La puntuación es la media de sus hojas."""
    scores: list[float] = []
    leaves = 0
    evidence: list[str] = []
    failed: list[str] = []
    matched = True

    def record(name: str, ok: bool, score: float, found: list[str]) -> None:
        nonlocal matched, leaves
        leaves += 1
        scores.append(score if ok else 0.0)
        if ok:
            evidence.extend(found)
        else:
            matched = False
            failed.append(name)

    if node.text_matches is not None:
        hits = ctx.hits(node.text_matches)
        ok = len(hits) >= node.text_matches.min_count
        record("text_matches", ok, max((h.score for h in hits), default=0.0), [h.line.text for h in hits[:3]])
    if node.repeated_rows is not None:
        rows = _rows(ctx.hits(node.repeated_rows))
        ok = len(rows) >= node.repeated_rows.min
        score = sum(max(h.score for h in row) for row in rows) / max(len(rows), 1)
        record("repeated_rows", ok, score, [row[0].line.text for row in rows[:4]])
    for name, rel, kind in (
        ("text_below", node.text_below, "below"),
        ("text_above", node.text_above, "above"),
        ("same_row", node.same_row, "row"),
    ):
        if rel is not None:
            ok, score, found = _relation(ctx, rel, kind)
            record(name, ok, score, found)
    if node.count_at_least is not None:
        bands = _bands(node.count_at_least.region)
        count = sum(1 for line in ctx.lines if in_bands(line.box, bands))
        record("count_at_least", count >= node.count_at_least.lines, 1.0, [])
    if node.absent is not None:
        hits = ctx.hits(node.absent)
        record("absent", not hits, 1.0, [])
    if node.color_dominant is not None:
        ok, score = _color(ctx, node.color_dominant)
        record("color_dominant", ok, score, [])
    for child in node.all_of or []:
        sub = evaluate(child, ctx)
        leaves += sub.leaves
        scores.extend([sub.score] * max(sub.leaves, 1))
        evidence.extend(sub.evidence)
        if not sub.matched:
            matched = False
            failed.extend(sub.failed)
    if node.any_of:
        subs = [evaluate(child, ctx) for child in node.any_of]
        best = max(subs, key=lambda o: (o.matched, o.score))
        leaves += 1
        scores.append(best.score if best.matched else 0.0)
        if best.matched:
            evidence.extend(best.evidence)
        else:
            matched = False
            failed.append("any_of")
    for child in node.none_of or []:
        sub = evaluate(child, ctx)
        leaves += 1
        scores.append(0.0 if sub.matched else 1.0)
        if sub.matched:
            matched = False
            failed.append("none_of")
    score = sum(scores) / len(scores) if scores else 0.0
    return Outcome(matched, score if matched else score * 0.5, leaves, evidence, failed)
