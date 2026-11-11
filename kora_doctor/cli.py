import argparse
import json
import sys
from importlib import resources

from . import __version__
from .analyzer import analyze
from .otel import enrich_with_otel
from .parser import InputError, load_records
from .render import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kora-doctor",
        description="Find execution waste in AI agent runs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    audit = sub.add_parser("audit", help="Analyze an AUDR JSON/JSONL file")
    audit.add_argument("input", help="Path to AUDR JSON, JSON array, or JSONL")
    audit.add_argument(
        "--otel",
        help="Optional OTLP/OTel JSON sidecar used to enrich AUDR records with execution evidence",
    )
    audit.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    audit.add_argument("--top", type=int, default=8, help="Number of findings to show (default: 8)")

    demo = sub.add_parser("demo", help="Try a bundled synthetic trace without your own data")
    demo.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    demo.add_argument("--top", type=int, default=8, help="Number of findings to show (default: 8)")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command not in ("audit", "demo"):
        parser.print_help()
        return 0

    try:
        otel_stats = None
        if args.command == "demo":
            # The bundled fixture is artificial; no network or private trace is needed.
            fixture = resources.files("kora_doctor").joinpath("data", "demo.jsonl")
            with resources.as_file(fixture) as fixture_path:
                records = load_records(str(fixture_path))
        else:
            records = load_records(args.input)
            if args.otel:
                records, otel_stats = enrich_with_otel(records, args.otel)
        report = analyze(records)
        if args.command == "demo":
            report.warnings.insert(0, "SYNTHETIC DEMO: bundled fixture only; no real execution, measured savings, or safe call deletion established.")
        if otel_stats is not None:
            report.warnings.append(
                "OTel enrichment: "
                f"{otel_stats['matched']}/{otel_stats['spans']} spans matched; "
                f"{otel_stats['args_hashed']} arg fingerprint(s); "
                f"{otel_stats['statuses']} status signal(s); "
                f"{otel_stats['inferred_retries']} retry link(s) inferred."
            )
            if otel_stats["conflicts"]:
                report.warnings.append(
                    f"OTel source conflict: {otel_stats['conflicts']} record(s) skipped; "
                    "original AUDR labels preserved."
                )
    except (InputError, OSError) as exc:
        print(f"kora-doctor: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        if args.command == "demo":
            print("SYNTHETIC DEMO — example data, not live measurements.\n")
        print(render_text(report, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
