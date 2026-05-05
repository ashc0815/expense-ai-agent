"""End-to-end smoke test: investigation block round-trips from the
fraud_investigator (PR-B) all the way to GET /api/chat/explain/{id},
which the AI explanation card (PR-C) consumes to render the OODA
agent's verdict + evidence_chain + summary.

Locks in the contract:
  - audit_report.investigation is preserved through JSON storage
  - compose_explanation passes it through verbatim under
    response["investigation"]
  - All schema fields the frontend needs (verdict, confidence,
    rounds_used, tools_called, evidence_chain[], summary,
    used_real_llm) are present in the response
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
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_inv_e2e")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from agent.fraud_investigator import investigate_submission
from backend.db.store import (
    Base, EvalBase, create_submission, get_db, update_submission_analysis,
    upsert_employee,
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
async def test_investigation_round_trips_to_explain_endpoint():
    """Build a real submission, run the OODA agent (mock path) on it,
    persist its output via update_submission_analysis (the same path
    _run_pipeline uses), then GET /api/chat/explain/{id} as a manager
    and assert the investigation block is present + structurally
    correct."""
    employee_id = "emp_inv_e2e"
    async with _Session() as db:
        await upsert_employee(db, {
            "id": employee_id, "name": "E2E Test",
            "department": "Engineering", "cost_center": "ENG",
            "level": "L3", "city": "上海",
        })
        sub = await create_submission(db, {
            "employee_id": employee_id,
            "status": "processing",
            "amount": Decimal("850"),
            "currency": "CNY",
            "category": "meal",
            "date": date.today().isoformat(),
            "merchant": "Test Restaurant",
            "receipt_url": "/uploads/test/x.jpg",
            "description": "客户接待",
        })

        investigation = await investigate_submission(
            db,
            submission={
                "employee_id": employee_id,
                "date": date.today().isoformat(),
                "category": "meal",
                "amount": 850.0,
                "currency": "CNY",
                "merchant": "Test Restaurant",
                "city": "上海",
                "description": "客户接待",
            },
            fraud_signals=[
                {"rule": "threshold_proximity", "score": 70, "evidence": "amount near limit"},
                {"rule": "vague_description", "score": 60, "evidence": "4-char description"},
            ],
            risk_score=85.0,
            force_mock=True,
        )

        await update_submission_analysis(
            db, sub.id,
            audit_report={
                "violations": [],
                "timeline": [],
                "investigation": investigation,
            },
            risk_score=85.0,
            tier="T3",
            status="reviewed",
        )

    # Hit the API as a manager and assert the investigation block survives
    h = {"X-User-Id": "MGR-1", "X-User-Role": "manager"}
    r = client.post(f"/api/chat/explain/{sub.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert "investigation" in body
    inv = body["investigation"]
    assert inv is not None, "investigation should be present (was triggered)"

    # Schema sanity — every field the AI explanation card needs
    assert inv["verdict"] in ("clean", "suspicious", "fraud")
    assert isinstance(inv["confidence"], (int, float))
    assert 0.0 <= inv["confidence"] <= 1.0
    assert inv["rounds_used"] >= 0
    assert isinstance(inv["tools_called"], list)
    assert isinstance(inv["evidence_chain"], list)
    assert isinstance(inv["summary"], str)
    assert isinstance(inv["used_real_llm"], bool)

    # Evidence-chain sanity: each step has a round number; mock walks
    # the fixed sequence so we expect the first tool to be employee_profile
    if inv["evidence_chain"]:
        first = inv["evidence_chain"][0]
        assert "round" in first
        # Mock starts with profile lookup
        if "tool" in first:
            assert first["tool"] == "get_employee_profile"


@pytest.mark.asyncio
async def test_no_investigation_when_audit_report_missing_field():
    """Submissions that didn't trigger the OODA agent (combined_risk
    < 80) must NOT include an investigation block — the response has
    investigation=null. The frontend conditionally renders, so this
    is the contract that keeps the card clean for low-risk cases."""
    employee_id = "emp_inv_no_trigger"
    async with _Session() as db:
        await upsert_employee(db, {
            "id": employee_id, "name": "Low Risk",
            "department": "Engineering", "cost_center": "ENG",
            "level": "L3", "city": "上海",
        })
        sub = await create_submission(db, {
            "employee_id": employee_id,
            "status": "processing",
            "amount": Decimal("50"),
            "currency": "CNY",
            "category": "meal",
            "date": date.today().isoformat(),
            "merchant": "Snack Bar",
            "receipt_url": "/uploads/test/y.jpg",
            "description": "团队下午茶",
        })
        await update_submission_analysis(
            db, sub.id,
            # No "investigation" key — this is what _run_pipeline emits
            # when combined_risk < 80
            audit_report={"violations": [], "timeline": []},
            risk_score=20.0,
            tier="T1",
            status="reviewed",
        )

    h = {"X-User-Id": "MGR-1", "X-User-Role": "manager"}
    r = client.post(f"/api/chat/explain/{sub.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Field present in response shape but explicitly null
    assert "investigation" in body
    assert body["investigation"] is None
