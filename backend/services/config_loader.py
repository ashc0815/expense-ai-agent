"""Shared config & prompt loader — single source of truth for eval + production.

Both the eval dashboard and business code read from the same JSON files:
  - eval_config.json  → fraud rule thresholds, ambiguity weights, LLM params
  - eval_prompts.json → all LLM prompt templates (versioned)

Dashboard edits these files via the API; business code reads them at call time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parents[1] / "tests"
_CONFIG_PATH = _BASE / "eval_config.json"
_PROMPTS_PATH = _BASE / "eval_prompts.json"


def load_config() -> dict[str, Any]:
    """Load full eval config. Returns empty dict if file missing."""
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def load_thresholds() -> dict[str, Any]:
    """Load config_thresholds section — used by fraud_rules.py."""
    cfg = load_config()
    return cfg.get("config_thresholds", {})


def load_ambiguity_weights() -> dict[str, float]:
    """Load ambiguity detector factor weights."""
    cfg = load_config()
    return cfg.get("ambiguity_weights", {})


def load_ambiguity_trigger_threshold() -> int:
    """Load ambiguity LLM trigger threshold."""
    cfg = load_config()
    return cfg.get("ambiguity_llm_trigger_threshold", 50)


def load_llm_params() -> dict[str, Any]:
    """Load LLM parameters (model, temperature, max_tokens)."""
    cfg = load_config()
    return {
        "model": cfg.get("model", "gpt-4o"),
        "temperature": cfg.get("temperature", 0),
        "max_tokens": cfg.get("max_tokens", 1024),
    }


def load_prompt(key: str, version: Optional[str] = None) -> str:
    """Load a prompt's content by key, using active_version or specified version.

    Returns empty string if key/version not found.
    """
    if not _PROMPTS_PATH.exists():
        return ""
    data = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
    prompt = data.get("prompts", {}).get(key)
    if not prompt:
        return ""
    ver = version or prompt.get("active_version", "v1")
    ver_data = prompt.get("versions", {}).get(ver, {})
    return ver_data.get("content", "")
