"""Conocimiento de un perfil: fuentes, ingesta incremental, actualización periódica y búsqueda."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gamepal.bus import EventBus
from gamepal.config import AppConfig
from gamepal.downloads import describe
from gamepal.events import KnowledgeProgress, StatusChanged
from gamepal.knowledge.embed import E5_SIZE_MB, E5Embedder, Embedder, ProviderEmbedder
from gamepal.knowledge.extract import (
    Document,
    chunk_markdown,
    html_to_document,
    in_scope,
    links,
    pdf_to_document,
    sitemap_urls,
    text_to_document,
)
from gamepal.knowledge.fetch import Fetcher, FetchError, normalize_url
from gamepal.knowledge.store import Hit, KnowledgeStore, Source, passage
from gamepal.profiles import LoadedProfile
from gamepal.providers import Provider

log = logging.getLogger(__name__)

FILE_TYPES = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf"}
MAX_FILE_BYTES = 50 * 1024 * 1024
REFRESH_SECONDS = {"daily": 86_400, "weekly": 7 * 86_400, "monthly": 30 * 86_400}
SCHEDULER_TICK_S = 3600
_SAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)


class KnowledgeError(ValueError):
    pass


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeService:
    def __init__(
        self,
        bus: EventBus,
        data_dir: Path,
        cache_dir: Path,
        *,
        config: Callable[[], AppConfig],
        provider: Callable[[], Provider | None],
        fetcher_factory: Callable[[float], Fetcher] | None = None,
    ) -> None:
        self._bus = bus
        self._root = data_dir / "knowledge"
        self._cache = cache_dir / "embeddings"
        self._config = config
        self._provider = provider
        self._fetcher_factory = fetcher_factory or (lambda rate: Fetcher(rate_per_host=rate))
        self.profile: LoadedProfile | None = None
        self.store: KnowledgeStore | None = None
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._queued: set[int] = set()
        self._worker: asyncio.Task[None] | None = None
        self._scheduler: asyncio.Task[None] | None = None
        self._e5: E5Embedder | None = None
        self.embedder_error: str = ""
        self.current: int | None = None

    # Ciclo de vida ---------------------------------------------------------------------------

    async def open(self, loaded: LoadedProfile | None) -> None:
        await self.close()
        self.profile = loaded
        if loaded is None:
            return
        self.store = KnowledgeStore(self._root / f"{loaded.profile.id}.sqlite")
        self._sync_profile_sources(loaded)
        self._worker = asyncio.create_task(self._work(), name="knowledge")
        # Una descarga que quedó a medias (game-pal se cerró) se retoma, en vez de quedarse «en curso».
        for source in self.store.sources():
            if source.status in ("running", "queued"):
                self.store.set_status(source.id, "pending")
                self.enqueue(source.id)
        self._scheduler = asyncio.create_task(self._schedule(), name="knowledge-refresh")

    async def close(self) -> None:
        for task in (self._worker, self._scheduler):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._worker = self._scheduler = None
        self._queue = asyncio.Queue()
        self._queued.clear()
        if self.store is not None:
            self.store.close()
            self.store = None

    def _sync_profile_sources(self, loaded: LoadedProfile) -> None:
        assert self.store is not None
        wanted = {}
        for source in loaded.profile.knowledge.sources:
            url = normalize_url(str(source.url))
            wanted[url] = {
                "depth": source.depth,
                "include": source.include,
                "exclude": source.exclude,
                "max_pages": source.max_pages,
            }
        for url, options in wanted.items():
            self.store.upsert_source("url", url, "profile", options)
        for existing in self.store.sources():
            if existing.origin == "profile" and existing.location not in wanted:
                self.store.remove_source(existing.id)

    # Fuentes ----------------------------------------------------------------------------------

    def _need_store(self) -> KnowledgeStore:
        if self.store is None or self.profile is None:
            raise KnowledgeError("No hay ningún perfil activo")
        return self.store

    def add_url(self, url: str, depth: int = 1, max_pages: int | None = None, include: list[str] | None = None,
                exclude: list[str] | None = None) -> Source:  # fmt: skip
        store = self._need_store()
        normalized = normalize_url(url)
        if not normalized.startswith(("http://", "https://")):
            raise KnowledgeError("La dirección debe empezar por http:// o https://")
        options = {"depth": depth, "include": include or [], "exclude": exclude or [], "max_pages": max_pages}
        source = store.upsert_source("url", normalized, "user", options)
        self.enqueue(source.id)
        return source

    def add_file(self, filename: str, data: bytes) -> Source:
        store = self._need_store()
        assert self.profile is not None
        name = _SAFE_NAME.sub("_", Path(filename).name).strip(". ") or "fichero"
        if Path(name).suffix.lower() not in FILE_TYPES:
            raise KnowledgeError(f"Tipo de fichero no admitido. Usa: {', '.join(sorted(FILE_TYPES))}")
        if len(data) > MAX_FILE_BYTES:
            raise KnowledgeError("El fichero supera los 50 MB")
        folder = self._root / "files" / self.profile.profile.id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(data)
        source = store.upsert_source("file", f"file:{name}", "user", {})
        self.enqueue(source.id)
        return source

    def remove(self, source_id: int) -> None:
        store = self._need_store()
        source = store.source(source_id)
        if source is None:
            raise KnowledgeError("No existe esa fuente")
        if source.origin == "profile":
            raise KnowledgeError("Las fuentes del perfil se cambian en su profile.yaml")
        if source.kind == "file" and self.profile is not None:
            (self._root / "files" / self.profile.profile.id / source.location.removeprefix("file:")).unlink(
                missing_ok=True
            )
        store.remove_source(source_id)

    def enqueue(self, source_id: int) -> None:
        if source_id in self._queued:
            return
        store = self._need_store()
        source = store.source(source_id)
        if source is None:
            return
        self._queued.add(source_id)
        store.set_status(source_id, "queued")
        self._queue.put_nowait(source_id)
        self._progress(source, "queued")

    def refresh(self, source_id: int | None = None) -> int:
        store = self._need_store()
        targets = [s for s in store.sources() if source_id is None or s.id == source_id]
        for source in targets:
            self.enqueue(source.id)
        return len(targets)

    def _progress(self, source: Source, state: str, pages: int = 0, message: str = "") -> None:
        assert self.profile is not None
        self._bus.publish(
            KnowledgeProgress(
                profile=self.profile.profile.id, source_id=source.id, location=source.location,
                state=state, pages=pages, message=message,  # type: ignore[arg-type]
            )
        )  # fmt: skip

    # Embeddings -------------------------------------------------------------------------------

    def embedder(self) -> Embedder | None:
        settings = self._config().knowledge
        if settings.embeddings == "none":
            return None
        if settings.embeddings == "provider":
            provider = self._provider()
            if provider is None or not settings.provider_model:
                return None
            return ProviderEmbedder(provider.embed, settings.provider_model)
        if self._e5 is None:
            self._e5 = E5Embedder(self._cache, self._download_progress)
        return self._e5

    def _download_progress(self, received: int, fraction: float | None) -> None:
        """La primera búsqueda descarga el modelo de embeddings: se avisa para que no parezca colgado."""
        if fraction is not None and fraction >= 1:
            event = StatusChanged(component="knowledge", state="ready", detail="Modelo de búsqueda listo")
        else:
            detail = describe("el modelo de búsqueda", received, E5_SIZE_MB)
            event = StatusChanged(component="knowledge", state="starting", detail=detail, progress=fraction)
        self._bus.publish(event)

    async def _vectors(self, texts: list[str]) -> tuple[Any, str | None]:
        embedder = self.embedder()
        if embedder is None or not texts:
            return None, None
        try:
            vectors = await embedder.embed(texts, query=False)
            self.embedder_error = ""
            return vectors, embedder.name
        except Exception as exc:
            # Sin vectores la búsqueda sigue funcionando por palabras.
            self.embedder_error = str(exc)
            log.warning("No se pudieron calcular embeddings: %s", exc)
            return None, None

    async def backfill(self) -> int:
        """Calcula los vectores que falten (p. ej. tras cambiar de modelo de embeddings)."""
        store, embedder = self.store, self.embedder()
        if store is None or embedder is None:
            return 0
        done = 0
        while True:
            pending = store.missing_embeddings(embedder.name)
            if not pending:
                return done
            vectors, model = await self._vectors([text for _, text in pending])
            if vectors is None or model is None:
                return done
            store.set_embeddings(model, [chunk_id for chunk_id, _ in pending], vectors)
            done += len(pending)

    # Ingesta ----------------------------------------------------------------------------------

    async def _work(self) -> None:
        while True:
            source_id = await self._queue.get()
            self._queued.discard(source_id)
            store = self.store
            source = store.source(source_id) if store else None
            if store is None or source is None:
                continue
            self.current = source_id
            store.set_status(source_id, "running")
            self._progress(source, "running")
            try:
                pages = await (self._ingest_url(source) if source.kind == "url" else self._ingest_file(source))
                store.finish_source(source_id, "ready")
                self._progress(source, "ready", pages)
            except asyncio.CancelledError:
                store.set_status(source_id, "pending")
                raise
            except Exception as exc:
                log.warning("Fallo al procesar %s: %s", source.location, exc)
                store.finish_source(source_id, "error", str(exc))
                self._progress(source, "error", message=str(exc))
            finally:
                self.current = None

    async def _save(self, source: Source, document: Document, etag: str | None = None,
                    last_modified: str | None = None) -> bool:  # fmt: skip
        """Guarda un documento si ha cambiado. Devuelve True si se reprocesó."""
        assert self.store is not None
        content_hash = _hash(document.markdown)
        known = self.store.documents(source.id).get(document.url)
        if known is not None and known.hash == content_hash:
            self.store.touch_document(known.id)
            return False
        chunks = chunk_markdown(document.markdown)
        if not chunks:
            return False
        vectors, model = await self._vectors([passage(document.title, c.heading, c.text) for c in chunks])
        self.store.save_document(
            source.id, document.url, document.title, content_hash, chunks, vectors, model, etag, last_modified
        )
        return True

    async def _ingest_file(self, source: Source) -> int:
        assert self.profile is not None
        name = source.location.removeprefix("file:")
        path = self._root / "files" / self.profile.profile.id / name
        if not path.is_file():
            raise KnowledgeError(f"Ya no existe el fichero {name}")
        data = path.read_bytes()
        stem = Path(name).stem
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            document = await asyncio.to_thread(pdf_to_document, source.location, data, stem)
        elif suffix in {".html", ".htm"}:
            document = await asyncio.to_thread(html_to_document, source.location, data.decode("utf-8", "replace"))
        else:
            document = text_to_document(source.location, data.decode("utf-8", "replace"), stem)
        if document is None:
            raise KnowledgeError("No se encontró texto útil en el fichero")
        await self._save(source, document)
        return 1

    async def _ingest_url(self, source: Source) -> int:
        assert self.store is not None
        settings = self._config().knowledge
        options = source.options
        depth_limit = int(options.get("depth") or 0)
        max_pages = int(options.get("max_pages") or settings.max_pages)
        include = list(options.get("include") or [])
        exclude = list(options.get("exclude") or [])
        known = self.store.documents(source.id)
        start = source.location
        fetcher = self._fetcher_factory(settings.rate_per_second)
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        seen = {start}
        kept: set[str] = set()
        pages = 0
        failures = 0
        try:
            while queue and pages < max_pages:
                url, depth = queue.popleft()
                previous = known.get(url)
                # Las páginas de las que salen enlaces se piden enteras: con un «sin cambios» (304) no habría
                # enlaces y no se seguiría con lo que falte, por ejemplo tras una descarga interrumpida.
                cached = previous if depth >= depth_limit else None
                try:
                    page = await fetcher.fetch(
                        url,
                        etag=cached.etag if cached else None,
                        last_modified=cached.last_modified if cached else None,
                    )
                except FetchError as exc:
                    failures += 1
                    if url == start:
                        raise
                    log.info("Se omite %s: %s", url, exc)
                    if previous is not None:
                        kept.add(url)  # un fallo puntual no borra lo que ya teníamos
                    continue
                pages += 1
                if page.not_modified and previous is not None:
                    kept.add(url)
                    self.store.touch_document(previous.id)
                    if depth < depth_limit:
                        # Sin HTML no hay enlaces nuevos: se revisan las páginas que ya conocíamos.
                        for known_url in known:
                            if known_url not in seen:
                                seen.add(known_url)
                                queue.append((known_url, depth + 1))
                    self._progress(source, "running", pages, url)
                    continue
                if page.content_type.endswith("xml") and url == start:
                    for found in sitemap_urls(page.text):
                        if found not in seen and in_scope(found, start, include, exclude):
                            seen.add(found)
                            queue.append((found, depth_limit))
                    continue
                document = await self._document(page.url if page.url else url, page.content_type, page.body)
                if document is not None:
                    document = Document(url, document.title, document.markdown)
                    await self._save(source, document, page.etag, page.last_modified)
                    kept.add(url)
                if depth < depth_limit and page.content_type.startswith(("text/html", "application/xhtml")):
                    for found in links(page.url, page.text):
                        if found not in seen and in_scope(found, start, include, exclude):
                            seen.add(found)
                            queue.append((found, depth + 1))
                self._progress(source, "running", pages, url)
        finally:
            await fetcher.aclose()
        if pages and failures < pages:
            self.store.remove_documents(source.id, kept)
        if not kept:
            raise KnowledgeError("No se encontró contenido útil en esa dirección")
        return len(kept)

    @staticmethod
    async def _document(url: str, content_type: str, body: bytes) -> Document | None:
        if content_type == "application/pdf":
            return await asyncio.to_thread(pdf_to_document, url, body, url.rsplit("/", 1)[-1])
        text = body.decode("utf-8", errors="replace")
        if content_type.startswith(("text/html", "application/xhtml")):
            return await asyncio.to_thread(html_to_document, url, text)
        return text_to_document(url, text, url.rsplit("/", 1)[-1])

    async def _schedule(self) -> None:
        while True:
            await asyncio.sleep(60)
            self._due_refreshes()
            await asyncio.sleep(SCHEDULER_TICK_S - 60)

    def _due_refreshes(self) -> None:
        if self.store is None or self.profile is None or not self._config().knowledge.auto_refresh:
            return
        period = REFRESH_SECONDS.get(self.profile.profile.knowledge.refresh)
        if period is None:
            return
        now = time.time()
        for source in self.store.sources():
            # Solo fuentes que el usuario ya descargó alguna vez: nunca se descarga nada sin pedirlo.
            if source.kind == "url" and source.updated_at and now - source.updated_at > period:
                self.enqueue(source.id)

    # Búsqueda ---------------------------------------------------------------------------------

    async def search(self, query: str, top_k: int | None = None) -> list[Hit]:
        store = self.store
        if store is None or not query.strip():
            return []
        settings = self._config().knowledge
        vector = None
        model = None
        min_score = 0.84
        embedder = self.embedder()
        if embedder is not None and store.stats()["chunks"]:
            try:
                vector = (await embedder.embed([query], query=True))[0]
                model = embedder.name
                min_score = embedder.min_score
            except Exception as exc:
                self.embedder_error = str(exc)
        return await asyncio.to_thread(
            store.search, query, vector=vector, model=model, top_k=top_k or settings.top_k, min_score=min_score
        )
