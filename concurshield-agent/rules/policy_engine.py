"""策略引擎——从 YAML 加载规则并执行费用合规检查。

所有业务数字和城市名全部从配置读取，零硬编码。
如果要把一线城市住宿 L1 限额从 500 改成 600，只需改 policy.yaml。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from config import ConfigLoader
from models.enums import ComplianceLevel
from models.expense import ApprovalStep, Employee, Invoice, RuleResult
from rules.city_normalizer import CityNormalizer


class PolicyEngine:
    """根据配置执行费用限额检查、审批链计算和发票校验。"""

    def __init__(self, config_loader: ConfigLoader) -> None:
        self._loader = config_loader
        self._policy = config_loader.get("policy")
        self._approval = config_loader.get("approval_flow")
        self._expense_types = config_loader.get("expense_types")
        self._tolerance = self._policy.get("tolerance", {})

        self._city_normalizer = CityNormalizer(
            config_loader.get("city_mapping"),
            self._policy.get("city_tiers", {}),
        )

        # 构建 subtype_id → parent_type 索引（如 "accommodation" → "travel"）
        self._subtype_to_parent: dict[str, str] = {}
        for parent_type, type_data in self._expense_types.get("expense_types", {}).items():
            for subtype in type_data.get("subtypes", []):
                self._subtype_to_parent[subtype["id"]] = parent_type

        # 构建 subtype_id → subtype config 索引
        self._subtype_config: dict[str, dict] = {}
        for type_data in self._expense_types.get("expense_types", {}).values():
            for subtype in type_data.get("subtypes", []):
                self._subtype_config[subtype["id"]] = subtype

    # ------------------------------------------------------------------
    # 公共属性
    # ------------------------------------------------------------------

    @property
    def city_normalizer(self) -> CityNormalizer:
        return self._city_normalizer

    # ------------------------------------------------------------------
    # 费用限额
    # ------------------------------------------------------------------

    def get_limit(
        self, expense_type: str, city: str, employee_level: str,
    ) -> Optional[float]:
        """查询费用限额。

        通过 CityNormalizer 标准化城市 → 查 tier → 查限额矩阵。
        返回 None 表示 "不限"。

        Args:
            expense_type: policy.yaml limits 下的 key，如 "accommodation_per_night"。
            city: 任意格式的城市名（中文/英文/缩写均可）。
            employee_level: "L1" ~ "L4"。
        """
        limits = self._policy.get("limits", {})
        type_limits = limits.get(expense_type)
        if not type_limits:
            return None

        tier = self._city_normalizer.get_tier(city)
        tier_limits = type_limits.get(tier)
        if not tier_limits:
            return None

        value = tier_limits.get(employee_level)
        if value == "不限":
            return None
        return float(value) if value is not None else None

    # ------------------------------------------------------------------
    # 合规判定
    # ------------------------------------------------------------------

    def check_tolerance(self, amount: float, limit: float) -> ComplianceLevel:
        """基于 tolerance 配置判断合规等级。

        Args:
            amount: 实际金额。
            limit: 限额（调用者已确认不为 None / "不限"）。

        Returns:
            A — 合规（amount ≤ limit）
            B — 超标但在容忍度内（警告通过）
            C — 超标超出容忍度（拒绝）
        """
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

    # ------------------------------------------------------------------
    # 审批链
    # ------------------------------------------------------------------

    def get_approval_chain(
        self,
        expense_type: str,
        amount: float,
        employee_level: str,
    ) -> list[ApprovalStep]:
        """基于 approval_flow.yaml + level_overrides 计算审批链。

        Args:
            expense_type: 父类型（"travel" / "entertainment"）或子类型 id。
                          若传入子类型 id，自动解析为父类型。
            amount: 报销金额。
            employee_level: "L1" ~ "L4"。

        Returns:
            有序的审批步骤列表。空列表表示自动通过。
        """
        # 子类型 → 父类型
        parent_type = self._subtype_to_parent.get(expense_type, expense_type)

        # ---- level_overrides: 自动审批 ----
        overrides = self._approval.get("level_overrides", {}).get(employee_level, {})
        auto_approve_below = overrides.get("auto_approve_below")
        if auto_approve_below is not None and amount < auto_approve_below:
            return [ApprovalStep(
                approver_role="auto",
                time_limit_hours=0,
                is_auto_approved=True,
            )]

        # ---- 查找匹配的审批规则 ----
        rules = self._approval.get("approval_rules", [])
        matched_rule = None
        for rule in rules:
            if rule["expense_type"] == parent_type:
                matched_rule = rule
                break

        if matched_rule is None:
            return []

        # ---- 构建累积审批链 ----
        # 审批链是累积的：金额越大，需要的审批层级越多
        chain: list[ApprovalStep] = []
        for condition in matched_rule.get("conditions", []):
            chain.append(ApprovalStep(
                approver_role=condition["approver_role"],
                time_limit_hours=condition.get("time_limit_hours", 24),
            ))
            amount_max = condition.get("amount_max")
            if amount_max is not None and amount <= amount_max:
                break

        # ---- level_overrides: 跳级审批 ----
        skip_direct_manager = overrides.get("skip_direct_manager", False)
        if skip_direct_manager:
            chain = [
                step for step in chain
                if step.approver_role != "direct_manager"
            ]

        return chain

    # ------------------------------------------------------------------
    # 发票校验
    # ------------------------------------------------------------------

    def validate_invoice(
        self,
        invoice: Invoice,
        employee: Employee,
        history: list[Invoice],
    ) -> list[RuleResult]:
        """发票校验规则集合。

        校验项:
        1. 金额 > 0
        2. 日期不在未来
        3. 日期不超过 1 年（防止过期发票报销）
        4. 重复发票检测（同一 invoice_code + invoice_number）
        5. 专票税额合理性
        6. 城市名可识别

        Args:
            invoice: 待校验的发票。
            employee: 报销人。
            history: 历史已提交的发票列表，用于查重。

        Returns:
            校验结果列表（包含通过和未通过的项）。
        """
        results: list[RuleResult] = []

        # ---- 规则 1: 金额正数 ----
        results.append(RuleResult(
            rule_name="amount_positive",
            passed=invoice.amount > 0,
            message="发票金额必须大于0" if invoice.amount <= 0 else "金额校验通过",
            severity="error" if invoice.amount <= 0 else "info",
        ))

        # ---- 规则 2: 日期不在未来 ----
        today = date.today()
        is_future = invoice.date > today
        results.append(RuleResult(
            rule_name="date_not_future",
            passed=not is_future,
            message=f"发票日期 {invoice.date} 晚于今天" if is_future else "日期校验通过",
            severity="error" if is_future else "info",
        ))

        # ---- 规则 3: 日期不超过 1 年 ----
        one_year_ago = today - timedelta(days=365)
        is_expired = invoice.date < one_year_ago
        results.append(RuleResult(
            rule_name="date_not_expired",
            passed=not is_expired,
            message=f"发票日期 {invoice.date} 已超过1年" if is_expired else "有效期校验通过",
            severity="error" if is_expired else "info",
        ))

        # ---- 规则 4: 重复发票检测 ----
        invoice_key = (invoice.invoice_code, invoice.invoice_number)
        is_duplicate = any(
            (h.invoice_code, h.invoice_number) == invoice_key
            for h in history
        )
        results.append(RuleResult(
            rule_name="no_duplicate",
            passed=not is_duplicate,
            message=f"发票 {invoice.invoice_code}-{invoice.invoice_number} 已被提交过"
            if is_duplicate else "查重校验通过",
            severity="error" if is_duplicate else "info",
        ))

        # ---- 规则 5: 专票税额合理性 ----
        vat_config = self._expense_types.get("vat_special_invoice", {})
        if vat_config.get("split_tax") and invoice.invoice_type.value == "专票":
            tax_ok = 0 < invoice.tax_amount <= invoice.amount
            results.append(RuleResult(
                rule_name="vat_tax_valid",
                passed=tax_ok,
                message="专票税额必须大于0且不超过金额" if not tax_ok else "专票税额校验通过",
                severity="error" if not tax_ok else "info",
            ))

        # ---- 规则 6: 城市名可识别 ----
        city_known = self._city_normalizer.is_known(invoice.city)
        results.append(RuleResult(
            rule_name="city_recognized",
            passed=city_known,
            message=f"城市名 '{invoice.city}' 无法识别，需人工复核"
            if not city_known else f"城市 '{invoice.city}' → '{self._city_normalizer.normalize(invoice.city)}'",
            severity="warning" if not city_known else "info",
        ))

        return results
