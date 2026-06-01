# LLM-only vs. Full Pipeline — Accuracy Comparison

**Question this answers:** is the deterministic layer worth it, or could we just
make a plain LLM call?

**Verdict:** the deterministic layer raised scored-field accuracy from
**15/18 (83.3%)** to **18/18 (100%)** on the reproducible recorded run across
the two golden documents. Every recorded failure under the LLM-only baseline was
fixed by a deterministic step, and nothing regressed. A live supporting run on
2026-06-01 raised accuracy from **12/18 (66.7%)** to **17/18 (94.4%)**; the
remaining live miss was a quarter text mismatch.

## Methodology (honest + reproducible)

The reproducible artifact is the **recorded GPT response** captured for each PDF
(`earnings_extractor/recorded_responses/*.json`). That lets reviewers rerun the
A/B with no API key. Both sides are scored against the same eval-only golden
targets (`evaluation/golden_metrics.py`); golden values are never fed into
either extractor.

- **LLM only** = raw cassette metrics, scored directly with **no** template
  completion, identity resolution, capital-return enrichment, normalization, or
  validation.
- **Full pipeline** = the same cassette metrics run through
  `earnings_extractor.pipeline.extract(...)`.

Reproduce with:

```bash
python3 scripts/compare_llm_vs_pipeline.py --mode recorded
# writes outputs/comparison/comparison_results_recorded.json
```

With a live API key, the same script can run a live A/B. That run is useful
supporting evidence, but it is not the keyless reviewer gate because live model
outputs can vary.

## Results

| Document | LLM only | Full pipeline |
|---|---|---|
| Tesla Q2 2025 | 9/9 (100%) | 9/9 (100%) |
| Citi Q1 2025 | 6/9 (66.7%) | 9/9 (100%) |
| **Combined** | **15/18 (83.3%)** | **18/18 (100%)** |

### Tesla Q2 2025 — recorded result

The recorded Tesla response already passed all nine scored fields before the
deterministic layer. The full pipeline preserved that score and added the same
normalization, evidence validation, and review flags used for every document.

### Citi Q1 2025 — recorded fields that flipped

| Field | LLM only | Full | Fixed by |
|---|---|---|---|
| Total revenue | FAIL (21,600 vs golden, off by 2.16e4) | PASS | Unit/scale normalization |
| Net income | FAIL (off by ~4,096) | PASS | Unit/scale normalization |
| Operating expenses | FAIL (off by 1.34e4) | PASS | Unit/scale normalization |

The Citi transcript states figures in **billions** ("$21.6 billion of
revenues"), so the raw LLM emitted `value=21.6, unit="billion"`. The golden
template expects **USD millions**, so the raw numbers are off by 1000×.
`normalize_metrics` converts billions→millions deterministically, landing all
three exactly on target (difference 0).

## Why this is the right division of labor

The single LLM call is good at the hard, fuzzy part — *reading the document and
finding the right number with a source quote*. It is unreliable at the boring,
high-stakes parts: getting the **scale** right (billions vs. millions is a 1000×
error, not a rounding error) and filling fields the document states implicitly
(company name). Those are exactly the things a deterministic function does
perfectly and repeatably, with no token cost and no variance.

So the deterministic layer is not redundant with the LLM — it is cleanup the LLM
is structurally bad at. The three Citi misses are the clearest case: a plain LLM
answer would have reported revenue as "21.6" into a millions column, a silent
1000× error that looks plausible and would pass casual review. The normalizer
makes that class of error impossible.

A caveat for honesty: this baseline scores the LLM's *structured* output as-is.
A prompt that explicitly demanded "USD millions" might recover some scale cases,
but that pushes correctness into prompt wording (high variance, model-dependent)
rather than a tested deterministic guarantee. The point of the deterministic
layer is to make these guarantees *not* depend on prompt luck.

## Live Supporting Run

On 2026-06-01, `python3 scripts/compare_llm_vs_pipeline.py --mode live` ran the
same one-call-per-document A/B with `gpt-5.4-mini`:

| Document | LLM only | Full pipeline |
|---|---|---|
| Tesla Q2 2025 | 9/9 (100%) | 9/9 (100%) |
| Citi Q1 2025 | 3/9 (33.3%) | 8/9 (88.9%) |
| **Combined** | **12/18 (66.7%)** | **17/18 (94.4%)** |

The live deterministic layer fixed Citi company identity, revenue, net income,
operating expenses, and capital-return wording. The remaining live miss was
`Quarter`, a text-label mismatch; the numeric and scale-sensitive fields were
fixed by deterministic code.
