import numpy as np

from gamepal.events import Box
from gamepal.perception.signals import Context, evaluate, in_bands
from gamepal.perception.vocabulary import Entry, Vocabulary
from gamepal.profiles.signals import SignalNode
from tests.conftest import line

VOCAB = Vocabulary()
for name in ("Rare", "Common", "Epic"):
    VOCAB.add(Entry(name.lower(), "rarity", {"en": name}))
VOCAB.add(Entry("hecate", "bosses", {"en": "Hecate"}))
VOCAB.add(Entry("hecate", "titles", {"en": "Witch of the Crossroads"}))

MENU = [
    line("Boons of Zeus", 0.4, 0.06),
    line("Storm Lash", 0.28, 0.25),
    line("Rare", 0.72, 0.25, w=0.05),
    line("Royal Spite", 0.28, 0.47),
    line("Common", 0.72, 0.47, w=0.06),
    line("Back", 0.05, 0.93, w=0.05),
]


def node(data: dict) -> SignalNode:
    return SignalNode.model_validate(data)


def test_bands() -> None:
    top_left = Box(x=0.05, y=0.05, width=0.1, height=0.04)
    assert in_bands(top_left, ("top", "left"))
    assert not in_bands(top_left, ("bottom",))
    assert in_bands(top_left, ("any",))


def test_text_matches_with_pattern_and_region() -> None:
    ctx = Context(MENU, VOCAB)
    assert evaluate(node({"text_matches": {"pattern": "^boons? of", "region": "top"}}), ctx).matched
    assert not evaluate(node({"text_matches": {"pattern": "^boons? of", "region": "bottom"}}), ctx).matched


def test_pattern_matches_without_accents() -> None:
    ctx = Context([line("Elige una bendición", 0.3, 0.05)], VOCAB)
    assert evaluate(node({"text_matches": {"pattern": "bendicion"}}), ctx).matched


def test_repeated_rows_counts_distinct_rows() -> None:
    ctx = Context(MENU, VOCAB)
    assert evaluate(node({"repeated_rows": {"vocabulary": "rarity", "min": 2}}), ctx).matched
    assert not evaluate(node({"repeated_rows": {"vocabulary": "rarity", "min": 3}}), ctx).matched


def test_relations() -> None:
    lines = [line("HECATE", 0.4, 0.52), line("Witch of the Crossroads", 0.36, 0.62, w=0.3)]
    ctx = Context(lines, VOCAB)
    rel = {"anchor": {"vocabulary": "bosses"}, "target": {"vocabulary": "titles"}}
    assert evaluate(node({"text_below": rel}), ctx).matched
    assert not evaluate(node({"text_above": rel}), ctx).matched
    row = Context([line("Storm Lash", 0.28, 0.25), line("Rare", 0.72, 0.252, w=0.05)], VOCAB)
    assert evaluate(
        node({"same_row": {"anchor": {"pattern": "storm"}, "target": {"vocabulary": "rarity"}}}), row
    ).matched


def test_combinators() -> None:
    ctx = Context(MENU, VOCAB)
    tree = node(
        {
            "any_of": [{"text_matches": {"pattern": "nothing"}}, {"repeated_rows": {"vocabulary": "rarity"}}],
            "none_of": [{"text_matches": {"pattern": "^paused$"}}],
        }
    )
    assert evaluate(tree, ctx).matched
    blocked = node({"repeated_rows": {"vocabulary": "rarity"}, "none_of": [{"text_matches": {"pattern": "back"}}]})
    outcome = evaluate(blocked, ctx)
    assert not outcome.matched
    assert "none_of" in outcome.failed


def test_absent_and_count() -> None:
    ctx = Context(MENU, VOCAB)
    assert evaluate(node({"absent": {"pattern": "paused"}}), ctx).matched
    assert evaluate(node({"count_at_least": {"lines": 3, "region": "left"}}), ctx).matched is False
    assert evaluate(node({"count_at_least": {"lines": 6}}), ctx).matched


def test_color_dominant() -> None:
    image = np.zeros((90, 160, 3), dtype=np.uint8)
    image[:, :] = (180, 20, 20)
    ctx = Context([], VOCAB, image)
    assert evaluate(node({"color_dominant": {"color": "#b41414", "min_fraction": 0.8}}), ctx).matched
    assert not evaluate(node({"color_dominant": {"color": "#14b414"}}), ctx).matched
    assert not evaluate(node({"color_dominant": {"color": "#b41414"}}), Context([], VOCAB)).matched


def test_missing_vocabulary_set_never_matches() -> None:
    ctx = Context(MENU, VOCAB)
    assert not evaluate(node({"text_matches": {"vocabulary": "no_existe"}}), ctx).matched
