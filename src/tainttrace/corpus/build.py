"""Build the topic corpus: search Europe PMC, download, parse, chunk, store.

The corpus deliberately mixes ordinary papers with papers Europe PMC tags as
retracted, as real literature collections do. Whether a paper is retracted
is decided later by the integrity store, not by this tag.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .chunk import chunk_paper
from .europepmc import EuropePMC, Hit
from .ncbi import NcbiPmc
from .jats import parse_jats
from .store import CorpusStore, default_corpus_dir

log = logging.getLogger(__name__)

TOPICS = {
    "mirna-cancer": (
        '(TITLE:(microRNA OR miRNA OR "miR" OR lncRNA OR "long non-coding RNA" OR circRNA) '
        "AND TITLE_ABS:(cancer OR tumor OR tumour OR carcinoma)) AND OPEN_ACCESS:y AND IN_EPMC:y"
    ),
}
RETRACTED_FILTER = ' AND PUB_TYPE:"retracted publication"'


@dataclass
class BuildPlan:
    topic: str = "mirna-cancer"
    n_topic: int = 2000
    n_retracted: int = 500
    years: tuple[int, int] = (2015, 2024)
    batch_size: int = 50


def select_hits(plan: BuildPlan, epmc: EuropePMC) -> list[tuple[Hit, str, bool]]:
    """Most-cited papers in the topic, plus most-cited retracted-tagged ones."""
    base = TOPICS[plan.topic]
    lo, hi = plan.years
    regular = epmc.search(
        f"{base} AND PUB_YEAR:[{lo} TO {hi}] NOT PUB_TYPE:\"retracted publication\"",
        plan.n_topic, sort="CITED desc",
    )
    retracted = epmc.search(base + RETRACTED_FILTER, plan.n_retracted, sort="CITED desc")
    out: dict[str, tuple[Hit, str, bool]] = {}
    for hit in regular:
        out.setdefault(hit.pmcid, (hit, plan.topic, False))
    for hit in retracted:
        out[hit.pmcid] = (hit, f"{plan.topic}/retracted-tag", True)
    return list(out.values())


def build_corpus(plan: BuildPlan, corpus_dir: Path | None = None) -> dict[str, int]:
    corpus_dir = corpus_dir or default_corpus_dir()
    xml_dir = corpus_dir / "xml"
    store = CorpusStore(corpus_dir / "corpus.sqlite")
    selected = select_hits(plan, EuropePMC())
    todo = [s for s in selected if not store.has_paper(s[0].pmcid)]
    log.info("selected %d papers, %d not yet in the corpus", len(selected), len(todo))

    ncbi = NcbiPmc()
    added = skipped = 0
    for start in range(0, len(todo), plan.batch_size):
        batch = todo[start : start + plan.batch_size]
        paths = ncbi.fetch_batch([hit.pmcid for hit, _, _ in batch], xml_dir)
        for hit, origin, tagged in batch:
            path = paths.get(hit.pmcid)
            paper = parse_jats(path, hit.pmcid) if path else None
            chunks = chunk_paper(paper) if paper else []
            if not chunks:
                skipped += 1
                continue
            store.add_paper(paper, chunks, origin, tagged)
            added += 1
        log.info("%d/%d processed (%d added, %d skipped)", start + len(batch), len(todo), added, skipped)
    stats = store.stats() | {"added_now": added, "skipped_now": skipped}
    store.close()
    return stats
