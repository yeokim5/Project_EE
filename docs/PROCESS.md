# PROCESS.md — Strange Loop Take-Home Playbook

This is my step-by-step plan for the Strange Loop earnings-extraction assignment.
I update the "Status" line in each phase as I go. Newest decisions get logged at
the bottom in the Decision Log.

---

## 0. The One-Paragraph Summary

The client pays analysts to read public earnings reports and earnings-call
transcripts, then type standard financial metrics into Excel by hand. I am
building a review-first tool that automates the draft work without removing
human control: it ingests PDFs, extracts the client's required Excel fields with
structured LLM calls, normalizes units and scale, validates results, attaches
source page and quote evidence, presents every value for human citation review,
and only then exports a client-template-compatible Excel workbook plus JSON and
an audit report. The target is at least 90% field-level extraction accuracy and a
cost comfortably below the manual analyst baseline.

---

## 1. What I'm Being Judged On

This assignment is a simulation of real Strange Loop work. They told me to
"pretend to be a Strange Loop Engineer." So they are scoring:

1. **Can I understand the client workflow?** The client already has an Excel
   process; the best solution augments it with a citation review gate instead of
   replacing analyst judgment.
2. **Can I handle ambiguity?** The brief was intentionally broad, and the client
   answer now narrows v1 scope.
3. **Is the code production-minded?** Clean, testable, auditable, no hardcoded
   sample answers.
4. **Does it generalize within v1 scope?** It should work on earnings reports
   and call transcripts beyond only Tesla and Citi.
5. **Can I defend decisions?** The follow-up likely probes architecture,
   accuracy, security, cost, and rollout path.

Guiding rules:

- **No hardcoding answers.** Golden values live only in eval code; the extractor
  must use the real extraction path.
- **Review first, template final.** Extraction creates draft artifacts and a
  citation review UI/report. Final Excel is created after human review decisions.
- **Honesty over a shiny number.** A real 90%+ with review flags beats a
  suspicious 100%.

---

## 2. Client-Confirmed Requirements

Andrew replied on 2026-05-29 with the information below.

**Excel template:** The team uses `assesment_info/EarningsSample (1).xlsx`.
The workbook has one sheet with these columns:

```text
Company Name
Quarter
Total revenue
Earnings per share
Net income
Operating income
Gross margin
Operating expenses
Buybacks and dividends
```

The final exported workbook must include a first sheet that is compatible with
that template. Draft extraction should happen before final export so a human can
review citations first. I can add additional tabs for normalized data, review
decisions, and evidence.

Source for the client-confirmed requirements in this section: Andrew's reply is
recorded in `docs/CLIENT_REPLY.md`.

**Manual review:** Approved. If a value is low-confidence, missing, ambiguous,
or fails a consistency check, set `needs_review = true` and show the source
context instead of pretending certainty.

**Document scope for v1:** Support only earnings report PDFs and earnings-call
transcripts. Do not spend v1 effort on 10-Qs, 10-Ks, press releases, or other
formats unless they are naturally covered by the same pipeline.

**Hosted LLM API:** Approved for the assignment. The earnings reports are public
data, and there are no restrictions on where the data can be processed.

**Cost baseline:** Analysts cost about `$50/hour` and process `10-15 PDFs/hour`,
so the manual baseline is roughly `$3.33-$5.00 per PDF`. The automated solution
should be comfortably below that, including LLM and infrastructure cost.

---

## 3. Key Facts From The Provided Data

The two sample PDFs are intentionally different:

- `citi_earnings_q12025.pdf` is a real earnings-call transcript with narrative
  prose such as "EPS of $1.96 and an RoTCE of 9.1% on $21.6 billion of
  revenues."
- `TSLA-Q2-2025-Update.pdf` is a structured shareholder update / earnings
  report deck with dense tables and financial statements.

Implication: the extractor needs a deterministic review-first pipeline that can
handle both prose and table-heavy PDFs. A transcript-only regex parser would be
overfit.

Initial eval targets are split into:

- **Primary template fields** — the client-facing Excel columns, scored for the
  main >=90% accuracy number when source-supported.
- **Auxiliary metrics** — useful audit/validation values such as RoTCE, CET1,
  cost of credit, operating cash flow, and free cash flow. These can appear in
  the normalized `Metrics`/`Evidence` tabs, but they are not part of the primary
  template score unless explicitly listed in the eval fixture.

Full golden values, source pages, and source quotes are defined in
`docs/EVAL_SPEC.md`. Do not create `evaluation/` golden fixtures until each
expected value has page/quote evidence there.

Scale trap: Tesla reports mostly in millions; Citi states many values in
billions. Normalize internally to one canonical numeric representation, then
format for the client-facing Excel sheet.

Template-field caveat: not every company/document will clearly contain every
template column. For example, "Operating income" and "Gross margin" may not be
meaningful for a bank transcript in the same way they are for an industrial
company. If a field is not clearly supported by source evidence, leave it blank
or mark it for review; do not guess.

---

## 4. Architecture Decisions

Keep the two architectures separate:

- **Development method:** verification-driven, eval-first, with a single lead
  agent and fresh-context review at gates.
- **Product architecture:** a deterministic review-first pipeline with
  constrained, structured LLM extraction calls. It is not a runtime autonomous
  agent.

Pipeline:

```text
ingest PDFs
-> classify document/page style (narrative vs table-heavy)
-> extract required metrics to structured JSON with evidence
-> normalize units/scale
-> validate and run consistency checks
-> assign confidence + needs_review
-> human reviews citations and approves/rejects/marks not applicable
-> export final client template Excel + JSON + audit report
```

Decisions:

- **LLM-based extraction path.** It is the best fit for unseen public earnings
  reports and transcripts. Regex/templates can support validation, but should
  not be the primary source of truth.
- **Recorded/offline mode.** Reviewers must be able to run the pipeline without
  an API key. Recorded LLM responses are acceptable as a cassette pattern, as
  long as golden values are not fed into extraction code.
- **Deterministic validators.** Unit/scale normalization and consistency checks
  push accuracy higher and create a defensible trust story.
- **Evidence on every metric.** Each extracted metric must carry source page,
  source quote, confidence, review reason, and human review status.
- **Draft before final.** `extract` writes draft artifacts and a review UI/report;
  `export` creates final Excel only after review decisions or an explicit
  draft/unreviewed override.
- **Excel output follows the client template.** The first sheet in the final
  workbook matches `EarningsSample (1).xlsx`; additional tabs provide the
  production-grade audit trail.

---

## 5. Agentic Coding Method

Use this loop for every non-trivial task:

```text
Explore (read-only) -> Plan -> Implement on a branch -> Verify (docs/VERIFY.md)
-> fresh-context Review (docs/REVIEW.md) -> iterate until green -> human approves
```

Rules:

- Build or extend the eval harness before/alongside extractor changes.
- **Two test layers, both required.** (1) The eval harness scores extraction
  accuracy end-to-end (integration). (2) `pytest` unit tests cover the
  deterministic logic — unit/scale normalization, consistency checks, the
  canonical→client-cell format mapper, and JSON schema validation/repair. Write
  the unit tests in the same phase as the code they cover, never bolted on at the
  end. Do not unit-test the live LLM call itself; that is what the eval is for.
- Use subagents sparingly: fresh-context diff review and one unseen-company
  smoke test only.
- Keep scope tight. Out-of-scope ideas go in README "Even better with more
  time," not into surprise product features.
- Do not auto-edit `AGENTS.md`; only propose durable rule changes.

---

## 6. Phases

Each phase has a status line. Update it as work moves.

Each phase also has a **Gate to advance**: a short list of concrete,
command-backed checks that must ALL be true before starting the next phase. Do
not begin the next phase until the current gate is green. A phase is never "done"
because code exists — only because its gate passes (see Section 9).

### Phase 0 — Project Setup

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. `git init`, create `.gitignore` for `.env`, `.venv/`, `outputs/`,
   `__pycache__/`, local scratch files.
2. Create package skeleton and docs skeletons.
3. Add `.env.example`.
4. Keep provided assignment inputs under `assesment_info/`.
5. Set up the test harness: configure `pytest` in `pyproject.toml`, create a
   `tests/` directory, and add one trivial passing test so `pytest -q` is green
   from day one (no extractor code needed yet).
6. Make a baseline git commit of the planning docs + scaffold.

**Gate to advance to Phase 2 (all must be true):**

- `pip install -e ".[dev]"` succeeds in a clean venv.
- `pytest -q` runs and is green (placeholder test is fine).
- `ruff check .` is clean.
- No secrets committed; `.env` gitignored; `git status` clean of junk.
- A baseline commit exists.

Current state:

- Done: `git init`, `.gitignore`, `.env.example`, planning docs, and a living
  repo map at `docs/PROJECT_MAP.md` with `scripts/update_project_map.py`.
- Done: package skeleton in `earnings_extractor/`, install/test scaffolding in
  `pyproject.toml` and `tests/`, and a baseline commit of the scaffold.
- Verified: `.venv/bin/python -m pip install -e ".[dev]"`,
  `.venv/bin/python -m pytest -q`, and `.venv/bin/python -m ruff check .`.
- Next action: start Phase 2 by extracting readable page-level text from both
  sample PDFs and inspecting the Excel template formats.

### Phase 1 — Client Questions

**Status:** [ ] todo  [ ] sent  [x] reply received

Done:

- Email sent 2026-05-29.
- Andrew replied with template, review preference, document scope, LLM approval,
  and cost baseline.

Next action: build against the confirmed requirements above.

**Gate to advance:** template, review preference, v1 scope, LLM approval, and
cost baseline are confirmed and recorded in `docs/CLIENT_REPLY.md`. (Met.)

### Phase 2 — Understand Data + Eval Targets

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. Extract page-level text from both PDFs; confirm prose and tables are readable.
2. Inspect `EarningsSample (1).xlsx`; preserve exact first-sheet columns **and
   match the sample row's per-cell formats** (currency columns are `$<n>B` text
   strings, `Gross margin` is a decimal fraction in a `0%` cell, EPS is a plain
   number). The sample row and formats are recorded in `docs/CLIENT_REPLY.md`.
3. Define golden metrics in an eval-only module, including template-required
   fields and source evidence.
4. Define accuracy from `docs/EVAL_SPEC.md`: field-level over scored template
   fields, numeric tolerance after unit normalization, exact-ish matching for
   company/quarter, and explicit scoring for unsupported fields that should be
   blank/review-flagged.

**Gate to advance to Phase 3 (all must be true):**

- Both sample PDFs produce readable page-level text (prose AND tables confirmed).
- The client template's columns AND sample-row cell formats are recorded.
- Golden fixture lives in eval-only code, never importable by the extractor.
- Every golden value carries source page + quote in `docs/EVAL_SPEC.md`.
- The accuracy rule (tolerances, blank/review scoring) is written down.

Current state:

- Done: `earnings_extractor/ingest.py` extracts page-level PDF text with
  1-based physical page numbers from `pdfplumber`.
- Done: `scripts/inspect_phase2_inputs.py --check` verifies cited PDF evidence
  using whitespace-normalized quote matching and confirms the Excel template's
  headers/sample formats.
- Done: `evaluation/` contains eval-only golden targets and tolerance constants;
  tests statically prevent runtime imports from `earnings_extractor/`.
- Verified: `.venv/bin/python -m pytest -q`,
  `.venv/bin/python -m ruff check .`,
  `python3 scripts/update_project_map.py --check`, and
  `.venv/bin/python scripts/inspect_phase2_inputs.py --check`.
- Next action: start Phase 3 with CLI command implementations, document/page
  classification, schema validation/repair, and the first end-to-end draft
  extraction path.

### Phase 3 — Core Pipeline, Eval-First

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. Create package `earnings_extractor`.
2. Stub CLI commands: `extract`, `review`, `export`, `inspect`, `eval`.
3. Build PDF ingestion into page chunks.
4. Classify input as earnings report / update deck vs call transcript, plus
   narrative vs table-heavy pages.
5. Define the internal metric schema.
6. Implement structured LLM extraction with evidence.
7. Get one company end-to-end and run eval immediately.
8. Add unit tests for the deterministic pieces introduced here: JSON-schema
   validation/repair of LLM output, and document/page classification.

Internal metric schema:

```text
company · ticker · document_type · fiscal_period · report_date ·
metric_name · metric_category · segment · value · unit · scale · period ·
gaap_or_non_gaap · year_over_year_change · source_page · source_quote ·
confidence · needs_review · review_reason · review_status · reviewer_note
```

**Gate to advance to Phase 4 (all must be true):**

- One PDF runs end-to-end to structured draft metrics with evidence fields.
- `eval` runs and prints a field-level score (any score — the harness works).
- Unit tests for schema validation/repair and classification pass.
- Malformed LLM output is rejected/repaired, not trusted blindly.
- Do not require final Excel yet.

Current state:

- Done: real `extract`, `inspect`, and `eval` CLI commands; deterministic
  classification; internal Pydantic metric schema; live OpenAI structured-output
  extraction; eval-only scoring behind `earnings_extractor/_eval_bridge.py`.
- Deferred by design: recorded mode, review UI, final Excel export,
  normalization, and consistency checks.
- Phase 3 live gate uses `.env` for `OPENAI_API_KEY`, `OPENAI_MODEL`, and
  `OPENAI_REASONING_EFFORT`.
- Verified: live Tesla extraction created `outputs/phase3_tesla/draft_metrics.json`
  with 9 metrics, evidence on 9/9, and review flags on 2/9. Eval printed
  `8/9` (`88.9%`), with the remaining miss on company-name extraction; Phase 4
  handles accuracy iteration and recorded mode.
- Verified: `.venv/bin/python -m pytest -q`,
  `.venv/bin/python -m ruff check .`, and
  `python3 scripts/update_project_map.py --check`.
- Next action: start Phase 4 by adding canonical normalization, consistency
  checks, review-flag logic, and cassette-style recorded mode.

### Phase 4 — Validation, Review Flags, Recorded Mode

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. Normalize values to a canonical internal unit/scale.
2. Add consistency checks where meaningful:
   - Tesla-style statements: gross profit = revenue - cost of revenue;
     operating income = gross profit - operating expenses; OCF - capex = FCF.
   - Citi-style transcript: cross-check repeated firmwide figures and capital
     return wording where present.
   - If a check is inapplicable to a document or company type, skip it with a
     reason rather than forcing a bad comparison.
3. Set `needs_review = true` for low confidence, missing evidence, unsupported
   template fields, malformed LLM output, or failed checks.
4. Record live LLM responses so `--mode recorded` runs keyless.
5. Get both sample PDFs working.
6. Add unit tests for: unit/scale normalization (millions vs billions →
   canonical), each consistency check including its inapplicable-skip behavior,
   and the review-flag triggers.

**Gate to advance to Phase 5 (all must be true):**

- Both sample PDFs extract; canonical normalization is correct.
- Unit tests for normalization, consistency checks, and review-flag logic pass.
- `--mode recorded` runs with NO API key.
- Inapplicable checks skip with a reason; failures set `needs_review = true`.
- `eval` field-level score is at or near target on both companies.

Current state:

- Done: `--mode recorded` replays cassette-style structured responses without
  loading OpenAI config or requiring `OPENAI_API_KEY`.
- Done: canonical normalization converts currency values to USD millions,
  percentages to percentage points, EPS to dollars/share, and capital
  expenditures to a positive outflow convention for checks.
- Done: deterministic post-processing completes all nine template rows, resolves
  company identity from full-document text/metadata, preserves source evidence,
  and review-flags low confidence, missing/placeholder evidence, blank template
  fields, normalization failures, and failed consistency checks.
- Done: Tesla recorded output includes operating cash flow, capital
  expenditures, and free cash flow, so the OCF - capex = FCF check runs on real
  recorded draft data.
- Verified: single-document recorded eval reports `9/9` (`100.0%`) for Tesla
  and `9/9` (`100.0%`) for Citi. The combined `outputs/run_001` draft is
  inspectable and contains both documents, but Phase 4 eval uses
  single-document drafts because the current scorer is keyed only by
  `metric_name`.
- Verified: `.venv/bin/python -m pytest -q`,
  `.venv/bin/python -m ruff check .`, and
  `python3 scripts/update_project_map.py --check`.
- Next action: start Phase 5 by generating `review_queue.json`,
  `evidence_report.md`, and a local static `review.html` / demo decisions flow
  from the recorded draft artifacts.

### Phase 5 — Human Review UI / Review Decisions

**Status:** [ ] todo  [ ] doing  [x] done

Do: create the human citation review layer.

Required review artifacts:

- `review.html` — local review UI/report showing each value, source PDF, page,
  source quote, confidence, validation status, and review controls/status.
- `review_queue.json` — rows needing attention or required approval.
- `review_decisions.json` — human decisions: `approved`, `rejected`,
  `needs_fix`, or `not_applicable`.

Review behavior:

- High-confidence values still need visible citation review before final export.
- Low-confidence or failed-check values can export only if explicitly approved.
- Rejected/needs-fix values do not export as final values.
- Not-applicable values stay blank in the client template and remain in the
  audit trail.

**Gate to advance to Phase 6 (all must be true):**

- `review.html` shows every value with source PDF, page, quote, confidence,
  check status, and a decision control.
- A valid `review_decisions.json` is produced (demo mode acceptable if clearly
  labeled).
- A unit test covers parsing/validation of the review-decision file.

Current state:

- Done: `review` CLI generates `review_queue.json`, `evidence_report.md`, a
  static local `review.html`, and optional demo `review_decisions.json`.
- Done: review items include deterministic run-scoped `metric_id`s, resolved
  source PDFs, confidence/check state, source page/quote, review reasons, and
  decision controls.
- Done: demo decisions are explicitly labeled with `is_demo = true`; browser
  downloads produce human-shaped files with `is_demo = false`.
- Verified: combined recorded review run produces 21 review items for Tesla and
  Citi, and unit tests cover decision parsing/validation, browser-shaped human
  decisions, duplicate/invalid statuses, source-resolution attention flags, and
  HTML JSON escaping.
- Next action: start Phase 6 by implementing reviewed export, including
  refusal of unreviewed required fields and draft/unreviewed handling for
  `is_demo = true` decisions.

### Phase 6 — Final Exports

**Status:** [ ] todo  [ ] doing  [x] done

Excel workbook contract:

- **Client Template** — first sheet in the final workbook, exact columns from `EarningsSample (1).xlsx`:
  `Company Name`, `Quarter`, `Total revenue`, `Earnings per share`,
  `Net income`, `Operating income`, `Gross margin`, `Operating expenses`,
  `Buybacks and dividends`. Cells must be rendered in the template's sample-row
  format (currency as `$<n>B` strings, `Gross margin` as a decimal fraction in a
  `0%` cell, EPS as a number) via a format-mapping layer from the internal
  canonical value. See `docs/CLIENT_REPLY.md` and `docs/EVAL_SPEC.md`.
- **Metrics** — full normalized long table, one row per metric, all schema
  fields.
- **Review Decisions** — approved/rejected/needs-fix/not-applicable decisions,
  reviewer notes, and timestamps.
- **Evidence** — metric-to-source map with page and quote.

Export rule: final Excel requires `review_decisions.json`. If an explicit
`--allow-unreviewed` override exists, the workbook must be visibly marked as
draft/unreviewed.

"Reviewed" is keyed off resolved attention state, not the mere presence of a
decision row. This matters because `review.html` pre-fills untouched items with
the same auto-logic as demo (blank → `not_applicable`, attention → `needs_fix`,
otherwise → `approved`) and a human who clicks Download without reviewing
produces an `is_demo = false` file that silently auto-approved high-confidence
values. The export gate must therefore refuse (without `--allow-unreviewed`)
when any of these holds:

- the decisions file is `is_demo = true`;
- a required template field's source metric had `requires_attention = true`
  (low confidence, missing/placeholder evidence, failed check, or
  source-resolution failure) and was not given an explicit human resolution:
  `approved`, `rejected`, `needs_fix`, or `not_applicable` with a reviewer note;
- a required template field has no decision at all.

A bare `approved` without a reviewer note on an item that still carries
`requires_attention = true` does not count as resolved.

Unit tests required here:

- the canonical→client-cell format mapper (e.g. `22,496` USD millions → `"$22.5B"`;
  `17.2%` → `0.172` in a `0%` cell; EPS stays a number);
- the export contract: first-sheet headers match the template exactly, the
  `Metrics` / `Review Decisions` / `Evidence` tabs exist;
- the review gate: a demo (`is_demo = true`) decisions file is refused without
  the override; an `is_demo = false` file that left an attention-flagged required
  field at auto-`approved` is also refused; a genuinely resolved file exports;
  and the override always produces a visibly draft-marked workbook.

CLI target:

```bash
python -m earnings_extractor extract assesment_info --out outputs/run_001 --mode recorded
python -m earnings_extractor review outputs/run_001 --out outputs/run_001/review.html --demo-decisions outputs/run_001/review_decisions.json
python scripts/make_acceptance_decisions.py outputs/run_001 --out outputs/run_001/human_review_decisions.json
python -m earnings_extractor export outputs/run_001 --decisions outputs/run_001/human_review_decisions.json --out outputs/extractions.xlsx
python -m earnings_extractor inspect outputs/run_001/draft_metrics.json
python -m earnings_extractor extract assesment_info/TSLA-Q2-2025-Update.pdf --out outputs/test_tesla --mode recorded
python -m earnings_extractor eval --draft outputs/test_tesla/draft_metrics.json --document-id tesla_q2_2025
python -m earnings_extractor extract assesment_info/citi_earnings_q12025.pdf --out outputs/test_citi --mode recorded
python -m earnings_extractor eval --draft outputs/test_citi/draft_metrics.json --document-id citi_q1_2025
```

**Gate to advance to Phase 7 (all must be true):**

- `extractions.xlsx` opens; first-sheet columns AND cell formats match the template.
- `Metrics`, `Review Decisions`, and `Evidence` tabs exist.
- Export refuses `is_demo = true` files and required fields left at auto-`approved`
  while still `requires_attention = true`, unless `--allow-unreviewed` (which
  marks the workbook draft). Reviewed-ness is keyed off resolved attention state,
  not the presence of a decision row.
- Mapper + export-contract + review-gate unit tests pass.
- Every populated final metric retains source page + quote.

Current state:

- Done: `export` CLI writes reviewed Excel, JSON sidecar, and stem-scoped audit
  report artifacts.
- Done: first workbook sheet preserves the client template columns and sample-row
  formats, with currency rendered as `$<n>B`, EPS numeric, and gross margin as a
  decimal fraction in a `0%` cell.
- Done: export gate refuses demo decisions and unresolved attention-flagged
  required fields unless `--allow-unreviewed` marks the workbook draft.
- Done: audit tabs `Metrics`, `Review Decisions`, and `Evidence` preserve source
  page/quote and review state for every metric.
- Verified: demo export fails without override, draft override export succeeds,
  synthetic acceptance decisions export a true final workbook, full pytest is
  green, ruff is clean, and single-document recorded eval remains `9/9` for
  both Tesla and Citi.
- Next action: start Phase 7 by running final eval plus the best-effort unseen
  company smoke test and documenting the result.

### Phase 7 — Final Eval + Generalization Smoke Test

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. Run `eval --min-accuracy 0.9` across both sample companies from the extractor
   pipeline. The threshold flag is the hard gate: without it, `eval` remains
   report-only and exits `0`.
2. Run one best-effort unseen-company smoke test within v1 scope: an earnings
   report PDF or call transcript, not a 10-K/10-Q. There is no golden label, so
   document whether output and evidence look plausible.

**Gate to advance to Phase 8 (all must be true):**

- `eval` reports >=90% over scored template fields, from the pipeline (not
  golden-fed), with `--min-accuracy 0.9` enforcing the exit code.
- Reviewed export reproduces end-to-end.
- Unseen-company smoke-test result is documented (a pass is not required; honest
  notes on output and failure modes are).

Current state:

- Done: `eval --min-accuracy FLOAT` is implemented. It prints the same report as
  before, exits nonzero below the threshold, rejects thresholds outside
  `0.0..1.0`, and keeps omitted-threshold eval runs report-only with exit `0`.
- Done: the no-cheat boundary remains intact: runtime CLI code receives plain
  report text plus an accuracy float through `_eval_bridge.py`; no runtime module
  imports golden fixtures directly.
- Verified: reviewed sample flow reproduced end-to-end through
  `outputs/extractions.xlsx`, `outputs/extractions.json`, and
  `outputs/extractions.audit.md`.
- Verified: fresh recorded single-document evals with `--min-accuracy 0.9`
  exit `0` and report `9/9` (`100.0%`) for Tesla and `9/9` (`100.0%`) for Citi.
- Smoke-test blocker: no user-provided unseen PDF was present at
  `outputs/unseen_input/`, so live unseen extraction was not run and no unseen
  accuracy claim or golden fixture was added.
- Next action: start Phase 8 by tightening README/docs, cost notes, limitations,
  and production rollout narrative.

### Phase 8 — Documentation + Cost

**Status:** [ ] todo  [ ] doing  [x] done

Do:

- Write `README.md` with what it does, how to run with/without API key,
  review-first workflow, template output contract, accuracy measurement,
  limitations, and production path.
- Write `docs/SYSTEM_DESIGN_NOTES.md` with the scale story.
- Estimate cost per PDF and compare it against the confirmed manual baseline of
  `$3.33-$5.00/PDF`.
- Explain that hosted LLMs are client-approved for these public documents, while
  a future confidential deployment would revisit retention, access controls, and
  audit logging.

Scale story:

```text
local prototype -> batch worker -> queue-based ingestion -> object storage ->
model cascade -> consistency checks + human review queue -> reviewed final export ->
eval dashboard -> audit logs -> SSO/RBAC -> firm-wide rollout
```

**Gate to advance to Phase 9 (all must be true):**

- A stranger can run the full flow from the README alone, no extra context.
- A cost-per-PDF estimate is written and compared to the `$3.33-$5.00` baseline.
- Limitations and production path are documented with no overclaims.

Current state:

- Done: `README.md` now explains the recorded/offline quickstart, optional live
  mode, review-first workflow, output artifacts, eval gate, limitations, and
  production path.
- Done: live extraction records OpenAI usage in `draft_metrics.json.llm_usage`
  while recorded drafts keep `llm_usage = []`; older draft artifacts remain
  schema-compatible.
- Done: measured Tesla live run with `gpt-5.4-mini` used `6,422` input tokens
  and `1,967` output tokens. At OpenAI's documented `$0.75 / 1M` input and
  `$4.50 / 1M` output token rates, this is `$0.013668/PDF`, about `244x-366x`
  below the confirmed `$3.33-$5.00/PDF` analyst baseline.
- Done: `docs/SYSTEM_DESIGN_NOTES.md`, `docs/LLM_VS_PIPELINE.md`,
  `html/llm_vs_pipeline.html`, and `docs/VERIFY.md` now align with the measured
  cost and current recorded/live A/B results.
- Verified: recorded combined extraction/review/export reproduced; Tesla and
  Citi `--min-accuracy 0.9` evals both reported `9/9`; full pytest, ruff, and
  project-map checks passed.

### Phase 9 — Final Audit + Submit

**Status:** [ ] todo  [ ] doing  [x] done

Do:

1. Re-read as evaluator; remove overclaims.
2. Run every command in `docs/VERIFY.md`.
3. Check no secrets and no junk files.
4. Rehearse interview-defense answers.
5. Submit through Ashby or cloud link.

**Submission gate (all must be true):**

- Every command in `docs/VERIFY.md` prints passing output.
- `pytest` green, `ruff` clean, `eval` >=90%.
- No secrets, no junk; repo committed; README runnable by a stranger.
- Interview-defense answers rehearsed.

Current state:

- Done: audited committed Phase 8 state at `5a2c1b8`; README puts the
  keyless recorded quickstart before optional live mode.
- Verified: install gate passed with `.venv/bin/python -m pip install -e
  ".[dev]"`; local shell has no bare `python` alias, so `python3 -m venv .venv`
  was used to create the venv.
- Verified: recorded combined extraction, inspection, review artifact
  generation, synthetic acceptance decisions, and reviewed export all passed.
- Verified: Tesla and Citi recorded evals with `--min-accuracy 0.9` both
  reported `9/9` (`100.0%`).
- Verified: `.venv/bin/python -m pytest -q` passed (`76 passed, 5 warnings`);
  `.venv/bin/python -m ruff check .`, `python3 scripts/update_project_map.py
  --check`, and `git diff --check` passed.
- Verified: `outputs/extractions.xlsx` first sheet is `Client Template` with the
  exact client headers, and `Metrics`, `Review Decisions`, and `Evidence` tabs
  exist.
- Verified: secret scan found only the README placeholder `OPENAI_API_KEY=...`
  and the test fake key `api_key="test-key"`; `.env`, `.venv/`, `outputs/`,
  caches, and egg-info remain ignored.
- Submission path: push final repo state and submit through the Ashby link (or
  cloud link by email) from the original assignment email.

### Phase 10 — Web UI (Additive Stretch)

**Status:** [ ] todo  [ ] doing  [x] done (pragmatic draft UI)

**Built now (the pragmatic web shell):** a Next.js UI plus Python API wrapper
around the existing extractor/export code. The UI supports keyless sample mode
for Tesla/Citi, uploaded PDFs when `OPENAI_API_KEY` is available server-side,
metric/source/review-flag rendering, and download of an explicitly marked
unreviewed draft workbook via `allow_unreviewed=True`.

- `app/` — Next.js App Router UI: mode selector, PDF upload, sample buttons,
  metric table with source quotes and review flags, and Excel draft download.
- `api/process.py` — Vercel Python function candidate. It writes uploaded/sample
  PDFs to `/tmp`, calls `earnings_extractor.pipeline.extract()`, creates
  synthetic draft decisions, calls `export_reviewed_run(...,
  allow_unreviewed=True)`, and returns metrics plus a base64 workbook.
- `scripts/web_api_server.py` — local/split-backend fallback exposing the same
  `/api/process` shape for development or Render/Railway/Fly deployment.
- `vercel.json`, `requirements.txt`, `package.json` — deployment/runtime config.

**Verified:** direct Python API sample mode returned 9 Tesla metrics and 9 Citi
metrics with `Client Template DRAFT` workbooks; browser click-through for Tesla
sample rendered metrics and downloaded a draft workbook; live Citi upload through
the API returned 9 metrics, a valid draft workbook, and OpenAI usage; `npm run
build` and `ruff check .` passed.

**Deployment note:** Vercel CLI 54 served the Next app locally but returned a
Next 404 for `/api/process` in the mixed Next/Python project. The code keeps the
Vercel Python function candidate, but the documented reliable path is the split
backend fallback: run/deploy the Python API separately and set
`PYTHON_API_BASE_URL` for the Next frontend.

**Built ahead of schedule (the reusable foundation, step 5 + part of step 1):**
The deterministic citation locator and a standalone read-only viewer exist now,
because the user asked specifically for pixel-accurate highlighting. This is
additive and does not change extraction, schema, or recorded mode.

- `earnings_extractor/locate.py` — `locate_evidence_bbox(pdf, page, quote) ->
  EvidenceLocation`. PyMuPDF `search_for` with chunked + numeric-anchor
  fallbacks; returns rects in PDF points plus page size for zoom-independent
  scaling. Covered by `tests/test_locate.py`.
- `scripts/build_citation_viewer.py` — generates a self-contained HTML per draft
  (PDF embedded, PDF.js from CDN): source PDF on the left, citations on the
  right, click-to-highlight on the exact supporting text.

**Still NOT done (the full interactive review app):** inline accept/edit/reject
decisions, generated human review decisions, and gated export of a reviewed
workbook. The web UI deliberately downloads a visibly unreviewed draft workbook.

The original full interactive review app remains future work. The whole point is
still additive: a thin presentation layer over the existing deterministic
pipeline, not a rewrite. The CLI + static `review.html` submission stands on its
own if this stretch layer is not deployed.

Future full-review goal: a single interactive tool where a user uploads/pastes a
PDF, watches each pipeline stage run step by step (ingest -> classify -> extract
-> normalize -> validate), reviews citations inline, and exports the final
reviewed workbook — all in one browser session, driving the same code the CLI
uses.

Future full-review work:

1. **Prerequisite refactor (deferred from Phase 5):** split the monolithic
   `extract()` in `earnings_extractor/pipeline.py` into:
   - `build_draft(input, mode, on_progress=None) -> DraftRun` — pure, in-memory
     computation that optionally calls an `on_progress` callback after each stage.
   - `write_draft(draft, out_dir) -> Path` — persistence only.
   The CLI becomes `write_draft(build_draft(...), out_dir)`, behavior-preserving
   and fully covered by existing tests. This is the seam the UI needs; without it
   the UI cannot show per-stage progress or render an in-memory draft.
2. Add an upload shim so a browser-uploaded PDF (bytes) is saved to a temp path
   and fed through `find_pdf_inputs` / `build_draft` without changing core logic.
3. Build a thin backend (FastAPI or Streamlit) that:
   - accepts a PDF upload,
   - calls `build_draft` with an `on_progress` callback and streams each stage to
     the UI,
   - renders the resulting `DraftRun` with the same evidence/review fields as
     `review.html`,
   - reuses the existing Phase 5 `review` artifacts and Phase 6 `export` functions
     for review decisions and final Excel — no duplicated extraction/export logic.
4. Keep the deterministic, auditable guarantees intact: the UI must surface
   confidence, source page, source quote, review reason, and review status, and
   must respect the same Phase 6 review gate — reviewed-ness keyed off resolved
   attention state, not the presence of a decision row, so an attention-flagged
   required field left at auto-`approved` cannot export without the
   visibly-marked override.
5. **Pixel-perfect citation highlighting (confirmed requirement).** The desired
   final form is a split view: the source PDF stays rendered on the left; the
   extracted fields/citations are on the right; clicking a citation jumps the PDF
   to the cited page and draws an exact highlight over the supporting text. To
   support this:
   - Add a **deterministic** locator, e.g.
     `locate_evidence_bbox(pdf, page, quote) -> list[Rect]`, that finds the
     quote's bounding rectangle(s) on the cited page using a PDF text library
     (PyMuPDF `fitz` or `pdfplumber`, both expose per-word/per-span bbox in PDF
     coordinate space). The LLM never produces coordinates; bbox is *derived*
     from the already-stored page + quote + the original PDF, so this requires
     **no change to extraction** and does not affect recorded/cassette mode.
   - Reuse this locator inside the existing validator's "quote is on the cited
     page" check so coordinate-finding and presence-checking share one code path.
   - Render the PDF with PDF.js and scale the stored rects onto the rendered
     viewport so the highlight lines up at any zoom. A multi-line quote yields one
     rect per line — that is expected and still pixel-accurate.
   - The bbox can be computed lazily in the UI from the draft + PDF, or persisted
     as an optional derived field on the metric row; either way it is additive and
     backward-compatible with existing drafts (no required schema change).

**Gate (all must be true):**

- The UI runs the *same* pipeline functions as the CLI; no extraction or export
  logic is reimplemented in the web layer.
- Uploading a sample PDF shows each stage progressing and ends in a reviewable
  draft with full evidence.
- Clicking a citation traces to the exact location in the rendered PDF (correct
  page, highlight aligned to the supporting text) without leaving the page.
- Review decisions made in the UI produce a valid `review_decisions.json` and a
  final workbook that matches the Phase 6 contract.
- All existing CLI tests and eval still pass unchanged (proves the refactor was
  behavior-preserving), and the new bbox locator is covered by a deterministic
  unit test (known quote -> expected page + non-empty rect).

---

## 7. Optional

The interactive upload-to-export web UI is now tracked as **Phase 10**
(post-submission stretch), not a loose optional. Still genuinely optional, only
after Phase 10 itself is green: PDF page previews and persistent reviewer
accounts. A simple local `review.html` remains part of the core workflow, not
optional.

### Deferred — do only if a real document forces it (don't pre-build)

- **LLM fallback for classification on `unknown` documents.** Skip unless you see
  a real misclassification. A fail-safe ("if `unknown` but full of numbers, treat
  as report and try anyway") is a cheaper first defense.
- **A second LLM verifier pass.** A real accuracy improvement but a Phase 7
  accuracy-push tool, not now; adding it early is wasted effort against a moving
  target. (Note: bounding-box citation highlighting is no longer deferred — it is
  now a confirmed Phase 10 requirement, step 5, since it is part of the desired
  final form. It remains additive and deterministic, so it stays post-submission.)

---

## 8. Timeline

Received Thursday 2026-05-28 around 12:23pm. Best signal is fast and
defensible, not rushed.

- **Fri:** client questions answered; docs updated; scaffold and data/eval work.
- **Sat:** core extraction, validation, recorded mode, review UI, reviewed export.
- **Sun:** README, cost/system-design notes, final verification, submission if
  green.

---

## 9. Verification Rule

A phase is not done because code exists. It is done when there is concrete
evidence: passing command output, generated files, source-backed extracted
values, or a documented review.

Run the relevant part of `docs/VERIFY.md` after each meaningful change.

---

## 10. Interview-Defense Prep

- Why a deterministic pipeline instead of a runtime agent?
- Why LLM extraction plus deterministic validators?
- How do I know it is not hardcoding the answers?
- Why make extraction review-first before final Excel?
- Why preserve the client's Excel template as the final first sheet?
- Why add review/evidence artifacts instead of only returning the template?
- How does the system handle missing or inapplicable fields?
- How is 90% accuracy measured?
- How does it handle both table-heavy reports and transcript prose?
- How would the eval set grow beyond Tesla/Citi?
- Why is hosted LLM processing acceptable here?
- What changes for confidential data?
- What does it cost per PDF versus analyst labor?
- What is the next two-week production sprint?

---

## 11. Pre-Submission Checklist

- `extract`, `review`, `export`, `inspect`, `eval`, `pytest`, and `ruff` pass.
- Unit tests cover the deterministic logic (normalization, consistency checks,
  canonical→client-cell mapper, schema validation/repair), not just imports.
- Recorded/offline mode works with no API key.
- Draft extraction artifacts exist: `draft_metrics.json`, `review_queue.json`,
  `evidence_report.md`, and `review.html`.
- Reviewed final Excel opens and first sheet matches the client template.
- JSON is readable and audit report explains evidence + review decisions.
- Every populated final metric has source page, quote, confidence, and review status.
- Unsupported or uncertain template cells are blank/review-flagged, not guessed.
- Eval reports >=90% from the extractor pipeline with `--min-accuracy 0.9`, not
  golden-fed shortcuts.
- Best-effort unseen-company smoke test is documented.
- README is runnable by a stranger.
- Cost estimate compares to `$3.33-$5.00/PDF` manual baseline.
- No secrets committed; `.env.example` exists.

---

## 12. Decision Log

> Format: `YYYY-MM-DD-HH-MM — decision / client answer / assumption changed`

- 2026-06-03-02-07 — Added a simpler README-first live batch run guide for
  fresh users: install, configure `.env`, place PDFs in `pdf_input_copy`, run
  the requested batch command, find `outputs/pdf_input_batch/extractions.xlsx`,
  and check details in the workbook's `Extraction Draft` tab.
- 2026-06-03-02-58 — Fixed two live-batch reliability misses with deterministic
  evidence checks instead of LLM arbitration. Currency normalization now uses
  explicit source-quote amounts to avoid double-scaling values the model already
  converted to USD millions, covering American Express `$13.9 billion` operating
  expenses -> `13900` USD millions. Capital-return enrichment now scans
  normalized page text and appends a nearby quarterly dividend-per-share fact to
  returned-capital/repurchase wording, covering BlackRock Q4 2025's `$5.0
  billion` returned, `$1.6 billion` repurchases, and `$5.73` per-share dividend.
  Added regression tests and verified with focused tests, full `pytest`, `ruff`,
  recorded Tesla/Citi evals, and the recorded review/export gate.
- 2026-06-03-03-21 — Made CLI batch output visibly review-first without
  crowding the terminal. The workbook now opens on `Review Instructions`, then
  `Extraction Draft`, `Review Queue`, and `Batch Status`; the queue includes
  status/reason/source page/source quote for every template field. The CLI
  prints only run progress, processed/skipped/failed counts, workbook path, and
  a pointer to the review tabs. Verified with full `pytest`, full `ruff`, and a
  recorded batch run to `outputs/batch_review_check/extractions.xlsx`.
- 2026-06-03-00-00 — Added live progress output to the `batch` CLI.
  `run_batch` now accepts an optional progress callback, and `process_single_pdf`
  reports compact per-file stages: started, reading PDF, classifying, extracting,
  preparing template rows, resolving company, normalizing, citation checking,
  validating, done, and workbook writing.
- 2026-06-03-00-20 — Changed batch extraction policy so deterministic document
  classification is advisory, not a hard reject gate. If classification is
  uncertain, live extraction now proceeds with an `earnings_report` fallback
  rather than silently skipping a plausible PDF; deterministic code still ranks
  pages and validates evidence/math after the model returns. Added regression
  coverage for uncertain-classification extraction and earnings-results press
  releases.
- 2026-06-03-00-35 — Simplified document-type handling further: operational
  extraction no longer changes behavior based on report-vs-transcript
  classification. User-provided batch PDFs are treated as in-scope earnings
  sources, and the same nine client fields are extracted with a generic
  `earnings_report` schema hint. Classification remains audit metadata/page
  context only; post-extraction validation/review flags remain the reliability
  layer.
- 2026-06-03-00-45 — Added a live-extraction heartbeat around the blocking
  OpenAI call. The CLI cannot show a truthful percent complete inside a single
  API request, so it now prints elapsed-time "still extracting metrics" updates
  every 2 seconds during live extraction.
- 2026-06-03-01-00 — Fixed same-company batch export grouping. Each extracted
  metric now persists its exact input `source_file`, and review/export uses that
  value before falling back to older inference. Added regression coverage that
  two BlackRock PDFs with different quarters export as two client rows instead
  of collapsing into one company row.
- 2026-06-03-01-15 — Hardened live structured-output repair. Empty or missing
  `source_quote` / `source_page` no longer fails an entire PDF; the affected row
  is blanked, assigned placeholder evidence/page metadata, and flagged for
  review so batch export can continue without silently claiming unsupported
  values. This keeps the deterministic reliability layer after the LLM while
  preserving one-row-per-PDF workbook output.
  Verified with `.venv/bin/python -m pytest tests/test_scaffold.py
  tests/test_cli_phase3.py -q`, `.venv/bin/python -m ruff check
  earnings_extractor/batch.py earnings_extractor/cli.py
  earnings_extractor/pipeline.py`, and a recorded one-PDF batch run.
- 2026-06-01-11-19 — Removed product-facing "demo" positioning from the web app
  and README surfaces. The deployed app is now linked as
  `https://project-ee-one.vercel.app/`, the UI presents bundled Tesla/Citi files
  as samples, and internal `is_demo` / `--demo-decisions` wording remains only
  where it protects export integrity for synthetic verification decisions.
- 2026-06-01-06-00 — Built Phase 11, the full review-first web UI, as a thin
  shell over the tested core. New serverless functions `api/extract.py` (one PDF
  per call → draft + template metrics + deterministic evidence rects from
  `locate.py`) and `api/export.py` (merges per-document drafts into one
  `DraftRun`, maps reviewer decisions onto canonical `metric_id`s by
  `(document_index, local metric_index)`, runs the real gated
  `export_reviewed_run(allow_unreviewed=False)` → one workbook). Shared logic
  lives in `scripts/web_lib.py`; no extraction/locate/review/normalize/export
  logic was reimplemented in TS. `app/page.tsx` is now multi-step: multi-PDF
  upload (or keyless Tesla+Citi demo) → per-document review with a PDF.js
  source-highlight overlay (citation-viewer math), inline value editing, and
  per-metric approve/edit/reject/N-A decisions → gated Excel export + download.
  `next.config.ts` rewrite generalized to `/api/:path*`; demo PDFs copied to
  `public/demo/`. Verified: `pytest` 80 passed (76 core unchanged + 4 new
  `tests/test_web_lib.py`), ruff clean, `tsc --noEmit` clean, and an in-process
  extract→edit→merge→gated-export run produced a final (non-draft) workbook with
  the edited Tesla revenue ($100B) and Citi ($21.6B) in one sheet. The Next
  production build + Vercel deploy + browser smoke test are run by the user on
  their machine (the sandbox cannot fetch the platform SWC binary).
- 2026-06-01-00-37 — Completed Phase 9 final audit. Full recorded gate passed:
  combined extract/inspect/review/export, Tesla `9/9`, Citi `9/9`, pytest
  `76 passed`, ruff clean, project map current, workbook template/audit tabs
  spot-checked, and secret scan clean except expected README/test placeholders.
- 2026-06-01-00-00 — Completed Phase 8 documentation + measured cost. Added
  live OpenAI token usage capture to draft artifacts, measured a Tesla live run
  at `$0.013668/PDF` (`6,422` input, `1,967` output, `223` reasoning tokens
  included in output), and updated README/system-design/comparison/verify docs
  with the `$3.33-$5.00/PDF` analyst baseline comparison. Recorded A/B now
  documents `15/18 -> 18/18`; live supporting A/B documents `12/18 -> 17/18`.
- 2026-05-31-16-00 — Built the Phase 10 foundation slice ahead of schedule: a deterministic citation locator (`earnings_extractor/locate.py`, PyMuPDF) and a standalone read-only viewer generator (`scripts/build_citation_viewer.py`) that renders the source PDF with click-to-highlight pixel-accurate citations. Additive only — no change to extraction, schema, or recorded mode; added `pymupdf>=1.24` to `pyproject.toml` and `tests/test_locate.py`. The full interactive upload→pipeline→review→export app (Phase 10 steps 1–4) remains post-submission. Detail in `docs/DECISIONS.md`.
- 2026-05-31-15-00 — Robustness pass for "any document, any company." Made three deterministic generalization fixes after an unseen-company (Netflix) smoke test failed with "No supported earnings PDFs found": (1) `classify_document` scans all pages for financial-statement markers so back-loaded shareholder letters classify as `earnings_report`; (2) transcript detection now requires structural evidence (speaker turns + `operator:`) or an earnings-call phrase backed by >=2 speaker turns, so a report that merely mentions a call no longer flips to transcript; (3) normalization handles the `thousands`/`k` scale. Covered by new tests in `test_classify.py` and `test_phase4_processing.py`. Chose deterministic fixes over a full review-all-pages LLM rewrite to preserve recorded-mode reproducibility, the keyless eval gate, and the sub-$5/PDF cost story. Third golden doc (Netflix) deferred pending a validated live cassette and confirmation of two judgment-call labels (gross margin, operating expenses). Detail in `docs/DECISIONS.md`.
- 2026-05-31-14-00 — Completed Phase 7 final eval gate. The `eval` CLI now enforces `--min-accuracy 0.9` by exit code while preserving report-only behavior when omitted; reviewed export reproduced end-to-end; Tesla and Citi fresh recorded evals both remained `9/9`; unseen smoke test is blocked until a user-provided v1-scope PDF exists under `outputs/unseen_input/`.
- 2026-05-31-13-00 — Completed Phase 6 reviewed final exports. The `export` CLI now enforces review decisions, refuses demo/unresolved attention states without `--allow-unreviewed`, writes template-compatible Excel plus JSON/audit artifacts, and preserves `Metrics`, `Review Decisions`, and `Evidence` tabs.
- 2026-05-31-12-00 — Completed Phase 5 human review artifacts. The `review` CLI now writes `review_queue.json`, `evidence_report.md`, `review.html`, and optional demo `review_decisions.json`; combined recorded review output contains 21 Tesla/Citi metrics with source file/page/quote, confidence, review reason, and decision controls.
- 2026-05-30-00-30 — Completed Phase 4 validation + recorded mode. Recorded extraction runs keyless, normalizes values to canonical units, completes all template rows with schema-safe unsupported placeholders, resolves company identity from full-document text/metadata, and runs a real Tesla OCF-capex=FCF check. Single-document recorded eval is `9/9` for Tesla and `9/9` for Citi; combined draft is inspectable but not used for Phase 4 eval until scoring is document-scoped.
- 2026-05-29-18-30 — Completed Phase 3 core pipeline. Live Tesla extraction writes `draft_metrics.json`; `inspect` summarizes the draft; `eval` reports an honest `8/9` (`88.9%`) score through the eval-only harness. Recorded/offline mode remains Phase 4.
- 2026-05-29-17-30 — Completed Phase 2 input/eval target foundation. Added `pdfplumber` page ingestion, whitespace-normalized evidence checks, `evaluation/` golden fixtures, tolerance constants, and `docs/DATA_INSPECTION.md`.
- 2026-05-29-17-00 — Inspected the template's sample data row (Amazon LLC). It dictates client-cell formats the docs previously missed: currency columns are `$<n>B` text strings, `Gross margin` is a decimal fraction in a `0%` cell (so 17.2% → 0.172, not 17.2), EPS is a plain number. Added a format-mapping layer requirement to export and a two-layer (internal canonical vs client cell) rule to the eval. Recorded in `docs/CLIENT_REPLY.md` and `docs/EVAL_SPEC.md`.
- 2026-05-29-16-00 — Updated product flow to review-first: draft extraction with citations, human review decisions, then final Excel export. The citation review layer is now core, not optional UI polish.
- 2026-05-29-15-30 — Andrew confirmed the Excel template, manual review preference, v1 document scope, hosted LLM approval, and cost baseline. Updated plan: first Excel sheet must match `EarningsSample (1).xlsx`; added audit tabs remain as production value.
- 2026-05-29 — Adopted verification-driven, eval-first method with a single lead agent + trimmed file set. Distinguished dev-method from product-architecture. Full decision detail lives in `docs/DECISIONS.md`.
- 2026-05-29 — Confirmed golden numbers against the PDFs. Committed to real-LLM extractor + recorded-response offline mode. One email to Andrew, CC Adam + Rick.
