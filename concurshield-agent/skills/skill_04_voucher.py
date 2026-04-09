"""Skill 04: 记账凭证生成。"""

from __future__ import annotations

from models.expense import ExpenseReport


SKILL_NAME = "voucher"


def process(report: ExpenseReport, config: dict) -> dict:
    """根据 expense_types.yaml 生成记账凭证。

    逻辑：
    - 根据费用类型确定借方科目
    - 统一贷方科目为 其他应收款-{员工姓名}
    - 增值税专用发票拆分进项税额

    Returns:
        {"passed": bool, "voucher_entries": list[dict], "issues": list[str]}
    """
    report.add_log(SKILL_NAME, "pass", "凭证生成完成（骨架实现）")
    return {"passed": True, "voucher_entries": [], "issues": []}
