"""Tests for the OODA fraud investigator (PR-B).

Covers:
  - MockLLM path: deterministic, no API key required. Walks the fixed
    tool sequence, emits verdict based on signal-count heuristic.
    Output shape matches the plan-agreed schema.
  - LLM JSON parse helper: tolerant of code-fenced output.
  - Tool dispatch: sync (geo, math) vs async DB tools both work.
  - LLM-driven path with mocked client: simulates a 2-round
    investigation (1 tool call + 1 final verdict) to verify the loop
    plumbing without spending real API tokens.
  - Pipeline integration smoke: real submission flow with risk_score
    forced high produces an `investigation` field on audit_report.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from decimal import Decimal
from datetime import date
from unittest.mock import patch, AsyncMock

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_investigator_test")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.fraud_investigator import (
    _call_tool, _parse_llm_json, _summarize_tool_result,
    investigate_submission, MAX_ROUNDS_DEFAULT,
)
from backend.db.store import Base, EvalBase, create_submission, upsert_employee


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(EvalBase.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())


def teardown_module(_):
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass


# ── Helpers ──────────────────────────────────────────────────────────


async def _seed_employee(emp_id: str, *, level: str = "L3", cost_center: str = "ENG"):
    async with _Session() as db:
        await upsert_employee(db, {
            "id": emp_id,
            "name": f"Test {emp_id}",
            "department": "Engineering",
            "cost_center": cost_center,
            "level": level,
            "city": "上海",
        })


def _signal(rule: str, score: float, evidence: str = "") -> dict:
    return {"rule": rule, "score": score, "evidence": evidence}


def _make_submission(employee_id: str = "emp_inv_test") -> dict:
    return {
        "employee_id": employee_id,
        "date": date.today().isoformat(),
        "category": "meal",
        "amount": 850.0,
        "currency": "CNY",
        "merchant": "Test Restaurant",
        "city": "上海",
        "description": "客户接待",
    }


# ── _parse_llm_json ──────────────────────────────────────────────────


def test_parse_llm_json_pure_json():
    out = _parse_llm_json('{"action": "final_verdict", "verdict": "fraud"}')
    assert out["action"] == "final_verdict"
    assert out["verdict"] == "fraud"


def test_parse_llm_json_codefenced():
    """Real LLMs occasionally wrap output in ```json ... ``` despite
    instructions; the parser should still extract the blob."""
    raw = """Sure, here's my analysis:
```json
{"action": "call_tool", "tool_name": "get_employee_profile", "tool_args": {"employee_id": "E001"}}
```
"""
    out = _parse_llm_json(raw)
    assert out["action"] == "call_tool"
    assert out["tool_name"] == "get_employee_profile"


def test_parse_llm_json_garbage_returns_error_action():
    """Unparseable output must produce an action the OODA loop can
    handle without crashing."""
    out = _parse_llm_json("totally not json at all")
    assert out["action"] == "parse_error"
    assert "non-JSON" in out["thought"]


# ── _summarize_tool_result ───────────────────────────────────────────


def test_summarize_known_tool_truncated():
    """Result summaries should be short — they go back into the next
    LLM prompt context, so we keep them compact."""
    msg = _summarize_tool_result("get_employee_profile", {
        "found": True, "name": "Test", "level": "L3",
        "department": "Engineering", "cost_center": "ENG",
    })
    assert "Test" in msg
    assert "L3" in msg
    assert len(msg) < 200


def test_summarize_unknown_tool_falls_back_to_json():
    msg = _summarize_tool_result("totally_new_tool", {"x": 1, "y": "z"})
    # Must not crash on an unknown tool name; just dump compact JSON
    assert "x" in msg


# ── _call_tool (sync vs async dispatch) ──────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_sync_geo():
    """Sync tool (geo) doesn't need a DB session — verifies the
    _SYNC_TOOLS branch."""
    async with _Session() as db:
        result = await _call_tool(db, "check_geo_feasibility", {
            "date_a": "2026-04-15", "city_a": "上海",
            "date_b": "2026-04-15", "city_b": "北京",
        })
    assert result["feasible"] is False


@pytest.mark.asyncio
async def test_call_tool_async_db():
    """Async tool gets the session — verifies the DB branch."""
    await _seed_employee("emp_call_tool_test")
    async with _Session() as db:
        result = await _call_tool(db, "get_employee_profile", {
            "employee_id": "emp_call_tool_test",
        })
    assert result["found"] is True


@pytest.mark.asyncio
async def test_call_tool_unknown_raises():
    async with _Session() as db:
        with pytest.raises(ValueError, match="unknown tool"):
            await _call_tool(db, "not_a_real_tool", {})


# ── MockLLM path: full investigate_submission ────────────────────────


@pytest.mark.asyncio
async def test_mock_path_runs_tool_sequence_and_emits_verdict():
    await _seed_employee("emp_mock_full")
    async with _Session() as db:
        result = await investigate_submission(
            db,
            submission=_make_submission("emp_mock_full"),
            fraud_signals=[
                _signal("threshold_proximity", 70),
                _signal("vague_description", 60),
            ],
            risk_score=85.0,
            force_mock=True,
        )

    # Schema sanity
    assert result["used_real_llm"] is False
    assert result["verdict"] in ("clean", "suspicious", "fraud")
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["rounds_used"] >= 1
    # Tool sequence walked at least once
    assert len(result["tools_called"]) >= 1
    # Evidence chain has one entry per tool round
    assert len(result["evidence_chain"]) == result["rounds_used"]
    # Summary references the rules
    assert "threshold_proximity" in result["summary"] or "vague_description" in result["summary"]


@pytest.mark.asyncio
async def test_mock_path_three_signals_yields_fraud():
    """Heuristic: ≥3 Layer-1 signals → fraud verdict."""
    await _seed_employee("emp_mock_fraud")
    async with _Session() as db:
        result = await investigate_submission(
            db,
            submission=_make_submission("emp_mock_fraud"),
            fraud_signals=[
                _signal("threshold_proximity", 70),
                _signal("vague_description", 60),
                _signal("weekend_frequency", 60),
            ],
            risk_score=85.0,
            force_mock=True,
        )
    assert result["verdict"] == "fraud"


@pytest.mark.asyncio
async def test_mock_path_high_score_signal_yields_fraud():
    """Heuristic: any signal with score >=85 → fraud."""
    await _seed_employee("emp_mock_high")
    async with _Session() as db:
        result = await investigate_submission(
            db,
            submission=_make_submission("emp_mock_high"),
            fraud_signals=[_signal("pre_resignation_rush", 85)],
            risk_score=80.0,
            force_mock=True,
        )
    assert result["verdict"] == "fraud"


@pytest.mark.asyncio
async def test_mock_path_no_signals_just_high_ambiguity_yields_suspicious():
    await _seed_employee("emp_mock_ambig_only")
    async with _Session() as db:
        result = await investigate_submission(
            db,
            submission=_make_submission("emp_mock_ambig_only"),
            fraud_signals=[],
            risk_score=85.0,
            force_mock=True,
        )
    assert result["verdict"] == "suspicious"


@pytest.mark.asyncio
async def test_mock_path_respects_max_rounds_cap():
    """max_rounds=1 → the mock can't call any tools (it reserves the
    last round for verdict). Should still return a valid result."""
    await _seed_employee("emp_mock_cap")
    async with _Session() as db:
        result = await investigate_submission(
            db,
            submission=_make_submission("emp_mock_cap"),
            fraud_signals=[_signal("round_amount", 55)],
            risk_score=80.0,
            max_rounds=1,
            force_mock=True,
        )
    # 0 tool rounds (max_rounds-1=0), but verdict still emitted
    assert result["rounds_used"] == 0
    assert result["verdict"] in ("clean", "suspicious", "fraud")


# ── Real-LLM path with mocked OpenAI client ──────────────────────────


@pytest.mark.asyncio
async def test_real_path_with_mocked_llm_two_rounds():
    """Simulate a 2-round real-LLM investigation:
       Round 1: LLM says 'call get_employee_profile'
       Round 2: LLM says 'final_verdict: suspicious'
    Verifies the OODA loop plumbing end-to-end without spending tokens."""
    await _seed_employee("emp_real_mock")

    # Two scripted LLM responses, returned in order
    scripted = [
        '{"action": "call_tool", "thought": "check baseline", '
        '"tool_name": "get_employee_profile", '
        '"tool_args": {"employee_id": "emp_real_mock"}}',
        '{"action": "final_verdict", "thought": "L3 + 2 signals + amount fits pattern", '
        '"verdict": "suspicious", "confidence": 0.7, '
        '"summary": "金额边界 + 描述模糊，建议人工核对"}',
    ]

    class _ScriptedClient:
        def __init__(self):
            self._i = 0
            self.chat = self
            self.completions = self

        async def create(self, **_kwargs):
            class _Msg:
                def __init__(self, content):
                    self.message = type("M", (), {"content": content})()
            content = scripted[self._i]
            self._i += 1
            return type("Resp", (), {
                "choices": [_Msg(content)],
                "usage": None,
            })()

    async def _scripted_async_openai(*args, **kwargs):
        return _ScriptedClient()

    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "fake-key-for-test",
        "AGENT_USE_REAL_LLM": "1",
    }), patch("openai.AsyncOpenAI", return_value=_ScriptedClient()):
        async with _Session() as db:
            result = await investigate_submission(
                db,
                submission=_make_submission("emp_real_mock"),
                fraud_signals=[_signal("threshold_proximity", 70)],
                risk_score=82.0,
            )

    assert result["used_real_llm"] is True
    assert result["verdict"] == "suspicious"
    assert result["confidence"] == pytest.approx(0.7)
    assert result["rounds_used"] == 2
    assert "get_employee_profile" in result["tools_called"]
    assert "金额边界" in result["summary"]


@pytest.mark.asyncio
async def test_real_path_tolerates_unknown_tool():
    """If the LLM hallucinates a tool name, the loop must record-and-
    continue, not crash."""
    await _seed_employee("emp_real_unknown")

    scripted = [
        '{"action": "call_tool", "tool_name": "nonsense_tool", "tool_args": {}}',
        '{"action": "final_verdict", "verdict": "clean", "confidence": 0.4, '
        '"summary": "no real evidence"}',
    ]

    class _ScriptedClient:
        def __init__(self):
            self._i = 0
            self.chat = self
            self.completions = self

        async def create(self, **_kwargs):
            class _Msg:
                def __init__(self, content):
                    self.message = type("M", (), {"content": content})()
            content = scripted[self._i]
            self._i += 1
            return type("Resp", (), {
                "choices": [_Msg(content)],
                "usage": None,
            })()

    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "fake-key-for-test",
        "AGENT_USE_REAL_LLM": "1",
    }), patch("openai.AsyncOpenAI", return_value=_ScriptedClient()):
        async with _Session() as db:
            result = await investigate_submission(
                db,
                submission=_make_submission("emp_real_unknown"),
                fraud_signals=[_signal("round_amount", 55)],
                risk_score=80.0,
            )

    # Unknown tool was logged but didn't break the loop
    error_rounds = [e for e in result["evidence_chain"] if "error" in e]
    assert len(error_rounds) == 1
    assert "unknown tool" in error_rounds[0]["error"]
    # Verdict still emitted in the next round
    assert result["verdict"] == "clean"


@pytest.mark.asyncio
async def test_real_path_max_rounds_returns_fallback_verdict():
    """If LLM keeps calling tools past max_rounds without final_verdict,
    return a conservative 'suspicious' verdict (don't burn budget)."""
    await _seed_employee("emp_real_loop")

    # Always return "call_tool" — never emit a verdict
    looping = (
        '{"action": "call_tool", "tool_name": "get_employee_profile", '
        '"tool_args": {"employee_id": "emp_real_loop"}}'
    )

    class _LoopingClient:
        def __init__(self):
            self.chat = self
            self.completions = self

        async def create(self, **_kwargs):
            class _Msg:
                def __init__(self, content):
                    self.message = type("M", (), {"content": content})()
            return type("Resp", (), {
                "choices": [_Msg(looping)],
                "usage": None,
            })()

    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "fake-key-for-test",
        "AGENT_USE_REAL_LLM": "1",
    }), patch("openai.AsyncOpenAI", return_value=_LoopingClient()):
        async with _Session() as db:
            result = await investigate_submission(
                db,
                submission=_make_submission("emp_real_loop"),
                fraud_signals=[_signal("threshold_proximity", 70)],
                risk_score=82.0,
                max_rounds=2,
            )

    # max_rounds reached — fallback verdict
    assert result["rounds_used"] == 2
    assert result["verdict"] == "suspicious"
    assert "max rounds" in result["summary"].lower()
