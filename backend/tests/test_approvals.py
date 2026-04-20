"""Task 6 — 审批 API 测试。"""
from __future__ import annotations

import io
import os
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_test_uploads")

import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import Base, get_db
from backend.main import app
from backend.storage import LocalStorage, get_storage

_test_engine = create_async_engine(_DB_URL)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)
_test_storage = LocalStorage(base_dir=Path("/tmp/concurshield_test_uploads"))

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
    app.dependency_overrides[get_storage] = lambda: _test_storage

def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_storage, None)
    os.unlink(_TMP_DB.name)

client = TestClient(app)

EMP_HEADERS  = {"X-User-Id": "emp-001", "X-User-Role": "employee"}
MGR_HEADERS  = {"X-User-Id": "mgr-001", "X-User-Role": "manager"}
FIN_HEADERS  = {"X-User-Id": "fin-001", "X-User-Role": "finance_admin"}


def _fake_png():
    return io.BytesIO(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )


def _submit(headers=EMP_HEADERS, amount="300.0"):
    r = client.post(
        "/api/submissions",
        headers=headers,
        data={"amount": amount, "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "海底捞"},
        files={"receipt_image": ("r.png", _fake_png(), "image/png")},
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


# ── 测试 ──────────────────────────────────────────────────────────

def test_manager_can_approve():
    sid = _submit()
    r = client.post(f"/api/submissions/{sid}/approve",
                    headers=MGR_HEADERS,
                    json={"comment": "looks good"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "manager_approved"
    assert body["approver_id"] == "mgr-001"
    assert body["approver_comment"] == "looks good"


def test_manager_can_reject():
    sid = _submit()
    r = client.post(f"/api/submissions/{sid}/reject",
                    headers=MGR_HEADERS,
                    json={"comment": "invalid receipt"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert body["approver_comment"] == "invalid receipt"


def test_employee_cannot_approve():
    sid = _submit()
    r = client.post(f"/api/submissions/{sid}/approve",
                    headers=EMP_HEADERS,
                    json={})
    assert r.status_code == 403


def test_finance_admin_can_approve():
    sid = _submit()
    r = client.post(f"/api/submissions/{sid}/approve",
                    headers=FIN_HEADERS,
                    json={})
    assert r.status_code == 200
    assert r.json()["status"] == "manager_approved"


def test_approve_nonexistent_returns_404():
    r = client.post("/api/submissions/no-such-id/approve",
                    headers=MGR_HEADERS, json={})
    assert r.status_code == 404


def test_double_approve_returns_409():
    sid = _submit()
    client.post(f"/api/submissions/{sid}/approve", headers=MGR_HEADERS, json={})
    r = client.post(f"/api/submissions/{sid}/approve", headers=MGR_HEADERS, json={})
    assert r.status_code == 409


def test_bulk_approve():
    ids = [_submit() for _ in range(3)]
    r = client.post("/api/submissions/bulk-approve",
                    headers=MGR_HEADERS,
                    json={"ids": ids, "comment": "batch ok"})
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["approved"]) == sorted(ids)
    assert body["skipped"] == []
    assert body["not_found"] == []


def test_bulk_approve_partial():
    sid = _submit()
    r = client.post("/api/submissions/bulk-approve",
                    headers=MGR_HEADERS,
                    json={"ids": [sid, "fake-id"], "comment": None})
    assert r.status_code == 200
    body = r.json()
    assert sid in body["approved"]
    assert "fake-id" in body["not_found"]
