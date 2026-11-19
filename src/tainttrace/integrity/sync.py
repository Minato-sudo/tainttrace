"""Download the latest Retraction Watch CSV and rebuild the store."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .rw import RW_CSV_URL, read_rw_csv
from .store import build_store, default_db_path


def download_rw_csv(dest: Path, url: str = RW_CSV_URL) -> Path:
    """Stream the CSV to `dest`, replacing it only after a complete download."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(".partial")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with partial.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    with partial.open(encoding="utf-8") as f:
        header = f.readline()
    if not header.startswith("Record ID,"):
        partial.unlink()
        raise ValueError(f"Unexpected content from {url}: {header[:80]!r}")
    os.replace(partial, dest)
    return dest


def sync(
    csv_path: Path | None = None,
    db_path: Path | None = None,
    download: bool = True,
) -> int:
    """Refresh the integrity store; returns the number of notices loaded."""
    db_path = db_path or default_db_path()
    csv_path = csv_path or db_path.parent / "raw" / "retraction_watch.csv"
    if download:
        download_rw_csv(csv_path)
    return build_store(read_rw_csv(csv_path), db_path, source_file=csv_path)
