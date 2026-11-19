"""Phase 2 smoke test: the baseline RAG pipeline on 30 questions.

For each question: retrieve 8 passages, check every passage's paper against
the Phase 1 integrity store (measurement only; the baseline ignores it), and,
when Claude credentials are available, generate a cited answer and validate
every citation. Writes bench/results/phase2_smoke.md.

Usage: uv run python bench/phase2_smoke.py [--k 8] [--no-generate] [--limit N]
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

from tainttrace.generate.answer import AnswerGenerator, citation_problems, has_claude_credentials
from tainttrace.integrity import Status, open_checker
from tainttrace.pipeline import RagPipeline, load_retriever

HERE = Path(__file__).parent
FLAGGED = {Status.RETRACTED, Status.EXPRESSION_OF_CONCERN}


def load_questions() -> list[str]:
    lines = (HERE / "data" / "smoke_questions.txt").read_text().splitlines()
    return [q.strip() for q in lines if q.strip() and not q.startswith("#")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--no-generate", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    questions = load_questions()[: args.limit]
    generate = not args.no_generate and has_claude_credentials()
    retriever = load_retriever()
    pipeline = RagPipeline(retriever, AnswerGenerator() if generate else None)
    checker = open_checker()  # Retraction Watch, plus Crossref/OpenAlex for newer retractions
    store = retriever.store

    def status_of(paper_id: int) -> Status:
        row = store.conn.execute("SELECT doi, pmid FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        if not (row["doi"] or row["pmid"]):
            return Status.NO_NOTICE
        return checker.check(doi=row["doi"], pmid=row["pmid"]).status

    pipeline.ask("warm up the models", 1)
    rows, latencies = [], []
    totals = dict(passages=0, flagged_passages=0, questions_flagged=0, citations=0,
                  cited_flagged=0, answers_citing_flagged=0, problems=0, answered=0,
                  input_tokens=0, output_tokens=0)
    problem_lines: list[str] = []

    for n, question in enumerate(questions, 1):
        t = time.perf_counter()
        result = pipeline.ask(question, args.k)
        latencies.append(time.perf_counter() - t)

        statuses = [status_of(r.chunk.paper_id) for r in result.retrieved]
        flagged = [i for i, s in enumerate(statuses) if s in FLAGGED]
        totals["passages"] += len(statuses)
        totals["flagged_passages"] += len(flagged)
        totals["questions_flagged"] += bool(flagged)

        cited = cited_flag = problems = "-"
        if result.answer is not None:
            answer = result.answer
            totals["answered"] += not answer.refused
            totals["input_tokens"] += answer.input_tokens
            totals["output_tokens"] += answer.output_tokens
            indices = [c.result_index for c in answer.citations]
            bad = citation_problems(answer, result.retrieved)
            flagged_cites = [i for i in indices if i in flagged]
            totals["citations"] += len(indices)
            totals["cited_flagged"] += len(flagged_cites)
            totals["answers_citing_flagged"] += bool(flagged_cites)
            totals["problems"] += len(bad)
            problem_lines += [f"- Q{n}: {p}" for p in bad]
            cited, cited_flag, problems = len(indices), len(flagged_cites), len(bad)

        papers = len({r.chunk.paper_id for r in result.retrieved})
        rows.append(
            f"| {n} | {question} | {len(result.retrieved)} ({papers} papers) | {len(flagged)} | "
            f"{cited} | {cited_flag} | {problems} |"
        )
        print(f"[{n}/{len(questions)}] {latencies[-1]:.1f}s, {len(flagged)} flagged passages", flush=True)

    nq = len(questions)
    retracted_papers = sum(
        status_of(r[0]) in FLAGGED for r in store.conn.execute("SELECT paper_id FROM papers")
    )
    out = [
        "# Phase 2 smoke test: baseline RAG",
        "",
        f"{nq} questions on non-coding RNAs in cancer, k = {args.k} passages each, over the "
        f"{store.stats()['papers']:,}-paper / {store.stats()['chunks']:,}-chunk corpus. "
        "Hybrid retrieval (BM25 + bge-small-en-v1.5, reciprocal rank fusion, bge-reranker-base, "
        "at most 2 passages per paper).",
        "",
        "\"Flagged\" means the Phase 1 integrity checker (Retraction Watch, plus live Crossref and "
        "OpenAlex lookups) records the "
        "passage's paper as retracted or under an expression of concern. The baseline pipeline does "
        "not use this information; it is measured here to size the problem Phase 3 addresses.",
        "",
        "## Summary",
        "",
        f"- Retrieval latency: median {statistics.median(latencies):.2f}s, "
        f"max {max(latencies):.2f}s per question (GPU).",
        f"- **Questions retrieving at least one flagged passage: {totals['questions_flagged']}/{nq}.**",
        f"- Flagged passages: {totals['flagged_passages']}/{totals['passages']} "
        f"({totals['flagged_passages'] / max(totals['passages'], 1):.0%}).",
        f"- Context: {retracted_papers} of the corpus's {store.stats()['papers']:,} papers "
        f"({retracted_papers / store.stats()['papers']:.0%}) are flagged, because the corpus "
        "deliberately includes 500 papers Europe PMC tags as retracted. These rates reflect that "
        "mix, not the prevalence of retracted work in the literature at large; what they show is "
        "that the baseline retrieves flagged papers about as readily as sound ones.",
    ]
    if generate:
        out += [
            f"- Answers generated: {totals['answered']}/{nq} with `{pipeline.generator.model}`; "
            f"{totals['input_tokens']:,} input / {totals['output_tokens']:,} output tokens in total.",
            f"- Citations: {totals['citations']}; **citation problems: {totals['problems']}** "
            "(every citation must point at a paragraph that was actually retrieved, with matching text).",
            f"- **Answers citing a flagged paper: {totals['answers_citing_flagged']}/{nq}** "
            f"({totals['cited_flagged']} citations), with no warning, as expected from a baseline.",
        ]
    else:
        out.append(
            "- Answer generation **not run**: no Claude credentials were found. Set "
            "`ANTHROPIC_API_KEY` and rerun to complete the citation check."
        )
    out += [
        "",
        "## Per question",
        "",
        "| # | Question | Retrieved | Flagged | Citations | Citing flagged | Problems |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
    ]
    if problem_lines:
        out += ["## Citation problems", "", *problem_lines, ""]

    dest = HERE / "results" / "phase2_smoke.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text("\n".join(out))
    print("\n".join(out[:20]))


if __name__ == "__main__":
    main()
