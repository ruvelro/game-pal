"""Extracción de datos de una pantalla reconocida: listas de opciones y valores sueltos."""

from __future__ import annotations

from typing import Any

from gamepal.perception.signals import Context, Hit, regex, same_row
from gamepal.profiles.signals import Extract, FieldRule, ListRule


def _vocab_value(ctx: Context, rule: FieldRule, text: str, language: str) -> dict[str, str] | None:
    if rule.vocabulary is None:
        return None
    found = ctx.vocabulary.find(rule.vocabulary, text)
    if not found:
        return None
    entry = found[0].entry
    return {"id": entry.id, "name": entry.display(language), "seen": text}


def _field(ctx: Context, anchor: Hit, next_anchor_y: float, rule: FieldRule, language: str) -> Any:
    if rule.vocabulary is not None and not ctx.vocabulary.has_set(rule.vocabulary):
        # Sin ese vocabulario (p. ej. juego no instalado) se usa el texto leído tal cual.
        rule = rule.model_copy(update={"vocabulary": None})
    lines = ctx.lines
    if rule.source == "anchor":
        pool = [anchor.line]
    elif rule.source == "same_row":
        pool = [line for i, line in enumerate(lines) if i != anchor.index and same_row(line.box, anchor.line.box)]
        pool.sort(key=lambda line: abs(line.box.center_x - anchor.line.box.center_x))
    else:
        a = anchor.line.box
        # El bloque de la opción abarca toda su fila, no solo la línea ancla.
        row = [line.box for line in lines if same_row(line.box, a)]
        left = min(box.x for box in row)
        right = max(box.x + box.width for box in row)
        pool = [
            line
            for i, line in enumerate(lines)
            if i != anchor.index
            and a.center_y < line.box.center_y < next_anchor_y
            and not same_row(line.box, a)
            and min(right, line.box.x + line.box.width) > max(left, line.box.x)
        ]
        pool.sort(key=lambda line: line.box.y)
        pool = pool[: rule.max_lines]
        if rule.vocabulary is None and rule.pattern is None:
            return " ".join(line.text for line in pool) or None
    for line in pool:
        if rule.pattern is not None:
            match = regex(rule.pattern).search(line.text)
            if match is None:
                continue
            if rule.vocabulary is None:
                return match.group(0)
        value = _vocab_value(ctx, rule, line.text, language)
        if value is not None:
            return value
        if rule.vocabulary is None and rule.pattern is None:
            return line.text
    return None


def extract_list(ctx: Context, rule: ListRule, language: str) -> list[dict[str, Any]]:
    anchors: list[Hit] = []
    for hit in sorted(ctx.hits(rule.anchor), key=lambda h: h.line.box.center_y):
        # Una fila con varias anclas cuenta una vez: la de mayor puntuación.
        if anchors and same_row(anchors[-1].line.box, hit.line.box):
            if hit.score > anchors[-1].score:
                anchors[-1] = hit
            continue
        anchors.append(hit)
    anchors = anchors[: rule.max_items]
    items: list[dict[str, Any]] = []
    for position, anchor in enumerate(anchors):
        next_y = anchors[position + 1].line.box.center_y if position + 1 < len(anchors) else 1.01
        item: dict[str, Any] = {}
        for name, field_rule in rule.fields.items():
            value = _field(ctx, anchor, next_y, field_rule, language)
            if value is not None:
                item[name] = value
        items.append(item)
    return items


def extract(ctx: Context, spec: Extract, language: str) -> tuple[dict[str, Any], bool]:
    """Devuelve los datos y si la extracción está completa según los mínimos del perfil."""
    data: dict[str, Any] = {}
    complete = True
    for name, rule in spec.lists.items():
        items = extract_list(ctx, rule, language)
        data[name] = items
        if len(items) < rule.min_items or any(len(item) < len(rule.fields) for item in items):
            complete = False
    for name, query in spec.values.items():
        hits = ctx.hits(query)
        if hits:
            best = max(hits, key=lambda h: h.score)
            if best.match is not None:
                entry = best.match.entry
                data[name] = {"id": entry.id, "name": entry.display(language), "seen": best.line.text}
            else:
                data[name] = best.line.text
        else:
            complete = False
    return data, complete
