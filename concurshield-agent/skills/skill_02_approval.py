"""Skill 02: 审批流程。

功能:
1. 调用 PolicyEngine.get_approval_chain() 获取每个费用类型的审批链
2. 多费用类型合并——取最高级别审批人
3. 支持 level_overrides：总监跳过直属主管，VP小额自动通过
4. 三级超时机制（reminder → escalate → auto_escalate）
5. 模拟审批：用可控的随机时间测试正常通过和超时场景
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Optional

from config import ConfigLoader
from models.expense import ApprovalResult, ApprovalStep, ExpenseReport
from rules.policy_engine import PolicyEngine


SKILL_NAME = "approval"

# 审批人角色等级排序——用于多类型合并时选取最高级别
_APPROVER_RANK: dict[str, int] = {
    "auto": 0,
    "direct_manager": 1,
    "department_head": 2,
    "vp": 3,
}


# ------------------------------------------------------------------
# 核心 API
# ------------------------------------------------------------------

def process(
    report: ExpenseReport,
    simulate_hours: Optional[list[float]] = None,
    seed: Optional[int] = None,
) -> ApprovalResult:
    """对报销单执行审批流程。

    Args:
        report: 待审批的报销单。
        simulate_hours: 注入每个审批步骤的模拟耗时（用于测试）。
            为 None 时随机生成。
        seed: 随机种子（用于可重现的测试）。

    Returns:
        ApprovalResult，含审批链、升级事件和跳过步骤。
    """
    loader = ConfigLoader()
    engine = PolicyEngine(loader)
    approval_config = loader.get("approval_flow")
    escalation_config = approval_config.get("escalation", {})
    overrides_config = approval_config.get("level_overrides", {})
    employee_level = report.employee.level.value

    # ------------------------------------------------------------------
    # 1. 按费用类型汇总金额
    # ------------------------------------------------------------------
    type_amounts: dict[str, float] = defaultdict(float)
    for item in report.line_items:
        type_amounts[item.expense_type] += item.amount

    # ------------------------------------------------------------------
    # 2. 获取每个类型的审批链
    # ------------------------------------------------------------------
    per_type_chains: dict[str, list[ApprovalStep]] = {}
    for expense_type, amount in type_amounts.items():
        chain = engine.get_approval_chain(expense_type, amount, employee_level)
        per_type_chains[expense_type] = chain

    # ------------------------------------------------------------------
    # 3. 计算 skipped_steps（对比 override 前后）
    # ------------------------------------------------------------------
    skipped_steps = _compute_skipped_steps(
        employee_level, overrides_config, per_type_chains, type_amounts,
    )

    # ------------------------------------------------------------------
    # 4. 多类型合并——取最高级别审批链
    # ------------------------------------------------------------------
    merged_chain = _merge_chains(list(per_type_chains.values()))

    # ------------------------------------------------------------------
    # 5. 模拟审批 + 超时升级
    # ------------------------------------------------------------------
    rng = random.Random(seed)
    escalation_events: list[str] = []
    executed_chain: list[ApprovalStep] = []

    for idx, step in enumerate(merged_chain):
        if step.is_auto_approved:
            completed = ApprovalStep(
                approver_role=step.approver_role,
                time_limit_hours=step.time_limit_hours,
                is_auto_approved=True,
                actual_hours=0.0,
                status="approved",
            )
            executed_chain.append(completed)
            continue

        # 确定模拟耗时
        if simulate_hours is not None and idx < len(simulate_hours):
            hours = simulate_hours[idx]
        else:
            # 随机 1~80 小时，覆盖全部三级超时阈值
            hours = rng.uniform(1, 80)

        # 检查超时升级
        events = _check_escalation(step.approver_role, hours, escalation_config)
        escalation_events.extend(events)

        # 确定步骤状态
        auto_escalate_hours = escalation_config.get("auto_escalate_after_hours", 72)
        escalate_hours = escalation_config.get("escalate_after_hours", 48)
        reminder_hours = escalation_config.get("reminder_after_hours", 24)

        if hours > auto_escalate_hours:
            status = "escalated"
        elif hours > escalate_hours:
            status = "escalated"
        elif hours > reminder_hours:
            status = "reminded"
        else:
            status = "approved"

        completed = ApprovalStep(
            approver_role=step.approver_role,
            time_limit_hours=step.time_limit_hours,
            is_auto_approved=False,
            actual_hours=round(hours, 1),
            status=status,
        )
        executed_chain.append(completed)

    # 审批结果：所有步骤都完成即通过
    approved = len(executed_chain) > 0

    return ApprovalResult(
        approved=approved,
        approval_chain=executed_chain,
        escalation_events=escalation_events,
        skipped_steps=skipped_steps,
    )


# ------------------------------------------------------------------
# Controller 兼容入口
# ------------------------------------------------------------------

def process_report(report: ExpenseReport, config: dict) -> dict:
    """AgentController 调用入口。

    Returns:
        {"passed": bool, "approval_result": ApprovalResult, "issues": list[str]}
    """
    result = process(report)
    issues: list[str] = []
    if not result.approved:
        issues.append("审批未通过")
    if result.escalation_events:
        issues.extend(result.escalation_events)

    log_detail = (
        f"审批链: {' → '.join(s.approver_role for s in result.approval_chain)}, "
        f"升级事件: {len(result.escalation_events)}, "
        f"跳过: {len(result.skipped_steps)}"
    )
    report.add_log(SKILL_NAME, "pass" if result.approved else "fail", log_detail)

    return {
        "passed": result.approved,
        "approval_result": result,
        "issues": issues,
    }


# ------------------------------------------------------------------
# 内部函数
# ------------------------------------------------------------------

def _merge_chains(chains: list[list[ApprovalStep]]) -> list[ApprovalStep]:
    """多费用类型审批链合并——取全部角色的并集，按级别排序。

    如果所有链都是 auto-approved，返回单个 auto 步骤。
    否则忽略 auto 链，合并所有非 auto 链中的角色。
    """
    if not chains:
        return []

    non_auto = [c for c in chains if not all(s.is_auto_approved for s in c)]

    # 所有类型都自动通过
    if not non_auto:
        return [ApprovalStep(
            approver_role="auto",
            time_limit_hours=0,
            is_auto_approved=True,
        )]

    # 收集所有角色及其最大时限
    role_time: dict[str, int] = {}
    for chain in non_auto:
        for step in chain:
            if step.is_auto_approved:
                continue
            existing = role_time.get(step.approver_role)
            if existing is None or step.time_limit_hours > existing:
                role_time[step.approver_role] = step.time_limit_hours

    # 按角色等级排序
    sorted_roles = sorted(role_time.keys(), key=lambda r: _APPROVER_RANK.get(r, 99))
    return [
        ApprovalStep(approver_role=role, time_limit_hours=role_time[role])
        for role in sorted_roles
    ]


def _compute_skipped_steps(
    employee_level: str,
    overrides_config: dict,
    per_type_chains: dict[str, list[ApprovalStep]],
    type_amounts: dict[str, float],
) -> list[str]:
    """计算因 level_override 被跳过的步骤。"""
    skipped: list[str] = []
    overrides = overrides_config.get(employee_level, {})

    # L3: skip_direct_manager
    if overrides.get("skip_direct_manager"):
        skipped.append(f"direct_manager ({employee_level}跳级审批)")

    # L4: auto_approve_below
    auto_below = overrides.get("auto_approve_below")
    if auto_below is not None:
        for expense_type, chain in per_type_chains.items():
            if chain and all(s.is_auto_approved for s in chain):
                amount = type_amounts.get(expense_type, 0)
                skipped.append(
                    f"{expense_type} ¥{amount:.0f} ({employee_level}自动审批, <{auto_below})"
                )

    return skipped


def _check_escalation(
    approver_role: str, hours: float, escalation_config: dict,
) -> list[str]:
    """根据模拟耗时和超时配置生成升级事件。"""
    events: list[str] = []
    reminder = escalation_config.get("reminder_after_hours", 24)
    escalate = escalation_config.get("escalate_after_hours", 48)
    auto_esc = escalation_config.get("auto_escalate_after_hours", 72)

    if hours > auto_esc:
        events.append(
            f"{approver_role}: {hours:.0f}h超时, 触发自动升级(>{auto_esc}h)"
        )
    elif hours > escalate:
        events.append(
            f"{approver_role}: {hours:.0f}h超时, 升级处理(>{escalate}h)"
        )
    elif hours > reminder:
        events.append(
            f"{approver_role}: {hours:.0f}h超时, 已发送提醒(>{reminder}h)"
        )

    return events
