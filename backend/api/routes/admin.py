"""管理员 API — 策略管理、审计日志、数据导出、统计。

挂载在 /api/admin 前缀下：
  GET  /policy          — 读取当前报销策略
  PUT  /policy          — 更新报销策略
  GET  /audit-log       — 查询审计日志（分页）
  GET  /export          — 导出提交记录为 CSV
  GET  /stats           — 汇总统计
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_role
from backend.db.store import (
    AuditLog, Submission, get_db, list_audit_logs,
)

router = APIRouter()

# ── 内存策略存储（生产可换 DB/Redis）────────────────────────────
_POLICY: dict = {
    "category_limits": {
        "meal": 200.0,
        "transport": 500.0,
        "accommodation": 800.0,
    },
    "max_amount_cny": 5000.0,
    "allowed_categories": ["meal", "transport", "accommodation", "entertainment", "other"],
    "require_receipt_above_cny": 100.0,
    "auto_approve_below_cny": 50.0,
    "gl_mapping": {
        "meal":          "6602.02",
        "transport":     "6601.03",
        "accommodation": "6601.04",
        "entertainment": "6602.01",
        "other":         "6603.99",
    },
    "projects": [
        {"code": "P-2026-A1", "name": "AI 防欺诈平台"},
        {"code": "P-2026-A2", "name": "ERP 集成升级"},
    ],
}


class PolicyUpdate(BaseModel):
    category_limits: Optional[dict] = None
    max_amount_cny: Optional[float] = None
    require_receipt_above_cny: Optional[float] = None
    auto_approve_below_cny: Optional[float] = None
    allowed_categories: Optional[list] = None
    gl_mapping: Optional[dict] = None
    projects: Optional[list] = None


# ── GET /policy ───────────────────────────────────────────────────

@router.get("/policy")
async def get_policy(
    ctx: UserContext = Depends(require_role("finance_admin")),
):
    return _POLICY


@router.get("/projects")
async def list_projects():
    """公共项目列表 — 任何角色都可读，员工提交报销时下拉选用。"""
    return {"items": _POLICY.get("projects", [])}


# ── PUT /policy ───────────────────────────────────────────────────

@router.put("/policy")
async def update_policy(
    body: PolicyUpdate,
    ctx: UserContext = Depends(require_role("finance_admin")),
):
    updates = body.model_dump(exclude_none=True)
    _POLICY.update(updates)
    return _POLICY


# ── GET /audit-log ────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    actor_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await list_audit_logs(
        db, actor_id=actor_id, action=action, page=page, page_size=page_size,
    )
    result["items"] = [
        {
            "id": log.id,
            "actor_id": log.actor_id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in result["items"]
    ]
    return result


# ── GET /export ───────────────────────────────────────────────────

@router.get("/export")
async def export_submissions(
    status: Optional[str] = Query(None),
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    q = select(Submission).order_by(Submission.created_at.desc())
    if status:
        q = q.where(Submission.status == status)
    rows = (await db.execute(q)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "employee_id", "status", "amount", "currency",
        "category", "date", "merchant", "tier", "risk_score",
        "approver_id", "created_at",
    ])
    for sub in rows:
        writer.writerow([
            sub.id, sub.employee_id, sub.status, float(sub.amount),
            sub.currency, sub.category, sub.date, sub.merchant,
            sub.tier or "", float(sub.risk_score) if sub.risk_score else "",
            sub.approver_id or "",
            sub.created_at.isoformat() if sub.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=submissions.csv"},
    )


# ── GET /stats ────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(Submission))).scalar_one()

    status_rows = (
        await db.execute(
            select(Submission.status, func.count().label("cnt"))
            .group_by(Submission.status)
        )
    ).all()
    by_status = {row.status: row.cnt for row in status_rows}

    tier_rows = (
        await db.execute(
            select(Submission.tier, func.count().label("cnt"))
            .where(Submission.tier.is_not(None))
            .group_by(Submission.tier)
        )
    ).all()
    by_tier = {row.tier: row.cnt for row in tier_rows}

    avg_risk = (
        await db.execute(
            select(func.avg(Submission.risk_score))
            .where(Submission.risk_score.is_not(None))
        )
    ).scalar_one()

    total_amount = (
        await db.execute(select(func.sum(Submission.amount)))
    ).scalar_one()

    return {
        "total_submissions": total,
        "by_status": by_status,
        "by_tier": by_tier,
        "avg_risk_score": float(avg_risk) if avg_risk else None,
        "total_amount_cny": float(total_amount) if total_amount else 0.0,
    }
