"""数据库层 — SQLAlchemy async，SQLite（开发）/ PostgreSQL（生产）。

DATABASE_URL 未设置时默认 sqlite+aiosqlite:///./concurshield.db

状态机（submissions.status）：
  processing → reviewed → manager_approved → finance_approved → exported
                                ↓                  ↓
                            rejected           rejected
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import calendar
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, Integer, Numeric, String, Text,
    UniqueConstraint, or_, select, func,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.config import DATABASE_URL, EVAL_DATABASE_URL

# ── 引擎 & Session ────────────────────────────────────────────────
#
# 两套引擎：主业务库（concurshield.db）+ Eval 库（concurshield_eval.db）。
# Eval 相关表（llm_traces / eval_runs）物理隔离，避免 Eval 数据量增长
# 影响主库性能，也便于独立备份 / 清理。
# Prompt / config 文件仍然共享（eval_prompts.json、eval_config.json），
# 这样 Dashboard 上改的 prompt 能即时作用于主系统的 Agent。

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

eval_engine = create_async_engine(EVAL_DATABASE_URL, echo=False)
EvalAsyncSessionLocal = async_sessionmaker(eval_engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """FastAPI 依赖：yield 一个主库 session，请求结束后自动关闭。"""
    async with AsyncSessionLocal() as session:
        yield session


async def get_eval_db() -> AsyncSession:
    """FastAPI 依赖：yield 一个 Eval 库 session。"""
    async with EvalAsyncSessionLocal() as session:
        yield session


# ── ORM 模型 ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """主业务库的 Declarative Base（submissions / reports / drafts / ...）。"""
    pass


class EvalBase(DeclarativeBase):
    """Eval 库的 Declarative Base（llm_traces / eval_runs）。"""
    pass


class Employee(Base):
    """员工档案 — 入账编码必需的部门 / 成本中心 / 银行账号都从这里 join。"""
    __tablename__ = "employees"

    id            = Column(String(64), primary_key=True)        # 员工工号
    name          = Column(String(255), nullable=False)
    email         = Column(String(255), nullable=True)
    department    = Column(String(100), nullable=False, default="未分配")
    cost_center   = Column(String(50),  nullable=False, default="CC-00")
    manager_id    = Column(String(64),  nullable=True)          # 直属经理工号
    bank_account  = Column(String(50),  nullable=True)
    level         = Column(String(10),  nullable=True)          # L1-L7
    hire_date     = Column(Date,        nullable=True)
    resignation_date = Column(Date,    nullable=True)
    city          = Column(String(50),  nullable=True, default="上海")
    home_currency = Column(String(3), nullable=False, default="CNY")
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))


class Submission(Base):
    __tablename__ = "submissions"

    id               = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id      = Column(String(64),  nullable=False)
    status           = Column(String(50),  nullable=False, default="processing")
    # processing | reviewed | manager_approved | finance_approved | exported | rejected | review_failed | needs_revision

    # ── 用户填写 / OCR 提取 ──────────────────────────────────────
    amount           = Column(Numeric(12, 2), nullable=False)
    currency         = Column(String(3),   nullable=False, default="CNY")
    category         = Column(String(50),  nullable=False)
    date             = Column(String(10),  nullable=False)        # ISO date string
    merchant         = Column(String(255), nullable=False)
    tax_rate         = Column(Numeric(5, 4), nullable=True)
    tax_amount       = Column(Numeric(12, 2), nullable=True)
    project_code     = Column(String(50),  nullable=True)
    description      = Column(Text,        nullable=True)
    receipt_url      = Column(String(500), nullable=False)

    # ── 发票字段 (ERP 入账必需) ──────────────────────────────────
    invoice_number   = Column(String(50),  nullable=True)               # 发票号码 — soft dedup only for MVP
    invoice_code     = Column(String(50),  nullable=True)               # 发票代码
    seller_tax_id    = Column(String(50),  nullable=True)               # 销方税号
    buyer_tax_id     = Column(String(50),  nullable=True)               # 购方税号

    # ── 系统派生字段 (员工档案 + 映射) ───────────────────────────
    department       = Column(String(100), nullable=True)               # 来自 employees.department
    cost_center      = Column(String(50),  nullable=True)               # 来自 employees.cost_center
    gl_account       = Column(String(50),  nullable=True)               # 来自 policy.gl_mapping[category]

    # ── AI 审核回填 ──────────────────────────────────────────────
    ocr_data         = Column(JSON,        nullable=True)
    audit_report     = Column(JSON,        nullable=True)
    risk_score       = Column(Numeric(5, 2), nullable=True)
    tier             = Column(String(5),   nullable=True)                # T1-T4

    # ── 经理审批 ────────────────────────────────────────────────
    approver_id        = Column(String(64),  nullable=True)
    approver_comment   = Column(Text,        nullable=True)
    approved_at        = Column(DateTime(timezone=True), nullable=True)

    # ── 财务审批 ────────────────────────────────────────────────
    finance_approver_id      = Column(String(64),  nullable=True)
    finance_approver_comment = Column(Text,        nullable=True)
    finance_approved_at      = Column(DateTime(timezone=True), nullable=True)

    # ── 凭证 / 入账 ─────────────────────────────────────────────
    voucher_number   = Column(String(50),  nullable=True, unique=True)  # YYYYMM-NNNN
    exported_at      = Column(DateTime(timezone=True), nullable=True)
    export_batch_id  = Column(String(36),  nullable=True)

    # ── 预算拦截 ────────────────────────────────────────────────
    budget_blocked       = Column(Boolean, nullable=False, default=False)
    budget_unblocked_by  = Column(String(64), nullable=True)
    budget_unblocked_at  = Column(DateTime(timezone=True), nullable=True)

    # ── 报销单关联 (多笔打包成一张报销单) ─────────────────────────
    report_id        = Column(String(36),  nullable=True, index=True)

    # ── 汇率 ────────────────────────────────────────────────────────
    exchange_rate    = Column(Numeric(10, 6), nullable=True)   # 1 invoice_currency = X home_currency
    converted_amount = Column(Numeric(12, 2), nullable=True)   # amount * exchange_rate

    # ── 时间戳 ──────────────────────────────────────────────────
    created_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at       = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))


class Draft(Base):
    """员工 + Agent 协作的草稿区。

    Agent 和员工都可以写，字段来源记在 field_sources 里（provenance）。
    草稿 → 点击"提交"时才转成正式 submission 走审批流程。
    """
    __tablename__ = "drafts"

    id             = Column(String(36),  primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id    = Column(String(64),  nullable=False)
    receipt_url    = Column(String(500), nullable=True)
    fields         = Column(JSON,        nullable=False, default=dict)
    # 例：{"merchant": "海底捞", "amount": 480.0, "date": "2026-04-12", ...}
    field_sources  = Column(JSON,        nullable=False, default=dict)
    # 例：{"merchant": "ocr", "amount": "ocr", "category": "agent_suggested",
    #      "date": "user_typed"}  — 每个字段的来源，合规审计用
    chat_history   = Column(JSON,        nullable=False, default=list)
    # 完整对话历史（含 tool_use / tool_result），用于 agent 继续对话
    submitted_as   = Column(String(36),  nullable=True)
    # 转正后的 submission id（null 表示未提交）
    layer          = Column(String(16),  nullable=True, default=None)
    entry          = Column(String(16),  nullable=True, default=None)
    report_id      = Column(String(36),  nullable=True, index=True)
    created_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at     = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id      = Column(String(64),  nullable=False)
    action        = Column(String(100), nullable=False)
    resource_type = Column(String(50),  nullable=False)
    resource_id   = Column(String(36),  nullable=True)
    detail        = Column(JSON,        nullable=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CostCenterBudget(Base):
    """每个成本中心按期间设置的预算总额。"""
    __tablename__ = "cost_center_budgets"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cost_center  = Column(String(64), nullable=False, index=True)
    period       = Column(String(16), nullable=False)   # "2026-Q2" or "2026"
    total_amount = Column(Numeric(14, 2), nullable=False)
    created_by   = Column(String(64), nullable=True)
    updated_at   = Column(DateTime(timezone=True),
                          default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("cost_center", "period", name="uq_cc_budget_period"),)


class BudgetPolicy(Base):
    """每个成本中心（或全局默认）的阈值和超限行为配置。cost_center IS NULL 表示全局默认。"""
    __tablename__ = "budget_policies"

    id                 = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cost_center        = Column(String(64), nullable=True, unique=True)   # NULL = global default
    info_threshold     = Column(Float, nullable=False, default=0.75)
    block_threshold    = Column(Float, nullable=False, default=0.95)
    over_budget_action = Column(String(16), nullable=False, default="warn_only")  # "warn_only" | "block"
    updated_by         = Column(String(64), nullable=True)
    updated_at         = Column(DateTime(timezone=True),
                                default=lambda: datetime.now(timezone.utc),
                                onupdate=lambda: datetime.now(timezone.utc))


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id                  = Column(String(36), primary_key=True,
                                 default=lambda: str(uuid.uuid4()))
    draft_id            = Column(String(36), nullable=False, index=True)
    entry               = Column(String(16), nullable=False)
    final_layer         = Column(String(16), nullable=False)
    ocr_confidence_min  = Column(Numeric(4, 3), nullable=True)
    classify_confidence = Column(Numeric(4, 3), nullable=True)
    fields_edited_count = Column(Integer, nullable=False, default=0)
    time_to_attest_ms   = Column(Integer, nullable=True)
    attest_or_abandoned = Column(String(16), nullable=False)
    created_at          = Column(DateTime(timezone=True),
                                 default=lambda: datetime.now(timezone.utc))


class Report(Base):
    """报销单 — 多笔 submission / draft 打包成一张，员工提交审批。

    状态:
      open      — 开放式购物车,员工可继续添加/编辑
      pending   — 已提交,等待经理审批
      approved  — 经理已批准
      rejected  — 经理已拒绝
      needs_revision — 经理退回修改
      withdrawn — 已撤回 (从 pending/approved 回到 open)
    """
    __tablename__ = "reports"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id   = Column(String(64),  nullable=False, index=True)
    title         = Column(String(255), nullable=False, default="新建报销单")
    status        = Column(String(20),  nullable=False, default="open")
    revision_reason = Column(String(500), nullable=True)
    # Per-report voucher number — assigned once when finance approves the
    # report and shared across all submissions in this report (matches
    # standard double-entry accounting: one business event = one voucher,
    # multiple debit lines + one credit line).
    voucher_number     = Column(String(50),  nullable=True, unique=True, index=True)
    voucher_posted_at  = Column(DateTime(timezone=True), nullable=True)
    submitted_at  = Column(DateTime(timezone=True), nullable=True)
    withdrawn_at  = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True),
                            default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))


class LLMTrace(EvalBase):
    """LLM 调用追踪 — 记录每次 LLM 调用的完整上下文，用于 eval 和 debug。

    物理存放在 Eval 专用库 concurshield_eval.db（通过 EVAL_DATABASE_URL 配置）。
    """
    __tablename__ = "llm_traces"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    component       = Column(String(50),  nullable=False, index=True)   # fraud_rule_11, ambiguity_detector, ocr
    submission_id   = Column(String(36),  nullable=True, index=True)    # 关联的报销单
    model           = Column(String(50),  nullable=False)               # gpt-4o, claude-sonnet-4-20250514, MiniMax-M2
    prompt          = Column(JSON,        nullable=False)               # 完整 messages 数组
    response        = Column(Text,        nullable=True)                # LLM 原始返回
    parsed_output   = Column(JSON,        nullable=True)                # 结构化解析结果
    latency_ms      = Column(Integer,     nullable=True)
    token_usage     = Column(JSON,        nullable=True)                # {"input": N, "output": N}
    error           = Column(Text,        nullable=True)                # 错误信息（如有）
    created_at      = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EvalRun(EvalBase):
    """Eval 运行记录 — 每次 eval 执行的汇总结果。

    物理存放在 Eval 专用库 concurshield_eval.db（通过 EVAL_DATABASE_URL 配置）。
    """
    __tablename__ = "eval_runs"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    started_at    = Column(DateTime(timezone=True), nullable=False)
    finished_at   = Column(DateTime(timezone=True), nullable=True)
    total_cases   = Column(Integer, nullable=True)
    passed_cases  = Column(Integer, nullable=True)
    pass_rate     = Column(Float,   nullable=True)
    results       = Column(JSON,    nullable=True)    # [{case_id, component, passed, score, latency_ms, error}]
    trigger       = Column(String(50), nullable=False, default="manual")  # manual | ci | pytest
    run_metadata  = Column("metadata", JSON, nullable=True)  # 6 factors: prompt_version, model, sampling, config, parsing, dataset
    component_metrics = Column(JSON, nullable=True)  # per-component P/R/F1: {comp: {正确标记, 误报, 漏报, 正确放行, precision, recall, f1}}
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """站内通知 — 经理收到撤回等事件时的提醒。"""
    __tablename__ = "notifications"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient_id  = Column(String(64),  nullable=False, index=True)
    kind          = Column(String(32),  nullable=False)   # report_withdrawn | ...
    title         = Column(String(255), nullable=False)
    body          = Column(Text,        nullable=True)
    link          = Column(String(500), nullable=True)
    read_at       = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── 预算期间工具 ──────────────────────────────────────────────────

def _current_period() -> str:
    """返回当前季度期间字符串，例如 '2026-Q2'。"""
    d = date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _period_date_range(period: str) -> tuple[str, str]:
    """将期间字符串转为 (start_date, end_date) ISO 日期字符串对。
    '2026-Q2' → ('2026-04-01', '2026-06-30')
    '2026'    → ('2026-01-01', '2026-12-31')
    """
    if "-Q" in period:
        year_str, q_str = period.split("-Q")
        year, q = int(year_str), int(q_str)
        month_start = (q - 1) * 3 + 1
        month_end = q * 3
        last_day = calendar.monthrange(year, month_end)[1]
        return f"{year}-{month_start:02d}-01", f"{year}-{month_end:02d}-{last_day:02d}"
    else:
        year = int(period)
        return f"{year}-01-01", f"{year}-12-31"


def _rolling_months(n: int) -> list[tuple[str, str]]:
    """Return (start_date, end_date) ISO string pairs for the last n complete calendar months.

    Returned newest-first: index 0 is last month, index n-1 is n months ago.
    Example on 2026-04-14 with n=3:
      [('2026-03-01', '2026-03-31'), ('2026-02-01', '2026-02-28'), ('2026-01-01', '2026-01-31')]
    """
    today = date.today()
    result = []
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        last_day = calendar.monthrange(year, month)[1]
        result.append((f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"))
    return result


# ── 初始化 ────────────────────────────────────────────────────────

async def init_db() -> None:
    """建表（幂等）。在 main.py startup 事件里调用。

    分别在主业务引擎和 Eval 引擎上运行 create_all。Eval 库只包含
    LLMTrace / EvalRun，但 create_all 对已有表是无操作，完全幂等。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with eval_engine.begin() as conn:
        await conn.run_sync(EvalBase.metadata.create_all)
    # 种植演示数据（幂等）
    async with AsyncSessionLocal() as db:
        await seed_budget_demo(db)


# ── CRUD — submissions ────────────────────────────────────────────

async def create_submission(db: AsyncSession, data: dict[str, Any]) -> Submission:
    sub = Submission(**data)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def get_submission(db: AsyncSession, submission_id: str) -> Optional[Submission]:
    result = await db.execute(select(Submission).where(Submission.id == submission_id))
    return result.scalar_one_or_none()


async def get_submission_by_invoice(db: AsyncSession, invoice_number: str) -> Optional[Submission]:
    """发票号码查重 — 用于提交前的 dedup 校验。"""
    result = await db.execute(
        select(Submission).where(Submission.invoice_number == invoice_number)
    )
    return result.scalar_one_or_none()


async def list_submissions(
    db: AsyncSession,
    *,
    employee_id: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    exported: Optional[bool] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    q = select(Submission).order_by(Submission.created_at.desc())
    if employee_id:
        q = q.where(Submission.employee_id == employee_id)
    if status:
        q = q.where(Submission.status == status)
    if statuses:
        q = q.where(Submission.status.in_(statuses))
    if exported is True:
        q = q.where(Submission.exported_at.is_not(None))
    elif exported is False:
        q = q.where(Submission.exported_at.is_(None))
    total_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(total_q)).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": (page * page_size) < total,
    }


async def list_recent_descriptions(
    db: AsyncSession,
    employee_id: str,
    days: int = 30,
    limit: int = 20,
) -> list[str]:
    """Return recent non-empty descriptions for an employee (for template detection)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:10]
    result = await db.execute(
        select(Submission.description)
        .where(
            Submission.employee_id == employee_id,
            Submission.description.isnot(None),
            Submission.description != "",
            Submission.date >= cutoff,
        )
        .order_by(Submission.created_at.desc())
        .limit(limit)
    )
    return [row[0] for row in result.all()]


async def list_submissions_by_merchant(
    db: AsyncSession,
    merchant: str,
    days: int = 90,
    limit: int = 100,
) -> list:
    """All submissions to a given merchant across all employees (for collusion/vendor rules)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:10]
    result = await db.execute(
        select(Submission)
        .where(Submission.merchant == merchant, Submission.date >= cutoff)
        .order_by(Submission.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_approvals_by_approver(
    db: AsyncSession,
    approver_id: str,
    days: int = 90,
) -> list:
    """All submissions approved by a given approver (for approval pattern analysis)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:10]
    result = await db.execute(
        select(Submission)
        .where(
            Submission.approver_id == approver_id,
            Submission.approved_at.isnot(None),
            Submission.date >= cutoff,
        )
        .order_by(Submission.approved_at.desc())
    )
    return list(result.scalars().all())


async def list_employee_submissions_by_quarter(
    db: AsyncSession,
    employee_id: str,
) -> dict[str, float]:
    """Return {quarter_label: total_amount} for seasonal analysis."""
    result = await db.execute(
        select(Submission.date, Submission.amount)
        .where(Submission.employee_id == employee_id)
        .order_by(Submission.date)
    )
    rows = result.all()
    quarter_totals: dict[str, float] = {}
    for row_date, amount in rows:
        try:
            d = date.fromisoformat(row_date) if isinstance(row_date, str) else row_date
            q = f"{d.year}-Q{(d.month - 1) // 3 + 1}"
            quarter_totals[q] = quarter_totals.get(q, 0) + float(amount)
        except (ValueError, TypeError):
            continue
    return quarter_totals


async def update_submission_status(
    db: AsyncSession,
    submission_id: str,
    status: str,
    approver_id: Optional[str] = None,
    approver_comment: Optional[str] = None,
) -> Optional[Submission]:
    sub = await get_submission(db, submission_id)
    if not sub:
        return None
    sub.status = status
    sub.updated_at = datetime.now(timezone.utc)
    if approver_id is not None:
        sub.approver_id = approver_id
    if approver_comment is not None:
        sub.approver_comment = approver_comment
    if status == "manager_approved":
        sub.approved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return sub


async def update_submission_finance(
    db: AsyncSession,
    submission_id: str,
    *,
    status: str,
    finance_approver_id: str,
    finance_approver_comment: Optional[str] = None,
    gl_account: Optional[str] = None,
    cost_center: Optional[str] = None,
    project_code: Optional[str] = None,
    voucher_number: Optional[str] = None,
) -> Optional[Submission]:
    """财务审批 — 通过/拒绝，可同时覆盖 GL/CC/项目编码。"""
    sub = await get_submission(db, submission_id)
    if not sub:
        return None
    sub.status = status
    sub.finance_approver_id = finance_approver_id
    sub.finance_approver_comment = finance_approver_comment
    if status == "finance_approved":
        sub.finance_approved_at = datetime.now(timezone.utc)
    if gl_account is not None:
        sub.gl_account = gl_account
    if cost_center is not None:
        sub.cost_center = cost_center
    if project_code is not None:
        sub.project_code = project_code
    if voucher_number is not None:
        sub.voucher_number = voucher_number
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return sub


async def mark_submissions_exported(
    db: AsyncSession,
    submission_ids: list[str],
    batch_id: str,
) -> int:
    """批量标记已导出。返回成功更新的数量。"""
    now = datetime.now(timezone.utc)
    count = 0
    for sid in submission_ids:
        sub = await get_submission(db, sid)
        if sub and sub.exported_at is None:
            sub.exported_at = now
            sub.export_batch_id = batch_id
            sub.status = "exported"
            sub.updated_at = now
            count += 1
    await db.commit()
    return count


async def append_audit_step(
    db: AsyncSession,
    submission_id: str,
    *,
    message: str,
    phase: str,
    passed: bool = True,
    extra: Optional[dict] = None,
) -> Optional[Submission]:
    """渐进式追加一条 audit_report.timeline 条目。

    Option B 的核心：审计 timeline 不再一次性写入全部 5 步，而是按
    审批阶段渐进追加。submit 时只写前 3 步（提交时能验证的事），
    经理批准后追加'凭证生成'，财务批准后追加'付款执行'。

    这样 AI 解释卡显示的 timeline 始终反映"已经发生的事"，不会出现
    "经理还没批就显示付款通过"这种误导性数据。
    """
    sub = await get_submission(db, submission_id)
    if not sub:
        return None
    report = dict(sub.audit_report or {})
    timeline = list(report.get("timeline") or [])
    entry = {
        "message": message,
        "passed": passed,
        "skipped": False,
        "phase": phase,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        entry.update(extra)
    timeline.append(entry)
    report["timeline"] = timeline
    sub.audit_report = report
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return sub


async def update_submission_analysis(
    db: AsyncSession,
    submission_id: str,
    *,
    ocr_data: Optional[dict] = None,
    audit_report: Optional[dict] = None,
    risk_score: Optional[float] = None,
    tier: Optional[str] = None,
    status: str = "reviewed",
) -> Optional[Submission]:
    sub = await get_submission(db, submission_id)
    if not sub:
        return None
    if ocr_data is not None:
        sub.ocr_data = ocr_data
    if audit_report is not None:
        sub.audit_report = audit_report
    if risk_score is not None:
        sub.risk_score = risk_score
    if tier is not None:
        sub.tier = tier
    sub.status = status
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return sub


# ── CRUD — employees ──────────────────────────────────────────────

async def upsert_employee(db: AsyncSession, data: dict[str, Any]) -> Employee:
    """新建或更新员工档案。"""
    existing = await get_employee(db, data["id"])
    if existing:
        for k, v in data.items():
            if k != "id" and v is not None:
                setattr(existing, k, v)
        existing.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing)
        return existing
    emp = Employee(**data)
    db.add(emp)
    await db.commit()
    await db.refresh(emp)
    return emp


async def get_employee(db: AsyncSession, employee_id: str) -> Optional[Employee]:
    result = await db.execute(select(Employee).where(Employee.id == employee_id))
    return result.scalar_one_or_none()


async def list_employees(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    q = select(Employee).order_by(Employee.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(Employee))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": (page * page_size) < total,
    }


async def delete_employee(db: AsyncSession, employee_id: str) -> bool:
    emp = await get_employee(db, employee_id)
    if not emp:
        return False
    await db.delete(emp)
    await db.commit()
    return True


# ── CRUD — drafts ─────────────────────────────────────────────────

async def create_draft(db: AsyncSession, employee_id: str, report_id: Optional[str] = None) -> Draft:
    draft = Draft(
        employee_id=employee_id,
        report_id=report_id,
        fields={},
        field_sources={},
        chat_history=[],
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def get_draft(db: AsyncSession, draft_id: str) -> Optional[Draft]:
    result = await db.execute(select(Draft).where(Draft.id == draft_id))
    return result.scalar_one_or_none()


async def update_draft_field(
    db: AsyncSession,
    draft_id: str,
    field: str,
    value: Any,
    source: str = "user",
) -> Optional[Draft]:
    """写入 draft 的单个字段，同时记录来源。"""
    draft = await get_draft(db, draft_id)
    if not draft:
        return None
    # SQLAlchemy JSON 字段需要整体替换才能检测变更
    fields = dict(draft.fields or {})
    sources = dict(draft.field_sources or {})
    fields[field] = value
    sources[field] = source
    draft.fields = fields
    draft.field_sources = sources
    draft.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


async def update_draft_receipt(
    db: AsyncSession, draft_id: str, receipt_url: str
) -> Optional[Draft]:
    draft = await get_draft(db, draft_id)
    if not draft:
        return None
    draft.receipt_url = receipt_url
    draft.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


async def append_draft_messages(
    db: AsyncSession, draft_id: str, messages: list[dict]
) -> Optional[Draft]:
    draft = await get_draft(db, draft_id)
    if not draft:
        return None
    history = list(draft.chat_history or [])
    history.extend(messages)
    draft.chat_history = history
    draft.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


async def mark_draft_submitted(
    db: AsyncSession, draft_id: str, submission_id: str
) -> Optional[Draft]:
    draft = await get_draft(db, draft_id)
    if not draft:
        return None
    draft.submitted_as = submission_id
    draft.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


# ── CRUD — reports ───────────────────────────────────────────────

async def create_report(
    db: AsyncSession, employee_id: str, title: str = "新建报销单"
) -> Report:
    report = Report(employee_id=employee_id, title=title, status="open")
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def get_report(db: AsyncSession, report_id: str) -> Optional[Report]:
    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


async def list_reports_for_employee(
    db: AsyncSession, employee_id: str, status: Optional[str] = None
) -> list[Report]:
    stmt = select(Report).where(Report.employee_id == employee_id)
    if status:
        stmt = stmt.where(Report.status == status)
    stmt = stmt.order_by(Report.updated_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_open_report(
    db: AsyncSession, employee_id: str
) -> Report:
    """Most-recent open report for the employee, or a fresh one."""
    stmt = (
        select(Report)
        .where(Report.employee_id == employee_id, Report.status == "open")
        .order_by(Report.updated_at.desc())
    )
    result = await db.execute(stmt)
    existing = result.scalars().first()
    if existing:
        return existing
    return await create_report(db, employee_id)


async def list_report_submissions(
    db: AsyncSession, report_id: str
) -> list[Submission]:
    result = await db.execute(
        select(Submission)
        .where(Submission.report_id == report_id)
        .order_by(Submission.created_at.asc())
    )
    return list(result.scalars().all())


async def list_report_drafts(
    db: AsyncSession, report_id: str
) -> list[Draft]:
    """Drafts attached to a report that haven't been finalized yet."""
    result = await db.execute(
        select(Draft)
        .where(Draft.report_id == report_id, Draft.submitted_as.is_(None))
        .order_by(Draft.created_at.asc())
    )
    return list(result.scalars().all())


async def set_report_status(
    db: AsyncSession,
    report_id: str,
    status: str,
    *,
    submitted_at: Optional[datetime] = None,
    withdrawn_at: Optional[datetime] = None,
) -> Optional[Report]:
    report = await get_report(db, report_id)
    if not report:
        return None
    report.status = status
    if submitted_at is not None:
        report.submitted_at = submitted_at
    if withdrawn_at is not None:
        report.withdrawn_at = withdrawn_at
    report.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(report)
    return report


async def attach_draft_to_report(
    db: AsyncSession, draft_id: str, report_id: str
) -> Optional[Draft]:
    draft = await get_draft(db, draft_id)
    if not draft:
        return None
    draft.report_id = report_id
    draft.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


# ── CRUD — notifications ─────────────────────────────────────────

async def create_notification(
    db: AsyncSession,
    *,
    recipient_id: str,
    kind: str,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
) -> Notification:
    n = Notification(
        recipient_id=recipient_id, kind=kind,
        title=title, body=body, link=link,
    )
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return n


async def list_notifications(
    db: AsyncSession, recipient_id: str, *, unread_only: bool = False
) -> list[Notification]:
    stmt = select(Notification).where(Notification.recipient_id == recipient_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def mark_notification_read(
    db: AsyncSession, notification_id: str
) -> Optional[Notification]:
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    n = result.scalar_one_or_none()
    if not n:
        return None
    n.read_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(n)
    return n


# ── 凭证号生成 — YYYYMM-NNNN，按月重置 ────────────────────────────

async def next_voucher_number(
    db: AsyncSession, report_id: Optional[str] = None
) -> str:
    """生成 YYYYMM-NNNN 凭证号。

    幂等保证（当传入 report_id 时）：如果该报销单已经有 voucher_number，
    直接返回它，不再分配新号。这保证一张 report 全程只占用一个凭证号——
    即使财务批准失败重试也不会消耗新号位。

    NNNN 在当月内自增，跨 reports 唯一。
    """
    # Idempotency: if this report already has a voucher, reuse it.
    if report_id:
        existing = await db.execute(
            select(Report.voucher_number).where(Report.id == report_id)
        )
        existing_vn = existing.scalar_one_or_none()
        if existing_vn:
            return existing_vn

    now = datetime.now(timezone.utc)
    prefix = now.strftime("%Y%m")
    # Count distinct existing vouchers (from both reports.voucher_number and
    # legacy submissions.voucher_number to keep series continuous during
    # the migration window).
    report_count = await db.execute(
        select(func.count()).select_from(Report).where(
            Report.voucher_number.like(f"{prefix}-%")
        )
    )
    sub_count = await db.execute(
        select(func.count(func.distinct(Submission.voucher_number))).where(
            Submission.voucher_number.like(f"{prefix}-%"),
            Submission.report_id.notin_(
                select(Report.id).where(Report.voucher_number.isnot(None))
            ),
        )
    )
    count = (report_count.scalar_one() or 0) + (sub_count.scalar_one() or 0)
    return f"{prefix}-{count + 1:04d}"


# ── CRUD — audit_logs ─────────────────────────────────────────────

async def create_audit_log(
    db: AsyncSession,
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> AuditLog:
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail or {},
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_audit_logs(
    db: AsyncSession,
    *,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    q = select(AuditLog).order_by(AuditLog.created_at.desc())
    if actor_id:
        q = q.where(AuditLog.actor_id == actor_id)
    if action:
        q = q.where(AuditLog.action == action)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": (page * page_size) < total,
    }


# ── CRUD — budget ─────────────────────────────────────────────────

async def get_budget_policy(db: AsyncSession, cost_center: Optional[str]) -> Optional[BudgetPolicy]:
    """优先返回成本中心专属策略，回退到全局默认（cost_center IS NULL）。"""
    specific = await db.execute(
        select(BudgetPolicy).where(BudgetPolicy.cost_center == cost_center)
    )
    policy = specific.scalar_one_or_none()
    if policy is not None:
        return policy
    default = await db.execute(
        select(BudgetPolicy).where(BudgetPolicy.cost_center.is_(None))
    )
    return default.scalar_one_or_none()


async def upsert_budget_policy(
    db: AsyncSession,
    cost_center: Optional[str],
    info_threshold: float,
    block_threshold: float,
    over_budget_action: str,
    updated_by: str,
) -> BudgetPolicy:
    if cost_center is not None:
        _where = BudgetPolicy.cost_center == cost_center
    else:
        _where = BudgetPolicy.cost_center.is_(None)
    existing = await db.execute(
        select(BudgetPolicy).where(_where)
    )
    policy = existing.scalar_one_or_none()
    if policy is None:
        policy = BudgetPolicy(
            id=str(uuid.uuid4()),
            cost_center=cost_center,
        )
        db.add(policy)
    policy.info_threshold = info_threshold
    policy.block_threshold = block_threshold
    policy.over_budget_action = over_budget_action
    policy.updated_by = updated_by
    policy.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(policy)
    return policy


async def upsert_cost_center_budget(
    db: AsyncSession,
    cost_center: str,
    period: str,
    total_amount: Decimal,
    created_by: str,
) -> CostCenterBudget:
    existing = await db.execute(
        select(CostCenterBudget).where(
            CostCenterBudget.cost_center == cost_center,
            CostCenterBudget.period == period,
        )
    )
    budget = existing.scalar_one_or_none()
    if budget is None:
        budget = CostCenterBudget(
            id=str(uuid.uuid4()),
            cost_center=cost_center,
            period=period,
            created_by=created_by,
        )
        db.add(budget)
    budget.total_amount = total_amount
    budget.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(budget)
    return budget


async def list_cost_center_budgets(
    db: AsyncSession, period: Optional[str] = None
) -> list[CostCenterBudget]:
    q = select(CostCenterBudget).order_by(CostCenterBudget.cost_center)
    if period:
        q = q.where(CostCenterBudget.period == period)
    result = await db.execute(q)
    return list(result.scalars().all())


async def list_budget_policies(db: AsyncSession) -> list[BudgetPolicy]:
    result = await db.execute(select(BudgetPolicy).order_by(BudgetPolicy.cost_center.nullsfirst()))
    return list(result.scalars().all())


async def get_budget_status(
    db: AsyncSession,
    cost_center: str,
    amount: Optional[Decimal] = None,
    period: Optional[str] = None,
) -> dict:
    """计算成本中心预算状态，返回含 signal 字段的字典。
    signal 基于 projected_pct（传入 amount 时）或 usage_pct 计算。
    """
    period = period or _current_period()

    b_result = await db.execute(
        select(CostCenterBudget).where(
            CostCenterBudget.cost_center == cost_center,
            CostCenterBudget.period == period,
        )
    )
    budget = b_result.scalar_one_or_none()
    if budget is None:
        return {"cost_center": cost_center, "period": period, "signal": "ok", "configured": False}

    start_date, end_date = _period_date_range(period)
    spent_result = await db.execute(
        select(func.sum(Submission.amount)).where(
            Submission.cost_center == cost_center,
            Submission.date >= start_date,
            Submission.date <= end_date,
            Submission.status.notin_(["rejected", "review_failed"]),
        )
    )
    spent = Decimal(str(spent_result.scalar() or 0))

    policy = await get_budget_policy(db, cost_center)
    info_threshold = policy.info_threshold if policy else 0.75
    block_threshold = policy.block_threshold if policy else 0.95
    over_budget_action = policy.over_budget_action if policy else "warn_only"

    total = float(budget.total_amount)
    spent_f = float(spent)
    usage_pct = round(spent_f / total, 4) if total > 0 else 0.0
    projected_pct = (
        round((spent_f + float(amount)) / total, 4) if amount is not None and total > 0 else None
    )
    check_pct = projected_pct if projected_pct is not None else usage_pct

    if check_pct > 1.0:
        signal = "over_budget"
    elif check_pct >= block_threshold:
        signal = "blocked"
    elif check_pct >= info_threshold:
        signal = "info"
    else:
        signal = "ok"

    out: dict = {
        "cost_center": cost_center,
        "period": period,
        "total_amount": total,
        "spent_amount": spent_f,
        "usage_pct": usage_pct,
        "info_threshold": info_threshold,
        "block_threshold": block_threshold,
        "over_budget_action": over_budget_action,
        "signal": signal,
        "configured": True,
    }
    if projected_pct is not None:
        out["projected_pct"] = projected_pct

    # ── rolling 3-month trend ──────────────────────────────────────────────
    month_ranges = _rolling_months(3)
    month_totals: list[float] = []
    for m_start, m_end in month_ranges:
        m_result = await db.execute(
            select(func.sum(Submission.amount)).where(
                Submission.cost_center == cost_center,
                Submission.date >= m_start,
                Submission.date <= m_end,
                Submission.status.notin_(["rejected", "review_failed"]),
            )
        )
        month_totals.append(float(m_result.scalar() or 0))

    monthly_avg = sum(month_totals) / len(month_totals) if month_totals else 0.0
    remaining = float(budget.total_amount) - spent_f

    if monthly_avg > 0 and remaining > 0:
        months_until_exhaust = remaining / monthly_avg
        overrun_date = date.today() + timedelta(days=int(months_until_exhaust * 30))
        estimated_overrun_date: Optional[str] = overrun_date.isoformat()
    elif remaining <= 0:
        months_until_exhaust = 0.0
        estimated_overrun_date = date.today().isoformat()
    else:
        months_until_exhaust = None
        estimated_overrun_date = None

    if months_until_exhaust is not None and months_until_exhaust < 1.0:
        overrun_risk = "high"
    elif months_until_exhaust is not None and months_until_exhaust < 2.0:
        overrun_risk = "moderate"
    else:
        overrun_risk = "ok"

    out["trend"] = {
        "monthly_avg": round(monthly_avg, 2),
        "months": list(reversed(month_totals)),  # oldest → newest for sparkline
        "overrun_risk": overrun_risk,
        "estimated_overrun_date": estimated_overrun_date,
    }
    return out


async def unblock_submission(
    db: AsyncSession, submission_id: str, unblocked_by: str
) -> Optional[Submission]:
    sub = await get_submission(db, submission_id)
    if sub is None:
        return None
    sub.budget_blocked = False
    sub.budget_unblocked_by = unblocked_by
    sub.budget_unblocked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(sub)
    return sub


# ── CRUD — telemetry ─────────────────────────────────────────────

async def insert_telemetry(
    db: AsyncSession,
    *,
    draft_id: str,
    entry: str,
    final_layer: str,
    ocr_confidence_min: float | None,
    classify_confidence: float | None,
    fields_edited_count: int,
    time_to_attest_ms: int | None,
    attest_or_abandoned: str,
) -> None:
    ev = TelemetryEvent(
        draft_id=draft_id,
        entry=entry,
        final_layer=final_layer,
        ocr_confidence_min=ocr_confidence_min,
        classify_confidence=classify_confidence,
        fields_edited_count=fields_edited_count,
        time_to_attest_ms=time_to_attest_ms,
        attest_or_abandoned=attest_or_abandoned,
    )
    db.add(ev)
    await db.commit()


# ── Demo 数据种子 ─────────────────────────────────────────────────

async def seed_budget_demo(db: AsyncSession) -> None:
    """种植演示预算数据。幂等——已存在时跳过。
    ENG-TRAVEL: 10,000 Q2 预算，已消耗 87%（8,700）→ signal: info
    MKT-EVENTS: 25,000 Q2 预算，已消耗 96%（24,000）→ signal: blocked
    """
    period = "2026-Q2"

    # Global default policy (idempotent via upsert)
    await upsert_budget_policy(db, None, 0.75, 0.95, "warn_only", "seed")

    # ENG-TRAVEL budget
    await upsert_cost_center_budget(db, "ENG-TRAVEL", period, Decimal("10000"), "seed")
    # MKT-EVENTS budget (custom policy: block at 90%, over budget → block)
    await upsert_cost_center_budget(db, "MKT-EVENTS", period, Decimal("25000"), "seed")
    await upsert_budget_policy(db, "MKT-EVENTS", 0.75, 0.90, "block", "seed")

    # Seed demo employees if they don't exist
    for emp_id, name, cc, hc in [
        ("E001", "Zhang Wei", "ENG-TRAVEL", "CNY"),
        ("E002", "Li Mei",   "MKT-EVENTS", "AUD"),
        ("E003", "Wang Fang","ENG-TRAVEL", "CNY"),
    ]:
        existing = await db.execute(select(Employee).where(Employee.id == emp_id))
        if existing.scalar_one_or_none() is not None:
            continue
        db.add(Employee(
            id=emp_id, name=name,
            department="Engineering" if cc == "ENG-TRAVEL" else "Marketing",
            cost_center=cc,
            home_currency=hc,
        ))

    await db.flush()

    # Seed historical spend for ENG-TRAVEL (8700 total across 3 submissions in Q2)
    for sub_id, emp_id, amt, date_str, merchant, category in [
        ("seed-eng-1", "E001", "3800", "2026-04-05", "Marriott Shanghai", "accommodation"),
        ("seed-eng-2", "E003", "2900", "2026-04-10", "Hilton Beijing", "accommodation"),
        ("seed-eng-3", "E001", "2000", "2026-04-12", "Air China", "transport"),
    ]:
        existing = await db.execute(select(Submission).where(Submission.id == sub_id))
        if existing.scalar_one_or_none() is not None:
            continue
        db.add(Submission(
            id=sub_id, employee_id=emp_id, status="finance_approved",
            amount=Decimal(amt), currency="CNY", category=category,
            date=date_str, merchant=merchant,
            receipt_url="/uploads/demo/receipt_01.jpg",
            cost_center="ENG-TRAVEL", department="Engineering",
        ))

    # Seed historical spend for MKT-EVENTS (24000 total across 2 submissions in Q2)
    for sub_id, emp_id, amt, date_str, merchant in [
        ("seed-mkt-1", "E002", "14000", "2026-04-03", "Grand Hyatt Event"),
        ("seed-mkt-2", "E002", "10000", "2026-04-08", "Shanghai Expo Center"),
    ]:
        existing = await db.execute(select(Submission).where(Submission.id == sub_id))
        if existing.scalar_one_or_none() is not None:
            continue
        db.add(Submission(
            id=sub_id, employee_id=emp_id, status="finance_approved",
            amount=Decimal(amt), currency="CNY", category="entertainment",
            date=date_str, merchant=merchant,
            receipt_url="/uploads/demo/receipt_02.jpg",
            cost_center="MKT-EVENTS", department="Marketing",
        ))

    await db.commit()
