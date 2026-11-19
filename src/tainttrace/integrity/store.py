"""SQLite store of editorial notices, with DOI, PMID and title indexes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Notice, NoticeType
from .normalize import normalize_title

SCHEMA = """
CREATE TABLE notices (
    record_id     TEXT PRIMARY KEY,
    notice_type   TEXT NOT NULL,
    notice_date   TEXT,
    doi           TEXT,
    pmid          TEXT,
    notice_doi    TEXT,
    title         TEXT NOT NULL,
    title_norm    TEXT NOT NULL,
    journal       TEXT NOT NULL,
    authors       TEXT NOT NULL,
    original_date TEXT,
    reasons       TEXT NOT NULL,
    source        TEXT NOT NULL
);
CREATE INDEX notices_doi ON notices(doi);
CREATE INDEX notices_pmid ON notices(pmid);
CREATE INDEX notices_notice_doi ON notices(notice_doi);
CREATE INDEX notices_title_norm ON notices(title_norm);
CREATE VIRTUAL TABLE titles_fts USING fts5(
    title_norm, content='notices', content_rowid='rowid'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Remote lookups live in the same file but survive re-ingest.
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_cache (
    source     TEXT NOT NULL,
    doi        TEXT NOT NULL,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, doi)
);
"""


def default_db_path() -> Path:
    base = os.environ.get("TAINTTRACE_DATA_DIR", "data")
    return Path(base) / "integrity.sqlite"


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _row_to_notice(row: sqlite3.Row) -> Notice:
    return Notice(
        record_id=row["record_id"],
        notice_type=NoticeType(row["notice_type"]),
        notice_date=date.fromisoformat(row["notice_date"]) if row["notice_date"] else None,
        doi=row["doi"],
        pmid=row["pmid"],
        title=row["title"],
        journal=row["journal"],
        authors=row["authors"],
        original_date=(
            date.fromisoformat(row["original_date"]) if row["original_date"] else None
        ),
        notice_doi=row["notice_doi"],
        reasons=tuple(json.loads(row["reasons"])),
        source=row["source"],
    )


def build_store(
    notices: Iterable[Notice], db_path: Path, source_file: Path | None = None
) -> int:
    """Build a fresh store and atomically swap it into place.

    Readers keep seeing the old file until the new one is complete. The
    remote lookup cache is carried over.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".building")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA)
        conn.executescript(CACHE_SCHEMA)
        count = 0
        for n in notices:
            conn.execute(
                "INSERT OR REPLACE INTO notices VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    n.record_id,
                    n.notice_type.value,
                    _iso(n.notice_date),
                    n.doi,
                    n.pmid,
                    n.notice_doi,
                    n.title,
                    normalize_title(n.title),
                    n.journal,
                    n.authors,
                    _iso(n.original_date),
                    json.dumps(n.reasons),
                    n.source,
                ),
            )
            count += 1
        conn.execute("INSERT INTO titles_fts(titles_fts) VALUES ('rebuild')")

        meta = {
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "notice_count": str(count),
        }
        if source_file is not None:
            meta["source_file"] = str(source_file)
            meta["source_sha256"] = hashlib.sha256(source_file.read_bytes()).hexdigest()
        conn.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())

        if db_path.exists():
            conn.execute("ATTACH DATABASE ? AS old", (str(db_path),))
            has_cache = conn.execute(
                "SELECT 1 FROM old.sqlite_master WHERE name = 'remote_cache'"
            ).fetchone()
            if has_cache:
                conn.execute("INSERT INTO remote_cache SELECT * FROM old.remote_cache")
            conn.commit()
            conn.execute("DETACH DATABASE old")
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, db_path)
    return count


class NoticeStore:
    """Read access to a built store, plus the remote lookup cache."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_db_path()
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No integrity store at {self.db_path}. Run `tainttrace sync` first."
            )
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(CACHE_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM meta")}

    def _select(self, where: str, args: tuple) -> list[Notice]:
        rows = self.conn.execute(f"SELECT * FROM notices WHERE {where}", args)
        return [_row_to_notice(r) for r in rows]

    def by_doi(self, doi: str) -> list[Notice]:
        return self._select("doi = ?", (doi,))

    def by_pmid(self, pmid: str) -> list[Notice]:
        return self._select("pmid = ?", (pmid,))

    def by_notice_doi(self, doi: str) -> list[Notice]:
        return self._select("notice_doi = ?", (doi,))

    def by_title_norm(self, title_norm: str) -> list[Notice]:
        return self._select("title_norm = ?", (title_norm,))

    def siblings(self, notice: Notice) -> list[Notice]:
        """All notices about the same paper as `notice`."""
        if notice.doi:
            return self.by_doi(notice.doi)
        if notice.pmid:
            return self.by_pmid(notice.pmid)
        return self.by_title_norm(normalize_title(notice.title))

    def title_candidates(self, title_norm: str, limit: int = 25) -> list[Notice]:
        """Full-text candidates for a normalized title, best first."""
        words = [w for w in title_norm.split() if len(w) > 2]
        if not words:
            return []
        # Rarer, longer words first keeps the OR query selective.
        words = sorted(set(words), key=len, reverse=True)[:12]
        query = " OR ".join(f'"{w}"' for w in words)
        rows = self.conn.execute(
            "SELECT n.* FROM titles_fts f JOIN notices n ON n.rowid = f.rowid "
            "WHERE titles_fts MATCH ? ORDER BY bm25(titles_fts) LIMIT ?",
            (query, limit),
        )
        return [_row_to_notice(r) for r in rows]

    def cache_get(self, source: str, doi: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM remote_cache WHERE source = ? AND doi = ?",
            (source, doi),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def cache_put(self, source: str, doi: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO remote_cache VALUES (?, ?, ?, ?)",
            (source, doi, json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        self.conn.commit()
