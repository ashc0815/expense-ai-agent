"""Skill 05: 付款执行。"""

from __future__ import annotations

from models.expense import ExpenseReport


SKILL_NAME = "payment"


def process(report: ExpenseReport, config: dict) -> dict:
    """执行付款操作。

    逻辑：
    - 生成付款指令
    - 调用银行接口（mock）
    - 更新报销单状态

    Returns:
        {"passed": bool, "payment_ref": str, "issues": list[str]}
    """
    report.add_log(SKILL_NAME, "pass", "付款完成（骨架实现）")
    return {"passed": True, "payment_ref": "PAY-MOCK-001", "issues": []}
