from enum import Enum


class EmployeeLevel(str, Enum):
    L1 = "L1"  # 普通员工
    L2 = "L2"  # 主管/经理
    L3 = "L3"  # 总监
    L4 = "L4"  # VP及以上


class InvoiceType(str, Enum):
    NORMAL = "普票"      # 增值税普通发票
    SPECIAL = "专票"     # 增值税专用发票


class ReportStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    RECEIPT_VALIDATED = "receipt_validated"
    APPROVED = "approved"
    COMPLIANCE_CHECKED = "compliance_checked"
    VOUCHER_GENERATED = "voucher_generated"
    PAID = "paid"
    REJECTED = "rejected"
    FLAGGED = "flagged"


class ComplianceLevel(str, Enum):
    A = "A"  # 完全合规
    B = "B"  # 警告通过（超标在容忍度内）
    C = "C"  # 拒绝（超标超出容忍度）


class FailAction(str, Enum):
    REJECT = "reject"
    WARN = "warn"
    SKIP = "skip"
    ALERT = "alert"
    RETRY = "retry"
