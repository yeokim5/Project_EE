# VERIFY.md — The Gate

These are the exact commands that must pass before any task is "done" and before submission. This is the deterministic gate that replaces CI for this project. Update this file manually as commands change.

## Run order

```bash
# 1. install (clean env, reviewer-friendly)
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

# 2. REQUIRED gate — draft extraction must succeed in offline/recorded mode (no API key)
python -m earnings_extractor extract assesment_info --out outputs/run_001 --mode recorded

# 3. draft outputs must be inspectable and reviewable
python -m earnings_extractor inspect outputs/run_001/draft_metrics.json
python -m earnings_extractor review outputs/run_001 --out outputs/run_001/review.html --demo-decisions outputs/run_001/review_decisions.json

# 4. final export requires review decisions
python scripts/make_acceptance_decisions.py outputs/run_001 --out outputs/run_001/human_review_decisions.json
python -m earnings_extractor export outputs/run_001 --decisions outputs/run_001/human_review_decisions.json --out outputs/extractions.xlsx

# 5. THE accuracy gate — must report >= 90% on scored template fields from the extractor pipeline
#    Recorded/offline mode is acceptable; live API mode is optional.
python -m earnings_extractor extract assesment_info/TSLA-Q2-2025-Update.pdf --out outputs/test_tesla --mode recorded
python -m earnings_extractor eval --draft outputs/test_tesla/draft_metrics.json --document-id tesla_q2_2025 --min-accuracy 0.9
python -m earnings_extractor extract assesment_info/citi_earnings_q12025.pdf --out outputs/test_citi --mode recorded
python -m earnings_extractor eval --draft outputs/test_citi/draft_metrics.json --document-id citi_q1_2025 --min-accuracy 0.9

# 6. tests + lint (hard)
pytest -q
ruff check .          # lint

# optional checks
python3 scripts/update_project_map.py --check
mypy earnings_extractor          # only if a real type config exists
python -m earnings_extractor extract assesment_info --mode live   # optional live check, needs API key
```

Live-mode drafts include measured OpenAI token usage in
`draft_metrics.json.llm_usage`; recorded drafts keep this list empty.

## Phase 3 interim gate

Phase 3 uses live mode while recorded/offline mode is still scheduled for Phase
4. Load `OPENAI_API_KEY` from `.env`; do not put secrets in the command line.

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m earnings_extractor extract assesment_info/TSLA-Q2-2025-Update.pdf --out outputs/phase3_tesla --mode live
.venv/bin/python -m earnings_extractor inspect outputs/phase3_tesla/draft_metrics.json
.venv/bin/python -m earnings_extractor eval --draft outputs/phase3_tesla/draft_metrics.json --document-id tesla_q2_2025
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
python3 scripts/update_project_map.py --check
```

## Phase 4 interim gate

Phase 4 adds recorded/keyless extraction, normalization, review flags, and
consistency checks. Accuracy eval intentionally runs on **single-document**
drafts because the current scorer maps rows by `metric_name` and is not yet
document-scoped for combined drafts.

```bash
.venv/bin/python -m earnings_extractor extract assesment_info/TSLA-Q2-2025-Update.pdf --out outputs/test_tesla --mode recorded
.venv/bin/python -m earnings_extractor eval --draft outputs/test_tesla/draft_metrics.json --document-id tesla_q2_2025
.venv/bin/python -m earnings_extractor extract assesment_info/citi_earnings_q12025.pdf --out outputs/test_citi --mode recorded
.venv/bin/python -m earnings_extractor eval --draft outputs/test_citi/draft_metrics.json --document-id citi_q1_2025
.venv/bin/python -m earnings_extractor extract assesment_info --out outputs/run_001 --mode recorded
.venv/bin/python -m earnings_extractor inspect outputs/run_001/draft_metrics.json
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
python3 scripts/update_project_map.py --check
```

## Pass criteria (all must be true)

- [ ] `extract` succeeds in **offline/recorded mode with no API key** and creates draft artifacts — this is the required reviewer path. Live mode is an optional API-backed check.
- [ ] `outputs/run_001/draft_metrics.json`, `outputs/run_001/review_queue.json`, `outputs/run_001/evidence_report.md`, and `outputs/run_001/review.html` all exist.
- [ ] `review.html` shows every extracted/scored value with source PDF, source page, source quote, confidence/check status, and review status/control.
- [ ] Recorded verification mode creates a clearly labeled `review_decisions.json` for deterministic checks; production use requires real human decisions.
- [ ] Final export requires `review_decisions.json`; unreviewed required fields are refused unless an explicit draft/unreviewed override is used.
- [ ] `outputs/extractions.json`, `outputs/extractions.xlsx`, and `outputs/extractions.audit.md` all exist after reviewed export.
- [ ] The first sheet in `outputs/extractions.xlsx` is client-template-compatible with the exact columns from `assesment_info/EarningsSample (1).xlsx`: `Company Name`, `Quarter`, `Total revenue`, `Earnings per share`, `Net income`, `Operating income`, `Gross margin`, `Operating expenses`, `Buybacks and dividends`.
- [ ] Additional Excel tabs exist for `Metrics`, `Review Decisions`, and `Evidence`.
- [ ] Every metric row has `source_page`, `source_quote`, `confidence`, `needs_review`, and review status.
- [ ] Uncertain, unsupported, or missing template values are blank and/or review-flagged with a reason; they are not guessed to make the template look complete.
- [ ] `eval` reports ≥90% over the scored template fields defined in `docs/EVAL_SPEC.md`, from the extractor pipeline in recorded/offline mode or live mode; golden values are NOT fed into the extractor.
- [ ] Applicable internal consistency checks run (for example gross profit = revenue − COGS; OCF − capex = FCF) and mismatches flag for review. Inapplicable checks are skipped with a reason, not forced.
- [ ] Cost notes compare estimated automation cost per PDF against the confirmed analyst baseline of roughly `$3.33-$5.00/PDF`.
- [ ] `pytest` green; `ruff` clean. (mypy optional unless configured.) Unit
      tests must cover the deterministic logic — unit/scale normalization,
      consistency checks, the canonical→client-cell format mapper, and JSON
      schema validation/repair — not just import smoke tests.
- [ ] No secrets committed; `.env.example` present; `git status` clean of junk.

## Best-effort (not a hard gate)

- [ ] Generalization **smoke test**: run the pipeline on one unseen earnings report PDF or earnings-call transcript. There are no golden labels for it, so this is NOT an accuracy gate — document the result and any failure modes in PROCESS.md.
- [ ] **Citation locator + viewer** (Phase 10 foundation slice): `tests/test_locate.py` passes, and the viewer builds and highlights the right text.

```bash
# deterministic locator tests
pytest tests/test_locate.py -q

# build a read-only citation viewer for a finished draft, then open it
python -m earnings_extractor extract assesment_info/citi_earnings_q12025.pdf --out outputs/test_citi --mode recorded
python scripts/build_citation_viewer.py \
  --draft outputs/test_citi/draft_metrics.json \
  --pdf assesment_info/citi_earnings_q12025.pdf \
  --out outputs/citations_citi.html
# open outputs/citations_citi.html → click a metric → highlight lands on its number
```

## Reminder

A task is not done because code exists or an agent says so. It is done when the relevant command above prints passing output. Re-run after every meaningful change.
