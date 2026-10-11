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
        "sells": "la cosecha se vende por {n} g", "seed": "semilla", "where": "dónde", "buy": "se compra en",
        "when_today": "plantada hoy (día {d})", "when_tomorrow": "plantada mañana (día {d})",
        "ready": "sí llega, se cosecha el día {h}", "first": "sí llega, primera cosecha el día {h}",
        "harvests": "{n} cosechas en total esta estación", "harvest_one": "1 sola cosecha esta estación",
        "too_late": "no llega (maduraría después del día {last}, cuando acaba la estación)",
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
        "not_sold": "ahora no se vende en ninguna tienda", "never_sold": "no se vende en tiendas",
        "festivals": "Fiestas de {season}: {list}.",
        "no_festivals": "{season} no tiene fiestas.",
        "festival_day": "día {d}", "festival_days": "días {first} a {last}",
        "fest_past": "ya pasó", "fest_today": "es hoy", "fest_in": "dentro de {n} días", "fest_in_one": "mañana",
        "fest_next": "La próxima fiesta es {name}: {when} ({days} de {season}).",
    },
    "en": {
        "days": "days", "day": "day", "regrow": "produces again every {n} {days}",
        "trellis": "trellis (can't walk through)",
        "irrigated": "{n} days when irrigated", "island": "all year on Ginger Island",
        "sells": "the crop sells for {n}g", "seed": "seed", "where": "where", "buy": "bought at",
        "when_today": "planted today (day {d})", "when_tomorrow": "planted tomorrow (day {d})",
        "ready": "makes it, harvest on day {h}", "first": "makes it, first harvest on day {h}",
        "harvests": "{n} harvests this season", "harvest_one": "a single harvest this season",
        "too_late": "too late (it would ripen after day {last}, when the season ends)",
        "festival_past": "already over this year: it was {season} {day}",
        "festival_next": "only on {season} {day}",
        "base": "Base days: speed fertilizer and the Agriculturist profession shorten them.",
        "next": "keeps growing in {season}",
        "calendar": "Today is {season} {day}{year}. The season has {total} days: {left} left (day {first} to {total}).",
        "calendar_last": "Today is {season} {day}{year}, the last day of the season.",
        "next_season": "Then {season} starts.",
        "year": ", year {n}",
        "not_sold": "no shop sells it right now", "never_sold": "not sold in shops",
        "festivals": "{season} festivals: {list}.",
        "no_festivals": "{season} has no festivals.",
        "festival_day": "day {d}", "festival_days": "days {first} to {last}",
        "fest_past": "already over", "fest_today": "today", "fest_in": "in {n} days", "fest_in_one": "tomorrow",
        "fest_next": "The next festival is {name}: {when} ({days} of {season}).",
    },
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Festival:
    names: dict[str, str]
    season: str
    days: tuple[int, ...]


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


def load_festivals(loaded: LoadedProfile) -> list[Festival]:
    relative = loaded.profile.calendar.events
    if not relative:
        return []
    path = loaded.path(relative, f"La tabla {relative}")
    if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ProfileError(f"No se puede leer la tabla {relative}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    found: list[Festival] = []
    for item in raw.get("festivals") or []:
        if not isinstance(item, dict) or not isinstance(item.get("names"), dict):
            continue
        days = tuple(sorted(int(d) for d in item.get("days") or [] if isinstance(d, int)))
        if days and item.get("season"):
            found.append(Festival({str(k): str(v) for k, v in item["names"].items()}, str(item["season"]), days))
    return found


class Reference:
    """Las tablas del perfil y su calendario, listos para dar al modelo lo que venga a cuento."""

    def __init__(
        self,
        tables: list[Table],
        seasons: list[str],
        days_per_season: int,
        festivals: list[Festival] | None = None,
    ) -> None:
        self.tables = tables
        self.seasons = seasons
        self.days_per_season = days_per_season
        self.festivals = festivals or []

    @classmethod
    def from_profile(cls, loaded: LoadedProfile) -> Reference:
        calendar = loaded.profile.calendar
        return cls(load_tables(loaded), list(calendar.seasons), calendar.days_per_season, load_festivals(loaded))

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
                # Sin nombrar ninguno, solo los que se pueden comprar y aún cosechar: los demás no sirven para
                # recomendar, y el modelo los recomienda igual aunque la fila diga que no.
                in_season = [
                    i
                    for i, e in enumerate(table.entries)
                    if self._still_grows(e, today) and self._on_sale(table, e, today) is not False
                ]
                indexes = sorted(set(indexes) | set(in_season[:MAX_TOPIC_ROWS]))
            if indexes:
                found.append((table, [table.entries[i] for i in indexes]))
        return found

    def _still_grows(self, entry: dict[str, Any], today: Today) -> bool:
        """Si plantado hoy da algo antes de que acabe la estación, o sigue creciendo en la siguiente."""
        if today.season not in (entry.get("seasons") or []):
            return False
        growth = entry.get("growth_days")
        following = self._next_season(today.season)
        if not growth or not self.days_per_season or (following and following in entry.get("seasons", [])):
            return True
        return bool(today.day + int(growth) <= self.days_per_season)

    def _on_sale(self, table: Table, entry: dict[str, Any], today: Today | None) -> bool | None:
        """Si su semilla se puede comprar ahora; None si la fila no habla de semillas."""
        seed = entry.get("seed")
        if not seed:
            return None
        places = list(seed.get("prices") or {})
        return bool(places) and not all(self._event_past(table, k, today) for k in places)

    def _festivals(self, today: Today, language: str) -> str:
        """Las fiestas de la estación con lo que falta para cada una, y cuál es la próxima."""
        if not self.festivals:
            return ""
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        total = self.days_per_season

        def name(festival: Festival) -> str:
            return festival.names.get(language) or festival.names.get("en", "")

        def days(festival: Festival) -> str:
            first, last = festival.days[0], festival.days[-1]
            if first == last:
                return text["festival_day"].format(d=first)
            return text["festival_days"].format(first=first, last=last)

        def until(wait: int) -> str:
            return text["fest_in_one"] if wait == 1 else text["fest_in"].format(n=wait)

        here = [f for f in self.festivals if f.season == today.season]
        season = seasons.get(today.season, today.season)
        items: list[str] = []
        upcoming: tuple[int, Festival] | None = None
        for festival in here:
            if festival.days[-1] < today.day:
                state = text["fest_past"]
            elif festival.days[0] <= today.day:
                state = text["fest_today"]
            else:
                wait = festival.days[0] - today.day
                state = until(wait)
                upcoming = upcoming or (wait, festival)
            items.append(f"{name(festival)} ({days(festival)}, {state})")
        lines = [
            text["festivals"].format(season=season, list="; ".join(items))
            if items
            else text["no_festivals"].format(season=season.capitalize())
        ]
        # Si ya no quedan en esta estación, la primera de las siguientes.
        if upcoming is None and total and today.season in self.seasons:
            start = self.seasons.index(today.season)
            for step in range(1, len(self.seasons) + 1):
                later = self.seasons[(start + step) % len(self.seasons)]
                ahead = sorted((f for f in self.festivals if f.season == later), key=lambda f: f.days[0])
                if ahead:
                    upcoming = ((total - today.day) + (step - 1) * total + ahead[0].days[0], ahead[0])
                    break
        if upcoming is not None:
            wait, festival = upcoming
            lines.append(
                text["fest_next"].format(
                    name=name(festival), when=until(wait), days=days(festival),
                    season=seasons.get(festival.season, festival.season),
                )
            )  # fmt: skip
        return " ".join(lines)

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
        festivals = self._festivals(today, language)
        return f"{line}\n{festivals}" if festivals else line

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

    def _event_past(self, table: Table, place: str, today: Today | None) -> bool | None:
        """Si la tienda es un festival, si ya pasó este año; None si no es un festival o no se sabe."""
        event = table.events.get(place)
        if event is None or today is None or today.season not in self.seasons:
            return None
        season, day = str(event.get("season", "")), int(event.get("day", 0))
        if season not in self.seasons:
            return None
        return (self.seasons.index(today.season), today.day) > (self.seasons.index(season), day)

    def _event_note(self, table: Table, place: str, today: Today | None, language: str) -> str:
        """Si la tienda es un festival: cuándo es, o que ya pasó este año."""
        past = self._event_past(table, place, today)
        if past is None:
            return ""
        event = table.events[place]
        text = TEXT.get(language, TEXT["en"])
        seasons = SEASON_NAMES.get(language, SEASON_NAMES["en"])
        season, day = str(event.get("season", "")), int(event.get("day", 0))
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
            parts.append(f"{text['seed']} «{seed_name}»" + (f" {text['buy']}: {prices}" if prices else ""))
            # Si solo se vende en fiestas que ya pasaron, decirlo claro: si no, el modelo la recomienda igual.
            if not seed.get("prices"):
                parts.append(text["never_sold"])
            elif not self._on_sale(table, entry, today):
                parts.append(text["not_sold"])
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
