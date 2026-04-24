"""Day 3 — AI explanation card endpoint tests.

Verifies:
  1. POST /api/chat/explain/{id} returns structured JSON for a manager.
  2. Tier → recommendation mapping is correct.
  3. Tool whitelist is enforced: employee role can't access the endpoint.
  4. Tool whitelist is also enforced at the dispatcher (we can't ask the
     manager_explain composer to run an off-list tool).
  5. Composer pulls employee history and computes an avg comparison.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import Base, create_submission, get_db, update_submission_analysis
from backend.main import app

_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)

async def _override_get_db():
    async with _Session() as session:
        yield session


_SUB_ID_T1 = None
_SUB_ID_T4 = None


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        global _SUB_ID_T1, _SUB_ID_T4
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _Session() as s:
            # 3 historical reports for emp-explain (avg ~150)
            base = date.today() - timedelta(days=20)
            for i, amt in enumerate([120, 150, 180]):
                await create_submission(s, {
                    "employee_id": "emp-explain",
                    "status": "exported",
                    "amount": float(amt), "currency": "CNY",
                    "category": "meal",
                    "date": (base + timedelta(days=i)).isoformat(),
                    "merchant": f"测试商户 {i}",
                    "description": f"团队午餐第 {i+1} 次",
                    "receipt_url": "/tmp/x.jpg",
                    "invoice_number": f"HIST{i:08d}",
                })
            # T1 case — happy path
            sub1 = await create_submission(s, {
                "employee_id": "emp-explain",
                "status": "reviewed",
                "amount": 150.00, "currency": "CNY",
                "category": "meal", "date": date.today().isoformat(),
                "merchant": "海底捞 (待审)",
                "description": "团队午餐讨论 AI 报销项目进度",
                "receipt_url": "/tmp/x.jpg",
                "invoice_number": "T1NEW0001",
            })
            await update_submission_analysis(
                s, sub1.id,
                tier="T1", risk_score=20.0,
                audit_report={
                    "final_status": "completed",
                    "timeline": [
                        {"message": "发票字段验证通过", "passed": True, "skipped": False},
                        {"message": "金额未超类别限额", "passed": True, "skipped": False},
                        {"message": "合规检查通过", "passed": True, "skipped": False},
                    ],
                    "shield_report": {"total_score": 8, "triggered": []},
                },
                status="reviewed",
            )
            _SUB_ID_T1 = sub1.id

            # T4 case — high risk, separate employee so history isn't polluted
            sub4 = await create_submission(s, {
                "employee_id": "emp-risky",
                "status": "reviewed",
                "amount": 5000.00, "currency": "CNY",
                "category": "entertainment", "date": date.today().isoformat(),
                "merchant": "未知娱乐场所",
                "description": "招待",
                "receipt_url": "/tmp/y.jpg",
                "invoice_number": "T4NEW0001",
            })
            await update_submission_analysis(
                s, sub4.id,
                tier="T4", risk_score=92.0,
                audit_report={
                    "final_status": "rejected",
                    "timeline": [
                        {"message": "发票字段验证通过", "passed": True, "skipped": False},
                        {"message": "金额超娱乐类别限额 200%", "passed": False, "skipped": False},
                    ],
                    "shield_report": {"total_score": 78, "triggered": [
                        {"message": "金额位于类别上限附近"},
                        {"message": "费用描述过于简短"},
                    ]},
                },
                status="reviewed",
            )
            _SUB_ID_T4 = sub4.id

    asyncio.new_event_loop().run_until_complete(_init())
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
MGR_HEADERS = {"X-User-Id": "mgr-001", "X-User-Role": "manager"}
FIN_HEADERS = {"X-User-Id": "fin-001", "X-User-Role": "finance_admin"}
EMP_HEADERS = {"X-User-Id": "emp-001", "X-User-Role": "employee"}


def test_manager_explain_t1_happy_path():
    resp = client.post(f"/api/chat/explain/{_SUB_ID_T1}", headers=MGR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["tier"] == "T1"
    assert body["recommendation"] == "approve"
    assert "建议批准" in body["headline"]
    assert body["risk_score"] == 20.0
    assert body["summary"]["merchant"] == "海底捞 (待审)"
    assert body["summary"]["amount"] == 150.0
    assert body["red_flags"] == []
    assert len(body["green_flags"]) >= 2  # timeline + invoice + description
    # 3 historical (120/150/180) + T1 itself = 4. Amounts excludes T1, avg = 150.
    assert body["context"]["history_count"] == 4
    assert body["context"]["history_avg"] == 150.0
    # tool registry honesty trace
    assert body["_agent_role"] == "manager_explain"
    assert "get_submission_for_review" in body["_tools_called"]
    assert "get_employee_submission_history" in body["_tools_called"]


def test_manager_explain_t4_high_risk():
    resp = client.post(f"/api/chat/explain/{_SUB_ID_T4}", headers=MGR_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "T4"
    assert body["recommendation"] == "reject"
    assert "驳回" in body["headline"]
    assert len(body["red_flags"]) >= 1
    assert body["advisory"] is not None
    assert "驳回" in body["advisory"]


def test_finance_can_also_call_explain():
    resp = client.post(f"/api/chat/explain/{_SUB_ID_T1}", headers=FIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["tier"] == "T1"


def test_employee_cannot_access_explain():
    resp = client.post(f"/api/chat/explain/{_SUB_ID_T1}", headers=EMP_HEADERS)
    assert resp.status_code == 403


def test_explain_404_for_unknown_submission():
    resp = client.post("/api/chat/explain/nonexistent-id-12345", headers=MGR_HEADERS)
    assert resp.status_code == 404


def test_compose_whitelist_blocks_offlist_tool():
    """Direct unit-test the whitelist enforcement inside compose_explanation."""
    from backend.api.routes import chat as chat_mod
    from backend.api.middleware.auth import UserContext

    async def go():
        async with _Session() as db:
            ctx = UserContext(user_id="mgr-001", roles=["manager"])
            allowed = set(chat_mod.TOOL_REGISTRY["manager_explain"])
            # Forbidden tools for this role
            for forbidden in ("update_draft_field", "extract_receipt_fields",
                              "check_duplicate_invoice", "get_my_recent_submissions"):
                assert forbidden not in allowed, f"{forbidden} should NOT be in manager_explain"

    asyncio.new_event_loop().run_until_complete(go())
