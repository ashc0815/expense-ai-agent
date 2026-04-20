"""预算管理路由。

GET  /api/budget/status/{cost_center}   — 任何已登录用户
GET  /api/budget/snapshot/me            — 任何已登录用户（My Reports 主动快照）
GET  /api/budget/policies/{cost_center} — finance_admin
PUT  /api/budget/policies/{cost_center} — finance_admin
GET  /api/budget/amounts                — finance_admin
POST /api/budget/amounts                — finance_admin
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth, require_role
from backend.db import store
from backend.db.store import get_db

router = APIRouter()


# ── GET /status/{cost_center} ─────────────────────────────────────

@router.get("/status/{cost_center}")
async def get_budget_status(
    cost_center: str,
    amount: Optional[float] = Query(None, ge=0),
    period: Optional[str] = Query(None),
    _ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    dec_amount = Decimal(str(amount)) if amount is not None else None
    return await store.get_budget_status(db, cost_center, dec_amount, period)


# ── GET /snapshot/me ──────────────────────────────────────────────

@router.get("/snapshot/me")
async def get_my_budget_snapshot(
    period: Optional[str] = Query(None),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Returns a pre-formatted budget insight for the current user's cost center.
    Returns {"message": null} when signal is 'ok' so frontend stays silent."""
    from backend.db.store import Employee
    emp_r = await db.execute(select(Employee).where(Employee.id == ctx.user_id))
    emp = emp_r.scalar_one_or_none()
    if not emp or not emp.cost_center:
        return {"message": None}

    status = await store.get_budget_status(db, emp.cost_center, None, period)
    sig = status.get("signal", "ok")
    if sig == "ok" or not status.get("configured"):
        return {"message": None}

    pct = round(status["usage_pct"] * 100, 1)
    remaining = status["total_amount"] - status["spent_amount"]
    cc = emp.cost_center

    if sig == "blocked":
        msg = (
            f"⚠️ 提醒：{cc} 预算已达 {pct}%（剩余 ¥{remaining:,.0f}）。"
            f"超过 {round(status['block_threshold'] * 100)}% 阈值的报销将被暂挂，需财务管理员审批解锁。"
        )
    elif sig == "over_budget":
        msg = (
            f"🚨 {cc} 预算已超出（当前 {pct}%）。"
            + ("新报销将被暂挂等待审批。" if status["over_budget_action"] == "block"
               else "新报销可继续提交，但将标记供财务关注。")
        )
    else:  # info
        msg = f"💡 {cc} 本季度预算已用 {pct}%，剩余 ¥{remaining:,.0f}。"

    # ── trend narrative (append when overrun_risk is high) ────────────────
    trend = status.get("trend")
    if trend and trend.get("overrun_risk") == "high" and trend.get("estimated_overrun_date"):
        avg = trend["monthly_avg"]
        overrun_date_str = trend["estimated_overrun_date"]
        msg += (
            f" 按近 3 个月月均 ¥{avg:,.0f} 的消费节奏，"
            f"预计 {overrun_date_str} 前后预算耗尽。"
        )

    return {"message": msg, "signal": sig, "usage_pct": status["usage_pct"]}


# ── GET/PUT /policies/{cost_center} ──────────────────────────────

class PolicyBody(BaseModel):
    info_threshold: float = 0.75
    block_threshold: float = 0.95
    over_budget_action: str = "warn_only"


@router.get("/policies/{cost_center}")
async def get_budget_policy(
    cost_center: str,
    _ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cc = None if cost_center == "_default" else cost_center
    policy = await store.get_budget_policy(db, cc)
    if policy is None:
        raise HTTPException(404, "策略未配置")
    return {
        "cost_center": policy.cost_center,
        "info_threshold": policy.info_threshold,
        "block_threshold": policy.block_threshold,
        "over_budget_action": policy.over_budget_action,
    }


@router.put("/policies/{cost_center}", status_code=200)
async def update_budget_policy(
    cost_center: str,
    body: PolicyBody,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if body.block_threshold <= body.info_threshold:
        raise HTTPException(400, "block_threshold 必须大于 info_threshold")
    if body.over_budget_action not in ("warn_only", "block"):
        raise HTTPException(400, "over_budget_action 只能是 'warn_only' 或 'block'")
    cc = None if cost_center == "_default" else cost_center
    policy = await store.upsert_budget_policy(
        db, cc, body.info_threshold, body.block_threshold,
        body.over_budget_action, ctx.user_id,
    )
    return {
        "cost_center": policy.cost_center,
        "info_threshold": policy.info_threshold,
        "block_threshold": policy.block_threshold,
        "over_budget_action": policy.over_budget_action,
    }


# ── GET/POST /amounts ─────────────────────────────────────────────

class AmountBody(BaseModel):
    cost_center: str
    period: str
    total_amount: float


@router.get("/amounts")
async def list_budget_amounts(
    period: Optional[str] = Query(None),
    _ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    budgets = await store.list_cost_center_budgets(db, period)
    return [
        {
            "cost_center": b.cost_center,
            "period": b.period,
            "total_amount": float(b.total_amount),
        }
        for b in budgets
    ]


@router.post("/amounts", status_code=201)
async def upsert_budget_amount(
    body: AmountBody,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if body.total_amount <= 0:
        raise HTTPException(400, "total_amount 必须为正数")
    budget = await store.upsert_cost_center_budget(
        db, body.cost_center, body.period,
        Decimal(str(body.total_amount)), ctx.user_id,
    )
    return {
        "cost_center": budget.cost_center,
        "period": budget.period,
        "total_amount": float(budget.total_amount),
    }
