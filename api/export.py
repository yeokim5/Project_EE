from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earnings_extractor.export import export_reviewed_run  # noqa: E402
from earnings_extractor.schema import DraftRun  # noqa: E402
from scripts.web_lib import build_decisions_file, merge_drafts  # noqa: E402

EXCEL_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty(204)

    def do_POST(self) -> None:
        try:
            request = _read_request(self)
            response = _export(request)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self._send_json(response)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _read_request(request: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(request.headers.get("content-length", "0"))
    raw_body = request.rfile.read(content_length)
    if raw_body:
        return json.loads(raw_body.decode("utf-8"))
    return {}


def _export(request: dict[str, Any]) -> dict[str, Any]:
    documents = request.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("export requires a non-empty 'documents' list")

    reviewer = str(request.get("reviewer") or "Web reviewer")
    drafts: list[DraftRun] = []
    per_document_decisions: list[list[dict[str, Any]]] = []
    for entry in documents:
        if not isinstance(entry, dict) or "draft" not in entry:
            raise ValueError("each document needs a 'draft'")
        drafts.append(DraftRun.model_validate(entry["draft"]))
        per_document_decisions.append(list(entry.get("decisions") or []))

    merged, offsets = merge_drafts(drafts)
    decisions = build_decisions_file(
        merged, offsets, per_document_decisions, reviewer=reviewer
    )

    # export.py loads the Excel template by relative path; pin CWD to repo root.
    os.chdir(ROOT)

    with tempfile.TemporaryDirectory(prefix="earnings-export-") as temp_root:
        run_dir = Path(temp_root) / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "draft_metrics.json").write_text(
            json.dumps(merged.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        decisions_path = run_dir / "review_decisions.json"
        decisions_path.write_text(
            json.dumps(decisions.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        out_path = Path(temp_root) / "earnings_extraction.xlsx"
        artifacts = export_reviewed_run(
            run_dir=run_dir,
            decisions_path=decisions_path,
            out_path=out_path,
            allow_unreviewed=False,
        )
        workbook_b64 = base64.b64encode(artifacts.xlsx_path.read_bytes()).decode(
            "ascii"
        )

    return {
        "ok": True,
        "run_id": merged.run_id,
        "is_draft_unreviewed": artifacts.is_draft_unreviewed,
        "workbook": {
            "filename": "earnings_extraction.xlsx",
            "content_type": EXCEL_CONTENT_TYPE,
            "base64": workbook_b64,
        },
    }
