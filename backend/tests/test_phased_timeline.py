"""Phased audit timeline integration test.

Verifies the audit_report.timeline grows progressively as the workflow
advances through approval phases:

  submit          → 3 entries, all phase="submit" (Skills 1-3)
  manager_approved → 4 entries, last phase="manager_approved"
  finance_approved → 4+ entries, finance pipeline (Skills 4-5) runs as background task

Skills 1-3 (receipt, approval, compliance) run at submit time.
Skills 4-5 (voucher, payment) run after finance approval.
"""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import time
from pathlib import Path

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_phased_test")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import Base, get_db
from backend.main import app
from backend.storage import LocalStorage, get_storage

_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)
_storage = LocalStorage(base_dir=Path("/tmp/concurshield_phased_test"))

async def _override_get_db():
    async with _Session() as session:
        yield session


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: _storage


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_storage, None)
    os.unlink(_TMP_DB.name)


client = TestClient(app)
EMP = {"X-User-Id": "emp-phased", "X-User-Role": "employee"}
MGR = {"X-User-Id": "mgr-phased", "X-User-Role": "manager"}
FIN = {"X-User-Id": "fin-phased", "X-User-Role": "finance_admin"}


def _png():
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
        b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
        b'_\xfb\x00\x00\x00\x00IEND\xaeB`\x82'
    )


def _wait_until(predicate, timeout=3.0):
    """Poll until predicate returns truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = predicate()
        if v:
            return v
        time.sleep(0.05)
    return None


def test_timeline_phases_grow_through_workflow():
    # ── Phase 1: submit via old single-submission API ──
    files = {"receipt_image": ("test.png", io.BytesIO(_png()), "image/png")}
    form = {
        "amount": "150.00", "currency": "CNY", "category": "meal",
        "date": "2026-04-10", "merchant": "海底捞测试",
        "description": "团队午餐讨论项目进度",
        "invoice_number": "10000001",
        "invoice_code": "310012135012",
    }
    resp = client.post("/api/submissions", headers=EMP, data=form, files=files)
    assert resp.status_code == 202, resp.text
    sid = resp.json()["id"]

    sub = _wait_until(lambda: (
        client.get(f"/api/submissions/{sid}", headers=EMP).json()
        if client.get(f"/api/submissions/{sid}", headers=EMP).json().get("audit_report")
        else None
    ))
    assert sub is not None, "AI pipeline did not complete in time"

    timeline = sub["audit_report"]["timeline"]
    assert len(timeline) == 4, f"submit phase should produce 4 entries, got {len(timeline)}"
    assert all(t.get("phase") == "submit" for t in timeline), \
        f"all submit-phase entries should be tagged phase='submit', got {timeline}"
    joined = " ".join(t.get("message", "") for t in timeline)
    assert "凭证" not in joined, f"voucher should not appear at submit phase: {joined}"
    assert "付款" not in joined, f"payment should not appear at submit phase: {joined}"

    # ── Phase 2: manager approves (submission-level) → timeline entry appended ──
    resp = client.post(f"/api/submissions/{sid}/approve",
                       headers=MGR, json={"comment": "ok"})
    assert resp.status_code == 200, resp.text

    sub = client.get(f"/api/submissions/{sid}", headers=MGR).json()
    timeline = sub["audit_report"]["timeline"]
    assert len(timeline) == 5, f"after manager approval should have 5 entries, got {len(timeline)}"
    assert timeline[-1]["phase"] == "manager_approved"


def test_explain_card_only_shows_real_phases_at_submit():
    """AI explain card before manager approval should NOT mention 凭证/付款."""
    files = {"receipt_image": ("test.png", io.BytesIO(_png()), "image/png")}
    form = {
        "amount": "120.00", "currency": "CNY", "category": "meal",
        "date": "2026-04-10", "merchant": "测试咖啡",
        "description": "客户会议咖啡",
        "invoice_number": "10000002",
        "invoice_code": "310012135012",
    }
    resp = client.post("/api/submissions", headers=EMP, data=form, files=files)
    sid = resp.json()["id"]

    _wait_until(lambda: (
        client.get(f"/api/submissions/{sid}", headers=EMP).json().get("audit_report")
    ))

    resp = client.post(f"/api/chat/explain/{sid}", headers=MGR)
    assert resp.status_code == 200
    body = resp.json()
    all_flags_text = " ".join(body["green_flags"] + body["yellow_flags"] + body["red_flags"])
    assert "凭证" not in all_flags_text, \
        f"AI card at submit phase should not mention voucher: {all_flags_text}"
    assert "付款" not in all_flags_text, \
        f"AI card at submit phase should not mention payment: {all_flags_text}"
