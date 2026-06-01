#!/usr/bin/env python3
"""Refresh the generated file tree in docs/PROJECT_MAP.md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_MAP = ROOT / "docs" / "PROJECT_MAP.md"
START = "<!-- FILE_TREE:START -->"
END = "<!-- FILE_TREE:END -->"

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".vercel",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "outputs",
}

EXCLUDED_FILES = {
    ".DS_Store",
    ".env",
    "tsconfig.tsbuildinfo",
}


def is_excluded(path: Path) -> bool:
    if path.is_dir() and path.name in EXCLUDED_DIRS:
        return True
    if path.is_dir() and path.name.endswith(".egg-info"):
        return True
    if path.is_file() and path.name in EXCLUDED_FILES:
        return True
    return any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts)


def sorted_children(path: Path) -> list[Path]:
    children = [child for child in path.iterdir() if not is_excluded(child)]
    return sorted(children, key=lambda child: (not child.is_dir(), child.name.lower()))


def build_tree(max_depth: int) -> str:
    lines = ["."]

    def walk(path: Path, prefix: str = "", depth: int = 0) -> None:
        children = sorted_children(path)
        if depth >= max_depth:
            if children:
                lines.append(f"{prefix}`-- ...")
            return

        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            connector = "`-- " if is_last else "|-- "
            suffix = "/" if child.is_dir() else ""
            lines.append(f"{prefix}{connector}{child.name}{suffix}")

            if child.is_dir():
                child_prefix = "    " if is_last else "|   "
                walk(child, prefix + child_prefix, depth + 1)

    walk(ROOT)
    return "\n".join(lines)


def replace_tree(content: str, tree: str) -> str:
    if START not in content or END not in content:
        raise ValueError(f"{PROJECT_MAP} must contain {START} and {END} markers")

    before, rest = content.split(START, 1)
    _, after = rest.split(END, 1)
    generated = f"{START}\n```text\n{tree}\n```\n{END}"
    return before + generated + after


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the generated file tree in docs/PROJECT_MAP.md."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if docs/PROJECT_MAP.md is stale instead of rewriting it.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="Maximum directory depth to render. Defaults to 4.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    content = PROJECT_MAP.read_text(encoding="utf-8")
    updated = replace_tree(content, build_tree(max_depth=args.max_depth))

    if args.check:
        if updated != content:
            print(
                "docs/PROJECT_MAP.md is stale. "
                "Run: python3 scripts/update_project_map.py"
            )
            return 1
        print("docs/PROJECT_MAP.md is up to date.")
        return 0

    PROJECT_MAP.write_text(updated, encoding="utf-8")
    print("Updated docs/PROJECT_MAP.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
