"""Live before/after for the grounded line-item selector.

For each (pdf, metric) target, prints what the draft extracted versus what the
definition-driven selector picks from the same page -- on real PDF text, with a
real model call. Shows the selector correcting a composite/component trap that
the plain extractor fell into, while the deterministic cages keep the value
grounded.

Usage:
    python scripts/demo_line_selector.py
"""

from __future__ import annotations

import json
from pathlib import Path

from earnings_extractor.config import load_openai_config
from earnings_extractor.ingest import read_pdf_pages
from earnings_extractor.line_item_selector import resolve_line_item

DRAFT = Path("outputs/pdf_input_batch/extractions.json")

# (pdf basename, metric, the wrong-line shape the draft fell into)
TARGETS = [
    ("marathon_petroleum_q1_2026.pdf", "Total revenue", "picked 'revenues and other income'"),
    ("marathon_petroleum_q3_2025.pdf", "Total revenue", "picked 'revenues and other income'"),
    ("pepsico_q1_2025.pdf", "Operating expenses", "picked SG&A (a component)"),
    ("pepsico_q1_2026.pdf", "Operating expenses", "picked SG&A (a component)"),
]


def draft_row(metrics: list[dict], basename: str, metric: str) -> dict | None:
    for row in metrics:
        if row.get("metric_name") == metric and Path(
            str(row.get("source_file"))
        ).name == basename:
            return row
    return None


def main() -> int:
    config = load_openai_config()
    metrics = json.loads(DRAFT.read_text())["metrics"]

    for basename, metric, note in TARGETS:
        row = draft_row(metrics, basename, metric)
        pdf = Path("pdf_input_fresh") / basename
        pages = read_pdf_pages(pdf)
        cited = row.get("source_page") if row else None
        page_text = next(
            (p.text for p in pages if p.page_number == cited),
            "\n".join(p.text for p in pages),
        )

        result = resolve_line_item(metric, page_text, config, "live")

        print("=" * 70)
        print(f"{basename}  |  {metric}")
        print(f"  draft   : value={row.get('value') if row else None!r}  ({note})")
        if result is None:
            print("  selector: (no definition / no candidates)")
        elif result.status == "selected":
            print(f"  selector: SELECTED  {result.label!r}")
            print(f"            line: {result.value_text!r}")
            print(f"            why : {result.reason}")
        elif result.status == "not_disclosed":
            print(f"  selector: NOT_DISCLOSED -- {result.reason}")
        else:
            print(f"  selector: REJECTED -- {result.reason}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
