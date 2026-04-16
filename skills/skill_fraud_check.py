"""Skill: 欺诈规则检测——对报销单运行 10 条确定性欺诈规则。

在 submit 阶段运行，位于合规检查之后。
需要 config 中传入 employee_history（员工历史提交）和
company_submissions（全公司近期提交，用于跨员工规则 1/7）。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from backend.services.fraud_rules import (
    DEFAULT_CONFIG,
    EmployeeRow,
    FraudSignal,
    SubmissionRow,
    rule_consecutive_invoices,
    rule_description_template,
    rule_duplicate_attendee,
    rule_fx_arbitrage,
    rule_geo_conflict,
    rule_merchant_category_mismatch,
    rule_person_amount_mismatch,
    rule_pre_resignation_rush,
    rule_receipt_contradiction,
    rule_round_amount,
    rule_threshold_proximity,
    rule_timestamp_conflict,
    rule_vague_description,
    rule_weekend_frequency,
)
from backend.services.llm_fraud_analyzer import analyze_submission
from backend.db.store import list_recent_descriptions
from models.expense import ExpenseReport


SKILL_NAME = "fraud_check"

_MOCK_FX_RATES = {
    "USD": 7.25, "EUR": 7.90, "GBP": 9.20, "JPY": 0.048,
    "AUD": 4.80, "CAD": 5.30, "HKD": 0.93, "SGD": 5.45,
}


def _market_rate(from_ccy: str, to_ccy: str) -> float:
    if to_ccy == "CNY":
        return _MOCK_FX_RATES.get(from_ccy, 0)
    return 0


def _report_to_submission_rows(report: ExpenseReport) -> list[SubmissionRow]:
    rows = []
    for item in report.line_items:
        inv = item.invoice
        rows.append(SubmissionRow(
            id=report.report_id,
            employee_id=report.employee.id,
            amount=item.amount,
            currency=item.currency,
            category=item.expense_type,
            date=item.date.isoformat() if isinstance(item.date, date) else str(item.date),
            merchant=inv.vendor if inv else "",
            invoice_number=inv.invoice_number if inv else None,
            invoice_code=inv.invoice_code if inv else None,
            description=item.description,
            exchange_rate=None,
            city=item.city,
            attendees=item.attendees or None,
        ))
    return rows


def _report_to_employee_row(report: ExpenseReport, config: dict) -> EmployeeRow:
    emp = report.employee
    resignation_date = config.get("_fraud_context", {}).get("resignation_date")
    if resignation_date and isinstance(resignation_date, str):
        resignation_date = date.fromisoformat(resignation_date)
    return EmployeeRow(
        id=emp.id,
        department=emp.department,
        hire_date=emp.hire_date if isinstance(emp.hire_date, date) else None,
        resignation_date=resignation_date,
    )


def process_report(report: ExpenseReport, config: dict) -> dict:
    """AgentController 入口。

    config["_fraud_context"] 应包含:
        employee_history: list[dict]   — 员工历史提交（SubmissionRow 字段）
        company_submissions: list[dict] — 全公司近期提交（跨员工规则用）
        resignation_date: Optional[str] — 员工离职日期 ISO 格式
    """
    fraud_ctx = config.get("_fraud_context", {})
    fraud_config = {**DEFAULT_CONFIG, **config.get("fraud_rules", {})}

    current_rows = _report_to_submission_rows(report)
    employee_row = _report_to_employee_row(report, config)

    history_dicts = fraud_ctx.get("employee_history", [])
    history_rows = [SubmissionRow(**d) for d in history_dicts]

    company_dicts = fraud_ctx.get("company_submissions", [])
    company_rows = [SubmissionRow(**d) for d in company_dicts]

    employee_all = history_rows + current_rows
    company_all = company_rows + current_rows

    all_signals: list[FraudSignal] = []

    # 场景 1: 重复报销 + Attendee 双吃 (跨员工)
    all_signals.extend(rule_duplicate_attendee(company_all))

    # 场景 2: 地理矛盾
    all_signals.extend(rule_geo_conflict(current_rows))

    # 场景 3: 卡线报销
    all_signals.extend(rule_threshold_proximity(employee_all, fraud_config))

    # 场景 4: 时间戳矛盾
    all_signals.extend(rule_timestamp_conflict(current_rows))

    # 场景 5: 周末高频报销
    all_signals.extend(rule_weekend_frequency(employee_all, employee_row, fraud_config))

    # 场景 6: 整数金额聚集
    all_signals.extend(rule_round_amount(employee_all, fraud_config))

    # 场景 7: 发票连号 (跨员工)
    all_signals.extend(rule_consecutive_invoices(company_all, fraud_config))

    # 场景 8: 商户类别不匹配
    all_signals.extend(rule_merchant_category_mismatch(current_rows, fraud_config))

    # 场景 9: 离职前突击报销
    all_signals.extend(rule_pre_resignation_rush(employee_all, employee_row, fraud_config))

    # 场景 10: 汇率套利
    all_signals.extend(rule_fx_arbitrage(current_rows, _market_rate, fraud_config))

    max_score = max((s.score for s in all_signals), default=0)
    passed = max_score < 80

    issues = [f"[{s.rule}] {s.evidence} (score={s.score})" for s in all_signals]

    log_detail = (
        f"{len(all_signals)} 条欺诈信号, "
        f"最高 score={max_score:.0f}, "
        f"{'通过' if passed else '需复核'}"
    )
    report.add_log(SKILL_NAME, "pass" if passed else "fail", log_detail)

    return {
        "passed": passed,
        "fraud_signals": [
            {
                "rule": s.rule,
                "score": s.score,
                "evidence": s.evidence,
                "details": s.details,
            }
            for s in all_signals
        ],
        "max_score": max_score,
        "signal_count": len(all_signals),
        "issues": issues,
    }


async def process_report_async(
    submissions: list[SubmissionRow],
    employee_id: str,
    db,
    fraud_config: dict = DEFAULT_CONFIG,
    employee_row: EmployeeRow | None = None,
    history_rows: list[SubmissionRow] | None = None,
    company_rows: list[SubmissionRow] | None = None,
) -> dict:
    """Async entry point that runs Level 1 (deterministic) + Level 2 (LLM) rules.

    Called from the pipeline when async context (DB session) is available.
    The existing sync `process_report()` continues to work for backward compatibility.
    """
    emp_row = employee_row or EmployeeRow(id=employee_id)
    hist = history_rows or []
    company = company_rows or []
    employee_all = hist + submissions
    company_all = company + submissions

    all_signals: list[FraudSignal] = []

    # ── Level 1: deterministic rules 1-10 ──
    all_signals.extend(rule_duplicate_attendee(company_all))
    all_signals.extend(rule_geo_conflict(submissions))
    all_signals.extend(rule_threshold_proximity(employee_all, fraud_config))
    all_signals.extend(rule_timestamp_conflict(submissions))
    all_signals.extend(rule_weekend_frequency(employee_all, emp_row, fraud_config))
    all_signals.extend(rule_round_amount(employee_all, fraud_config))
    all_signals.extend(rule_consecutive_invoices(company_all, fraud_config))
    all_signals.extend(rule_merchant_category_mismatch(submissions, fraud_config))
    all_signals.extend(rule_pre_resignation_rush(employee_all, emp_row, fraud_config))
    all_signals.extend(rule_fx_arbitrage(submissions, _market_rate, fraud_config))

    # ── Level 2: LLM-powered rules 11-14 ──
    for sub in submissions:
        try:
            recent = await list_recent_descriptions(db, employee_id) if db else []
        except Exception:
            recent = []

        llm_analysis = await analyze_submission(
            submission=sub,
            recent_descriptions=recent,
            receipt_location=sub.city,
        )
        all_signals.extend(rule_description_template(submissions, llm_analysis, fraud_config))
        all_signals.extend(rule_receipt_contradiction(submissions, llm_analysis))
        all_signals.extend(rule_person_amount_mismatch(submissions, llm_analysis))
        all_signals.extend(rule_vague_description(submissions, llm_analysis, fraud_config))

    max_score = max((s.score for s in all_signals), default=0)
    passed = max_score < 80

    return {
        "passed": passed,
        "fraud_signals": [
            {"rule": s.rule, "score": s.score, "evidence": s.evidence, "details": s.details}
            for s in all_signals
        ],
        "max_score": max_score,
        "signal_count": len(all_signals),
        "issues": [f"[{s.rule}] {s.evidence} (score={s.score})" for s in all_signals],
    }
