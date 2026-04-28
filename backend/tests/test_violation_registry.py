"""Tests for the rule-violation registry — locks in the contract that
every triggered ambiguity factor / failed policy rule maps to a stable
rule_id with human-readable text + severity.

Why this matters: the AI explanation card on the approval page reads
audit_report.violations to render "this expense violated [rule_id] —
explanation" cards. If a factor stops mapping (or maps to garbage), the
manager UI silently goes blank. These tests catch that at PR time."""
from __future__ import annotations

from dataclasses import dataclass

from agent.violation_registry import (
    AMBIGUITY_VIOLATIONS, POLICY_VIOLATIONS,
    collect_ambiguity_violations, collect_policy_violations,
    violation_from_factor, violation_from_rule_result,
)


# ── Ambiguity factor → violation ─────────────────────────────────────

def test_every_ambiguity_factor_has_a_template():
    """Every factor used by ambiguity_detector must have a violation entry.
    If you add a new factor in agent/ambiguity_detector.py, you MUST add
    a matching entry in violation_registry.AMBIGUITY_VIOLATIONS."""
    expected_factors = {
        "description_vague", "amount_boundary", "pattern_anomaly",
        "time_anomaly", "city_mismatch",
    }
    assert expected_factors.issubset(AMBIGUITY_VIOLATIONS.keys()), (
        f"Missing factor template(s): "
        f"{expected_factors - AMBIGUITY_VIOLATIONS.keys()}"
    )


def test_each_ambiguity_violation_has_required_fields():
    for factor, v in AMBIGUITY_VIOLATIONS.items():
        assert v.get("rule_id"), f"{factor} missing rule_id"
        assert v["rule_id"].startswith("ambiguity."), (
            f"{factor} rule_id should be namespaced as 'ambiguity.*'"
        )
        assert v.get("rule_text"), f"{factor} missing rule_text"
        assert v.get("severity") in {"info", "warn", "error"}, (
            f"{factor} severity must be info/warn/error"
        )


def test_violation_from_factor_returns_independent_copy():
    """Mutating the returned dict must not poison the registry."""
    v = violation_from_factor("description_vague")
    assert v is not None
    v["rule_text"] = "MUTATED"
    again = violation_from_factor("description_vague")
    assert again["rule_text"] != "MUTATED"


def test_violation_from_factor_unknown_returns_none():
    assert violation_from_factor("nonexistent_factor") is None


def test_collect_ambiguity_violations_skips_unknown_factors():
    out = collect_ambiguity_violations([
        "description_vague", "garbage_factor", "amount_boundary",
    ])
    assert len(out) == 2
    assert {v["rule_id"] for v in out} == {
        "ambiguity.description_vague", "ambiguity.amount_boundary",
    }


# ── Policy rule_result → violation ───────────────────────────────────

@dataclass
class _FakeRuleResult:
    rule_name: str
    passed: bool
    message: str = ""
    severity: str = "error"


def test_policy_passed_results_produce_no_violations():
    rr = _FakeRuleResult("amount_positive", passed=True)
    assert violation_from_rule_result(rr) is None


def test_known_policy_rule_uses_template():
    rr = _FakeRuleResult("amount_positive", passed=False, message="amount=-50")
    v = violation_from_rule_result(rr)
    assert v is not None
    assert v["rule_id"] == "policy.amount_positive"
    assert "金额" in v["rule_text"]
    # Engine message is preserved as evidence
    assert v.get("evidence") == "amount=-50"


def test_unknown_policy_rule_falls_back_to_message():
    """If we add a new policy rule but forget to add a template, the engine
    message is still surfaced (with a generic rule_id) — fail-soft, not blank."""
    rr = _FakeRuleResult(
        "weird_new_rule", passed=False,
        message="This is the engine's natural-language explanation.",
        severity="warn",
    )
    v = violation_from_rule_result(rr)
    assert v is not None
    assert v["rule_id"] == "policy.weird_new_rule"
    assert v["rule_text"] == "This is the engine's natural-language explanation."
    assert v["severity"] == "warn"


def test_collect_policy_violations_only_includes_failures():
    rs = [
        _FakeRuleResult("amount_positive", passed=True),
        _FakeRuleResult("date_not_future", passed=False, message="date is 2030"),
        _FakeRuleResult("city_recognized", passed=True),
        _FakeRuleResult("invoice_format", passed=False, message="missing prefix"),
    ]
    out = collect_policy_violations(rs)
    assert len(out) == 2
    assert {v["rule_id"] for v in out} == {
        "policy.date_not_future", "policy.invoice_format",
    }


def test_collect_policy_violations_handles_none():
    """Defensive — should not crash on None or empty list."""
    assert collect_policy_violations(None) == []
    assert collect_policy_violations([]) == []
