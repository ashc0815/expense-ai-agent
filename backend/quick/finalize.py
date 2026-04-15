"""Helper: convert a draft into a formal submission and enqueue the pipeline."""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext
from backend.api.routes.admin import _POLICY
from backend.api.routes.submissions import _run_pipeline
from backend.db.store import (
    create_audit_log, create_submission, get_draft, get_employee,
    get_submission_by_invoice, mark_draft_submitted,
)


async def finalize_draft_to_submission(
    draft_id: str,
    ctx: UserContext,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> str:
    """Extract draft into a submission; return the new submission id."""
    draft = await get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft 不存在")
    if draft.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")
    if draft.submitted_as:
        raise HTTPException(status_code=409, detail=f"该 draft 已提交为 {draft.submitted_as}")
    if not draft.receipt_url:
        raise HTTPException(status_code=422, detail="请先上传发票")

    fields = draft.fields or {}
    for required in ("merchant", "amount", "date", "category"):
        if not fields.get(required):
            raise HTTPException(status_code=422, detail=f"缺少必填字段：{required}")

    # 发票号去重
    inv = fields.get("invoice_number")
    if inv:
        existing = await get_submission_by_invoice(db, inv)
        if existing:
            raise HTTPException(
                status_code=422,
                detail=f"发票号 {inv} 已被报销过（单据 #{existing.id[:8]}）",
            )

    # 派生 department / cost_center / gl_account
    emp = await get_employee(db, ctx.user_id)
    department  = emp.department  if emp else None
    cost_center = emp.cost_center if emp else None
    gl_account  = (_POLICY.get("gl_mapping") or {}).get(fields.get("category"))

    sub = await create_submission(db, {
        "employee_id":    ctx.user_id,
        "status":         "processing",
        "amount":         float(fields["amount"]),
        "currency":       fields.get("currency", "CNY"),
        "category":       fields["category"],
        "date":           fields["date"],
        "merchant":       fields["merchant"],
        "tax_amount":     float(fields.get("tax_amount") or 0) or None,
        "project_code":   fields.get("project_code"),
        "description":    fields.get("description"),
        "receipt_url":    draft.receipt_url,
        "invoice_number": inv,
        "invoice_code":   fields.get("invoice_code"),
        "department":     department,
        "cost_center":    cost_center,
        "gl_account":     gl_account,
    })
    await mark_draft_submitted(db, draft_id, sub.id)
    await create_audit_log(
        db, actor_id=ctx.user_id, action="draft_submitted",
        resource_type="submission", resource_id=sub.id,
        detail={
            "draft_id": draft_id,
            "field_sources": draft.field_sources,
        },
    )
    background_tasks.add_task(_run_pipeline, sub.id, {
        "employee_id": ctx.user_id,
        "employee_name": emp.name if emp else None,
        "department": department,
        "city": emp.city if emp else None,
        "level": emp.level if emp else None,
        "amount": float(fields["amount"]),
        "currency": fields.get("currency", "CNY"),
        "category": fields["category"],
        "date": fields["date"],
        "merchant": fields["merchant"],
        "tax_amount": float(fields.get("tax_amount") or 0) or None,
        "description": fields.get("description"),
        "invoice_number": inv,
        "invoice_code": fields.get("invoice_code"),
    })
    return sub.id
