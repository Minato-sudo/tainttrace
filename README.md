# TaintTrace

Retraction-aware retrieval for scientific question answering. See [PLAN.md](PLAN.md) for the full project plan.

**Status:**
- **Phase 1 (integrity store): complete.** Given a DOI, PMID or title, it reports whether a paper has been retracted, carries an expression of concern, was corrected, or was reinstated.
- **Phase 2 (baseline RAG): complete.** Answers questions about non-coding RNAs in cancer from 2,500 open-access papers, with every statement cited to an exact paragraph. Deliberately unaware of retractions; that's Phase 3.

## Setup

```bash
uv sync --extra gpu           # NVIDIA GPU (CUDA 13 driver); use --extra cpu otherwise
uv run tainttrace sync        # downloads Retraction Watch data (~68 MB) and builds data/integrity.sqlite
uv run tainttrace corpus build   # ~2,500 papers from Europe PMC / NCBI, ~10 minutes
uv run tainttrace corpus embed   # dense embeddings; ~5 minutes on GPU, hours on a busy CPU
export ANTHROPIC_API_KEY=...     # for answer generation
uv run tainttrace ask "Does circHIPK3 promote colorectal cancer metastasis?"
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

## Phase 2: the baseline RAG pipeline

| Stage | What it does |
|---|---|
| Corpus | 2,000 most-cited open-access papers on microRNAs, lncRNAs and circRNAs in cancer (2015–2024), plus the 500 most-cited ones Europe PMC tags as retracted. This field was chosen because paper mills hit it hard, so retracted work competes for retrieval as it does in reality. Papers are found through Europe PMC search and downloaded as JATS XML from NCBI in batches (Europe PMC's own full-text endpoint took ~20 s per paper). |
| Parsing | Sections and paragraphs, with tables, figures and back matter (acknowledgments, funding, etc.) dropped. Every paragraph keeps the reference ids it cites, resolved to DOIs where available (97% of references), for Phase 5's taint analysis. |
| Chunks | Consecutive paragraphs within a section, up to ~220 words. 85,754 chunks. |
| Retrieval | BM25 (SQLite FTS5) and dense (`bge-small-en-v1.5`) each return 50 candidates, fused with reciprocal rank fusion; the top 40 are reranked with `bge-reranker-base`; at most 2 passages per paper; top 8 returned. Runs on the GPU via ONNX Runtime when available. |
| Generation | `claude-opus-5-5`, with each passage sent as a `search_result` block (one text block per paragraph), so Claude's citations point at exact paragraphs. Every citation is checked against what was actually sent. Server-side refusal fallbacks are on (`fallbacks: "default"`). |

### Phase 2 results

From `uv run python bench/phase2_smoke.py` (full report: [bench/results/phase2_smoke.md](bench/results/phase2_smoke.md)), 30 questions, 8 passages each:

- Retrieval: median 0.78 s per question on an RTX 5050 laptop GPU.
- **19 of 30 questions retrieved at least one passage from a retracted paper** (or one under an expression of concern); 37 of 237 passages (16%).
- 506 of the corpus's 2,500 papers (20%) are flagged, by design, so these rates reflect the corpus mix rather than the literature at large. The point is that plain retrieval surfaces retracted papers about as readily as sound ones.
- Of the 500 papers Europe PMC tags as retracted, 375 are in the Retraction Watch file and **the other 125 were confirmed only by the live Crossref/OpenAlex fallbacks**, another sign the file lags.
- **Not yet verified:** answer generation and the citation check were tested only against a fake client; running them needs `ANTHROPIC_API_KEY`.

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

- **Title-only matches can hit the wrong copy.** Journals sometimes publish the same title twice (an accidental duplicate, a reissued version, a ChemInform abstract), and only one copy is retracted. Across three look-alike probe runs, excluding retraction notices, 9 of ~44 same-title papers with different DOIs (3/~24, 5/9 and 1/11) were matched to a retracted record. A known case: *Acta Tropica* retracted an accidental duplicate (`10.1016/j.actatropica.2013.10.010`); the legitimate original (`.007`) matches by title. Title matches are therefore reported with `certain = False`. DOI lookups are not affected.
- **OpenAlex daily budget.** Keyless OpenAlex access has a small daily budget ($0.10). Single-DOI lookups are free, but searches cost credits: one full run of `bench/phase1_check.py` uses most of a day's budget. Set `TAINTTRACE_OPENALEX_KEY` for heavier use.
- **Coverage** is only as good as Retraction Watch, Crossref and OpenAlex. Retractions with no DOI are found only by PMID or title.
