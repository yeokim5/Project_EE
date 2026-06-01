# DATA_INSPECTION.md — Phase 2 Input Findings

Phase 2 confirms the provided sample inputs are machine-readable enough to build
the deterministic review-first pipeline.

## PDF Text Readability

PDF text is extracted with `pdfplumber` through
`earnings_extractor.ingest.read_pdf_pages`.

Page-number convention:

- Use `pdfplumber.Page.page_number`.
- Treat page numbers as 1-based physical PDF pages.
- This convention matches the page references in `docs/EVAL_SPEC.md`.

Evidence matching convention:

- Collapse whitespace in both extracted page text and expected quotes before
  matching: `re.sub(r"\s+", " ", text).strip()`.
- Check that a cited quote appears on the cited page.
- Do not require a quote to appear only on one page; repeated values can be
  legitimate in summaries and statements.

Verified source readability:

- Tesla `TSLA-Q2-2025-Update.pdf`: page 1 contains `Q2 2025 Update`; page 4
  contains the financial summary rows used for revenue, EPS, net income,
  operating income, gross margin, operating expenses, operating cash flow, and
  free cash flow.
- Citi `citi_earnings_q12025.pdf`: page 1 contains the transcript title; page 2
  contains capital return and CET1 evidence; page 3 contains firmwide revenue,
  EPS, net income, RoTCE, expenses, and cost of credit evidence.

## Excel Template Contract

`assesment_info/EarningsSample (1).xlsx` has one worksheet with the exact client
headers:

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

The sample row confirms the display contract:

- Currency template columns are text strings in `$<n>B` form.
- `Earnings per share` is numeric with currency number format.
- `Gross margin` is a decimal fraction in a `0%` cell.
- `Buybacks and dividends` is free text.

Run the check:

```bash
.venv/bin/python scripts/inspect_phase2_inputs.py --check
```
