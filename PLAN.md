# TaintTrace: Retraction-Aware Scientific RAG

A literature question-answering system that knows when its evidence has been retracted, and when its evidence *depends on* retracted work, and tells the user at the level of each claim.

---

## 1. The problem

AI research assistants retrieve retracted papers and present them as sound evidence. This is documented across several independent studies:

| Source | What it found |
|---|---|
| Labenbacher et al., *JMIR* 2026 ([PMC13134821](https://pmc.ncbi.nlm.nih.gov/articles/PMC13134821/)), read in full | 9 free GenAI tools, 15 retracted papers, 5 standard questions each, asked twice. **No tool** handled retractions consistently. Best: ChatGPT 5 at 8/15 fully correct. Research-focused tools (SciSpace, ScienceOS, Consensus) scored **0/15**. Retracted papers appeared in topic overviews without warning in up to 53% of cases (SciSpace). Repeat-query inconsistency up to 25% (Consensus). |
| Metze et al., *J Clin Anesth* 2025, via [Retraction Watch](https://retractionwatch.com/2025/11/19/ai-unreliable-identifying-retracted-research-papers-study/), read in full | 21 chatbots, 132 references (Boldt case). Fewer than half of retractions identified. ~18% false positives on Boldt's intact papers, ~4.5% on other authors'. Answers changed between runs and drifted into useless hedging ("worth double-checking"). |
| Thelwall et al., *Learned Publishing* 2025 (reported in the same article) | 217 retracted or flagged papers × 30 runs = 6,510 ChatGPT reports. **None** mentioned the retraction or concern. |
| Gu et al., *J Adv Res* 2025 | ChatGPT answered questions using retracted cancer-imaging studies without acknowledging retraction in >70% of prompts. |
| [Aaron Tay, June 2025](https://aarontay.substack.com/p/how-should-academic-retrieval-augmented-7f6), read in full | 8 academic RAG tools. With a high-profile retraction, some flagged it. With a **low-profile** retraction, almost all failed. Uploaded PDFs: even a bold "RETRACTED" stamp was missed by ChatGPT o3, Gemini 2.5 Pro and NotebookLM. Key observation: *a correctly flagged record at retrieval can still be summarized without the warning in the answer.* |

The JMIR authors explicitly call for "AI integrity layers" and note that handling "is more realistically achieved through post-generation checks rather than through changes to the underlying model itself." That is what this project builds.

## 2. What already exists, and the gap

| Existing | What it does | What it doesn't do |
|---|---|---|
| Crossref + Retraction Watch database (68k+ retractions, daily CSV) | Authoritative list of retracted DOIs | Nothing at answer time |
| OpenAlex `is_retracted` | Boolean per work, synced daily from RW, Crossref, PubMed | Collapses retraction / correction / concern into one flag; no reason, no date |
| Scite (paid) | Shows notices, Reference Check on manuscripts | Closed; not a RAG layer; doesn't propagate into generated answers |
| Scopus AI, Web of Science RA | Silently drop retracted papers | No transparency; useless when a retraction is the topic |
| Feet of Clay detector (Cabanac, PPS) | Flags 764k+ papers whose **bibliographies** contain retracted refs | Bibliography-level only: can't tell if the citing paper's *claim depends on* the retracted work or just mentions it |
| Retraction checks in reference managers (Zotero etc.) | Flags items in your library | Not in the generation path |

**The gap this project fills.** No open system does all of:

1. Check every retrieved paper **and every uploaded PDF** (DOI extraction) against live retraction data.
2. Carry that status through generation to **each claim** in the answer, deterministically, not by asking the LLM to remember.
3. Detect **second-order taint at claim level**: a non-retracted paper whose specific claim rests on retracted work (using citation contexts and intents), as opposed to merely citing it.
4. Abstain or downgrade when the only support for a claim is retracted or tainted.
5. Measure all of this on a **public benchmark** of low-profile retractions, which doesn't exist yet. Existing studies use 2 to 132 papers and are not reusable test suites.

## 3. Goals and non-goals

**Goals**
- G1. A RAG pipeline over scientific literature where no retracted paper reaches the answer unflagged.
- G2. Claim-level evidence labels: `supported`, `supported-by-retracted`, `supported-by-tainted`, `unsupported`.
- G3. RetractBench: a reusable benchmark (300+ retracted papers, matched controls, standard question templates) with a scorer.
- G4. Published results comparing plain RAG, filter-only RAG, and TaintTrace, with numbers.
- G5. Usable as a standalone **integrity layer** (Python library + MCP server) that other RAG systems can call.

**Non-goals**
- Judging whether a retraction was justified, or detecting fraud in non-retracted papers.
- Fine-tuning or retraining LLMs.
- Full coverage of every field on day one. Start biomedical (best metadata), expand later.

## 4. System architecture

```
                    ┌──────────────────────────────────────────────┐
                    │           Integrity store (daily sync)        │
                    │  Retraction Watch CSV · Crossref updates ·     │
                    │  OpenAlex is_retracted · PubPeer (optional)    │
                    └───────────────┬──────────────────────────────┘
                                    │ status lookups (DOI/PMID/title)
 question ─► Query planner ─► Hybrid retrieval ─► Integrity gate ─► Taint analyzer ─► Generator ─► Claim verifier ─► Answer
             (decompose,       (BM25 + dense +    (annotate every   (citation-context   (status-aware   (split claims,     + evidence
              detect "about     reranker over      chunk with paper  NLI: does this     prompt, cites   NLI each vs. its   table
              a retraction"     paper chunks)      status; policy:   chunk's claim      chunk ids)      chunks, attach     + badges
              intent)                              filter / flag)    rest on retracted                  status labels
                                                                     refs?)                             deterministically)
 uploaded PDF ─► DOI/title extraction ─► same integrity gate
```

### 4.1 Integrity store
- Daily ingest of the Retraction Watch CSV (`gitlab.com/crossref/retraction-watch-data`): DOI, PMID, title, retraction date, reason codes, notice type (retraction, expression of concern, correction, reinstatement).
- Join with OpenAlex `is_retracted` and Crossref `update-type:retraction` for papers RW lacks.
- Store in SQLite/Postgres with a fuzzy title index (for papers without DOIs, a known weak spot of OpenAlex).
- Keep **notice type** distinct. Collapsing to a boolean is exactly the flaw Hauschke et al. (2024) documented in OpenAlex.

### 4.2 Corpus and retrieval
- Corpus: PubMed Central open-access subset for a few chosen topics (start ~50k papers), chunked by section with section-type metadata (abstract, methods, results, discussion).
- Every chunk carries `paper_id`, `doi`, `section`, and the paper's citation list resolved to DOIs.
- Hybrid retrieval: BM25 + dense embeddings, reciprocal rank fusion, cross-encoder reranker.
- Deliberately **include** retracted papers in the index, as real-world corpora do. The gate decides what happens to them.

### 4.3 Integrity gate (deterministic, no LLM)
- Every retrieved chunk is annotated with its paper's status before the generator sees it.
- Policy modes:
  - `strict`: drop retracted chunks; tell the user N retracted sources were excluded and list them.
  - `flag` (default): keep them, but label them in the prompt and force the label into the output.
  - `historical`: when the question is *about* a retraction or a controversy, retracted sources are the point; still labelled.
- The query planner picks `historical` when the question names a retracted work or asks about controversy.

### 4.4 Taint analyzer (the research core)
For each retrieved chunk from a non-retracted paper:
1. Find in-text citations inside the chunk and resolve them to DOIs (reference list + Semantic Scholar `contexts`).
2. Check each cited DOI against the integrity store.
3. If a cited work is retracted, classify the dependency with an NLI / citation-intent model:
   - **load-bearing**: the chunk's claim depends on the retracted result ("Following X, we used…", "X showed that…, therefore…").
   - **background**: cited as context; claim stands without it.
   - **critical**: cited *because* it's retracted or disputed.
4. Load-bearing → chunk is `tainted`. Background or critical → no taint, but recorded.
5. Use publication dates: a citation made **before** the retraction is normal practice but still tainted; one made **after** is a stronger signal.

This is what makes the project more than a lookup table. Feet of Clay flags bibliographies; this flags specific claims.

### 4.5 Generation and claim verification
- Status-aware prompt: each chunk is shown with an id and a status tag.
- After generation: split the answer into atomic claims, map each claim to the chunk ids it cites, and verify support with an NLI model.
- **Labels are attached by code, not by the LLM**, from the chunk statuses. Tay's finding (a flagged record summarized without the warning) is solved structurally, not by prompting.
- If a claim's only support is retracted or tainted: rewrite as unsupported or remove, and say why.

### 4.6 PDF upload path
- Extract DOI from the PDF (first pages, metadata, Crossmark link); fall back to title matching.
- Check the integrity store before any summarization; warn first if retracted.
- Also scan the PDF's own reference list and report retracted references (Feet-of-Clay-style), with load-bearing analysis on the passages that cite them.

### 4.7 Interfaces
- Python library: `tainttrace.check(doi)`, `tainttrace.gate(chunks)`, `tainttrace.verify(answer, chunks)`.
- FastAPI service.
- MCP server so Claude Code, Claude Desktop and other agents can call the integrity layer.
- Small web UI (Streamlit or Next.js): answer with per-claim badges, evidence table, "excluded sources" panel.

## 5. RetractBench: the benchmark

Existing studies use 2 to 132 papers, mostly high-profile, mostly not reusable. RetractBench fixes that.

**Composition (v1)**
- 300 retracted papers sampled from the RW database, stratified by:
  - visibility: high (top-cited, RW leaderboard) vs. **low** (≤10 citations, single RW mention), because Tay showed low-profile is where tools fail
  - retraction age: <1 year, 1 to 5 years, >5 years
  - field: biomedical first, plus CS and social science subsets
  - reason: misconduct, error, duplication, paper mill
- 300 matched non-retracted controls (same journal and year, similar citation count) to measure false positives. Metze et al. found 18% false positives, so this matters.
- 150 "tainted" items: non-retracted papers whose claims load-bear on a retracted paper, hand-labelled with load-bearing / background / critical. This is the novel part and the most labour-intensive.
- Question templates adapted from the JMIR study's five (topic overview, relevant articles, summarize, is it retracted, why), plus claim-level questions for the taint set.

**Metrics**
- Unflagged retracted inclusion rate (topic overviews and summaries). Primary metric, comparable to the JMIR table.
- Retraction detection precision and recall; false-positive rate on controls.
- Taint detection precision and recall at claim level.
- Answer faithfulness (claim-level NLI support rate).
- Consistency across repeated runs (JMIR reports 2.7 to 25% disagreement).
- Answer usefulness: does strict mode destroy coverage? Measure answer completeness against a reference.

**Baselines**
1. Plain hybrid RAG on the same corpus, no integrity layer.
2. Filter-only RAG (drop `is_retracted` papers, the Scopus AI approach).
3. Prompt-only: tell the LLM to check for retractions (what most tools effectively do).
4. TaintTrace in `flag` and `strict` modes.
5. Optional: published numbers from the JMIR study for context (different setup, not directly comparable; say so).

## 6. Phased build plan

| Phase | Weeks | Deliverables | Done when |
|---|---|---|---|
| 0. Setup | 1 | Repo, env, CI, config; API keys for Semantic Scholar and an LLM | `make test` runs green |
| 1. Integrity store | 1 to 2 | RW CSV ingest, OpenAlex/Crossref join, DOI and fuzzy-title lookup, daily sync script, `check(doi)` | Correctly classifies all 15 JMIR papers and a 100-paper spot check |
| 2. Baseline RAG | 2 to 3 | PMC OA ingest (one topic area), section chunking, hybrid retrieval + reranker, cited generation | Answers a 30-question smoke set with valid citations |
| 3. Integrity gate + claim verifier | 3 to 4 | Status annotation, three policy modes, claim splitting, NLI support check, deterministic labels | No retracted chunk reaches an answer unlabelled on the smoke set |
| 4. RetractBench v1 | 4 to 6 | Sampling scripts, controls, question generation, scorer; first 300 + 300 items | Baselines 1 to 3 scored |
| 5. Taint analyzer | 6 to 8 | Citation-context extraction, dependency classifier (start: LLM-judge; then fine-tuned small NLI model), date logic | Taint set labelled; analyzer beats a "cites a retracted paper → tainted" baseline on precision |
| 6. PDF path | 8 | DOI extraction, pre-summary warning, reference-list scan | Tay's stamped and unstamped PDF cases both flagged |
| 7. Interfaces | 9 | FastAPI, MCP server, web UI with badges | Demo end to end from UI and from Claude Code |
| 8. Evaluation + write-up | 10 to 11 | Full benchmark runs, ablations, error analysis, README, arXiv-style report, public dataset release | Headline table with all baselines and confidence intervals |

About 11 weeks part-time for one person. Phases 1 to 4 alone already give a defensible project.

## 7. Tech stack

- Python 3.12, `uv` for env management
- Retrieval: `rank_bm25` or Tantivy for BM25; embeddings via an open model (e.g. bge / e5 family) or an API; Qdrant or pgvector; a cross-encoder reranker
- LLM: Claude via the Anthropic API (generation, claim splitting, LLM-judge labels)
- NLI / dependency classifier: an off-the-shelf NLI cross-encoder first, then a small fine-tuned model on the labelled taint set
- PDF: GROBID (references + structure) with a PyMuPDF fallback
- Data: Retraction Watch CSV (Crossref GitLab), OpenAlex API, Crossref REST API, Semantic Scholar Graph API (citation contexts and intents), PMC OA
- Service: FastAPI; MCP Python SDK; Streamlit for the first UI
- Storage: SQLite for the integrity store in dev, Postgres later
- Eval: pytest + a custom scorer; results as CSV and markdown tables

## 8. Repository layout

```
Rag_Project/
  PLAN.md
  README.md
  pyproject.toml
  src/tainttrace/
    integrity/      # RW/OpenAlex/Crossref ingest, store, lookup
    corpus/         # PMC ingest, chunking, reference resolution
    retrieval/      # BM25, dense, fusion, rerank
    gate/           # status annotation and policy modes
    taint/          # citation contexts, dependency classifier
    generate/       # prompts, generation
    verify/         # claim splitting, NLI, labelling
    pdf/            # DOI extraction, reference scan
    api/            # FastAPI app
    mcp/            # MCP server
  bench/
    build/          # sampling and question generation
    data/           # released benchmark files
    score.py
  ui/
  tests/
  scripts/          # daily sync, ingest jobs
```

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Retraction metadata is incomplete or inconsistent (OpenAlex boolean collapse; papers without DOIs) | Use RW as primary, keep notice types, fuzzy title match, report coverage gaps honestly |
| Semantic Scholar citation contexts are sparse (measured 4 to 84 per 100 records) | Parse full text from PMC OA directly with GROBID; S2 is a supplement |
| Load-bearing vs. background is subjective | Write a labelling guide, double-label a subset, report inter-annotator agreement (the JMIR authors used Cohen's κ) |
| Strict mode hides retracted work when the user needs it | Historical mode + always list exclusions |
| Benchmark leakage (LLMs memorized famous retractions) | Emphasize low-profile and recent retractions; report by stratum |
| Rate limits on public APIs | Cache aggressively; bulk snapshots (OpenAlex, RW CSV) instead of per-call APIs |
| Scope creep | Phases 1 to 4 are the minimum viable result; taint analysis is the stretch that makes it novel |

## 10. What "success" looks like

- Headline number: unflagged retracted inclusion drops from the baseline rate (expected 20 to 50%, consistent with the JMIR table) to near 0% in `flag` mode, with a false-positive rate on controls well below the ~18% Metze et al. measured for chatbots.
- Taint analyzer: measurably better precision than bibliography-level flagging on the hand-labelled set.
- Released: code, RetractBench v1, and a short report. The benchmark is useful to others even if they never use the system.

## 11. Open decisions

1. **First topic area for the corpus.** Recommendation: a biomedical area with many retractions and good PMC OA coverage (e.g. oncology or COVID-19 research).
2. **Embedding and reranker models:** open-source local vs. API. Recommendation: open-source, so the benchmark is reproducible.
3. **Labelling help** for the taint set: solo, or recruit a domain-knowledgeable second annotator for agreement scores.

## 12. Sources

- Labenbacher S, Niederer M, Hammer S, et al. Performance of AI Tools in Citing Retracted Literature: Content Analysis. *J Med Internet Res* 2026;28:e88766. https://pmc.ncbi.nlm.nih.gov/articles/PMC13134821/
- Retraction Watch, "AI unreliable in identifying retracted research papers, says study," 19 Nov 2025 (covers Metze et al., *J Clin Anesth* 2025, and Thelwall et al., *Learned Publishing* 2025). https://retractionwatch.com/2025/11/19/ai-unreliable-identifying-retracted-research-papers-study/
- Tay A. How should academic RAG systems handle retracted works? (II), June 2025. https://aarontay.substack.com/p/how-should-academic-retrieval-augmented-7f6
- Gu T, et al. Alarm: retracted articles on cancer imaging… used by ChatGPT. *J Adv Res* 2025;71:1–3.
- Cabanac G, et al. Problematic Paper Screener / Feet of Clay detector. https://www.irit.fr/~Guillaume.Cabanac/problematic-paper-screener/
- Hauschke C, et al. Retracted papers in OpenAlex, 2024. https://arxiv.org/pdf/2403.13339
- Crossref, Retraction Watch data access. https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/
- OpenAlex `is_retracted` attribute. https://help.openalex.org/data/works/attributes/
- Semantic Scholar Graph API (citation contexts and intents). https://api.semanticscholar.org/api-docs/
