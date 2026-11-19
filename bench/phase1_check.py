"""Phase 1 acceptance check for the integrity store.

Four probes, each written to bench/results/phase1.md:

1. The 15 retracted papers from Labenbacher et al. (JMIR 2026), by DOI and
   by title alone.
2. Retracted papers sampled from the store, looked up by perturbed title
   only (tests fuzzy matching, not the DOI index).
3. Non-retracted papers sampled from OpenAlex, by DOI and by title
   (false-positive rate).
4. Recent publisher retractions registered with Crossref but absent from
   the local Retraction Watch file (tests the live fallback).

Usage: uv run python bench/phase1_check.py [--sample 50] [--seed 7]
"""

from __future__ import annotations

import argparse
import csv
import random
import time
from collections import Counter
from pathlib import Path

import httpx

from tainttrace.integrity import IntegrityChecker, NoticeStore, RemoteSources, Status

HERE = Path(__file__).parent
FLAGGED = {Status.RETRACTED, Status.EXPRESSION_OF_CONCERN}


def perturb(title: str, rng: random.Random) -> str:
    """Make a title look like it came from a PDF or reference list."""
    variants = [
        f"RETRACTED: {title}",
        title.upper(),
        title.rstrip(".") + ".",
        title.replace(":", " -"),
        title.lower(),
    ]
    return rng.choice(variants)


def jmir(offline: IntegrityChecker) -> tuple[list[str], int, int]:
    rows = list(csv.DictReader((HERE / "data" / "jmir2026_retracted.csv").open()))
    lines = ["| Ref | DOI | By DOI | By title only | Title score |", "|---|---|---|---|---|"]
    ok_doi = ok_title = 0
    for r in rows:
        by_doi = offline.check(doi=r["doi"])
        by_title = offline.check(title=r["title"])
        ok_doi += by_doi.status is Status.RETRACTED
        ok_title += by_title.status is Status.RETRACTED
        score = f"{by_title.title_score:.0f}" if by_title.title_score else "-"
        lines.append(
            f"| {r['ref']} | {r['doi']} | {by_doi.status.value} | {by_title.status.value} | {score} |"
        )
    return lines, ok_doi, ok_title


def get(client: httpx.Client, url: str, params: dict, tries: int = 5) -> httpx.Response | None:
    """GET with backoff on rate limits and server errors; None if it never succeeds."""
    for attempt in range(tries):
        try:
            resp = client.get(url, params=params)
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code == 200:
            return resp
        if resp is not None and resp.status_code == 429 and float(
            resp.headers.get("x-ratelimit-remaining-usd", "1")
        ) < 0.001:
            # OpenAlex daily budget spent; retrying won't help until it resets.
            return None
        time.sleep(2**attempt)
    return None


def sample_retracted(store: NoticeStore, n: int, rng: random.Random) -> list[tuple[str, str]]:
    """Seeded sample of (doi, title) for retracted papers with usable titles."""
    rows = store.conn.execute(
        "SELECT doi, title FROM notices WHERE notice_type = 'retraction' AND doi IS NOT NULL "
        "AND length(title) > 40 ORDER BY record_id"
    ).fetchall()
    return [tuple(r) for r in rng.sample(rows, n)]


def sampled_titles(store: NoticeStore, offline: IntegrityChecker, n: int, rng: random.Random):
    rows = sample_retracted(store, n, rng)
    misses = []
    hit = 0
    for _, title in rows:
        query = perturb(title, rng)
        result = offline.check(title=query)
        if result.status in FLAGGED:
            hit += 1
        else:
            misses.append((query, result.title_score))
    return hit, len(rows), misses


def openalex_controls(client: httpx.Client, n: int, seed: int) -> list[dict]:
    resp = client.get(
        "https://api.openalex.org/works",
        params={
            "filter": "is_retracted:false,has_doi:true,type:article,from_publication_date:2000-01-01",
            "sample": n,
            "seed": seed,
            "per_page": n,
            "select": "doi,title",
        },
    )
    resp.raise_for_status()
    return [w for w in resp.json()["results"] if w.get("doi") and w.get("title")]


def controls(online: IntegrityChecker, offline: IntegrityChecker, works: list[dict]):
    fp_doi = Counter()
    fp_title = []
    for w in works:
        fp_doi[online.check(doi=w["doi"]).status] += 1
        by_title = offline.check(title=w["title"])
        if by_title.status in FLAGGED:
            matched = by_title.notices[0].title
            fp_title.append((w["title"], matched, by_title.title_score))
    return fp_doi, fp_title


def lookalikes(
    client: httpx.Client, store: NoticeStore, offline: IntegrityChecker, n: int, rng: random.Random
):
    """Non-retracted papers whose titles resemble a retracted paper's title.

    For each sampled retracted title, ask OpenAlex for the closest title
    matches and keep those with a different DOI that OpenAlex does not mark
    retracted. These are the papers fuzzy matching is most likely to confuse.
    """
    rows = sample_retracted(store, n, rng)
    tested, notices, failed, flagged = 0, 0, 0, []
    for doi, title in rows:
        resp = get(
            client,
            "https://api.openalex.org/works",
            {
                "search": title[:200],
                "filter": "is_retracted:false,has_doi:true",
                "per_page": 3,
                "select": "doi,title",
            },
        )
        if resp is None:
            failed += 1
            continue
        for w in resp.json()["results"]:
            other = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
            if not w.get("title") or other == doi or store.by_doi(other):
                continue
            # OpenAlex indexes retraction notices as works of their own.
            # Matching a notice to the paper it retracts is correct.
            if store.by_notice_doi(other) or w["title"].lower().lstrip().startswith(
                ("retracted", "retraction", "withdrawn")
            ):
                notices += 1
                break
            tested += 1
            result = offline.check(title=w["title"])
            if result.status in FLAGGED:
                flagged.append((w["title"], other, result.notices[0].title, result.title_score))
            break
    return tested, notices, failed, flagged


def recent_publisher_retractions(client: httpx.Client, store: NoticeStore, n: int) -> list[str]:
    resp = client.get(
        "https://api.crossref.org/works",
        params={"filter": "update-type:retraction", "rows": 200, "sort": "created", "order": "desc"},
    )
    resp.raise_for_status()
    missing: list[str] = []
    for item in resp.json()["message"]["items"]:
        for upd in item.get("update-to") or []:
            doi = upd.get("DOI", "").lower()
            if upd.get("type") == "retraction" and doi and not store.by_doi(doi) and doi not in missing:
                missing.append(doi)
    return missing[:n]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    store = NoticeStore()
    client = httpx.Client(timeout=60.0, follow_redirects=True)
    offline = IntegrityChecker(store)
    online = IntegrityChecker(store, RemoteSources(store))
    meta = store.meta()

    out = [
        "# Phase 1 acceptance check: integrity store",
        "",
        f"Store built {meta['built_at']} from {meta['notice_count']} Retraction Watch notices "
        f"(sha256 `{meta.get('source_sha256', '?')[:12]}`). Sample size {args.sample}, seed {args.seed}. "
        "Probes 1 and 2 are deterministic for a given store and seed; probes 3, 3b and 4 "
        "query live APIs and vary between runs.",
        "",
    ]

    lines, ok_doi, ok_title = jmir(offline)
    out += [
        "## 1. JMIR 2026 study papers (Labenbacher et al.)",
        "",
        f"Offline, Retraction Watch only. **By DOI: {ok_doi}/15 retracted. By title only: {ok_title}/15.**",
        "",
        *lines,
        "",
    ]

    hit, total, misses = sampled_titles(store, offline, args.sample, rng)
    out += [
        "## 2. Retracted papers looked up by perturbed title only",
        "",
        f"Offline. **Found {hit}/{total} ({hit / total:.0%}).** Titles were given a random "
        "perturbation: a `RETRACTED:` prefix, all caps, all lowercase, a trailing period, "
        "or colons replaced by dashes.",
        "",
    ]
    if misses:
        out += ["Misses:", ""] + [
            f"- `{q}` (best score {s:.0f})" if s else f"- `{q}` (no candidate)" for q, s in misses
        ]
        out.append("")

    works = openalex_controls(client, args.sample, args.seed)
    fp_doi, fp_title = controls(online, offline, works)
    flagged = sum(v for k, v in fp_doi.items() if k in FLAGGED)
    out += [
        "## 3. Non-retracted controls (random OpenAlex articles)",
        "",
        f"{len(works)} articles that OpenAlex marks `is_retracted: false`.",
        "",
        f"- **By DOI, with live fallbacks: {flagged}/{len(works)} flagged** "
        f"(statuses: {', '.join(f'{k.value} {v}' for k, v in fp_doi.most_common())}). "
        "A flag here is not automatically wrong: Crossref may know of a notice OpenAlex lacks.",
        f"- **By title only, offline: {len(fp_title)}/{len(works)} flagged.**",
        "",
    ]
    for query, matched, score in fp_title:
        out.append(f"  - `{query}` matched `{matched}` (score {score:.0f})")
    out.append("")

    tested, notice_hits, failed, flagged_like = lookalikes(client, store, offline, args.sample, rng)
    out += [
        "## 3b. Look-alike titles (hard false-positive probe)",
        "",
        "For each sampled retracted paper, the closest OpenAlex title match with a different "
        "DOI that OpenAlex does not mark retracted. Checked by title only, offline.",
        "",
        f"- {failed}/{args.sample} OpenAlex searches failed after retries and were skipped.",
        f"- {notice_hits} of those matches were retraction notices (\"Retracted: ...\"), "
        "which OpenAlex indexes as separate works. Matching a notice to its paper is correct, "
        "so they are excluded.",
        f"- **Flagged: {len(flagged_like)}/{tested} remaining look-alikes.** Each is a "
        "same-title paper with a different DOI: an accidental duplicate, a reissued version, "
        "or a correction that reuses the title. Title-only matches are therefore reported "
        "with `certain = False`; a DOI lookup never confuses them.",
        "",
    ]
    for query, other, matched, score in flagged_like:
        out.append(f"  - `{query}` ({other}) matched `{matched}` (score {score:.0f})")
    out.append("")

    recent = recent_publisher_retractions(client, store, args.sample)
    caught = sum(online.check(doi=d).status is Status.RETRACTED for d in recent)
    caught_offline = sum(offline.check(doi=d).status is Status.RETRACTED for d in recent)
    out += [
        "## 4. Recent publisher retractions missing from the local file",
        "",
        f"{len(recent)} papers whose retraction is registered with Crossref but not in the "
        "downloaded Retraction Watch CSV.",
        "",
        f"- **Offline (Retraction Watch only): {caught_offline}/{len(recent)} caught.**",
        f"- **With Crossref and OpenAlex fallbacks: {caught}/{len(recent)} caught.**",
        "",
    ]

    dest = HERE / "results" / "phase1.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
