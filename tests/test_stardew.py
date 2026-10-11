"""Lector de partidas de Stardew Valley con un guardado sintético."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gamepal.plugins import save_reader
from gamepal.profiles.loader import load_profile

SAVE = """<?xml version="1.0" encoding="utf-8"?>
<SaveGame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <player>
    <name>Ana</name><farmName>Lunares</farmName><money>1234</money><totalMoneyEarned>5000</totalMoneyEarned>
    <seasonForSaveGame>1</seasonForSaveGame><maxHealth>100</maxHealth><maxStamina>270</maxStamina>
    <deepestMineLevel>40</deepestMineLevel><houseUpgradeLevel>1</houseUpgradeLevel>
    <farmingLevel>4</farmingLevel><miningLevel>3</miningLevel><combatLevel>2</combatLevel>
    <foragingLevel>1</foragingLevel><fishingLevel>0</fishingLevel><luckLevel>0</luckLevel>
    <spouse>Leah</spouse>
    <items>
      <Item xsi:type="Tool"><Name>Copper Pickaxe</Name></Item>
      <Item xsi:nil="true" />
      <Item xsi:type="Object"><Name>Blueberry Seeds</Name><Stack>15</Stack></Item>
    </items>
    <friendshipData>
      <item><key><string>Leah</string></key><value><Friendship><Points>2600</Points></Friendship></value></item>
      <item><key><string>Lewis</string></key><value><Friendship><Points>300</Points></Friendship></value></item>
    </friendshipData>
  </player>
  <currentSeason>summer</currentSeason><dayOfMonth>7</dayOfMonth><year>1</year>
  <dailyLuck>0.07</dailyLuck><isRaining>true</isRaining><weatherForTomorrow>3</weatherForTomorrow>
</SaveGame>
"""


XXE = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><SaveGame>&e;</SaveGame>'


@pytest.fixture
def reader(repo_profiles: Path):  # type: ignore[no-untyped-def]
    return save_reader(load_profile(repo_profiles / "stardew-valley", bundled=True), {})


def _farm(root: Path, name: str, body: str = SAVE) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / name).write_text(body, encoding="utf-8")
    (folder / "SaveGameInfo").write_text("<Farmer />", encoding="utf-8")
    return folder / name


def test_stardew_reader_reads_farm(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    snap = reader.read(_farm(tmp_path, "Ana_123"))
    facts = {f.key: f for f in snap.facts}
    assert snap.in_run
    assert snap.run_id == "Ana_123"
    assert facts["day"].details == {"season": "summer", "day": 7, "year": 1}
    assert facts["weather"].value == "rain"
    assert facts["weather"].details == {"tomorrow": "lightning"}
    assert facts["luck"].value == "good"
    assert facts["money"].value == "1234g"
    assert facts["skill:farming"].value == "4"
    assert facts["deepest_mine"].value == "40"
    assert facts["spouse"].subject == "Leah"
    items = [f for f in snap.facts if f.kind == "item"]
    assert [(f.subject, f.details["Stack"]) for f in items] == [("Copper Pickaxe", 1), ("Blueberry Seeds", 15)]
    friends = [f for f in snap.facts if f.key.startswith("friend:")]
    assert [(f.subject, f.value) for f in friends] == [("Leah", "10♥"), ("Lewis", "1♥")]
    assert snap.summary["farm"] == "Lunares"
    assert snap.summary["total_earned"] == 5000


def test_stardew_reader_picks_latest_farm(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    old = _farm(tmp_path, "Ana_1")
    new = _farm(tmp_path, "Bea_2")
    (tmp_path / "steam_autocloud.vdf").write_text("x", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    assert reader.find(tmp_path) == new


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("<Other />", "No es una partida"),
        ("<SaveGame><year>1</year></SaveGame>", "no tiene jugador"),
        (XXE, "(?i)entit"),
    ],
)
def test_stardew_reader_rejects_bad_saves(tmp_path: Path, reader, body: str, error: str) -> None:  # type: ignore[no-untyped-def]
    path = _farm(tmp_path, "Ana_1", body)
    with pytest.raises(ValueError, match=error):  # defusedxml también lanza ValueError
        reader.read(path)
