"""Skill 01: 发票/收据验证。"""

from __future__ import annotations

from models.expense import ExpenseReport


SKILL_NAME = "receipt_validation"


def process(report: ExpenseReport, config: dict) -> dict:
    """验证报销单中每个 LineItem 的发票信息。

    检查项：
    - 发票是否存在（对于 requires_invoice 的费用类型）
    - 发票金额与报销金额是否一致
    - 发票日期是否合理

    Returns:
        {"passed": bool, "issues": list[str]}
    """
    report.add_log(SKILL_NAME, "pass", "发票验证通过（骨架实现）")
    return {"passed": True, "issues": []}
