"""Extracción del contenido útil de páginas web, PDF y ficheros de texto, y troceado por secciones."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urljoin, urlsplit

from gamepal.knowledge.fetch import normalize_url

log = logging.getLogger(__name__)

TARGET_CHARS = 900
MAX_CHARS = 1400
MIN_CHARS = 120
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Líneas de navegación que algunas wikis cuelan en el contenido.
_BOILERPLATE = re.compile(
    r"^\s*(jump to (navigation|search)|saltar a (la )?(navegación|búsqueda)|ir a (la )?(navegación|búsqueda)|"
    r"aller à la (navigation|recherche)|zur navigation springen|zur suche springen|edit source|editar código)\s*$",
    re.IGNORECASE,
)
# «Parsnip - Stardew Valley Wiki», «Chronos | Hades Wiki | Fandom»: se quita el nombre de la wiki.
_TITLE_NOISE = re.compile(
    r"\s+[|·–—-]\s+[^|·–—]*\b(fandom|wiki|wikia|fextralife|ign|wikipedia|wikidot)\b.*$", re.IGNORECASE
)

# Ruido habitual de las wikis: páginas especiales, historiales, usuarios, ficheros…
DEFAULT_EXCLUDE = [
    "*Special:*", "*Especial:*", "*Talk:*", "*Discusión:*", "*User:*", "*Usuario:*", "*User_blog:*",
    "*File:*", "*Archivo:*", "*Template:*", "*Plantilla:*", "*Category:*", "*Categoría:*", "*Message_Wall:*",
    "*action=*", "*oldid=*", "*diff=*", "*printable=*", "*veaction=*", "*/f/*", "*/f?*",
]  # fmt: skip


@dataclass(frozen=True, slots=True)
class Document:
    url: str
    title: str
    markdown: str


@dataclass(frozen=True, slots=True)
class Chunk:
    heading: str
    text: str


def clean_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title).strip() or title.strip()


def html_to_document(url: str, html: str) -> Document | None:
    import trafilatura

    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        favor_recall=True,
    )
    if not markdown:
        return None
    markdown = "\n".join(line for line in markdown.splitlines() if not _BOILERPLATE.match(line))
    if len(markdown.strip()) < MIN_CHARS:
        return None
    metadata: Any = trafilatura.extract_metadata(html, default_url=url)
    title = clean_title(metadata.title) if metadata is not None and metadata.title else urlsplit(url).path
    return Document(url, title, markdown)


def pdf_to_document(url: str, data: bytes, fallback_title: str) -> Document | None:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:500]:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    if not pages:
        return None
    meta = reader.metadata
    title = str(meta.title) if meta is not None and meta.title else fallback_title
    return Document(url, clean_title(title), "\n\n".join(pages))


def text_to_document(url: str, text: str, fallback_title: str) -> Document | None:
    if len(text.strip()) < MIN_CHARS:
        return None
    first = next((line for line in text.splitlines() if line.strip()), fallback_title)
    match = _HEADING.match(first.strip())
    return Document(url, match.group(2) if match else fallback_title, text)


def links(base: str, html: str) -> list[str]:
    """Enlaces de una página, absolutos y sin fragmento."""
    import lxml.html

    try:
        tree = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return []
    found: list[str] = []
    for href in tree.xpath("//a/@href"):
        href = str(href).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        found.append(normalize_url(urljoin(base, href)))
    return list(dict.fromkeys(found))


def sitemap_urls(xml: str) -> list[str]:
    import lxml.etree

    try:
        root = lxml.etree.fromstring(xml.encode("utf-8"), parser=lxml.etree.XMLParser(resolve_entities=False))
    except lxml.etree.XMLSyntaxError:
        return []
    return [normalize_url(str(loc.text).strip()) for loc in root.iter("{*}loc") if loc.text]


def in_scope(url: str, start: str, include: list[str], exclude: list[str]) -> bool:
    """Misma web que el origen, dentro de los patrones de inclusión y fuera de los de exclusión."""
    target, origin = urlsplit(url), urlsplit(start)
    if target.scheme not in {"http", "https"} or target.netloc != origin.netloc:
        return False
    if any(fnmatch(url, pattern) or fnmatch(target.path, pattern) for pattern in [*DEFAULT_EXCLUDE, *exclude]):
        return False
    if include:
        return any(fnmatch(target.path, pattern) or fnmatch(url, pattern) for pattern in include)
    return True


def chunk_markdown(markdown: str) -> list[Chunk]:
    """Trocea por secciones y, dentro de cada sección, por párrafos hasta ~900 caracteres."""
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush_paragraph() -> None:
        if buffer:
            paragraphs.append("\n".join(buffer).strip())
            buffer.clear()

    def flush_section() -> None:
        flush_paragraph()
        if any(p for p in paragraphs):
            sections.append((list(path), [p for p in paragraphs if p]))
        paragraphs.clear()

    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            path[:] = [*path[: level - 1], heading.group(2).strip()]
            continue
        if not line.strip():
            flush_paragraph()
        else:
            buffer.append(line.rstrip())
    flush_section()

    chunks: list[Chunk] = []
    for heads, paras in sections:
        title = " › ".join(h for h in heads if h)
        current = ""
        for paragraph in paras:
            while len(paragraph) > MAX_CHARS:
                cut = paragraph.rfind(". ", 0, MAX_CHARS)
                cut = cut + 1 if cut > TARGET_CHARS // 2 else MAX_CHARS
                pieces = [paragraph[:cut].strip(), paragraph[cut:].strip()]
                if current:
                    chunks.append(Chunk(title, current))
                    current = ""
                chunks.append(Chunk(title, pieces[0]))
                paragraph = pieces[1]
            if current and len(current) + len(paragraph) > TARGET_CHARS:
                chunks.append(Chunk(title, current))
                current = paragraph
            else:
                current = f"{current}\n\n{paragraph}" if current else paragraph
        if current:
            # Las secciones muy cortas se unen a la anterior si comparten título.
            if chunks and len(current) < MIN_CHARS and chunks[-1].heading == title:
                chunks[-1] = Chunk(title, chunks[-1].text + "\n\n" + current)
            else:
                chunks.append(Chunk(title, current))
    return [c for c in chunks if len(c.text) >= 40]
