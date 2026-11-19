"""Command line: `tainttrace sync | check | info`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .integrity import NoticeStore, default_db_path, open_checker, sync
from .integrity.models import CheckResult

if TYPE_CHECKING:
    from .pipeline import RagResult


def _result_json(result: CheckResult) -> dict:
    data = asdict(result)
    data["retraction_date"] = result.retraction_date
    data["certain"] = result.certain
    data["reasons"] = list(result.reasons)
    return json.loads(json.dumps(data, default=str))


def _print_result(result: CheckResult) -> None:
    print(f"status:     {result.status.value}")
    print(f"matched by: {result.matched_by or '-'}", end="")
    if result.title_score is not None:
        print(f" (title similarity {result.title_score:.1f})", end="")
    print()
    if result.matched_by == "title":
        print("note:       title-only match; give a DOI to confirm it is the same paper")
    if result.is_notice_itself:
        print("note:       the DOI given is a notice, shown for the paper it concerns")
    print(f"sources:    {', '.join(result.sources) or '-'}")
    for n in sorted(result.notices, key=lambda n: str(n.notice_date)):
        when = n.notice_date.isoformat() if n.notice_date else "undated"
        print(f"  - {n.notice_type.value:<22} {when:<10}  [{n.source}]")
    if result.notices:
        print(f"title:      {result.notices[0].title}")
    if result.reasons:
        print(f"reasons:    {'; '.join(result.reasons)}")
    for err in result.errors:
        print(f"warning:    could not reach {err}", file=sys.stderr)


def _print_rag(result: RagResult) -> None:
    from .generate.answer import citation_problems, source_for

    print(f"Retrieved {len(result.retrieved)} passages:")
    for i, r in enumerate(result.retrieved, 1):
        score = f"{r.rerank_score:6.2f}" if r.rerank_score is not None else "     -"
        print(f"  [{i}] {score}  {r.chunk.title[:90]}")
        print(f"            {r.chunk.section[:60]} | {source_for(r)}")
    answer = result.answer
    if answer is None:
        return
    print()
    if answer.refused:
        print("Claude declined to answer this question.")
        return
    for seg in answer.segments:
        marks = sorted({c.result_index + 1 for c in seg.citations})
        print(seg.text + ("".join(f"[{m}]" for m in marks) if marks else ""), end="")
    print(f"\n\n(model {answer.model}, {answer.input_tokens} in / {answer.output_tokens} out tokens)")
    for problem in citation_problems(answer, result.retrieved):
        print(f"citation problem: {problem}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tainttrace")
    parser.add_argument("--db", type=Path, default=None, help="store path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="download Retraction Watch data and rebuild")
    p_sync.add_argument("--csv", type=Path, help="use this CSV instead of the default path")
    p_sync.add_argument("--no-download", action="store_true", help="rebuild from the CSV on disk")

    p_check = sub.add_parser("check", help="integrity status of one paper")
    p_check.add_argument("--doi")
    p_check.add_argument("--pmid")
    p_check.add_argument("--title")
    p_check.add_argument("--offline", action="store_true", help="skip Crossref/OpenAlex")
    p_check.add_argument("--json", action="store_true")

    sub.add_parser("info", help="show store metadata")

    p_corpus = sub.add_parser("corpus", help="build or inspect the paper corpus")
    corpus_sub = p_corpus.add_subparsers(dest="corpus_cmd", required=True)
    p_build = corpus_sub.add_parser("build", help="download and index papers")
    p_build.add_argument("--topic", default="mirna-cancer")
    p_build.add_argument("--n-topic", type=int, default=2000)
    p_build.add_argument("--n-retracted", type=int, default=500)
    corpus_sub.add_parser("embed", help="compute dense embeddings for all chunks")
    corpus_sub.add_parser("stats", help="show corpus counts")

    p_ask = sub.add_parser("ask", help="answer a question from the corpus")
    p_ask.add_argument("question")
    p_ask.add_argument("-k", type=int, default=8, help="passages to retrieve")
    p_ask.add_argument("--retrieve-only", action="store_true", help="skip answer generation")

    args = parser.parse_args(argv)

    if args.cmd == "sync":
        count = sync(csv_path=args.csv, db_path=args.db, download=not args.no_download)
        print(f"Loaded {count} notices into {args.db or default_db_path()}")
        return 0

    if args.cmd == "info":
        for key, value in NoticeStore(args.db).meta().items():
            print(f"{key}: {value}")
        return 0

    if args.cmd == "corpus":
        return _corpus(args)
    if args.cmd == "ask":
        return _ask(args)

    checker = open_checker(args.db, remote=not args.offline)
    try:
        result = checker.check(doi=args.doi, pmid=args.pmid, title=args.title)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(_result_json(result), indent=2))
    else:
        _print_result(result)
    return 0


def _corpus(args: argparse.Namespace) -> int:
    import logging

    from .corpus.store import CorpusStore, default_corpus_dir

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.corpus_cmd == "build":
        from .corpus.build import BuildPlan, build_corpus

        plan = BuildPlan(topic=args.topic, n_topic=args.n_topic, n_retracted=args.n_retracted)
        print(json.dumps(build_corpus(plan), indent=2))
    elif args.corpus_cmd == "embed":
        from .retrieval.dense import DEFAULT_EMBED_MODEL, DenseIndex, FastEmbedder

        index = DenseIndex.build(CorpusStore(), FastEmbedder(batch_size=128), DEFAULT_EMBED_MODEL)
        index.save(default_corpus_dir() / "dense.npz")
        print(f"Embedded {len(index.ids)} chunks")
    else:
        print(json.dumps(CorpusStore().stats(), indent=2))
    return 0


def _ask(args: argparse.Namespace) -> int:
    from .generate.answer import AnswerGenerator, has_claude_credentials
    from .pipeline import RagPipeline, load_retriever

    generator = None
    if not args.retrieve_only:
        if not has_claude_credentials():
            print("No Claude credentials found (set ANTHROPIC_API_KEY); showing retrieval only.\n",
                  file=sys.stderr)
        else:
            generator = AnswerGenerator()
    _print_rag(RagPipeline(load_retriever(), generator).ask(args.question, args.k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
