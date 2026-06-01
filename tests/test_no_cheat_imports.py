import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGE = ROOT / "earnings_extractor"
EVAL_BRIDGE = RUNTIME_PACKAGE / "_eval_bridge.py"


def test_runtime_package_does_not_import_evaluation_fixtures() -> None:
    offenders: list[str] = []
    for path in RUNTIME_PACKAGE.rglob("*.py"):
        if path == EVAL_BRIDGE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "evaluation" or alias.name.startswith(
                        "evaluation."
                    ):
                        offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "evaluation" or module.startswith("evaluation."):
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_eval_bridge_is_the_only_runtime_eval_bridge() -> None:
    source = EVAL_BRIDGE.read_text(encoding="utf-8")

    assert 'import_module("evaluation.runner")' in source
