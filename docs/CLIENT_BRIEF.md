# CLIENT_BRIEF.md — Confirmed Assignment Brief

## Problem

The client has analysts reading public earnings reports and earnings-call
transcripts, then entering standard financial metrics into Excel by hand. This
manual process is expensive and should be automated where possible.

## Success Target

- At least 90% field-level accuracy before deployment.
- Source evidence for every populated metric.
- Human review for extracted values, especially uncertain, unsupported, or
  inconsistent values.
- Output that fits the client's existing Excel workflow after review.

## Confirmed Client Answers

Andrew replied on 2026-05-29:

- The team has an Excel template: `assesment_info/EarningsSample (1).xlsx`.
- Manual review flags for uncertain values are a good idea.
- V1 only needs to support earnings report PDFs and earnings-call transcripts.
- Hosted LLM APIs are acceptable for the assignment because the data is public.
- There are no current restrictions on where the data can be processed.
- Analyst cost baseline is about `$50/hour` at `10-15 PDFs/hour`, or roughly
  `$3.33-$5.00/PDF`.

Source: `docs/CLIENT_REPLY.md`.

## Required Client Template Columns

The first sheet of the exported workbook should match these columns:

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

Draft extraction should create review artifacts before final Excel export. After
human review, additional sheets may be added for auditability:

- `Metrics` — normalized long-table output.
- `Review Decisions` — reviewer approvals/rejections/not-applicable decisions.
- `Evidence` — source page and source quote for each populated metric.

## Provided Inputs

- `EarningsSample (1).xlsx` — confirmed client template.
- `citi_earnings_q12025.pdf` — narrative earnings-call transcript.
- `TSLA-Q2-2025-Update.pdf` — table-heavy earnings report/update deck.

## Working Assumptions

- The provided documents are public earnings materials.
- Missing or inapplicable fields should be review-flagged, not guessed.
- The extractor may use hosted LLM calls in live mode and recorded responses in
  offline reviewer mode.
- The internal schema may be richer than the client template, but exports must
  map cleanly into the template.
- Final client Excel should be generated from reviewed extraction output, not
  raw unreviewed draft output.
