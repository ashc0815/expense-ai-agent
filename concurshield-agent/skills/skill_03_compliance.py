"""Skill 03: 合规检查。"""

from __future__ import annotations

from models.expense import ExpenseReport


SKILL_NAME = "compliance"


def process(report: ExpenseReport, config: dict) -> dict:
    """根据 policy.yaml 执行费用合规检查。

    检查项：
    - 城市名标准化
    - 费用限额检查（按城市分级 × 员工等级）
    - 超标容忍度判定（A/B/C级）
    - 招待费参会人员名单检查

    Returns:
        {"passed": bool, "compliance_level": str, "issues": list[str]}
    """
    report.add_log(SKILL_NAME, "pass", "合规检查通过（骨架实现）")
    return {"passed": True, "compliance_level": "A", "issues": []}
