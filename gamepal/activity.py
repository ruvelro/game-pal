"""Registro de actividad: cada petición a un modelo, local o en la nube, con tiempos, tokens y coste.

Se guarda en un SQLite local (las últimas 5000 peticiones) y nunca incluye el contenido de las peticiones.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_ROWS = 5000

# Precios orientativos en dólares por millón de tokens (entrada, salida). Pueden cambiar: revísalos
# en la web de cada proveedor. OpenRouter informa del coste real y no necesita esta tabla.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    local INTEGER NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL,
    fallback INTEGER NOT NULL DEFAULT 0,
    ms REAL NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost REAL,
    estimated INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS requests_at ON requests(at);
"""


@dataclass(frozen=True, slots=True)
class RequestRecord:
    role: str
    provider: str
    local: bool
    model: str
    ok: bool
    ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    fallback: bool = False
    error: str = ""
    at: float = 0.0


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = PRICES.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


class ActivityStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, record: RequestRecord) -> RequestRecord:
        estimated = False
        cost = record.cost
        if cost is None and not record.local:
            cost = estimate_cost(record.model, record.input_tokens, record.output_tokens)
            estimated = cost is not None
        stamp = record.at or time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO requests(at, role, provider, local, model, ok, fallback, ms, input_tokens,
                   output_tokens, cost, estimated, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (stamp, record.role, record.provider, int(record.local), record.model, int(record.ok),
                 int(record.fallback), record.ms, record.input_tokens, record.output_tokens, cost,
                 int(estimated), record.error[:300]),
            )  # fmt: skip
            self._db.execute(
                "DELETE FROM requests WHERE id NOT IN (SELECT id FROM requests ORDER BY id DESC LIMIT ?)",
                (MAX_ROWS,),
            )
        return RequestRecord(**{**asdict(record), "cost": cost, "at": stamp})

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM requests ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def summary(self, since: float) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                """SELECT provider, local, COUNT(*) AS requests, SUM(ok) AS ok, AVG(ms) AS avg_ms,
                   SUM(COALESCE(input_tokens, 0)) AS input_tokens, SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                   SUM(COALESCE(cost, 0)) AS cost, MAX(estimated) AS estimated
                   FROM requests WHERE at >= ? GROUP BY provider, local ORDER BY requests DESC""",
                (since,),
            ).fetchall()
            hourly = self._db.execute(
                """SELECT CAST((at - ?) / 3600 AS INTEGER) AS bucket, local, COUNT(*) AS requests
                   FROM requests WHERE at >= ? GROUP BY bucket, local""",
                (since, since),
            ).fetchall()
        providers = [dict(r) for r in rows]
        total = sum(p["requests"] for p in providers)
        local = sum(p["requests"] for p in providers if p["local"])
        return {
            "since": since,
            "total": total,
            "local": local,
            "cloud": total - local,
            "cost": round(sum(p["cost"] or 0 for p in providers), 6),
            "providers": providers,
            "hourly": [dict(r) for r in hourly],
        }
