# EVAL_SPEC.md — Accuracy and Golden-Value Rules

This file defines how the take-home measures accuracy. The goal is an honest
field-level score from the extractor pipeline, not a shortcut through expected
values.

## Non-Negotiables

- Golden expected values live only in eval code/docs, never in extraction code.
- The extractor must not import, read, or derive from the golden dataset.
- Every populated metric must include `source_page`, `source_quote`,
  `confidence`, `needs_review`, `review_reason` when applicable, and review
  status before final export.
- Unsupported or inapplicable values should be blank and/or review-flagged, not
  guessed.

## Scored Template Fields

The primary accuracy score is over the client-template fields that can be
source-supported for each document:

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

For each company/document, the eval fixture should explicitly mark every field
as one of:

- `expected_value` — field is present and should be extracted.
- `expected_blank_review` — field is not clearly supported or not meaningful for
  that issuer/document; correct behavior is blank plus review reason.
- `not_scored` — field is outside the current fixture's scope and should not
  affect the percentage.

Do not silently remove difficult fields from the denominator. If a field is
excluded, the fixture must say why.

## Auxiliary Metrics

Auxiliary metrics can appear in the `Metrics` and `Evidence` tabs and can be
used for validation or demonstration, but they are separate from the primary
template score unless explicitly listed in the eval fixture.

Examples:

- RoTCE
- CET1 ratio
- cost of credit
- operating cash flow
- free cash flow
- adjusted EBITDA

## Two Layers: Internal Canonical Value vs Client-Facing Cell

There are two distinct representations and the eval must not confuse them:

1. **Internal canonical value** — what the extractor stores and what the eval
   compares numerically: currency in USD millions, margins/ratios in percentage
   points (e.g. `17.2`), EPS in dollars per diluted share.
2. **Client-facing template cell** — what gets written into the first sheet of
   the exported workbook, formatted to match the template sample row (see
   `docs/CLIENT_REPLY.md`):
   - currency columns (`Total revenue`, `Net income`, `Operating income`,
     `Operating expenses`) → text string in `$<n>B` billions form, e.g.
     `22,496` USD millions → `"$22.5B"`;
   - `Gross margin` → **decimal fraction** in a `0%`-formatted cell, e.g. `17.2%`
     → `0.172` (writing `17.2` here is a bug — it renders as 1720%);
   - `Earnings per share` → number with currency format, e.g. `0.33`;
   - `Quarter`, `Company Name`, `Buybacks and dividends` → text.

The eval scores the **internal canonical value** with the tolerances below. A
separate format check confirms the client cell renders in the template's style
(round-trip the cell back to a canonical value and compare).

## Numeric Comparison

Normalize to the internal canonical value before comparing:

- Currency values compare in USD millions internally (regardless of how the
  client cell is later displayed in `$B`).
- Percentages/margins compare as percentage points, e.g. `17.2` for `17.2%`
  (note: the client `Gross margin` cell stores the decimal `0.172`).
- EPS compares as dollars per diluted share when the source states diluted EPS.

Tolerance:

- Currency metrics: pass if absolute difference is `<= max(1.0, 0.005 *
  expected_value)` in USD millions. This allows small rounding differences.
- EPS: pass if absolute difference is `<= 0.01`.
- Percentages/margins/ratios: pass if absolute difference is `<= 0.1`
  percentage points.
- Text fields such as company and quarter use normalized string matching.

## Review-Flag Scoring

For `expected_blank_review` fields, a result passes when:

- the client-template cell is blank or clearly indicates no supported value, and
- the normalized metric/review output includes `needs_review = true`, and
- `review_reason` explains why the field was not filled, and
- the human review decision is `not_applicable` or another explicit non-final
  status.

A guessed numeric value for an unsupported field is a failure even if the value
looks plausible.

## Source Evidence Scoring

A numeric value cannot pass unless its row includes:

- source page number;
- quote or table row text containing the value or enough context to audit it;
- confidence;
- review flag;
- human review status before final export.

Evidence is not expected to be a long excerpt. It should be short but sufficient
for a reviewer to find the value in the PDF.

## Initial Evidence-Backed Golden Targets

### Tesla Q2 2025

Primary template fields:

| Field | Expected | Source |
| --- | ---: | --- |
| Company Name | Tesla | File name and document title, page 1: `Q2 2025 Update` |
| Quarter | Q2 2025 | Page 1: `Q2 2025 Update` |
| Total revenue | 22,496 USD millions | Page 4: `Total revenues ... Q2-2025 22,496` |
| Earnings per share | 0.33 | Page 4: `EPS attributable to common stockholders, diluted (GAAP) ... Q2-2025 0.33` |
| Net income | 1,172 USD millions | Page 4: `Net income attributable to common stockholders (GAAP) ... Q2-2025 1,172` |
| Operating income | 923 USD millions | Page 4: `Income from operations ... Q2-2025 923` |
| Gross margin | 17.2% | Page 4: `Total GAAP gross margin ... Q2-2025 17.2%` |
| Operating expenses | 2,955 USD millions | Page 4: `Operating expenses ... Q2-2025 2,955` |
| Buybacks and dividends | expected_blank_review | No clearly supported buyback/dividend template value found in inspected Tesla pages; do not guess. |

Auxiliary values:

| Metric | Expected | Source |
| --- | ---: | --- |
| Operating cash flow | 2,540 USD millions | Page 4 and page 26: `Net cash provided by operating activities ... Q2-2025 2,540` |
| Free cash flow | 146 USD millions | Page 4: `Free cash flow ... Q2-2025 146` |

### Citi Q1 2025

Primary template fields:

| Field | Expected | Source |
| --- | ---: | --- |
| Company Name | Citi | Page 1 transcript title: `Citi First Quarter 2025 Earnings Call` |
| Quarter | Q1 2025 | Page 1 transcript title: `Citi First Quarter 2025 Earnings Call` |
| Total revenue | 21,600 USD millions | Page 3: `on $21.6 billion of revenues` |
| Earnings per share | 1.96 | Page 3: `EPS of $1.96` |
| Net income | 4,100 USD millions | Page 3: `net income of $4.1 billion` |
| Operating income | expected_blank_review | Not clearly supported as a firmwide bank metric in the transcript excerpts; do not infer. |
| Gross margin | expected_blank_review | Not meaningful for a bank transcript in the same way as an industrial company; do not infer. |
| Operating expenses | 13,400 USD millions | Page 3: `Expenses of $13.4 billion` |
| Buybacks and dividends | `$2.8B capital returned, including $1.75B buybacks` | Page 2: `returned $2.8 billion in capital ... including $1.75 billion of buybacks` |

Auxiliary values:

| Metric | Expected | Source |
| --- | ---: | --- |
| RoTCE | 9.1% | Page 3: `RoTCE of 9.1%` |
| CET1 ratio | 13.4% | Page 2: `CET1 ratio of 13.4%` |
| Cost of credit | 2,700 USD millions | Page 3: `Cost of credit was $2.7 billion` |
