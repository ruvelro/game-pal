"""Guardados sintéticos de Hades II para tests: mismo formato que el real, contenido inventado."""

from __future__ import annotations

import struct
import zlib
from typing import Any


def luabins_value(value: Any) -> bytes:
    if value is None:
        return b"-"
    if value is True:
        return b"1"
    if value is False:
        return b"0"
    if isinstance(value, (int, float)):
        return b"N" + struct.pack("<d", float(value))
    if isinstance(value, str):
        raw = value.encode()
        return b"S" + struct.pack("<I", len(raw)) + raw
    if isinstance(value, list):
        value = {i + 1: v for i, v in enumerate(value)}
    if isinstance(value, dict):
        out = b"T" + struct.pack("<II", 0, len(value))
        for key, item in value.items():
            out += luabins_value(key) + luabins_value(item)
        return out
    raise TypeError(type(value))


def lz4_literals(data: bytes) -> bytes:
    """Un bloque LZ4 válido con todo como literales (sin compresión real)."""
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    rest, extra = n - 15, bytearray()
    while rest >= 255:
        extra.append(255)
        rest -= 255
    extra.append(rest)
    return bytes([0xF0]) + bytes(extra) + data


def _string(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<I", len(raw)) + raw


def build_save(root: dict[str, Any], *, version: int = 18, runs: int = 12, map_name: str = "F_Combat03",
               corrupt: bool = False) -> bytes:  # fmt: skip
    lua = bytes([1]) + luabins_value(root)
    compressed = lz4_literals(lua)
    body = struct.pack("<HH", version, 0) + struct.pack("<Q", 1_760_000_000) + _string("Location_Erebus")
    body += struct.pack("<IIII", runs, 100, 4, 20) + bytes([0, 0])
    if version >= 18:
        body += struct.pack("<I", 0)
    body += struct.pack("<I", 1) + _string("CurrentRun")
    body += _string(map_name) + _string("") + struct.pack("<I", len(compressed)) + compressed
    checksum = zlib.adler32(body) & 0xFFFFFFFF
    if corrupt:
        checksum ^= 1
    return b"SGB1" + struct.pack("<I", checksum) + body


def run_state() -> dict[str, Any]:
    return {
        "GameState": {"PrimaryWeaponName": "WeaponLob"},
        "CurrentRun": {
            "RunDepthCache": 7,
            "CurrentRoom": {"Name": "F_Boss01", "Encounter": {"Name": "BossHecate01", "Completed": False}},
            "Hero": {
                "Health": 55, "MaxHealth": 90, "Mana": 30, "MaxMana": 60, "LastStands": [{}, {}],
                "Traits": [
                    {"Name": "LobCloseAttackAspect", "Slot": "Aspect", "Rarity": "Common"},
                    {"Name": "LuckyCoinKeepsake", "Slot": "Keepsake", "Rarity": "Rare"},
                    {"Name": "ZeusWeaponBoon", "Rarity": "Epic", "StackNum": 2},
                    {"Name": "LastStandSlowTimeMetaUpgrade", "Rarity": "Epic"},
                ],
            },
            "LootChoiceHistory": [
                {"UpgradeName": "ZeusUpgrade", "UpgradeChoices": [
                    {"Name": "ZeusWeaponBoon", "Rarity": "Epic", "Chosen": "true"},
                    {"Name": "ZeusSpecialBoon", "Rarity": "Rare", "Chosen": "false"},
                ]},
            ],
        },
    }  # fmt: skip
