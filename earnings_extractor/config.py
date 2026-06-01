"""Runtime configuration for live extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_REASONING_EFFORT = "low"


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    reasoning_effort: str


def load_openai_config() -> OpenAIConfig:
    # Prefer an .env in the current working directory, but fall back to
    # dotenv's default upward search so the config loads regardless of where
    # the command is invoked from.
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        load_dotenv(dotenv_path=cwd_env)
    else:
        load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for Phase 3 live mode")

    return OpenAIConfig(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        reasoning_effort=(
            os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip()
            or DEFAULT_REASONING_EFFORT
        ),
    )
