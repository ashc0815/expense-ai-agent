"""Level 2 确定性欺诈检测规则（场景 1-10）。

每条规则接收结构化数据，返回 FraudSignal 列表。
所有规则纯函数、无副作用，可直接 pytest。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence


@dataclass
class FraudSignal:
    rule: str
    score: float          # 0-100
    evidence: str
    details: dict = field(default_factory=dict)


@dataclass
class SubmissionRow:
    """规则引擎所需的报销行数据（从 DB 行或 mock 构造）。"""
    id: str
    employee_id: str
    amount: float
    currency: str
    category: str
    date: str                          # ISO "YYYY-MM-DD"
    merchant: str
    invoice_number: Optional[str] = None
    invoice_code: Optional[str] = None
    description: Optional[str] = None
    exchange_rate: Optional[float] = None
    city: Optional[str] = None
    attendees: Optional[list[str]] = None


@dataclass
class EmployeeRow:
    id: str
    department: str = "未分配"
    hire_date: Optional[date] = None
    resignation_date: Optional[date] = None


# ── 可配置阈值 ──────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "threshold_proximity_pct": 0.03,    # 场景3: 距限额 3% 以内算卡线
    "threshold_proximity_limit": 300.0, # 场景3: 每日限额
    "threshold_proximity_min_count": 3, # 场景3: 至少 N 笔才 flag
    "weekend_meal_max_weeks": 4,        # 场景5: 连续 N 周有周末餐饮
    "weekend_exempt_depts": ["销售部", "市场部", "BD"],  # 场景5: 豁免部门
    "round_amount_pct": 0.5,            # 场景6: 超过 50% 是整数就 flag
    "round_amount_min_count": 5,        # 场景6: 至少 N 笔
    "consecutive_invoice_min": 3,       # 场景7: 至少 N 张连号
    "merchant_category_blocklist": {    # 场景8: 商户关键词 → 不允许的类别
        "足浴": ["meal", "transport", "accommodation"],
        "按摩": ["meal", "transport", "accommodation"],
        "烟酒": ["meal", "transport", "office"],
        "棋牌": ["meal", "transport"],
        "KTV": ["meal", "transport", "office"],
        "会所": ["meal", "transport", "office"],
    },
    "rush_days_before_last": 30,        # 场景9: 离职前 N 天
    "rush_amount_multiplier": 3.0,      # 场景9: 金额超过月均 X 倍
    "fx_deviation_pct": 0.02,           # 场景10: 汇率偏差 > 2%
    # ── Level 2: LLM 翻译层 (场景 11-14) ──
    "template_score_threshold": 70,     # 场景11: 模板化评分阈值
    "vagueness_threshold": 60,          # 场景14: 模糊度阈值
    "vagueness_suspicious_categories": ["gift", "entertainment", "supplies", "other"],  # 场景14
}


# ═══════════════════════════════════════════════════════════════════
# 场景 1: 重复报销 + Attendee 双吃
# ═══════════════════════════════════════════════════════════════════

def rule_duplicate_attendee(
    all_submissions: Sequence[SubmissionRow],
) -> list[FraudSignal]:
    """A 报了一顿饭，B 的 attendee list 里也有 A（或反过来）。"""
    signals = []
    by_date_merchant: dict[str, list[SubmissionRow]] = defaultdict(list)
    for s in all_submissions:
        if s.category == "meal" and s.attendees:
            key = f"{s.date}|{s.merchant}"
            by_date_merchant[key].append(s)

    for key, group in by_date_merchant.items():
        if len(group) < 2:
            continue
        submitters = {s.employee_id for s in group}
        for s in group:
            overlap = submitters & set(s.attendees or [])
            if overlap:
                signals.append(FraudSignal(
                    rule="duplicate_attendee",
                    score=80,
                    evidence=f"员工 {s.employee_id} 的报销 attendee 中包含同餐报销人 {overlap}",
                    details={"key": key, "overlap": list(overlap)},
                ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 2: 地理矛盾
# ═══════════════════════════════════════════════════════════════════

def rule_geo_conflict(
    submissions: Sequence[SubmissionRow],
) -> list[FraudSignal]:
    """同一天出现不同城市的消费，但没有对应的交通记录。"""
    signals = []
    by_date: dict[str, list[SubmissionRow]] = defaultdict(list)
    for s in submissions:
        if s.city:
            by_date[s.date].append(s)

    for dt, group in by_date.items():
        cities = {s.city for s in group}
        if len(cities) <= 1:
            continue
        has_transport = any(s.category == "transport" for s in group)
        if not has_transport:
            signals.append(FraudSignal(
                rule="geo_conflict",
                score=75,
                evidence=f"{dt} 出现 {cities} 多城市消费但无交通记录",
                details={"date": dt, "cities": list(cities)},
            ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 3: 卡线报销
# ═══════════════════════════════════════════════════════════════════

def rule_threshold_proximity(
    submissions: Sequence[SubmissionRow],
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """长期稳定在限额附近（如 300 限额报 299/298/295）。"""
    signals = []
    limit = config["threshold_proximity_limit"]
    pct = config["threshold_proximity_pct"]
    min_count = config["threshold_proximity_min_count"]
    lower = limit * (1 - pct)

    near = [s for s in submissions if lower <= s.amount <= limit]
    if len(near) >= min_count:
        signals.append(FraudSignal(
            rule="threshold_proximity",
            score=70,
            evidence=f"{len(near)} 笔报销金额在 {lower:.0f}-{limit:.0f} 区间（限额 {limit}）",
            details={"count": len(near), "amounts": [s.amount for s in near]},
        ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 4: 时间戳矛盾
# ═══════════════════════════════════════════════════════════════════

def rule_timestamp_conflict(
    submissions: Sequence[SubmissionRow],
    min_travel_hours: float = 2.0,
) -> list[FraudSignal]:
    """同一天不同城市的消费，间隔不到合理通勤时间。
    （MVP: 同一天 + 不同城市 + 非 transport 类别就 flag。
    未来可以接入 receipt 上的消费时间。）"""
    signals = []
    by_date: dict[str, list[SubmissionRow]] = defaultdict(list)
    for s in submissions:
        if s.city:
            by_date[s.date].append(s)

    for dt, group in by_date.items():
        non_transport = [s for s in group if s.category != "transport"]
        cities = {s.city for s in non_transport}
        if len(cities) > 1:
            signals.append(FraudSignal(
                rule="timestamp_conflict",
                score=70,
                evidence=f"{dt} 不同城市 {cities} 有非交通消费，间隔不足",
                details={"date": dt, "cities": list(cities)},
            ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 5: 周末/节假日高频报销
# ═══════════════════════════════════════════════════════════════════

def rule_weekend_frequency(
    submissions: Sequence[SubmissionRow],
    employee: EmployeeRow,
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """非销售岗位连续多个周末都有餐饮报销。"""
    if employee.department in config.get("weekend_exempt_depts", []):
        return []

    signals = []
    max_weeks = config["weekend_meal_max_weeks"]
    weekend_weeks: set[str] = set()
    for s in submissions:
        if s.category != "meal":
            continue
        try:
            d = date.fromisoformat(s.date)
        except ValueError:
            continue
        if d.weekday() >= 5:  # Saturday=5, Sunday=6
            iso_week = d.isocalendar()
            weekend_weeks.add(f"{iso_week[0]}-W{iso_week[1]:02d}")

    if len(weekend_weeks) >= max_weeks:
        signals.append(FraudSignal(
            rule="weekend_frequency",
            score=60,
            evidence=f"非销售岗位 {len(weekend_weeks)} 个周末有餐饮报销",
            details={"weeks": sorted(weekend_weeks), "dept": employee.department},
        ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 6: 整数金额聚集
# ═══════════════════════════════════════════════════════════════════

def rule_round_amount(
    submissions: Sequence[SubmissionRow],
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """大量整数金额（200/300/500），大概率编造或凑发票。"""
    signals = []
    min_count = config["round_amount_min_count"]
    pct_threshold = config["round_amount_pct"]

    if len(submissions) < min_count:
        return []

    round_count = sum(1 for s in submissions if s.amount == int(s.amount))
    ratio = round_count / len(submissions)
    if ratio >= pct_threshold and round_count >= min_count:
        signals.append(FraudSignal(
            rule="round_amount",
            score=55,
            evidence=f"{round_count}/{len(submissions)} 笔 ({ratio:.0%}) 为整数金额",
            details={"round_count": round_count, "total": len(submissions), "ratio": ratio},
        ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 7: 发票连号
# ═══════════════════════════════════════════════════════════════════

def rule_consecutive_invoices(
    all_submissions: Sequence[SubmissionRow],
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """同一商户的多张发票号码连续 → 批量获取发票嫌疑。"""
    signals = []
    min_seq = config["consecutive_invoice_min"]

    by_merchant: dict[str, list[int]] = defaultdict(list)
    for s in all_submissions:
        if s.invoice_number and s.merchant:
            try:
                num = int(s.invoice_number)
                by_merchant[s.merchant].append(num)
            except ValueError:
                continue

    for merchant, nums in by_merchant.items():
        if len(nums) < min_seq:
            continue
        nums_sorted = sorted(set(nums))
        seq_len = 1
        max_seq = 1
        for i in range(1, len(nums_sorted)):
            if nums_sorted[i] == nums_sorted[i - 1] + 1:
                seq_len += 1
                max_seq = max(max_seq, seq_len)
            else:
                seq_len = 1
        if max_seq >= min_seq:
            signals.append(FraudSignal(
                rule="consecutive_invoices",
                score=75,
                evidence=f"商户「{merchant}」有 {max_seq} 张连号发票",
                details={"merchant": merchant, "max_seq": max_seq},
            ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 8: 商户类型与费用类别不匹配
# ═══════════════════════════════════════════════════════════════════

def rule_merchant_category_mismatch(
    submissions: Sequence[SubmissionRow],
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """商户名包含特定关键词，但报销类别与之不匹配。"""
    signals = []
    blocklist = config["merchant_category_blocklist"]

    for s in submissions:
        merchant_lower = s.merchant.lower()
        for keyword, blocked_cats in blocklist.items():
            if keyword in merchant_lower and s.category in blocked_cats:
                signals.append(FraudSignal(
                    rule="merchant_category_mismatch",
                    score=80,
                    evidence=f"商户「{s.merchant}」含关键词「{keyword}」，但类别为 {s.category}",
                    details={"submission_id": s.id, "keyword": keyword, "category": s.category},
                ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 9: 离职前突击报销
# ═══════════════════════════════════════════════════════════════════

def rule_pre_resignation_rush(
    submissions: Sequence[SubmissionRow],
    employee: EmployeeRow,
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """提交离职后，突然提交大量历史积压报销。"""
    if not employee.resignation_date:
        return []

    signals = []
    rush_days = config["rush_days_before_last"]
    multiplier = config["rush_amount_multiplier"]
    resign = employee.resignation_date
    window_start = resign - timedelta(days=rush_days)

    rush_subs = []
    normal_subs = []
    for s in submissions:
        try:
            d = date.fromisoformat(s.date)
        except ValueError:
            continue
        if window_start <= d <= resign:
            rush_subs.append(s)
        else:
            normal_subs.append(s)

    if not rush_subs or not normal_subs:
        return signals

    rush_total = sum(s.amount for s in rush_subs)

    all_dates = []
    for s in normal_subs:
        try:
            all_dates.append(date.fromisoformat(s.date))
        except ValueError:
            continue
    if not all_dates:
        return signals

    span_days = max((max(all_dates) - min(all_dates)).days, 30)
    monthly_avg = sum(s.amount for s in normal_subs) / (span_days / 30)

    if rush_total > monthly_avg * multiplier:
        signals.append(FraudSignal(
            rule="pre_resignation_rush",
            score=85,
            evidence=f"离职前 {rush_days} 天报销 {rush_total:.0f}，月均 {monthly_avg:.0f}（{rush_total/monthly_avg:.1f}x）",
            details={
                "rush_total": rush_total,
                "monthly_avg": monthly_avg,
                "rush_count": len(rush_subs),
                "resignation_date": resign.isoformat(),
            },
        ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 10: 汇率套利
# ═══════════════════════════════════════════════════════════════════

def rule_fx_arbitrage(
    submissions: Sequence[SubmissionRow],
    get_market_rate: callable,
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """员工选择的汇率偏离市场汇率超过阈值。

    get_market_rate(from_currency, to_currency) → float
    """
    signals = []
    deviation_pct = config["fx_deviation_pct"]

    for s in submissions:
        if s.exchange_rate is None or s.currency == "CNY":
            continue
        market = get_market_rate(s.currency, "CNY")
        if market <= 0:
            continue
        deviation = abs(s.exchange_rate - market) / market
        if deviation > deviation_pct:
            direction = "高于" if s.exchange_rate > market else "低于"
            signals.append(FraudSignal(
                rule="fx_arbitrage",
                score=70,
                evidence=f"汇率 {s.exchange_rate} {direction}市场价 {market}（偏差 {deviation:.1%}）",
                details={
                    "submission_id": s.id,
                    "used_rate": s.exchange_rate,
                    "market_rate": market,
                    "deviation": deviation,
                },
            ))
    return signals


# ═══════════════════════════════════════════════════════════════════
# 场景 11: 备注模板化（需 LLM 分析结果）
# ═══════════════════════════════════════════════════════════════════

def rule_description_template(
    submissions: Sequence[SubmissionRow],
    llm_analysis: dict,
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """多笔报销的备注措辞高度相似，疑似模板化填写。

    LLM 分析 template_score (0-100) 反映备注的模板化程度。
    超过阈值则 flag。
    """
    threshold = config.get("template_score_threshold", 70)
    score = llm_analysis.get("template_score", 0)
    evidence = llm_analysis.get("template_evidence", "")

    if score < threshold:
        return []

    descs = [s.description for s in submissions if s.description]
    return [FraudSignal(
        rule="description_template",
        score=65,
        evidence=f"备注模板化评分 {score}/100: {evidence}",
        details={"template_score": score, "sample_count": len(descs)},
    )]


# ═══════════════════════════════════════════════════════════════════
# 场景 12: Receipt 与备注矛盾（需 LLM 分析结果）
# ═══════════════════════════════════════════════════════════════════

def rule_receipt_contradiction(
    submissions: Sequence[SubmissionRow],
    llm_analysis: dict,
) -> list[FraudSignal]:
    """Receipt 显示的消费地点与备注描述的地点语义不一致。"""
    if not llm_analysis.get("contradiction_found"):
        return []

    evidence = llm_analysis.get("contradiction_evidence", "receipt 与备注地点不一致")
    return [FraudSignal(
        rule="receipt_contradiction",
        score=70,
        evidence=f"Receipt 与备注矛盾: {evidence}",
        details={"contradiction_evidence": evidence},
    )]


# ═══════════════════════════════════════════════════════════════════
# 场景 13: 人数与金额不匹配（需 LLM 分析结果）
# ═══════════════════════════════════════════════════════════════════

def rule_person_amount_mismatch(
    submissions: Sequence[SubmissionRow],
    llm_analysis: dict,
) -> list[FraudSignal]:
    """备注提及的人数与金额不匹配（人均消费异常高）。"""
    person_count = llm_analysis.get("extracted_person_count")
    reasonable = llm_analysis.get("person_amount_reasonable", True)

    if person_count is None or reasonable:
        return []

    per_person = llm_analysis.get("per_person_amount", 0)
    evidence = llm_analysis.get("person_amount_evidence", "")
    return [FraudSignal(
        rule="person_amount_mismatch",
        score=60,
        evidence=f"备注 {person_count} 人, 人均 {per_person:.0f}: {evidence}",
        details={
            "person_count": person_count,
            "per_person_amount": per_person,
        },
    )]


# ═══════════════════════════════════════════════════════════════════
# 场景 14: 模糊事由掩盖消费性质（需 LLM 分析结果）
# ═══════════════════════════════════════════════════════════════════

def rule_vague_description(
    submissions: Sequence[SubmissionRow],
    llm_analysis: dict,
    config: dict = DEFAULT_CONFIG,
) -> list[FraudSignal]:
    """备注过于模糊，且类别属于高风险类别（礼品、娱乐等），可能在掩盖消费性质。"""
    threshold = config.get("vagueness_threshold", 60)
    suspicious_cats = config.get("vagueness_suspicious_categories",
                                  ["gift", "entertainment", "supplies", "other"])
    vagueness = llm_analysis.get("vagueness_score", 0)

    if vagueness < threshold:
        return []

    # Only flag if the category is one that benefits from vague descriptions
    flagged = [s for s in submissions if s.category in suspicious_cats]
    if not flagged:
        return []

    evidence = llm_analysis.get("vagueness_evidence", "")
    return [FraudSignal(
        rule="vague_description",
        score=60,
        evidence=f"备注模糊度 {vagueness}/100 且类别为 {flagged[0].category}: {evidence}",
        details={"vagueness_score": vagueness, "category": flagged[0].category},
    )]
