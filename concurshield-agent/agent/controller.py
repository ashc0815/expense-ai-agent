"""总控 Agent——根据 workflow.yaml 编排各 skill 的执行。"""

from __future__ import annotations

import importlib
from typing import Any

from models.enums import FailAction, ReportStatus
from models.expense import ExpenseReport


# skill 名称到模块路径的映射
SKILL_MODULE_MAP = {
    "receipt_validation": "skills.skill_01_receipt",
    "approval": "skills.skill_02_approval",
    "compliance": "skills.skill_03_compliance",
    "voucher": "skills.skill_04_voucher",
    "payment": "skills.skill_05_payment",
}

# skill 通过后对应的报销单状态
SKILL_STATUS_MAP = {
    "receipt_validation": ReportStatus.RECEIPT_VALIDATED,
    "approval": ReportStatus.APPROVED,
    "compliance": ReportStatus.COMPLIANCE_CHECKED,
    "voucher": ReportStatus.VOUCHER_GENERATED,
    "payment": ReportStatus.PAID,
}


class AgentController:
    """根据 workflow.yaml 配置顺序执行各 skill。"""

    def __init__(self, workflow_config: dict, full_config: dict) -> None:
        self._pipeline = workflow_config.get("pipeline", [])
        self._full_config = full_config

    def run(self, report: ExpenseReport) -> dict[str, Any]:
        """执行完整流程。

        Returns:
            {"success": bool, "final_status": str, "results": list[dict]}
        """
        report.status = ReportStatus.SUBMITTED
        results: list[dict] = []

        for step in self._pipeline:
            skill_name = step["skill"]
            enabled = step.get("enabled", True)
            fail_action = FailAction(step.get("fail_action", "reject"))
            max_retries = step.get("max_retries", 1)

            if not enabled:
                results.append({"skill": skill_name, "skipped": True})
                continue

            module_path = SKILL_MODULE_MAP.get(skill_name)
            if not module_path:
                results.append({"skill": skill_name, "error": f"未知 skill: {skill_name}"})
                continue

            module = importlib.import_module(module_path)

            # 执行 skill（支持重试）
            result = None
            for attempt in range(max_retries):
                result = module.process(report, self._full_config)
                if result.get("passed", False):
                    break

            results.append({"skill": skill_name, **result})

            if result.get("passed", False):
                new_status = SKILL_STATUS_MAP.get(skill_name)
                if new_status:
                    report.status = new_status
            else:
                # 根据 fail_action 决定后续行为
                if fail_action == FailAction.REJECT:
                    report.status = ReportStatus.REJECTED
                    return {
                        "success": False,
                        "final_status": report.status.value,
                        "stopped_at": skill_name,
                        "results": results,
                    }
                elif fail_action == FailAction.WARN:
                    report.add_log(skill_name, "warn", "失败但继续执行")
                elif fail_action == FailAction.SKIP:
                    pass
                elif fail_action == FailAction.ALERT:
                    report.add_log(skill_name, "alert", "异常告警")

        return {
            "success": True,
            "final_status": report.status.value,
            "results": results,
        }
