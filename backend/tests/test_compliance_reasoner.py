"""Tests for the agent compliance reasoner.

Each of the three checks gets a hits-it / misses-it pair, plus a few
edge cases. The seed scenarios from seed_compliance_demo are the
gold path; we also build fresh data inline to test negatives that
don't exist in the seed.

Plus a registry test: every reasoner finding kind has a template
entry in AGENT_VIOLATIONS, so the factory never silently drops
findings on the floor.
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
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_reasoner_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from agent.compliance_reasoner import (
    ALLOWANCE_BLOCKS_CATEGORY, reason_about_submission,
)
from agent.violation_registry import (
    AGENT_VIOLATIONS, collect_agent_violations,
    violation_from_agent_finding,
)
from backend.db.store import (
    Base, EvalBase, add_employee_leave, add_submission_attendee,
    seed_budget_demo, seed_compliance_demo, upsert_employee_allowance,
)


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


def setup_module(_):
    # Build tables + run seeds against THIS file's engine. We avoid
    # init_db() because it uses the module-level engine in store.py,
    # which gets bound to whichever DATABASE_URL is set first across
    # the whole pytest run.
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


def teardown_module(_):
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass


# ── Scenario 1 — travel during leave ─────────────────────────────────

@pytest.mark.asyncio
async def test_travel_claim_during_seeded_leave_fires():
    """E001 has approved vacation 2026-04-15..17. A transport claim on
    4-16 must produce agent.travel_during_leave."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-tdl-1",
            employee_id="E001",
            expense_date="2026-04-16",
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.travel_during_leave" in kinds
        f = next(x for x in findings if x["kind"] == "agent.travel_during_leave")
        assert f["evidence_chain"]
        ev = f["evidence_chain"][0]
        assert ev["kind"] == "approved_leave"
        assert ev["leave_kind"] == "vacation"
        assert ev["start_date"] == "2026-04-15"
        assert ev["end_date"] == "2026-04-17"


@pytest.mark.asyncio
async def test_travel_claim_outside_leave_does_not_fire():
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-tdl-2",
            employee_id="E001",
            expense_date="2026-04-25",  # well outside the leave window
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.travel_during_leave" not in kinds


@pytest.mark.asyncio
async def test_non_travel_category_does_not_trigger_leave_check():
    """Office supplies on a leave day shouldn't fire — the rule only
    applies to travel-shaped categories (transport/accommodation)."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-tdl-3",
            employee_id="E001",
            expense_date="2026-04-16",
            category="other",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.travel_during_leave" not in kinds


@pytest.mark.asyncio
async def test_pending_leave_does_not_block_claim():
    """Only APPROVED leave triggers — pending requests are still up in
    the air and shouldn't block legitimate business travel."""
    async with _Session() as db:
        await add_employee_leave(
            db, employee_id="E777",
            start_date=date(2026, 7, 10),
            end_date=date(2026, 7, 12),
            kind="personal", status="pending",
        )
        findings = await reason_about_submission(
            db,
            submission_id="test-tdl-4",
            employee_id="E777",
            expense_date="2026-07-11",
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.travel_during_leave" not in kinds


# ── Scenario 2 — claim vs allowance ──────────────────────────────────

@pytest.mark.asyncio
async def test_transport_claim_with_active_car_allowance_fires():
    """E003 has car_allowance ¥2000 active. A transport claim must fire."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-cva-1",
            employee_id="E003",
            expense_date="2026-04-20",
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.claim_vs_allowance" in kinds
        f = next(x for x in findings if x["kind"] == "agent.claim_vs_allowance")
        ev = f["evidence_chain"][0]
        assert ev["kind"] == "active_allowance"
        assert ev["allowance_kind"] == "car_allowance"
        assert ev["monthly_amount"] == 2000.0


@pytest.mark.asyncio
async def test_unrelated_category_does_not_fire_against_car_allowance():
    """E003 has car_allowance — but a meal claim shouldn't trigger;
    car_allowance only blocks transport."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-cva-2",
            employee_id="E003",
            expense_date="2026-04-20",
            category="meal",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.claim_vs_allowance" not in kinds


@pytest.mark.asyncio
async def test_no_allowance_no_finding():
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-cva-3",
            employee_id="E001",  # no allowance seeded
            expense_date="2026-04-20",
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.claim_vs_allowance" not in kinds


@pytest.mark.asyncio
async def test_meal_per_diem_blocks_meal_claims():
    """Mapping check — meal_per_diem must block category=meal."""
    async with _Session() as db:
        await upsert_employee_allowance(
            db, employee_id="E555", kind="meal_per_diem",
            monthly_amount=Decimal("1500"),
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )
        findings = await reason_about_submission(
            db,
            submission_id="test-cva-4",
            employee_id="E555",
            expense_date="2026-04-01",
            category="meal",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.claim_vs_allowance" in kinds


# ── Scenario 3 — cross-employee meal double-dip ──────────────────────

@pytest.mark.asyncio
async def test_meal_collision_with_seeded_attendee_record_fires():
    """E001 is seeded as attendee on E002's seed-mkt-1 (entertainment
    on 2026-04-03). When E001 submits a meal that day → fires."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-meal-1",
            employee_id="E001",
            expense_date="2026-04-03",
            category="meal",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.cross_person_meal_double_dip" in kinds
        f = next(x for x in findings if x["kind"] == "agent.cross_person_meal_double_dip")
        ev = f["evidence_chain"][0]
        assert ev["kind"] == "appears_on_other_submission"
        assert ev["other_submission_id"] == "seed-mkt-1"
        assert ev["other_submitter_id"] == "E002"


@pytest.mark.asyncio
async def test_no_meal_collision_when_no_attendee_record():
    """Different employee, same date, same category — but no attendee
    appearance. Must not fire."""
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-meal-2",
            employee_id="E999",  # no records
            expense_date="2026-04-03",
            category="meal",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.cross_person_meal_double_dip" not in kinds


@pytest.mark.asyncio
async def test_non_meal_category_does_not_trigger_collision_check():
    async with _Session() as db:
        findings = await reason_about_submission(
            db,
            submission_id="test-meal-3",
            employee_id="E001",
            expense_date="2026-04-03",
            category="transport",
        )
        kinds = [f["kind"] for f in findings]
        assert "agent.cross_person_meal_double_dip" not in kinds


# ── Multi-trigger: all three at once ─────────────────────────────────

@pytest.mark.asyncio
async def test_two_findings_at_once_for_e003_on_attendee_day():
    """Build a tricky case: an employee with car_allowance who claims
    transport on a day they were also attendee on someone else's
    entertainment. Should produce allowance violation but NOT meal
    collision (transport != meal/entertainment)."""
    async with _Session() as db:
        # Add E003 as attendee on seed-mkt-1
        await add_submission_attendee(
            db, submission_id="seed-mkt-1",
            name="Wang Fang", employee_id="E003", role="colleague",
        )
        findings = await reason_about_submission(
            db,
            submission_id="test-multi-1",
            employee_id="E003",
            expense_date="2026-04-03",
            category="transport",
        )
        kinds = sorted(f["kind"] for f in findings)
        assert "agent.claim_vs_allowance" in kinds
        # transport is not meal/entertainment → no collision
        assert "agent.cross_person_meal_double_dip" not in kinds


# ── Registry / factory tests ─────────────────────────────────────────

def test_every_finding_kind_has_a_template():
    """If the reasoner ever emits a kind we haven't templated, the UI
    silently drops it. Sanity-check the union here."""
    reasoner_kinds = {
        "agent.travel_during_leave",
        "agent.claim_vs_allowance",
        "agent.cross_person_meal_double_dip",
    }
    assert reasoner_kinds.issubset(AGENT_VIOLATIONS.keys())


def test_violation_factory_attaches_evidence_chain_and_context():
    finding = {
        "kind": "agent.claim_vs_allowance",
        "context": {"category": "transport"},
        "evidence_chain": [{"kind": "active_allowance", "allowance_kind": "car_allowance"}],
    }
    v = violation_from_agent_finding(finding)
    assert v["rule_id"] == "agent.claim_vs_allowance"
    assert v["severity"] == "error"
    assert v["context"] == {"category": "transport"}
    assert v["evidence_chain"][0]["allowance_kind"] == "car_allowance"


def test_unknown_finding_kind_returns_none():
    assert violation_from_agent_finding({"kind": "agent.does_not_exist"}) is None


def test_collect_agent_violations_skips_unknowns():
    findings = [
        {"kind": "agent.travel_during_leave", "evidence_chain": [], "context": {}},
        {"kind": "agent.does_not_exist"},
        {"kind": "agent.claim_vs_allowance", "evidence_chain": [], "context": {}},
    ]
    out = collect_agent_violations(findings)
    assert len(out) == 2
    assert {v["rule_id"] for v in out} == {
        "agent.travel_during_leave", "agent.claim_vs_allowance",
    }


def test_allowance_blocks_category_mapping_is_not_empty():
    """Smoke: the mapping is the source of truth for what counts as a
    conflict. An empty mapping would silently disable check 2."""
    assert ALLOWANCE_BLOCKS_CATEGORY
    assert "car_allowance" in ALLOWANCE_BLOCKS_CATEGORY
    assert "transport" in ALLOWANCE_BLOCKS_CATEGORY["car_allowance"]


def test_policy_limit_exceeded_no_longer_recommends_splitting():
    """The 'split into multiple submissions' suggestion was itself
    non-compliant — make sure we don't accidentally restore it."""
    from agent.violation_registry import POLICY_VIOLATIONS
    suggestion = POLICY_VIOLATIONS["limit_exceeded"].get("suggestion", "")
    assert "拆分" not in suggestion
    assert "拆单" not in suggestion
    assert "split" not in suggestion.lower()
