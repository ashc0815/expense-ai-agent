"""Layer decision — pure function, exhaustive case coverage."""
from __future__ import annotations

from backend.quick.layer_decision import decide_layer


def _ocr(amount=150.0, merchant="海底捞", date="2026-04-14", confidence=0.95):
    return {
        "amount": amount, "merchant": merchant, "date": date,
        "confidence": confidence,
    }


def _classify(category="meal", confidence=0.9):
    return {"category": category, "confidence": confidence}


def _dedupe(is_duplicate=False):
    return {"is_duplicate": is_duplicate}


def _budget(signal="ok"):
    return {"signal": signal}


# ── Hard errors ──────────────────────────────────────────────────

def test_ocr_all_empty_is_hard():
    ocr = {"amount": None, "merchant": None, "date": None, "confidence": 0.0}
    assert decide_layer(ocr, _classify(), _dedupe(), _budget()) == "3_hard"


def test_not_a_receipt_is_hard():
    ocr = {"amount": None, "merchant": None, "date": None, "confidence": 0.0,
           "not_a_receipt": True}
    assert decide_layer(ocr, _classify(), _dedupe(), _budget()) == "3_hard"


# ── Soft errors ──────────────────────────────────────────────────

def test_three_fields_need_fix_is_soft():
    # merchant + date missing (2) + classify low-conf (1) + project missing (1) = 4
    ocr = _ocr(merchant=None, date=None)
    classify = _classify(confidence=0.3)
    out = decide_layer(ocr, classify, _dedupe(), _budget(),
                       missing_optional_fields=["project_code"])
    assert out == "3_soft"


# ── Happy path ───────────────────────────────────────────────────

def test_all_high_confidence_is_layer_1():
    assert decide_layer(_ocr(), _classify(), _dedupe(), _budget()) == "1"


# ── Layer 2 ──────────────────────────────────────────────────────

def test_one_optional_missing_is_layer_2():
    out = decide_layer(_ocr(), _classify(), _dedupe(), _budget(),
                       missing_optional_fields=["project_code"])
    assert out == "2"


def test_two_optional_missing_is_layer_2():
    out = decide_layer(_ocr(), _classify(), _dedupe(), _budget(),
                       missing_optional_fields=["project_code", "description"])
    assert out == "2"


def test_classify_mid_confidence_is_layer_2():
    assert decide_layer(_ocr(), _classify(confidence=0.65),
                        _dedupe(), _budget()) == "2"


def test_classify_low_confidence_counts_as_needs_fix():
    # conf < 0.5 = category needs fix; that's 1 field → Layer 2
    assert decide_layer(_ocr(), _classify(confidence=0.3),
                        _dedupe(), _budget()) == "2"


def test_budget_warn_is_layer_2():
    assert decide_layer(_ocr(), _classify(),
                        _dedupe(), _budget(signal="warn")) == "2"


def test_dedupe_flag_is_layer_2():
    assert decide_layer(_ocr(), _classify(),
                        _dedupe(is_duplicate=True), _budget()) == "2"
