# REVIEW.md — Fresh-Context Reviewer Checklist

Use a fresh-context agent (or a fresh read of my own) to review the diff against this list before merging. The reviewer should look ONLY at the diff + the requirements, not the prior conversation. Fix only correctness, requirement, security, and test gaps — not style preferences.

## Correctness
- [ ] Does the change do what the task/requirement asked, nothing more?
- [ ] Are extracted values numerically correct vs. the source? Spot-check against the PDF.
- [ ] Units/scale normalized correctly (millions vs. billions)?
- [ ] Does the first Excel sheet match the client template columns from `assesment_info/EarningsSample (1).xlsx`?
- [ ] Are template fields that are not clearly supported by source evidence left blank and/or flagged for review instead of guessed?
- [ ] Do golden/eval fixtures trace each expected value to `docs/EVAL_SPEC.md` source page + quote evidence?
- [ ] Does extraction create draft artifacts first instead of silently producing a final client Excel workbook?
- [ ] Does final export require review decisions for required fields?

## No cheating / honesty
- [ ] Extractor does NOT import or read golden/expected values anywhere.
- [ ] Accuracy reported by `eval` comes from the real LLM path, not a shortcut.
- [ ] No metric asserted without `source_page` + `source_quote`.
- [ ] Auxiliary metrics such as RoTCE, CET1, cost of credit, OCF, and FCF are not mixed into the primary template score unless explicitly listed in the eval fixture.
- [ ] No final populated value bypasses human review status unless clearly marked as draft/unreviewed.

## Robustness
- [ ] Malformed LLM output is rejected/repaired, not trusted blindly.
- [ ] Low-confidence or applicable consistency-failing values set `needs_review = true`.
- [ ] Inapplicable consistency checks are skipped with a reason rather than forced into misleading failures.
- [ ] Would this plausibly work on an unseen earnings report PDF or call transcript, or is it overfit to Tesla/Citi?
- [ ] Review UI/report exposes source PDF, page, quote, confidence, validation status, and approve/reject/not-applicable decision state.

## Regressions
- [ ] Did previously passing tests / eval accuracy stay green?
- [ ] No accidental change to the schema or output contract.

## Security / hygiene
- [ ] No secrets, keys, or tokens in the diff.
- [ ] No new dependency that's unnecessary or risky.

## Scope
- [ ] No unrequested features snuck in. Out-of-scope ideas → README "even better with more time", not the code.
- [ ] v1 scope stays focused on earnings report PDFs and call transcripts; 10-Ks, 10-Qs, press releases, and UI polish remain optional unless already covered naturally.

## Verdict
- [ ] APPROVE (gate green, no blocking issues), or
- [ ] CHANGES NEEDED (list the specific correctness/requirement/security/test gaps only).
