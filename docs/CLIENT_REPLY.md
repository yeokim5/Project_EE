# CLIENT_REPLY.md — Client Reply Evidence

This records the user-provided email reply from Andrew on 2026-05-29. Keep this
as source evidence for client-confirmed requirements referenced elsewhere in the
docs.

## Key Reply

From Andrew Stahlman:

```text
I've attached the template that the team uses.

> If the system is not confident about a value, would you want a manual review step?

That sounds like a good idea to me.

> Are those the main document types the team usually works with

Yes, the earnings report PDFs and the call transcripts are all we need to support for now.

> Is it acceptable to use a hosted LLM API for this assignment?

Yes, the earnings reports are public data so using an externally hosted LLM is fine.

> are there any security, compliance, or budget constraints I should keep in mind

We don't have any restrictions on where the data can be processed. re: cost,
we'll be benchmarking against the labor cost of our analysts. Our analysts cost
~$50/hr and can process 10-15 PDFs per hour.
```

## Attached Template

The attached workbook is stored at:

```text
assesment_info/EarningsSample (1).xlsx
```

It has one worksheet (`Sheet1`) with these columns:

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

### Sample data row (ground truth for cell format)

The template ships with one example row. This dictates the expected cell
**format** of each client-facing column, not just the header text. Inspected
directly from the file:

```text
Company Name            : "Amazon LLC"                   (text)
Quarter                 : "Q1 2025"                      (text)
Total revenue           : "$150B"                        (text string, $ + billions)
Earnings per share      : 0.8                            (number, format "$"#,##0.00)
Net income              : "$8.3B"                         (text string, $ + billions)
Operating income        : "$12.5B"                        (text string, $ + billions)
Gross margin            : 0.46                            (number, format 0% → a DECIMAL FRACTION, 0.46 = 46%)
Operating expenses      : "$142.5B"                       (text string, $ + billions)
Buybacks and dividends  : "$0.5B Buybacks, $0 Dividends"  (free text)
```

Key implications (these drive the export/eval format, see `docs/EVAL_SPEC.md`):

- The four big currency columns (revenue, net income, operating income,
  operating expenses) are **text strings in `$<n>B` billions form**, not raw
  numbers in millions. Tesla's `22,496` (millions) renders as `"$22.5B"`.
- `Gross margin` is a **decimal fraction** (0.46) with a `0%` display format, so
  Tesla's 17.2% must be written as `0.172`, NOT `17.2` (which would render as
  1720%).
- `Earnings per share` is a plain number with a currency format; `0.33` is fine.
- `Quarter` and `Buybacks and dividends` are free text.
