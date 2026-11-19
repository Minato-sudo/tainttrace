"""Hybrid retrieval: BM25 + dense, fused with reciprocal rank, then reranked."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from ..corpus.store import ChunkRecord, CorpusStore
from .dense import DenseIndex, Embedder, onnx_providers

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class FastReranker:
    def __init__(self, model: str = DEFAULT_RERANK_MODEL):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model = TextCrossEncoder(model, providers=onnx_providers())

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.rerank(query, texts, batch_size=16)]


@dataclass
class Retrieved:
    chunk: ChunkRecord
    rerank_score: float | None
    fused_score: float
    ranks: dict[str, int] = field(default_factory=dict)
    """1-based rank of this chunk in each first-stage list it appeared in."""


def reciprocal_rank_fusion(lists: dict[str, list[int]], k: int = 60) -> list[tuple[int, float, dict]]:
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for name, ids in lists.items():
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(chunk_id, {})[name] = rank
    ordered = sorted(scores, key=lambda c: -scores[c])
    return [(c, scores[c], ranks[c]) for c in ordered]


class HybridRetriever:
    def __init__(
        self,
        store: CorpusStore,
        dense: DenseIndex | None,
        embedder: Embedder | None,
        reranker: Reranker | None = None,
        first_stage: int = 50,
        rerank_pool: int = 40,
        max_per_paper: int = 2,
    ):
        self.store = store
        self.dense = dense
        self.embedder = embedder
        self.reranker = reranker
        self.first_stage = first_stage
        self.rerank_pool = rerank_pool
        self.max_per_paper = max_per_paper

    def retrieve(self, query: str, k: int = 8) -> list[Retrieved]:
        lists = {"bm25": [c for c, _ in self.store.bm25(query, self.first_stage)]}
        if self.dense is not None and self.embedder is not None:
            qvec = self.embedder.embed_query(query)
            lists["dense"] = [c for c, _ in self.dense.search(qvec, self.first_stage)]
        fused = reciprocal_rank_fusion(lists)[: self.rerank_pool]
        if not fused:
            return []

        records = {c.chunk_id: c for c in self.store.get_chunks([c for c, _, _ in fused])}
        candidates = [
            Retrieved(records[c], None, score, ranks) for c, score, ranks in fused if c in records
        ]
        if self.reranker is not None:
            texts = [f"{r.chunk.title}. {r.chunk.text}" for r in candidates]
            for r, s in zip(candidates, self.reranker.score(query, texts), strict=True):
                r.rerank_score = s
            candidates.sort(key=lambda r: -r.rerank_score)

        # Several chunks from one paper crowd out other evidence.
        per_paper: Counter[int] = Counter()
        out = []
        for r in candidates:
            if per_paper[r.chunk.paper_id] >= self.max_per_paper:
                continue
            per_paper[r.chunk.paper_id] += 1
            out.append(r)
            if len(out) == k:
                break
        return out
