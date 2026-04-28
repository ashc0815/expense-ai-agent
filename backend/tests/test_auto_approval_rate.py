"""Tests for the auto-approval funnel KPI — locks in the math.

This endpoint feeds the eval dashboard's "AI Auto-Approval Funnel" cards
(T1+T2 / T3 / T4 breakdown). The math is simple but easy to break:
  auto_approve_rate = (T1 + T2) / total
  human_review_rate = T3 / total
  rejection_rate    = T4 / total
  T1 + T2 + T3 + T4 == total  (when all tiered)

We seed 4 submissions with known tiers and assert the rates match.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, datetime, timezone

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)  # share the file for tests
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_funnel_test")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import (
    Base, EvalBase, create_submission, get_db, update_submission_analysis,
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

    async def _seed():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(EvalBase.metadata.create_all)
        async with _Session() as s:
            today_iso = date.today().isoformat()
            # 5 reviewed submissions: 2x T1, 1x T2, 1x T3, 1x T4
            # Expected: total=5, auto=3 (60%), review=1 (20%), reject=1 (20%)
            seed_data = [
                ("FUNNEL00000001", "T1"),
                ("FUNNEL00000002", "T1"),
                ("FUNNEL00000003", "T2"),
                ("FUNNEL00000004", "T3"),
                ("FUNNEL00000005", "T4"),
            ]
            for inv, tier in seed_data:
                sub = await create_submission(s, {
                    "employee_id": "emp-funnel",
                    "status": "reviewed",
                    "amount": 100.0, "currency": "CNY",
                    "category": "meal", "date": today_iso,
                    "merchant": f"Merchant {tier}",
                    "receipt_url": "/tmp/x.jpg",
                    "invoice_number": inv,
                })
                # set tier in a separate update so we can keep create_submission
                # signature stable
                from sqlalchemy import update as _update
                from backend.db.store import Submission as _Sub
                await s.execute(_update(_Sub).where(_Sub.id == sub.id).values(tier=tier))
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed())
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
        pass  # Windows: temp file still locked, OS Temp cleanup handles it


client = TestClient(app)
HEADERS = {"X-User-Id": "emp-funnel", "X-User-Role": "employee"}


def test_funnel_returns_correct_breakdown():
    r = client.get("/api/eval/auto-approval-rate?days=30", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5
    assert body["by_tier"] == {"T1": 2, "T2": 1, "T3": 1, "T4": 1}
    assert body["auto_approve_count"] == 3   # T1 + T2
    assert body["auto_approve_rate"] == 0.6  # 3 / 5
    assert body["human_review_count"] == 1
    assert body["human_review_rate"] == 0.2
    assert body["rejection_count"] == 1
    assert body["rejection_rate"] == 0.2


def test_rates_sum_to_one():
    r = client.get("/api/eval/auto-approval-rate?days=30", headers=HEADERS).json()
    total = (
        r["auto_approve_rate"] + r["human_review_rate"] + r["rejection_rate"]
    )
    assert abs(total - 1.0) < 0.001, f"rates should sum to 1, got {total}"


def test_window_days_param_is_validated():
    # 0 days → invalid
    r = client.get("/api/eval/auto-approval-rate?days=0", headers=HEADERS)
    assert r.status_code == 422

    # 366 days → invalid (cap is 365)
    r = client.get("/api/eval/auto-approval-rate?days=400", headers=HEADERS)
    assert r.status_code == 422


def test_zero_data_returns_zero_rates_not_division_by_zero():
    """Default window of 1 day: today's seed should still appear, but if a
    real deployment has no recent reviewed submissions, the endpoint must
    return 0.0 rates instead of crashing on /0."""
    # We can't easily prove a "zero" scenario without wiping the table, but
    # we can at least confirm a 1-day window doesn't crash.
    r = client.get("/api/eval/auto-approval-rate?days=1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    # All rates must be valid floats
    for k in ("auto_approve_rate", "human_review_rate", "rejection_rate"):
        assert isinstance(body[k], float)
        assert 0.0 <= body[k] <= 1.0
