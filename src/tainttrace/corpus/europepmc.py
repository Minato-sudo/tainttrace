"""Europe PMC: search open-access papers and fetch their full-text XML."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"


@dataclass(frozen=True)
class Hit:
    pmcid: str
    doi: str | None
    pmid: str | None
    title: str
    year: int | None
    cited_by: int


def _hit(r: dict) -> Hit | None:
    if not r.get("pmcid"):
        return None
    year = r.get("pubYear")
    return Hit(
        pmcid=r["pmcid"],
        doi=(r.get("doi") or "").lower() or None,
        pmid=r.get("pmid"),
        title=r.get("title", ""),
        year=int(year) if year and year.isdigit() else None,
        cited_by=int(r.get("citedByCount") or 0),
    )


class EuropePMC:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 0.1):
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, url: str, params: dict | None = None, tries: int = 4) -> httpx.Response:
        for attempt in range(tries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.client.get(url, params=params)
            except httpx.TransportError:
                if attempt == tries - 1:
                    raise
            else:
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp
                if attempt == tries - 1:
                    resp.raise_for_status()
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def search(self, query: str, limit: int, sort: str | None = None) -> Iterator[Hit]:
        """Yield up to `limit` hits that have a PMC id, paging with cursorMark."""
        cursor, seen = "*", 0
        while seen < limit:
            params = {
                "query": query,
                "format": "json",
                "resultType": "lite",
                "pageSize": min(1000, limit - seen),
                "cursorMark": cursor,
            }
            if sort:
                params["sort"] = sort
            resp = self._get(SEARCH_URL, params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                return
            for r in results:
                hit = _hit(r)
                if hit is not None:
                    seen += 1
                    yield hit
                    if seen >= limit:
                        return
            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

    def fulltext(self, pmcid: str, cache_dir: Path) -> Path | None:
        """Download a paper's JATS XML into `cache_dir`; None if unavailable."""
        dest = cache_dir / f"{pmcid}.xml"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        resp = self._get(FULLTEXT_URL.format(pmcid=pmcid))
        if resp.status_code != 200 or not resp.content.lstrip().startswith(b"<"):
            return None
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return dest
