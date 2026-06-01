"""Explicit bridge from CLI to eval-only code."""

from __future__ import annotations

import importlib
from pathlib import Path


def run_eval(draft_path: Path, document_id: str) -> str:
    runner = importlib.import_module("evaluation.runner")
    return runner.run(draft_path=draft_path, document_id=document_id)


def run_eval_with_accuracy(draft_path: Path, document_id: str) -> tuple[str, float]:
    runner = importlib.import_module("evaluation.runner")
    return runner.run_with_accuracy(draft_path=draft_path, document_id=document_id)
