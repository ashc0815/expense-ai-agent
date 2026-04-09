"""示例报销单数据——用于测试和演示。"""

from datetime import date, datetime

from models.enums import EmployeeLevel, InvoiceType, ReportStatus
from models.expense import Employee, ExpenseReport, Invoice, LineItem


def create_sample_employee() -> Employee:
    return Employee(
        name="张三",
        id="EMP-001",
        department="销售部",
        city="上海",
        hire_date=date(2022, 3, 15),
        bank_account="6222000000012345678",
        level=EmployeeLevel.L1,
    )


def create_sample_report() -> ExpenseReport:
    """创建一份典型的差旅报销单——干净数据，全流程通过。"""
    employee = create_sample_employee()

    invoice_hotel = Invoice(
        invoice_code="031001900211",
        invoice_number="12345678",
        invoice_type=InvoiceType.SPECIAL,
        amount=450.0,
        tax_amount=27.0,
        date=date(2025, 1, 10),
        vendor="如家酒店",
        city="上海",
        items=["住宿费"],
        buyer_name="示例科技有限公司",
    )

    invoice_meal = Invoice(
        invoice_code="031001900212",
        invoice_number="12345679",
        invoice_type=InvoiceType.NORMAL,
        amount=85.0,
        tax_amount=0.0,
        date=date(2025, 1, 10),
        vendor="餐厅A",
        city="上海",
        items=["餐费"],
        buyer_name="示例科技有限公司",
    )

    line_items = [
        LineItem(
            expense_type="accommodation",
            amount=450.0,
            currency="CNY",
            city="上海",
            date=date(2025, 1, 10),
            invoice=invoice_hotel,
            description="上海客户拜访出差住宿一晚标准间",
        ),
        LineItem(
            expense_type="meals",
            amount=85.0,
            currency="CNY",
            city="上海",
            date=date(2025, 1, 10),
            invoice=invoice_meal,
            description="上海客户拜访期间工作午餐一人份",
        ),
    ]

    return ExpenseReport(
        report_id="RPT-2025-001",
        employee=employee,
        line_items=line_items,
        total_amount=535.0,
        submit_date=datetime(2025, 1, 11, 9, 30),
        status=ReportStatus.DRAFT,
    )


def create_over_limit_report() -> ExpenseReport:
    """创建一份超标报销单，用于测试合规检查拒绝。"""
    employee = create_sample_employee()

    invoice = Invoice(
        invoice_code="031001900213",
        invoice_number="12345680",
        invoice_type=InvoiceType.NORMAL,
        amount=600.0,
        tax_amount=0.0,
        date=date(2025, 2, 5),
        vendor="五星酒店",
        city="北京",
        items=["住宿费"],
        buyer_name="示例科技有限公司",
    )

    line_items = [
        LineItem(
            expense_type="accommodation",
            amount=600.0,  # L1在一线城市限额500，超标100 → C级
            currency="CNY",
            city="北京",
            date=date(2025, 2, 5),
            invoice=invoice,
            description="北京总部会议期间住宿一晚超标酒店",
        ),
    ]

    return ExpenseReport(
        report_id="RPT-2025-002",
        employee=employee,
        line_items=line_items,
        total_amount=600.0,
        submit_date=datetime(2025, 2, 6, 10, 0),
        status=ReportStatus.DRAFT,
    )
