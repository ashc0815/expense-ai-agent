"""Skill 04: 记账凭证生成。

- 会计科目映射从 expense_types.yaml 的 accounting_debit 字段读取
- 贷方科目用 default_credit_account 模板，替换 {employee_name}
- 增值税专用发票拆分：根据 vat_special_invoice 配置决定是否自动拆分进项税额
- 凭证号：记-{YYYYMM}-{自增序号}
- 验证借贷平衡
"""

from __future__ import annotations

import threading
from typing import Optional

from config import ConfigLoader
from models.enums import InvoiceType
from models.expense import ExpenseReport, VoucherEntry, VoucherResult
from rules.policy_engine import PolicyEngine


SKILL_NAME = "voucher"

# 凭证号自增序号（线程安全）
_voucher_lock = threading.Lock()
_voucher_seq: dict[str, int] = {}  # key: "YYYYMM", value: seq


def _next_voucher_number(year_month: str) -> str:
    """生成凭证号: 记-{YYYYMM}-{自增序号}。"""
    with _voucher_lock:
        seq = _voucher_seq.get(year_month, 0) + 1
        _voucher_seq[year_month] = seq
    return f"记-{year_month}-{seq:04d}"


def reset_voucher_seq() -> None:
    """重置序号（用于测试）。"""
    with _voucher_lock:
        _voucher_seq.clear()


# ------------------------------------------------------------------
# 核心 API
# ------------------------------------------------------------------

def process(
    report: ExpenseReport,
    voucher_month: Optional[str] = None,
) -> VoucherResult:
    """根据报销单生成记账凭证。

    Args:
        report: 已通过合规检查的报销单。
        voucher_month: 凭证月份 "YYYYMM"，默认取报销单提交月份。

    Returns:
        VoucherResult，含分录明细和借贷平衡校验。
    """
    loader = ConfigLoader()
    engine = PolicyEngine(loader)
    expense_types_cfg = loader.get("expense_types")
    vat_cfg = expense_types_cfg.get("vat_special_invoice", {})
    split_tax = vat_cfg.get("split_tax", False)
    tax_debit_account = vat_cfg.get("tax_debit_account", "应交税费-进项税额")

    # 贷方科目模板
    credit_template = expense_types_cfg.get("default_credit_account", "其他应收款-{employee_name}")
    credit_account = credit_template.replace("{employee_name}", report.employee.name)

    # 凭证月份
    if voucher_month is None:
        voucher_month = report.submit_date.strftime("%Y%m")
    voucher_number = _next_voucher_number(voucher_month)

    entries: list[VoucherEntry] = []
    issues: list[str] = []
    total_debit = 0.0
    total_credit = 0.0

    for idx, item in enumerate(report.line_items):
        subtype_cfg = engine.get_subtype_config(item.expense_type)
        debit_account = subtype_cfg.get("accounting_debit", "管理费用-其他")

        # 判断是否需要拆分进项税
        needs_tax_split = (
            split_tax
            and item.invoice is not None
            and item.invoice.invoice_type == InvoiceType.SPECIAL
            and item.invoice.tax_amount > 0
        )

        if needs_tax_split:
            # 拆分: 费用金额(不含税) + 进项税额
            net_amount = round(item.amount - item.invoice.tax_amount, 2)
            tax_amount = round(item.invoice.tax_amount, 2)

            entries.append(VoucherEntry(
                account=debit_account,
                direction="debit",
                amount=net_amount,
                description=f"{item.description}(不含税)",
            ))
            total_debit += net_amount

            entries.append(VoucherEntry(
                account=tax_debit_account,
                direction="debit",
                amount=tax_amount,
                description=f"{item.description}(进项税额)",
            ))
            total_debit += tax_amount
        else:
            entries.append(VoucherEntry(
                account=debit_account,
                direction="debit",
                amount=round(item.amount, 2),
                description=item.description,
            ))
            total_debit += round(item.amount, 2)

    # 贷方: 合计一笔
    total_debit = round(total_debit, 2)
    entries.append(VoucherEntry(
        account=credit_account,
        direction="credit",
        amount=total_debit,
        description=f"报销单 {report.report_id}",
    ))
    total_credit = total_debit

    # 借贷平衡校验
    balanced = abs(total_debit - total_credit) < 0.01
    if not balanced:
        issues.append(
            f"借贷不平衡: 借方{total_debit:.2f} ≠ 贷方{total_credit:.2f}"
        )

    return VoucherResult(
        voucher_number=voucher_number,
        entries=entries,
        total_debit=total_debit,
        total_credit=total_credit,
        balanced=balanced,
        issues=issues,
    )


# ------------------------------------------------------------------
# Controller 兼容入口
# ------------------------------------------------------------------

def process_report(report: ExpenseReport, config: dict) -> dict:
    """AgentController 调用入口。"""
    result = process(report)

    log_detail = (
        f"{result.voucher_number}, "
        f"{len(result.entries)}笔分录, "
        f"{'平衡' if result.balanced else '不平衡'}"
    )
    report.add_log(SKILL_NAME, "pass" if result.balanced else "fail", log_detail)

    return {
        "passed": result.balanced and not result.issues,
        "voucher_result": result,
        "issues": result.issues,
    }
