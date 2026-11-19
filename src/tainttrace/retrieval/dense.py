"""Dense embeddings for chunks, stored as a normalized float32 matrix."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from ..corpus.store import CorpusStore

log = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# bge v1.5 retrieval works best with this instruction on queries only.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def onnx_providers() -> list[str]:
    """Use the GPU when the CUDA build of onnxruntime is installed."""
    import onnxruntime

    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        return ["CPUExecutionProvider"]
    # The CUDA and cuDNN libraries come from pip wheels inside the virtualenv,
    # which the system loader doesn't search; load them explicitly.
    onnxruntime.preload_dlls()
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return (m / np.maximum(norms, 1e-12)).astype(np.float32)


class FastEmbedder:
    def __init__(self, model: str = DEFAULT_EMBED_MODEL, batch_size: int = 64):
        from fastembed import TextEmbedding

        self.model_name = model
        self.model = TextEmbedding(model, providers=onnx_providers())
        self.batch_size = batch_size
        self.query_prefix = _BGE_QUERY_PREFIX if "bge" in model.lower() else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        vecs = list(self.model.passage_embed(texts, batch_size=self.batch_size))
        return _normalize(np.vstack(vecs))

    def embed_query(self, text: str) -> np.ndarray:
        vec = next(iter(self.model.query_embed(self.query_prefix + text)))
        return _normalize(np.asarray(vec))


class DenseIndex:
    """Chunk embeddings aligned with chunk ids, searched by brute force.

    Brute-force cosine over ~100k x 384 floats takes a few milliseconds and
    needs no vector database.
    """

    def __init__(self, ids: np.ndarray, vectors: np.ndarray, model: str):
        self.ids = ids
        self.vectors = vectors
        self.model = model

    @classmethod
    def build(cls, store: CorpusStore, embedder: Embedder, model: str) -> DenseIndex:
        ids: list[int] = []
        parts: list[np.ndarray] = []
        for batch_ids, texts in store.iter_chunk_texts():
            parts.append(embedder.embed_documents(texts))
            ids.extend(batch_ids)
            if len(ids) % 5120 < len(batch_ids):
                log.info("embedded %d chunks", len(ids))
        vectors = np.vstack(parts) if parts else np.zeros((0, 0), np.float32)
        return cls(np.asarray(ids, dtype=np.int64), vectors, model)

    def save(self, path: Path) -> None:
        np.savez(path, ids=self.ids, vectors=self.vectors, model=np.asarray(self.model))

    @classmethod
    def load(cls, path: Path) -> DenseIndex:
        data = np.load(path)
        return cls(data["ids"], data["vectors"], str(data["model"]))

    def search(self, query_vec: np.ndarray, limit: int) -> list[tuple[int, float]]:
        if len(self.ids) == 0:
            return []
        scores = self.vectors @ query_vec
        limit = min(limit, len(scores))
        top = np.argpartition(-scores, limit - 1)[:limit]
        top = top[np.argsort(-scores[top])]
        return [(int(self.ids[i]), float(scores[i])) for i in top]
