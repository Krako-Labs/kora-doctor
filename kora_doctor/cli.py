import argparse
import json
import sys

from . import __version__
from .analyzer import analyze
from .parser import InputError, load_records
from .render import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kora-doctor",
        description="Find the LLM calls your AI agent may never have needed.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    audit = sub.add_parser("audit", help="Analyze an AUDR JSON/JSONL file")
    audit.add_argument("input", help="Path to AUDR JSON, JSON array, or JSONL")
    audit.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    audit.add_argument("--top", type=int, default=8, help="Number of findings to show (default: 8)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "audit":
        parser.print_help()
        return 0

    try:
        records = load_records(args.input)
        report = analyze(records)
    except (InputError, OSError) as exc:
        print(f"kora-doctor: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
