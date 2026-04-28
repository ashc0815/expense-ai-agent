"""Structured rule-violation registry — used by the audit pipeline to attach
human-readable, citable rule descriptions to flagged submissions.

Goal: when an expense is flagged, the manager / finance UI shouldn't just
say "ambiguity score 65 / tier T3"; it should say:

    [policy.amount_exceeds_limit] 餐费 ¥250 超过 L4 员工 + 一线城市限额 ¥200
    [ambiguity.description_vague] 费用描述太短，请补充业务说明

Each violation entry has:
    rule_id    — stable, machine-readable key (e.g. "ambiguity.weekend_meal")
    rule_text  — natural-language explanation shown to the human
    severity   — "info" | "warn" | "error"
    suggestion — (optional) how to fix the violation

Aligned with Airwallex Spend AI's "Explainability" principle:
every AI decision is transparent, auditable, and traceable to a specific rule.
"""
from __future__ import annotations


# ── Ambiguity factor → violation template ─────────────────────────────
# Source factor names come from agent.ambiguity_detector._WEIGHTS.
AMBIGUITY_VIOLATIONS: dict[str, dict[str, str]] = {
    "description_vague": {
        "rule_id": "ambiguity.description_vague",
        "rule_text": "费用描述过短或含泛化词（如「其他」「杂费」「办公」），无法判断业务用途。",
        "severity": "warn",
        "suggestion": "补充具体业务说明：客户名称、项目编号、参会人员或事由。",
    },
    "amount_boundary": {
        "rule_id": "ambiguity.amount_boundary",
        "rule_text": "金额处于政策限额边界（90%–110%），存在拆分嫌疑。",
        "severity": "warn",
        "suggestion": "确认金额是否真实，必要时附议价或多家比价材料。",
    },
    "pattern_anomaly": {
        "rule_id": "ambiguity.pattern_anomaly",
        "rule_text": "近 7 天内存在多笔相似金额、相同类型的费用，可能为重复或人为拆单。",
        "severity": "warn",
        "suggestion": "核对历史报销，避免同一支出重复申报。",
    },
    "time_anomaly": {
        "rule_id": "ambiguity.time_anomaly",
        "rule_text": "费用发生在非工作日（周末），需补充业务必要性说明。",
        "severity": "info",
        "suggestion": "说明加班、客户接待或差旅必要性。",
    },
    "city_mismatch": {
        "rule_id": "ambiguity.city_mismatch",
        "rule_text": "城市名无法识别或标准化前后不一致，可能影响限额匹配。",
        "severity": "info",
        "suggestion": "请填写标准城市名称，或在描述中注明实际发生地。",
    },
}


# ── Policy rule_name → violation template ─────────────────────────────
# Source rule names come from rules.policy_engine RuleResult.rule_name.
# Items not listed fall back to a generic template (see _generic_policy_violation).
POLICY_VIOLATIONS: dict[str, dict[str, str]] = {
    "amount_positive": {
        "rule_id": "policy.amount_positive",
        "rule_text": "金额必须大于零。",
        "severity": "error",
    },
    "date_not_future": {
        "rule_id": "policy.date_not_future",
        "rule_text": "费用日期不能晚于今天。",
        "severity": "error",
    },
    "city_recognized": {
        "rule_id": "policy.city_recognized",
        "rule_text": "城市名称必须在公司支持的城市列表中。",
        "severity": "warn",
        "suggestion": "请联系管理员添加该城市，或选择就近的支持城市。",
    },
    "invoice_format": {
        "rule_id": "policy.invoice_format",
        "rule_text": "发票号 / 发票代码格式不符合规定。",
        "severity": "error",
    },
    "limit_exceeded": {
        "rule_id": "policy.limit_exceeded",
        "rule_text": "费用金额超过适用政策限额（按城市等级 × 员工等级匹配）。",
        "severity": "error",
        # NOTE: do NOT suggest "拆分至限额以内" — splitting one expense into
        # multiple submissions to dodge the per-submission limit is itself a
        # policy violation. Suggest legitimate paths only.
        "suggestion": "补充业务正当性说明并附领导事先批准；或申请一次性例外授权。",
    },
}


# ── Agent compliance reasoner findings → violation template ───────────
# These are violations that hard-coded rules can't catch because they
# need cross-record reasoning (other submissions, HR data, allowance
# tables). Source: backend.services.compliance_lookups + the reasoner
# in agent.compliance_reasoner. Each entry's `severity` is the default;
# the factory may override per-finding (e.g. an APPROVED leave is
# error, a pending one is warn).
AGENT_VIOLATIONS: dict[str, dict[str, str]] = {
    "agent.travel_during_leave": {
        "rule_id": "agent.travel_during_leave",
        "rule_text": "出差/差旅费用日期与该员工已批准的休假记录重叠，需说明实际行程。",
        "severity": "error",
        # No "withdraw and resubmit" — that's also a workaround. Either it
        # was a real business need (then explain it), or it's a mistake
        # (then withdraw the line item).
        "suggestion": "若确为公务出差，请说明业务必要性并附领导确认；若为误报，请撤回该笔。",
    },
    "agent.claim_vs_allowance": {
        "rule_id": "agent.claim_vs_allowance",
        "rule_text": "该员工已领取覆盖此类支出的固定补贴，不可同时再行实报实销。",
        "severity": "error",
        "suggestion": "本笔费用应由现有补贴承担；如属补贴未覆盖的特殊情形，请在描述中说明。",
    },
    "agent.cross_person_meal_double_dip": {
        "rule_id": "agent.cross_person_meal_double_dip",
        "rule_text": "同一顿用餐同时出现在你和他人的报销单中，疑似一餐被两人重复报销。",
        "severity": "error",
        "suggestion": "请确认是否为不同场合；若为同一顿，应仅由一人报销，另一方撤回。",
    },
}


def violation_from_agent_finding(finding: dict) -> dict | None:
    """Map a reasoner finding → fully-populated violation dict.

    `finding` shape:
        {
            "kind":           "agent.travel_during_leave" | ...
            "evidence_chain": [ {kind, ...}, ... ]
            "context":        { ...free-form display hints... }
        }
    """
    kind = finding.get("kind")
    template = AGENT_VIOLATIONS.get(kind) if kind else None
    if not template:
        return None
    v = dict(template)
    chain = finding.get("evidence_chain") or []
    if chain:
        v["evidence_chain"] = chain
    ctx = finding.get("context") or {}
    if ctx:
        v["context"] = ctx
    return v


def collect_agent_violations(findings) -> list[dict]:
    """Convert a list of reasoner findings → violation dicts."""
    out: list[dict] = []
    for f in findings or []:
        v = violation_from_agent_finding(f)
        if v:
            out.append(v)
    return out


def violation_from_factor(factor: str) -> dict | None:
    """Map an ambiguity factor name → violation dict (or None if unknown)."""
    template = AMBIGUITY_VIOLATIONS.get(factor)
    if not template:
        return None
    # Return a fresh copy so callers can mutate (e.g. add evidence) safely.
    return dict(template)


def violation_from_rule_result(rule_result) -> dict | None:
    """Map a failed RuleResult → violation dict.

    Accepts any object with `.rule_name`, `.message`, `.severity`, `.passed`.
    Returns None if the rule passed (only failures generate violations).
    """
    if getattr(rule_result, "passed", True):
        return None

    rule_name = getattr(rule_result, "rule_name", "unknown_rule")
    template = POLICY_VIOLATIONS.get(rule_name)
    if template:
        v = dict(template)
        # Use the engine's actual message as evidence if it's more specific.
        msg = getattr(rule_result, "message", "")
        if msg:
            v["evidence"] = msg
        return v

    # Generic fallback for un-templated rules — at least preserve the message.
    return {
        "rule_id": f"policy.{rule_name}",
        "rule_text": getattr(rule_result, "message", "未通过的政策检查"),
        "severity": getattr(rule_result, "severity", "warn"),
    }


def collect_ambiguity_violations(triggered_factors: list[str]) -> list[dict]:
    """Convert a list of triggered factor names → violation dicts."""
    out: list[dict] = []
    for f in triggered_factors:
        v = violation_from_factor(f)
        if v:
            out.append(v)
    return out


def collect_policy_violations(rule_results) -> list[dict]:
    """Convert a list of RuleResult-like objects → violation dicts (failures only)."""
    out: list[dict] = []
    for rr in rule_results or []:
        v = violation_from_rule_result(rr)
        if v:
            out.append(v)
    return out
