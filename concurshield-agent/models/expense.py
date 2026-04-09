from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from models.enums import EmployeeLevel, InvoiceType, ReportStatus


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
