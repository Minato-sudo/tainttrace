"""SQLite corpus: papers, their references, and chunks with an FTS5 index."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .chunk import Chunk
from .jats import Paper

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id    INTEGER PRIMARY KEY,
    pmcid       TEXT UNIQUE NOT NULL,
    doi         TEXT,
    pmid        TEXT,
    title       TEXT NOT NULL,
    journal     TEXT NOT NULL,
    year        INTEGER,
    origin      TEXT NOT NULL,   -- which corpus query selected it
    epmc_retracted_tag INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS papers_doi ON papers(doi);
CREATE TABLE IF NOT EXISTS refs (
    paper_id INTEGER NOT NULL REFERENCES papers(paper_id),
    ref_id   TEXT NOT NULL,
    doi      TEXT,
    pmid     TEXT,
    title    TEXT NOT NULL,
    PRIMARY KEY (paper_id, ref_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   INTEGER PRIMARY KEY,
    paper_id   INTEGER NOT NULL REFERENCES papers(paper_id),
    ord        INTEGER NOT NULL,
    section    TEXT NOT NULL,
    text       TEXT NOT NULL,
    paragraphs TEXT NOT NULL      -- JSON [{"text": ..., "cites": [ref_id, ...]}]
);
CREATE INDEX IF NOT EXISTS chunks_paper ON chunks(paper_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, section, text, content='', contentless_delete=1, tokenize='porter unicode61'
);
"""


def default_corpus_dir() -> Path:
    return Path(os.environ.get("TAINTTRACE_DATA_DIR", "data")) / "corpus"


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: int
    paper_id: int
    pmcid: str
    doi: str | None
    title: str
    journal: str
    year: int | None
    section: str
    paragraphs: list[dict]

    @property
    def text(self) -> str:
        return "\n\n".join(p["text"] for p in self.paragraphs)


class CorpusStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_corpus_dir() / "corpus.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def has_paper(self, pmcid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM papers WHERE pmcid = ?", (pmcid,)).fetchone() is not None

    def add_paper(self, paper: Paper, chunks: list[Chunk], origin: str, epmc_retracted: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO papers (pmcid, doi, pmid, title, journal, year, origin, epmc_retracted_tag) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (paper.pmcid, paper.doi, paper.pmid, paper.title, paper.journal, paper.year,
             origin, int(epmc_retracted)),
        )
        paper_id = cur.lastrowid
        self.conn.executemany(
            "INSERT OR IGNORE INTO refs VALUES (?,?,?,?,?)",
            [(paper_id, r.ref_id, r.doi, r.pmid, r.title) for r in paper.references.values()],
        )
        for c in chunks:
            paras = [{"text": p.text, "cites": p.cites} for p in c.paragraphs]
            cur = self.conn.execute(
                "INSERT INTO chunks (paper_id, ord, section, text, paragraphs) VALUES (?,?,?,?,?)",
                (paper_id, c.ord, c.section, c.text, json.dumps(paras)),
            )
            self.conn.execute(
                "INSERT INTO chunks_fts (rowid, title, section, text) VALUES (?,?,?,?)",
                (cur.lastrowid, paper.title, c.section, c.text),
            )
        self.conn.commit()
        return paper_id

    def chunk_ids(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")]

    def iter_chunk_texts(self, batch: int = 512):
        """Yield (chunk_ids, texts) batches in chunk_id order, for embedding."""
        last = 0
        while True:
            rows = self.conn.execute(
                "SELECT c.chunk_id, p.title, c.section, c.text FROM chunks c "
                "JOIN papers p USING (paper_id) WHERE c.chunk_id > ? ORDER BY c.chunk_id LIMIT ?",
                (last, batch),
            ).fetchall()
            if not rows:
                return
            last = rows[-1][0]
            yield [r[0] for r in rows], [f"{r[1]}. {r[2]}. {r[3]}" for r in rows]

    def get_chunks(self, chunk_ids: list[int]) -> list[ChunkRecord]:
        if not chunk_ids:
            return []
        marks = ",".join("?" * len(chunk_ids))
        rows = self.conn.execute(
            "SELECT c.chunk_id, c.paper_id, p.pmcid, p.doi, p.title, p.journal, p.year, "
            f"c.section, c.paragraphs FROM chunks c JOIN papers p USING (paper_id) "
            f"WHERE c.chunk_id IN ({marks})",
            chunk_ids,
        ).fetchall()
        by_id = {
            r["chunk_id"]: ChunkRecord(
                chunk_id=r["chunk_id"], paper_id=r["paper_id"], pmcid=r["pmcid"], doi=r["doi"],
                title=r["title"], journal=r["journal"], year=r["year"], section=r["section"],
                paragraphs=json.loads(r["paragraphs"]),
            )
            for r in rows
        }
        return [by_id[i] for i in chunk_ids if i in by_id]

    def bm25(self, query: str, limit: int) -> list[tuple[int, float]]:
        """Top chunk ids by BM25 (higher score is better)."""
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query.lower()).split() if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        rows = self.conn.execute(
            # Title and section matches weigh more than body matches.
            "SELECT rowid, -bm25(chunks_fts, 2.0, 1.5, 1.0) FROM chunks_fts "
            "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 2.0, 1.5, 1.0) LIMIT ?",
            (match, limit),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def stats(self) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "papers": q("SELECT count(*) FROM papers"),
            "papers_epmc_retracted_tag": q("SELECT count(*) FROM papers WHERE epmc_retracted_tag = 1"),
            "chunks": q("SELECT count(*) FROM chunks"),
            "references": q("SELECT count(*) FROM refs"),
            "references_with_doi": q("SELECT count(*) FROM refs WHERE doi IS NOT NULL"),
        }
