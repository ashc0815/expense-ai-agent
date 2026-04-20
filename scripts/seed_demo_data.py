"""Demo 数据植入脚本。

一键在本地 SQLite 数据库里创建 5 张状态各异的报销单，覆盖完整审批流程，
供演示/面试时直接展示。无需启动 FastAPI 服务。

用法：
    source venv/bin/activate
    python scripts/seed_demo_data.py           # 植入演示数据
    python scripts/seed_demo_data.py --reset   # 清空 DB 后重新植入

运行后直接访问：
    http://localhost:8000/manager/queue.html?as=manager
    http://localhost:8000/finance/review.html?as=finance_admin
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ── 把项目根加入 sys.path（脚本从任意目录运行都能找到 backend 模块）──────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", str(_ROOT / "uploads"))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import backend.config as _cfg
from backend.db.store import (
    Base, create_submission, upsert_employee,
    update_submission_analysis, update_submission_status,
    update_submission_finance, append_audit_step, next_voucher_number,
)


# ── 颜色输出（终端） ──────────────────────────────────────────────────────────
def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"

OK   = _c("✓", "32")
INFO = _c("→", "36")
WARN = _c("!", "33")


async def seed(reset: bool = False) -> None:
    db_url = _cfg.DATABASE_URL
    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    if reset:
        print(f"{WARN} 清空数据库…")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    print(f"{INFO} 初始化表结构…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    today = date.today()
    last_month = today - timedelta(days=30)
    two_weeks_ago = today - timedelta(days=14)

    async with Session() as db:
        # ── 植入员工档案 ────────────────────────────────────────────────────
        print(f"\n{INFO} 植入员工档案…")
        employees = [
            {"id": "emp-demo-01", "name": "张伟",  "department": "销售部", "level": "L3",
             "city": "上海", "cost_center": "CC-SALES", "bank_account": "6222021234560001"},
            {"id": "emp-demo-02", "name": "李娜",  "department": "技术部", "level": "L4",
             "city": "北京", "cost_center": "CC-TECH",  "bank_account": "6222021234560002"},
            {"id": "emp-demo-03", "name": "王芳",  "department": "市场部", "level": "L5",
             "city": "广州", "cost_center": "CC-MKT",   "bank_account": "6222021234560003"},
        ]
        for emp in employees:
            await upsert_employee(db, emp)
            print(f"  {OK} 员工 {emp['name']}（{emp['id']}）")

        # ── 单据 1：T1 低风险，等待经理审批 ────────────────────────────────
        print(f"\n{INFO} 单据 1 — T1 低风险（status=reviewed，等待经理审批）…")
        sub1 = await create_submission(db, {
            "employee_id": "emp-demo-01",
            "status": "processing",
            "amount": 148.00, "currency": "CNY",
            "category": "meal", "date": today.isoformat(),
            "merchant": "海底捞火锅（南京西路店）",
            "description": "团队午餐讨论 Q2 销售策略及目标拆解",
            "receipt_url": "/uploads/demo/receipt_01.jpg",
            "invoice_number": "20260001",
            "invoice_code": "310012135012",
            "department": "销售部", "cost_center": "CC-SALES",
            "gl_account": "6602-01",
        })
        await update_submission_analysis(db, sub1.id,
            tier="T1", risk_score=18.0,
            audit_report={
                "final_status": "completed",
                "timeline": [
                    {"message": "发票字段验证通过（发票号 20260001，代码 310012135012）",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "金额 ¥148 未超餐饮类别限额 ¥200",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "合规检查通过（描述具体，未触发模糊性检测）",
                     "passed": True, "skipped": False, "phase": "submit"},
                ],
                "shield_report": {"total_score": 6, "triggered": []},
            },
            status="reviewed",
        )
        print(f"  {OK} 单据 ID: {sub1.id[:8]}…  tier=T1  risk=18  status=reviewed")

        # ── 单据 2：T3 中风险，等待经理审批（需人工核对）──────────────────
        print(f"\n{INFO} 单据 2 — T3 中风险（status=reviewed，advisory=需人工核对）…")
        sub2 = await create_submission(db, {
            "employee_id": "emp-demo-02",
            "status": "processing",
            "amount": 680.00, "currency": "CNY",
            "category": "entertainment", "date": today.isoformat(),
            "merchant": "福楼法餐厅（三里屯）",
            "description": "客户招待晚餐",
            "receipt_url": "/uploads/demo/receipt_02.jpg",
            "invoice_number": "20260002",
            "invoice_code": "110000230202",
            "department": "技术部", "cost_center": "CC-TECH",
            "gl_account": "6602-04",
        })
        await update_submission_analysis(db, sub2.id,
            tier="T3", risk_score=66.0,
            audit_report={
                "final_status": "pending_review",
                "timeline": [
                    {"message": "发票字段验证通过",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "金额 ¥680 接近娱乐类别限额上限（¥700）",
                     "passed": False, "skipped": False, "phase": "submit"},
                    {"message": "合规检查：描述'客户招待晚餐'较为简短，建议补充客户名称",
                     "passed": False, "skipped": False, "phase": "submit"},
                ],
                "shield_report": {
                    "total_score": 48,
                    "triggered": [
                        {"message": "金额位于类别上限 90-110% 区间"},
                        {"message": "费用描述缺少具体对象信息"},
                    ],
                },
            },
            status="reviewed",
        )
        print(f"  {OK} 单据 ID: {sub2.id[:8]}…  tier=T3  risk=66  status=reviewed")

        # ── 单据 3：T1，经理已批准，等待财务审批 ───────────────────────────
        print(f"\n{INFO} 单据 3 — T1（status=manager_approved，等待财务审批）…")
        sub3 = await create_submission(db, {
            "employee_id": "emp-demo-01",
            "status": "processing",
            "amount": 260.00, "currency": "CNY",
            "category": "transport", "date": two_weeks_ago.isoformat(),
            "merchant": "滴滴出行",
            "description": "出差拜访客户往返交通费（上海→苏州）",
            "receipt_url": "/uploads/demo/receipt_03.jpg",
            "invoice_number": "20260003",
            "invoice_code": "310012135012",
            "department": "销售部", "cost_center": "CC-SALES",
            "gl_account": "6602-02",
        })
        await update_submission_analysis(db, sub3.id,
            tier="T1", risk_score=15.0,
            audit_report={
                "final_status": "completed",
                "timeline": [
                    {"message": "发票字段验证通过",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "金额 ¥260 未超交通类别限额",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "合规检查通过（描述包含出发地、目的地、事由）",
                     "passed": True, "skipped": False, "phase": "submit"},
                ],
                "shield_report": {"total_score": 4, "triggered": []},
            },
            status="reviewed",
        )
        # 经理批准 → 追加 timeline[3]
        await update_submission_status(db, sub3.id, "manager_approved",
            approver_id="mgr-demo", approver_comment="出差合规，批准")
        await append_audit_step(db, sub3.id,
            message="凭证已生成（经理 mgr-demo 批准）",
            phase="manager_approved")
        print(f"  {OK} 单据 ID: {sub3.id[:8]}…  tier=T1  status=manager_approved")

        # ── 单据 4：财务已批准（完整闭环，有凭证号）──────────────────────
        print(f"\n{INFO} 单据 4 — 财务已批准（status=finance_approved，有凭证号）…")
        sub4 = await create_submission(db, {
            "employee_id": "emp-demo-03",
            "status": "processing",
            "amount": 980.00, "currency": "CNY",
            "category": "accommodation", "date": last_month.isoformat(),
            "merchant": "上海希尔顿酒店",
            "description": "出差住宿 2 晚（参加 AI 峰会）",
            "receipt_url": "/uploads/demo/receipt_04.jpg",
            "invoice_number": "20260004",
            "invoice_code": "310012135012",
            "department": "市场部", "cost_center": "CC-MKT",
            "gl_account": "6602-03",
        })
        await update_submission_analysis(db, sub4.id,
            tier="T2", risk_score=38.0,
            audit_report={
                "final_status": "completed",
                "timeline": [
                    {"message": "发票字段验证通过",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "金额 ¥980 在住宿限额范围内",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "合规检查通过",
                     "passed": True, "skipped": False, "phase": "submit"},
                ],
                "shield_report": {"total_score": 12, "triggered": []},
            },
            status="reviewed",
        )
        await update_submission_status(db, sub4.id, "manager_approved",
            approver_id="mgr-demo", approver_comment="出差合理，批准")
        await append_audit_step(db, sub4.id,
            message="凭证已生成（经理 mgr-demo 批准）",
            phase="manager_approved")
        voucher = await next_voucher_number(db)
        await update_submission_finance(db, sub4.id,
            status="finance_approved",
            finance_approver_id="fin-demo",
            finance_approver_comment="核对发票，批准",
            voucher_number=voucher,
        )
        await append_audit_step(db, sub4.id,
            message=f"付款已执行（凭证 {voucher}，财务 fin-demo）",
            phase="finance_approved",
            extra={"voucher_number": voucher},
        )
        print(f"  {OK} 单据 ID: {sub4.id[:8]}…  tier=T2  status=finance_approved  凭证={voucher}")

        # ── 单据 5：T4 高风险，已驳回 ──────────────────────────────────────
        print(f"\n{INFO} 单据 5 — T4 高风险（status=rejected）…")
        sub5 = await create_submission(db, {
            "employee_id": "emp-demo-02",
            "status": "processing",
            "amount": 4800.00, "currency": "CNY",
            "category": "entertainment", "date": two_weeks_ago.isoformat(),
            "merchant": "某某 KTV 俱乐部",
            "description": "招待",
            "receipt_url": "/uploads/demo/receipt_05.jpg",
            "invoice_number": "20260005",
            "invoice_code": "110000230202",
            "department": "技术部", "cost_center": "CC-TECH",
            "gl_account": "6602-04",
        })
        await update_submission_analysis(db, sub5.id,
            tier="T4", risk_score=92.0,
            audit_report={
                "final_status": "rejected",
                "timeline": [
                    {"message": "发票字段验证通过",
                     "passed": True, "skipped": False, "phase": "submit"},
                    {"message": "金额 ¥4800 超娱乐类别限额 ¥700 约 586%",
                     "passed": False, "skipped": False, "phase": "submit"},
                    {"message": "合规检查：描述仅'招待'，信息严重不足",
                     "passed": False, "skipped": False, "phase": "submit"},
                ],
                "shield_report": {
                    "total_score": 88,
                    "triggered": [
                        {"message": "金额远超类别上限（>500%）"},
                        {"message": "费用描述过于简短（≤3 字）"},
                        {"message": "KTV 类商户风险较高"},
                    ],
                },
            },
            status="reviewed",
        )
        await update_submission_status(db, sub5.id, "rejected",
            approver_id="mgr-demo",
            approver_comment="金额严重超限，描述不合规，驳回。请提供完整发票和详细说明后重新提交。")
        print(f"  {OK} 单据 ID: {sub5.id[:8]}…  tier=T4  risk=92  status=rejected")

    await engine.dispose()

    print(f"""
{'─'*55}
{_c('演示数据植入完成！', '32;1')}

  5 张单据状态：
  • 单据 1  (张伟)   T1 低风险  → 等待经理审批
  • 单据 2  (李娜)   T3 中风险  → 等待经理审批（需核对）
  • 单据 3  (张伟)   T1 低风险  → 经理已批，等财务
  • 单据 4  (王芳)   T2 次低风险 → 财务已批，有凭证号
  • 单据 5  (李娜)   T4 高风险  → 已驳回

  现在可以访问：
  经理审批  →  http://localhost:8000/manager/queue.html?as=manager
  财务审批  →  http://localhost:8000/finance/review.html?as=finance_admin
  员工视角  →  http://localhost:8000/employee/my-reports.html?as=employee

  开发者模式（看 AI 内部 trace）：在 URL 后加 &dev=1
{'─'*55}""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="植入 ExpenseFlow 演示数据")
    parser.add_argument("--reset", action="store_true", help="清空数据库后重新植入")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))
