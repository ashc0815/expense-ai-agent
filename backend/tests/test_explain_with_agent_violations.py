"""End-to-end smoke test: agent.* violations round-trip from the
reasoner all the way to the GET /api/chat/explain/{id} response that
the AI explanation card consumes.

This locks in the contract between PR-B (backend reasoner) and PR-C
(frontend renderer): the structured `evidence_chain` + `context`
fields must survive the JSON serialisation through audit_report and
come back out of compose_explanation unchanged.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date
from decimal import Decimal

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_explain_e2e")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from agent.compliance_reasoner import reason_about_submission
from agent.violation_registry import collect_agent_violations
from backend.db.store import (
    Base, EvalBase, create_submission, get_db,
    seed_budget_demo, seed_compliance_demo, update_submission_analysis,
)
from backend.main import app


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():
    async with _Session() as session:
        yield session


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(EvalBase.metadata.create_all)
        async with _Session() as db:
            await seed_budget_demo(db)
            await seed_compliance_demo(db)

    asyncio.new_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass


client = TestClient(app)


@pytest.mark.asyncio
async def test_agent_violation_round_trips_to_explain_endpoint():
    """Build a submission that triggers agent.travel_during_leave (E001
    transport on a leave day), run the reasoner, persist via the same
    update_submission_analysis path the pipeline uses, then GET the
    explain endpoint and assert the agent violation came back with
    evidence_chain intact."""
    async with _Session() as db:
        sub = await create_submission(db, {
            "employee_id": "E001",
            "status": "processing",
            "amount": Decimal("450"),
            "currency": "CNY",
            "category": "transport",
            "date": "2026-04-16",  # inside seeded leave window 4-15..17
            "merchant": "Didi",
            "receipt_url": "/uploads/test/x.jpg",
        })

        findings = await reason_about_submission(
            db,
            submission_id=sub.id,
            employee_id="E001",
            expense_date="2026-04-16",
            category="transport",
        )
        violations = collect_agent_violations(findings)
        assert violations, "reasoner should emit a violation for this case"

        await update_submission_analysis(
            db, sub.id,
            audit_report={"violations": violations, "timeline": []},
            risk_score=70.0,
            tier="T3",
            status="reviewed",
        )

    # Now hit the API as a manager and confirm the violation round-trips
    h = {"X-User-Id": "MGR-1", "X-User-Role": "manager"}
    r = client.post(f"/api/chat/explain/{sub.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert "violations" in body
    agent_vios = [v for v in body["violations"]
                  if (v.get("rule_id") or "").startswith("agent.")]
    assert agent_vios, "agent violation missing from explain response"
    v = next(x for x in agent_vios
             if x["rule_id"] == "agent.travel_during_leave")

    # Evidence chain survived JSON round-trip
    assert v.get("evidence_chain"), "evidence_chain dropped on round-trip"
    chain = v["evidence_chain"]
    assert chain[0]["kind"] == "approved_leave"
    assert chain[0]["leave_kind"] == "vacation"
    assert chain[0]["start_date"] == "2026-04-15"
    assert chain[0]["end_date"] == "2026-04-17"

    # Context present and severity preserved
    assert v.get("context", {}).get("category") == "transport"
    assert v.get("severity") == "error"
    # Suggestion text is not the bad "split-bills" one
    assert "拆" not in (v.get("suggestion") or "")
