"""Tests for the auto-applier — PR-C of #5 用户行为学习自动规则.

Locks in the apply-rules behavior:

  1. apply_rules_to_draft writes rule.value to the draft when an active
     rule's merchant matches.
  2. applied_count and last_applied_at are bumped.
  3. Source is recorded as "auto_rule" in field_sources (so the audit
     trail shows the field came from a learned rule, not OCR/user).
  4. Inactive rules (suggested / dismissed) are NOT applied.
  5. No matching merchant → no rules fire, no DB writes.
  6. Idempotent: applying twice doesn't double-bump applied_count when
     the value is already correct.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_apply_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import (
    Base, EvalBase, AutoRule, create_draft, decide_auto_rule,
    get_draft, upsert_auto_rule,
)
from backend.services.pattern_miner import apply_rules_to_draft


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


async def _make_active_rule(emp: str, merchant: str, field: str, value: str):
    async with _Session() as db:
        rule = await upsert_auto_rule(
            db,
            employee_id=emp,
            trigger_type="merchant_exact",
            trigger_value=merchant.casefold(),
            field=field,
            value=value,
            confidence=0.95,
            evidence_count=8,
        )
        await decide_auto_rule(
            db, rule.id, new_status="active", decided_by=emp,
        )
        return rule.id


@pytest.mark.asyncio
async def test_apply_writes_rule_value_to_draft():
    emp = "emp-apply-1"
    rule_id = await _make_active_rule(emp, "Starbucks", "category", "meal")

    async with _Session() as db:
        draft = await create_draft(db, emp)
        applied = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Starbucks",
        )
        assert len(applied) == 1
        assert applied[0]["field"] == "category"
        assert applied[0]["value"] == "meal"
        assert applied[0]["rule_id"] == rule_id

        # Verify it landed in the draft
        d2 = await get_draft(db, draft.id)
        assert (d2.fields or {}).get("category") == "meal"
        assert (d2.field_sources or {}).get("category") == "auto_rule"


@pytest.mark.asyncio
async def test_apply_increments_applied_count():
    emp = "emp-apply-2"
    rule_id = await _make_active_rule(emp, "Marriott", "category", "accommodation")

    async with _Session() as db:
        draft = await create_draft(db, emp)
        await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Marriott",
        )
        # Re-fetch the rule
        from backend.db.store import get_auto_rule
        rule = await get_auto_rule(db, rule_id)
        assert rule.applied_count == 1
        assert rule.last_applied_at is not None


@pytest.mark.asyncio
async def test_suggested_rule_is_not_applied():
    """Only 'active' rules apply. A suggestion the user hasn't seen yet
    must NOT silently mutate their draft."""
    emp = "emp-apply-3"
    async with _Session() as db:
        rule = await upsert_auto_rule(
            db,
            employee_id=emp,
            trigger_type="merchant_exact",
            trigger_value="hilton",
            field="category",
            value="accommodation",
            confidence=0.9,
            evidence_count=6,
        )
        # Stays in 'suggested' state
        assert rule.status == "suggested"

        draft = await create_draft(db, emp)
        applied = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Hilton",
        )
        assert applied == []
        d2 = await get_draft(db, draft.id)
        assert (d2.fields or {}).get("category") is None


@pytest.mark.asyncio
async def test_no_matching_merchant_is_a_noop():
    emp = "emp-apply-4"
    await _make_active_rule(emp, "Hilton", "category", "accommodation")

    async with _Session() as db:
        draft = await create_draft(db, emp)
        applied = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="UnrelatedShop",
        )
        assert applied == []


@pytest.mark.asyncio
async def test_apply_is_idempotent_when_value_already_correct():
    """Running the applier twice on the same draft should NOT bump
    applied_count the second time — the value is already right, so the
    rule didn't actually do anything."""
    emp = "emp-apply-5"
    rule_id = await _make_active_rule(emp, "Tim Hortons", "category", "meal")

    async with _Session() as db:
        draft = await create_draft(db, emp)
        # First call — bumps to 1
        a1 = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Tim Hortons",
        )
        assert len(a1) == 1
        # Second call — value is already 'meal', no change, no bump
        a2 = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Tim Hortons",
        )
        assert a2 == []
        from backend.db.store import get_auto_rule
        rule = await get_auto_rule(db, rule_id)
        assert rule.applied_count == 1


@pytest.mark.asyncio
async def test_overwrite_false_preserves_user_value():
    """When overwrite=False (used by future flows where user already
    typed something), the rule must NOT clobber a non-empty field."""
    emp = "emp-apply-6"
    await _make_active_rule(emp, "Didi", "category", "transport")

    async with _Session() as db:
        from backend.db.store import update_draft_field
        draft = await create_draft(db, emp)
        await update_draft_field(
            db, draft.id, "category", "user_chose_other", source="user",
        )
        applied = await apply_rules_to_draft(
            db, employee_id=emp, draft_id=draft.id, merchant="Didi",
            overwrite=False,
        )
        assert applied == []
        d2 = await get_draft(db, draft.id)
        assert (d2.fields or {}).get("category") == "user_chose_other"
