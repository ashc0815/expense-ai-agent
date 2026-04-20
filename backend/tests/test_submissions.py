"""Task 5 — 提交报销单 API 测试。"""
from __future__ import annotations

import io
import os
import tempfile

# 使用临时文件 DB（background task 需要跨 engine 访问同一张表）
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
    _cfg.DATABASE_URL = _DB_URL  # 让 _run_pipeline 背景任务也用同一个 DB

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
HEADERS = {"X-User-Id": "emp-test", "X-User-Role": "employee"}


def _fake_png():
    png = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    return io.BytesIO(png)


# ── 测试 ──────────────────────────────────────────────────────────

def test_submit_returns_202_and_processing():
    r = client.post(
        "/api/submissions",
        headers=HEADERS,
        data={"amount": "480.0", "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "海底捞"},
        files={"receipt_image": ("r.png", _fake_png(), "image/png")},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "processing"
    assert "id" in body


def test_get_submission_after_post():
    r1 = client.post(
        "/api/submissions",
        headers=HEADERS,
        data={"amount": "200.0", "currency": "CNY",
              "category": "transport", "date": "2026-04-11", "merchant": "滴滴"},
        files={"receipt_image": ("r.png", _fake_png(), "image/png")},
    )
    sub_id = r1.json()["id"]
    r2 = client.get(f"/api/submissions/{sub_id}", headers=HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == sub_id


def test_employee_cannot_see_others_submission():
    r1 = client.post(
        "/api/submissions",
        headers={"X-User-Id": "emp-A", "X-User-Role": "employee"},
        data={"amount": "100.0", "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "KFC"},
        files={"receipt_image": ("r.png", _fake_png(), "image/png")},
    )
    sub_id = r1.json()["id"]
    r2 = client.get(f"/api/submissions/{sub_id}",
                    headers={"X-User-Id": "emp-B", "X-User-Role": "employee"})
    assert r2.status_code == 403


def test_wrong_file_type_returns_422():
    r = client.post(
        "/api/submissions",
        headers=HEADERS,
        data={"amount": "100.0", "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "test"},
        files={"receipt_image": ("doc.xlsx", io.BytesIO(b"PK\x03\x04"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 422


def test_large_file_returns_413():
    r = client.post(
        "/api/submissions",
        headers=HEADERS,
        data={"amount": "100.0", "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "test"},
        files={"receipt_image": ("big.png", io.BytesIO(b"x" * (10*1024*1024+1)), "image/png")},
    )
    assert r.status_code == 413
