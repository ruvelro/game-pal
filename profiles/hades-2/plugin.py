"""Lector de guardados de Hades II.

Formato (SGB1): firma, Adler-32 del resto, versión 17 o 18, marca de tiempo, ubicación, contadores,
claves, mapa actual y el estado de Lua comprimido en LZ4 y serializado en LuaBins. Solo se lee:
nunca se escribe ni se ejecuta nada del juego.

Matiz importante: el estado de la partida solo describe una partida en curso si el mapa actual no es
el hub. En el hub, «CurrentRun» es la partida que acaba de terminar y el personaje ya está reiniciado.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path
from typing import Any

from gamepal.evidence.formats import FormatError, _Reader, lua_list, luabins_loads, lz4_block_decompress
from gamepal.plugins import SaveFact, SaveSnapshot

MAGIC = b"SGB1"
MAX_SAVE_BYTES = 32 * 1024 * 1024
SUPPORTED_VERSIONS = (17, 18)
GOD_PREFIXES = ("Zeus", "Poseidon", "Apollo", "Hera", "Aphrodite", "Demeter", "Hephaestus", "Hestia", "Ares",
                "Hermes", "Artemis", "Athena", "Dionysus", "Selene", "Chaos")  # fmt: skip


class HadesSaveReader:
    def find(self, folder: Path) -> Path | None:
        """El perfil que se está jugando: el guardado modificado más recientemente (incluidos los _Temp)."""
        candidates = [p for p in folder.glob("Profile*.sav") if p.is_file() and p.stat().st_size > 64]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None

    def read(self, path: Path) -> SaveSnapshot:
        if path.stat().st_size > MAX_SAVE_BYTES:
            raise FormatError("El guardado es demasiado grande")
        data = path.read_bytes()
        header, root = parse(data)
        return snapshot(path, header, root)


def parse(data: bytes) -> tuple[dict[str, Any], dict[Any, Any]]:
    reader = _Reader(data)
    if reader.take(4) != MAGIC:
        raise FormatError("No es un guardado de Hades II")
    checksum = reader.u32()
    if zlib.adler32(data[8:]) & 0xFFFFFFFF != checksum:
        # Lo normal es que el juego lo esté escribiendo en ese momento: se reintentará.
        raise FormatError("La suma de comprobación no coincide (¿guardado a medias?)")
    version = reader.u16()
    reader.u16()
    if version not in SUPPORTED_VERSIONS:
        raise FormatError(f"Versión de guardado no soportada: {version}")
    header: dict[str, Any] = {"version": version, "timestamp": reader.u64(), "location": reader.string()}
    header["runs"] = reader.u32()
    header["meta_points"] = reader.u32()
    header["shrine_points"] = reader.u32()
    header["grasp"] = reader.u32()
    header["easy_mode"] = bool(reader.u8())
    header["hard_mode"] = bool(reader.u8())
    if version >= 18:
        reader.u32()
    for _ in range(reader.u32()):
        reader.string()
    header["map"] = reader.string()
    header["next_map"] = reader.string()
    compressed = reader.take(reader.u32())
    values = luabins_loads(lz4_block_decompress(compressed))
    if not values or not isinstance(values[0], dict):
        raise FormatError("Estado de Lua vacío")
    return header, values[0]


def _category(name: str) -> str | None:
    if name.endswith("MetaUpgrade"):
        return "arcana"
    if name.endswith("Keepsake"):
        return "keepsake"
    if name.endswith("Aspect") or name.startswith("DummyWeapon"):
        return "aspect"
    if name.endswith(("Boon", "Blessing", "Curse")):
        return "boon"
    if name.endswith("Trait") and not name.startswith("Unused"):
        return "hammer"
    return None


def snapshot(path: Path, header: dict[str, Any], root: dict[Any, Any]) -> SaveSnapshot:
    game = root.get("GameState") or {}
    run = root.get("CurrentRun") or {}
    hero = run.get("Hero") or {}
    in_run = not str(header["map"]).startswith("Hub")
    facts: list[SaveFact] = []

    weapon = game.get("PrimaryWeaponName")
    if isinstance(weapon, str):
        facts.append(SaveFact("weapon", "weapon", weapon))

    for trait in lua_list(hero.get("Traits")):
        if not isinstance(trait, dict) or not isinstance(trait.get("Name"), str):
            continue
        name = trait["Name"]
        slot = trait.get("Slot")
        category = "keepsake" if slot == "Keepsake" else "aspect" if slot == "Aspect" else _category(name)
        if slot == "Familiar":
            category = "familiar"
        if category is None or (category in {"boon", "hammer"} and not in_run):
            continue
        key = category if category in {"keepsake", "aspect", "familiar"} else f"{category}:{name}"
        details = {k: trait[k] for k in ("Rarity", "StackNum") if trait.get(k) is not None}
        facts.append(SaveFact(key, category, name, None, details))

    if in_run:
        facts.append(SaveFact("health", "stat", "health", f"{hero.get('Health')}/{hero.get('MaxHealth')}"))
        if hero.get("MaxMana"):
            facts.append(SaveFact("mana", "stat", "mana", f"{hero.get('Mana')}/{hero.get('MaxMana')}"))
        facts.append(SaveFact("last_stands", "stat", "last_stands", str(len(lua_list(hero.get("LastStands"))))))
        room = run.get("CurrentRoom") or {}
        encounter = room.get("Encounter") or {}
        if isinstance(room.get("Name"), str):
            details = {"encounter": encounter.get("Name"), "completed": bool(encounter.get("Completed"))}
            facts.append(SaveFact("room", "room", room["Name"], None, details))
        facts.append(SaveFact("depth", "stat", "depth", str(run.get("RunDepthCache", ""))))

    # Elecciones de la partida (en curso o la última): qué se ofreció y qué se cogió, en orden.
    choices = []
    for entry in lua_list(run.get("LootChoiceHistory")):
        if not isinstance(entry, dict):
            continue
        options = [o for o in lua_list(entry.get("UpgradeChoices")) if isinstance(o, dict)]
        chosen = next((o.get("Name") for o in options if str(o.get("Chosen")) == "true"), None)
        choices.append(
            {
                "source": entry.get("UpgradeName"),
                "chosen": chosen,
                "offered": [o.get("Name") for o in options],
                "rarity": next((o.get("Rarity") for o in options if o.get("Name") == chosen), None),
            }
        )

    profile = path.stem.replace("_Temp", "")
    summary = {
        "map": header["map"],
        "runs": header["runs"],
        "grasp": header["grasp"],
        "version": header["version"],
        "choices": choices[-30:],
        "choices_belong_to": "partida en curso" if in_run else "última partida terminada",
    }
    return SaveSnapshot(
        path=str(path),
        saved_at=float(header["timestamp"]) or os.path.getmtime(path),
        run_id=f"{profile}:{header['runs']}",
        in_run=in_run,
        facts=facts,
        summary=summary,
    )
