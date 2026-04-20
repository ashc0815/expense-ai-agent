"""Skill 01: 发票/收据验证——四重校验。

四重校验流程:
1. 格式校验：发票代码 11/12 位数字，号码 8 位数字
2. 抬头校验：购买方名称 vs 企业名称（从 policy.yaml 读取）
3. 重复查重：发票代码+号码 vs 历史库
4. 日期校验：vs 员工入职日期，vs 报销提交日期

重点：发票城市字段经过 CityNormalizer 标准化后再比对。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from config import ConfigLoader
from models.expense import Employee, ExpenseReport, Invoice, ReceiptResult, RuleResult
from rules.city_normalizer import CityNormalizer


SKILL_NAME = "receipt_validation"


def process(
    invoice: Invoice,
    employee: Employee,
    history: list[Invoice],
    submit_date: Optional[date] = None,
) -> ReceiptResult:
    """对单张发票执行四重校验。

    Args:
        invoice: 待校验发票。
        employee: 报销人。
        history: 历史已提交发票列表（用于查重）。
        submit_date: 报销提交日期，默认为今天。

    Returns:
        ReceiptResult，含逐项校验明细和标准化后的城市名。
    """
    if submit_date is None:
        submit_date = date.today()

    loader = ConfigLoader()
    normalizer = CityNormalizer(
        loader.get("city_mapping"),
        loader.get("policy").get("city_tiers", {}),
    )
    company_name = loader.get("policy").get("company_info", {}).get("name", "")

    checks: list[RuleResult] = []

    # ------------------------------------------------------------------
    # 1. 格式校验
    # ------------------------------------------------------------------

    # 1a. 发票代码：11 位或 12 位纯数字
    code_valid = bool(re.fullmatch(r"\d{11,12}", invoice.invoice_code))
    checks.append(RuleResult(
        rule_name="format_code",
        passed=code_valid,
        message=f"发票代码 '{invoice.invoice_code}' 应为11或12位数字"
        if not code_valid else f"发票代码格式正确（{len(invoice.invoice_code)}位）",
        severity="error" if not code_valid else "info",
    ))

    # 1b. 发票号码：8 位纯数字
    number_valid = bool(re.fullmatch(r"\d{8}", invoice.invoice_number))
    checks.append(RuleResult(
        rule_name="format_number",
        passed=number_valid,
        message=f"发票号码 '{invoice.invoice_number}' 应为8位数字"
        if not number_valid else "发票号码格式正确",
        severity="error" if not number_valid else "info",
    ))

    # ------------------------------------------------------------------
    # 2. 抬头校验
    # ------------------------------------------------------------------

    if company_name and invoice.buyer_name:
        buyer_ok = invoice.buyer_name == company_name
        checks.append(RuleResult(
            rule_name="buyer_name_match",
            passed=buyer_ok,
            message=f"购买方 '{invoice.buyer_name}' 与企业名称 '{company_name}' 不一致"
            if not buyer_ok else "购买方抬头校验通过",
            severity="error" if not buyer_ok else "info",
        ))
    elif company_name and not invoice.buyer_name:
        checks.append(RuleResult(
            rule_name="buyer_name_match",
            passed=False,
            message="发票缺少购买方名称",
            severity="warning",
        ))

    # ------------------------------------------------------------------
    # 3. 重复查重
    # ------------------------------------------------------------------

    invoice_key = (invoice.invoice_code, invoice.invoice_number)
    is_duplicate = any(
        (h.invoice_code, h.invoice_number) == invoice_key
        for h in history
    )
    checks.append(RuleResult(
        rule_name="no_duplicate",
        passed=not is_duplicate,
        message=f"发票 {invoice.invoice_code}-{invoice.invoice_number} 已被提交过"
        if is_duplicate else "查重校验通过",
        severity="error" if is_duplicate else "info",
    ))

    # ------------------------------------------------------------------
    # 4. 日期校验
    # ------------------------------------------------------------------

    # 4a. 发票日期不能早于员工入职日期
    before_hire = invoice.date < employee.hire_date
    checks.append(RuleResult(
        rule_name="date_after_hire",
        passed=not before_hire,
        message=f"发票日期 {invoice.date} 早于入职日期 {employee.hire_date}"
        if before_hire else "发票日期晚于入职日期",
        severity="error" if before_hire else "info",
    ))

    # 4b. 发票日期不能晚于提交日期
    after_submit = invoice.date > submit_date
    checks.append(RuleResult(
        rule_name="date_before_submit",
        passed=not after_submit,
        message=f"发票日期 {invoice.date} 晚于提交日期 {submit_date}"
        if after_submit else "发票日期不晚于提交日期",
        severity="error" if after_submit else "info",
    ))

    # ------------------------------------------------------------------
    # 城市标准化 & 一致性
    # ------------------------------------------------------------------

    normalized_city = normalizer.normalize(invoice.city)

    city_known = normalizer.is_known(invoice.city)
    checks.append(RuleResult(
        rule_name="city_recognized",
        passed=city_known,
        message=f"城市 '{invoice.city}' 无法识别，需人工复核"
        if not city_known
        else f"城市标准化: '{invoice.city}' -> '{normalized_city}'",
        severity="warning" if not city_known else "info",
    ))

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    # 只有 severity=error 的失败项才导致整体不通过
    passed = all(c.passed for c in checks if c.severity == "error")

    return ReceiptResult(
        invoice=invoice,
        passed=passed,
        checks=checks,
        normalized_city=normalized_city,
    )


def process_report(report: ExpenseReport, config: dict) -> dict:
    """AgentController 调用入口——对报销单中每张发票执行四重校验。

    Returns:
        {"passed": bool, "receipt_results": list[ReceiptResult], "issues": list[str]}
    """
    receipt_results: list[ReceiptResult] = []
    issues: list[str] = []

    # 收集报销单内所有发票用于内部查重
    all_invoices = [
        item.invoice for item in report.line_items if item.invoice is not None
    ]

    for idx, item in enumerate(report.line_items):
        if item.invoice is None:
            issues.append(f"行项目[{idx}] '{item.description}' 缺少发票")
            continue

        # 历史库 = 当前发票之前的所有发票（同一报销单内也查重）
        history = all_invoices[:idx]

        result = process(
            invoice=item.invoice,
            employee=report.employee,
            history=history,
            submit_date=report.submit_date.date()
            if hasattr(report.submit_date, "date")
            else report.submit_date,
        )
        receipt_results.append(result)

        if not result.passed:
            failed = [c for c in result.checks if not c.passed and c.severity == "error"]
            for c in failed:
                issues.append(f"行项目[{idx}] {c.rule_name}: {c.message}")

    all_passed = all(r.passed for r in receipt_results) and not issues
    log_detail = f"{len(receipt_results)}张发票校验, {len(issues)}个问题"
    report.add_log(SKILL_NAME, "pass" if all_passed else "fail", log_detail)

    return {
        "passed": all_passed,
        "receipt_results": receipt_results,
        "issues": issues,
    }
