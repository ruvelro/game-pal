"""Genera los datos de cultivos del perfil de Stardew Valley desde la wiki oficial.

    uv run python scripts/stardew_reference.py

Lee las fichas de cada cultivo y de sus semillas con la API de MediaWiki (no el HTML), y los nombres en
cada idioma de los enlaces entre wikis. Escribe en profiles/stardew-valley/reference/:

- crops.yaml: cuánto tarda en crecer cada cultivo, cuándo se planta y dónde se compra la semilla.
- festivals.yaml: qué días son las fiestas de cada estación.

El asistente los usa como dato exacto: así no tiene que recordar tiempos de cultivo ni fechas.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import yaml

API = "https://stardewvalleywiki.com/mediawiki/api.php"
USER_AGENT = "game-pal/scripts (+https://github.com/ruvelro/game-pal)"
CATEGORIES = (
    "Category:Spring crops",
    "Category:Summer crops",
    "Category:Fall crops",
    "Category:Winter crops",
    "Category:Multi-Seasonal crops",
)
# Cultivos que no están en esas categorías de la wiki.
EXTRA = ("Powdermelon", "Pineapple", "Taro Root")
# Mezclas de semillas de forraje: dan una planta al azar, no el cultivo.
FORAGE_MIXES = {"Spring Seeds", "Summer Seeds", "Fall Seeds", "Winter Seeds"}
LANGUAGES = ("es", "fr", "de", "it", "pt", "ja", "ko", "zh", "ru", "tr", "hu")
SEASONS = ("spring", "summer", "fall", "winter")
# Dónde se compran las semillas, según el campo de la ficha.
SHOPS = {
    "gPrice": "pierre",
    "jPrice": "joja",
    "oPrice": "oasis",
    "tPrice": "traveling_cart",
    "ePrice": "egg_festival",
}  # el comerciante de la isla no cobra en oro, sino en objetos
# Si la pregunta habla de esto, se dan las filas de la estación en curso aunque no se nombre ninguna.
KEYWORDS = {
    "es": ["plantar", "planto", "planta", "sembrar", "siembro", "cultivo", "cultivar", "cosecha", "cosechar",
           "semilla", "huerto", "crecer", "crece", "madurar", "madura", "regar"],
    "en": ["plant", "sow", "crop", "harvest", "seed", "grow", "ripen", "farm"],
}  # fmt: skip
FESTIVALS = {"egg_festival": "Egg Festival"}
OUT_DIR = Path(__file__).resolve().parent.parent / "profiles" / "stardew-valley" / "reference"
HEADER = (
    "# Generado con scripts/stardew_reference.py desde la wiki oficial. No lo edites a mano: vuelve a\n"
    "# ejecutar el script. El asistente usa estos datos como exactos para no inventar tiempos, fechas ni precios.\n"
)


def api(params: dict[str, str]) -> dict[str, Any]:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - URL fija de la wiki
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        data: dict[str, Any] = json.load(response)
    time.sleep(1)  # una petición por segundo: la wiki es de la comunidad
    return data


def batches(items: list[str], size: int = 50) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def infobox(text: str) -> dict[str, str]:
    """Campos de la primera ficha {{Infobox…}}, respetando plantillas y enlaces anidados."""
    start = text.find("{{Infobox")
    if start < 0:
        return {}
    fields: dict[str, str] = {}
    depth, i, key, buffer = 0, start, "", []
    while i < len(text):
        two = text[i : i + 2]
        if two in ("{{", "[["):
            depth += 1
            buffer.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            if depth == 0:
                break
            buffer.append(two)
            i += 2
            continue
        if text[i] == "|" and depth == 1:
            if key:
                fields[key] = "".join(buffer).strip()
            end = text.find("=", i)
            key, buffer, i = text[i + 1 : end].strip(), [], end + 1
            continue
        buffer.append(text[i])
        i += 1
    if key:
        fields[key] = "".join(buffer).strip()
    return fields


def first_int(value: str | None) -> int | None:
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group().replace(",", "")) if match else None


def names_of(value: str) -> list[str]:
    return [found.strip() for found in re.findall(r"\{\{Name\|([^|}]+)", value or "")]


def other_prices(value: str) -> dict[str, int]:
    """«[[Egg Festival]]: {{Price|100}}» → {"egg_festival": 100}."""
    found: dict[str, int] = {}
    for place, price in re.findall(r"\[\[([^\]|]+)[^\]]*\]\][^{]*\{\{Price\|([\d,]+)", value or ""):
        found[re.sub(r"\W+", "_", place.strip().lower()).strip("_")] = int(price.replace(",", ""))
    return found


def seasons_of(value: str) -> list[str]:
    lowered = value.lower()
    return [season for season in SEASONS if season in lowered]


def regrow_days(text: str) -> int | None:
    match = re.search(r"continues? to produce (?:every (\d+) days?|(every day))", text)
    if not match:
        return None
    return int(match.group(1)) if match.group(1) else 1


def pages(titles: list[str]) -> dict[str, dict[str, Any]]:
    """Texto y nombres en otros idiomas de cada página, siguiendo redirecciones."""
    found: dict[str, dict[str, Any]] = {}
    for group in batches(titles):
        data = api({
            "action": "query", "titles": "|".join(group), "redirects": "1",
            "prop": "revisions|langlinks", "rvprop": "content", "rvslots": "main", "lllimit": "max",
        })  # fmt: skip
        query = data.get("query", {})
        for page in query.get("pages", []):
            if page.get("missing"):
                continue
            text = page["revisions"][0]["slots"]["main"]["content"]
            names = {
                link["lang"]: re.sub(r"\s*\([^)]*\)$", "", link["title"])  # «Piña (fruta)» → «Piña»
                for link in page.get("langlinks", [])
                if link["lang"] in LANGUAGES
            }
            found[page["title"]] = {"text": text, "names": names}
        for redirect in query.get("redirects", []):
            if redirect["to"] in found:
                found[redirect["from"]] = found[redirect["to"]]
    return found


def members(category: str) -> set[str]:
    data = api({"action": "query", "list": "categorymembers", "cmtitle": category, "cmlimit": "max",
                "cmnamespace": "0"})  # fmt: skip
    return {member["title"] for member in data["query"]["categorymembers"]}


def festival_days(sentence: str, season: str) -> list[int]:
    """«on the 13th of every Spring» → [13]; «during Spring 15-17» → [15, 16, 17]."""
    span = re.search(rf"{season} (\d+)(?:-(\d+))?", sentence, re.IGNORECASE)
    if span:
        first = int(span.group(1))
        return list(range(first, int(span.group(2) or first) + 1))
    single = re.search(r"(\d+)(?:st|nd|rd|th)\b", sentence)
    return [int(single.group(1))] if single else []


def festivals() -> list[dict[str, Any]]:
    """Las fiestas de la página Festivals: cada «===Nombre===» dentro de la sección de su estación."""
    text = pages(["Festivals"])["Festivals"]["text"]
    parts = re.split(r"^(==+)\s*(.*?)\s*==+\s*$", text, flags=re.MULTILINE)
    found: list[tuple[str, str, list[int]]] = []
    season = ""
    for level, title, body in zip(parts[1::3], parts[2::3], parts[3::3], strict=True):
        if level == "==":
            season = re.sub(r"[\[\]]", "", title).strip().lower()
            continue
        sentence = re.search(r"(?:takes place|is held)[^.]*\.", body)
        days = festival_days(sentence.group(0), season) if sentence and season in SEASONS else []
        if level == "===" and days:
            found.append((title.strip(), season, days))
    names = pages([title for title, _, _ in found])
    return [
        {"names": {"en": title, **(names.get(title) or {}).get("names", {})}, "season": season, "days": days}
        for title, season, days in found
    ]


def write(name: str, document: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=120), "utf-8")
    return path


def main() -> int:
    titles: set[str] = set(EXTRA)
    for category in CATEGORIES:
        titles |= members(category)
    trellis = members("Category:Trellis Crops")

    crop_pages = pages(sorted(titles))
    found: list[tuple[dict[str, Any], dict[str, str], str | None]] = []
    for title, page in sorted(crop_pages.items()):
        box = infobox(page["text"])
        seeds = [name for name in names_of(box.get("seed", "")) if name not in FORAGE_MIXES]
        found.append(({"title": title, **page}, box, seeds[0] if seeds else None))
    seed_pages = pages(sorted({seed for _, _, seed in found if seed}))

    crops: list[dict[str, Any]] = []
    for page, box, seed in found:
        seed_page = seed_pages.get(seed or "")
        seed_box = infobox(seed_page["text"]) if seed_page else {}
        # La ficha de la semilla es la más precisa: la del cultivo puede mezclar variantes (uvas de forraje).
        growth_text = seed_box.get("growth") or box.get("growth", "")
        season_text = seed_box.get("season") or box.get("season", "")
        growth = first_int(growth_text)
        seasons = seasons_of(season_text)
        if growth is None or not seasons:
            continue  # no es un cultivo que se plante (árboles, objetos de forraje…)
        english = box.get("eng") or page["title"]
        entry: dict[str, Any] = {"names": {"en": english, **page["names"]}, "seasons": seasons, "growth_days": growth}
        irrigated = re.search(r"\((\d+) when irrigated\)", growth_text)
        if irrigated:
            entry["growth_days_irrigated"] = int(irrigated.group(1))
        if "Ginger Island" in season_text:
            entry["ginger_island_all_year"] = True
        regrow = regrow_days(page["text"])
        if regrow:
            entry["regrow_days"] = regrow
        if page["title"] in trellis:
            entry["trellis"] = True
        sell = first_int(box.get("sellprice"))
        if sell:
            entry["sell_price"] = sell
        if seed and seed_page:
            prices = {shop: first_int(seed_box.get(field)) for field, shop in SHOPS.items()}
            prices = {shop: price for shop, price in prices.items() if price}
            prices.update(other_prices(seed_box.get("otherprice", "")))
            entry["seed"] = {"names": {"en": seed, **seed_page["names"]}, "prices": prices}
        crops.append(entry)

    crops.sort(key=lambda c: (SEASONS.index(c["seasons"][0]), c["names"]["en"]))
    # Sitios que solo venden un día al año: su fecha, para saber si ya pasó.
    events: dict[str, dict[str, Any]] = {}
    for key, title in FESTIVALS.items():
        page = pages([title]).get(title)
        match = (
            re.search(r"on the (\d+)(?:st|nd|rd|th) of every \[\[(Spring|Summer|Fall|Winter)\]\]", page["text"])
            if page
            else None
        )
        if match:
            events[key] = {"season": match.group(2).lower(), "day": int(match.group(1))}
    document = {
        "id": "crops",
        "title": {"es": "Cultivos", "en": "Crops"},
        "source": "Stardew Valley Wiki",
        "source_url": "https://stardewvalleywiki.com/Crops",
        "generated": date.today().isoformat(),
        "keywords": KEYWORDS,
        "events": events,
        "entries": crops,
    }
    print(f"{len(crops)} cultivos en {write('crops.yaml', document)}")
    found = festivals()
    calendar = {
        "id": "festivals",
        "source": "Stardew Valley Wiki",
        "source_url": "https://stardewvalleywiki.com/Festivals",
        "generated": date.today().isoformat(),
        "festivals": found,
    }
    print(f"{len(found)} fiestas en {write('festivals.yaml', calendar)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
