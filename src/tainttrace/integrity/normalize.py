"""Identifier and title normalization shared by ingest and lookup."""

from __future__ import annotations

import re
import unicodedata

_DOI_PREFIXES = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|urn:doi:)", re.IGNORECASE
)
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
# Leading markers publishers and RW put on titles of retracted items.
_TITLE_MARKERS = re.compile(
    r"^\s*(?:retracted|retraction|withdrawn|expression of concern)"
    r"(?:\s+article)?\s*[:.\-–]\s*",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize_doi(value: str | None) -> str | None:
    """Return a lowercase bare DOI, or None if the value is not a DOI."""
    if not value:
        return None
    doi = _DOI_PREFIXES.sub("", value.strip()).strip().rstrip(".,;")
    doi = doi.lower()
    return doi if _DOI_SHAPE.match(doi) else None


def normalize_pmid(value: str | None) -> str | None:
    """Return a PMID as digits, or None for blanks and RW's "0" placeholder."""
    if not value:
        return None
    pmid = value.strip()
    if not pmid.isdigit() or int(pmid) == 0:
        return None
    return str(int(pmid))


def normalize_title(value: str | None) -> str:
    """Lowercase, strip retraction markers, accents and punctuation."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Markers can be stacked, e.g. "RETRACTED ARTICLE: Retraction: ...".
    while True:
        stripped = _TITLE_MARKERS.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _NON_WORD.sub(" ", text.lower())
    return _SPACES.sub(" ", text).strip()
