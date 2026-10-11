import asyncio
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.events import KnowledgeProgress
from gamepal.knowledge.extract import Chunk, chunk_markdown, in_scope, links, sitemap_urls
from gamepal.knowledge.fetch import Fetcher, FetchError, normalize_url
from gamepal.knowledge.service import KnowledgeService
from gamepal.knowledge.store import KnowledgeStore
from gamepal.profiles import load_profile

ARTICLE = """<html><head><title>{title} | Wiki Fandom</title></head><body>
<nav>Jump to navigation</nav>
<article><h1>{title}</h1>
<p>Jump to navigation</p>
<p>{body} Esta página explica todo sobre {title} con detalle suficiente para que el extractor la considere
contenido principal y no ruido de navegación de la wiki.</p>
<h2>Estrategia</h2><p>Para vencer a {title} esquiva sus ataques en área y castiga tras cada combo largo.
Conviene llevar mejoras de daño a distancia y guardar la defensa para la segunda fase.</p>
{links}
</article></body></html>"""


def page(title: str, body: str, hrefs: list[str] = ()) -> str:  # type: ignore[assignment]
    anchors = "".join(f'<p><a href="{h}">{h}</a></p>' for h in hrefs)
    return ARTICLE.format(title=title, body=body, links=anchors)


class Site:
    """Un sitio web simulado con robots.txt, enlaces y contadores de visitas."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, str, dict[str, str]]] = {
            "/robots.txt": (200, "User-agent: *\nDisallow: /privado\n", {"content-type": "text/plain"}),
            "/wiki/Chronos": (
                200,
                page(
                    "Chronos",
                    "Titán del tiempo.",
                    ["/wiki/Hecate", "/privado/x", "/wiki/Special:Random", "https://otra.web/a"],
                ),
                {"content-type": "text/html", "etag": '"v1"'},
            ),
            "/wiki/Hecate": (
                200,
                page("Hecate", "Bruja de la encrucijada.", ["/wiki/Scylla"]),
                {"content-type": "text/html"},
            ),
            "/wiki/Scylla": (200, page("Scylla", "Sirena del mar.", []), {"content-type": "text/html"}),
            "/redir": (302, "", {"location": "http://192.168.1.1/admin"}),
        }
        self.hits: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.hits[path] = self.hits.get(path, 0) + 1
        if path == "/wiki/Chronos" and request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        status, body, headers = self.pages.get(path, (404, "no", {"content-type": "text/plain"}))
        return httpx.Response(status, text=body, headers=headers)


def public_except_private(host: str) -> bool:
    return not host.startswith(("192.168.", "127.", "10.", "localhost"))


def fetcher_for(site: Site):  # type: ignore[no-untyped-def]
    return lambda rate: Fetcher(
        rate_per_host=1000, transport=httpx.MockTransport(site.handler), resolver=public_except_private
    )


# Extracción ---------------------------------------------------------------------------------


def test_chunk_markdown_keeps_section_paths() -> None:
    text = "# Chronos\nIntro corta del jefe que dura bastante para no fundirse.\n\n## Fase 1\n" + "Esquiva. " * 200
    chunks = chunk_markdown(text)
    assert chunks[0].heading == "Chronos"
    assert all(c.heading == "Chronos › Fase 1" for c in chunks[1:])
    assert max(len(c.text) for c in chunks) <= 1400


def test_scope_rules() -> None:
    start = "https://wiki.example/wiki/Chronos"
    assert in_scope("https://wiki.example/wiki/Hecate", start, [], [])
    assert not in_scope("https://otra.web/wiki/Hecate", start, [], [])
    assert not in_scope("https://wiki.example/wiki/Special:Random", start, [], [])
    assert not in_scope("https://wiki.example/wiki/Hecate?action=edit", start, [], [])
    assert not in_scope("https://wiki.example/blog/x", start, ["/wiki/*"], [])
    assert not in_scope("https://wiki.example/wiki/Spoilers", start, [], ["*Spoiler*"])


def test_links_and_sitemaps() -> None:
    html = '<a href="/a#top">a</a><a href="b">b</a><a href="mailto:x@y">m</a><a href="/a">dup</a>'
    assert links("https://w.example/wiki/x", html) == ["https://w.example/a", "https://w.example/wiki/b"]
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://w.example/p</loc></url></urlset>'
    )
    assert sitemap_urls(xml) == ["https://w.example/p"]
    assert sitemap_urls('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>') == []


def test_normalize_url() -> None:
    assert normalize_url("HTTPS://Wiki.Example/A#sec") == "https://wiki.example/A"


# Descargas ----------------------------------------------------------------------------------


async def test_fetcher_blocks_private_networks_and_robots() -> None:
    site = Site()
    fetcher = fetcher_for(site)(1)
    with pytest.raises(FetchError, match="pública"):
        await fetcher.fetch("http://127.0.0.1/wiki/x")
    with pytest.raises(FetchError, match="pública"):
        await fetcher.fetch("https://wiki.example/redir")  # la redirección apunta a la red local
    with pytest.raises(FetchError, match="robots"):
        await fetcher.fetch("https://wiki.example/privado/x")
    with pytest.raises(FetchError, match="http"):
        await fetcher.fetch("file:///etc/passwd")
    page = await fetcher.fetch("https://wiki.example/wiki/Hecate")
    assert "Bruja" in page.text
    await fetcher.aclose()


async def test_fetcher_rejects_big_or_unknown_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/zip":
            return httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
        return httpx.Response(200, content=b"x" * (6 * 1024 * 1024), headers={"content-type": "text/html"})

    fetcher = Fetcher(rate_per_host=1000, transport=httpx.MockTransport(handler), resolver=lambda h: True)
    with pytest.raises(FetchError, match="Tipo"):
        await fetcher.fetch("https://w.example/zip")
    with pytest.raises(FetchError, match="grande"):
        await fetcher.fetch("https://w.example/big")
    await fetcher.aclose()


# Índice -------------------------------------------------------------------------------------


def test_store_lexical_search_is_injection_safe(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "kb.sqlite")
    source = store.upsert_source("url", "https://w.example/", "user", {})
    store.save_document(source.id, "https://w.example/c", "Chronos", "h1",
                        [Chunk("Chronos › Fase 1", "Esquiva la guadaña y castiga.")], None, None)  # fmt: skip
    store.save_document(source.id, "https://w.example/h", "Hecate", "h2",
                        [Chunk("Hecate", "Clones y anillos de fuego.")], None, None)  # fmt: skip
    hits = store.search("¿cómo esquivo la GUADAÑA de Chronos?")
    assert hits[0].title == "Chronos"
    assert hits[0].url == "https://w.example/c"
    for nasty in ['" OR 1=1 --', "NEAR(", "*", "title:x", ")"]:
        store.search(nasty)  # no lanza errores de sintaxis de FTS5
    assert store.remove_documents(source.id, keep={"https://w.example/c"}) == 1
    assert [h.title for h in store.search("anillos fuego")] == []


def test_store_semantic_search_and_fusion(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "kb.sqlite")
    source = store.upsert_source("url", "https://w.example/", "user", {})
    a, b = np.array([1, 0, 0], dtype=np.float32), np.array([0, 1, 0], dtype=np.float32)
    store.save_document(source.id, "u1", "Parsnip", "1", [Chunk("", "Plant in spring.")], np.stack([a]), "m")
    store.save_document(source.id, "u2", "Melon", "2", [Chunk("", "Summer crop.")], np.stack([b]), "m")
    hits = store.search("chirivía", vector=a, model="m", min_score=0.5)
    assert [h.title for h in hits] == ["Parsnip"]
    assert store.search("chirivía", vector=a, model="otro") == []


# Servicio -----------------------------------------------------------------------------------


@pytest.fixture
def config() -> AppConfig:
    return AppConfig.model_validate({"knowledge": {"embeddings": "none"}})


async def _service(tmp_path: Path, site: Site, config: AppConfig, repo_profiles: Path) -> KnowledgeService:
    service = KnowledgeService(
        EventBus(), tmp_path / "data", tmp_path / "cache",
        config=lambda: config, provider=lambda: None, fetcher_factory=fetcher_for(site),
    )  # fmt: skip
    await service.open(load_profile(repo_profiles / "generic", bundled=True))
    return service


async def _wait(service: KnowledgeService, source_id: int) -> KnowledgeProgress:
    async with service._bus.subscribe({"knowledge"}) as sub:
        while True:
            event = await asyncio.wait_for(sub.get(), 10)
            assert isinstance(event, KnowledgeProgress)
            if event.source_id == source_id and event.state in {"ready", "error"}:
                return event


async def test_crawl_respects_depth_scope_and_robots(tmp_path: Path, config: AppConfig, repo_profiles: Path) -> None:
    site = Site()
    service = await _service(tmp_path, site, config, repo_profiles)
    waiter = asyncio.create_task(_wait(service, 1))
    await asyncio.sleep(0)
    source = service.add_url("https://wiki.example/wiki/Chronos", depth=1)
    event = await waiter
    assert source.id == 1
    assert event.state == "ready"
    assert service.store is not None
    assert sorted(service.store.titles()) == ["Chronos", "Hecate"]  # Scylla está a profundidad 2
    assert "/privado/x" not in site.hits  # robots.txt
    assert "/wiki/Special:Random" not in site.hits
    hits = await service.search("guadaña combo Chronos")
    assert hits[0].title == "Chronos"
    assert "Jump to navigation" not in hits[0].text

    # Segunda pasada: Chronos responde 304 y nada se reprocesa.
    waiter = asyncio.create_task(_wait(service, 1))
    await asyncio.sleep(0)
    service.refresh(1)
    assert (await waiter).state == "ready"
    assert sorted(service.store.titles()) == ["Chronos", "Hecate"]
    await service.close()


async def test_unreachable_start_is_an_error(tmp_path: Path, config: AppConfig, repo_profiles: Path) -> None:
    service = await _service(tmp_path, Site(), config, repo_profiles)
    waiter = asyncio.create_task(_wait(service, 1))
    await asyncio.sleep(0)
    service.add_url("https://wiki.example/no-existe")
    event = await waiter
    assert event.state == "error"
    assert "404" in event.message
    await service.close()


async def test_files_are_ingested_and_removed(tmp_path: Path, config: AppConfig, repo_profiles: Path) -> None:
    service = await _service(tmp_path, Site(), config, repo_profiles)
    waiter = asyncio.create_task(_wait(service, 1))
    await asyncio.sleep(0)
    guide = "# Guía de granja\n\n## Primavera\n" + "Planta chirivías el primer día y riega cada mañana. " * 8
    source = service.add_file("../../guía granja.md", guide.encode())
    assert (await waiter).state == "ready"
    assert source.location == "file:guía granja.md"
    assert (await service.search("chirivías primavera"))[0].title == "Guía de granja"
    service.remove(source.id)
    assert await service.search("chirivías") == []
    with pytest.raises(ValueError, match="no admitido"):
        service.add_file("virus.exe", b"MZ")
    await service.close()


async def test_profile_sources_are_synced_but_never_fetched_alone(
    tmp_path: Path, config: AppConfig, repo_profiles: Path
) -> None:
    site = Site()
    service = KnowledgeService(
        EventBus(), tmp_path / "data", tmp_path / "cache",
        config=lambda: config, provider=lambda: None, fetcher_factory=fetcher_for(site),
    )  # fmt: skip
    await service.open(load_profile(repo_profiles / "hades-2", bundled=True))
    assert service.store is not None
    sources = service.store.sources()
    assert [(s.origin, s.status) for s in sources] == [("profile", "pending")]
    service._due_refreshes()  # nunca descargada: no se programa sola
    assert service._queue.empty()
    with pytest.raises(ValueError, match=r"profile\.yaml"):
        service.remove(sources[0].id)
    await service.close()


async def test_assistant_cites_knowledge(repo_profiles: Path) -> None:
    from gamepal.assistant import Assistant
    from gamepal.events import AnswerUpdate, QuestionAsked
    from gamepal.knowledge.store import Hit
    from gamepal.providers import LocalOpenAIProvider

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("response_format"):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"terms": ["parsnip"]}'}}]})
        chunk = json.dumps({"choices": [{"delta": {"content": "Plántalas en primavera [1]."}}]})
        return httpx.Response(200, text=f"data: {chunk}\n\ndata: [DONE]\n\n")

    queries: list[str] = []

    async def retrieve(query: str) -> list[Hit]:
        queries.append(query)
        return [Hit(1, "Parsnip", "Growth", "Takes 4 days.", "https://wiki.example/Parsnip", 0.03)]

    provider = LocalOpenAIProvider("http://127.0.0.1:1/v1", "m", transport=httpx.MockTransport(handler))
    bus = EventBus()
    assistant = Assistant(
        bus, config=AppConfig, provider=lambda: provider,
        profile=lambda: load_profile(repo_profiles / "generic", bundled=True),
        screen=lambda: None, read=lambda: None, retrieve=retrieve, has_knowledge=lambda: True,
    )  # fmt: skip
    runner = asyncio.create_task(assistant.run())
    await asyncio.sleep(0)
    async with bus.subscribe({"assistant.answer"}) as sub:
        bus.publish(QuestionAsked(id="q", text="¿Cuándo planto chirivías?", source="text"))
        while True:
            event = await asyncio.wait_for(sub.get(), 5)
            assert isinstance(event, AnswerUpdate)
            if event.done:
                break
    runner.cancel()
    assert "parsnip" in queries[0]
    assert event.sources == [{"n": "1", "title": "Parsnip › Growth", "url": "https://wiki.example/Parsnip"}]
    prompt = seen[-1]["messages"][-1]["content"]
    assert "<conocimiento>" in prompt
    assert "[1] Parsnip › Growth" in prompt


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Parsnip Seeds - Stardew Valley Wiki", "Parsnip Seeds"),
        ("Chronos | Hades Wiki | Fandom", "Chronos"),
        ("Jump-Start - Guide", "Jump-Start - Guide"),
    ],
)
def test_clean_title(raw: str, clean: str) -> None:
    from gamepal.knowledge.extract import clean_title

    assert clean_title(raw) == clean
