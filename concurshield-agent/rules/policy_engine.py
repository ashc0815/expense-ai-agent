"""策略引擎——从 YAML 加载规则并执行费用合规检查。"""

from __future__ import annotations

from typing import Optional

from models.enums import ComplianceLevel
from rules.city_normalizer import CityNormalizer


class PolicyEngine:
    """根据 policy.yaml 配置执行费用限额检查。"""

    def __init__(self, policy_config: dict, city_normalizer: CityNormalizer) -> None:
        self._policy = policy_config
        self._city_normalizer = city_normalizer
        self._tolerance = policy_config.get("tolerance", {})

    def get_city_tier(self, city: str) -> str:
        """获取城市所属等级。"""
        normalized, _ = self._city_normalizer.normalize(city)
        tiers = self._policy.get("city_tiers", {})
        for tier_name, tier_data in tiers.items():
            cities = tier_data.get("cities", [])
            if normalized in cities:
                return tier_name
        # 默认归入 tier_3（通配符 "*"）
        return "tier_3"

    def get_limit(self, expense_type: str, city: str, employee_level: str) -> Optional[float]:
        """获取某费用类型在给定城市和员工等级下的限额。

        Returns:
            限额金额，若为"不限"则返回 None。
        """
        limits = self._policy.get("limits", {})
        type_limits = limits.get(expense_type)
        if not type_limits:
            return None

        tier = self.get_city_tier(city)
        tier_limits = type_limits.get(tier)
        if not tier_limits:
            return None

        value = tier_limits.get(employee_level)
        if value == "不限":
            return None
        return float(value) if value is not None else None

    def check_compliance(self, amount: float, limit: Optional[float]) -> ComplianceLevel:
        """检查金额是否合规。

        Returns:
            ComplianceLevel.A — 合规
            ComplianceLevel.B — 超标但在容忍度内（警告通过）
            ComplianceLevel.C — 超标超出容忍度（拒绝）
        """
        if limit is None:
            return ComplianceLevel.A

        if amount <= limit:
            return ComplianceLevel.A

        overage = amount - limit
        warning_threshold = self._tolerance.get("warning_threshold", 50)
        percentage_mode = self._tolerance.get("percentage_mode", False)

        if percentage_mode:
            overage_value = (overage / limit) * 100 if limit > 0 else float("inf")
        else:
            overage_value = overage

        if overage_value <= warning_threshold:
            return ComplianceLevel.B
        return ComplianceLevel.C
