"""Tests for the agent compliance reasoner's read-only tools.

Each tool is the agent's "eyes" for a violation class hard rules
can't see in a single submission. The three demo scenarios from
seed_compliance_demo are the gold path:

  Scenario 1 — travel during approved leave (E001 vacation 4-15..17)
  Scenario 2 — claim-vs-allowance conflict (E003 car_allowance)
  Scenario 3 — cross-employee meal double-dip (E001 on E002's seed-mkt-1)

Every tool is tested for: hits, misses, and shape of the JSON it
returns (since the agent passes that JSON straight back into the LLM).
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
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_compliance_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import (
    Base, EvalBase, add_employee_leave, add_submission_attendee,
    create_submission, seed_budget_demo, seed_compliance_demo,
    upsert_employee_allowance,
)
from backend.services.compliance_lookups import (
    find_overlapping_claims, get_employee_allowances,
    get_employee_leave_in_range, get_submission_attendees,
    list_meals_with_attendees,
)


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


def setup_module(_):
    # We bypass init_db() because it uses the module-level `engine` in
    # backend.db.store, which is bound at import time to whichever
    # DATABASE_URL env var happened to be set first across the whole
    # pytest run. Building tables + running seeds against THIS file's
    # local engine keeps each test module fully self-contained.
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


# ── Tool 1 — leave conflict ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_leave_lookup_finds_overlapping_vacation():
    """Seeded: E001 vacation 4-15..17. A travel date 4-16 must hit."""
    async with _Session() as db:
        out = await get_employee_leave_in_range(
            db, employee_id="E001",
            start_date="2026-04-16", end_date="2026-04-16",
        )
        assert out["count"] == 1
        leave = out["leaves"][0]
        assert leave["kind"] == "vacation"
        assert leave["status"] == "approved"
        assert leave["start_date"] == "2026-04-15"
        assert leave["end_date"] == "2026-04-17"


@pytest.mark.asyncio
async def test_leave_lookup_no_overlap_returns_empty():
    async with _Session() as db:
        out = await get_employee_leave_in_range(
            db, employee_id="E001",
            start_date="2026-05-01", end_date="2026-05-31",
        )
        assert out["count"] == 0
        assert out["leaves"] == []


@pytest.mark.asyncio
async def test_leave_lookup_pending_filtered_out_by_default():
    """Only approved leave should block a travel claim. A pending leave
    must not be returned unless the caller asks for it explicitly."""
    async with _Session() as db:
        await add_employee_leave(
            db, employee_id="E099",
            start_date=date(2026, 6, 10), end_date=date(2026, 6, 12),
            kind="personal", status="pending",
        )
        out = await get_employee_leave_in_range(
            db, employee_id="E099",
            start_date="2026-06-11", end_date="2026-06-11",
        )
        assert out["count"] == 0
        # Now ask for any status
        out2 = await get_employee_leave_in_range(
            db, employee_id="E099",
            start_date="2026-06-11", end_date="2026-06-11",
            status=None,
        )
        assert out2["count"] == 1


# ── Tool 2 — allowance lookup ────────────────────────────────────────

@pytest.mark.asyncio
async def test_allowances_returns_active_car_allowance_for_e003():
    """Seeded: E003 has car_allowance ¥2000/month from 2026-01-01."""
    async with _Session() as db:
        out = await get_employee_allowances(
            db, employee_id="E003", on_date="2026-04-15",
        )
        assert out["count"] == 1
        a = out["allowances"][0]
        assert a["kind"] == "car_allowance"
        assert a["monthly_amount"] == 2000.0


@pytest.mark.asyncio
async def test_allowances_excludes_expired_records():
    async with _Session() as db:
        await upsert_employee_allowance(
            db, employee_id="E098", kind="phone_allowance",
            monthly_amount=Decimal("300"),
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 12, 31),
        )
        out = await get_employee_allowances(
            db, employee_id="E098", on_date="2026-04-01",
        )
        assert out["count"] == 0


@pytest.mark.asyncio
async def test_allowances_includes_open_ended_records():
    async with _Session() as db:
        await upsert_employee_allowance(
            db, employee_id="E097", kind="meal_per_diem",
            monthly_amount=Decimal("500"),
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )
        out = await get_employee_allowances(
            db, employee_id="E097", on_date="2030-12-31",
        )
        assert out["count"] == 1


# ── Tool 3 — meal collision ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_meal_collision_detects_e001_on_e002_dinner():
    """Seeded: E001 is an attendee on E002's seed-mkt-1 (entertainment
    on 2026-04-03). Querying E001's window must surface the appearance.
    """
    async with _Session() as db:
        out = await list_meals_with_attendees(
            db, employee_id="E001", on_date="2026-04-03", window_days=1,
        )
        assert out["appearance_count"] == 1
        ap = out["attendee_appearances"][0]
        assert ap["submitter_employee_id"] == "E002"
        assert ap["submission_id"] == "seed-mkt-1"
        assert ap["category"] == "entertainment"


@pytest.mark.asyncio
async def test_meal_collision_full_picture_when_self_also_claimed():
    """When the employee also has their own meal that day, both lists
    are populated — that's the actual collision the reasoner flags."""
    async with _Session() as db:
        sub = await create_submission(db, {
            "employee_id": "E001",
            "status": "reviewed",
            "amount": Decimal("450"), "currency": "CNY",
            "category": "meal", "date": "2026-04-03",
            "merchant": "Side restaurant",
            "receipt_url": "/uploads/x.jpg",
        })
        out = await list_meals_with_attendees(
            db, employee_id="E001", on_date="2026-04-03", window_days=1,
        )
        assert out["self_count"] >= 1
        assert any(s["submission_id"] == sub.id for s in out["self_submissions"])
        # The cross-person appearance is still there
        assert out["appearance_count"] >= 1


@pytest.mark.asyncio
async def test_meal_collision_quiet_window_returns_zero_each_side():
    async with _Session() as db:
        out = await list_meals_with_attendees(
            db, employee_id="E001", on_date="2026-12-25", window_days=1,
        )
        assert out["self_count"] == 0
        assert out["appearance_count"] == 0


# ── Tool 4 — overlapping claims ──────────────────────────────────────

@pytest.mark.asyncio
async def test_overlap_finds_seeded_eng_travel_claims():
    """Seeded: E001 has 2 ENG-TRAVEL submissions in early April (3800
    accommodation + 2000 transport). The transport claim is the only
    one of category='transport'."""
    async with _Session() as db:
        out = await find_overlapping_claims(
            db, employee_id="E001", category="transport",
            start_date="2026-04-01", end_date="2026-04-30",
        )
        assert out["count"] == 1
        assert out["claims"][0]["submission_id"] == "seed-eng-3"


@pytest.mark.asyncio
async def test_overlap_excludes_explicit_submission_id():
    async with _Session() as db:
        out = await find_overlapping_claims(
            db, employee_id="E001", category="transport",
            start_date="2026-04-01", end_date="2026-04-30",
            exclude_submission_id="seed-eng-3",
        )
        assert out["count"] == 0


# ── Bonus accessor ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_attendees_returns_seeded_attendees_for_seed_mkt_1():
    """seed-mkt-1 has E001 (colleague) + Acme Client (external)."""
    async with _Session() as db:
        rows = await get_submission_attendees(db, "seed-mkt-1")
        assert len(rows) == 2
        emp_attendee = next(r for r in rows if r["employee_id"] == "E001")
        assert emp_attendee["role"] == "colleague"
        client = next(r for r in rows if r["employee_id"] is None)
        assert client["role"] == "client"


# ── Idempotency on init_db ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_running_seed_twice_does_not_duplicate():
    """seed_compliance_demo is wired into init_db; a server restart must
    not pile up duplicates."""
    async with _Session() as db:
        await seed_compliance_demo(db)  # second call
        # Still exactly one allowance for E003
        out = await get_employee_allowances(
            db, employee_id="E003", on_date="2026-04-15",
        )
        assert out["count"] == 1
        # Still exactly two attendees on seed-mkt-1
        attendees = await get_submission_attendees(db, "seed-mkt-1")
        assert len(attendees) == 2
