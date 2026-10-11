"""Ficha de un objeto buscada al momento en la wiki del juego, por su nombre exacto.

Para «¿para qué sirve esto?» con un objeto en pantalla, el índice de la wiki puede no tener esa página o no
encontrarla si se pregunta en otro idioma. La API de MediaWiki dice en una sola petición cuáles de los
nombres que se ven existen como página; luego se descarga solo esa ficha, en el idioma del juego.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode

from gamepal.knowledge.extract import html_to_document
from gamepal.knowledge.fetch import Fetcher, FetchError
from gamepal.security import validate_remote_endpoint

log = logging.getLogger(__name__)

MAX_CANDIDATES = 50  # títulos por petición que admite la API de MediaWiki
MAX_PAGES = 2
MAX_PAGE_CHARS = 1800
RATE_PER_HOST = 4.0


@dataclass(frozen=True, slots=True)
class WikiPage:
    title: str
    url: str
    text: str
    language: str


def _bare_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def prose_first(markdown: str) -> str:
    """Las frases antes que la tabla de la ficha: sin razonar, el modelo lee mal las celdas sueltas."""
    lines = markdown.splitlines()
    table = [line for line in lines if line.lstrip().startswith("|")]
    prose = [line for line in lines if not line.lstrip().startswith("|")]
    return "\n".join([*prose, "", *table]).strip() if table and any(p.strip() for p in prose) else markdown


def candidates(*groups: list[str]) -> list[str]:
    """Nombres que podrían ser el título de una ficha: sin cifras sueltas ni frases largas, sin repetir."""
    seen: dict[str, str] = {}
    for group in groups:
        for raw in group:
            name = re.sub(r"\s+", " ", raw).strip(" .,:;¿?¡!«»\"'()[]")
            letters = sum(c.isalpha() for c in name)
            if not 3 <= len(name) <= 60 or letters < len(name) * 0.6 or len(name.split()) > 6:
                continue
            seen.setdefault(name.casefold(), name)
    return list(seen.values())[:MAX_CANDIDATES]


class WikiLookup:
    """Busca fichas en las wikis del perfil, primero en la del idioma del juego."""

    def __init__(self, wikis: list[tuple[str, str]], fetcher: Fetcher | None = None) -> None:
        # (idioma, dirección de api.php). Solo https: la dirección viene del perfil.
        self._wikis = [(language, validate_remote_endpoint(api)) for language, api in wikis]
        self._fetcher = fetcher or Fetcher(rate_per_host=RATE_PER_HOST)
        # Lo ya preguntado: título → página, o None si no existe. Las fichas no cambian durante la partida.
        self._titles: dict[tuple[str, str], tuple[str, str] | None] = {}
        self._pages: dict[tuple[str, str], WikiPage | None] = {}
        self._translations: dict[tuple[str, str], str | None] = {}

    async def aclose(self) -> None:
        await self._fetcher.aclose()

    def _order(self, language: str) -> list[tuple[str, str]]:
        return sorted(self._wikis, key=lambda wiki: wiki[0] != language)

    async def find(self, names: list[str], language: str) -> list[WikiPage]:
        """Las fichas de los primeros nombres que existen como página, en el orden en que se dan."""
        found: list[WikiPage] = []
        pending = list(names)
        for wiki_language, api in self._order(language):
            if not pending or len(found) >= MAX_PAGES:
                break
            titles = await self._existing(api, pending)
            for name in list(pending):
                hit = titles.get(name)
                if hit is None:
                    continue
                pending.remove(name)
                page = await self._page(api, wiki_language, *hit)
                if page is not None and all(page.title != other.title for other in found):
                    found.append(page)
                if len(found) >= MAX_PAGES:
                    break
        return found

    async def translate(self, names: list[str], language: str) -> dict[str, str]:
        """Nombres en inglés (los del guardado) → nombre en el idioma del juego, según los enlaces de la wiki."""
        english = next((api for lang, api in self._wikis if lang == "en"), None)
        if english is None or language == "en":
            return {}
        unknown = [n for n in dict.fromkeys(names) if (language, n) not in self._translations]
        for group in [unknown[i : i + MAX_CANDIDATES] for i in range(0, len(unknown), MAX_CANDIDATES)]:
            params = {
                "action": "query", "titles": "|".join(group), "redirects": "1", "prop": "langlinks",
                "lllang": language, "format": "json", "formatversion": "2",
            }  # fmt: skip
            try:
                data = await self._fetcher.get_json(f"{english}?{urlencode(params)}")
            except FetchError as exc:
                log.info("No se pudieron traducir nombres: %s", exc)
                return self._known(names, language)
            query = data.get("query", {}) if isinstance(data, dict) else {}
            alias = {item["from"]: item["to"] for item in query.get("normalized", [])}
            redirect = {item["from"]: item["to"] for item in query.get("redirects", [])}
            local: dict[str, str] = {}
            for page in query.get("pages", []):
                links = page.get("langlinks") or []
                if links:
                    local[page["title"]] = re.sub(r"\s*\([^)]*\)$", "", links[0].get("title", ""))
            for name in group:
                title = redirect.get(alias.get(name, name), alias.get(name, name))
                # Una redirección lleva a otra página (Gold Pickaxe → Pickaxes): su nombre no sirve.
                same = title == alias.get(name, name)
                self._translations[(language, name)] = local.get(title) if same else None
        return self._known(names, language)

    def _known(self, names: list[str], language: str) -> dict[str, str]:
        return {n: local for n in names if (local := self._translations.get((language, n)))}

    async def _existing(self, api: str, names: list[str]) -> dict[str, tuple[str, str]]:
        """Nombre → (título, dirección) de los que existen, siguiendo mayúsculas y redirecciones."""
        unknown = [n for n in names if (api, n) not in self._titles]
        if unknown:
            params = {
                "action": "query", "titles": "|".join(unknown), "redirects": "1", "prop": "info",
                "inprop": "url", "format": "json", "formatversion": "2",
            }  # fmt: skip
            try:
                data = await self._fetcher.get_json(f"{api}?{urlencode(params)}")
            except FetchError as exc:
                log.info("No se pudo consultar %s: %s", api, exc)
                return {}
            query = data.get("query", {}) if isinstance(data, dict) else {}
            alias = {item["from"]: item["to"] for item in query.get("normalized", [])}
            redirect = {item["from"]: item["to"] for item in query.get("redirects", [])}
            pages = {p["title"]: p for p in query.get("pages", []) if not p.get("missing") and not p.get("invalid")}
            for name in unknown:
                title = alias.get(name, name)
                title = redirect.get(title, title)
                page = pages.get(title)
                self._titles[(api, name)] = (page["title"], page.get("fullurl", "")) if page else None
        return {n: hit for n in names if (hit := self._titles.get((api, n))) is not None}

    async def _page(self, api: str, language: str, title: str, url: str) -> WikiPage | None:
        key = (api, title)
        if key not in self._pages:
            self._pages[key] = await self._download(api, language, title, url)
        return self._pages[key]

    async def _download(self, api: str, language: str, title: str, url: str) -> WikiPage | None:
        params = {"action": "parse", "page": title, "prop": "text", "disabletoc": "1", "format": "json",
                  "formatversion": "2"}  # fmt: skip
        try:
            data = await self._fetcher.get_json(f"{api}?{urlencode(params)}")
        except FetchError as exc:
            log.info("No se pudo descargar la ficha %s: %s", title, exc)
            return None
        html = data.get("parse", {}).get("text", "") if isinstance(data, dict) else ""
        if not isinstance(html, str) or not html:
            return None
        wrapped = f"<html><head><title>{title}</title></head><body><main>{html}</main></body></html>"
        document = await asyncio.to_thread(html_to_document, url or title, wrapped)
        # El extractor descarta las páginas muy cortas; una ficha breve sigue siendo útil.
        text = prose_first(document.markdown) if document is not None else _bare_text(html)
        return WikiPage(title, url, text[:MAX_PAGE_CHARS], language) if text.strip() else None
