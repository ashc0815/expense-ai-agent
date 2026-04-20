"""审批 API — 经理审批 / 拒绝报销单。

路由挂载在 /api/submissions 前缀下：
  POST /{id}/approve         经理通过 → manager_approved
  POST /{id}/reject          经理拒绝 → rejected
  POST /bulk-approve         批量通过

经理只看 status in (reviewed, review_failed)；通过后转给财务。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_role
from backend.api.routes.submissions import _sub_dict
from backend.db.store import (
    append_audit_step, create_audit_log, get_submission, update_submission_status, get_db,
)

router = APIRouter()

# 经理可处理的状态（包括审核失败的，便于人工兜底）
_MANAGER_ACTIONABLE = ("processing", "reviewed", "review_failed")


class ApproveBody(BaseModel):
    comment: Optional[str] = None


class RejectBody(BaseModel):
    comment: Optional[str] = None


class BulkApproveBody(BaseModel):
    ids: List[str]
    comment: Optional[str] = None


# ── POST /{id}/approve ────────────────────────────────────────────

@router.post("/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    body: ApproveBody = ApproveBody(),
    ctx: UserContext = Depends(require_role("manager", "finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    sub = await get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if sub.status not in _MANAGER_ACTIONABLE:
        raise HTTPException(
            status_code=409,
            detail=f"报销单当前状态 '{sub.status}' 不可审批",
        )
    updated = await update_submission_status(
        db, submission_id, "manager_approved",
        approver_id=ctx.user_id,
        approver_comment=body.comment,
    )
    # Option B: 经理批准触发"凭证生成"timeline 步骤
    updated = await append_audit_step(
        db, submission_id,
        message=f"凭证已生成（经理 {ctx.user_id} 批准）",
        phase="manager_approved",
    ) or updated
    await create_audit_log(
        db, actor_id=ctx.user_id, action="manager_approved",
        resource_type="submission", resource_id=submission_id,
        detail={"comment": body.comment},
    )
    return _sub_dict(updated)


# ── POST /{id}/reject ─────────────────────────────────────────────

@router.post("/{submission_id}/reject")
async def reject_submission(
    submission_id: str,
    body: RejectBody = RejectBody(),
    ctx: UserContext = Depends(require_role("manager", "finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    sub = await get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if sub.status not in _MANAGER_ACTIONABLE:
        raise HTTPException(
            status_code=409,
            detail=f"报销单当前状态 '{sub.status}' 不可拒绝",
        )
    updated = await update_submission_status(
        db, submission_id, "rejected",
        approver_id=ctx.user_id,
        approver_comment=body.comment,
    )
    await create_audit_log(
        db, actor_id=ctx.user_id, action="manager_rejected",
        resource_type="submission", resource_id=submission_id,
        detail={"comment": body.comment},
    )
    return _sub_dict(updated)


# ── POST /bulk-approve ────────────────────────────────────────────

@router.post("/bulk-approve")
async def bulk_approve(
    body: BulkApproveBody,
    ctx: UserContext = Depends(require_role("manager", "finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    results = {"approved": [], "skipped": [], "not_found": []}
    for sid in body.ids:
        sub = await get_submission(db, sid)
        if not sub:
            results["not_found"].append(sid)
            continue
        if sub.status not in _MANAGER_ACTIONABLE:
            results["skipped"].append({"id": sid, "status": sub.status})
            continue
        await update_submission_status(
            db, sid, "manager_approved",
            approver_id=ctx.user_id,
            approver_comment=body.comment,
        )
        await append_audit_step(
            db, sid,
            message=f"凭证已生成（经理 {ctx.user_id} 批量批准）",
            phase="manager_approved",
        )
        await create_audit_log(
            db, actor_id=ctx.user_id, action="manager_approved",
            resource_type="submission", resource_id=sid,
            detail={"bulk": True, "comment": body.comment},
        )
        results["approved"].append(sid)
    return results
