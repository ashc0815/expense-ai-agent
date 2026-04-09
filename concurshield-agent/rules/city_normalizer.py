"""城市名标准化模块——修复Concur系统城市名不一致的核心差异化模块。"""

from __future__ import annotations

from typing import Optional


class CityNormalizer:
    """根据 city_mapping.yaml 配置将各种城市别名标准化为统一名称。"""

    def __init__(self, config: dict) -> None:
        mappings = config.get("mappings", {})
        self._case_sensitive = config.get("match_strategy", {}).get("case_sensitive", False)
        self._unmapped_behavior = config.get("match_strategy", {}).get("unmapped_behavior", "flag_for_review")

        # 构建反向索引：别名 → 标准名
        self._alias_map: dict[str, str] = {}
        for standard_name, aliases in mappings.items():
            key = standard_name if self._case_sensitive else standard_name.lower()
            self._alias_map[key] = standard_name
            for alias in aliases:
                akey = alias if self._case_sensitive else alias.lower()
                self._alias_map[akey] = standard_name

    def normalize(self, city_name: str) -> tuple[str, bool]:
        """标准化城市名。

        Returns:
            (标准化名称, 是否成功匹配)。
            未匹配时返回原名和 False。
        """
        key = city_name if self._case_sensitive else city_name.lower()
        if key in self._alias_map:
            return self._alias_map[key], True
        return city_name, False

    def needs_review(self, city_name: str) -> bool:
        """检查该城市名是否需要人工复核。"""
        _, matched = self.normalize(city_name)
        return not matched and self._unmapped_behavior == "flag_for_review"
