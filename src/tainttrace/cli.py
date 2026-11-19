"""Command line: `tainttrace sync | check | info`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .integrity import NoticeStore, default_db_path, open_checker, sync
from .integrity.models import CheckResult


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

    args = parser.parse_args(argv)

    if args.cmd == "sync":
        count = sync(csv_path=args.csv, db_path=args.db, download=not args.no_download)
        print(f"Loaded {count} notices into {args.db or default_db_path()}")
        return 0

    if args.cmd == "info":
        for key, value in NoticeStore(args.db).meta().items():
            print(f"{key}: {value}")
        return 0

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


if __name__ == "__main__":
    raise SystemExit(main())
