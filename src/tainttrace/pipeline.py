"""Load the default corpus, retriever and generator in one place."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .corpus.store import CorpusStore, default_corpus_dir
from .generate.answer import Answer, AnswerGenerator
from .retrieval.dense import DEFAULT_EMBED_MODEL, DenseIndex, FastEmbedder
from .retrieval.hybrid import FastReranker, HybridRetriever, Retrieved


def load_retriever(corpus_dir: Path | None = None, rerank: bool = True) -> HybridRetriever:
    corpus_dir = corpus_dir or default_corpus_dir()
    store = CorpusStore(corpus_dir / "corpus.sqlite")
    dense_path = corpus_dir / "dense.npz"
    if not dense_path.exists():
        raise FileNotFoundError(f"No dense index at {dense_path}. Run `tainttrace corpus embed`.")
    dense = DenseIndex.load(dense_path)
    embedder = FastEmbedder(dense.model or DEFAULT_EMBED_MODEL)
    return HybridRetriever(store, dense, embedder, FastReranker() if rerank else None)


@dataclass
class RagResult:
    question: str
    retrieved: list[Retrieved]
    answer: Answer | None


class RagPipeline:
    def __init__(self, retriever: HybridRetriever, generator: AnswerGenerator | None):
        self.retriever = retriever
        self.generator = generator

    def ask(self, question: str, k: int = 8) -> RagResult:
        retrieved = self.retriever.retrieve(question, k)
        answer = self.generator.answer(question, retrieved) if self.generator else None
        return RagResult(question, retrieved, answer)
