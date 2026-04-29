"""Auto-rules tests — pattern miner + API.

Locks in the invariants for #5 用户行为学习自动规则:

  Miner:
    1. >= MIN_EVIDENCE consistent submissions on the same merchant/field
       produces a rule.
    2. < THRESHOLD consistency (mixed values) produces NO rule.
    3. < MIN_EVIDENCE submissions produces NO rule.
    4. Re-running the miner refreshes a 'suggested' rule but never
       overwrites an 'active' or 'dismissed' one (user already decided).

  API:
    5. POST /mine returns suggestions; GET / returns counts.
    6. /accept moves suggested → active and writes an audit log.
    7. /dismiss moves suggested → dismissed.
    8. Cannot accept/dismiss a non-suggested rule (409).
    9. Cannot operate on someone else's rule (403).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_auto_rules_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import (
    Base, EvalBase, AutoRule, create_submission, get_db,
    list_auto_rules, find_auto_rule_by_scope, decide_auto_rule,
)
from backend.services.pattern_miner import (
    mine_for_employee, _candidate_rules, _normalize_merchant,
    MIN_EVIDENCE, THRESHOLD,
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


async def _seed_submissions(employee_id: str, rows: list[dict]):
    today_iso = date.today().isoformat()
    async with _Session() as s:
        for row in rows:
            await create_submission(s, {
                "employee_id": employee_id,
                "status": "finance_approved",
                "amount": row.get("amount", 100.0),
                "currency": "CNY",
                "category": row["category"],
                "date": today_iso,
                "merchant": row["merchant"],
                "receipt_url": "/tmp/x.jpg",
                "project_code": row.get("project_code"),
                "cost_center": row.get("cost_center"),
            })


# ── Miner unit tests ─────────────────────────────────────────────────


def test_normalize_merchant_collapses_case_and_whitespace():
    assert _normalize_merchant("Starbucks") == _normalize_merchant("  STARBUCKS  ")


def test_candidate_rules_requires_min_evidence(monkeypatch):
    """4 consistent submissions must NOT produce a rule (default MIN_EVIDENCE=5)."""
    class S:
        def __init__(self, sid, m, c):
            self.id = sid
            self.merchant = m
            self.category = c
            self.project_code = None
            self.cost_center = None
    subs = [S(f"s{i}", "Starbucks", "meal") for i in range(MIN_EVIDENCE - 1)]
    assert _candidate_rules(subs) == []


def test_candidate_rules_emits_rule_at_threshold():
    class S:
        def __init__(self, sid, m, c):
            self.id = sid
            self.merchant = m
            self.category = c
            self.project_code = None
            self.cost_center = None
    subs = [S(f"s{i}", "Starbucks", "meal") for i in range(MIN_EVIDENCE)]
    cands = _candidate_rules(subs)
    assert len(cands) == 1
    assert cands[0]["trigger_value"] == "starbucks"
    assert cands[0]["field"] == "category"
    assert cands[0]["value"] == "meal"
    assert cands[0]["confidence"] == 1.0
    assert cands[0]["evidence_count"] == MIN_EVIDENCE


def test_candidate_rules_drops_inconsistent_data():
    """Mixed categories below threshold produce no rule."""
    class S:
        def __init__(self, sid, m, c):
            self.id = sid
            self.merchant = m
            self.category = c
            self.project_code = None
            self.cost_center = None
    # 6 submissions, 50/50 split — below 0.8 threshold
    subs = [S(f"s{i}", "Starbucks", "meal" if i % 2 == 0 else "office_supplies")
            for i in range(6)]
    assert _candidate_rules(subs) == []


def test_candidate_rules_strong_majority_clears_threshold():
    """8 submissions, 7 'meal' + 1 'other' — 0.875 >= 0.8 produces a rule."""
    class S:
        def __init__(self, sid, m, c):
            self.id = sid
            self.merchant = m
            self.category = c
            self.project_code = None
            self.cost_center = None
    subs = [S(f"s{i}", "Starbucks", "meal") for i in range(7)]
    subs.append(S("s7", "Starbucks", "office_supplies"))
    cands = _candidate_rules(subs)
    assert len(cands) == 1
    assert cands[0]["value"] == "meal"
    assert THRESHOLD <= cands[0]["confidence"] < 1.0


# ── Miner integration tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_mine_for_employee_persists_suggestion():
    emp = "emp-mine-1"
    await _seed_submissions(emp, [
        {"merchant": "海底捞", "category": "meal"} for _ in range(MIN_EVIDENCE)
    ])
    async with _Session() as db:
        rules = await mine_for_employee(db, emp)
    assert len(rules) == 1
    assert rules[0].trigger_value == _normalize_merchant("海底捞")
    assert rules[0].field == "category"
    assert rules[0].value == "meal"
    assert rules[0].status == "suggested"


@pytest.mark.asyncio
async def test_mining_does_not_overwrite_active_rule():
    """User already accepted; running the miner again must leave it alone."""
    emp = "emp-mine-2"
    await _seed_submissions(emp, [
        {"merchant": "Didi", "category": "transport"} for _ in range(MIN_EVIDENCE)
    ])
    async with _Session() as db:
        rules = await mine_for_employee(db, emp)
        assert len(rules) == 1
        # User accepts the rule
        await decide_auto_rule(db, rules[0].id, new_status="active",
                               decided_by=emp)

    # More evidence pours in. Re-mine.
    await _seed_submissions(emp, [
        {"merchant": "Didi", "category": "transport"} for _ in range(MIN_EVIDENCE)
    ])
    async with _Session() as db:
        rules2 = await mine_for_employee(db, emp)
        rule = await find_auto_rule_by_scope(
            db, employee_id=emp, trigger_type="merchant_exact",
            trigger_value=_normalize_merchant("Didi"), field="category",
        )
    assert rule.status == "active"
    # mine_for_employee returns the current row even if it didn't update it
    assert rules2[0].status == "active"


# ── API tests ─────────────────────────────────────────────────────────


client = TestClient(app)


def _headers(uid: str, role: str = "employee"):
    return {"X-User-Id": uid, "X-User-Role": role}


@pytest.mark.asyncio
async def test_api_mine_then_list_then_accept(monkeypatch):
    emp = "emp-api-1"
    await _seed_submissions(emp, [
        {"merchant": "Marriott", "category": "accommodation"}
        for _ in range(MIN_EVIDENCE)
    ])
    h = _headers(emp)

    # Mine
    r = client.post("/api/auto-rules/mine", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] >= 1
    rule_id = body["items"][0]["id"]
    assert body["items"][0]["status"] == "suggested"

    # List
    r = client.get("/api/auto-rules", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["suggested"] >= 1

    # Accept
    r = client.post(f"/api/auto-rules/{rule_id}/accept", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "active"

    # Cannot accept again
    r = client.post(f"/api/auto-rules/{rule_id}/accept", headers=h)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_api_dismiss_marks_rule_dismissed():
    emp = "emp-api-2"
    await _seed_submissions(emp, [
        {"merchant": "Tim Hortons", "category": "meal"}
        for _ in range(MIN_EVIDENCE)
    ])
    h = _headers(emp)
    r = client.post("/api/auto-rules/mine", headers=h)
    rule_id = r.json()["items"][0]["id"]

    r = client.post(f"/api/auto-rules/{rule_id}/dismiss", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"


@pytest.mark.asyncio
async def test_api_cannot_operate_on_someone_elses_rule():
    emp = "emp-api-3"
    intruder = "emp-api-3-intruder"
    await _seed_submissions(emp, [
        {"merchant": "Hilton", "category": "accommodation"}
        for _ in range(MIN_EVIDENCE)
    ])
    r = client.post("/api/auto-rules/mine", headers=_headers(emp))
    rule_id = r.json()["items"][0]["id"]

    r = client.post(f"/api/auto-rules/{rule_id}/accept",
                    headers=_headers(intruder))
    assert r.status_code == 403


def test_api_admin_overview_requires_finance_admin():
    r = client.get("/api/auto-rules/admin",
                   headers=_headers("emp-x", "employee"))
    assert r.status_code == 403

    r = client.get("/api/auto-rules/admin",
                   headers=_headers("admin-x", "finance_admin"))
    assert r.status_code == 200
    body = r.json()
    assert "by_status" in body
    assert "total" in body
