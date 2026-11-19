import hashlib

import numpy as np
import pytest

from tainttrace.corpus.jats import Paragraph, Paper, Section
from tainttrace.corpus.chunk import chunk_paper
from tainttrace.corpus.store import CorpusStore
from tainttrace.retrieval.dense import DenseIndex
from tainttrace.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion


class HashEmbedder:
    """Deterministic bag-of-words vectors: shared words mean similar vectors."""

    dim = 64

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for w in text.lower().split():
            v[int(hashlib.md5(w.strip(".,").encode()).hexdigest(), 16) % self.dim] += 1
        return v / max(np.linalg.norm(v), 1e-9)

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


class KeywordReranker:
    def __init__(self, keyword: str):
        self.keyword = keyword

    def score(self, query, texts):
        return [float(t.lower().count(self.keyword)) for t in texts]


def _paper(i: int, topic: str, n_paras: int = 3) -> Paper:
    paras = [
        Paragraph(f"Paragraph {j} of paper {i} reports that {topic} drives tumour growth in many models.")
        for j in range(n_paras)
    ]
    return Paper(f"PMC{i}", f"10.1000/p{i}", None, f"Paper {i} on {topic}", "J", 2020, [],
                 [Section("Results", paras)], {})


@pytest.fixture
def store(tmp_path):
    s = CorpusStore(tmp_path / "c.sqlite")
    topics = ["hotair", "malat1", "hotair", "let-7", "mir-21"]
    for i, topic in enumerate(topics):
        p = _paper(i, topic)
        s.add_paper(p, chunk_paper(p, max_words=15), "test", epmc_retracted=False)
    return s


def test_rrf_rewards_agreement():
    fused = reciprocal_rank_fusion({"a": [1, 2, 3], "b": [3, 1, 4]})
    order = [c for c, _, _ in fused]
    assert order[0] == 1  # ranks 1 and 2
    assert order[1] == 3  # ranks 3 and 1
    assert fused[0][2] == {"a": 1, "b": 2}
    assert set(order) == {1, 2, 3, 4}


def test_dense_search_and_roundtrip(tmp_path, store):
    emb = HashEmbedder()
    index = DenseIndex.build(store, emb, "hash")
    assert len(index.ids) == store.stats()["chunks"]
    path = tmp_path / "dense.npz"
    index.save(path)
    loaded = DenseIndex.load(path)
    assert loaded.model == "hash"
    top = loaded.search(emb.embed_query("malat1 tumour growth"), 3)
    assert store.get_chunks([top[0][0]])[0].title == "Paper 1 on malat1"
    assert [s for _, s in top] == sorted((s for _, s in top), reverse=True)


def test_hybrid_caps_chunks_per_paper(store):
    emb = HashEmbedder()
    retriever = HybridRetriever(store, DenseIndex.build(store, emb, "hash"), emb, max_per_paper=1)
    results = retriever.retrieve("hotair tumour growth", k=5)
    papers = [r.chunk.paper_id for r in results]
    assert len(papers) == len(set(papers))
    assert results[0].chunk.title.endswith("hotair")


def test_reranker_decides_final_order(store):
    emb = HashEmbedder()
    retriever = HybridRetriever(
        store, DenseIndex.build(store, emb, "hash"), emb, reranker=KeywordReranker("let-7")
    )
    results = retriever.retrieve("tumour growth", k=3)
    assert results[0].chunk.title == "Paper 3 on let-7"
    assert results[0].rerank_score > 0
    assert all("bm25" in r.ranks or "dense" in r.ranks for r in results)


def test_bm25_only_without_dense(store):
    results = HybridRetriever(store, None, None).retrieve("malat1", k=2)
    assert results and results[0].chunk.title == "Paper 1 on malat1"
    assert results[0].rerank_score is None
