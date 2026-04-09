"""城市名标准化模块——修复Concur系统城市名不一致的核心差异化模块。

Concur 系统的已知缺陷：同一城市在不同报销单中可能出现 "Shanghai"、"shanghai"、
"SH"、"沪" 等多种写法，导致费用标准匹配错误。本模块通过 city_mapping.yaml 配置
将所有别名统一映射为标准中文名，再结合 policy.yaml 的城市分级进行限额查询。
"""

from __future__ import annotations


class CityNormalizer:
    """根据 city_mapping.yaml + policy.yaml 将各种城市别名标准化为统一中文名。

    核心能力:
        normalize("Shanghai") → "上海"
        normalize("SH")       → "上海"
        normalize("沪")       → "上海"
        get_tier("Shanghai")  → "tier_1"
    """

    def __init__(self, city_mapping_config: dict, city_tiers_config: dict) -> None:
        """
        Args:
            city_mapping_config: city_mapping.yaml 的完整内容。
            city_tiers_config: policy.yaml 中的 city_tiers 部分。
        """
        mappings = city_mapping_config.get("mappings", {})
        strategy = city_mapping_config.get("match_strategy", {})
        self._case_sensitive: bool = strategy.get("case_sensitive", False)
        self._unmapped_behavior: str = strategy.get("unmapped_behavior", "flag_for_review")

        # ---------- 构建别名 → 标准名反向索引 ----------
        self._alias_map: dict[str, str] = {}
        for standard_name, aliases in mappings.items():
            self._alias_map[self._key(standard_name)] = standard_name
            for alias in aliases:
                self._alias_map[self._key(alias)] = standard_name

        # ---------- 构建标准名 → tier 索引 ----------
        self._city_to_tier: dict[str, str] = {}
        for tier_name, tier_data in city_tiers_config.items():
            for city in tier_data.get("cities", []):
                if city != "*":
                    self._city_to_tier[city] = tier_name
        self._default_tier = "tier_3"  # 通配符 "*" 对应的默认等级

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def normalize(self, city_name: str) -> str:
        """将任意城市名（中文/英文/缩写/别称）标准化为中文标准名。

        未映射的城市名原样返回。
        """
        result = self._alias_map.get(self._key(city_name))
        return result if result is not None else city_name

    def get_tier(self, city_name: str) -> str:
        """返回城市所属等级：tier_1 / tier_2 / tier_3。

        先标准化城市名，再查分级表。未映射城市归入 tier_3。
        """
        normalized = self.normalize(city_name)
        return self._city_to_tier.get(normalized, self._default_tier)

    def is_known(self, city_name: str) -> bool:
        """该城市名是否能在映射表中找到（包括标准名和别名）。"""
        return self._key(city_name) in self._alias_map

    def needs_review(self, city_name: str) -> bool:
        """未映射的城市名是否需要触发人工复核（由 unmapped_behavior 配置决定）。"""
        return not self.is_known(city_name) and self._unmapped_behavior == "flag_for_review"

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _key(self, name: str) -> str:
        """生成查找用的 key（根据 case_sensitive 配置决定是否转小写）。"""
        return name if self._case_sensitive else name.lower()
