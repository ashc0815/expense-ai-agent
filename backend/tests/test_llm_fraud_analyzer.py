"""LLM fraud analyzer tests — mock LLM, verify parsing and fallback."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from backend.services.fraud_rules import SubmissionRow


def _sub(description="与客户张总会面讨论合作事宜", merchant="海底捞",
         amount=480.0, category="meal", city="上海") -> SubmissionRow:
    return SubmissionRow(
        id="s1", employee_id="emp-1", amount=amount, currency="CNY",
        category=category, date="2026-04-10", merchant=merchant,
        description=description, city=city,
    )


MOCK_LLM_RESPONSE = {
    "template_score": 85,
    "template_evidence": "3/3 descriptions follow near-identical pattern",
    "contradiction_found": False,
    "contradiction_evidence": "",
    "extracted_person_count": 2,
    "per_person_amount": 240.0,
    "person_amount_reasonable": True,
    "person_amount_evidence": "240 per person for meal is reasonable",
    "vagueness_score": 30,
    "vagueness_evidence": "Mentions specific person and topic",
}


@pytest.mark.asyncio
async def test_analyze_returns_structured_result():
    from backend.services.llm_fraud_analyzer import analyze_submission

    mock_response = json.dumps(MOCK_LLM_RESPONSE)
    with patch(
        "backend.services.llm_fraud_analyzer._call_llm",
        new=AsyncMock(return_value=mock_response),
    ), patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        result = await analyze_submission(
            submission=_sub(),
            recent_descriptions=["与客户张总会面讨论合作事宜", "与客户张总讨论项目进度"],
            receipt_location="上海市浦东新区",
        )
    assert result["template_score"] == 85
    assert result["extracted_person_count"] == 2
    assert result["vagueness_score"] == 30
    assert result["contradiction_found"] is False


@pytest.mark.asyncio
async def test_fallback_on_llm_failure():
    from backend.services.llm_fraud_analyzer import analyze_submission

    with patch(
        "backend.services.llm_fraud_analyzer._call_llm",
        new=AsyncMock(side_effect=Exception("API down")),
    ), patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        result = await analyze_submission(
            submission=_sub(),
            recent_descriptions=[],
            receipt_location=None,
        )
    # Fallback returns neutral scores (no false positives)
    assert result["template_score"] == 0
    assert result["vagueness_score"] == 0
    assert result["contradiction_found"] is False
    assert result["extracted_person_count"] is None


@pytest.mark.asyncio
async def test_malformed_llm_json_falls_back():
    from backend.services.llm_fraud_analyzer import analyze_submission

    with patch(
        "backend.services.llm_fraud_analyzer._call_llm",
        new=AsyncMock(return_value="this is not json"),
    ), patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        result = await analyze_submission(
            submission=_sub(),
            recent_descriptions=[],
            receipt_location=None,
        )
    assert result["template_score"] == 0


@pytest.mark.asyncio
async def test_skips_llm_when_no_description():
    from backend.services.llm_fraud_analyzer import analyze_submission

    with patch(
        "backend.services.llm_fraud_analyzer._call_llm",
        new=AsyncMock(),
    ) as mock_call:
        result = await analyze_submission(
            submission=_sub(description=None),
            recent_descriptions=[],
            receipt_location=None,
        )
    mock_call.assert_not_called()
    assert result["template_score"] == 0
