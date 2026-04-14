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
    asyncio.get_event_loop().run_until_complete(_create())
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
    asyncio.get_event_loop().run_until_complete(
        _seed_budget("CC-LOW", "2026-Q2", 10000.0)
    )
    r = client.get("/api/budget/status/CC-LOW?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["signal"] == "ok"


def test_budget_status_projected_blocked():
    """Passing amount that would push usage to ≥ 95% → signal: blocked."""
    asyncio.get_event_loop().run_until_complete(
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
