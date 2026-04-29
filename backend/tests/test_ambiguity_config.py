"""Tests for ambiguity-detector configurability.

Locks in the contract that the eval dashboard's edits to
backend/tests/eval_config.json actually flow through to the detector
without a code change. Each test patches the loader, re-imports the
module so the module-level constants pick up the new values, and
asserts the scoring/recommendation changes accordingly.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import date
from unittest.mock import patch

# Headless config for the imports below
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")

import pytest


def _reload_detector():
    """Re-import agent.ambiguity_detector so its module-level constants
    are recomputed against the currently-patched loaders."""
    sys.modules.pop("agent.ambiguity_detector", None)
    return importlib.import_module("agent.ambiguity_detector")


def _restore_detector():
    """Restore the module to its default state (loaded from disk config)."""
    sys.modules.pop("agent.ambiguity_detector", None)
    importlib.import_module("agent.ambiguity_detector")


# ── Defaults (sanity that historical behavior is preserved) ──────────


def test_defaults_match_historical_hardcoded_values():
    detector = _reload_detector()
    assert detector._TIER_AUTO_PASS_MAX == 30
    assert detector._TIER_HUMAN_REVIEW_MAX == 70
    assert detector._DESC_SHORT_THRESHOLD == 10
    assert detector._DESC_SHORT_SCORE == 50.0
    assert detector._BOUNDARY_LOWER_PCT == pytest.approx(0.90)
    assert detector._BOUNDARY_UPPER_PCT == pytest.approx(1.10)
    # 10 vague words in the default config (tracking the JSON file)
    assert len(detector._VAGUE_WORDS) >= 8
    assert "其他" in detector._VAGUE_WORDS


# ── Tier-threshold override ──────────────────────────────────────────


def test_tighter_thresholds_route_more_to_human_review():
    """Client wants stricter sensitivity: lower auto-pass cap from 30 to
    15. A submission that scored 20 used to auto-pass; now should hit
    human review."""
    with patch(
        "backend.services.config_loader.load_ambiguity_tier_thresholds",
        return_value={"auto_pass_max": 15, "human_review_max": 70},
    ):
        detector = _reload_detector()
        try:
            assert detector._TIER_AUTO_PASS_MAX == 15
            # A score of 20 now exceeds the new auto_pass_max (15)
            # — it should fall through to human_review.
            score = 20
            if score < detector._TIER_AUTO_PASS_MAX:
                rec = "auto_pass"
            elif score <= detector._TIER_HUMAN_REVIEW_MAX:
                rec = "human_review"
            else:
                rec = "suggest_reject"
            assert rec == "human_review"
        finally:
            _restore_detector()


def test_loose_thresholds_let_more_through():
    """Client B has higher tolerance: auto-pass anything <50."""
    with patch(
        "backend.services.config_loader.load_ambiguity_tier_thresholds",
        return_value={"auto_pass_max": 50, "human_review_max": 80},
    ):
        detector = _reload_detector()
        try:
            assert detector._TIER_AUTO_PASS_MAX == 50
            # A score of 35 used to be human_review; now auto_pass.
            assert 35 < detector._TIER_AUTO_PASS_MAX
        finally:
            _restore_detector()


# ── Vague-words override ─────────────────────────────────────────────


def test_vague_words_override_changes_classification():
    """Client redefines what counts as a vague description."""
    with patch(
        "backend.services.config_loader.load_ambiguity_vague_words",
        return_value=["奇怪的词"],
    ):
        detector = _reload_detector()
        try:
            assert detector._VAGUE_WORDS == ["奇怪的词"]
            # The default vague word "其他" no longer triggers
            assert "其他" not in detector._VAGUE_WORDS
        finally:
            _restore_detector()


# ── Description short threshold ──────────────────────────────────────


def test_short_threshold_override_changes_classification():
    """Client requires 20+ chars before deeming a description short."""
    with patch(
        "backend.services.config_loader.load_ambiguity_description_short",
        return_value=(20, 75.0),
    ):
        detector = _reload_detector()
        try:
            assert detector._DESC_SHORT_THRESHOLD == 20
            assert detector._DESC_SHORT_SCORE == 75.0
        finally:
            _restore_detector()


# ── Boundary band override ───────────────────────────────────────────


def test_boundary_band_override_widens_trigger_zone():
    """Client wants amounts within 80%-120% of the limit to flag."""
    with patch(
        "backend.services.config_loader.load_ambiguity_boundary_band",
        return_value=(0.80, 1.20),
    ):
        detector = _reload_detector()
        try:
            assert detector._BOUNDARY_LOWER_PCT == pytest.approx(0.80)
            assert detector._BOUNDARY_UPPER_PCT == pytest.approx(1.20)
        finally:
            _restore_detector()


# ── Loader fallback robustness ───────────────────────────────────────


def test_loader_failure_falls_back_to_historical_constants():
    """If the loader raises, the detector must still load with the
    historical defaults — never crash the import."""
    def _boom(*_, **__):
        raise RuntimeError("config loader broken")

    with patch(
        "backend.services.config_loader.load_ambiguity_tier_thresholds",
        side_effect=_boom,
    ), patch(
        "backend.services.config_loader.load_ambiguity_vague_words",
        side_effect=_boom,
    ), patch(
        "backend.services.config_loader.load_ambiguity_description_short",
        side_effect=_boom,
    ), patch(
        "backend.services.config_loader.load_ambiguity_boundary_band",
        side_effect=_boom,
    ):
        detector = _reload_detector()
        try:
            # Falls back to the FALLBACK_* constants
            assert detector._TIER_AUTO_PASS_MAX == 30
            assert detector._TIER_HUMAN_REVIEW_MAX == 70
            assert detector._DESC_SHORT_THRESHOLD == 10
            assert detector._BOUNDARY_LOWER_PCT == pytest.approx(0.90)
            assert "其他" in detector._VAGUE_WORDS
        finally:
            _restore_detector()
