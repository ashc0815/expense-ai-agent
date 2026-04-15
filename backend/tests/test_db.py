"""Task 4 — 数据库层测试（SQLite in-memory）。"""
from __future__ import annotations

import os
import pytest
import pytest_asyncio

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from backend.db.store import (
    Base, create_submission, get_submission, list_submissions,
    update_submission_status, update_submission_analysis,
    create_audit_log, list_audit_logs,
    create_draft, get_draft,
)

# ── 测试用 in-memory 引擎 ─────────────────────────────────────────

TEST_ENGINE = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSession = async_sessionmaker(TEST_ENGINE, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="function")
async def db():
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        yield session
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _sample_data(**kwargs):
    base = dict(
        employee_id="emp-001",
        amount=480.0,
        currency="CNY",
        category="meal",
        date="2026-04-11",
        merchant="海底捞",
        receipt_url="uploads/2026-04/abc_receipt.jpg",
    )
    base.update(kwargs)
    return base


# ── 测试 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_submission(db: AsyncSession):
    sub = await create_submission(db, _sample_data())
    assert sub.id is not None
    fetched = await get_submission(db, sub.id)
    assert fetched is not None
    assert fetched.employee_id == "emp-001"
    assert float(fetched.amount) == 480.0


@pytest.mark.asyncio
async def test_list_submissions_pagination(db: AsyncSession):
    for i in range(5):
        await create_submission(db, _sample_data(employee_id=f"emp-{i:03d}"))
    result = await list_submissions(db, page=1, page_size=2)
    assert len(result["items"]) == 2
    assert result["total"] == 5
    assert result["has_next"] is True


@pytest.mark.asyncio
async def test_list_submissions_status_filter(db: AsyncSession):
    await create_submission(db, _sample_data(status="submitted"))
    await create_submission(db, _sample_data(status="reviewed"))
    result = await list_submissions(db, status="reviewed")
    assert result["total"] == 1
    assert result["items"][0].status == "reviewed"


@pytest.mark.asyncio
async def test_update_submission_status(db: AsyncSession):
    sub = await create_submission(db, _sample_data())
    updated = await update_submission_status(db, sub.id, "manager_approved",
                                             approver_id="mgr-001",
                                             approver_comment="LGTM")
    assert updated.status == "manager_approved"
    assert updated.approver_id == "mgr-001"


@pytest.mark.asyncio
async def test_update_submission_analysis(db: AsyncSession):
    sub = await create_submission(db, _sample_data(status="processing"))
    updated = await update_submission_analysis(
        db, sub.id,
        ocr_data={"merchant": "海底捞", "amount": 480.0},
        audit_report={"passed": True},
        risk_score=12.5,
        tier="T1",
    )
    assert updated.status == "reviewed"
    assert updated.tier == "T1"
    assert float(updated.risk_score) == 12.5
    assert updated.ocr_data["merchant"] == "海底捞"


@pytest.mark.asyncio
async def test_create_and_list_audit_log(db: AsyncSession):
    await create_audit_log(db, actor_id="emp-001", action="submission_created",
                           resource_type="submission", resource_id="sub-001",
                           detail={"amount": 480.0})
    result = await list_audit_logs(db)
    assert result["total"] == 1
    assert result["items"][0].action == "submission_created"


@pytest.mark.asyncio
async def test_draft_has_layer_and_entry_columns(db: AsyncSession):
    draft = await create_draft(db, employee_id="emp_001")
    assert draft.layer is None
    assert draft.entry is None

    draft.layer = "1"
    draft.entry = "quick"
    await db.commit()
    await db.refresh(draft)

    reloaded = await get_draft(db, draft.id)
    assert reloaded.layer == "1"
    assert reloaded.entry == "quick"
