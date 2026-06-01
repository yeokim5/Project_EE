from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earnings_extractor.pipeline import extract  # noqa: E402
from earnings_extractor.schema import DraftRun  # noqa: E402
from scripts.web_lib import template_metric_payloads  # noqa: E402

GOLDEN_DOCS = {
    "tesla": ROOT / "assesment_info" / "TSLA-Q2-2025-Update.pdf",
    "citi": ROOT / "assesment_info" / "citi_earnings_q12025.pdf",
}


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty(204)

    def do_POST(self) -> None:
        try:
            request = _read_request(self)
            response = _process(request)
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
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        return _read_multipart(raw_body, content_type)
    if raw_body:
        return json.loads(raw_body.decode("utf-8"))
    return {}


def _read_multipart(raw_body: bytes, content_type: str) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {content_type}\n" "MIME-Version: 1.0\n\n").encode()
        + raw_body
    )
    payload: dict[str, Any] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        if filename:
            payload["filename"] = filename
            payload["file_bytes"] = part.get_payload(decode=True)
        else:
            payload[name] = part.get_content().strip()
    return payload


def _process(request: dict[str, Any]) -> dict[str, Any]:
    mode = request.get("mode", "live")
    if mode not in {"live", "recorded"}:
        raise ValueError("mode must be 'live' or 'recorded'")

    # export.py loads the Excel template by relative path, and the pipeline
    # reads bundled assets by relative path too; pin CWD to the repo root.
    os.chdir(ROOT)

    with tempfile.TemporaryDirectory(prefix="earnings-extract-") as temp_root:
        temp_dir = Path(temp_root)
        pdf_path = _write_input_pdf(request, mode, temp_dir)
        run_dir = temp_dir / "run"
        draft_path = extract(pdf_path, run_dir, mode=mode)
        draft = DraftRun.model_validate_json(draft_path.read_text(encoding="utf-8"))

        metrics = template_metric_payloads(draft, pdf_path)
        return {
            "ok": True,
            "mode": mode,
            "document_name": pdf_path.name,
            "run_id": draft.run_id,
            "draft": draft.model_dump(mode="json"),
            "metrics": metrics,
            "summary": {
                "metric_count": len(metrics),
                "needs_review_count": sum(
                    1 for metric in metrics if metric["needs_review"]
                ),
                "llm_usage": [
                    usage.model_dump(mode="json") for usage in draft.llm_usage
                ],
            },
        }


def _write_input_pdf(request: dict[str, Any], mode: str, temp_dir: Path) -> Path:
    if mode == "recorded":
        demo_id = request.get("demoDocument") or request.get("demo_document")
        if demo_id not in GOLDEN_DOCS:
            raise ValueError("recorded mode requires demoDocument='tesla' or 'citi'")
        source = GOLDEN_DOCS[str(demo_id)]
        target = temp_dir / source.name
        shutil.copy(source, target)
        return target

    filename = _safe_filename(str(request.get("filename") or "upload.pdf"))
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    target = temp_dir / filename
    file_bytes = request.get("file_bytes")
    if file_bytes is None:
        file_base64 = request.get("fileBase64") or request.get("file_base64")
        if not file_base64:
            raise ValueError("live mode requires an uploaded PDF")
        file_bytes = base64.b64decode(str(file_base64))
    target.write_bytes(file_bytes)
    return target


def _safe_filename(filename: str) -> str:
    candidate = Path(filename).name.strip() or f"upload-{uuid4().hex}.pdf"
    return "".join(
        char if char.isalnum() or char in ".-_" else "_" for char in candidate
    )
