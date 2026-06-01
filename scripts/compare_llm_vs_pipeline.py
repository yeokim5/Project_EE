"""A/B test: hybrid (LLM + deterministic) vs. LLM-only extraction accuracy.

Isolates exactly what the deterministic post-LLM layer (template completion,
company identity resolution, capital-return enrichment, unit/scale normalization,
and validation/review flagging) buys us in accuracy.

Key design: ONE extraction call per document, branched two ways so both arms see
the *identical* LLM output. The only difference between arms is the deterministic
layer -- so the accuracy delta is attributable to that layer alone, not to the
model's run-to-run randomness.

  * "LLM only" = the raw model metrics, scored directly with NO deterministic
    post-processing.
  * "Hybrid"   = the SAME metrics run through the full deterministic chain.

Both are scored against the eval-only golden targets; golden values are never fed
into either arm.

Modes:
  --mode recorded : keyless, replays the committed cassette (default).
  --mode live     : makes a real API call (needs OPENAI_API_KEY in .env, costs
                    tokens). This is the true live A/B test.

Usage:
    python scripts/compare_llm_vs_pipeline.py                 # recorded
    python scripts/compare_llm_vs_pipeline.py --mode live     # real API A/B
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from earnings_extractor.classify import classify_document, select_extraction_pages
from earnings_extractor.config import load_openai_config
from earnings_extractor.extractor import extract_metrics_live
from earnings_extractor.identity import apply_company_identity, resolve_company_identity
from earnings_extractor.ingest import read_pdf_metadata, read_pdf_pages
from earnings_extractor.normalize import normalize_metrics
from earnings_extractor.recorded import extract_metrics_recorded
from earnings_extractor.schema import (
    DraftRun,
    MetricRow,
    MetricsBatch,
    new_run_id,
    utc_now_iso,
)
from earnings_extractor.validation import (
    complete_template_rows,
    enrich_capital_return_text,
    validate_metrics,
)
from evaluation.scoring import ScoreReport, score_draft

ROOT = Path(__file__).resolve().parents[1]
ASSESSMENT_INFO = ROOT / "assesment_info"
OUT_DIR = ROOT / "outputs" / "comparison"

DOCUMENTS = [
    {
        "document_id": "tesla_q2_2025",
        "pdf": ASSESSMENT_INFO / "TSLA-Q2-2025-Update.pdf",
    },
    {
        "document_id": "citi_q1_2025",
        "pdf": ASSESSMENT_INFO / "citi_earnings_q12025.pdf",
    },
]


def make_draft(metrics: list[MetricRow], model: str) -> DraftRun:
    return DraftRun(
        run_id=new_run_id(),
        created_at=utc_now_iso(),
        mode="recorded",
        model=model,
        reasoning_effort=None,
        documents=[],
        classifications=[],
        selected_pages={},
        metrics=metrics,
    )


def apply_deterministic_layer(metrics, document_type, pages_for_extraction, pages,
                              metadata, source_file):
    """Mirror exactly the deterministic chain in pipeline.extract()."""
    complete_template_rows(metrics, document_type, pages_for_extraction)
    identity = resolve_company_identity(
        pages=pages,
        metadata=metadata,
        source_file=source_file,
        document_type=document_type,
    )
    apply_company_identity(metrics, identity)
    enrich_capital_return_text(metrics, pages)
    normalize_metrics(metrics)
    validate_metrics(metrics, pages)
    return metrics


def extract_once(
    pdf_path: Path, mode: str
) -> tuple[MetricsBatch, str, list, list, dict]:
    """Single extraction call, returning the raw batch + everything the
    deterministic layer needs. No call is made twice."""
    pages = read_pdf_pages(pdf_path)
    metadata = read_pdf_metadata(pdf_path)
    classification = classify_document(pages)
    pages_for_extraction = select_extraction_pages(pages)

    if mode == "live":
        config = load_openai_config()
        batch = extract_metrics_live(
            pages=pages_for_extraction,
            document_type=classification.document_type,
            config=config,
        )
        model = config.model
    else:
        batch = extract_metrics_recorded(pdf_path)
        model = "recorded"

    return batch, classification.document_type, pages, pages_for_extraction, {
        "metadata": metadata,
        "model": model,
        "source_file": str(pdf_path),
    }


def report_to_dict(report: ScoreReport) -> dict:
    return {
        "document_id": report.document_id,
        "passed": report.passed,
        "total": report.total,
        "accuracy": report.accuracy,
        "fields": [
            {"field": f.field_name, "passed": f.passed, "reason": f.reason}
            for f in report.fields
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "recorded"), default="recorded")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for doc in DOCUMENTS:
        document_id = doc["document_id"]
        pdf_path = doc["pdf"]

        batch, doc_type, pages, sel_pages, ctx = extract_once(pdf_path, args.mode)

        # Arm A: raw LLM output, untouched.
        raw_metrics = [m.model_copy(deep=True) for m in batch.metrics]
        raw_draft = make_draft(raw_metrics, model=f"{ctx['model']}-llm-only")

        # Arm B: SAME output through the deterministic layer.
        hybrid_metrics = [m.model_copy(deep=True) for m in batch.metrics]
        apply_deterministic_layer(
            hybrid_metrics, doc_type, sel_pages, pages,
            ctx["metadata"], ctx["source_file"],
        )
        hybrid_draft = make_draft(hybrid_metrics, model=f"{ctx['model']}-hybrid")

        raw_report = score_draft(raw_draft, document_id=document_id)
        hybrid_report = score_draft(hybrid_draft, document_id=document_id)

        results.append(
            {
                "document_id": document_id,
                "pdf": pdf_path.name,
                "mode": args.mode,
                "model": ctx["model"],
                "llm_only": report_to_dict(raw_report),
                "hybrid": report_to_dict(hybrid_report),
            }
        )

    out_json = OUT_DIR / f"comparison_results_{args.mode}.json"
    out_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print(f"\nA/B test ({args.mode} mode) -- one call per doc, branched two ways\n")
    tot_raw = tot_hyb = tot_n = 0
    for r in results:
        lo, hy = r["llm_only"], r["hybrid"]
        tot_raw += lo["passed"]
        tot_hyb += hy["passed"]
        tot_n += hy["total"]
        print(f"=== {r['document_id']} ({r['pdf']}, model={r['model']}) ===")
        print(f"  LLM only: {lo['passed']}/{lo['total']} ({lo['accuracy']:.1%})"
              f"   Hybrid: {hy['passed']}/{hy['total']} ({hy['accuracy']:.1%})")
        by_raw = {f["field"]: f for f in lo["fields"]}
        for ff in hy["fields"]:
            rf = by_raw.get(ff["field"], {"passed": None, "reason": "n/a"})
            flip = ""
            if rf["passed"] is False and ff["passed"] is True:
                flip = "  <== FIXED by deterministic layer"
            elif rf["passed"] is True and ff["passed"] is False:
                flip = "  <== REGRESSED"
            rs = "PASS" if rf["passed"] else "FAIL"
            fs = "PASS" if ff["passed"] else "FAIL"
            print(f"    {ff['field']:24s} LLM:{rs}  HYBRID:{fs}{flip}")
            if flip:
                print(f"        raw : {rf['reason']}")
                print(f"        hyb : {ff['reason']}")
        print()

    print(f"COMBINED  LLM only: {tot_raw}/{tot_n} ({tot_raw/tot_n:.1%})"
          f"   Hybrid: {tot_hyb}/{tot_n} ({tot_hyb/tot_n:.1%})")
    print(f"Wrote {out_json}")


if __name__ == "__main__":
    main()
