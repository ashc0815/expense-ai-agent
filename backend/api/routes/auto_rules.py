"""Auto-rules API — pattern-mined suggestions and user accept/dismiss.

Endpoints:
  GET   /api/auto-rules            List the current user's rules (all statuses)
  POST  /api/auto-rules/mine       Run the miner for the current user; returns
                                   suggested rules (writes new ones to the DB)
  POST  /api/auto-rules/{id}/accept    Move suggested → active
  POST  /api/auto-rules/{id}/dismiss   Move suggested → dismissed

Admin-only:
  GET   /api/auto-rules/admin      All rules across all employees (overview)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth, require_role
from backend.db.store import (
    AutoRule, create_audit_log, decide_auto_rule, get_auto_rule,
    get_db, list_auto_rules, list_auto_rules_all,
)
from backend.services.pattern_miner import mine_for_employee

router = APIRouter()


def _rule_dict(r: AutoRule) -> dict:
    return {
        "id": r.id,
        "employee_id": r.employee_id,
        "trigger_type": r.trigger_type,
        "trigger_value": r.trigger_value,
        "field": r.field,
        "value": r.value,
        "confidence": r.confidence,
        "evidence_count": r.evidence_count,
        "sample_ids": r.sample_ids or [],
        "status": r.status,
        "applied_count": r.applied_count or 0,
        "last_applied_at": r.last_applied_at.isoformat() if r.last_applied_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decided_by": r.decided_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("")
async def list_my_rules(
    status: Optional[str] = Query(None),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's auto-rules. Optionally filter by status."""
    rows = await list_auto_rules(
        db, employee_id=ctx.user_id, status=status,
    )
    suggested = [r for r in rows if r.status == "suggested"]
    active = [r for r in rows if r.status == "active"]
    dismissed = [r for r in rows if r.status == "dismissed"]
    return {
        "items": [_rule_dict(r) for r in rows],
        "counts": {
            "suggested": len(suggested),
            "active": len(active),
            "dismissed": len(dismissed),
            "total": len(rows),
        },
    }


@router.post("/mine")
async def run_miner(
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the miner for the current user. Returns the rules produced
    (existing suggested rules are refreshed, not duplicated)."""
    rules = await mine_for_employee(db, ctx.user_id)
    new_count = sum(1 for r in rules if r.status == "suggested")
    return {
        "items": [_rule_dict(r) for r in rules],
        "found": len(rules),
        "suggested": new_count,
    }


@router.post("/{rule_id}/accept")
async def accept_rule(
    rule_id: str,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = await get_auto_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if rule.employee_id is not None and rule.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="无权操作他人规则")
    if rule.status != "suggested":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {rule.status}，仅 suggested 可接受",
        )
    updated = await decide_auto_rule(
        db, rule_id, new_status="active", decided_by=ctx.user_id,
    )
    await create_audit_log(
        db, actor_id=ctx.user_id, action="auto_rule_accepted",
        resource_type="auto_rule", resource_id=rule_id,
        detail={
            "trigger_type": updated.trigger_type,
            "trigger_value": updated.trigger_value,
            "field": updated.field,
            "value": updated.value,
            "confidence": updated.confidence,
        },
    )
    return _rule_dict(updated)


@router.post("/{rule_id}/dismiss")
async def dismiss_rule(
    rule_id: str,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = await get_auto_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if rule.employee_id is not None and rule.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="无权操作他人规则")
    if rule.status != "suggested":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {rule.status}，仅 suggested 可拒绝",
        )
    updated = await decide_auto_rule(
        db, rule_id, new_status="dismissed", decided_by=ctx.user_id,
    )
    await create_audit_log(
        db, actor_id=ctx.user_id, action="auto_rule_dismissed",
        resource_type="auto_rule", resource_id=rule_id,
        detail={
            "trigger_value": updated.trigger_value,
            "field": updated.field,
        },
    )
    return _rule_dict(updated)


@router.get("/admin")
async def admin_list_all_rules(
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Admin view — every rule across all employees, grouped by status."""
    rows = await list_auto_rules_all(db)
    by_status: dict[str, list[dict]] = {
        "suggested": [], "active": [], "dismissed": [], "superseded": [],
    }
    for r in rows:
        by_status.setdefault(r.status, []).append(_rule_dict(r))
    return {
        "total": len(rows),
        "by_status": by_status,
    }
