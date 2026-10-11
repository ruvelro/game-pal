"""Datos exactos del juego: tablas del perfil (cultivos…) y calendario de la partida.

Los modelos pequeños se inventan cifras (días de cultivo, precios) y fallan con las cuentas (cuántos días
quedan de estación). Aquí no se les pide que recuerden ni que calculen: game-pal busca en las tablas del
perfil lo que se menciona, hace las cuentas con el día del guardado y se lo da hecho.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from gamepal.profiles import LoadedProfile, ProfileError

log = logging.getLogger(__name__)

MAX_REFERENCE_BYTES = 2 * 1024 * 1024
# Si se pregunta en general («¿qué planto?»), como mucho estas filas de la estación en curso.
MAX_TOPIC_ROWS = 24
# Una fila se cita si su nombre aparece en lo que se pregunta o se ve; los nombres muy cortos dan falsos positivos.
MIN_NAME_CHARS = 3

SEASON_NAMES = {
    "es": {"spring": "primavera", "summer": "verano", "fall": "otoño", "winter": "invierno"},
    "en": {"spring": "spring", "summer": "summer", "fall": "fall", "winter": "winter"},
}
PLACE_NAMES = {
    "es": {
        "pierre": "Pierre",
        "joja": "JojaMart",
        "oasis": "Oasis",
        "traveling_cart": "carrito ambulante",
        "egg_festival": "Festival del Huevo",
    },
    "en": {
        "pierre": "Pierre",
        "joja": "JojaMart",
        "oasis": "Oasis",
        "traveling_cart": "Traveling Cart",
        "egg_festival": "Egg Festival",
    },
}
TEXT = {
    "es": {
        "days": "días", "day": "día", "regrow": "vuelve a dar cada {n} {days}",
        "trellis": "con espaldera (no se pasa por encima)",
        "irrigated": "{n} días si está junto al agua", "island": "en la isla Jengibre, todo el año",
        "sells": "se vende por {n} g", "seed": "semilla", "where": "dónde",
        "when_today": "plantada hoy (día {d})", "when_tomorrow": "plantada mañana (día {d})",
        "ready": "se cosecha el día {h}", "first": "primera cosecha el día {h}",
        "harvests": "{n} cosechas en total esta estación", "harvest_one": "1 sola cosecha esta estación",
        "too_late": "ya no llega (maduraría después del día {last})",
        "festival_past": "ya pasó este año: fue el día {day} de {season}",
        "festival_next": "solo el día {day} de {season}",
        "base": "Días base: el abono de velocidad y la profesión Agricultor los acortan.",
        "next": "sigue creciendo en {season}",
        "calendar": (
            "Hoy es el día {day} de {season}{year}. La estación tiene {total} días: quedan {left} "
            "(del día {first} al {total})."
        ),
        "calendar_last": "Hoy es el día {day} de {season}{year}, el último de la estación.",
        "next_season": "Después empieza {season}.",
        "year": ", año {n}",
    },
    "en": {
        "days": "days", "day": "day", "regrow": "produces again every {n} {days}",
        "trellis": "trellis (can't walk through)",
        "irrigated": "{n} days when irrigated", "island": "all year on Ginger Island",
        "sells": "sells for {n}g", "seed": "seed", "where": "where",
        "when_today": "planted today (day {d})", "when_tomorrow": "planted tomorrow (day {d})",
        "ready": "harvest on day {h}", "first": "first harvest on day {h}",
        "harvests": "{n} harvests this season", "harvest_one": "a single harvest this season",
        "too_late": "too late (it would ripen after day {last})",
        "festival_past": "already over this year: it was {season} {day}",
        "festival_next": "only on {season} {day}",
        "base": "Base days: speed fertilizer and the Agriculturist profession shorten them.",
        "next": "keeps growing in {season}",
        "calendar": "Today is {season} {day}{year}. The season has {total} days: {left} left (day {first} to {total}).",
        "calendar_last": "Today is {season} {day}{year}, the last day of the season.",
        "next_season": "Then {season} starts.",
        "year": ", year {n}",
    },
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Today:
    season: str
    day: int
    year: int | None = None


@dataclass
class Table:
    id: str
    title: dict[str, str]
    source: str
    source_url: str
    keywords: dict[str, list[str]]
    entries: list[dict[str, Any]]
    # Sitios que solo abren un día al año (festivales): {"egg_festival": {"season": "spring", "day": 13}}.
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    # (palabras normalizadas, fila) de cada nombre en cada idioma, para buscar lo que se menciona.
    names: list[tuple[tuple[str, ...], int]] = field(default_factory=list)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _stem(word: str) -> str:
    """Raíz común del singular y el plural: «chirivías» y «chirivía», «verdes» y «verde», «berries» y «berry».

    No es una lematización: basta con que las dos formas den lo mismo.
    """
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s"):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def words(text: str) -> tuple[str, ...]:
    flat = _strip_accents(text.lower())
    return tuple(_stem(w) for w in re.findall(r"[^\W_]+", flat))


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))


def load_tables(loaded: LoadedProfile) -> list[Table]:
    tables: list[Table] = []
    for relative in loaded.profile.reference:
        path = loaded.path(relative, f"La tabla {relative}")
        if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
            raise ProfileError(f"No se puede leer la tabla {relative}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = [e for e in raw.get("entries", []) if isinstance(e, dict) and isinstance(e.get("names"), dict)]
        table = Table(
            id=str(raw.get("id") or path.stem),
            title=dict(raw.get("title") or {}),
            source=str(raw.get("source") or ""),
            source_url=str(raw.get("source_url") or ""),
            keywords={lang: [str(k) for k in values] for lang, values in (raw.get("keywords") or {}).items()},
            entries=entries,
            events={str(k): dict(v) for k, v in (raw.get("events") or {}).items() if isinstance(v, dict)},
        )
        for index, entry in enumerate(entries):
            seed = entry.get("seed") or {}
            for name in [*entry["names"].values(), *(seed.get("names") or {}).values()]:
                key = words(str(name))
                if key and len("".join(key)) >= MIN_NAME_CHARS:
                    table.names.append((key, index))
        tables.append(table)
    return tables


class Reference:
    """Las tablas del perfil y su calendario, listos para dar al modelo lo que venga a cuento."""

    def __init__(self, tables: list[Table], seasons: list[str], days_per_season: int) -> None:
        self.tables = tables
        self.seasons = seasons
        self.days_per_season = days_per_season

    @classmethod
    def from_profile(cls, loaded: LoadedProfile) -> Reference:
        calendar = loaded.profile.calendar
        return cls(load_tables(loaded), list(calendar.seasons), calendar.days_per_season)

    def _next_season(self, season: str) -> str | None:
        if season not in self.seasons:
            return None
        return self.seasons[(self.seasons.index(season) + 1) % len(self.seasons)]

    def find(self, text: str, today: Today | None) -> list[tuple[Table, list[dict[str, Any]]]]:
        """Las filas de cada tabla que vienen a cuento: las que se nombran y, si se habla del tema, las de hoy."""
        said = words(text)
        found: list[tuple[Table, list[dict[str, Any]]]] = []
        for table in self.tables:
            indexes = sorted({index for key, index in table.names if _contains(said, key)})
            topic = any(_contains(said, words(keyword)) for keywords in table.keywords.values() for keyword in keywords)
            if topic and today is not None:
                in_season = [i for i, e in enumerate(table.entries) if today.season in (e.get("seasons") or [])]
                indexes = sorted(set(indexes) | set(in_season[:MAX_TOPIC_ROWS]))
            if indexes:
                found.append((table, [table.entries[i] for i in indexes]))
        return found

    def calendar(self, today: Today | None, language: str) -> str:
        """El día de hoy y lo que queda de estación, ya contado."""
        if today is None or not self.days_per_season or today.season not in self.seasons:
            return ""
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        total, day = self.days_per_season, today.day
        year = text["year"].format(n=today.year) if today.year else ""
        season = seasons.get(today.season, today.season)
        if day >= total:
            line = text["calendar_last"].format(season=season, day=day, year=year)
        else:
            line = text["calendar"].format(
                season=season, day=day, year=year, total=total, left=total - day, first=day + 1
            )
        following = self._next_season(today.season)
        if following:
            line += " " + text["next_season"].format(season=seasons.get(following, following))
        return line

    def render(self, text: str, today: Today | None, language: str) -> str:
        """Bloques <datos_del_juego> con las filas que vienen a cuento, en el idioma de las respuestas."""
        blocks: list[str] = []
        for table, entries in self.find(text, today):
            title = table.title.get(language) or table.title.get("en") or table.id
            lines = [f'<datos_del_juego tabla="{title}" fuente="{table.source}">']
            if any("growth_days" in entry for entry in entries):
                lines.append(TEXT.get(language, TEXT["en"])["base"])
            lines += [self._row(table, entry, today, language) for entry in entries]
            lines.append("</datos_del_juego>")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _event_note(self, table: Table, place: str, today: Today | None, language: str) -> str:
        """Si la tienda es un festival: cuándo es, o que ya pasó este año."""
        event = table.events.get(place)
        if event is None or today is None or today.season not in self.seasons:
            return ""
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        season, day = str(event.get("season", "")), int(event.get("day", 0))
        if season not in self.seasons:
            return ""
        past = (self.seasons.index(today.season), today.day) > (self.seasons.index(season), day)
        key = "festival_past" if past else "festival_next"
        note = text[key].format(day=day, season=seasons.get(season, season))
        return f" ({note})"

    def _row(self, table: Table, entry: dict[str, Any], today: Today | None, language: str) -> str:
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        places = PLACE_NAMES.get(language, PLACE_NAMES["en"])
        names = entry["names"]
        local, english = names.get(language) or names.get("en", ""), names.get("en", "")
        label = f"{local} ({english})" if english and english != local else local
        parts = [label, "/".join(seasons.get(s, s) for s in entry.get("seasons", []))]
        growth = entry.get("growth_days")
        if growth:
            parts.append(f"{growth} {text['days']}")
        if entry.get("growth_days_irrigated"):
            parts.append(text["irrigated"].format(n=entry["growth_days_irrigated"]))
        regrow = entry.get("regrow_days")
        if regrow:
            parts.append(text["regrow"].format(n=regrow, days=text["day"] if regrow == 1 else text["days"]))
        if entry.get("trellis"):
            parts.append(text["trellis"])
        if entry.get("ginger_island_all_year"):
            parts.append(text["island"])
        if entry.get("sell_price"):
            parts.append(text["sells"].format(n=entry["sell_price"]))
        seed = entry.get("seed") or {}
        if seed:
            seed_names = seed.get("names") or {}
            seed_name = seed_names.get(language) or seed_names.get("en", "")
            prices = ", ".join(
                f"{places.get(k, k)} {v} g{self._event_note(table, k, today, language)}"
                for k, v in (seed.get("prices") or {}).items()
            )
            parts.append(f"{text['seed']} «{seed_name}»" + (f": {prices}" if prices else ""))
        timing = self._timing(entry, today, language)
        if timing:
            parts.append(timing)
        return "- " + " · ".join(p for p in parts if p)

    def _timing(self, entry: dict[str, Any], today: Today | None, language: str) -> str:
        """Qué pasa si se planta hoy y si se planta mañana: cuándo se cosecha y cuántas veces, o si ya no llega."""
        growth = entry.get("growth_days")
        if today is None or not growth or not self.days_per_season or today.season not in entry.get("seasons", []):
            return ""
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        total = self.days_per_season
        following = self._next_season(today.season)
        continues = following is not None and following in entry.get("seasons", [])
        regrow = entry.get("regrow_days")
        plans: list[str] = []
        for key, start in (("when_today", today.day), ("when_tomorrow", today.day + 1)):
            if start > total:
                continue
            harvest = start + growth
            when = text[key].format(d=start)
            if harvest > total and continues:
                plans.append(f"{when}: {text['next'].format(season=seasons.get(following or '', following or ''))}")
            elif harvest > total:
                plans.append(f"{when}: {text['too_late'].format(last=total)}")
            elif regrow and not continues:
                count = 1 + (total - harvest) // regrow
                total_text = text["harvest_one"] if count == 1 else text["harvests"].format(n=count)
                plans.append(f"{when}: {text['first'].format(h=harvest)}, {total_text}")
            else:
                plans.append(f"{when}: {text['ready'].format(h=harvest)}")
        return "; ".join(plans)
