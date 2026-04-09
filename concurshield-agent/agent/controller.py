"""总控 Agent——根据 workflow.yaml 编排各 skill 的执行。

流程编排完全配置驱动:
- 客户A想跳过审批 → 改 workflow.yaml 把 approval 设为 enabled: false
- 客户B想把付款失败改为直接终止 → 改 fail_action 为 reject
- 不需要改一行代码
"""

from __future__ import annotations

import importlib
import time
from datetime import timedelta
from typing import Any, Optional

from config import ConfigLoader
from models.enums import FailAction, FinalStatus, ReportStatus
from models.expense import ExpenseReport, ProcessingResult, StepResult


# skill 名称 → 模块路径
_SKILL_MODULE_MAP: dict[str, str] = {
    "receipt_validation": "skills.skill_01_receipt",
    "approval":          "skills.skill_02_approval",
    "compliance":        "skills.skill_03_compliance",
    "voucher":           "skills.skill_04_voucher",
    "payment":           "skills.skill_05_payment",
}

# skill 名称 → 中文显示名
_SKILL_DISPLAY_NAME: dict[str, str] = {
    "receipt_validation": "发票验证",
    "approval":          "审批流程",
    "compliance":        "合规审查",
    "voucher":           "凭证生成",
    "payment":           "付款执行",
}

# skill 通过后对应的报销单状态
_SKILL_STATUS_MAP: dict[str, ReportStatus] = {
    "receipt_validation": ReportStatus.RECEIPT_VALIDATED,
    "approval":          ReportStatus.APPROVED,
    "compliance":        ReportStatus.COMPLIANCE_CHECKED,
    "voucher":           ReportStatus.VOUCHER_GENERATED,
    "payment":           ReportStatus.PAID,
}


class ExpenseController:
    """根据 workflow.yaml 配置编排费用报销审核全流程。"""

    def __init__(self, config_loader: ConfigLoader) -> None:
        self._loader = config_loader
        self._workflow = config_loader.get("workflow")
        self._pipeline = self._workflow.get("pipeline", [])

    def process_report(self, report: ExpenseReport) -> ProcessingResult:
        """执行完整流程。

        Returns:
            ProcessingResult，含最终状态、时间线、shield 报告和配置快照。
        """
        t_start = time.monotonic()
        report.status = ReportStatus.SUBMITTED
        config_snapshot = self._loader.get_all()

        timeline: list[StepResult] = []
        shield_report: Optional[dict] = None
        final_status = FinalStatus.COMPLETED

        self._log(report, "系统", f"开始处理报销单 {report.report_id}")
        self._log(report, "系统",
                  f"员工: {report.employee.name}({report.employee.level.value}), "
                  f"金额: ¥{report.total_amount:.2f}, "
                  f"流程步骤: {len(self._pipeline)}个")

        for step_cfg in self._pipeline:
            skill_name = step_cfg["skill"]
            enabled = step_cfg.get("enabled", True)
            fail_action = FailAction(step_cfg.get("fail_action", "reject"))
            max_retries = step_cfg.get("max_retries", 1)
            display_name = _SKILL_DISPLAY_NAME.get(skill_name, skill_name)

            # ---- 跳过禁用的步骤 ----
            if not enabled:
                step_result = StepResult(
                    skill_name=skill_name,
                    display_name=display_name,
                    passed=True,
                    skipped=True,
                    duration=timedelta(),
                    detail={},
                    fail_action=fail_action.value,
                    message=f"[{display_name}] 已禁用，跳过",
                )
                timeline.append(step_result)
                self._log(report, display_name, "已禁用，跳过")
                continue

            # ---- 加载 skill 模块 ----
            module_path = _SKILL_MODULE_MAP.get(skill_name)
            if not module_path:
                step_result = StepResult(
                    skill_name=skill_name,
                    display_name=display_name,
                    passed=False,
                    skipped=False,
                    duration=timedelta(),
                    detail={"error": f"未知 skill: {skill_name}"},
                    fail_action=fail_action.value,
                    message=f"[{display_name}] 未知的 skill 模块",
                )
                timeline.append(step_result)
                continue

            module = importlib.import_module(module_path)
            skill_fn = getattr(module, "process_report", None) or module.process

            # ---- 执行（支持重试） ----
            result_dict: dict = {}
            attempts = 0
            t_step_start = time.monotonic()

            for attempt in range(max_retries):
                attempts = attempt + 1
                result_dict = skill_fn(report, config_snapshot)

                if result_dict.get("passed", False):
                    break

                if attempt < max_retries - 1 and fail_action == FailAction.RETRY:
                    self._log(report, display_name,
                              f"第{attempts}次执行失败，重试中...")

            t_step_end = time.monotonic()
            duration = timedelta(seconds=t_step_end - t_step_start)
            passed = result_dict.get("passed", False)

            # ---- 构建步骤日志消息 ----
            retry_note = f"(第{attempts}次)" if attempts > 1 else ""
            if passed:
                message = f"[{display_name}] 通过{retry_note}"
            else:
                issues = result_dict.get("issues", [])
                issue_summary = "; ".join(issues[:3]) if issues else "未知原因"
                message = f"[{display_name}] 未通过{retry_note}: {issue_summary}"

            step_result = StepResult(
                skill_name=skill_name,
                display_name=display_name,
                passed=passed,
                skipped=False,
                duration=duration,
                detail=result_dict,
                fail_action=fail_action.value,
                message=message,
            )
            timeline.append(step_result)
            self._log(report, display_name, message)

            # ---- 通过：更新状态 ----
            if passed:
                new_status = _SKILL_STATUS_MAP.get(skill_name)
                if new_status:
                    report.status = new_status

                # 合规检查特殊逻辑：shield 触发
                if skill_name == "compliance":
                    compliance_result = result_dict.get("compliance_result")
                    if compliance_result and getattr(compliance_result, "shield_triggered", False):
                        ambiguity_action = step_cfg.get("ambiguity_action", "flag_for_review")
                        shield_report = self._build_shield_report(compliance_result)

                        if ambiguity_action == "flag_for_review":
                            final_status = FinalStatus.PENDING_REVIEW
                            report.status = ReportStatus.FLAGGED
                            self._log(report, "合规审查",
                                      "⚠ 模糊检测触发 Shield，转入人工复核")
                            break  # 终止流程
                continue

            # ---- 未通过：根据 fail_action 决定后续 ----
            if fail_action == FailAction.REJECT:
                final_status = FinalStatus.REJECTED
                report.status = ReportStatus.REJECTED
                self._log(report, display_name, "✗ 流程终止（拒绝）")
                break

            elif fail_action == FailAction.RETRY:
                # 已经重试完所有次数仍失败
                if skill_name == "payment":
                    final_status = FinalStatus.PAYMENT_FAILED
                else:
                    final_status = FinalStatus.REJECTED
                self._log(report, display_name,
                          f"✗ 重试{max_retries}次后仍失败")
                break

            elif fail_action == FailAction.WARN:
                self._log(report, display_name, "⚠ 警告，继续执行")
                continue

            elif fail_action == FailAction.ALERT:
                self._log(report, display_name, "⚠ 告警已发送，继续执行")
                continue

            elif fail_action == FailAction.SKIP:
                self._log(report, display_name, "跳过")
                continue

        # ---- 汇总 ----
        t_end = time.monotonic()
        total_time = timedelta(seconds=t_end - t_start)

        status_display = {
            FinalStatus.COMPLETED: "全部完成",
            FinalStatus.REJECTED: "已拒绝",
            FinalStatus.PENDING_REVIEW: "待人工复核",
            FinalStatus.PAYMENT_FAILED: "付款失败",
        }
        self._log(report, "系统",
                  f"流程结束 → {status_display.get(final_status, final_status.value)}")

        return ProcessingResult(
            final_status=final_status,
            timeline=timeline,
            shield_report=shield_report,
            config_snapshot=config_snapshot,
            total_processing_time=total_time,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _log(report: ExpenseReport, source: str, message: str) -> None:
        """向报销单写入中文处理日志。"""
        report.add_log(source, "log", message)

    @staticmethod
    def _build_shield_report(compliance_result: Any) -> dict:
        """从合规结果中提取模糊检测报告。"""
        flagged_items: list[dict] = []
        for detail in getattr(compliance_result, "line_details", []):
            ambiguity = getattr(detail, "ambiguity", None)
            if ambiguity and ambiguity.recommendation != "auto_pass":
                flagged_items.append({
                    "expense_type": detail.line_item.expense_type,
                    "amount": detail.line_item.amount,
                    "description": detail.line_item.description,
                    "ambiguity_score": ambiguity.score,
                    "recommendation": ambiguity.recommendation,
                    "triggered_factors": ambiguity.triggered_factors,
                    "explanation": ambiguity.explanation,
                })
        return {
            "shield_triggered": True,
            "overall_level": compliance_result.overall_level.value,
            "flagged_items": flagged_items,
            "total_issues": len(compliance_result.issues),
        }


# ------------------------------------------------------------------
# 向后兼容别名
# ------------------------------------------------------------------

class AgentController:
    """向后兼容：委托给 ExpenseController。"""

    def __init__(self, workflow_config: dict, full_config: dict) -> None:
        loader = ConfigLoader()
        self._ctrl = ExpenseController(loader)

    def run(self, report: ExpenseReport) -> dict[str, Any]:
        result = self._ctrl.process_report(report)
        return {
            "success": result.final_status == FinalStatus.COMPLETED,
            "final_status": report.status.value,
            "results": [
                {
                    "skill": s.skill_name,
                    "passed": s.passed,
                    "skipped": s.skipped,
                }
                for s in result.timeline
            ],
        }
