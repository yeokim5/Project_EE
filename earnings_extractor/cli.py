"""Command-line entrypoints for the earnings extraction workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from earnings_extractor import _eval_bridge
from earnings_extractor.batch import run_batch
from earnings_extractor.export import export_reviewed_run
from earnings_extractor.pipeline import extract, inspect_draft
from earnings_extractor.review import write_review_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="earnings_extractor",
        description="Review-first earnings metric extraction workflow.",
    )
    subparsers = parser.add_subparsers(dest="command")

    batch_parser = subparsers.add_parser(
        "batch",
        help=(
            "Run a whole folder of PDFs into ONE Excel workbook. "
            "Per-PDF errors are skipped, not fatal."
        ),
    )
    batch_parser.add_argument(
        "input_path",
        type=Path,
        nargs="?",
        default=Path("pdf_input"),
        help="Folder of PDFs (default: ./pdf_input).",
    )
    batch_parser.add_argument(
        "--out",
        type=Path,
        default=Path("outputs/extractions.xlsx"),
        help="Output .xlsx path (default: outputs/extractions.xlsx).",
    )
    batch_parser.add_argument(
        "--mode",
        choices=("live", "recorded"),
        default="live",
        help="live needs OPENAI_API_KEY; recorded replays bundled samples.",
    )

    extract_parser = subparsers.add_parser("extract", help="Create draft metrics.")
    extract_parser.add_argument("input_path", type=Path)
    extract_parser.add_argument("--out", required=True, type=Path)
    extract_parser.add_argument("--mode", choices=("live", "recorded"), default="live")

    inspect_parser = subparsers.add_parser("inspect", help="Summarize a draft file.")
    inspect_parser.add_argument("draft_path", type=Path)

    eval_parser = subparsers.add_parser("eval", help="Score a draft file.")
    eval_parser.add_argument("--draft", required=True, type=Path)
    eval_parser.add_argument("--document-id", required=True)
    eval_parser.add_argument("--min-accuracy", type=float)

    review_parser = subparsers.add_parser(
        "review",
        help="Create human review artifacts for a draft run.",
    )
    review_parser.add_argument("run_dir", type=Path)
    review_parser.add_argument("--out", type=Path)
    review_parser.add_argument("--demo-decisions", type=Path)

    export_parser = subparsers.add_parser(
        "export",
        help="Create reviewed Excel/JSON/audit export artifacts.",
    )
    export_parser.add_argument("run_dir", type=Path)
    export_parser.add_argument("--decisions", required=True, type=Path)
    export_parser.add_argument("--out", required=True, type=Path)
    export_parser.add_argument("--allow-unreviewed", action="store_true")
    return parser


def _not_implemented(command: str) -> int:
    print(f"{command!r} is not implemented until a later phase.")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "batch":
            summary = run_batch(
                args.input_path,
                args.out,
                args.mode,
                progress=lambda message: print(message, flush=True),
            )
            print(summary.as_text(), end="")
            return 0
        if args.command == "extract":
            draft_path = extract(args.input_path, args.out, args.mode)
            print(f"Wrote {draft_path}")
            return 0
        if args.command == "inspect":
            print(inspect_draft(args.draft_path), end="")
            return 0
        if args.command == "eval":
            if args.min_accuracy is None:
                print(
                    _eval_bridge.run_eval(
                        draft_path=args.draft,
                        document_id=args.document_id,
                    ),
                    end="",
                )
                return 0
            if not 0.0 <= args.min_accuracy <= 1.0:
                print("--min-accuracy must be between 0.0 and 1.0.", file=sys.stderr)
                return 2
            report_text, accuracy = _eval_bridge.run_eval_with_accuracy(
                draft_path=args.draft,
                document_id=args.document_id,
            )
            print(report_text, end="")
            if accuracy < args.min_accuracy:
                print(
                    f"Accuracy {accuracy:.1%} below minimum "
                    f"{args.min_accuracy:.1%}.",
                    file=sys.stderr,
                )
                return 1
            return 0
        if args.command == "review":
            artifacts = write_review_artifacts(
                args.run_dir,
                html_out=args.out,
                demo_decisions_out=args.demo_decisions,
            )
            print(f"Wrote {artifacts.review_queue_path}")
            print(f"Wrote {artifacts.evidence_report_path}")
            print(f"Wrote {artifacts.review_html_path}")
            if artifacts.review_decisions_path is not None:
                print(f"Wrote {artifacts.review_decisions_path}")
            return 0
        if args.command == "export":
            artifacts = export_reviewed_run(
                args.run_dir,
                decisions_path=args.decisions,
                out_path=args.out,
                allow_unreviewed=args.allow_unreviewed,
            )
            print(f"Wrote {artifacts.xlsx_path}")
            print(f"Wrote {artifacts.json_path}")
            print(f"Wrote {artifacts.audit_report_path}")
            return 0
    except NotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command!r}")
    return 2
