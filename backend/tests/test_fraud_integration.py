"""Integration test — full fraud pipeline with rules 1-14."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.fraud_rules import SubmissionRow
from skills.skill_fraud_check import process_report_async


MOCK_LLM_ANALYSIS = {
    "template_score": 85,
    "template_evidence": "3/3 identical pattern",
    "contradiction_found": True,
    "contradiction_evidence": "mall vs office",
    "extracted_person_count": 2,
    "per_person_amount": 490.0,
    "person_amount_reasonable": False,
    "person_amount_evidence": "490 per person for lunch is high",
    "vagueness_score": 75,
    "vagueness_evidence": "very generic",
}

CLEAN_LLM_ANALYSIS = {
    "template_score": 10,
    "template_evidence": "",
    "contradiction_found": False,
    "contradiction_evidence": "",
    "extracted_person_count": 3,
    "per_person_amount": 100.0,
    "person_amount_reasonable": True,
    "person_amount_evidence": "",
    "vagueness_score": 15,
    "vagueness_evidence": "",
}


@pytest.mark.asyncio
async def test_level2_rules_fire_with_llm_analysis():
    """When LLM returns high-risk analysis, rules 11-14 should all fire."""
    with patch(
        "skills.skill_fraud_check.analyze_submission",
        new=AsyncMock(return_value=MOCK_LLM_ANALYSIS),
    ), patch(
        "skills.skill_fraud_check.list_recent_descriptions",
        new=AsyncMock(return_value=["与客户会面"] * 5),
    ):
        result = await process_report_async(
            submissions=[SubmissionRow(
                id="s1", employee_id="emp-1", amount=980.0, currency="CNY",
                category="gift", date="2026-04-10", merchant="购物中心",
                description="项目相关支出",
            )],
            employee_id="emp-1",
            db=None,
        )
    rules_hit = {s["rule"] for s in result["fraud_signals"]}
    assert "description_template" in rules_hit
    assert "receipt_contradiction" in rules_hit
    assert "person_amount_mismatch" in rules_hit
    assert "vague_description" in rules_hit


@pytest.mark.asyncio
async def test_level2_rules_silent_on_clean_submission():
    """When LLM returns low-risk analysis, rules 11-14 should not fire."""
    with patch(
        "skills.skill_fraud_check.analyze_submission",
        new=AsyncMock(return_value=CLEAN_LLM_ANALYSIS),
    ), patch(
        "skills.skill_fraud_check.list_recent_descriptions",
        new=AsyncMock(return_value=[]),
    ):
        result = await process_report_async(
            submissions=[SubmissionRow(
                id="s1", employee_id="emp-1", amount=300.0, currency="CNY",
                category="meal", date="2026-04-10", merchant="海底捞",
                description="与团队午餐讨论Q2季度计划",
            )],
            employee_id="emp-1",
            db=None,
        )
    level2_rules = {"description_template", "receipt_contradiction",
                    "person_amount_mismatch", "vague_description"}
    rules_hit = {s["rule"] for s in result["fraud_signals"]}
    assert not (rules_hit & level2_rules), f"Clean submission should not trigger Level 2 rules, got {rules_hit & level2_rules}"
