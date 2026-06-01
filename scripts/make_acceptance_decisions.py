"""Create synthetic acceptance decisions for local recorded verification.

This script is not part of the product CLI. It exists so the Phase 6 acceptance
commands can exercise the final-export path without pretending that the
production workflow skips human review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from earnings_extractor.review import (
    ReviewDecision,
    ReviewDecisionsFile,
    build_review_items,
)
from earnings_extractor.schema import DraftRun, utc_now_iso

SYNTHETIC_REVIEWER = "SYNTHETIC ACCEPTANCE HELPER - not human reviewed"
SYNTHETIC_NOTE = (
    "Synthetic acceptance decision for local verification; production requires "
    "real human review."
)


def build_acceptance_decisions(draft: DraftRun) -> ReviewDecisionsFile:
    reviewed_at = utc_now_iso()
    decisions = []
    for item in build_review_items(draft):
        if item.requires_attention and item.value in (None, ""):
            status = "not_applicable"
            note = SYNTHETIC_NOTE
        elif item.requires_attention:
            status = "approved"
            note = SYNTHETIC_NOTE
        else:
            status = "approved"
            note = None
        decisions.append(
            ReviewDecision(
                metric_id=item.metric_id,
                review_status=status,
                reviewer_note=note,
                reviewed_at=reviewed_at,
                reviewer=SYNTHETIC_REVIEWER,
            )
        )
    return ReviewDecisionsFile(
        run_id=draft.run_id,
        created_at=reviewed_at,
        is_demo=False,
        decisions=decisions,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create synthetic review decisions for acceptance checks.",
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    draft_path = args.run_dir / "draft_metrics.json"
    try:
        draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        parser.error(f"Missing draft metrics file: {draft_path}")
    except ValidationError as exc:
        parser.error(f"Invalid draft metrics file: {draft_path}: {exc}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    decisions = build_acceptance_decisions(draft)
    args.out.write_text(
        json.dumps(decisions.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
