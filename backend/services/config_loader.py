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


# ── Per-tier risk thresholds and per-factor knobs ──────────────────────
# These were hardcoded constants in agent/ambiguity_detector.py until we
# wired them through here so the eval dashboard can tune sensitivity per
# client without a code change. Defaults preserve the previous behavior.

def load_ambiguity_tier_thresholds() -> dict[str, int]:
    """Score boundaries: < auto_pass_max → auto_pass,
    <= human_review_max → human_review, > human_review_max → suggest_reject.
    """
    cfg = load_config()
    raw = cfg.get("ambiguity_thresholds", {})
    return {
        "auto_pass_max": int(raw.get("auto_pass_max", 30)),
        "human_review_max": int(raw.get("human_review_max", 70)),
    }


def load_ambiguity_vague_words() -> list[str]:
    """Words that, when present in the description, mark it as vague."""
    cfg = load_config()
    return list(
        cfg.get("ambiguity_factors", {})
           .get("description", {})
           .get("vague_words", [])
    )


def load_ambiguity_description_short(
) -> tuple[int, float]:
    """Return (short_threshold_chars, short_score). A description shorter
    than `short_threshold_chars` and not flagged as vague gets `short_score`.
    """
    cfg = load_config()
    desc = cfg.get("ambiguity_factors", {}).get("description", {})
    return (
        int(desc.get("short_threshold_chars", 10)),
        float(desc.get("short_score", 50.0)),
    )


def load_ambiguity_boundary_band() -> tuple[float, float]:
    """Return (lower_pct, upper_pct) defining the amount-boundary band
    around the policy limit (e.g. 0.90, 1.10 → 90%-110% triggers)."""
    cfg = load_config()
    band = cfg.get("ambiguity_factors", {}).get("amount_boundary", {})
    return (
        float(band.get("lower_pct", 0.90)),
        float(band.get("upper_pct", 1.10)),
    )


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
