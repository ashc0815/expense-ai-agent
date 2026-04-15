"""Pure function that classifies a Draft's completeness into a Layer.

Layers:
  "1"       — happy path, all critical fields high-conf, budget ok
  "2"       — 1-2 fields need user fix / classify mid-conf / budget warn
  "3_soft"  — >= 3 fields need user fix; card stays but shows 手填/重拍
  "3_hard"  — OCR all empty or not-a-receipt; frontend auto-redirects

Thresholds (v1, hard-coded; future: telemetry-driven):
  OCR amount/merchant confidence >= 0.8  → high
  classify confidence >= 0.8              → high
  classify confidence 0.5 - 0.8           → mid (Layer 2 inline chip)
  classify confidence < 0.5               → counts as "needs fix"
  Layer 2 capacity                        → max 2 missing/fix fields
"""
from __future__ import annotations

OCR_CONF_HIGH = 0.8
CLASSIFY_CONF_HIGH = 0.8
CLASSIFY_CONF_MID = 0.5
LAYER_2_MAX_FIELDS = 2


def _is_empty(v):
    return v in (None, "", 0, 0.0)


def decide_layer(
    ocr: dict,
    classify: dict,
    dedupe: dict,
    budget: dict,
    missing_optional_fields: list[str] | None = None,
) -> str:
    # ── Hard errors ─────────────────────────────────────────────
    if ocr.get("not_a_receipt"):
        return "3_hard"

    critical = [ocr.get("amount"), ocr.get("merchant"), ocr.get("date")]
    if all(_is_empty(v) for v in critical):
        return "3_hard"

    # ── Count fields that need user fix ─────────────────────────
    fix_count = 0
    if _is_empty(ocr.get("amount")):
        fix_count += 1
    if _is_empty(ocr.get("merchant")):
        fix_count += 1
    if _is_empty(ocr.get("date")):
        fix_count += 1

    classify_conf = classify.get("confidence") or 0
    if classify_conf < CLASSIFY_CONF_MID:
        fix_count += 1

    fix_count += len(missing_optional_fields or [])

    # ── Soft error: too many fields ─────────────────────────────
    if fix_count >= 3:
        return "3_soft"

    # ── Layer 2 conditions ──────────────────────────────────────
    if fix_count >= 1:
        return "2"
    if CLASSIFY_CONF_MID <= classify_conf < CLASSIFY_CONF_HIGH:
        return "2"
    if budget.get("signal") == "warn":
        return "2"
    if dedupe.get("is_duplicate"):
        return "2"

    # ── Happy path ──────────────────────────────────────────────
    return "1"
