# REVIEW_WORKFLOW.md — Human Review Before Final Excel

The core product flow is review-first. Extraction produces a draft with
citations; a human approves, rejects, or annotates the extracted values; final
Excel export happens after that review.

## Why This Is The Product

The client is replacing manual analyst entry, not asking for an opaque black-box
spreadsheet generator. The trust unlock is:

```text
value + exact page + source quote + confidence/check status + human decision
```

This makes the system believable for financial workflows. The UI can be simple;
the review gate is the important product behavior.

## CLI Flow

### 1. Draft extraction

```bash
python -m earnings_extractor extract assesment_info --out outputs/run_001 --mode recorded
```

Creates:

```text
outputs/run_001/draft_metrics.json
outputs/run_001/review_queue.json
outputs/run_001/evidence_report.md
outputs/run_001/review.html
```

`draft_metrics.json` contains all extracted values, source pages, quotes,
confidence, validation status, and review flags. It is not the final client
workbook.

### 2. Human citation review

```bash
python -m earnings_extractor review outputs/run_001 --out outputs/run_001/review.html
```

The review UI should let the reviewer see each extracted value beside:

- source PDF;
- source page;
- source quote;
- confidence;
- validation/check status;
- review reason.

Reviewer decisions:

```text
approved
rejected
needs_fix
not_applicable
```

The UI writes or downloads:

```text
outputs/run_001/review_decisions.json
```

For a simple first implementation, `review.html` may be a local static HTML file
that lets the reviewer filter, inspect citations, and download
`review_decisions.json`. A richer Streamlit/FastAPI UI is optional later.

### 3. Final Excel export

```bash
python -m earnings_extractor export outputs/run_001 --decisions outputs/run_001/review_decisions.json --out outputs/extractions.xlsx
```

Creates:

```text
outputs/extractions.xlsx
outputs/extractions.json
outputs/audit_report.md
```

Final export should refuse required unreviewed fields unless the user passes an
explicit override such as:

```bash
--allow-unreviewed
```

If an override is used, the resulting workbook must be clearly marked as draft
or unreviewed.

## Review Decision Schema

Each decision should include:

```text
metric_id
review_status
reviewer_note
reviewed_at
reviewer
```

Allowed `review_status` values:

- `approved` — reviewer accepts value and evidence.
- `rejected` — reviewer says the value is wrong and should not export.
- `needs_fix` — reviewer found an issue that requires correction before final.
- `not_applicable` — field should remain blank because the metric is not
  meaningful or not supported for this company/document.

## Export Rules

- Approved values can appear in the client template and final metrics tab.
- `not_applicable` fields should remain blank in the client template and appear
  in the review/audit trail.
- `rejected` and `needs_fix` values should not appear as final values.
- Every final populated value must retain source page and quote in the audit
  artifacts.
- Low-confidence values can export only if a human explicitly approved them.

## Automated Verification Without Hiding The Human Step

The repo still needs deterministic commands reviewers can run. For recorded/demo
mode, it is acceptable for the review command to generate a clearly labeled demo
`review_decisions.json` from the draft artifacts:

```bash
python -m earnings_extractor review outputs/run_001 --out outputs/run_001/review.html --demo-decisions outputs/run_001/review_decisions.json
```

This is acceptable as long as:

- it is clearly labeled as demo review state;
- it is not imported by extraction code;
- the eval score still comes from extracted draft values compared against eval
  fixtures;
- the README explains that production use requires real human review.
