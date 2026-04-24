"""Tests for budget endpoints."""
from __future__ import annotations
import asyncio, os, tempfile, uuid

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"
os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/cs_test_budget")

from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient
from backend.db.store import Base, get_db, upsert_cost_center_budget, upsert_budget_policy
from backend.main import app

_test_engine = create_async_engine(_DB_URL)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)

async def _override_get_db():
    async with _TestSession() as session:
        yield session

def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL
    async def _create():
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.new_event_loop().run_until_complete(_create())
    app.dependency_overrides[get_db] = _override_get_db

def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    os.unlink(_TMP_DB.name)

client = TestClient(app)
HEADERS = {"X-User-Id": "emp-test", "X-User-Role": "employee"}
ADMIN_HEADERS = {"X-User-Id": "finance-1", "X-User-Role": "finance_admin"}


async def _seed_budget(cost_center: str, period: str, total: float):
    async with _TestSession() as db:
        await upsert_cost_center_budget(db, cost_center, period, Decimal(str(total)), "test")
        await upsert_budget_policy(db, None, 0.75, 0.95, "warn_only", "test")


def test_budget_status_no_budget_returns_ok():
    """Cost center with no budget row → signal: ok, configured: false."""
    r = client.get("/api/budget/status/UNKNOWN-CC", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["signal"] == "ok"
    assert body["configured"] is False


def test_budget_status_under_info_threshold_returns_ok():
    asyncio.new_event_loop().run_until_complete(
        _seed_budget("CC-LOW", "2026-Q2", 10000.0)
    )
    r = client.get("/api/budget/status/CC-LOW?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["signal"] == "ok"


def test_budget_status_projected_blocked():
    """Passing amount that would push usage to ≥ 95% → signal: blocked."""
    asyncio.new_event_loop().run_until_complete(
        _seed_budget("CC-HIGH", "2026-Q2", 10000.0)
    )
    # 0 spent + 9501 amount = 95.01% of 10000 → blocked
    r = client.get(
        "/api/budget/status/CC-HIGH?period=2026-Q2&amount=9501",
        headers=HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["signal"] == "blocked"
    assert "projected_pct" in body
    assert body["projected_pct"] >= 0.95


def test_post_amounts_returns_201():
    """POST /amounts should return 201 Created."""
    r = client.post(
        "/api/budget/amounts",
        json={"cost_center": "CC-NEW", "period": "2026-Q2", "total_amount": 5000.0},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["cost_center"] == "CC-NEW"
    assert body["total_amount"] == 5000.0


def test_put_policy_requires_finance_admin():
    r = client.put(
        "/api/budget/policies/_default",
        headers=HEADERS,   # employee role
        json={"info_threshold": 0.75, "block_threshold": 0.95, "over_budget_action": "warn_only"},
    )
    assert r.status_code == 403


def test_put_policy_invalid_thresholds():
    r = client.put(
        "/api/budget/policies/_default",
        headers=ADMIN_HEADERS,
        json={"info_threshold": 0.95, "block_threshold": 0.80, "over_budget_action": "warn_only"},
    )
    assert r.status_code == 400


def test_put_and_get_policy_roundtrip():
    r = client.put(
        "/api/budget/policies/_default",
        headers=ADMIN_HEADERS,
        json={"info_threshold": 0.70, "block_threshold": 0.90, "over_budget_action": "block"},
    )
    assert r.status_code == 200
    assert r.json()["block_threshold"] == 0.90

    r2 = client.get("/api/budget/policies/_default", headers=ADMIN_HEADERS)
    assert r2.status_code == 200
    assert r2.json()["over_budget_action"] == "block"


def test_upsert_and_list_budget_amounts():
    r = client.post(
        "/api/budget/amounts",
        headers=ADMIN_HEADERS,
        json={"cost_center": "ENG-TEST", "period": "2026-Q2", "total_amount": 5000.0},
    )
    assert r.status_code == 201
    assert r.json()["total_amount"] == 5000.0

    r2 = client.get("/api/budget/amounts?period=2026-Q2", headers=ADMIN_HEADERS)
    assert r2.status_code == 200
    items = r2.json()
    assert any(i["cost_center"] == "ENG-TEST" for i in items)


def test_upsert_budget_amount_negative_rejected():
    r = client.post(
        "/api/budget/amounts",
        headers=ADMIN_HEADERS,
        json={"cost_center": "ENG-TEST", "period": "2026-Q2", "total_amount": -100.0},
    )
    assert r.status_code == 400


import io

def _fake_png():
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )


async def _seed_employee_with_cc(employee_id: str, cost_center: str):
    from backend.db.store import Employee
    async with _TestSession() as db:
        existing = await db.execute(
            __import__('sqlalchemy', fromlist=['select']).select(Employee).where(Employee.id == employee_id)
        )
        if existing.scalar_one_or_none() is None:
            emp = Employee(
                id=employee_id, name="Test User",
                department="Engineering", cost_center=cost_center,
            )
            db.add(emp)
            await db.commit()


def test_submit_with_budget_blocked_sets_flag():
    """When cost-center budget is at 95%+ and submission would exceed it, budget_blocked = True."""
    from decimal import Decimal
    cc = "CC-BLOCK-TEST"
    emp_id = f"emp-blk-{uuid.uuid4().hex[:6]}"
    asyncio.new_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))

    # Seed existing spend of 9500 (95%) so any new amount triggers block
    async def _seed_spend():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission).where(
                    Submission.id == f"seed-blk-{emp_id}"
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id=f"seed-blk-{emp_id}", employee_id=emp_id, status="reviewed",
                    amount=Decimal("9500"), currency="CNY", category="meal",
                    date="2026-04-10", merchant="Test Merchant",
                    receipt_url="http://example.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()
    asyncio.new_event_loop().run_until_complete(_seed_spend())

    r = client.post(
        "/api/submissions",
        headers={"X-User-Id": emp_id, "X-User-Role": "employee"},
        data={
            "amount": "200", "currency": "CNY", "category": "meal",
            "date": "2026-04-14", "merchant": "Blocked Merchant",
        },
        files={"receipt_image": ("r.png", io.BytesIO(_fake_png()), "image/png")},
    )
    assert r.status_code == 202
    body = r.json()
    assert body.get("budget_blocked") is True


def test_unblock_submission():
    """Finance admin can unblock a budget-blocked submission."""
    from decimal import Decimal
    async def _create_blocked():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission).where(
                    Submission.id == "blocked-sub-t4"
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="blocked-sub-t4", employee_id="emp-fin-test", status="manager_approved",
                    amount=Decimal("500"), currency="CNY", category="meal",
                    date="2026-04-14", merchant="Blocked Hotel",
                    receipt_url="http://example.com/r.png",
                    cost_center="CC-BLOCK-TEST",
                    budget_blocked=True,
                ))
                await db.commit()
    asyncio.new_event_loop().run_until_complete(_create_blocked())

    r = client.patch("/api/submissions/blocked-sub-t4/unblock", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["budget_blocked"] is False


def test_unblock_requires_finance_admin():
    r = client.patch("/api/submissions/blocked-sub-t4/unblock", headers=HEADERS)
    assert r.status_code == 403


# ── Trend field tests ──────────────────────────────────────────────────────────

def test_budget_status_trend_high_risk():
    """When past-month avg is high relative to remaining budget → overrun_risk=high, monthly_avg correct."""
    import calendar as _cal
    from datetime import date as _date

    cc = "CC-TREND"
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))

    # Seed Q2 spend (April 2026): 87% = 8700 used, 1300 remaining
    async def _seed_q2():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "trend-q2-spend")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="trend-q2-spend", employee_id="emp-trend", status="reviewed",
                    amount=Decimal("8700"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="TrendTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()
    asyncio.new_event_loop().run_until_complete(_seed_q2())

    # Seed past 3 complete months (Jan/Feb/Mar 2026 relative to today ≥ 2026-04-01)
    # 1800 + 2200 + 2525 = 6525, avg = 2175
    past_submissions = [
        ("trend-m1", "2026-01-15", Decimal("1800")),
        ("trend-m2", "2026-02-15", Decimal("2200")),
        ("trend-m3", "2026-03-15", Decimal("2525")),
    ]
    async def _seed_past():
        from backend.db.store import Submission
        async with _TestSession() as db:
            for sid, dt, amt in past_submissions:
                existing = await db.execute(
                    __import__('sqlalchemy', fromlist=['select']).select(Submission)
                    .where(Submission.id == sid)
                )
                if existing.scalar_one_or_none() is None:
                    db.add(Submission(
                        id=sid, employee_id="emp-trend", status="reviewed",
                        amount=amt, currency="CNY", category="travel",
                        date=dt, merchant="TrendPast", receipt_url="http://x.com/r.png",
                        cost_center=cc,
                    ))
            await db.commit()
    asyncio.new_event_loop().run_until_complete(_seed_past())

    r = client.get(f"/api/budget/status/{cc}?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "trend" in body
    trend = body["trend"]
    assert abs(trend["monthly_avg"] - 2175.0) < 1.0        # avg of 1800+2200+2525
    assert trend["overrun_risk"] == "high"                  # 1300 remaining / 2175 avg ≈ 0.6 months
    assert trend["estimated_overrun_date"] is not None
    assert len(trend["months"]) == 3                        # oldest → newest


def test_budget_status_trend_zero_history():
    """No past-month submissions → monthly_avg=0, overrun_risk=ok, no overrun date."""
    cc = "CC-TREND-ZERO"
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))

    r = client.get(f"/api/budget/status/{cc}?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "trend" in body
    trend = body["trend"]
    assert trend["monthly_avg"] == 0.0
    assert trend["overrun_risk"] == "ok"
    assert trend["estimated_overrun_date"] is None


def test_budget_status_no_budget_has_no_trend():
    """Unconfigured cost center → configured=False, no trend key."""
    r = client.get("/api/budget/status/CC-NO-BUDGET-EVER", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert "trend" not in body


# ── snapshot/me trend narrative tests ─────────────────────────────────────────

def test_snapshot_me_appends_trend_narrative_when_high_risk():
    """snapshot/me: when signal=info/blocked and overrun_risk=high → message contains 月均."""
    cc = "CC-SNAP-HIGH"
    emp_id = "emp-snap-high"
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.new_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    # Seed Q2 spend at 80% (info signal)
    async def _seed_q2_snap():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "snap-q2-high")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="snap-q2-high", employee_id=emp_id, status="reviewed",
                    amount=Decimal("8000"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="SnapTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()

    # Seed past months: avg 3000/month → 2000 remaining / 3000 avg = 0.67 months → high
    async def _seed_past_snap():
        from backend.db.store import Submission
        async with _TestSession() as db:
            for sid, dt, amt in [
                ("snap-m1", "2026-01-15", Decimal("3000")),
                ("snap-m2", "2026-02-15", Decimal("3000")),
                ("snap-m3", "2026-03-15", Decimal("3000")),
            ]:
                existing = await db.execute(
                    __import__('sqlalchemy', fromlist=['select']).select(Submission)
                    .where(Submission.id == sid)
                )
                if existing.scalar_one_or_none() is None:
                    db.add(Submission(
                        id=sid, employee_id=emp_id, status="reviewed",
                        amount=amt, currency="CNY", category="travel",
                        date=dt, merchant="SnapPast", receipt_url="http://x.com/r.png",
                        cost_center=cc,
                    ))
            await db.commit()

    asyncio.new_event_loop().run_until_complete(_seed_q2_snap())
    asyncio.new_event_loop().run_until_complete(_seed_past_snap())

    r = client.get(
        "/api/budget/snapshot/me",
        headers={"X-User-Id": emp_id, "X-User-Role": "employee"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["message"] is not None
    assert "月均" in body["message"]


def test_snapshot_me_no_trend_when_ok_risk():
    """snapshot/me: when overrun_risk=ok (low avg), message does not mention 月均."""
    cc = "CC-SNAP-OK"
    emp_id = "emp-snap-ok"
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.new_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    # Seed Q2 spend at 80% (info signal — so we get a message)
    async def _seed_q2_ok():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "snap-q2-ok")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="snap-q2-ok", employee_id=emp_id, status="reviewed",
                    amount=Decimal("8000"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="OkTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()

    # No past-month submissions → monthly_avg = 0 → overrun_risk = ok
    asyncio.new_event_loop().run_until_complete(_seed_q2_ok())

    r = client.get(
        "/api/budget/snapshot/me",
        headers={"X-User-Id": emp_id, "X-User-Role": "employee"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["message"] is not None       # signal=info → message exists
    assert "月均" not in body["message"]     # but no trend narrative


# ── chat tool trend key test ───────────────────────────────────────────────────

def test_tool_get_budget_summary_includes_trend():
    """tool_get_budget_summary must return a 'trend' key so the LLM can read overrun_risk."""
    cc = "CC-CHAT-TREND"
    emp_id = "emp-chat-trend"
    asyncio.new_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.new_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    async def _call_tool():
        from backend.api.routes.chat import tool_get_budget_summary
        from backend.api.middleware.auth import UserContext
        async with _TestSession() as db:
            ctx = UserContext(user_id=emp_id, role="employee")
            return await tool_get_budget_summary({}, ctx, db, "")

    result = asyncio.new_event_loop().run_until_complete(_call_tool())
    assert "trend" in result
    assert result["trend"] is not None or result.get("configured") is False
