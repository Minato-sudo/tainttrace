"""Read the Retraction Watch CSV published by Crossref."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from .models import Notice, NoticeType
from .normalize import normalize_doi, normalize_pmid

RW_CSV_URL = (
    "https://gitlab.com/crossref/retraction-watch-data/-/raw/main/"
    "retraction_watch.csv?inline=false"
)


def parse_rw_date(value: str) -> date | None:
    """Parse RW's "M/D/YYYY H:MM" timestamps; blanks give None."""
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value.split()[0], "%m/%d/%Y").date()
    except ValueError:
        return None


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def row_to_notice(row: dict[str, str]) -> Notice | None:
    """Convert one CSV row; rows with an unknown notice type are skipped."""
    notice_type = NoticeType.from_rw(row.get("RetractionNature", ""))
    if notice_type is None:
        return None
    return Notice(
        record_id=row["Record ID"].strip(),
        notice_type=notice_type,
        notice_date=parse_rw_date(row.get("RetractionDate", "")),
        doi=normalize_doi(row.get("OriginalPaperDOI")),
        pmid=normalize_pmid(row.get("OriginalPaperPubMedID")),
        title=row.get("Title", "").strip(),
        journal=row.get("Journal", "").strip(),
        authors=row.get("Author", "").strip(),
        original_date=parse_rw_date(row.get("OriginalPaperDate", "")),
        notice_doi=normalize_doi(row.get("RetractionDOI")),
        reasons=tuple(r.lstrip("+") for r in _split(row.get("Reason", ""))),
    )


def read_rw_csv(path: Path) -> Iterator[Notice]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            notice = row_to_notice(row)
            if notice is not None:
                yield notice
