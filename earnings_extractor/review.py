"""Human review artifact generation and decision validation."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from earnings_extractor.schema import DraftRun, MetricRow, ReviewStatus, utc_now_iso
from earnings_extractor.validation import PLACEHOLDER_SOURCE_QUOTE

DecisionStatus = Literal["approved", "rejected", "needs_fix", "not_applicable"]


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    metric_index: int
    source_file: str
    source_resolution_failed: bool
    requires_attention: bool
    company: str | None = None
    ticker: str | None = None
    document_type: str
    fiscal_period: str | None = None
    report_date: str | None = None
    metric_name: str
    metric_category: str | None = None
    segment: str | None = None
    value: float | str | None = None
    unit: str | None = None
    scale: str | None = None
    period: str | None = None
    gaap_or_non_gaap: str | None = None
    year_over_year_change: float | str | None = None
    source_page: int
    source_quote: str
    confidence: float
    needs_review: bool
    review_reason: str | None = None
    review_status: ReviewStatus
    reviewer_note: str | None = None


class ReviewQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    draft_created_at: str
    is_demo: bool = False
    items: list[ReviewItem]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    review_status: DecisionStatus
    reviewer_note: str | None = None
    reviewed_at: str
    reviewer: str

    @field_validator("metric_id", "reviewed_at", "reviewer")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ReviewDecisionsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    is_demo: bool
    decisions: list[ReviewDecision]

    @field_validator("run_id", "created_at")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class ReviewArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_queue_path: Path
    evidence_report_path: Path
    review_html_path: Path
    review_decisions_path: Path | None = None


def build_review_items(draft: DraftRun) -> list[ReviewItem]:
    """Build one review item per draft metric, preserving draft order."""

    type_counts = Counter(doc.document_type for doc in draft.documents)
    items: list[ReviewItem] = []
    for index, metric in enumerate(draft.metrics):
        source_file = _resolve_source_file(metric, draft, type_counts)
        source_resolution_failed = source_file is None
        items.append(
            ReviewItem(
                metric_id=_metric_id(draft.run_id, index),
                metric_index=index,
                source_file=source_file or "unknown",
                source_resolution_failed=source_resolution_failed,
                requires_attention=metric.needs_review or source_resolution_failed,
                company=metric.company,
                ticker=metric.ticker,
                document_type=metric.document_type,
                fiscal_period=metric.fiscal_period,
                report_date=metric.report_date,
                metric_name=metric.metric_name,
                metric_category=metric.metric_category,
                segment=metric.segment,
                value=metric.value,
                unit=metric.unit,
                scale=metric.scale,
                period=metric.period,
                gaap_or_non_gaap=metric.gaap_or_non_gaap,
                year_over_year_change=metric.year_over_year_change,
                source_page=metric.source_page,
                source_quote=metric.source_quote,
                confidence=metric.confidence,
                needs_review=metric.needs_review,
                review_reason=metric.review_reason,
                review_status=metric.review_status,
                reviewer_note=metric.reviewer_note,
            )
        )
    return items


def write_review_artifacts(
    run_dir: Path,
    html_out: Path | None = None,
    demo_decisions_out: Path | None = None,
) -> ReviewArtifacts:
    draft_path = run_dir / "draft_metrics.json"
    draft = _load_draft_file(draft_path)
    html_path = html_out or run_dir / "review.html"
    queue_path = run_dir / "review_queue.json"
    evidence_path = run_dir / "evidence_report.md"

    items = build_review_items(draft)
    queue = ReviewQueue(
        run_id=draft.run_id,
        created_at=utc_now_iso(),
        draft_created_at=draft.created_at,
        items=items,
    )
    queue_path.write_text(
        json.dumps(queue.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    evidence_path.write_text(_render_evidence_report(draft, items), encoding="utf-8")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_render_review_html(queue), encoding="utf-8")

    if demo_decisions_out is not None:
        demo_decisions_out.parent.mkdir(parents=True, exist_ok=True)
        decisions = _build_demo_decisions(draft.run_id, items)
        demo_decisions_out.write_text(
            json.dumps(decisions.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    return ReviewArtifacts(
        review_queue_path=queue_path,
        evidence_report_path=evidence_path,
        review_html_path=html_path,
        review_decisions_path=demo_decisions_out,
    )


def load_review_decisions(
    path: Path,
    expected_run_id: str | None = None,
) -> ReviewDecisionsFile:
    try:
        decisions = ReviewDecisionsFile.model_validate_json(path.read_text("utf-8"))
    except ValidationError as exc:
        raise ValueError(f"Invalid review decisions file: {path}") from exc

    if expected_run_id is not None and decisions.run_id != expected_run_id:
        raise ValueError(
            "Review decisions run_id does not match draft run_id: "
            f"{decisions.run_id!r} != {expected_run_id!r}"
        )
    metric_ids = [decision.metric_id for decision in decisions.decisions]
    duplicates = sorted(
        metric_id for metric_id, count in Counter(metric_ids).items() if count > 1
    )
    if duplicates:
        duplicate_text = ", ".join(duplicates)
        raise ValueError(f"Duplicate review decision metric_id(s): {duplicate_text}")
    return decisions


def _load_draft_file(path: Path) -> DraftRun:
    try:
        return DraftRun.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing draft metrics file: {path}") from None
    except ValidationError as exc:
        raise ValueError(f"Invalid draft file: {path}") from exc


def _metric_id(run_id: str, metric_index: int) -> str:
    return f"{run_id}:{metric_index:04d}"


# Corporate suffixes / filler words that should not be used to match a company
# name against a filename (e.g. "Netflix, Inc." should match "netflix_q1.pdf").
_COMPANY_STOPWORDS = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "companies",
        "ltd",
        "limited",
        "plc",
        "llc",
        "lp",
        "llp",
        "group",
        "holdings",
        "holding",
        "the",
        "and",
        "sa",
        "ag",
        "nv",
        "class",
    }
)


def _significant_tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens, minus corporate filler and 1-char noise."""

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {
        token
        for token in tokens
        if len(token) > 1 and token not in _COMPANY_STOPWORDS
    }


def _resolve_source_file(
    metric: MetricRow,
    draft: DraftRun,
    type_counts: Counter[str],
) -> str | None:
    if metric.source_file:
        return metric.source_file
    if len(draft.documents) == 1:
        return draft.documents[0].source_file
    if type_counts[metric.document_type] == 1:
        return next(
            doc.source_file
            for doc in draft.documents
            if doc.document_type == metric.document_type
        )

    # Multiple same-type documents: match the metric's company/ticker against
    # each filename by shared significant tokens, so "Netflix, Inc." and ticker
    # "NFLX" still resolve to "netflix_q1_2025.pdf". Pick the unambiguous best
    # match; bail out (None) only when nothing matches or two docs tie.
    candidate_tokens: set[str] = set()
    for candidate in (metric.company, metric.ticker):
        if candidate:
            candidate_tokens |= _significant_tokens(candidate)
    if not candidate_tokens:
        return None

    scored: list[tuple[int, str]] = []
    for doc in draft.documents:
        stem_tokens = _significant_tokens(Path(doc.source_file).stem)
        overlap = len(candidate_tokens & stem_tokens)
        if overlap:
            scored.append((overlap, doc.source_file))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous: two documents match equally well
    return scored[0][1]


def _render_evidence_report(draft: DraftRun, items: list[ReviewItem]) -> str:
    lines = [
        "# Evidence Report",
        "",
        f"- Run ID: `{draft.run_id}`",
        f"- Draft created: `{draft.created_at}`",
        f"- Metrics: `{len(items)}`",
        "",
    ]

    grouped: dict[str, list[ReviewItem]] = defaultdict(list)
    for item in items:
        group_name = f"{item.company or 'Unknown company'} — {item.source_file}"
        grouped[group_name].append(item)

    for group_name in sorted(grouped):
        lines.extend(
            [
                f"## {group_name}",
                "",
                "| Metric ID | Metric | Value | Page | Confidence | "
                "Needs review | Review reason | Source quote |",
                "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
            ]
        )
        for item in grouped[group_name]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(item.metric_id),
                        _md_cell(item.metric_name),
                        _md_cell(_display_value(item.value)),
                        str(item.source_page),
                        f"{item.confidence:.2f}",
                        "yes" if item.needs_review else "no",
                        _md_cell(item.review_reason or ""),
                        _md_cell(item.source_quote),
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines)


def _render_review_html(queue: ReviewQueue) -> str:
    data_json = json.dumps(queue.model_dump(mode="json"), ensure_ascii=False)
    safe_json = data_json.replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Earnings Extraction Review</title>
  <style>
    :root {{
      color-scheme: light;
      --border: #d8dee4;
      --muted: #57606a;
      --attention: #9a3412;
      --ok: #166534;
      --bg: #f6f8fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #24292f;
      background: white;
    }}
    header {{
      padding: 20px 24px;
      border-bottom: 1px solid var(--border);
      background: var(--bg);
    }}
    main {{ padding: 20px 24px 40px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .demo-note {{
      margin-top: 12px;
      padding: 10px 12px;
      border: 1px solid #facc15;
      background: #fefce8;
      font-size: 14px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
      margin-bottom: 16px;
    }}
    label {{ display: grid; gap: 4px; font-size: 13px; color: var(--muted); }}
    input, select, textarea, button {{
      font: inherit;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      background: white;
    }}
    button {{
      cursor: pointer;
      background: #0969da;
      color: white;
      border-color: #0969da;
    }}
    .metric {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      margin: 12px 0;
    }}
    .metric.attention {{ border-left: 4px solid var(--attention); }}
    .metric.ok {{ border-left: 4px solid var(--ok); }}
    .metric-header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 8px;
    }}
    .metric-title {{ font-weight: 700; }}
    .pill {{
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      color: var(--muted);
    }}
    dl {{
      display: grid;
      grid-template-columns: minmax(120px, 180px) 1fr;
      gap: 6px 12px;
      margin: 10px 0;
    }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    blockquote {{
      margin: 10px 0;
      padding: 10px 12px;
      background: var(--bg);
      border-left: 3px solid var(--border);
      white-space: pre-wrap;
    }}
    .decision-row {{
      display: grid;
      grid-template-columns: minmax(160px, 220px) 1fr;
      gap: 10px;
      align-items: start;
      margin-top: 10px;
    }}
    .hidden {{ display: none; }}
  </style>
</head>
<body>
  <header>
    <h1>Earnings Extraction Review</h1>
    <div class="meta" id="summary"></div>
    <div class="demo-note">
      Downloaded decisions from this page are human review files.
      Auto-generated demo decisions are created only by the CLI
      <code>--demo-decisions</code> flag and must not be treated as
      production approval.
    </div>
  </header>
  <main>
    <section class="toolbar" aria-label="Review controls">
      <label>Filter
        <select id="filter">
          <option value="all">All metrics</option>
          <option value="attention">Needs attention</option>
        </select>
      </label>
      <label>Search
        <input id="search" type="search" placeholder="Company, metric, quote">
      </label>
      <label>Reviewer
        <input id="reviewer" type="text" value="Human reviewer">
      </label>
      <button id="download" type="button">Download decisions JSON</button>
    </section>
    <section id="metrics" aria-label="Metrics for review"></section>
  </main>
  <script id="review-data" type="application/json">{safe_json}</script>
  <script>
    const queue = JSON.parse(document.getElementById("review-data").textContent);
    const decisions = new Map();
    const metricsEl = document.getElementById("metrics");
    const filterEl = document.getElementById("filter");
    const searchEl = document.getElementById("search");
    const reviewerEl = document.getElementById("reviewer");

    function text(value) {{
      return value === null || value === undefined || value === ""
        ? "—"
        : String(value);
    }}

    function initialStatus(item) {{
      if (item.value === null || item.value === "") return "not_applicable";
      if (item.requires_attention) return "needs_fix";
      return "approved";
    }}

    function appendField(dl, label, value) {{
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = text(value);
      dl.append(dt, dd);
    }}

    function render() {{
      metricsEl.textContent = "";
      const query = searchEl.value.trim().toLowerCase();
      const filtered = queue.items.filter((item) => {{
        if (filterEl.value === "attention" && !item.requires_attention) return false;
        if (!query) return true;
        return [
          item.company,
          item.ticker,
          item.metric_name,
          item.source_file,
          item.source_quote,
          item.review_reason,
        ].some((value) => text(value).toLowerCase().includes(query));
      }});
      document.getElementById("summary").textContent =
        `Run ${{queue.run_id}} · ${{queue.items.length}} metrics · `
        + `${{filtered.length}} shown`;

      for (const item of filtered) {{
        const current = decisions.get(item.metric_id) || {{
          review_status: initialStatus(item),
          reviewer_note: item.requires_attention ? text(item.review_reason) : "",
        }};
        const card = document.createElement("article");
        card.className = `metric ${{item.requires_attention ? "attention" : "ok"}}`;

        const header = document.createElement("div");
        header.className = "metric-header";
        const title = document.createElement("div");
        title.className = "metric-title";
        title.textContent = `${{text(item.company)}} · ${{item.metric_name}}`;
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = item.requires_attention
          ? "needs attention"
          : "ready for review";
        header.append(title, pill);

        const dl = document.createElement("dl");
        appendField(dl, "Metric ID", item.metric_id);
        appendField(dl, "Value", item.value);
        appendField(
          dl,
          "Unit / scale",
          [item.unit, item.scale].filter(Boolean).join(" / "),
        );
        appendField(dl, "Source PDF", item.source_file);
        appendField(dl, "Source page", item.source_page);
        appendField(dl, "Confidence", Number(item.confidence).toFixed(2));
        appendField(dl, "Needs review", item.needs_review ? "yes" : "no");
        appendField(dl, "Review reason", item.review_reason);

        const quote = document.createElement("blockquote");
        quote.textContent = text(item.source_quote);

        const decisionRow = document.createElement("div");
        decisionRow.className = "decision-row";
        const selectLabel = document.createElement("label");
        selectLabel.textContent = "Decision";
        const select = document.createElement("select");
        for (const status of ["approved", "rejected", "needs_fix", "not_applicable"]) {{
          const option = document.createElement("option");
          option.value = status;
          option.textContent = status;
          select.append(option);
        }}
        select.value = current.review_status;
        select.addEventListener("change", () => {{
          decisions.set(item.metric_id, {{
            ...current,
            review_status: select.value,
            reviewer_note: note.value,
          }});
        }});
        selectLabel.append(select);

        const noteLabel = document.createElement("label");
        noteLabel.textContent = "Reviewer note";
        const note = document.createElement("textarea");
        note.rows = 2;
        note.value = current.reviewer_note || "";
        note.addEventListener("input", () => {{
          decisions.set(item.metric_id, {{
            review_status: select.value,
            reviewer_note: note.value,
          }});
        }});
        noteLabel.append(note);
        decisionRow.append(selectLabel, noteLabel);
        card.append(header, dl, quote, decisionRow);
        metricsEl.append(card);
      }}
    }}

    function downloadDecisions() {{
      const now = new Date().toISOString();
      const payload = {{
        run_id: queue.run_id,
        created_at: now,
        is_demo: false,
        decisions: queue.items.map((item) => {{
          const current = decisions.get(item.metric_id) || {{
            review_status: initialStatus(item),
            reviewer_note: item.requires_attention ? text(item.review_reason) : "",
          }};
          return {{
            metric_id: item.metric_id,
            review_status: current.review_status,
            reviewer_note: current.reviewer_note || null,
            reviewed_at: now,
            reviewer: reviewerEl.value.trim() || "Human reviewer",
          }};
        }}),
      }};
      const blob = new Blob([JSON.stringify(payload, null, 2) + "\\n"], {{
        type: "application/json",
      }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "review_decisions.json";
      link.click();
      URL.revokeObjectURL(url);
    }}

    filterEl.addEventListener("change", render);
    searchEl.addEventListener("input", render);
    document.getElementById("download").addEventListener("click", downloadDecisions);
    render();
  </script>
</body>
</html>
"""


def _build_demo_decisions(
    run_id: str,
    items: list[ReviewItem],
) -> ReviewDecisionsFile:
    created_at = utc_now_iso()
    decisions = []
    for item in items:
        status, note = _demo_status_and_note(item)
        decisions.append(
            ReviewDecision(
                metric_id=item.metric_id,
                review_status=status,
                reviewer_note=note,
                reviewed_at=created_at,
                reviewer="Demo reviewer",
            )
        )
    return ReviewDecisionsFile(
        run_id=run_id,
        created_at=created_at,
        is_demo=True,
        decisions=decisions,
    )


def _demo_status_and_note(item: ReviewItem) -> tuple[DecisionStatus, str]:
    if item.value in (None, ""):
        return (
            "not_applicable",
            (
                "Demo shortcut: blank values are marked not_applicable for "
                "deterministic verification; a human must confirm true N/A."
            ),
        )
    if item.source_resolution_failed or item.source_quote == PLACEHOLDER_SOURCE_QUOTE:
        return (
            "needs_fix",
            "Demo decision: source evidence needs human correction before export.",
        )
    return (
        "approved",
        "Demo decision: populated value with source evidence.",
    )


def _display_value(value: float | str | None) -> str:
    if value in (None, ""):
        return ""
    return str(value)


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
