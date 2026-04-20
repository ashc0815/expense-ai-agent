"""财务 API — CSV 导出。

挂载在 /api/finance 前缀下：
  GET  /export/preview             待导出列表（finance_approved && exported_at IS NULL）
  POST /export                     批量导出 CSV（标记 exported）

注意：财务审批/拒绝已迁移至 reports.py（以报销单为单位操作）。
"""
from __future__ import annotations

import csv
import io
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_role
from backend.api.routes.submissions import _sub_dict
from backend.db.store import (
    create_audit_log, get_db, get_submission, list_submissions,
    mark_submissions_exported,
)

router = APIRouter()


class ExportBody(BaseModel):
    ids: List[str]


# ── GET /export/preview ───────────────────────────────────────────

@router.get("/export/preview")
async def export_preview(
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    """列出 finance_approved 但未导出的所有单。"""
    result = await list_submissions(
        db, status="finance_approved", exported=False,
        page=1, page_size=500,
    )
    result["items"] = [_sub_dict(s) for s in result["items"]]
    return result


# ── POST /export ──────────────────────────────────────────────────

# CSV 列定义 — 通用 ERP 入账格式（用户拿到后自行映射到金蝶/用友/SAP）
_CSV_COLUMNS = [
    ("voucher_number",     "凭证号"),
    ("date",               "业务日期"),
    ("employee_id",        "员工工号"),
    ("department",         "部门"),
    ("cost_center",        "成本中心"),
    ("gl_account",         "会计科目"),
    ("project_code",       "项目编号"),
    ("category",           "费用类别"),
    ("merchant",           "商户"),
    ("currency",           "币种"),
    ("amount",             "金额"),
    ("tax_amount",         "税额"),
    ("invoice_code",       "发票代码"),
    ("invoice_number",     "发票号码"),
    ("seller_tax_id",      "销方税号"),
    ("description",        "摘要"),
    ("approver_id",        "经理审批人"),
    ("approved_at",        "经理审批时间"),
    ("finance_approver_id", "财务审批人"),
    ("finance_approved_at", "财务审批时间"),
]


@router.post("/export")
async def export_csv(
    body: ExportBody,
    ctx: UserContext = Depends(require_role("finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    if not body.ids:
        raise HTTPException(status_code=400, detail="未指定任何报销单")

    # 校验：所有 ID 必须存在且 status=finance_approved 且未导出
    rows = []
    for sid in body.ids:
        sub = await get_submission(db, sid)
        if not sub:
            raise HTTPException(status_code=404, detail=f"报销单 {sid} 不存在")
        if sub.status != "finance_approved" or sub.exported_at is not None:
            raise HTTPException(
                status_code=409,
                detail=f"报销单 {sid} 状态 '{sub.status}' 不可导出",
            )
        rows.append(sub)

    # 生成 CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label in _CSV_COLUMNS])
    for sub in rows:
        d = _sub_dict(sub)
        writer.writerow([
            d.get(key) if d.get(key) is not None else ""
            for key, _ in _CSV_COLUMNS
        ])

    # 标记为已导出
    batch_id = str(uuid.uuid4())
    await mark_submissions_exported(db, body.ids, batch_id)
    await create_audit_log(
        db, actor_id=ctx.user_id, action="finance_exported",
        resource_type="submission", resource_id=None,
        detail={"batch_id": batch_id, "count": len(rows), "ids": body.ids},
    )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=erp_export_{batch_id[:8]}.csv",
            "X-Batch-Id": batch_id,
        },
    )
