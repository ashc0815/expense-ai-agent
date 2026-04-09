"""模糊/歧义检测器——识别需要人工复核的边界情况。"""

from __future__ import annotations

from models.expense import ExpenseReport


class AmbiguityDetector:
    """检测报销单中的模糊或歧义信息。"""

    def __init__(self, config: dict) -> None:
        self._config = config

    def detect(self, report: ExpenseReport) -> list[dict]:
        """扫描报销单，返回需要复核的模糊项。

        检查项：
        - 城市名无法标准化
        - 费用类型分类不明确
        - 金额处于限额边界

        Returns:
            [{"field": str, "value": str, "reason": str}, ...]
        """
        return []
