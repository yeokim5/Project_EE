"""Capture a real live LLM response into a reusable recorded cassette.

This makes keyless/offline mode and the eval gate reproducible. It runs the SAME
classification + page-selection + live extraction that the pipeline uses, then
writes the RAW model output (before any deterministic post-processing) to
``earnings_extractor/recorded_responses/<pdf_name>.json`` — which is exactly what
``--mode recorded`` reads back.

Requires a real OPENAI_API_KEY in .env (live call, costs tokens).

Usage:
    python scripts/record_cassette.py assesment_info/TSLA-Q2-2025-Update.pdf
    python scripts/record_cassette.py path/to/report.pdf --force
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from earnings_extractor.classify import classify_document, select_extraction_pages
from earnings_extractor.config import load_openai_config
from earnings_extractor.extractor import extract_metrics_live
from earnings_extractor.ingest import read_pdf_pages
from earnings_extractor.recorded import RECORDED_RESPONSES_DIR


def record(pdf_path: Path, force: bool) -> Path:
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Input must be a PDF: {pdf_path}")

    out_path = RECORDED_RESPONSES_DIR / f"{pdf_path.name}.json"
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists. Re-run with --force to overwrite."
        )

    config = load_openai_config()  # raises if OPENAI_API_KEY missing
    pages = read_pdf_pages(pdf_path)
    classification = classify_document(pages)
    if classification.document_type == "unknown":
        raise ValueError(
            f"{pdf_path.name} classified as 'unknown' — not a supported earnings "
            "report or transcript; nothing to record."
        )
    pages_for_extraction = select_extraction_pages(pages)

    batch = extract_metrics_live(
        pages=pages_for_extraction,
        document_type=classification.document_type,
        config=config,
    )

    RECORDED_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(batch.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Recorded {len(batch.metrics)} raw metrics "
        f"({classification.document_type}, model={config.model}) → {out_path}"
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing cassette for this PDF.",
    )
    args = parser.parse_args()
    record(args.pdf_path, force=args.force)


if __name__ == "__main__":
    main()
