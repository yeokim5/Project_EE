# AGENTS.md — Instructions for any coding agent in this repo

These are **stable** rules. Do not auto-edit this file. Update it manually only when a rule is durable (or the same correction has come up 3+ times). One-off notes go in the worklog, not here.

## What this project is

A review-first tool that extracts standard financial metrics (revenue, net income, EPS, etc.) from earnings PDFs into a draft dataset, validates them, attaches source evidence, flags uncertain values, lets a human review/approve citations, and only then exports the client Excel workbook plus JSON/audit artifacts. Target: ≥90% field-level accuracy, believable enough to roll out firm-wide. Full context is in `docs/PROCESS.md`; client-confirmed requirements are in `docs/CLIENT_REPLY.md`; scoring rules and golden-value boundaries are in `docs/EVAL_SPEC.md`; review workflow is in `docs/REVIEW_WORKFLOW.md`.

## Two architectures — do not conflate them

- **Development method (how we build):** agentic, verification-driven (see below).
- **Product architecture (what runs at inference):** a **deterministic review-first pipeline with constrained, structured LLM extraction calls** (plural is fine — e.g. per-page/per-chunk — but orchestration is fixed code, not agent-decided), NOT a runtime agent. Stages: ingest → classify (table vs narrative) → LLM extract to structured JSON + evidence → normalize units/scale → deterministic validation + consistency checks → confidence / review-flag → human review decision → final export. Keep the runtime deterministic, testable, and auditable.

## Development method: verification-driven, eval-first

Follow this loop for every task:

```
Explore (read-only) → Plan → Implement on a branch → Verify (run docs/VERIFY.md)
→ fresh-context Review (docs/REVIEW.md) → iterate until the gate is green → human approves
```

- **Eval-first.** The golden-metrics eval is the verification target. Build/extend it BEFORE or alongside extractor changes so every change is scored immediately. Never write extractor logic without a way to score it.
- Start non-trivial work in plan mode; get the plan approved before editing.
- Use subagents sparingly — only for (a) fresh-context diff review and (b) the unseen-company generalization test. No agent swarms.

## Never do

- **Never hardcode or read golden/expected values inside the extractor.** Golden values live only in the eval module and must never be importable by extraction code. Accuracy must come from the real LLM path.
- Never commit secrets. API keys go in `.env` (gitignored); ship `.env.example` only.
- Never claim a metric without `source_page` + `source_quote`.
- Never let a low-confidence or consistency-check-failing value pass silently — set `needs_review = true`.
- Never create a final client Excel workbook from unreviewed extraction output unless the command explicitly marks it as draft/unreviewed. The normal path is draft extraction → human review decisions → final export.
- Never silently widen scope. Out-of-scope ideas go to the "even better with more time" section of the README, not into the code.

## Build / run commands

```bash
# install
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

# draft extraction (offline/recorded mode works with no API key)
python -m earnings_extractor extract assesment_info --out outputs/run_001 --mode recorded
python -m earnings_extractor inspect outputs/run_001/draft_metrics.json

# human review + final export
python -m earnings_extractor review outputs/run_001 --out outputs/run_001/review.html --demo-decisions outputs/run_001/review_decisions.json
python -m earnings_extractor export outputs/run_001 --decisions outputs/run_001/review_decisions.json --out outputs/extractions.xlsx

# the gate
python -m earnings_extractor eval
```

Exact, current gate commands live in `docs/VERIFY.md` — always run those before calling a task done.

## Output contracts

### Draft extraction artifacts

`extract` writes draft artifacts, not final client Excel:

```
draft_metrics.json · review_queue.json · evidence_report.md · review.html
```

### Client Excel template — final export first sheet

After human review, the first worksheet in the final export must match the client template columns exactly:

```
Company Name · Quarter · Total revenue · Earnings per share · Net income ·
Operating income · Gross margin · Operating expenses · Buybacks and dividends
```

Match the template's **cell formats**, not just the headers (see the sample row
in `docs/CLIENT_REPLY.md`): currency columns are `$<n>B` text strings (e.g.
`"$22.5B"`), `Gross margin` is a decimal fraction in a `0%` cell (17.2% → `0.172`,
never `17.2`), EPS is a plain number. Map from the internal canonical value
(USD millions / percentage points) to these display formats at export time.

### Internal metric schema — draft JSON / review UI / audit tabs

The normalized JSON output and audit-oriented Excel tabs use this richer schema:

```
company · ticker · document_type · fiscal_period · report_date ·
metric_name · metric_category · segment · value · unit · scale · period ·
gaap_or_non_gaap · year_over_year_change · source_page · source_quote ·
confidence · needs_review · review_reason · review_status · reviewer_note
```

## Conventions

- Python, typed where reasonable, small focused modules.
- LLM calls return structured JSON validated against the schema; reject/repair malformed output, don't trust it blindly.
- Tesla reports in millions, Citi states billions in prose → always normalize unit/scale to one canonical form (USD millions internally), then render to the client template's display format (`$<n>B` strings, gross margin as decimal fraction) only at export.

## After each task (soft auto-update)

At the end of a task:
1. Append to `docs/DECISIONS.md` ONLY if you made a real architectural choice with a tradeoff (format: decision / alternatives / reason / consequences). Don't log routine edits.
2. Update the relevant phase status + worklog line in `docs/PROCESS.md`.
3. Propose an AGENTS.md change ONLY if the new rule is durable — do not edit it automatically.
