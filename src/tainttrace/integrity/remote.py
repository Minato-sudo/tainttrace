"""Live fallbacks: Crossref `updated-by` notices and OpenAlex `is_retracted`.

Both are consulted only for DOIs, results are cached in the store, and
cached answers expire so a paper retracted after the lookup is picked up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx

from .models import Notice, NoticeType
from .store import NoticeStore

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_URL = "https://api.openalex.org/works/doi:{doi}"

# Crossref update types that bear on reliability. Others (e.g. "addendum",
# "new_version") are ignored.
_CROSSREF_TYPES = {
    "retraction": NoticeType.RETRACTION,
    "withdrawal": NoticeType.RETRACTION,
    "removal": NoticeType.RETRACTION,
    "partial_retraction": NoticeType.CORRECTION,
    "expression_of_concern": NoticeType.EXPRESSION_OF_CONCERN,
    "correction": NoticeType.CORRECTION,
    "corrigendum": NoticeType.CORRECTION,
    "erratum": NoticeType.CORRECTION,
    "reinstatement": NoticeType.REINSTATEMENT,
}


@dataclass(frozen=True)
class RemoteAnswer:
    notices: tuple[Notice, ...]
    openalex_retracted: bool | None
    """None when OpenAlex could not be reached or does not know the DOI."""
    errors: tuple[str, ...]


def _crossref_date(update: dict) -> date | None:
    parts = (update.get("updated") or {}).get("date-parts") or [[]]
    try:
        y, m, d = (list(parts[0]) + [1, 1])[:3]
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def crossref_notices(doi: str, message: dict) -> list[Notice]:
    """Turn a Crossref work's `updated-by` list into notices."""
    title = (message.get("title") or [""])[0]
    journal = (message.get("container-title") or [""])[0]
    notices = []
    for i, update in enumerate(message.get("updated-by") or []):
        notice_type = _CROSSREF_TYPES.get(str(update.get("type", "")).lower())
        if notice_type is None:
            continue
        notices.append(
            Notice(
                record_id=f"crossref:{doi}:{i}",
                notice_type=notice_type,
                notice_date=_crossref_date(update),
                doi=doi,
                pmid=None,
                title=title,
                journal=journal,
                authors="",
                original_date=None,
                notice_doi=(update.get("DOI") or "").lower() or None,
                reasons=(),
                source=f"crossref/{update.get('source', 'unknown')}",
            )
        )
    return notices


class RemoteSources:
    def __init__(
        self,
        store: NoticeStore,
        client: httpx.Client | None = None,
        max_age: timedelta = timedelta(days=7),
        mailto: str | None = None,
    ):
        self.store = store
        self.max_age = max_age
        # OpenAlex gives keyless callers a small daily budget; DOI lookups
        # are free, but a key lifts the limit for heavy use.
        self.openalex_key = os.environ.get("TAINTTRACE_OPENALEX_KEY")
        mailto = mailto or os.environ.get("TAINTTRACE_MAILTO")
        agent = "tainttrace/0.1" + (f" (mailto:{mailto})" if mailto else "")
        self.client = client or httpx.Client(
            timeout=15.0, headers={"User-Agent": agent}, follow_redirects=True
        )

    def _cached(self, source: str, doi: str) -> dict | None:
        entry = self.store.cache_get(source, doi)
        if entry is None:
            return None
        fetched = datetime.fromisoformat(entry["fetched_at"])
        if datetime.now(UTC) - fetched > self.max_age:
            return None
        return entry

    def _fetch(self, source: str, url: str, doi: str) -> dict:
        """Return {"found": bool, "data": dict|None}; raises on network errors."""
        cached = self._cached(source, doi)
        if cached is not None:
            return cached
        resp = self.client.get(url)
        if resp.status_code == 404:
            entry = {"found": False, "data": None}
        else:
            resp.raise_for_status()
            entry = {"found": True, "data": resp.json()}
        entry["fetched_at"] = datetime.now(UTC).isoformat()
        self.store.cache_put(source, doi, entry)
        return entry

    def lookup(self, doi: str) -> RemoteAnswer:
        notices: list[Notice] = []
        openalex: bool | None = None
        errors: list[str] = []
        encoded = quote(doi, safe="/")

        try:
            entry = self._fetch("crossref", CROSSREF_URL.format(doi=encoded), doi)
            if entry["found"]:
                notices = crossref_notices(doi, entry["data"].get("message", {}))
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"crossref: {exc}")

        try:
            url = OPENALEX_URL.format(doi=encoded)
            if self.openalex_key:
                url += f"?api_key={self.openalex_key}"
            entry = self._fetch("openalex", url, doi)
            if entry["found"]:
                openalex = bool(entry["data"].get("is_retracted"))
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"openalex: {exc}")

        return RemoteAnswer(tuple(notices), openalex, tuple(errors))
