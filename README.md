# TaintTrace

Retraction-aware retrieval for scientific question answering. See [PLAN.md](PLAN.md) for the full project plan.

**Status: Phase 1 (integrity store) complete.** Given a DOI, PMID or title, it reports whether a paper has been retracted, carries an expression of concern, was corrected, or was reinstated.

## Setup

```bash
uv sync
uv run tainttrace sync        # downloads Retraction Watch data (~68 MB) and builds data/integrity.sqlite
```

Optional environment variables:

| Variable | Purpose |
|---|---|
| `TAINTTRACE_DATA_DIR` | Where the store lives (default `data/`) |
| `TAINTTRACE_MAILTO` | Contact email sent to Crossref and OpenAlex (gets the polite pool) |
| `TAINTTRACE_OPENALEX_KEY` | OpenAlex API key. DOI lookups work without one; a key lifts the daily budget |

## Usage

```bash
uv run tainttrace check --doi 10.1038/nature04533
uv run tainttrace check --title "RETRACTED: Hydroxychloroquine and azithromycin as a treatment of COVID-19"
uv run tainttrace check --pmid 23432189 --json
uv run tainttrace check --doi 10.1155/2023/6455106 --offline   # Retraction Watch only
uv run tainttrace info
```

From Python:

```python
from tainttrace.integrity import open_checker, Status

checker = open_checker()                 # remote=False to stay offline
result = checker.check(doi="10.1056/NEJMoa1200303")
result.status          # Status.RETRACTED
result.certain         # True: matched by an exact identifier
result.retraction_date, result.reasons, result.sources, result.errors
```

Keep the data fresh with `scripts/daily_sync.sh` (a cron example is in the script).

## How it works

1. **Local store.** The [Retraction Watch database](https://gitlab.com/crossref/retraction-watch-data) (73,877 notices about 64,854 DOIs) is loaded into SQLite with indexes on DOI, PMID, notice DOI and an FTS5 title index. Rebuilds are atomic.
2. **Status from history.** A paper can have several notices (concern, then retraction, then reinstatement). The latest retraction or reinstatement decides; a later concern still counts. Notice types are kept distinct rather than collapsed into one boolean.
3. **Lookup order.** DOI, then the DOI of a notice (resolved to the paper it concerns), then PMID, then fuzzy title (score ≥ 90, exact match for titles under 4 words). A title is never matched onto a record carrying a different DOI.
4. **Live fallbacks.** When the local data has no retraction, Crossref's `updated-by` field and OpenAlex's `is_retracted` are checked by DOI. Answers are cached for 7 days. Unreachable sources are reported in `errors`, never silently treated as "fine".

`no_notice` means no source we checked records a notice. It does not certify that a paper is sound.

## Phase 1 results

From `uv run python bench/phase1_check.py` (full report: [bench/results/phase1.md](bench/results/phase1.md)):

| Probe | Result |
|---|---|
| 15 retracted papers from Labenbacher et al., JMIR 2026 | 15/15 by DOI, 15/15 by title only |
| 50 random retracted papers, perturbed title only | 50/50 |
| 50 random non-retracted OpenAlex articles | 0/50 flagged by DOI, 0/50 by title |
| 50 recent publisher retractions absent from the Retraction Watch file | 0/50 offline, **50/50 with live fallbacks** |

The last row matters: on 18 Nov 2026, the publishers' retractions of 321 papers, taken from Crossref's 300 most recent retraction notices, were not yet in the downloaded Retraction Watch file. Relying on that file alone misses fresh retractions.

## Known limitations

- **Title-only matches can hit the wrong copy.** Journals sometimes publish the same title twice (an accidental duplicate, a reissued version, a ChemInform abstract), and only one copy is retracted. In two look-alike probe runs, excluding retraction notices, 3 of ~24 and 5 of 9 same-title papers with different DOIs were matched to a retracted record. A known case: *Acta Tropica* retracted an accidental duplicate (`10.1016/j.actatropica.2013.10.010`); the legitimate original (`.007`) matches by title. Title matches are therefore reported with `certain = False`. DOI lookups are not affected.
- **OpenAlex daily budget.** Keyless OpenAlex access now has a small daily budget. Single-DOI lookups are free, but the look-alike probe uses search and could not complete on 18 Nov 2026 after the budget was spent; the committed report shows it as skipped.
- **Coverage** is only as good as Retraction Watch, Crossref and OpenAlex. Retractions with no DOI are found only by PMID or title.
