from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from models.enums import EmployeeLevel, InvoiceType, ReportStatus


@dataclass
class ApprovalStep:
    """审批链中的一个步骤。"""
    approver_role: str
    time_limit_hours: int
    is_auto_approved: bool = False
    actual_hours: float = 0.0    # 模拟实际审批耗时
    status: str = ""             # "approved" | "escalated" | "reminded" | "skipped"


@dataclass
class RuleResult:
    """规则校验结果。"""
    rule_name: str
    passed: bool
    message: str
    severity: str = "error"  # "error" | "warning" | "info"


@dataclass
class ApprovalResult:
    """审批流程结果。"""
    approved: bool
    approval_chain: list[ApprovalStep]    # 实际走过的审批链
    escalation_events: list[str]          # 超时升级事件
    skipped_steps: list[str]              # 因 level_override 跳过的步骤


@dataclass
class ReceiptResult:
    """单张发票的收据验证结果。"""
    invoice: "Invoice"
    passed: bool
    checks: list[RuleResult]
    normalized_city: str


@dataclass
class Employee:
    name: str
    id: str
    department: str
    city: str
    hire_date: date
    bank_account: str
    level: EmployeeLevel


@dataclass
class Invoice:
    invoice_code: str
    invoice_number: str
    invoice_type: InvoiceType
    amount: float
    tax_amount: float
    date: date
    vendor: str
    city: str
    items: list[str] = field(default_factory=list)
    buyer_name: str = ""  # 购买方名称（用于抬头校验）


@dataclass
class LineItem:
    expense_type: str        # 对应 expense_types.yaml 的 subtype id
    amount: float
    currency: str
    city: str
    date: date
    invoice: Optional[Invoice]
    description: str
    attendees: list[str] = field(default_factory=list)


@dataclass
class ExpenseReport:
    report_id: str
    employee: Employee
    line_items: list[LineItem]
    total_amount: float
    submit_date: datetime
    status: ReportStatus = ReportStatus.DRAFT
    processing_log: list[dict] = field(default_factory=list)

    def add_log(self, skill: str, result: str, detail: str = "") -> None:
        self.processing_log.append({
            "skill": skill,
            "result": result,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        })
