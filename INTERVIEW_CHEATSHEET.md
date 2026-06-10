# Interview Cheat Sheet — Assignment 1 & 2

## How I answer (read this first)
- First answer **short — under 30 seconds.** Then stop.
- Follow-up comes → **then go deep.**
- Pushback → start with **"That's fair,"** never defend immediately.
- After answering, **stop. Don't add.**
- Be the person who: explains it simply to a customer · holds the core requirement · doesn't blindly trust AI output · understands trade-offs · changes the design when given feedback.

## 30-sec pitch
- Turns an earnings PDF into a draft of the team's workbook.
- Every value linked to its source quote; anything uncertain is flagged.
- Analyst verifies in seconds instead of hunting the filing — nothing unverified reaches the client sheet.

## Opening framing (use once, early)
- Before writing code, I asked Andrew: the template, confidence/review behavior, document types, cost + data constraints.
- That shaped every decision — review-first was my idea, not handed to me.

---

# ASSIGNMENT 1 — Earnings Extractor

## Q. How does the whole process work under the hood?
**▸ 30s:** "It's a fixed pipeline from PDF to reviewed Excel. The model only reads; my code controls every step and does everything reliable."
**▸ If they want the walk-through:**
- Read PDF page-by-page → keep page numbers (needed for evidence later).
- Select the high-signal pages (revenue, EPS, net income, cash flow, dividends) — not the whole filing.
- One LLM call → structured JSON per field: value, source page, source quote, confidence.
- Deterministic code then: fill missing rows → resolve company identity → handle buybacks/dividends → normalize units → validate.
- Live-only safety passes: line-item correction + semantic verifier (flag only, never edit).
- Human review → approve/reject → export. Only approved values hit the client sheet.
- **One line:** "Model reads, deterministic code makes it reliable, human makes the final call."

## Q. What does the model do?
**▸ 30s:** "The one genuinely ambiguous part — reading unstructured documents into a structured draft."
**▸ Deeper:**
- Reads selected pages → returns value + source page + source quote + confidence per field.
- Why an LLM: earnings docs aren't standardized — Tesla is table-heavy, Citi is a transcript, everyone phrases metrics differently.
- AI is good at varied language/layout: "this sentence is EPS," "this line looks like total revenue."
- It does NOT own the process — only the fuzzy reading.
- *(for Adam): I kept the model off the bottleneck — the bottleneck isn't reading, it's trusting the number.*

## Q. What is handled by deterministic code?
**▸ 30s:** "Everything that needs to be reliable, repeatable, and auditable."
**▸ Deeper:**
- Reading pages, selecting high-signal pages, enforcing the schema, adding missing rows, company identity, unit normalization, citation validation, number-in-quote check, consistency checks, review flags, Excel formatting.
- Why: these aren't tasks where I want probabilistic behavior.
- "$21.6 billion → 21,600 USD millions should happen the same way every time. A quote not on its page should always be flagged."
- **One line:** "The model reads. The deterministic layer enforces the contract."

## Q. Why a review-first workflow?
**▸ 30s:** "In finance, one wrong number has real consequences — so I never let the system silently finalize a value."
**▸ Deeper:**
- A high-confidence answer can still be wrong — right number, wrong period / line item / accounting basis.
- Goal wasn't to replace the analyst — it was to remove the slow part: hunting PDFs, copying citations, checking evidence.
- So: draft + source evidence + review reasons → human approves/rejects/marks N/A.
- Saves analyst time without removing analyst judgment.
- *(mine, proposed to Andrew — silently filling an unsupported value is the expensive failure.)*

## Q. How do validation and review flags work?
**▸ 30s:** "Validation never changes a value — it only decides if a human should look, and tells them why."
**▸ Deeper — every metric carries needs_review + review_reason. Checks:**
- Is there a source quote? Is it on the cited page? Does the number appear in the quote?
- Confidence below threshold? Field blank? Scale suspicious? Consistency (OCF − capex = FCF)?
- Fail any → flagged with a plain-English reason, never silently accepted.
- Confidence is just a triage signal — the real value is showing the reviewer *where the number came from and why we're unsure.*
- **Key design:** `needs_review` (automated) vs `review_status` (human) are separate. A value can pass every check and still be "pending" until a human approves. Export = approved only.

## Q. How do you handle failures?
**▸ 30s:** "I make failures visible instead of hiding them — a flag or a blank, never a silent wrong number."
**▸ Deeper:**
- Wrong page but right quote → code searches PDF; if quote is on exactly one page, fix it; if zero or many, don't guess — flag.
- Malformed model output → schema rejects it or repairs only harmless formatting; never invents a value.
- Batch: one bad PDF doesn't fail the batch — it's marked failed, the rest continue, workbook shows status.
- **One line:** "Fail visibly, recover only when safe, route uncertainty to review. A blank beats a confident wrong number."

## Q. What trade-offs did you make?
**▸ 30s:** "The biggest was a deterministic pipeline instead of an autonomous agent."
**▸ Deeper:**
- **Pipeline vs agent:** an agent lets the model decide what to do next — more flexible, but too hard to test and audit for finance. I chose fixed steps the model can't reorder → easier to debug, evaluate, explain.
- **Page selection:** only high-signal pages to the model (numeric density + financial terms). Win: small prompts, low cost. Cost: recall — a page can fall outside the top set. Mitigation: only the *extraction* call is capped; identity, capital-return, validation read the full document.
- **Review-first vs auto-export:** adds a human step, but a wrong number is more dangerous than a slower workflow.
- **Extra live LLM checks (line-item, verifier):** cost more / slower, but catch wrong-row and full-year-vs-quarter errors.

## Q. What would you improve?
**▸ 30s (technical / "rebuild" framing):** "Retrieval — page selection. It's the bottleneck: if the right page never reaches the model, nothing downstream can recover it."
**▸ Deeper:**
- Per-category coverage: guarantee a candidate page for income statement, cash flow, EPS, capital-return so one can't crowd out the others.
- Scale page budget with document length; measure evidence-page recall on a real corpus.
- Then: clearer validation states (split model-confidence / checks-passed / human-decision); broader stratified eval (field-level + review-queue precision/recall).
**▸ If framed as "from user feedback" → lead with batch UX (Adam):**
- One bad PDF shouldn't fail the batch; missing metrics shouldn't block a draft export; errors written for the user, not the developer.
- "I made a first pass — a batch CLI that produces a draft/unreviewed workbook + status sheet. With more time: better progress reporting, per-PDF failure summaries, batch-level eval."

## Cost (likely asked)
**▸ 30s:** "Measured, not guessed — main extraction call is ~7–9k tokens per PDF across ~30 docs."
- Honest caveats: that's the extraction call only (verifier/line-item add more, unmetered); exact $ needs the price sheet × tokens.
- Even at a few cents/PDF, well under the benchmark — $50/hr ÷ 10–15 PDFs = $3–5/PDF → ~2 orders of magnitude cheaper.

## Accuracy (likely asked)
**▸ 30s:** "Not one aggregate number — field-level with tolerances, plus whether unsupported fields were correctly left blank."
- 9/9 on each golden doc; deterministic layer takes raw 15/18 → 18/18.
- Built quality in: golden values live outside the extraction package; a test blocks the runtime from importing them → score = real output, not a leaked answer key.
- Honest caveat: 2 docs = v1 signal, not a production guarantee.

## Follow-up zingers (A1)
- **"needs_review=False means correct?"** → "No — it passed the checks I built, not factually correct. That's why review_status is separate. The human approval and the verifier catch errors the rules don't enumerate."
- **"Does validation ever edit a value?"** → "Two narrow repairs do — table-scale misparse and snapping a quote to its real page. Both still flag the row. Everything else only flags."
- **"Define a true-positive review flag."** → "TP: a value that genuinely needed judgment got flagged. FP: a correct value flagged unnecessarily — review fatigue. FN: a wrong value slipped through unflagged — the dangerous one. Drive FN→0 first, then cut FP so analysts keep trusting the queue."

---

# ASSIGNMENT 2 — Message Queue (no message loss)

**Core principle (mirrors A1):** the system should **never silently lose an acknowledged message.** If uncertain → reject, retry, or redeliver.

## Q. Summarize your critique.
**▸ 30s:** "I started with the hardest requirement: once an enqueue is acknowledged, the message must not be lost. The original design has three ways to violate that."
**▸ Deeper:**
- Each message body is on **one** storage node → one disk/node failure loses it.
- Controller can crash **after writing the body but before the SQL row** → message exists but can't be found.
- Dequeue **deletes the SQL row before delivery is confirmed** → a crash loses it permanently.
- Fixes: replicate bodies, make storage→SQL recoverable, lease-and-ack instead of delete-on-read.

## Q. What is the most serious problem?
**▸ 30s:** "Any path that loses an acknowledged message. If I pick one — dequeue delete-before-delivery."
- The queue deletes its own record before it knows the consumer received the message.
- Crash after deleting the SQL row but before the response reaches the consumer → gone, and the consumer never got it.
- Directly violates the no-loss requirement.

## Q. How can a message be lost during enqueue?
**▸ 30s:** "Two ways."
- Body written to only one storage node → that node/disk fails → body gone.
- Body written successfully, but controller crashes before the SQL row → body exists on disk, but the queue can't discover it.
- From the customer's view, the message is lost even if the bytes still exist.

## Q. How can a message be lost during dequeue?
**▸ 30s:** "Delete-on-read."
- Original flow: mark delivered → read body → delete SQL row → return to consumer.
- Crash after deleting the row but before the response reaches the consumer → queue forgot it, consumer never got it.
- Fix: don't delete on read. Mark in-flight with a **lease**; delete only after **acknowledgment.**

## Q. What would you fix first?
**▸ 30s:** "Anything that can permanently lose an acknowledged message."
- 1. Replicate the message body **before** acknowledging enqueue.
- 2. Make storage→SQL recoverable — stable message ID + reconciliation to repair partial writes.
- 3. Replace delete-on-read with lease-and-ack.
- Then: atomic dequeue claims, SQL failover, storage placement, ordering.

## Q. What trade-off does your solution introduce?
**▸ 30s:** "More complexity and cost — but it matches the requirement."
- Replication → more disk + write latency.
- Lease-and-ack → possible duplicate delivery during failures.
- Reconciliation → background repair work.
- The prompt allows duplicates during outages but **not** loss → choose redelivery over silent loss.
- **One line:** "Never silently lose an acknowledged message. If uncertain — reject, retry, redeliver."

---

# Questions to ask them
- How do you think about where an LLM should and shouldn't sit in a workflow? (I kept the model off the bottleneck.)
- What does "build quality in" look like for an FDE under customer time pressure?
- When you deploy with a customer, how do you surface problems they don't even know to report yet?
- 6 months into a deployment, how do you know it succeeded — adoption, trust, time saved?

# Delivery reminders
- Each answer: weakness first → why reasonable for v1 → how I'd harden it.
- Close on "…so the analyst can trust it" / "…not bad data in the client's sheet" / "…never silently lose a message."
- Demo: never say "live API call" — say "it runs the extraction pipeline."
- Two-part question? Count to 1 → "How many did they ask?" → answer **both** parts.
- One thought = one sentence = one breath. Answer, then stop.
- Tabs: app · result · source PDF · final Excel · README. Plan B if live breaks: "I'll use the saved run."
