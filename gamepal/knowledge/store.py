"""Índice de conocimiento de un perfil: SQLite con búsqueda por palabras (FTS5) y por significado.

Los vectores se guardan junto a cada fragmento y se buscan por fuerza bruta con numpy: con unos
miles de fragmentos por juego tarda milisegundos y evita depender de extensiones nativas de SQLite.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from gamepal.knowledge.embed import Vectors
from gamepal.knowledge.extract import Chunk
from gamepal.perception.text import normalize

SCHEMA_VERSION = 1
RRF_K = 60
CANDIDATES = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('url', 'file')),
    location TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL CHECK (origin IN ('profile', 'user')),
    options TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL,
    updated_at REAL,
    pages INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    hash TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    fetched_at REAL NOT NULL,
    UNIQUE (source_id, url)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, heading, text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, title, heading, text) VALUES (new.id, new.title, new.heading, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, title, heading, text)
    VALUES ('delete', old.id, old.title, old.heading, old.text);
END;
"""


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    kind: str
    location: str
    origin: str
    options: dict[str, Any]
    status: str
    error: str
    added_at: float
    updated_at: float | None
    pages: int
    chunks: int


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: int
    url: str
    hash: str
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: int
    title: str
    heading: str
    text: str
    url: str
    score: float


class KnowledgeStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?), ('revision', '0')", (str(SCHEMA_VERSION),)
        )
        self._vectors: tuple[str, str, np.ndarray, np.ndarray] | None = None  # (revisión, modelo, ids, matriz)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
                self._db.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'")
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _revision(self) -> str:
        row = self._db.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
        return str(row[0])

    # Fuentes ----------------------------------------------------------------------------------

    @staticmethod
    def _source(row: sqlite3.Row) -> Source:
        return Source(
            row["id"], row["kind"], row["location"], row["origin"], json.loads(row["options"]), row["status"],
            row["error"], row["added_at"], row["updated_at"], row["pages"], row["chunks"],
        )  # fmt: skip

    def sources(self) -> list[Source]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM sources ORDER BY origin, id").fetchall()
        return [self._source(r) for r in rows]

    def source(self, source_id: int) -> Source | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source(row) if row else None

    def upsert_source(self, kind: str, location: str, origin: str, options: dict[str, Any]) -> Source:
        with self._tx() as db:
            db.execute(
                """INSERT INTO sources(kind, location, origin, options, added_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(location) DO UPDATE SET options = excluded.options""",
                (kind, location, origin, json.dumps(options), time.time()),
            )
            row = db.execute("SELECT * FROM sources WHERE location = ?", (location,)).fetchone()
        return self._source(row)

    def remove_source(self, source_id: int) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def set_status(self, source_id: int, status: str, error: str = "") -> None:
        with self._lock:
            self._db.execute("UPDATE sources SET status = ?, error = ? WHERE id = ?", (status, error[:500], source_id))

    def finish_source(self, source_id: int, status: str, error: str = "") -> None:
        with self._tx() as db:
            db.execute(
                """UPDATE sources SET status = ?, error = ?, updated_at = ?,
                   pages = (SELECT COUNT(*) FROM documents WHERE source_id = ?),
                   chunks = (SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id = c.document_id
                             WHERE d.source_id = ?)
                   WHERE id = ?""",
                (status, error[:500], time.time(), source_id, source_id, source_id),
            )

    # Documentos -------------------------------------------------------------------------------

    def documents(self, source_id: int) -> dict[str, StoredDocument]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, url, hash, etag, last_modified FROM documents WHERE source_id = ?", (source_id,)
            ).fetchall()
        return {r["url"]: StoredDocument(r["id"], r["url"], r["hash"], r["etag"], r["last_modified"]) for r in rows}

    def touch_document(self, document_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE documents SET fetched_at = ? WHERE id = ?", (time.time(), document_id))

    def save_document(
        self,
        source_id: int,
        url: str,
        title: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: Vectors | None,
        model: str | None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Sustituye el documento y todos sus fragmentos de una vez."""
        with self._tx() as db:
            db.execute("DELETE FROM documents WHERE source_id = ? AND url = ?", (source_id, url))
            cursor = db.execute(
                """INSERT INTO documents(source_id, url, title, hash, etag, last_modified, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (source_id, url, title, content_hash, etag, last_modified, time.time()),
            )
            document_id = cursor.lastrowid
            for position, chunk in enumerate(chunks):
                blob = vectors[position].astype(np.float32).tobytes() if vectors is not None else None
                db.execute(
                    """INSERT INTO chunks(document_id, position, title, heading, text, model, embedding)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (document_id, position, title, chunk.heading, chunk.text, model if blob else None, blob),
                )

    def remove_documents(self, source_id: int, keep: set[str]) -> int:
        """Borra los documentos de la fuente que ya no aparecen. Devuelve cuántos."""
        current = self.documents(source_id)
        gone = [doc.id for url, doc in current.items() if url not in keep]
        if gone:
            with self._tx() as db:
                db.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i in gone])
        return len(gone)

    def missing_embeddings(self, model: str, limit: int = 256) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, heading, text FROM chunks WHERE model IS NOT ? OR embedding IS NULL LIMIT ?",
                (model, limit),
            ).fetchall()
        return [(r["id"], passage(r["title"], r["heading"], r["text"])) for r in rows]

    def set_embeddings(self, model: str, ids: list[int], vectors: Vectors) -> None:
        with self._tx() as db:
            db.executemany(
                "UPDATE chunks SET model = ?, embedding = ? WHERE id = ?",
                [(model, vectors[i].astype(np.float32).tobytes(), chunk_id) for i, chunk_id in enumerate(ids)],
            )

    def titles(self, limit: int = 5000) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT title FROM documents LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    def stats(self) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT (SELECT COUNT(*) FROM documents) AS pages, (SELECT COUNT(*) FROM chunks) AS chunks"
            ).fetchone()
        return {"pages": row["pages"], "chunks": row["chunks"]}

    # Búsqueda ---------------------------------------------------------------------------------

    def _lexical(self, query: str, limit: int) -> list[int]:
        terms = [t for t in normalize(query).split() if len(t) > 1][:24]
        if not terms:
            return []
        # Cada término entre comillas: el texto del usuario nunca se interpreta como sintaxis de FTS5.
        expression = " OR ".join(f'"{term}"' for term in terms)
        with self._lock:
            rows = self._db.execute(
                # Pesos de bm25: el título pesa más que la sección, y esta más que el texto.
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts, 4.0, 2.0, 1.0) LIMIT ?",
                (expression, limit),
            ).fetchall()
        return [r[0] for r in rows]

    def _matrix(self, model: str) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            revision = self._revision()
            if self._vectors is not None and self._vectors[0] == revision and self._vectors[1] == model:
                return self._vectors[2], self._vectors[3]
            rows = self._db.execute(
                "SELECT id, embedding FROM chunks WHERE model = ? AND embedding IS NOT NULL", (model,)
            ).fetchall()
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            matrix = (
                np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                if rows
                else np.zeros((0, 1), dtype=np.float32)
            )
            self._vectors = (revision, model, ids, matrix)
            return ids, matrix

    def _semantic(self, vector: Vectors, model: str, limit: int, min_score: float) -> list[int]:
        ids, matrix = self._matrix(model)
        if not len(ids) or matrix.shape[1] != vector.shape[0]:
            return []
        scores = matrix @ vector
        order = np.argsort(-scores)[:limit]
        best = float(scores[order[0]]) if len(order) else 0.0
        # Solo lo que se parece de verdad: cerca del mejor resultado y por encima de un mínimo.
        return [int(ids[i]) for i in order if scores[i] >= min_score and scores[i] >= best - 0.06]

    def search(
        self,
        query: str,
        *,
        vector: Vectors | None = None,
        model: str | None = None,
        top_k: int = 5,
        min_score: float = 0.84,
    ) -> list[Hit]:
        lexical = self._lexical(query, CANDIDATES)
        semantic = self._semantic(vector, model, CANDIDATES, min_score) if vector is not None and model else []
        fused: dict[int, float] = {}
        for ranking in (lexical, semantic):
            for rank, chunk_id in enumerate(ranking):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(fused.items(), key=lambda item: -item[1])[:top_k]
        if not best:
            return []
        marks = ",".join("?" for _ in best)
        with self._lock:
            rows = self._db.execute(
                f"""SELECT c.id, c.title, c.heading, c.text, d.url FROM chunks c
                    JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})""",  # noqa: S608
                [chunk_id for chunk_id, _ in best],
            ).fetchall()
        by_id = {r["id"]: r for r in rows}
        return [
            Hit(cid, by_id[cid]["title"], by_id[cid]["heading"], by_id[cid]["text"], by_id[cid]["url"], score)
            for cid, score in best
            if cid in by_id
        ]


def passage(title: str, heading: str, text: str) -> str:
    """Lo que se convierte en vector: el título y la sección dan contexto al fragmento."""
    head = f"{title} — {heading}" if heading else title
    return f"{head}\n{text}"
