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
class AmbiguityResult:
    """模糊/歧义检测结果。"""
    score: float                    # 0-100
    triggered_factors: list[str]    # 哪些因素触发了
    recommendation: str             # "auto_pass" | "human_review" | "suggest_reject"
    explanation: str                # 中文解释


@dataclass
class LLMReviewResult:
    """Phase 2 预留：LLM 深度语义分析结果。"""
    confidence: float       # 0.0-1.0
    recommendation: str     # "approve" | "reject" | "review"
    reasoning: str


@dataclass
class LineItemComplianceDetail:
    """单行项目的合规检查明细。"""
    line_item: "LineItem"
    normalized_city: str
    city_tier: str
    limit: Optional[float]                    # None = 不限
    compliance_level: "ComplianceLevel"       # A/B/C (forward ref for enum)
    extra_checks: list[RuleResult]            # 附加校验结果（如参会人名单）
    ambiguity: Optional[AmbiguityResult]      # None = 未触发模糊检测


@dataclass
class ComplianceResult:
    """整单合规检查结果。"""
    overall_level: "ComplianceLevel"
    line_details: list[LineItemComplianceDetail]
    shield_triggered: bool        # 有 AMBIGUOUS 项
    issues: list[str]


@dataclass
class VoucherEntry:
    """一条凭证分录。"""
    account: str          # 会计科目
    direction: str        # "debit" | "credit"
    amount: float
    description: str


@dataclass
class VoucherResult:
    """记账凭证生成结果。"""
    voucher_number: str
    entries: list[VoucherEntry]
    total_debit: float
    total_credit: float
    balanced: bool
    issues: list[str]


@dataclass
class PaymentResult:
    """付款执行结果。"""
    success: bool
    payment_ref: str
    payment_method: str           # "bank_transfer" | "petty_cash"
    pre_checks: list[RuleResult]
    amount: float
    retry_count: int = 0
    failure_reason: str = ""


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
