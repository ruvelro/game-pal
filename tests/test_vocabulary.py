from pathlib import Path

import pytest

from gamepal.perception.text import normalize
from gamepal.perception.vocabulary import Entry, Vocabulary, build_vocabulary, load_game_text, load_yaml
from gamepal.profiles import load_profile
from gamepal.profiles.model import GameTextSource
from tests.fixtures.synthetic import write_game_text


def _vocab() -> Vocabulary:
    v = Vocabulary()
    v.add(Entry("strike", "boons", {"en": "Heaven Strike", "es": "Golpe Celestial"}))
    v.add(Entry("flourish", "boons", {"en": "Heaven Flourish"}))
    v.add(Entry("rare", "rarity", {"en": "Rare", "es": "Raro"}))
    return v


def test_normalize_strips_case_accents_and_punctuation() -> None:
    assert normalize("¡Elige una BENDICIÓN!") == "elige una bendicion"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HEAVEN STRIKE", "strike"),
        ("Heaven Strlke", "strike"),  # error de OCR
        ("Golpe Celestlal  RARO", "strike"),
        ("Heaven Flourish", "flourish"),
    ],
)
def test_find_tolerates_ocr_errors(text: str, expected: str) -> None:
    assert _vocab().find("boons", text)[0].entry.id == expected


def test_short_words_must_match_exactly() -> None:
    v = _vocab()
    assert v.find("rarity", "Rare")[0].distance == 0
    assert v.find("rarity", "Bare") == []


def test_matches_do_not_overlap() -> None:
    found = _vocab().find("boons", "Heaven Strike Heaven Flourish")
    assert sorted(m.entry.id for m in found) == ["flourish", "strike"]
    assert len({m.start for m in found}) == 2


def test_display_prefers_requested_language() -> None:
    entry = _vocab().get("boons", "strike")
    assert entry is not None
    assert entry.display("es") == "Golpe Celestial"
    assert entry.display("fr") == "Heaven Strike"


def test_load_yaml_accepts_plain_and_detailed_entries(tmp_path: Path) -> None:
    path = tmp_path / "v.yaml"
    path.write_text(
        "sets:\n  gods: [Zeus, Hera]\n  bosses:\n    - id: hecate\n      names: {en: Hecate, es: Hécate}\n"
        "      aliases: {en: [Witch of the Crossroads]}\n",
        encoding="utf-8",
    )
    v = Vocabulary()
    load_yaml(v, path)
    assert v.sets() == {"bosses": 1, "gods": 2}
    assert v.find("bosses", "witch of the crossroads")[0].entry.id == "hecate"


def test_load_game_text_reads_every_language(tmp_path: Path) -> None:
    write_game_text(tmp_path)
    v = Vocabulary()
    source = GameTextSource(set="boons", path="Content/Game/Text/{lang}/TraitText.{lang}.sjson", id_pattern="Boon$")
    count = load_game_text(v, tmp_path, source, {"en": "en", "es": "es", "fr": "fr"})
    assert count == 4
    entry = v.get("boons", "ZeusWeaponBoon")
    assert entry is not None
    assert entry.names == {"en": "Storm Lash", "es": "Latigazo de Tormenta"}
    assert v.get("boons", "LuckyCoinKeepsake") is None


def test_game_text_inside_macos_app_bundle(tmp_path: Path) -> None:
    write_game_text(tmp_path / "Game.app" / "Contents" / "Resources")
    v = Vocabulary()
    source = GameTextSource(set="boons", path="Content/Game/Text/{lang}/TraitText.{lang}.sjson")
    assert load_game_text(v, tmp_path, source, {"en": "en"}) == 5


def test_game_text_paths_must_be_relative() -> None:
    with pytest.raises(ValueError, match="relativa"):
        GameTextSource(set="boons", path="../../etc/passwd")


def test_hades_vocabulary_without_game_install(repo_profiles: Path) -> None:
    loaded = load_profile(repo_profiles / "hades-2", bundled=True)
    v = build_vocabulary(loaded.profile, loaded.root, None, ["es", "en"])
    assert "boons" not in v.sets()
    assert v.find("bosses", "HÉCATE")[0].entry.id == "hecate"
    assert v.find("rarity", "Legendario")[0].entry.id == "legendary"
