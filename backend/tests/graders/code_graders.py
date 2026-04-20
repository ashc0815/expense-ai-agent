"""Code-based graders for eval cases.

Each grader takes (actual_output, expected_spec) and returns (passed, message).
All graders are deterministic — no LLM calls.
"""
from __future__ import annotations

from typing import Any


def grade_score_range(actual: float, expected: list[float]) -> tuple[bool, str]:
    """Check that actual score is within [lo, hi] range."""
    lo, hi = expected[0], expected[1]
    passed = lo <= actual <= hi
    msg = f"score={actual:.1f} {'∈' if passed else '∉'} [{lo}, {hi}]"
    return passed, msg


def grade_field_match(actual: Any, expected: Any) -> tuple[bool, str]:
    """Exact match on a field value."""
    passed = actual == expected
    msg = f"actual={actual!r} {'==' if passed else '!='} expected={expected!r}"
    return passed, msg


def grade_enum_in(actual: Any, allowed: list) -> tuple[bool, str]:
    """Check that actual value is in the allowed set."""
    passed = actual in allowed
    msg = f"actual={actual!r} {'∈' if passed else '∉'} {allowed}"
    return passed, msg


def grade_list_contains(actual: list, required: list) -> tuple[bool, str]:
    """Check that all required items appear in the actual list."""
    missing = [r for r in required if r not in actual]
    passed = len(missing) == 0
    msg = f"missing={missing}" if missing else "all required items present"
    return passed, msg


def grade_bool(actual: bool, expected: bool) -> tuple[bool, str]:
    """Check boolean match."""
    passed = actual == expected
    msg = f"actual={actual} {'==' if passed else '!='} expected={expected}"
    return passed, msg


def classify_detection(expected_signal: bool, actual_signal: bool) -> str:
    """Classify a detection result into TP/FP/FN/TN.

    Returns a business-friendly Chinese label:
      正确标记 (TP) — expected=True,  actual=True
      漏报     (FN) — expected=True,  actual=False
      误报     (FP) — expected=False, actual=True
      正确放行 (TN) — expected=False, actual=False
    """
    if expected_signal and actual_signal:
        return "正确标记"
    elif expected_signal and not actual_signal:
        return "漏报"
    elif not expected_signal and actual_signal:
        return "误报"
    else:
        return "正确放行"


def grade_case(actual_output: dict, expect: dict) -> list[tuple[str, bool, str]]:
    """Run all applicable graders for a single eval case.

    Returns list of (check_name, passed, message) tuples.
    """
    results: list[tuple[str, bool, str]] = []

    for key, expected_val in expect.items():
        if key.endswith("_range"):
            # e.g. template_score_range → check actual_output["template_score"]
            field_name = key[:-6]  # strip "_range"
            actual_val = actual_output.get(field_name)
            if actual_val is None:
                results.append((key, False, f"field '{field_name}' not found in output"))
            else:
                passed, msg = grade_score_range(float(actual_val), expected_val)
                results.append((key, passed, msg))

        elif key == "has_signal":
            actual_val = actual_output.get("has_signal", False)
            passed, msg = grade_bool(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key == "rule_name":
            actual_val = actual_output.get("rule_name")
            passed, msg = grade_field_match(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key == "layer":
            actual_val = actual_output.get("layer")
            passed, msg = grade_field_match(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key == "recommendation":
            actual_val = actual_output.get("recommendation")
            passed, msg = grade_field_match(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key == "recommendation_in":
            actual_val = actual_output.get("recommendation")
            passed, msg = grade_enum_in(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key == "triggered_contains":
            actual_val = actual_output.get("triggered_factors", [])
            passed, msg = grade_list_contains(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key in ("contradiction_found", "person_amount_reasonable"):
            actual_val = actual_output.get(key)
            passed, msg = grade_field_match(actual_val, expected_val)
            results.append((key, passed, msg))

        elif key.endswith("_range") is False and key not in (
            "http_status", "tool_calls_include", "tool_calls_exclude",
            "response_contains", "response_quality",
            "whitelist_error_contains", "blocked_tool",
            "green_flags_min", "red_flags_min", "advisory_contains",
        ):
            # Generic field match for any other expected field
            actual_val = actual_output.get(key)
            if actual_val is not None:
                passed, msg = grade_field_match(actual_val, expected_val)
                results.append((key, passed, msg))

    return results
