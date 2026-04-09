"""模糊/歧义检测器——识别需要人工复核的边界情况。

五维评分模型（加权求和 0-100）：
1. 描述模糊度 (25%): description <10字 或包含泛化词
2. 金额边界   (20%): 金额在限额的 90%-110% 区间
3. 模式异常   (25%): 同一员工 7天内 ≥3笔相似金额(±15%)的同类型费用
4. 时间异常   (15%): 周末/节假日的工作餐、非工作时间的交通费
5. 城市不匹配 (15%): 原始城市名未被识别，或标准化前后不一致

ambiguity_score:
  <30  → auto_pass（自动通过，附低风险标签）
  30-70 → human_review（标记待人工复核）
  >70  → suggest_reject（建议拒绝）
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from config import ConfigLoader
from models.expense import AmbiguityResult, Employee, LLMReviewResult, LineItem, RuleResult
from rules.policy_engine import PolicyEngine


# 泛化词列表——描述中出现这些词视为模糊
_VAGUE_WORDS: list[str] = [
    "相关费用", "其他", "杂项", "若干", "一批", "等等",
    "费用", "相关", "补贴", "报销",
]

# 因素权重
_WEIGHTS = {
    "description_vague": 0.25,
    "amount_boundary":   0.20,
    "pattern_anomaly":   0.25,
    "time_anomaly":      0.15,
    "city_mismatch":     0.15,
}


class AmbiguityDetector:
    """检测报销行项目中的模糊或歧义信息。"""

    def __init__(self, config_loader: ConfigLoader) -> None:
        self._loader = config_loader
        self._engine = PolicyEngine(config_loader)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        line_item: LineItem,
        employee: Employee,
        rule_results: list[RuleResult],
        history: list[LineItem],
    ) -> AmbiguityResult:
        """对单行项目进行模糊条件评分。

        Args:
            line_item: 待评估的行项目。
            employee: 报销人。
            rule_results: 前序合规检查的 RuleResult 列表（供参考）。
            history: 同一员工的历史行项目（用于模式异常检测）。

        Returns:
            AmbiguityResult，含 0-100 评分、触发因素列表、建议和中文解释。
        """
        factors: dict[str, float] = {}
        triggered: list[str] = []
        explanations: list[str] = []

        # ---- 1. 描述模糊度 (25%) ----
        score_desc = self._score_description(line_item.description)
        factors["description_vague"] = score_desc
        if score_desc > 0:
            triggered.append("description_vague")
            if len(line_item.description) < 10:
                explanations.append(f"描述过短({len(line_item.description)}字)")
            else:
                explanations.append("描述含泛化词")

        # ---- 2. 金额边界 (20%) ----
        score_boundary = self._score_amount_boundary(line_item, employee)
        factors["amount_boundary"] = score_boundary
        if score_boundary > 0:
            triggered.append("amount_boundary")
            explanations.append("金额处于限额边界(90%-110%)")

        # ---- 3. 模式异常 (25%) ----
        score_pattern = self._score_pattern_anomaly(line_item, history)
        factors["pattern_anomaly"] = score_pattern
        if score_pattern > 0:
            triggered.append("pattern_anomaly")
            explanations.append("7天内存在多笔相似金额的同类型费用")

        # ---- 4. 时间异常 (15%) ----
        score_time = self._score_time_anomaly(line_item)
        factors["time_anomaly"] = score_time
        if score_time > 0:
            triggered.append("time_anomaly")
            day_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][line_item.date.weekday()]
            explanations.append(f"费用发生在{day_name}")

        # ---- 5. 城市不匹配 (15%) ----
        score_city = self._score_city_mismatch(line_item)
        factors["city_mismatch"] = score_city
        if score_city > 0:
            triggered.append("city_mismatch")
            normalized = self._engine.city_normalizer.normalize(line_item.city)
            if not self._engine.city_normalizer.is_known(line_item.city):
                explanations.append(f"城市'{line_item.city}'无法识别")
            else:
                explanations.append(f"城市名标准化: '{line_item.city}'→'{normalized}'")

        # ---- 加权汇总 ----
        total_score = sum(
            factors[k] * _WEIGHTS[k] for k in _WEIGHTS
        )
        total_score = round(min(100.0, max(0.0, total_score)), 1)

        # ---- 建议 ----
        if total_score < 30:
            recommendation = "auto_pass"
        elif total_score <= 70:
            recommendation = "human_review"
        else:
            recommendation = "suggest_reject"

        explanation = "; ".join(explanations) if explanations else "未发现异常"

        return AmbiguityResult(
            score=total_score,
            triggered_factors=triggered,
            recommendation=recommendation,
            explanation=explanation,
        )

    async def llm_review(
        self, line_item: LineItem, context: dict,
    ) -> LLMReviewResult:
        """生产环境调用 LLM 做深度语义分析。

        当前版本使用规则评分模型，此接口预留给 Phase 2。
        触发条件：ambiguity_score > 50。
        """
        return LLMReviewResult(
            confidence=0.0,
            recommendation="review",
            reasoning="Phase 2 预留接口，当前版本未实现 LLM 分析",
        )

    # ------------------------------------------------------------------
    # 评分因子
    # ------------------------------------------------------------------

    def _score_description(self, description: str) -> float:
        """描述模糊度评分。

        <10字 → 50分，含泛化词 → 100分，清晰 → 0分。
        """
        if not description or not description.strip():
            return 100.0

        for word in _VAGUE_WORDS:
            if word in description:
                return 100.0

        if len(description.strip()) < 10:
            return 50.0

        return 0.0

    def _score_amount_boundary(self, item: LineItem, employee: Employee) -> float:
        """金额边界评分。

        金额在限额 90%-110% 区间 → 100分，否则 → 0分。
        """
        subtype_cfg = self._engine.get_subtype_config(item.expense_type)
        limit_key = subtype_cfg.get("limit_key")
        if not limit_key:
            return 0.0

        limit = self._engine.get_limit(limit_key, item.city, employee.level.value)
        if limit is None:
            return 0.0

        lower = limit * 0.90
        upper = limit * 1.10
        if lower <= item.amount <= upper:
            return 100.0
        return 0.0

    def _score_pattern_anomaly(self, item: LineItem, history: list[LineItem]) -> float:
        """模式异常评分。

        同一费用类型，7天内 ≥3笔金额在 ±15% 范围内 → 100分。
        """
        if not history:
            return 0.0

        window_start = item.date - timedelta(days=7)
        similar_count = 0
        for h in history:
            if h.expense_type != item.expense_type:
                continue
            if not (window_start <= h.date <= item.date):
                continue
            if item.amount == 0:
                continue
            ratio = abs(h.amount - item.amount) / item.amount
            if ratio <= 0.15:
                similar_count += 1

        if similar_count >= 3:
            return 100.0
        if similar_count == 2:
            return 50.0
        return 0.0

    def _score_time_anomaly(self, item: LineItem) -> float:
        """时间异常评分。

        周末(六/日)的餐费或交通费 → 100分。
        """
        weekday = item.date.weekday()  # 0=Mon, 5=Sat, 6=Sun
        is_weekend = weekday >= 5

        if not is_weekend:
            return 0.0

        # 只对餐费和交通费标记周末异常
        weekend_sensitive_types = {"meals", "transport_local", "client_meal"}
        if item.expense_type in weekend_sensitive_types:
            return 100.0

        return 0.0

    def _score_city_mismatch(self, item: LineItem) -> float:
        """城市不匹配评分。

        城市未识别 → 100分，标准化前后不一致 → 50分，完全匹配 → 0分。
        """
        normalizer = self._engine.city_normalizer
        if not normalizer.is_known(item.city):
            return 100.0

        normalized = normalizer.normalize(item.city)
        if item.city != normalized:
            return 50.0

        return 0.0
