"""Task 14 — E2E 黄金路径测试（单进程内，模拟完整报销生命周期）。

Golden path:
  1. 员工提交报销单 → 202 + processing
  2. 员工查询自己的报销单 → 200
  3. 员工无法查看他人的报销单 → 403
  4. 经理查看全部报销单列表（含该条）
  5. 经理批准 → status=approved
  6. 员工看到状态已变为 approved
  7. 管理员统计中该条已被计入
  8. 管理员导出 CSV 中包含该条
"""
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
    asyncio.new_event_loop().run_until_complete(_create())

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: _test_storage

def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_storage, None)
    os.unlink(_TMP_DB.name)

client = TestClient(app)

EMP = {"X-User-Id": "emp-golden", "X-User-Role": "employee"}
MGR = {"X-User-Id": "mgr-golden", "X-User-Role": "manager"}
FIN = {"X-User-Id": "fin-golden", "X-User-Role": "finance_admin"}
OTHER_EMP = {"X-User-Id": "emp-other", "X-User-Role": "employee"}

def _png() -> bytes:
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )


def test_golden_path():
    # Step 1: 员工提交报销单
    r = client.post(
        "/api/submissions",
        headers=EMP,
        data={"amount": "480.0", "currency": "CNY",
              "category": "meal", "date": "2026-04-11", "merchant": "海底捞"},
        files={"receipt_image": ("r.png", io.BytesIO(_png()), "image/png")},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "processing"
    sub_id = body["id"]

    # Step 2: 员工查询自己的报销单
    r = client.get(f"/api/submissions/{sub_id}", headers=EMP)
    assert r.status_code == 200
    assert r.json()["id"] == sub_id

    # Step 3: 其他员工无法查看 → 403
    r = client.get(f"/api/submissions/{sub_id}", headers=OTHER_EMP)
    assert r.status_code == 403

    # Step 4: 经理查看列表
    r = client.get("/api/submissions", headers=MGR)
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["items"]]
    assert sub_id in ids

    # Step 5: 经理批准
    r = client.post(f"/api/submissions/{sub_id}/approve",
                    headers=MGR, json={"comment": "LGTM"})
    assert r.status_code == 200
    assert r.json()["status"] == "manager_approved"
    assert r.json()["approver_id"] == "mgr-golden"

    # Step 6: 员工看到状态已更新为 manager_approved
    r = client.get(f"/api/submissions/{sub_id}", headers=EMP)
    assert r.status_code == 200
    assert r.json()["status"] == "manager_approved"

    # Step 7: 管理员统计
    r = client.get("/api/admin/stats", headers=FIN)
    assert r.status_code == 200
    stats = r.json()
    assert stats["total_submissions"] >= 1

    # Step 8: 管理员导出 CSV 包含该条
    r = client.get("/api/admin/export", headers=FIN)
    assert r.status_code == 200
    assert sub_id in r.text


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_users_me():
    r = client.get("/api/users/me", headers=EMP)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "emp-golden"
    assert "employee" in body["roles"]
