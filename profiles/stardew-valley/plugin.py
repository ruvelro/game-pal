"""Lector de partidas de Stardew Valley.

Cada granja es una carpeta dentro de Saves con un XML del mismo nombre. El juego guarda al final de
cada día, así que el guardado describe la mañana del día en curso. Solo se lee; nunca se escribe.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from gamepal.plugins import SaveFact, SaveSnapshot

MAX_SAVE_BYTES = 64 * 1024 * 1024
SKILLS = ("farming", "mining", "combat", "foraging", "fishing", "luck")
SEASONS = {"0": "spring", "1": "summer", "2": "fall", "3": "winter"}
WEATHER = {"0": "sunny", "1": "rain", "2": "debris", "3": "lightning", "4": "festival", "5": "snow", "6": "wedding"}
HEART_POINTS = 250
MAX_ITEMS = 36
MAX_FRIENDS = 10


def _text(node: Any, tag: str) -> str | None:
    found = node.find(tag)
    return found.text if found is not None and found.text is not None else None


def _int(node: Any, tag: str) -> int | None:
    value = _text(node, tag)
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


class StardewSaveReader:
    def find(self, folder: Path) -> Path | None:
        """La granja jugada más recientemente."""
        best: tuple[float, Path] | None = None
        for farm in folder.iterdir():
            save = farm / farm.name
            if farm.is_dir() and save.is_file():
                mtime = save.stat().st_mtime
                if best is None or mtime > best[0]:
                    best = (mtime, save)
        return best[1] if best else None

    def read(self, path: Path) -> SaveSnapshot:
        if path.stat().st_size > MAX_SAVE_BYTES:
            raise ValueError("La partida es demasiado grande")
        # defusedxml rechaza entidades y DTD: un XML manipulado no puede leer ficheros ni agotar memoria.
        root = ElementTree.parse(str(path)).getroot()
        if root.tag != "SaveGame":
            raise ValueError("No es una partida de Stardew Valley")
        player = root.find("player")
        if player is None:
            raise ValueError("La partida no tiene jugador")
        facts: list[SaveFact] = []

        season = _text(root, "currentSeason") or SEASONS.get(_text(player, "seasonForSaveGame") or "", "")
        day, year = _int(root, "dayOfMonth"), _int(root, "year")
        facts.append(SaveFact("day", "day", f"{season} {day}, year {year}", f"{season} {day} · Y{year}",
                              {"season": season, "day": day, "year": year}))  # fmt: skip
        weather = "rain" if _text(root, "isRaining") == "true" else "sunny"
        tomorrow = WEATHER.get(_text(root, "weatherForTomorrow") or "", "unknown")
        facts.append(SaveFact("weather", "stat", "weather", weather, {"tomorrow": tomorrow}))
        luck = float(_text(root, "dailyLuck") or 0)
        mood = "good" if luck > 0.02 else "bad" if luck < -0.02 else "neutral"
        facts.append(SaveFact("luck", "stat", "daily_luck", mood, {"value": round(luck, 3)}))
        facts.append(SaveFact("money", "stat", "money", f"{_int(player, 'money') or 0}g"))
        for label, tag in (("max_health", "maxHealth"), ("max_stamina", "maxStamina"),
                           ("deepest_mine", "deepestMineLevel"), ("house", "houseUpgradeLevel")):  # fmt: skip
            value = _int(player, tag)
            if value is not None:
                facts.append(SaveFact(label, "stat", label, str(value)))
        for skill in SKILLS:
            level = _int(player, f"{skill}Level")
            if level is not None:
                facts.append(SaveFact(f"skill:{skill}", "skill", skill, str(level)))
        spouse = _text(player, "spouse")
        if spouse:
            facts.append(SaveFact("spouse", "friend", spouse, "spouse"))

        items = player.find("items")
        count = 0
        for item in items if items is not None else []:
            name = _text(item, "Name")
            if not name or count >= MAX_ITEMS:
                continue
            stack = _int(item, "Stack") or _int(item, "stack") or 1
            facts.append(SaveFact(f"item:{count}:{name}", "item", name, None, {"Stack": stack}))
            count += 1

        friends: list[tuple[int, str]] = []
        friendship = player.find("friendshipData")
        for entry in friendship if friendship is not None else []:
            name = entry.findtext("key/string")
            points = entry.findtext("value/Friendship/Points")
            if name and points:
                friends.append((int(points), name))
        for points, name in sorted(friends, reverse=True)[:MAX_FRIENDS]:
            facts.append(SaveFact(f"friend:{name}", "friend", name, f"{points // HEART_POINTS}♥", {"points": points}))

        summary = {
            "farm": _text(player, "farmName"),
            "farmer": _text(player, "name"),
            "season": season,
            "day": day,
            "year": year,
            "total_earned": _int(player, "totalMoneyEarned"),
            "friends_known": len(friends),
        }
        return SaveSnapshot(
            path=str(path),
            saved_at=os.path.getmtime(path),
            run_id=path.parent.name,  # la granja: los datos de la partida no caducan al cambiar de día
            in_run=True,
            facts=facts,
            summary=summary,
        )
