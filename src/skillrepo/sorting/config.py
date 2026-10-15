"""Where the skill judge sends its prompts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path("catalog") / "judge_config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1"
DEFAULT_MODEL = "Qwen/Qwen3.8-27B"


@dataclass(frozen=True)
class JudgeConfig:
    """OpenAI-compatible chat endpoint used to judge skill copies."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout_seconds: float = 180
    max_variants: int = 4
    max_chars: int = 6000
    concurrency: int = 3
    submodule_timeout_seconds: float = 90

    def endpoint(self, suffix: str) -> str:
        return self.base_url.rstrip("/") + "/" + suffix.lstrip("/")


def load_judge_config(root: Path | None = None, path: Path | None = None) -> JudgeConfig:
    """Load ``catalog/judge_config.json``, then let environment variables override it."""

    root = root or Path.cwd()
    config_path = path or (root / CONFIG_PATH)
    raw: dict = {}
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    base_url = os.environ.get("SKILLREPO_LLM_BASE_URL") or raw.get("base_url") or DEFAULT_BASE_URL
    model = os.environ.get("SKILLREPO_LLM_MODEL") or raw.get("model") or DEFAULT_MODEL
    api_key = os.environ.get("SKILLREPO_LLM_API_KEY")
    if api_key is None:
        api_key = raw.get("api_key") or ""
    return JudgeConfig(
        base_url=str(base_url),
        model=str(model),
        api_key=str(api_key),
        timeout_seconds=float(raw.get("timeout_seconds") or 180),
        max_variants=int(raw.get("max_variants") or 4),
        max_chars=int(raw.get("max_chars") or 6000),
        concurrency=int(raw.get("concurrency") or 3),
        submodule_timeout_seconds=float(raw.get("submodule_timeout_seconds") or 90),
    )
