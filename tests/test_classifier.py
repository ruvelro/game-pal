from pathlib import Path

import pytest

from gamepal.perception.classifier import Classification, ScreenTracker, classify
from gamepal.perception.vocabulary import build_vocabulary
from gamepal.profiles import LoadedProfile, load_profile
from tests.conftest import line
from tests.fixtures.synthetic import write_game_text


@pytest.fixture
def hades(repo_profiles: Path) -> LoadedProfile:
    return load_profile(repo_profiles / "hades-2", bundled=True)


def _menu(title: str, rarities: list[str]) -> list:
    names = ["Storm Lash", "Royal Spite", "Solar Ring"]
    lines = [line(title, 0.36, 0.06, w=0.3)]
    for i, rarity in enumerate(rarities):
        y = 0.24 + i * 0.22
        lines += [
            line(names[i], 0.28, y),
            line(rarity, 0.72, y, w=0.06),
            line(f"Effect {i} +20%", 0.28, y + 0.065, w=0.3),
        ]
    return lines


def test_boon_choice_from_game_text(hades: LoadedProfile, tmp_path: Path) -> None:
    vocab = build_vocabulary(hades.profile, hades.root, write_game_text(tmp_path), ["en", "es"])
    result = classify(hades.profile, vocab, _menu("Something", ["Rare", "Common", "Epic"]), "es")
    assert result.screen_id == "boon_choice"
    assert result.certain
    assert result.complete
    options = result.data["options"]
    assert [o["name"]["name"] for o in options] == ["Latigazo de Tormenta", "Rencor Regio", "Anillo Solar"]
    assert [o["rarity"]["id"] for o in options] == ["rare", "common", "epic"]
    assert options[0]["effect"] == "Effect 0 +20%"


def test_boon_choice_from_title_without_game_text(hades: LoadedProfile) -> None:
    vocab = build_vocabulary(hades.profile, hades.root, None, ["en"])
    result = classify(hades.profile, vocab, _menu("Boons of Zeus", ["Rare", "Common", "Epic"]), "en")
    assert result.screen_id == "boon_choice"
    assert result.data["options"][1]["name"] == "Royal Spite"


def test_pause_menu_is_not_a_choice(hades: LoadedProfile, tmp_path: Path) -> None:
    vocab = build_vocabulary(hades.profile, hades.root, write_game_text(tmp_path), ["en"])
    result = classify(hades.profile, vocab, _menu("Inventory", ["Rare", "Common"]), "en")
    assert result.screen_id == "reading_menu"


def test_boss_intro_and_unknown_screen(hades: LoadedProfile) -> None:
    vocab = build_vocabulary(hades.profile, hades.root, None, ["en"])
    boss = classify(hades.profile, vocab, [line("Chronos", 0.4, 0.5), line("Titan of Time", 0.38, 0.6)], "es")
    assert boss.screen_id == "boss_intro"
    assert boss.data["boss"]["name"] == "Cronos"
    nothing = classify(hades.profile, vocab, [line("Hola", 0.4, 0.5)], "es")
    assert nothing.screen is None


def _c(hades: LoadedProfile, screen_id: str | None, certain: bool) -> Classification:
    screen = next((s for s in hades.profile.screens if s.id == screen_id), None)
    return Classification(screen, 0.9 if certain else 0.5, certain, True)


def test_tracker_needs_two_reads_unless_certain(hades: LoadedProfile) -> None:
    tracker = ScreenTracker()
    assert tracker.feed(_c(hades, "victory", False)) == []
    assert tracker.wants_reread
    entered = tracker.feed(_c(hades, "victory", False))
    assert [t.kind for t in entered] == ["enter"]
    switched = tracker.feed(_c(hades, "boss_intro", True))
    assert [t.kind for t in switched] == ["leave", "enter"]


def test_tracker_leaves_after_two_misses(hades: LoadedProfile) -> None:
    tracker = ScreenTracker()
    tracker.feed(_c(hades, "victory", True))
    assert tracker.feed(_c(hades, None, True)) == []
    assert tracker.wants_reread
    assert [t.kind for t in tracker.feed(_c(hades, None, True))] == ["leave"]
    assert tracker.current is None
