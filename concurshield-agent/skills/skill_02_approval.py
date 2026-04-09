"""Skill 02: 审批流程。"""

from __future__ import annotations

from models.expense import ExpenseReport


SKILL_NAME = "approval"


def process(report: ExpenseReport, config: dict) -> dict:
    """根据 approval_flow.yaml 确定审批人并执行审批。

    逻辑：
    - 根据费用类型和金额匹配审批规则
    - 考虑员工等级跳级审批
    - 处理超时升级机制

    Returns:
        {"passed": bool, "approver": str, "issues": list[str]}
    """
    report.add_log(SKILL_NAME, "pass", "审批通过（骨架实现）")
    return {"passed": True, "approver": "auto", "issues": []}
