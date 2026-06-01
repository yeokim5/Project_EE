# SYSTEM_DESIGN_NOTES.md — Rollout and Defense Notes

## Product Architecture

The runtime product should be a deterministic review-first pipeline with
constrained, structured LLM extraction calls:

```text
PDF ingest
-> page-level text/table extraction
-> document/page classification
-> structured LLM extraction
-> unit and scale normalization
-> deterministic validation
-> confidence + review flagging
-> human citation review
-> reviewed Excel/JSON/audit export
```

This is not a runtime autonomous agent. The workflow needs reproducibility,
auditability, predictable costs, testable failure modes, and a human approval
gate before final client deliverables.

## Why LLM + Validators

LLMs are useful because earnings reports and transcripts vary by company,
industry, and format. They are good at finding source-backed values in messy
tables and prose. Deterministic validators are necessary because financial
outputs need repeatable guarantees:

- normalize millions vs billions and thousands vs millions;
- resolve document identity when the filing implies rather than labels it;
- preserve blanks for unsupported or not-applicable fields instead of guessing;
- verify statement relationships where available;
- detect malformed/missing evidence;
- flag low-confidence or unsupported fields for review.

The A/B comparison in `docs/LLM_VS_PIPELINE.md` shows the split clearly. On the
reproducible recorded run, the same raw model outputs score `15/18` before the
deterministic layer and `18/18` after it. A live supporting run on 2026-06-01
scored `12/18` before the deterministic layer and `17/18` after it. The
remaining live miss was a quarter text mismatch, while the deterministic layer
fixed the high-risk financial failures: 1000x scale errors, identity cleanup,
and capital-return wording.

## Review-First Excel Strategy

The system should not jump straight from extraction to final Excel. It should:

```text
extract draft metrics -> review citations -> export final workbook
```

After review, the first Excel sheet should match the client's template exactly:

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

Additional tabs make the reviewed result production-grade:

- `Metrics` for normalized long-table data.
- `Review Decisions` for approvals, rejections, and not-applicable fields.
- `Evidence` for page/quote auditability.

This respects the current analyst workflow while adding the control the existing
manual process lacks: every value can be traced back to a citation before it
enters the final spreadsheet.

## Accuracy Measurement

For the take-home:

- Use a small golden set for Tesla and Citi, stored only in `evaluation/` code.
- Score field-level accuracy over the client-template fields defined in
  `docs/EVAL_SPEC.md`.
- Normalize units and scale before comparing numeric values.
- Use the numeric tolerances defined in `docs/EVAL_SPEC.md`.
- Count unsupported-but-correctly-review-flagged fields separately from wrong
  filled values.

For production:

- Build a larger labeled eval set across industries and document types.
- Track accuracy by metric, company type, document type, and extraction mode.
- Add regression tests for every production incident.

## Security

For this assignment, hosted LLM processing is client-approved because the
documents are public earnings materials and Andrew confirmed no processing
location restrictions. Source: `docs/CLIENT_REPLY.md`.

For a confidential production rollout, revisit:

- API data retention and no-training guarantees;
- encryption at rest and in transit;
- access controls, SSO, and RBAC;
- audit logs;
- data residency;
- redaction of internal notes or analyst comments;
- vendor review and incident response.

## Cost

Client baseline:

- Analysts cost about `$50/hour`.
- Analysts process `10-15 PDFs/hour`.
- Manual cost is roughly `$3.33-$5.00/PDF`.

Source: `docs/CLIENT_REPLY.md`.

Measured live sample:

- PDF: `assesment_info/TSLA-Q2-2025-Update.pdf`.
- Model: `gpt-5.4-mini`, `OPENAI_REASONING_EFFORT=low`.
- Token usage from `draft_metrics.json.llm_usage`: `6,422` input tokens,
  `1,967` output tokens, `8,389` total tokens.
- `223` reasoning tokens are included inside `output_tokens`; they are stored
  for audit but not added again in cost math.
- Pricing source: OpenAI model docs for `gpt-5.4-mini`
  (`https://developers.openai.com/api/docs/models/gpt-5.4-mini`) list
  `$0.75 / 1M` input tokens and `$4.50 / 1M` output tokens.

Calculation:

```text
(6,422 / 1,000,000 * $0.75) + (1,967 / 1,000,000 * $4.50)
= $0.013668 per PDF
```

That one measured sample is about `244x-366x` cheaper than the confirmed
`$3.33-$5.00/PDF` analyst baseline. Document length varies, so production should
track cost per document, but the observed LLM cost is comfortably below the
manual benchmark.

## Scale Path

```text
local prototype
-> batch worker
-> queue-based ingestion
-> object storage for PDFs and extracted artifacts
-> model cascade, cheap model first and escalate on low confidence
-> deterministic checks
-> human review queue
-> reviewed final export
-> eval dashboard
-> audit logs
-> SSO/RBAC
-> firm-wide rollout
```

## Next Two-Week Sprint

1. Expand the golden eval set to 30-50 documents across industries.
2. Upgrade the local review HTML into a persistent review UI if the client wants
   multi-user review, history, or PDF page previews.
3. Add confidence calibration from historical eval results.
4. Add document ingestion from the client's real storage/workflow.
5. Add monitoring for extraction failures, review rates, and cost per document.
