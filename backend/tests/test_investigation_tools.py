"""Tests for the 8 read-only investigation tools (PR-A of fraud
investigator architecture).

Each tool is the agent's "eye" for a specific kind of question. Every
function returns a JSON-friendly dict so the agent can drop the result
straight into the next LLM prompt.

Layout:
  - 4 sync pure-function tools (geo, math)  → unit-test directly
  - 4 async DB tools                        → seed a tmp DB, query
  - INVESTIGATION_TOOLS registry            → ensure all 9 entries
                                              point to callables
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_invtools_test")

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.store import (
    Base, EvalBase, Employee, add_submission_attendee, create_submission,
    upsert_employee,
)
from backend.services.investigation_tools import (
    INVESTIGATION_TOOLS,
    check_geo_feasibility,
    check_math_consistency,
    get_amount_distribution,
    get_approval_history,
    get_employee_profile,
    get_merchant_usage,
    get_peer_comparison,
    get_recent_expenses,
    get_submission_attendees,
)

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


async def _seed_employee(emp_id: str, *, cost_center: str = "ENG", level: str = "L3", city: str = "上海"):
    async with _Session() as db:
        await upsert_employee(db, {
            "id": emp_id, "name": f"Test {emp_id}",
            "department": "Engineering", "cost_center": cost_center,
            "level": level, "city": city,
        })


async def _seed_submission(
    *, sub_id: str, employee_id: str, amount: float, category: str = "meal",
    merchant: str = "Default Merchant", date_str: str | None = None,
    cost_center: str | None = None, status: str = "finance_approved",
    description: str = "",
):
    async with _Session() as db:
        await create_submission(db, {
            "id": sub_id,
            "employee_id": employee_id,
            "status": status,
            "amount": Decimal(str(amount)),
            "currency": "CNY",
            "category": category,
            "date": date_str or date.today().isoformat(),
            "merchant": merchant,
            "receipt_url": "/uploads/test/x.jpg",
            "cost_center": cost_center,
            "description": description,
        })


# ── Tool 1: get_employee_profile ─────────────────────────────────────


@pytest.mark.asyncio
async def test_employee_profile_found_returns_full_record():
    await _seed_employee("emp_inv_1", cost_center="ENG-TRAVEL", level="L4")
    async with _Session() as db:
        out = await get_employee_profile(db, employee_id="emp_inv_1")
    assert out["found"] is True
    assert out["employee_id"] == "emp_inv_1"
    assert out["cost_center"] == "ENG-TRAVEL"
    assert out["level"] == "L4"


@pytest.mark.asyncio
async def test_employee_profile_missing_returns_found_false():
    async with _Session() as db:
        out = await get_employee_profile(db, employee_id="emp_does_not_exist")
    assert out["found"] is False
    assert out["employee_id"] == "emp_does_not_exist"


# ── Tool 2: get_recent_expenses ──────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_expenses_returns_within_window():
    emp = "emp_inv_recent"
    await _seed_employee(emp)
    today = date.today()
    # in window
    await _seed_submission(sub_id="rec_1", employee_id=emp, amount=100, date_str=today.isoformat())
    await _seed_submission(sub_id="rec_2", employee_id=emp, amount=200, date_str=(today - timedelta(days=10)).isoformat())
    # outside window
    await _seed_submission(sub_id="rec_3", employee_id=emp, amount=999, date_str=(today - timedelta(days=400)).isoformat())

    async with _Session() as db:
        out = await get_recent_expenses(db, employee_id=emp, days=90)
    assert out["count"] == 2
    ids = sorted([e["id"] for e in out["expenses"]])
    assert ids == ["rec_1", "rec_2"]


# ── Tool 3: get_approval_history ─────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_history_lists_approver_decisions():
    """Submissions that have approver_id + approved_at set show up in
    the history. This tool's value is detecting rubber-stampers."""
    from datetime import datetime, timezone
    approver = "mgr_001"
    submitter = "emp_inv_apphist"
    await _seed_employee(submitter)

    async with _Session() as db:
        sub = await create_submission(db, {
            "id": "appr_sub_1",
            "employee_id": submitter,
            "status": "manager_approved",
            "amount": Decimal("150"),
            "currency": "CNY",
            "category": "meal",
            "date": date.today().isoformat(),
            "merchant": "Test M",
            "receipt_url": "/uploads/x.jpg",
        })
        # Stamp it as approved by approver
        sub.approver_id = approver
        sub.approver_comment = "ok"
        sub.approved_at = datetime.now(timezone.utc)
        await db.commit()

    async with _Session() as db:
        out = await get_approval_history(db, approver_id=approver, days=90)
    assert out["count"] == 1
    assert out["approvals"][0]["submitter_id"] == submitter
    assert out["approvals"][0]["comment"] == "ok"


# ── Tool 4: get_merchant_usage ───────────────────────────────────────


@pytest.mark.asyncio
async def test_merchant_usage_unique_submitters():
    await _seed_employee("emp_mu_1")
    await _seed_employee("emp_mu_2")
    today = date.today().isoformat()
    await _seed_submission(sub_id="mu_a", employee_id="emp_mu_1", amount=100, merchant="Acme Cafe", date_str=today)
    await _seed_submission(sub_id="mu_b", employee_id="emp_mu_2", amount=200, merchant="Acme Cafe", date_str=today)
    await _seed_submission(sub_id="mu_c", employee_id="emp_mu_1", amount=80,  merchant="Acme Cafe", date_str=today)

    async with _Session() as db:
        out = await get_merchant_usage(db, merchant="Acme Cafe", days=30)

    assert out["total_count"] == 3
    assert out["unique_submitters"] == 2
    assert sorted(out["submitters"]) == ["emp_mu_1", "emp_mu_2"]


@pytest.mark.asyncio
async def test_merchant_usage_unknown_merchant_zero():
    async with _Session() as db:
        out = await get_merchant_usage(db, merchant="Never Seen Inc", days=30)
    assert out["total_count"] == 0
    assert out["submitters"] == []


# ── Tool 5: get_peer_comparison ──────────────────────────────────────


@pytest.mark.asyncio
async def test_peer_comparison_percentile_high():
    """Self spends 500/meal, peers all spend 100 → self_percentile = 1.0."""
    cc = "INV-CC-A"
    await _seed_employee("emp_pc_self", cost_center=cc)
    await _seed_employee("emp_pc_p1", cost_center=cc)
    await _seed_employee("emp_pc_p2", cost_center=cc)
    today = date.today().isoformat()
    # self: two ¥500 meals
    await _seed_submission(sub_id="pc_s1", employee_id="emp_pc_self", amount=500, date_str=today, cost_center=cc)
    await _seed_submission(sub_id="pc_s2", employee_id="emp_pc_self", amount=500, date_str=today, cost_center=cc)
    # peers: ¥100 each
    await _seed_submission(sub_id="pc_p1", employee_id="emp_pc_p1", amount=100, date_str=today, cost_center=cc)
    await _seed_submission(sub_id="pc_p2", employee_id="emp_pc_p2", amount=100, date_str=today, cost_center=cc)

    async with _Session() as db:
        out = await get_peer_comparison(db, employee_id="emp_pc_self", category="meal", days=30)

    assert out["self_avg"] == 500
    assert out["peer_count"] == 2
    assert out["peer_avg_mean"] == 100
    assert out["self_percentile"] == 1.0  # higher than 100% of peers


@pytest.mark.asyncio
async def test_peer_comparison_no_peers_returns_none_percentile():
    cc = "INV-CC-LONELY"
    await _seed_employee("emp_pc_alone", cost_center=cc)
    today = date.today().isoformat()
    await _seed_submission(sub_id="pc_alone", employee_id="emp_pc_alone", amount=300, date_str=today, cost_center=cc)
    async with _Session() as db:
        out = await get_peer_comparison(db, employee_id="emp_pc_alone", category="meal", days=30)
    assert out["peer_count"] == 0
    assert out["self_percentile"] is None


# ── Tool 6: get_amount_distribution ──────────────────────────────────


@pytest.mark.asyncio
async def test_amount_distribution_quantiles_match():
    emp = "emp_ad_1"
    await _seed_employee(emp)
    today = date.today().isoformat()
    # 5 samples: 100, 200, 300, 400, 500 → median = 300, mean = 300
    for i, amt in enumerate([100, 200, 300, 400, 500]):
        await _seed_submission(sub_id=f"ad_{i}", employee_id=emp, amount=amt, date_str=today)

    async with _Session() as db:
        out = await get_amount_distribution(db, employee_id=emp, category="meal", days=180)

    assert out["n"] == 5
    assert out["min"] == 100
    assert out["max"] == 500
    assert out["median"] == 300
    assert out["mean"] == 300


@pytest.mark.asyncio
async def test_amount_distribution_no_data_returns_none_quantiles():
    async with _Session() as db:
        out = await get_amount_distribution(
            db, employee_id="emp_no_history", category="meal", days=30,
        )
    assert out["n"] == 0
    assert out["median"] is None


# ── Tool 7: check_geo_feasibility ────────────────────────────────────


def test_geo_same_city_always_feasible():
    out = check_geo_feasibility(
        date_a="2026-04-15", city_a="上海",
        date_b="2026-04-15", city_b="上海",
    )
    assert out["feasible"] is True
    assert out["distance_km"] == 0


def test_geo_same_day_distant_cities_infeasible():
    """Same day, Shanghai → Beijing 1100km — flagged."""
    out = check_geo_feasibility(
        date_a="2026-04-15", city_a="上海",
        date_b="2026-04-15", city_b="北京",
    )
    assert out["feasible"] is False
    assert out["distance_km"] >= 1000
    assert "needs a travel record" in out["reason"].lower() or "travel" in out["reason"].lower()


def test_geo_same_day_close_cities_feasible():
    """Shanghai → Suzhou 80km — same-day commute is fine."""
    out = check_geo_feasibility(
        date_a="2026-04-15", city_a="上海",
        date_b="2026-04-15", city_b="苏州",
    )
    assert out["feasible"] is True


def test_geo_different_days_distant_cities_feasible():
    """Shanghai → Beijing across 2 days — flight is plausible."""
    out = check_geo_feasibility(
        date_a="2026-04-15", city_a="上海",
        date_b="2026-04-17", city_b="北京",
    )
    assert out["feasible"] is True
    assert out["date_diff_days"] == 2


def test_geo_unknown_city_returns_no_judgment():
    out = check_geo_feasibility(
        date_a="2026-04-15", city_a="上海",
        date_b="2026-04-15", city_b="Atlantis",
    )
    # Cannot judge — return feasible=True with cities_known=False so the
    # agent knows not to over-trust this signal
    assert out["feasible"] is True
    assert out.get("cities_known") is False


# ── Tool 8: check_math_consistency ───────────────────────────────────


def test_math_consistency_per_person_claim_matches():
    """¥80/人 × 5 people ≈ ¥400 — claim matches submitted."""
    out = check_math_consistency(
        amount=400, attendees_count=5,
        description="团队午餐 5人 人均 80",
    )
    assert out["consistent"] is True
    assert out["expected_total"] == 400.0


def test_math_consistency_per_person_claim_mismatch():
    """¥80/人 × 5 people = 400, but submitted 800 — clear mismatch."""
    out = check_math_consistency(
        amount=800, attendees_count=5,
        description="团队午餐 5人 人均 80",
    )
    assert out["consistent"] is False
    assert "off" in out["reason"]


def test_math_consistency_extracts_headcount_from_description():
    """When attendees_count is None but description has '5人',
    use the description claim."""
    out = check_math_consistency(
        amount=400,
        description="团队午餐 5人 人均 80",
    )
    assert out["effective_attendees_count"] == 5
    assert out["consistent"] is True


def test_math_consistency_no_claims_returns_none_verdict():
    """No numbers in description → tool can't judge, returns None."""
    out = check_math_consistency(amount=200, description="客户接待")
    assert out["consistent"] is None


def test_math_consistency_derives_per_person_when_no_claim():
    """When description has headcount but no per-person claim, derive
    per-person amount and let the agent decide."""
    out = check_math_consistency(amount=900, description="3人")
    assert out["amount_per_person_derived"] == 300.0
    assert out["consistent"] is None  # tool doesn't judge alone


# ── Bonus: get_submission_attendees ──────────────────────────────────


@pytest.mark.asyncio
async def test_attendees_returns_what_was_seeded():
    """Used when Layer-1 cites a specific other submission and the agent
    wants to see who was at that meal."""
    submitter = "emp_att_seed"
    await _seed_employee(submitter)
    await _seed_submission(sub_id="att_test_sub", employee_id=submitter, amount=2000, category="entertainment")
    async with _Session() as db:
        await add_submission_attendee(
            db, submission_id="att_test_sub",
            name="Client X", role="client",
        )
        await add_submission_attendee(
            db, submission_id="att_test_sub",
            name="Self", employee_id=submitter, role="colleague",
        )

    async with _Session() as db:
        out = await get_submission_attendees(db, submission_id="att_test_sub")

    assert out["count"] == 2
    roles = sorted(a["role"] for a in out["attendees"])
    assert roles == ["client", "colleague"]


# ── Registry sanity ──────────────────────────────────────────────────


def test_tool_registry_has_all_callables():
    """The OODA agent in PR-B will look up tools by name. Make sure
    every entry in INVESTIGATION_TOOLS is actually callable."""
    assert len(INVESTIGATION_TOOLS) >= 8
    for name, fn in INVESTIGATION_TOOLS.items():
        assert callable(fn), f"tool {name} is not callable"


def test_tool_registry_covers_advertised_8_tools():
    """The plan promises 8 tools; ensure they're all registered. The
    9th (get_submission_attendees) is a bonus accessor."""
    expected = {
        "get_employee_profile",
        "get_recent_expenses",
        "get_approval_history",
        "get_merchant_usage",
        "get_peer_comparison",
        "get_amount_distribution",
        "check_geo_feasibility",
        "check_math_consistency",
    }
    assert expected.issubset(set(INVESTIGATION_TOOLS.keys()))
