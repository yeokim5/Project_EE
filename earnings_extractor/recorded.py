"""Cassette-style recorded extraction payloads for keyless verification."""

from __future__ import annotations

import json
from pathlib import Path

from earnings_extractor.schema import MetricsBatch, repair_metric_batch

RECORDED_RESPONSES_DIR = Path(__file__).resolve().parent / "recorded_responses"


def extract_metrics_recorded(pdf_path: Path) -> MetricsBatch:
    cassette_path = RECORDED_RESPONSES_DIR / f"{pdf_path.name}.json"
    if not cassette_path.is_file():
        raise FileNotFoundError(f"No recorded extraction available for {pdf_path}")
    payload = json.loads(cassette_path.read_text(encoding="utf-8"))
    return repair_metric_batch(payload)
