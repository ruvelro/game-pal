import asyncio
from pathlib import Path

import pytest

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import ScreenDetected
from gamepal.evidence.formats import FormatError, lua_list, luabins_loads, lz4_block_decompress
from gamepal.evidence.ledger import Fact, Ledger, Rank
from gamepal.evidence.service import EvidenceService, pretty
from gamepal.perception.vocabulary import Entry, Vocabulary
from gamepal.plugins import PluginError, UntrustedPluginError, plugin_digest, save_reader
from gamepal.profiles import load_profile
from tests.fixtures.hades_save import build_save, luabins_value, lz4_literals, run_state

# Formatos -----------------------------------------------------------------------------------


def test_lz4_literals_and_overlapping_matches() -> None:
    assert lz4_block_decompress(lz4_literals(b"hola mundo" * 5)) == b"hola mundo" * 5
    # «ab» como literales y una coincidencia de 10 bytes a distancia 2: repite el patrón.
    block = bytes([(2 << 4) | 6]) + b"ab" + bytes([2, 0])
    assert lz4_block_decompress(block) == b"ab" * 6


@pytest.mark.parametrize("bad", [bytes([0x20]) + b"a", bytes([0x11]) + b"a" + bytes([9, 0])])
def test_lz4_rejects_corrupt_blocks(bad: bytes) -> None:
    with pytest.raises(FormatError):
        lz4_block_decompress(bad)


def test_luabins_roundtrip_and_limits() -> None:
    value = {"a": [1, 2.5, "x"], 3: True, "n": None}
    decoded = luabins_loads(bytes([1]) + luabins_value(value))[0]
    assert lua_list(decoded["a"]) == [1, 2.5, "x"]
    assert decoded[3] is True
    with pytest.raises(FormatError):
        luabins_loads(bytes([1]) + b"T" + (10**6).to_bytes(4, "little") * 2)
    with pytest.raises(FormatError):
        luabins_loads(bytes([1, ord("Z")]))


# Lector de Hades II -------------------------------------------------------------------------


@pytest.fixture
def reader(repo_profiles: Path):  # type: ignore[no-untyped-def]
    return save_reader(load_profile(repo_profiles / "hades-2", bundled=True), {})


def test_hades_reader_in_run(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "Profile1.sav"
    path.write_bytes(build_save(run_state()))
    snap = reader.read(path)
    assert snap.in_run
    assert snap.run_id == "Profile1:12"
    facts = {f.key: f for f in snap.facts}
    assert facts["weapon"].subject == "WeaponLob"
    assert facts["aspect"].subject == "LobCloseAttackAspect"
    assert facts["keepsake"].subject == "LuckyCoinKeepsake"
    assert facts["boon:ZeusWeaponBoon"].details == {"Rarity": "Epic", "StackNum": 2}
    assert facts["arcana:LastStandSlowTimeMetaUpgrade"].kind == "arcana"
    assert facts["health"].value == "55/90"
    assert facts["last_stands"].value == "2"
    assert facts["room"].details == {"encounter": "BossHecate01", "completed": False}
    assert snap.summary["choices"][0]["chosen"] == "ZeusWeaponBoon"


def test_hades_reader_in_hub_ignores_stale_boons(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "Profile1.sav"
    path.write_bytes(build_save(run_state(), map_name="Hub_Main", version=17))
    snap = reader.read(path)
    assert not snap.in_run
    keys = {f.key for f in snap.facts}
    assert "boon:ZeusWeaponBoon" not in keys
    assert "health" not in keys
    assert {"weapon", "aspect", "keepsake"} <= keys
    assert snap.summary["choices_belong_to"] == "última partida terminada"


def test_hades_reader_rejects_bad_files(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "Profile1.sav"
    path.write_bytes(build_save(run_state(), corrupt=True))
    with pytest.raises(FormatError, match="comprobación"):
        reader.read(path)
    path.write_bytes(build_save(run_state(), version=16))
    with pytest.raises(FormatError, match="Versión"):
        reader.read(path)
    path.write_bytes(b"NOPE" + b"\0" * 100)
    with pytest.raises(FormatError):
        reader.read(path)


def test_reader_picks_most_recent_profile(tmp_path: Path, reader) -> None:  # type: ignore[no-untyped-def]
    import os
    import time

    old, new = tmp_path / "Profile1.sav", tmp_path / "Profile2_Temp.sav"
    old.write_bytes(build_save(run_state()))
    new.write_bytes(build_save(run_state()))
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    assert reader.find(tmp_path) == new


# Plugins ------------------------------------------------------------------------------------


def _user_profile(tmp_path: Path, plugin: str) -> Path:
    folder = tmp_path / "mi-juego"
    folder.mkdir()
    (folder / "profile.yaml").write_text(
        "id: mi-juego\nname: Mi juego\nevidence: {save_reader: 'plugin:Reader'}\n", encoding="utf-8"
    )
    (folder / "plugin.py").write_text(plugin, encoding="utf-8")
    return folder


def test_user_plugins_need_explicit_trust(tmp_path: Path) -> None:
    folder = _user_profile(tmp_path, "class Reader:\n    pass\n")
    loaded = load_profile(folder)
    with pytest.raises(UntrustedPluginError) as info:
        save_reader(loaded, {})
    digest = info.value.digest
    assert digest == plugin_digest(folder / "plugin.py")
    assert save_reader(loaded, {"mi-juego": digest}) is not None
    # Si el código cambia, la confianza anterior ya no vale.
    (folder / "plugin.py").write_text("class Reader:\n    x = 1\n", encoding="utf-8")
    with pytest.raises(UntrustedPluginError):
        save_reader(load_profile(folder), {"mi-juego": digest})


def test_broken_plugins_are_reported(tmp_path: Path) -> None:
    folder = _user_profile(tmp_path, "raise RuntimeError('boom')\n")
    loaded = load_profile(folder)
    digest = plugin_digest(folder / "plugin.py")
    with pytest.raises(PluginError, match="boom"):
        save_reader(loaded, {"mi-juego": digest})


# Registro de evidencia ----------------------------------------------------------------------


def _fact(key: str, rank: Rank, label: str = "x") -> Fact:
    return Fact(key, "boon", key, label, rank)


def test_ledger_ranks() -> None:
    ledger = Ledger()
    assert ledger.add(_fact("boon:A", Rank.PLAYER))
    assert not ledger.add(_fact("boon:A", Rank.SEEN))  # lo visto no pisa lo que dijo el jugador
    ledger.apply_save("run1", [_fact("boon:A", Rank.SAVE, "A guardado")], {}, 0, "p")
    assert ledger.facts()[0].rank == Rank.SAVE
    assert ledger.add(_fact("boon:B", Rank.PLAYER))
    # Partida nueva: se olvida lo dicho en la anterior, se conserva lo del guardado nuevo.
    ledger.apply_save("run2", [_fact("weapon", Rank.SAVE)], {}, 0, "p")
    assert [f.key for f in ledger.facts()] == ["weapon"]
    with pytest.raises(ValueError, match="set_offers"):
        ledger.add(_fact("offer:C", Rank.OFFERED))


def test_ledger_prompt_marks_origins() -> None:
    ledger = Ledger()
    ledger.apply_save("r", [_fact("weapon", Rank.SAVE, "Argent Skull")], {}, 0, "p")
    ledger.add(_fact("boon:A", Rank.PLAYER, "Latigazo"))
    ledger.set_offers([Fact("offer:B", "offer", "B", "Rencor", Rank.OFFERED)])
    text = ledger.prompt()
    assert "Argent Skull [guardado]" in text
    assert "Latigazo [lo dijo el jugador]" in text
    assert "todavía no lo tiene): Rencor" in text


async def test_service_offers_player_statements_and_save(tmp_path: Path, repo_profiles: Path) -> None:
    vocabulary = Vocabulary()
    vocabulary.add(Entry("ZeusWeaponBoon", "boons", {"es": "Latigazo de Tormenta", "en": "Storm Lash"}))
    vocabulary.add(Entry("HeraSpecialBoon", "boons", {"es": "Rencor Regio"}))
    saves = tmp_path / "saves"
    saves.mkdir()
    config = AppConfig.model_validate({"save_dirs": {"hades-2": str(saves)}})
    service = EvidenceService(EventBus(), config=lambda: config, vocabulary=lambda: vocabulary)
    await service.open(load_profile(repo_profiles / "hades-2", bundled=True))
    options = [
        {"name": {"id": "ZeusWeaponBoon", "name": "Latigazo de Tormenta"}, "rarity": {"name": "Raro"}},
        {"name": {"id": "HeraSpecialBoon", "name": "Rencor Regio"}},
    ]
    service.on_screen(ScreenDetected(screen_id="boon_choice", change="enter", data={"options": options}))
    assert [o.label for o in service.ledger.offers()] == ["Latigazo de Tormenta", "Rencor Regio"]
    assert service.ledger.facts() == []  # ofrecido no es tenido

    assert service.on_player("¿Cojo el Latigazo de Tormenta?") is None
    assert service.on_player("No voy a coger Rencor Regio") is None
    assert service.on_player("He cogido Latigazo de Tormenta o Rencor Regio") is None  # ambiguo
    fact = service.on_player("He cogido el latigazo de tormenta")
    assert fact is not None
    assert (fact.rank, fact.key) == (Rank.PLAYER, "boon:ZeusWeaponBoon")

    (saves / "Profile1.sav").write_bytes(build_save(run_state()))
    assert service.reader is not None
    assert await service.check_save(service.reader)
    facts = {f.key: f for f in service.ledger.facts()}
    assert facts["boon:ZeusWeaponBoon"].rank == Rank.SAVE
    assert facts["boon:ZeusWeaponBoon"].label == "Latigazo de Tormenta"
    assert facts["weapon"].label == "Weapon Lob"  # sin vocabulario de armas: el id legible
    assert not await service.check_save(service.reader)  # sin cambios no se relee
    await service.close()


def test_pretty_ids() -> None:
    assert pretty("ApolloWeaponBoon") == "Apollo Weapon Boon"


async def test_missing_save_folder_is_reported(repo_profiles: Path) -> None:
    config = AppConfig.model_validate({"save_dirs": {"hades-2": "/no/existe"}})
    service = EvidenceService(EventBus(), config=lambda: config, vocabulary=Vocabulary)
    await service.open(load_profile(repo_profiles / "hades-2", bundled=True))
    assert service.reader is not None
    assert not await service.check_save(service.reader)
    await asyncio.sleep(0)
    await service.close()
