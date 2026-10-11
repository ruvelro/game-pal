"""Embeddings para la búsqueda semántica: modelo local en ONNX o el servidor de modelos local."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

Vectors = npt.NDArray[np.float32]

E5_REPO = "Xenova/multilingual-e5-small"
# Revisión fija: el modelo no cambia sin que lo decidamos nosotros.
E5_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
BATCH = 16


class Embedder(Protocol):
    name: str
    # Similitud mínima para considerar relevante un fragmento: cada modelo tiene su escala.
    min_score: float

    async def embed(self, texts: list[str], *, query: bool) -> Vectors: ...


def _normalize(vectors: Vectors) -> Vectors:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-9)).astype(np.float32)


class E5Embedder:
    """multilingual-e5-small cuantizado (118 MB): bueno entre idiomas y rápido en CPU."""

    name = "multilingual-e5-small-q8"
    # Medido: en este modelo lo no relacionado ronda 0,78-0,81 y lo relevante supera 0,88.
    min_score = 0.84

    def __init__(self, cache_dir: Path) -> None:
        self._cache = cache_dir
        self._session: Any = None
        self._tokenizer: Any = None
        self._inputs: set[str] = set()
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model = hf_hub_download(E5_REPO, "onnx/model_quantized.onnx", revision=E5_REVISION, cache_dir=self._cache)
        vocab = hf_hub_download(E5_REPO, "tokenizer.json", revision=E5_REVISION, cache_dir=self._cache)
        tokenizer = Tokenizer.from_file(vocab)
        tokenizer.enable_truncation(512)
        tokenizer.enable_padding(pad_id=1, pad_token="<pad>")  # noqa: S106 - token de relleno, no una contraseña
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._session.get_inputs()}
        self._tokenizer = tokenizer

    def _run(self, texts: list[str]) -> Vectors:
        with self._lock:
            self._load()
            out: list[Vectors] = []
            for start in range(0, len(texts), BATCH):
                encoded = self._tokenizer.encode_batch(texts[start : start + BATCH])
                ids = np.array([e.ids for e in encoded], dtype=np.int64)
                mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                feeds = {"input_ids": ids, "attention_mask": mask}
                if "token_type_ids" in self._inputs:
                    feeds["token_type_ids"] = np.zeros_like(ids)
                hidden = self._session.run(None, feeds)[0]
                pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
                out.append(_normalize(pooled.astype(np.float32)))
            return np.concatenate(out) if out else np.zeros((0, 384), dtype=np.float32)

    async def embed(self, texts: list[str], *, query: bool) -> Vectors:
        prefix = "query: " if query else "passage: "
        return await asyncio.to_thread(self._run, [prefix + t for t in texts])


class ProviderEmbedder:
    """Embeddings del servidor local compatible con OpenAI (p. ej. nomic-embed en LM Studio u Ollama)."""

    def __init__(self, embed: Any, model: str) -> None:
        self.name = f"provider-{model}"
        self.min_score = 0.45
        self._embed = embed
        self._model = model

    async def embed(self, texts: list[str], *, query: bool) -> Vectors:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            vectors += await self._embed(texts[start : start + BATCH], model=self._model)
        return _normalize(np.array(vectors, dtype=np.float32))
