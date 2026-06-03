"""Score a batch draft against the verified 15-PDF production golden set.

Picks each document's rows out of the combined draft by matching the PDF
basename, scores every golden field with the existing tolerance-aware
``score_field``, and prints per-document and overall accuracy.

Usage:
    python scripts/score_production.py outputs/pdf_input_batch/extractions.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from evaluation.golden_production import PRODUCTION_FIELDS
from evaluation.scoring import score_field


def _basename(path_like: str | Path) -> str:
    return Path(str(path_like)).name


def load_metrics(draft_path: Path) -> list[SimpleNamespace]:
    """Load draft rows as shims.

    The persisted draft carries review/decision bookkeeping fields that the
    strict ``MetricRow`` contract forbids, so we read only the fields the scorer
    touches rather than re-validating the whole row.
    """

    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    rows = payload["metrics"] if isinstance(payload, dict) else payload
    return [
        SimpleNamespace(
            source_file=row.get("source_file"),
            metric_name=row.get("metric_name"),
            value=row.get("value"),
            source_quote=row.get("source_quote"),
            source_page=row.get("source_page"),
            needs_review=row.get("needs_review", True),
            review_reason=row.get("review_reason"),
        )
        for row in rows
    ]


def main(draft_path: Path) -> int:
    metrics = load_metrics(draft_path)
    by_file: dict[str, dict[str, MetricRow]] = defaultdict(dict)
    for metric in metrics:
        if metric.source_file:
            by_file[_basename(metric.source_file)][metric.metric_name] = metric

    scored = [f for f in PRODUCTION_FIELDS if f.status != "not_scored"]
    passed = 0
    per_doc: dict[str, list[tuple[str, bool, str]]] = defaultdict(list)
    for field in scored:
        actual = by_file.get(_basename(field.source_file), {}).get(field.field_name)
        result = score_field(field, actual)
        passed += int(result.passed)
        per_doc[field.document_id].append(
            (field.field_name, result.passed, result.reason)
        )

    for doc in sorted(per_doc):
        fields = per_doc[doc]
        ok = sum(1 for _, p, _ in fields if p)
        print(f"\n{doc}: {ok}/{len(fields)}")
        for name, p, reason in fields:
            mark = "PASS" if p else "FAIL"
            print(f"  {mark}  {name}: {reason}")

    total = len(scored)
    print("\n" + "=" * 56)
    print(f"PRODUCTION ACCURACY: {passed}/{total} = {passed / total:.1%}")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "outputs/pdf_input_batch/extractions.json"
    )
    raise SystemExit(main(target))
