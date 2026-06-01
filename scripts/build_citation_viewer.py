"""Build a standalone split-view citation viewer for one extracted PDF.

Renders the source PDF on the left (PDF.js) and the extracted citations on the
right. Clicking a citation jumps to its page and draws a pixel-accurate
highlight over the supporting text. Highlight rectangles are computed
deterministically by ``earnings_extractor.locate`` from the stored page + quote
+ the original PDF; the LLM never produces coordinates, so the viewer is
additive and works on any existing draft.

Usage:

    python scripts/build_citation_viewer.py \
        --draft outputs/run_001/draft_metrics.json \
        --pdf assesment_info/TSLA-Q2-2025-Update.pdf \
        --out outputs/run_001/citations_tesla.html

The output HTML is fully self-contained (the PDF is embedded), so it opens with
a double-click and needs only network access to load PDF.js from the CDN.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from earnings_extractor.locate import locate_evidence_bbox
from earnings_extractor.schema import load_draft

PDFJS_VERSION = "3.11.174"


def build_viewer(draft_path: Path, pdf_path: Path, out_path: Path) -> Path:
    draft = load_draft(draft_path)
    pdf_path = Path(pdf_path)
    pdf_name = str(pdf_path)

    citations = []
    for index, metric in enumerate(draft.metrics):
        # Only render citations that belong to this PDF.
        if _metric_source(metric, draft) not in (None, pdf_name):
            continue
        location = locate_evidence_bbox(
            pdf_path, metric.source_page, metric.source_quote
        )
        rects = []
        if location.matched and location.page_width and location.page_height:
            for rect in location.rects:
                rects.append(
                    {
                        "left": rect.x0 / location.page_width,
                        "top": rect.y0 / location.page_height,
                        "width": (rect.x1 - rect.x0) / location.page_width,
                        "height": (rect.y1 - rect.y0) / location.page_height,
                    }
                )
        citations.append(
            {
                "id": f"cit-{index}",
                "metric_name": metric.metric_name,
                "value": _display_value(metric),
                "page": metric.source_page,
                "quote": metric.source_quote,
                "confidence": metric.confidence,
                "needs_review": metric.needs_review,
                "review_status": metric.review_status,
                "review_reason": metric.review_reason or "",
                "rects": rects,
                "matched": bool(rects),
            }
        )

    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    html = _render_html(
        pdf_b64=pdf_b64,
        pdf_label=pdf_path.name,
        citations=citations,
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _metric_source(metric, draft) -> str | None:
    # Single-document drafts have one source; multi-document drafts can be
    # filtered by the document whose pages cover this metric. We keep it simple:
    # if there is exactly one document, everything belongs to it.
    if len(draft.documents) == 1:
        return draft.documents[0].source_file
    return None


def _display_value(metric) -> str:
    if metric.value in (None, ""):
        return "—"
    value = metric.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    suffix = ""
    if metric.unit:
        suffix = f" {metric.unit}"
    if metric.scale:
        suffix += f" ({metric.scale})"
    return f"{value}{suffix}"


def _render_html(pdf_b64: str, pdf_label: str, citations: list[dict]) -> str:
    citations_json = json.dumps(citations)
    return _TEMPLATE.format(
        pdfjs=PDFJS_VERSION,
        pdf_label=_escape(pdf_label),
        pdf_b64=pdf_b64,
        citations_json=citations_json,
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Citation viewer — {pdf_label}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    color: #1a1a1a; background: #f4f5f7;
  }}
  header {{
    padding: 10px 16px; background: #fff; border-bottom: 1px solid #e2e4e8;
    display: flex; align-items: baseline; gap: 12px;
  }}
  header h1 {{ font-size: 15px; margin: 0; font-weight: 600; }}
  header .sub {{ font-size: 12px; color: #6b7280; }}
  .layout {{ display: flex; height: calc(100vh - 44px); }}
  #pdf-pane {{
    flex: 1 1 60%; overflow: auto; padding: 16px; background: #e9ebef;
  }}
  .page-wrap {{
    position: relative; margin: 0 auto 16px; background: #fff;
    box-shadow: 0 1px 4px rgba(0,0,0,.18); width: fit-content;
  }}
  .page-wrap canvas {{ display: block; }}
  .hl {{
    position: absolute; background: rgba(255, 214, 0, .35);
    outline: 1px solid rgba(214, 158, 0, .9); border-radius: 2px;
    pointer-events: none; transition: background .2s;
  }}
  .hl.flash {{ background: rgba(255, 145, 0, .55); }}
  #side {{
    flex: 1 1 40%; max-width: 460px; overflow: auto; background: #fff;
    border-left: 1px solid #e2e4e8; padding: 12px;
  }}
  .cit {{
    border: 1px solid #e2e4e8; border-radius: 8px; padding: 10px 12px;
    margin-bottom: 10px; cursor: pointer;
    transition: border-color .15s, background .15s;
  }}
  .cit:hover {{ border-color: #9aa3af; background: #fafbfc; }}
  .cit.active {{ border-color: #2563eb; background: #eff4ff; }}
  .cit .row1 {{
    display: flex; justify-content: space-between; gap: 8px;
    align-items: baseline;
  }}
  .cit .name {{ font-weight: 600; font-size: 13px; }}
  .cit .val {{ font-variant-numeric: tabular-nums; font-size: 13px; color: #111; }}
  .cit .meta {{
    font-size: 11px; color: #6b7280; margin-top: 4px;
    display: flex; gap: 8px; flex-wrap: wrap;
  }}
  .cit .quote {{ font-size: 12px; color: #374151; margin-top: 6px; line-height: 1.35;
    border-left: 3px solid #d1d5db; padding-left: 8px; }}
  .badge {{
    font-size: 10px; padding: 1px 6px; border-radius: 999px; font-weight: 600;
  }}
  .badge.review {{ background: #fef3c7; color: #92400e; }}
  .badge.ok {{ background: #dcfce7; color: #166534; }}
  .badge.nomatch {{ background: #fee2e2; color: #991b1b; }}
  .reason {{ font-size: 11px; color: #92400e; margin-top: 4px; }}
</style>
</head>
<body>
<header>
  <h1>Citation viewer</h1>
  <span class="sub">{pdf_label} — click a citation to highlight its source</span>
</header>
<div class="layout">
  <div id="pdf-pane"></div>
  <div id="side"></div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/{pdfjs}/pdf.min.js"></script>
<script>
const PDF_B64 = "{pdf_b64}";
const CITATIONS = {citations_json};

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/{pdfjs}/pdf.worker.min.js";

function b64ToBytes(b64) {{
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}}

const pageWraps = {{}};      // pageNumber -> wrapper div
const RENDER_SCALE = 1.4;

async function main() {{
  const pdf = await pdfjsLib.getDocument({{ data: b64ToBytes(PDF_B64) }}).promise;
  const pane = document.getElementById("pdf-pane");

  for (let n = 1; n <= pdf.numPages; n++) {{
    const page = await pdf.getPage(n);
    const viewport = page.getViewport({{ scale: RENDER_SCALE }});
    const wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.style.width = viewport.width + "px";
    wrap.style.height = viewport.height + "px";
    wrap.dataset.page = n;
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    wrap.appendChild(canvas);
    pane.appendChild(wrap);
    pageWraps[n] = wrap;
    await page.render({{ canvasContext: canvas.getContext("2d"), viewport }}).promise;
  }}

  buildSidebar();
}}

function buildSidebar() {{
  const side = document.getElementById("side");
  CITATIONS.forEach((c) => {{
    const el = document.createElement("div");
    el.className = "cit";
    el.id = c.id;
    const status = c.needs_review
      ? '<span class="badge review">needs review</span>'
      : '<span class="badge ok">ok</span>';
    const match = c.matched
      ? ""
      : '<span class="badge nomatch">no on-page match</span>';
    const reason = c.review_reason
      ? '<div class="reason">' + escapeHtml(c.review_reason) + "</div>"
      : "";
    el.innerHTML =
      '<div class="row1"><span class="name">' + escapeHtml(c.metric_name) +
      '</span><span class="val">' + escapeHtml(String(c.value)) + "</span></div>" +
      '<div class="meta"><span>page ' + c.page + "</span>" +
      "<span>conf " + c.confidence.toFixed(2) + "</span>" + status + match +
      "</div>" + reason +
      '<div class="quote">' + escapeHtml(c.quote) + "</div>";
    el.addEventListener("click", () => activate(c));
    side.appendChild(el);
  }});
}}

let activeCit = null;
function activate(c) {{
  document.querySelectorAll(".cit.active").forEach((e) => e.classList.remove("active"));
  document.querySelectorAll(".hl").forEach((e) => e.remove());
  const card = document.getElementById(c.id);
  if (card) card.classList.add("active");
  activeCit = c;

  const wrap = pageWraps[c.page];
  if (!wrap) return;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  c.rects.forEach((r) => {{
    const hl = document.createElement("div");
    hl.className = "hl flash";
    hl.style.left = (r.left * w) + "px";
    hl.style.top = (r.top * h) + "px";
    hl.style.width = (r.width * w) + "px";
    hl.style.height = (r.height * h) + "px";
    wrap.appendChild(hl);
    setTimeout(() => hl.classList.remove("flash"), 700);
  }});

  if (c.rects.length) {{
    const first = c.rects[0];
    const target = wrap.offsetTop + first.top * h - 80;
    document.getElementById("pdf-pane").scrollTo({{ top: target, behavior: "smooth" }});
  }} else {{
    wrap.scrollIntoView({{ behavior: "smooth", block: "start" }});
  }}
}}

function escapeHtml(s) {{
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}}

main().catch((err) => {{
  document.getElementById("pdf-pane").textContent = "Failed to render PDF: " + err;
}});
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = build_viewer(args.draft, args.pdf, args.out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
