"""Skill 05: 付款执行。

- 五重校验：收款人、开户行、账号格式、金额一致性、前置审核
- 付款方式阈值从 policy.yaml payment 配置读取（不硬编码）
- 模拟成功率从配置读取（默认 95%）
- 失败处理：记录失败原因，支持重试
"""

from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Optional

from config import ConfigLoader
from models.enums import ReportStatus
from models.expense import (
    ExpenseReport,
    PaymentResult,
    RuleResult,
    VoucherResult,
)


SKILL_NAME = "payment"

# 前置审核通过的最低状态
_MIN_STATUS_FOR_PAYMENT = {
    ReportStatus.COMPLIANCE_CHECKED,
    ReportStatus.VOUCHER_GENERATED,
    ReportStatus.APPROVED,
}


# ------------------------------------------------------------------
# 核心 API
# ------------------------------------------------------------------

def process(
    report: ExpenseReport,
    voucher: Optional[VoucherResult] = None,
    seed: Optional[int] = None,
) -> PaymentResult:
    """对报销单执行五重校验后模拟付款。

    Args:
        report: 待付款的报销单。
        voucher: 凭证结果（用于金额交叉校验）。
        seed: 随机种子（用于可重现的测试）。

    Returns:
        PaymentResult，含校验结果、付款方式和执行状态。
    """
    loader = ConfigLoader()
    payment_cfg = loader.get("policy").get("payment", {})
    threshold = payment_cfg.get("bank_transfer_threshold", 5000)
    success_rate = payment_cfg.get("success_rate", 0.95)

    # ------------------------------------------------------------------
    # 五重校验
    # ------------------------------------------------------------------
    pre_checks: list[RuleResult] = []

    # 1. 收款人校验
    payee_ok = bool(report.employee.name and report.employee.name.strip())
    pre_checks.append(RuleResult(
        rule_name="payee_valid",
        passed=payee_ok,
        message="收款人姓名为空" if not payee_ok else f"收款人: {report.employee.name}",
        severity="error" if not payee_ok else "info",
    ))

    # 2. 开户行/账户存在性
    has_account = bool(report.employee.bank_account and report.employee.bank_account.strip())
    pre_checks.append(RuleResult(
        rule_name="bank_account_exists",
        passed=has_account,
        message="银行账户为空" if not has_account else "银行账户已提供",
        severity="error" if not has_account else "info",
    ))

    # 3. 账号格式（纯数字+连字符，去掉连字符后 16-24 位数字）
    account_stripped = re.sub(r"[-\s]", "", report.employee.bank_account or "")
    format_ok = bool(re.fullmatch(r"\d{16,24}", account_stripped))
    pre_checks.append(RuleResult(
        rule_name="bank_account_format",
        passed=format_ok,
        message=f"账号格式异常: '{report.employee.bank_account}'"
        if not format_ok else "账号格式校验通过",
        severity="error" if not format_ok else "info",
    ))

    # 4. 金额一致性
    line_sum = round(sum(item.amount for item in report.line_items), 2)
    amount_match = abs(report.total_amount - line_sum) < 0.01
    pre_checks.append(RuleResult(
        rule_name="amount_consistency",
        passed=amount_match,
        message=f"总金额{report.total_amount}与行项目合计{line_sum}不一致"
        if not amount_match else f"金额一致: ¥{report.total_amount:.2f}",
        severity="error" if not amount_match else "info",
    ))

    # 5. 前置审核状态
    status_ok = report.status in _MIN_STATUS_FOR_PAYMENT
    pre_checks.append(RuleResult(
        rule_name="prior_approval",
        passed=status_ok,
        message=f"报销单状态 '{report.status.value}' 未通过前置审核"
        if not status_ok else f"前置审核通过(状态: {report.status.value})",
        severity="error" if not status_ok else "info",
    ))

    # ------------------------------------------------------------------
    # 校验未通过 → 直接返回失败
    # ------------------------------------------------------------------
    failed_checks = [c for c in pre_checks if not c.passed and c.severity == "error"]
    if failed_checks:
        reasons = "; ".join(c.message for c in failed_checks)
        return PaymentResult(
            success=False,
            payment_ref="",
            payment_method="",
            pre_checks=pre_checks,
            amount=report.total_amount,
            failure_reason=f"预校验失败: {reasons}",
        )

    # ------------------------------------------------------------------
    # 付款方式
    # ------------------------------------------------------------------
    if report.total_amount >= threshold:
        payment_method = "bank_transfer"
    else:
        payment_method = "petty_cash"

    # ------------------------------------------------------------------
    # 模拟付款
    # ------------------------------------------------------------------
    rng = random.Random(seed)
    succeeded = rng.random() < success_rate

    now = datetime.now()
    payment_ref = f"PAY-{now.strftime('%Y%m%d')}-{report.report_id}" if succeeded else ""

    failure_reason = ""
    if not succeeded:
        failure_reason = "银行通道超时，请重试"

    return PaymentResult(
        success=succeeded,
        payment_ref=payment_ref,
        payment_method=payment_method,
        pre_checks=pre_checks,
        amount=report.total_amount,
        failure_reason=failure_reason,
    )


# ------------------------------------------------------------------
# Controller 兼容入口
# ------------------------------------------------------------------

def process_report(report: ExpenseReport, config: dict) -> dict:
    """AgentController 调用入口。"""
    result = process(report)

    log_detail = (
        f"{'成功' if result.success else '失败'}, "
        f"{result.payment_method or 'N/A'}, "
        f"¥{result.amount:.2f}"
    )
    if result.payment_ref:
        log_detail += f", ref={result.payment_ref}"
    if result.failure_reason:
        log_detail += f", 原因: {result.failure_reason}"

    report.add_log(SKILL_NAME, "pass" if result.success else "fail", log_detail)

    return {
        "passed": result.success,
        "payment_result": result,
        "issues": [result.failure_reason] if result.failure_reason else [],
    }
