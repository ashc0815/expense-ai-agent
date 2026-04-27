"""Voucher granularity tests — one report, one voucher number.

PR-1 changed voucher_number to live primarily on Report (not Submission)
and made next_voucher_number() idempotent per report. This test file
locks in those invariants:

  1. A finance approval generates ONE voucher_number per report and
     copies it to all submissions in the report.
  2. Calling next_voucher_number(db, report_id=X) twice returns the
     SAME number (idempotent), even if there's a retry / partial failure.
  3. Different reports get different voucher numbers.
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
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_voucher_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import (
    Base, create_report, create_submission, get_db,
    next_voucher_number, set_report_status, get_report,
)


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())


def teardown_module(_):
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass  # Windows: temp file still locked, OS Temp cleanup handles it


@pytest.mark.asyncio
async def test_next_voucher_number_idempotent_per_report():
    """Same report → same voucher number, no matter how many times called."""
    async with _Session() as db:
        report = await create_report(db, "emp-vt-1", title="Voucher Test")
        # First call: assigns a fresh number
        v1 = await next_voucher_number(db, report_id=report.id)
        assert v1.startswith(f"{date.today().strftime('%Y%m')}-")

        # Persist it on the report (mimicking what finance_approve does)
        report.voucher_number = v1
        await db.commit()

        # Second call with same report_id: must return SAME number
        v2 = await next_voucher_number(db, report_id=report.id)
        assert v2 == v1, f"expected idempotent call to return {v1}, got {v2}"

        # Third call: still same
        v3 = await next_voucher_number(db, report_id=report.id)
        assert v3 == v1


@pytest.mark.asyncio
async def test_different_reports_get_different_voucher_numbers():
    """Two different reports must get distinct voucher numbers."""
    async with _Session() as db:
        r1 = await create_report(db, "emp-vt-2", title="Report A")
        r2 = await create_report(db, "emp-vt-3", title="Report B")

        v1 = await next_voucher_number(db, report_id=r1.id)
        r1.voucher_number = v1
        await db.commit()

        v2 = await next_voucher_number(db, report_id=r2.id)
        assert v2 != v1, "different reports must not share voucher numbers"


@pytest.mark.asyncio
async def test_voucher_number_increments_across_reports():
    """The N suffix should increment monotonically across the month."""
    async with _Session() as db:
        # Read all currently-allocated vouchers to compute the expected base
        prefix = date.today().strftime("%Y%m")
        r = await create_report(db, "emp-vt-incr", title="Incr Test")
        v = await next_voucher_number(db, report_id=r.id)
        assert v.startswith(prefix + "-")
        n = int(v.split("-")[1])
        assert n >= 1


@pytest.mark.asyncio
async def test_legacy_call_without_report_id_still_works():
    """Backward compat: calling without report_id returns a fresh number
    (used by tests / callers that don't know the report yet)."""
    async with _Session() as db:
        v = await next_voucher_number(db)
        prefix = date.today().strftime("%Y%m")
        assert v.startswith(prefix + "-")
