# DECISIONS.md — Architectural Decision Log

Append-only. Newest at top. This is also my ammo for the cofounder "why did you do X over Y" call.

Format per entry:
- **Decision** — what I chose
- **Alternatives** — what I considered
- **Reason** — why this won
- **Consequences** — what it costs / what follows

---

## 2026-05-31 — Pixel-accurate citations via a deterministic locator + standalone viewer (Phase 10 slice)

- **Decision:** Build the hardest, most reusable piece of Phase 10 now — a
  deterministic `locate_evidence_bbox(pdf, page, quote) -> EvidenceLocation`
  (`earnings_extractor/locate.py`) plus a static viewer generator
  (`scripts/build_citation_viewer.py`) — while leaving the full interactive
  upload/pipeline/review/export web app for post-submission.
- **Alternatives:** (a) Build the whole Phase 10 web app now; rejected as too
  large a surface to take on before Phase 8/9 (README, cost, final audit). (b)
  Ask the LLM to emit bounding boxes; rejected because coordinates from a model
  are unverifiable and would break recorded/cassette mode.
- **Reason:** The bbox is *derived*, not predicted: PyMuPDF's `page.search_for`
  resolves the already-stored quote on the already-stored page of the original
  PDF, with chunked and numeric-anchor fallbacks for quotes that wrap or whose
  whitespace differs from the layout. This is purely additive — no change to
  extraction, schema, or recorded mode — and it is the exact seam the full UI
  needs, so it is not throwaway work. PyMuPDF over pdfplumber because
  `search_for` returns exact rects (including one per wrapped line) in a single
  call; the small new dependency is justified for a highlighting feature.
- **Consequences:** New runtime dependency `pymupdf>=1.24` in `pyproject.toml`.
  The viewer emits a self-contained HTML (PDF embedded, PDF.js from CDN) per
  draft — large for big decks (~12 MB for Tesla) because the PDF is inlined;
  acceptable for a local review artifact. Highlights are expressed as fractions
  of page size so they stay aligned at any zoom. Grounding is *text-presence*
  accurate (the quote and number are on the cited page), not yet a UI guarantee
  beyond the static viewer. Covered by `tests/test_locate.py` (known quote ->
  rect on correct page; wrapped statement line resolves; absent quote -> no
  rects; out-of-range page -> empty). On real data the locator resolved 11/12
  Tesla citations and 9/9 Citi — the one miss is the intentionally-blank Tesla
  buyback placeholder, which has no quote to locate.

## 2026-05-31 — Robustness: whole-document statement scan + structural transcript gate + thousands scale

- **Decision:** Three deterministic changes to make extraction generalize to any
  in-scope earnings PDF and company without a full LLM rewrite:
  1. `classify_document` now scans **all** pages for financial-statement markers
     (`has_statement` reads `full_text`, not just the head window), so reports
     that place statements after several narrative pages still classify as
     `earnings_report`.
  2. Transcript detection now requires **structural** evidence, not just a
     phrase: `has_transcript_structure` (>=4 head speaker turns *and* an
     `operator:` line) OR (`earnings call`/`conference call` phrase *and* >=2
     speaker turns). A report that merely mentions an upcoming earnings call no
     longer flips to `earnings_call_transcript`.
  3. `normalize_currency_to_usd_millions` handles the `thousands`/`k` scale
     (divide by 1000), and `_coerce_number_and_scale` recognizes those suffixes,
     so smaller filers reporting "$ in thousands" land on the canonical
     USD-millions basis.
- **Alternatives:** Rewrite the pipeline to send every page to an LLM and review
  everything (the user explicitly asked whether to do this). Rejected: the
  deterministic classify/select/normalize/validate split is what enables
  recorded-mode reproducibility, the no-key eval gate, and cheap runs well under
  the ~$3-5/PDF analyst-cost ceiling. The blanks users saw were correct
  review-first behavior (e.g. Citi does not report gross margin or operating
  income), not extraction failures — so the fix targeted the real gaps
  (back-loaded statements, transcript false-positives, thousands scale) rather
  than discarding the architecture.
- **Reason:** These were the concrete generalization gaps found while smoke-
  testing an unseen company (Netflix, whose statements sit on pages 9/11-14 and
  which previously failed with "No supported earnings PDFs found"). Each is a
  narrow, testable heuristic fix; none introduces company-specific tokens.
- **Consequences:** Netflix and other back-loaded shareholder letters now
  classify and select correctly. Transcript detection is stricter, so a future
  transcript that lacks both structural signals and the phrase could be missed —
  acceptable given the alternative (false positives) is worse for a 2-type
  scope. Covered by new tests in `test_classify.py`
  (`test_back_loaded_letter_classifies_as_earnings_report`,
  `test_back_loaded_statements_are_selected_for_extraction`,
  `test_narrative_mention_of_earnings_call_stays_a_report`) and
  `test_phase4_processing.py`
  (`test_currency_normalization_handles_thousands_scale`).

## 2026-05-31 — Third golden doc (Netflix) deferred pending validated cassette + two judgment calls

- **Decision:** Do **not** bake Netflix golden values into
  `evaluation/golden_metrics.py` yet. Keep the eval gate on the two validated
  documents (tesla_q2_2025, citi_q1_2025, both 9/9) until a Netflix cassette is
  generated from a real API run and two judgment-call labels are confirmed.
- **Alternatives:** Hand-enter Netflix golden values now to widen the eval set.
- **Reason:** Two of the nine Netflix fields are genuine judgment calls, not
  stated line items: **Gross margin** (Netflix reports operating margin, not
  gross margin → proposed `expected_blank_review`) and **Operating expenses**
  (no single stated line; components sum to ~$7,195.8M → proposed
  `expected_blank_review`). Committing unvalidated values, or a golden derived
  from a draft I can't verify against a live cassette (no API key in sandbox),
  would corrupt the accuracy signal — worse than a smaller, trustworthy gold set.
- **Consequences:** Eval coverage stays at two documents. Prepared Netflix
  values for when the user activates this: Company Name="Netflix" (p11),
  Quarter="Q1 2025" (p11), Total revenue=10543 (p1), EPS diluted=6.61 (p1),
  Net income=2890 (p1), Operating income=3347 (p1), Buybacks="$3.5B of share
  repurchases (3.7M shares)" (p6); Gross margin and Operating expenses pending
  user confirmation as `expected_blank_review`. Activation: commit the Netflix
  PDF as a tracked fixture, run extract with an API key to record a cassette,
  then add the `PRIMARY_FIELDS["netflix_q1_2025"]` block.

## 2026-05-31 — Eval threshold is enforced only when requested

- **Decision:** The `eval` CLI now accepts `--min-accuracy`. When the flag is
  present, accuracy below the threshold exits nonzero; when omitted, `eval`
  remains report-only and exits `0`.
- **Alternatives:** Keep the >=90% gate as a manual read of printed output; or
  make every `eval` invocation fail by default below the target.
- **Reason:** Phase 7 needs a machine-enforced gate for verification and future
  CI, but preserving report-only behavior keeps existing exploratory eval runs
  backward-compatible.
- **Consequences:** Gate commands must include `--min-accuracy 0.9` when they
  are meant to enforce the threshold. The runtime package still reaches
  evaluation only through `_eval_bridge.py`, which keeps the no-golden-import
  boundary intact.

## 2026-05-31 — Attention-flagged final values require approval plus a note

- **Decision:** Phase 6 allows an attention-flagged required value to populate
  the final client template only when the decision is `approved` and
  `reviewer_note` is non-empty. Attention-flagged blank or inapplicable fields
  can pass final export as `not_applicable` with a note and remain blank.
- **Alternatives:** Block all attention-flagged values from final export; or let
  a bare `approved` populate flagged values.
- **Reason:** Some valid fields are intentionally review-flagged, such as Citi's
  capital-return text and Tesla's not-applicable buyback/dividend field. A note
  makes the human resolution explicit without forcing good values out of the
  workbook.
- **Consequences:** Demo decisions still cannot produce a normal final export.
  Human-shaped or synthetic acceptance decisions must carry notes on attention
  items, and non-populating statuses blank the client cell.

## 2026-05-31 — Pixel-perfect citation highlighting is a derived, deterministic Phase 10 feature

- **Decision:** The final-form UI will let a reviewer click a citation and see the
  exact supporting text highlighted in the rendered PDF (split view, PDF on the
  left). The highlight rectangle is computed by a deterministic locator,
  `locate_evidence_bbox(pdf, page, quote) -> list[Rect]`, derived from the
  already-stored cited page + source quote + the original PDF — not produced by
  the LLM. Targeted to Phase 10 (post-submission), additive, no extraction change.
- **Alternatives:** (a) Ask the LLM to return coordinates — rejected, LLMs do not
  reliably produce reliable pixel coordinates and it would pollute the no-cheat
  extraction boundary. (b) Page-only navigation with a text-search highlight —
  workable but not pixel-perfect and the user explicitly asked for pixel-perfect.
  (c) Persist bbox as a new required schema field captured at extract time —
  rejected as unnecessary coupling; bbox is derivable on demand.
- **Reason:** Page + quote + PDF fully determine the rectangle, so coordinates are
  derived data. Computing them deterministically (PyMuPDF/pdfplumber expose
  per-span bbox) keeps the LLM boundary clean, keeps recorded/cassette mode
  working (the PDF is present in both modes), and reuses the validator's existing
  "quote on cited page" search as the same code path. A multi-line quote yields
  one rect per line, which is still pixel-accurate.
- **Consequences:** Phase 10 gains step 5 + a gate clause (click traces to the
  exact location) and a deterministic unit test for the locator. The original PDF
  must be available at render time. Bbox may be computed lazily in the UI or
  stored as an optional derived field; either is backward-compatible with existing
  drafts. No change to Phases 0-6.

---

## 2026-05-31 — Reviewed-ness is keyed off resolved attention state, not decision presence

- **Decision:** The Phase 6 export gate decides whether a value is "reviewed" by
  inspecting resolved attention state, not by checking that a decision row
  exists. A required template field is unreviewed (and refused without
  `--allow-unreviewed`) when the decisions file is `is_demo = true`, when the
  field has no decision, or when its source metric had `requires_attention = true`
  and was left at a bare `approved` rather than an explicit `rejected` /
  `needs_fix` / `not_applicable` resolution.
- **Alternatives:** Treat any decisions file with a row per metric as fully
  reviewed; or trust the `review_status` value alone regardless of attention.
- **Reason:** `review.html` pre-fills untouched items with the same auto-logic as
  demo (blank → `not_applicable`, attention → `needs_fix`, else → `approved`) and
  stamps browser downloads `is_demo = false`. A reviewer who clicks Download
  without reviewing would otherwise emit a "human" file that silently
  auto-approved high-confidence values. Keying the gate off attention state keeps
  that from sailing through as production approval.
- **Consequences:** Phase 6 needs the draft (or queue) alongside the decisions
  file to recompute `requires_attention`, and a review-gate unit test must cover
  the auto-`approved`-but-still-flagged case. A bare `approved` on a flagged item
  is not a shortcut to export.

## 2026-05-31 — Review decisions carry demo state for Phase 6 export integrity

- **Decision:** `review_decisions.json` includes `is_demo`. The Phase 5 CLI sets
  `is_demo = true` only for `--demo-decisions`; browser-downloaded human review
  files set `is_demo = false`.
- **Alternatives:** Omit demo state and rely on file names or reviewer names; or
  block demo decision generation entirely.
- **Reason:** Reviewers need deterministic offline verification, but auto-created
  decisions are not real human approval. A first-class flag gives Phase 6 a clear
  hook to mark demo-reviewed exports as draft/unreviewed instead of accidentally
  treating them as production approvals.
- **Consequences:** Phase 5 validates both demo and human-shaped decision files.
  Phase 6 must honor `is_demo = true` when enforcing final-export integrity.

## 2026-05-31 — Phase 5 source-file mapping is a review-layer heuristic

- **Decision:** Review artifacts resolve each metric's source PDF without
  changing the draft metric schema: single-document runs use the only document;
  combined runs use a unique `document_type`, then company/ticker filename
  matching, otherwise `source_file = "unknown"` and `requires_attention = true`.
- **Alternatives:** Add `source_file` to every metric during extraction now; or
  omit source-file display in Phase 5.
- **Reason:** The current draft metric row does not carry its originating
  `source_file`, and Phase 5 should not widen into extraction/schema migration
  work. The heuristic is truthful for the current Tesla/Citi combined run and
  fails visibly when ambiguous.
- **Consequences:** Review artifacts are usable now, but future extraction should
  carry `source_file` on each metric to support same-type multi-document runs
  without heuristics.

## 2026-05-30 — Quote-on-page verification runs only for metrics that have a value

- **Decision:** The runtime "source quote appears on cited page" check is applied
  only when a metric actually has a value. For blank rows (no value), skip it; the
  "Template field is blank and requires review" flag already routes them to a human.
- **Alternatives:** Run the quote check on every row regardless of value (the prior
  behavior); or special-case specific negative-evidence sentences.
- **Reason:** The check exists to catch a fabricated citation backing a *reported
  number*. When there is no value, the model's `source_quote` is typically an honest
  "nothing to cite here" sentence, and flagging it "Source quote not found on cited
  page N" reads like a hallucination signal — a false alarm in a review-first tool
  whose review reasons must be trustworthy. Gating on value-present removes the
  misleading reason without weakening integrity: a populated value with a bad quote
  is still flagged.
- **Consequences:** Cleaner, non-misleading review reasons on legitimately-absent
  metrics; no change to eval (only review flags differ, never value scoring).

## 2026-05-30 — Source quotes are verified against the cited page at runtime

- **Decision:** During validation, confirm each populated metric's `source_quote`
  actually appears (whitespace-normalized, case-insensitive) on its cited
  `source_page`. If it does not, set `needs_review = true` with a
  "Source quote not found on cited page N" reason.
- **Alternatives:** Trust the model/cassette quote as long as it is non-empty
  (the prior behavior); or defer evidence checks to human review only.
- **Reason:** The product's trust story is "every value traces to a real
  citation." A non-empty-but-hallucinated quote would silently break that. The
  check is deterministic, cheap (reuses the Phase 2 whitespace-match approach),
  and is a stronger signal than self-reported model confidence. It only adds
  review flags and never changes value scoring, so eval accuracy is unaffected.
- **Consequences:** Honest evidence integrity at the row level; rows with
  trimmed/ellipsized or paraphrased quotes get flagged for human attention.

## 2026-05-30 — Confidence is uncalibrated; quote verification carries the trust

- **Decision:** Keep the `confidence` field but treat it only as a review-triage
  hint, gated by a single `LOW_CONFIDENCE_THRESHOLD = 0.75` constant. Do not let
  it gate export, and do not attempt calibration in this take-home.
- **Alternatives:** Remove the field; or present it as a calibrated probability.
- **Reason:** `confidence` is model self-report bounded only by schema range
  validation; with two documents there is nothing to calibrate against.
  Removing it would just mean re-adding it for the future review queue/model
  routing. Real trust comes from source-page/quote presence, runtime quote
  verification, deterministic consistency checks, and human review.
- **Consequences:** Docs/UI describe it as "model-reported, uncalibrated."
  Calibration stays a production-roadmap item, not a deliverable claim.

## 2026-05-30 — Capital-return enrichment is a scoped heuristic and stays review-flagged

- **Decision:** `enrich_capital_return_text` extracts "Buybacks and dividends"
  from a capital-return sentence via regex, but the resulting row stays
  `needs_review = true` (confidence 0.85) with a "verify against the cited
  source" reason rather than auto-approving.
- **Alternatives:** Auto-clear review on a regex match (the prior behavior); or
  drop the heuristic entirely and always leave the field blank/review.
- **Reason:** The regex is shaped to the sample transcript phrasing and will not
  generalize to all wordings, so presenting its output as trusted would
  overclaim. Keeping it review-flagged respects the "every populated value gets
  a citation review" rule while still surfacing a useful candidate value.
- **Consequences:** The field is populated with evidence but always enters human
  review; unseen phrasings simply fall back to the blank/review placeholder.

## 2026-05-30 — Assignment/admin PDFs are skipped via scoped markers

- **Decision:** `classify.py` marks PDFs containing `NON_SOURCE_MARKERS`
  ("take home assignment", "problem statement for candidate") as `unknown` so
  directory extraction skips the assignment prompt itself, and requires an
  explicit `operator:` turn (not just speaker density) before calling a document
  a transcript.
- **Alternatives:** Extract from every PDF in the directory; or rely on
  speaker-turn counts alone.
- **Reason:** The assignment directory contains admin PDFs that describe
  earnings calls without being source documents; without a filter they would
  produce junk metrics. These markers are deliberately scoped to this
  assignment's admin files, not a general classifier capability.
- **Consequences:** Clean directory runs on the provided inputs; a production
  deployment would replace these markers with a real document-source policy.

## 2026-05-30 — Phase 4 recorded eval stays single-document until scoring is document-scoped

- **Decision:** Use single-document drafts for Phase 4 accuracy eval, while still
  allowing combined directory drafts for inspection/review artifacts.
- **Alternatives:** Make the combined draft scorable immediately; or keep using
  a combined draft and accept row overwrites in the current flat scorer.
- **Reason:** `evaluation.scoring.score_draft` currently maps rows by normalized
  `metric_name` only. A combined Tesla+Citi draft has duplicate template field
  names, so one company's row can overwrite the other's during scoring.
- **Consequences:** Phase 4 verification runs eval separately for Tesla and
  Citi. A later phase can add document scoping to the eval if combined-run
  scoring becomes necessary.

## 2026-05-30 — Company identity is deterministic and review-flagged when inferred

- **Decision:** Resolve company identity from full-document text and PDF
  metadata before final draft validation, preferring real on-page evidence and
  review-flagging metadata-only inference.
- **Alternatives:** Rely on the LLM to infer company names from sparse cover
  pages; or hardcode sample ticker/company aliases.
- **Reason:** Tesla's extracted cover text only says `Q2 2025 Update`, so an LLM
  answer of `Tesla` would be weakly evidenced. Full-document text contains a
  real `Tesla` quote, while metadata provides a generic candidate signal.
- **Consequences:** The `Company Name` row can pass eval with truthful evidence,
  and unseen issuers still get a generic metadata/text path instead of a
  sample-specific lookup.

## 2026-05-29 — Phase 3 live extraction uses Responses structured outputs

- **Decision:** Use the OpenAI Responses API with Pydantic structured outputs for Phase 3 live extraction. Default to `gpt-5.4-mini` with `OPENAI_REASONING_EFFORT=low`, both overrideable through `.env`. Keep `gpt-5.5` as a later escalation candidate rather than the default.
- **Alternatives:** Use free-form JSON from a chat completion; default to a flagship model; or wait for recorded mode before adding live extraction.
- **Reason:** Structured outputs give a validated schema boundary for source-backed metric rows, and the mini model is a cost-disciplined default for extracting a small fixed field set. Reasoning tokens are billed as output, so the exact cost depends on the run, but the expected per-PDF cost is still well under `$0.10` and comfortably below the confirmed `$3.33-$5.00/PDF` analyst baseline. Official references: pricing (`https://developers.openai.com/api/docs/pricing`), structured outputs (`https://developers.openai.com/api/docs/guides/structured-outputs`), and reasoning (`https://developers.openai.com/api/docs/guides/reasoning`).
- **Consequences:** Phase 3 requires `OPENAI_API_KEY`; Phase 4 still needs recorded mode so reviewers can reproduce the pipeline without a key. The eval bridge is isolated from runtime extraction code so golden values stay out of the extractor path.

## 2026-05-29 — Phase 2 uses pdfplumber for page-level PDF text

- **Decision:** Use `pdfplumber` as a runtime dependency for page-level PDF text extraction, wrapped by `earnings_extractor.ingest.read_pdf_pages`.
- **Alternatives:** Shell out to `pdftotext`; use `pypdf`; keep PDF reading as a script-only dev dependency until later.
- **Reason:** The local environment does not provide `pdftotext`, and Phase 3 needs ingestion in the runtime package. `pdfplumber` handles the provided transcript prose and table-heavy Tesla update well enough for cited-page evidence checks.
- **Consequences:** Runtime install has a heavier PDF dependency, but ingestion is deterministic, testable, and ready for the extraction pipeline.

## 2026-05-29 — Eval-only fixtures live in evaluation/

- **Decision:** Store golden targets and tolerance constants in `evaluation/`, outside the runtime `earnings_extractor/` package.
- **Alternatives:** Use `eval/golden.py`; place fixtures under `tests/fixtures/`; or keep expected values only in docs.
- **Reason:** `evaluation/` avoids confusion with the future `eval` CLI command and keeps expected answers isolated from extractor code while still importable by tests and the eval harness.
- **Consequences:** Docs and static tests must enforce the boundary: runtime code must not import `evaluation`, and golden values remain eval-only.

## 2026-05-29 — Two test layers + per-phase advance gates

- **Decision:** Keep two distinct test layers: the eval harness for end-to-end extraction accuracy, and `pytest` unit tests for deterministic logic (normalization, consistency checks, the canonical→client-cell mapper, schema validation/repair). Unit tests are written in the same phase as the code they cover (folded into Phases 3, 4, 5, 6), not as a final bolt-on phase. Each phase also gets an explicit "Gate to advance" — concrete, command-backed checks that must all pass before the next phase starts.
- **Alternatives:** A single "testing phase" at the end; or relying on the eval alone with no unit tests.
- **Reason:** The eval proves accuracy but doesn't isolate which deterministic function broke; unit tests do, and they're exactly the bug-prone pure functions (e.g. the `17.2% → 0.172` mapper). A trailing test phase tends to get rushed and skipped. Per-phase gates stop work from advancing on top of an unverified foundation, matching the "done = passing output, not code exists" rule.
- **Consequences:** Slightly more discipline per phase; `pytest` now has an owning home in the plan instead of being a gate with no scheduled work. Test scaffold (`tests/`, pytest config, a green placeholder) moves into Phase 0.

## 2026-05-29 — Client cells follow the template sample-row format

- **Decision:** Render the client template's first sheet to match the formats in the template's sample data row, not just its headers: currency columns (`Total revenue`, `Net income`, `Operating income`, `Operating expenses`) as `$<n>B` text strings; `Gross margin` as a decimal fraction in a `0%`-formatted cell (17.2% → `0.172`); `Earnings per share` as a plain number; `Quarter`/`Buybacks and dividends` as text. Keep a separate internal canonical value (USD millions, percentage points) that the eval compares; add a format-mapping layer at export.
- **Alternatives:** Write raw numeric values in millions into the template (what the earlier docs implied); or invent our own display format.
- **Reason:** The template ships with an `Amazon LLC` sample row whose cell types/number-formats are the real contract for "template-compatible." Matching headers but writing `22496` into a column the client formats as `$150B`, or `17.2` into a `0%` cell (→ 1720%), would produce a workbook that looks broken next to their own sample.
- **Consequences:** Export needs an explicit canonical→display mapping and a round-trip format check in the eval. EVAL_SPEC now separates internal-value scoring from client-cell format verification. Discovered by inspecting the sample row, which the original docs (column headers only) had skipped.

## 2026-05-29 — Review-first workflow before final Excel

- **Decision:** Change the core product flow from immediate export to `extract draft -> human citation review -> final export`. `extract` writes draft metrics, evidence, review queue, and a review UI/report. `export` creates the final client workbook only after review decisions, or with an explicit draft/unreviewed override.
- **Alternatives:** Generate final Excel immediately with review flags inside it; or build a full hosted review app before the extractor is working.
- **Reason:** The trust unlock is not just extracting numbers; it is showing the exact citation and making a human approve questionable values before they enter the final spreadsheet. This better matches an analyst workflow and makes the submission more defensible.
- **Consequences:** CLI and verification gates need separate `extract`, `review`, and `export` steps. The first implementation can use a simple local `review.html` plus `review_decisions.json`; a richer hosted UI remains future work.

## 2026-05-29 — Primary eval score follows template fields

- **Decision:** Define the primary accuracy score over client-template fields in `docs/EVAL_SPEC.md`; keep auxiliary metrics such as RoTCE, CET1, cost of credit, operating cash flow, and free cash flow separate unless explicitly listed in an eval fixture.
- **Alternatives:** Score every interesting extracted metric together; or score only a free-form long-table output.
- **Reason:** The client provided an Excel template, so the main score should align with the workflow they care about. Auxiliary metrics are still useful for audit and validation, but mixing them into the primary denominator would make the gate ambiguous.
- **Consequences:** Eval fixtures must mark fields as `expected_value`, `expected_blank_review`, or `not_scored`, and every expected value needs source page + quote evidence.

## 2026-05-29 — Client-confirmed template, scope, LLM, and cost baseline

- **Decision:** Make the first Excel export sheet compatible with `assesment_info/EarningsSample (1).xlsx`, whose columns are `Company Name`, `Quarter`, `Total revenue`, `Earnings per share`, `Net income`, `Operating income`, `Gross margin`, `Operating expenses`, and `Buybacks and dividends`. Keep added tabs for normalized metrics, review decisions, and evidence.
- **Alternatives:** Use only my own normalized long-table workbook; or exactly mirror the client template with no audit tabs.
- **Reason:** Andrew confirmed the team has a template. Matching it respects the existing workflow, while audit tabs preserve source evidence and reviewability.
- **Consequences:** Export code needs a template-mapping layer from normalized metrics to client-facing fields. Fields that are missing, inapplicable, or weakly supported must be blank and/or review-flagged rather than guessed.

## 2026-05-29 — V1 document scope is earnings reports and call transcripts

- **Decision:** Support public earnings report PDFs and earnings-call transcripts for v1.
- **Alternatives:** Broaden immediately to 10-Qs, 10-Ks, press releases, investor decks, and arbitrary filings.
- **Reason:** Andrew confirmed earnings report PDFs and call transcripts are all the team needs for now. The Tesla update deck fits the "earnings report PDF" bucket; Citi fits the transcript bucket.
- **Consequences:** The pipeline still handles table-heavy and narrative sources, but final docs should not overclaim support for filings outside v1 scope.

## 2026-05-29 — Hosted LLM is approved; cost target benchmarked to analysts

- **Decision:** Use a hosted LLM API for the assignment, with recorded/offline mode for reviewers. Benchmark cost against analyst labor: `$50/hour` at `10-15 PDFs/hour`, or roughly `$3.33-$5.00/PDF`.
- **Alternatives:** Avoid hosted LLMs entirely; or ignore cost until production.
- **Reason:** Andrew explicitly approved external hosted LLM use for these public earnings documents and gave a practical budget comparison.
- **Consequences:** README and system-design notes should include a per-PDF cost estimate and show that the automated path is comfortably cheaper than manual processing.

## 2026-05-29 — Doc review pass: de-hype the gates

- **Decision:** Required gate = offline/recorded mode (keyless); live API optional. venv install instead of an unsafe system-package override. Unseen-company test = best-effort smoke test, not an accuracy gate. mypy optional. Softened the virtual-card and sample-security claims. "One LLM call" → "structured extraction calls (may be plural)." Added explicit Excel tabs; after the client reply, the first tab is the client template rather than a generic summary.
- **Alternatives:** Keep the stronger/cleaner-sounding original wording.
- **Reason:** A reviewer flagged that several gates weren't realistically passable and a few claims were overstated. Honest, passable gates read like someone who ships, not someone hyping.
- **Consequences:** Docs promise less but deliver reliably; submission can't be blocked by a missing API key or an unlabeled generalization file.

## 2026-05-29 — Eval-first development

- **Decision:** Build the golden-metrics eval harness before/alongside the extractor; treat its accuracy number as the gate for every change.
- **Alternatives:** Build the extractor first and measure accuracy at the end.
- **Reason:** The assignment hands me a crisp verification target (known correct numbers). Scoring every change immediately turns "write and hope" into measurable iteration.
- **Consequences:** Slightly more upfront work on the eval module; golden values must stay isolated from extractor code.

## 2026-05-29 — Product is a deterministic pipeline, not a runtime agent

- **Decision:** At inference the product is a fixed pipeline with constrained, structured LLM extraction calls (ingest → classify → extract → normalize → validate → flag → export). Calls may be plural for page/chunk processing; orchestration remains fixed code, not agent-decided.
- **Alternatives:** An autonomous agent that decides its own steps at runtime.
- **Reason:** 90% accuracy + auditability demand reproducibility. A deterministic pipeline is testable, cheap, and traceable; an agent is none of those.
- **Consequences:** Less "smart" runtime behavior, but every output is explainable — which is the point for a financial workflow.

## 2026-05-29 — Real LLM extractor + recorded-response offline mode

- **Decision:** The real extraction path is LLM-based. Offline/sample mode replays recorded LLM responses (cassette pattern).
- **Alternatives:** Regex/template extraction; or hardcoded sample outputs.
- **Reason:** Only an LLM generalizes to unseen companies/formats. Recorded responses let reviewers run the extractor pipeline keyless without faking results.
- **Consequences:** More moving parts; must keep golden values out of the extractor so accuracy stays honest.

## 2026-05-29 — Documents are two different types

- **Decision:** Treat Citi as a narrative transcript and Tesla as a table-heavy update deck; classify and handle both.
- **Alternatives:** Assume all inputs are transcripts (as the brief loosely implies).
- **Reason:** Only Citi is actually a transcript. Real robustness requires handling prose AND tables.
- **Consequences:** A classification step and two extraction strategies; raised as a client question.

## 2026-05-29 — Client comms: one email, correctly routed

- **Decision:** Send one email, To Andrew (IT/budget/security), CC Adam + Rick, with separate product vs. technical sections.
- **Alternatives:** Two separate emails; or one email to the client only.
- **Reason:** The brief routes technical/budget to Andrew and "CC me." One well-routed email respects their time while showing I understood the org.
- **Consequences:** Slight ambiguity on who the "client" is (unsigned brief) — covered by CC'ing both.
