"""Fetch PMC full-text JATS XML from NCBI E-utilities, many papers per request.

Europe PMC's per-paper full-text endpoint can take 20+ seconds per article;
NCBI's efetch returns the same JATS for a batch of ids in one call.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _article_pmcid(article: ET.Element) -> str | None:
    for aid in article.findall("./front/article-meta/article-id"):
        kind = aid.get("pub-id-type")
        value = (aid.text or "").strip()
        if kind in ("pmcid", "pmc") and value:
            return value if value.startswith("PMC") else f"PMC{value}"
    return None


class NcbiPmc:
    def __init__(self, client: httpx.Client | None = None, api_key: str | None = None):
        self.client = client or httpx.Client(timeout=120.0, follow_redirects=True)
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")
        # NCBI allows 3 requests/s without a key, 10 with one.
        self.min_interval = 0.12 if self.api_key else 0.4
        self._last = 0.0

    def _efetch(self, ids: list[str], tries: int = 4) -> bytes:
        params = {"db": "pmc", "retmode": "xml", "id": ",".join(i.removeprefix("PMC") for i in ids)}
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(tries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                # POST keeps long id lists out of the URL.
                resp = self.client.post(EFETCH_URL, data=params)
                if resp.status_code == 200:
                    return resp.content
            except httpx.TransportError:
                pass
            time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"efetch failed for {len(ids)} ids starting {ids[0]}")

    def fetch_batch(self, pmcids: list[str], cache_dir: Path) -> dict[str, Path]:
        """Download the given papers into `cache_dir`, one XML file each.

        Returns the paths for papers now on disk; ids NCBI did not return
        (no open-access full text) are absent.
        """
        cache_dir.mkdir(parents=True, exist_ok=True)
        found = {p: cache_dir / f"{p}.xml" for p in pmcids if (cache_dir / f"{p}.xml").exists()}
        missing = [p for p in pmcids if p not in found]
        if not missing:
            return found
        root = ET.fromstring(self._efetch(missing))
        for article in root.findall("article"):
            pmcid = _article_pmcid(article)
            if pmcid not in missing:
                continue
            dest = cache_dir / f"{pmcid}.xml"
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(ET.tostring(article, encoding="utf-8"))
            tmp.replace(dest)
            found[pmcid] = dest
        return found
