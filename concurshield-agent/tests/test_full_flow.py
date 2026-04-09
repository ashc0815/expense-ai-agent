"""端到端流程测试 + CityNormalizer / PolicyEngine 单元测试。"""

import sys
from datetime import date, timedelta
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from config import ConfigLoader
from agent.controller import AgentController
from mock_data.sample_reports import create_sample_report, create_over_limit_report
from models.enums import ComplianceLevel, EmployeeLevel, InvoiceType, ReportStatus
from models.expense import Employee, Invoice, ReceiptResult
from rules.city_normalizer import CityNormalizer
from rules.policy_engine import PolicyEngine
from skills.skill_01_receipt import process as receipt_process


def _make_loader() -> ConfigLoader:
    ConfigLoader.reset()
    loader = ConfigLoader()
    loader.load()
    return loader


# ======================================================================
# ConfigLoader
# ======================================================================

class TestConfigLoader(unittest.TestCase):
    def test_load_all_configs(self):
        loader = _make_loader()
        self.assertIn("employee_levels", loader.get("policy"))
        self.assertIn("mappings", loader.get("city_mapping"))
        self.assertIn("approval_rules", loader.get("approval_flow"))
        self.assertIn("expense_types", loader.get("expense_types"))
        self.assertIn("pipeline", loader.get("workflow"))


# ======================================================================
# CityNormalizer
# ======================================================================

class TestCityNormalizer(unittest.TestCase):
    def setUp(self):
        loader = _make_loader()
        self._normalizer = CityNormalizer(
            loader.get("city_mapping"),
            loader.get("policy").get("city_tiers", {}),
        )

    # --- normalize ---

    def test_normalize_english(self):
        self.assertEqual(self._normalizer.normalize("Shanghai"), "上海")

    def test_normalize_english_lowercase(self):
        self.assertEqual(self._normalizer.normalize("shanghai"), "上海")

    def test_normalize_abbreviation(self):
        self.assertEqual(self._normalizer.normalize("SH"), "上海")

    def test_normalize_chinese_standard(self):
        self.assertEqual(self._normalizer.normalize("上海"), "上海")

    def test_normalize_chinese_alias(self):
        self.assertEqual(self._normalizer.normalize("沪"), "上海")

    def test_normalize_beijing(self):
        self.assertEqual(self._normalizer.normalize("Beijing"), "北京")
        self.assertEqual(self._normalizer.normalize("BJ"), "北京")
        self.assertEqual(self._normalizer.normalize("京"), "北京")

    def test_normalize_chengdu_alias(self):
        self.assertEqual(self._normalizer.normalize("蓉"), "成都")

    def test_normalize_shenzhen(self):
        self.assertEqual(self._normalizer.normalize("鹏城"), "深圳")

    def test_normalize_unknown_passthrough(self):
        self.assertEqual(self._normalizer.normalize("UnknownCity"), "UnknownCity")

    # --- get_tier ---

    def test_tier_1_chinese(self):
        self.assertEqual(self._normalizer.get_tier("上海"), "tier_1")

    def test_tier_1_english(self):
        self.assertEqual(self._normalizer.get_tier("Shanghai"), "tier_1")

    def test_tier_1_abbreviation(self):
        self.assertEqual(self._normalizer.get_tier("SH"), "tier_1")

    def test_tier_1_lowercase(self):
        self.assertEqual(self._normalizer.get_tier("shanghai"), "tier_1")

    def test_tier_2_city(self):
        self.assertEqual(self._normalizer.get_tier("成都"), "tier_2")
        self.assertEqual(self._normalizer.get_tier("Chengdu"), "tier_2")
        self.assertEqual(self._normalizer.get_tier("蓉"), "tier_2")

    def test_tier_3_unknown(self):
        self.assertEqual(self._normalizer.get_tier("UnknownCity"), "tier_3")

    def test_all_tier_1_cities(self):
        for city in ["北京", "上海", "广州", "深圳", "杭州"]:
            self.assertEqual(self._normalizer.get_tier(city), "tier_1", f"{city} should be tier_1")

    def test_all_tier_2_cities(self):
        for city in ["成都", "武汉", "南京", "重庆", "西安", "苏州", "长沙", "郑州"]:
            self.assertEqual(self._normalizer.get_tier(city), "tier_2", f"{city} should be tier_2")

    # --- is_known ---

    def test_is_known_standard(self):
        self.assertTrue(self._normalizer.is_known("上海"))

    def test_is_known_alias(self):
        self.assertTrue(self._normalizer.is_known("Shanghai"))
        self.assertTrue(self._normalizer.is_known("SH"))
        self.assertTrue(self._normalizer.is_known("沪"))

    def test_is_known_false(self):
        self.assertFalse(self._normalizer.is_known("UnknownCity"))

    # --- needs_review ---

    def test_needs_review_unknown(self):
        self.assertTrue(self._normalizer.needs_review("UnknownCity"))

    def test_needs_review_known(self):
        self.assertFalse(self._normalizer.needs_review("Shanghai"))


# ======================================================================
# PolicyEngine — 费用限额
# ======================================================================

class TestPolicyEngineLimits(unittest.TestCase):
    def setUp(self):
        self._engine = PolicyEngine(_make_loader())

    def test_limit_accommodation_tier1_L1(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "上海", "L1"), 500.0)

    def test_limit_via_english_city(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "Shanghai", "L1"), 500.0)

    def test_limit_via_abbreviation(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "SH", "L1"), 500.0)

    def test_limit_tier2(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "成都", "L1"), 350.0)

    def test_limit_tier3(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "UnknownCity", "L1"), 250.0)

    def test_limit_unlimited(self):
        self.assertIsNone(self._engine.get_limit("accommodation_per_night", "上海", "L4"))

    def test_limit_meals(self):
        self.assertEqual(self._engine.get_limit("meals_per_person", "上海", "L1"), 100.0)
        self.assertEqual(self._engine.get_limit("meals_per_person", "成都", "L2"), 100.0)

    def test_limit_transport(self):
        self.assertEqual(self._engine.get_limit("local_transport_per_day", "上海", "L1"), 200.0)
        self.assertIsNone(self._engine.get_limit("local_transport_per_day", "上海", "L3"))

    def test_limit_nonexistent_type(self):
        self.assertIsNone(self._engine.get_limit("nonexistent_type", "上海", "L1"))


# ======================================================================
# PolicyEngine — 合规判定
# ======================================================================

class TestPolicyEngineTolerance(unittest.TestCase):
    def setUp(self):
        self._engine = PolicyEngine(_make_loader())

    def test_under_limit(self):
        self.assertEqual(self._engine.check_tolerance(400, 500), ComplianceLevel.A)

    def test_at_limit(self):
        self.assertEqual(self._engine.check_tolerance(500, 500), ComplianceLevel.A)

    def test_over_within_tolerance(self):
        # 超标 30，容忍度 50 → B
        self.assertEqual(self._engine.check_tolerance(530, 500), ComplianceLevel.B)

    def test_over_at_tolerance_boundary(self):
        # 超标 50，容忍度 50 → B（≤ threshold）
        self.assertEqual(self._engine.check_tolerance(550, 500), ComplianceLevel.B)

    def test_over_beyond_tolerance(self):
        # 超标 51 → C
        self.assertEqual(self._engine.check_tolerance(551, 500), ComplianceLevel.C)

    def test_over_way_beyond(self):
        self.assertEqual(self._engine.check_tolerance(1000, 500), ComplianceLevel.C)


# ======================================================================
# PolicyEngine — 审批链
# ======================================================================

class TestPolicyEngineApprovalChain(unittest.TestCase):
    def setUp(self):
        self._engine = PolicyEngine(_make_loader())

    def test_travel_small_amount_L1(self):
        # ≤2000 → [direct_manager]
        chain = self._engine.get_approval_chain("travel", 1500, "L1")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].approver_role, "direct_manager")
        self.assertFalse(chain[0].is_auto_approved)

    def test_travel_medium_amount_L1(self):
        # ≤10000 → [direct_manager, department_head]
        chain = self._engine.get_approval_chain("travel", 5000, "L1")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0].approver_role, "direct_manager")
        self.assertEqual(chain[1].approver_role, "department_head")

    def test_travel_large_amount_L1(self):
        # >10000 → [direct_manager, department_head, vp]
        chain = self._engine.get_approval_chain("travel", 15000, "L1")
        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[2].approver_role, "vp")
        self.assertEqual(chain[2].time_limit_hours, 48)

    def test_L3_skip_direct_manager(self):
        # L3 跳过 direct_manager
        chain = self._engine.get_approval_chain("travel", 5000, "L3")
        roles = [s.approver_role for s in chain]
        self.assertNotIn("direct_manager", roles)
        self.assertIn("department_head", roles)

    def test_L4_auto_approve_below_threshold(self):
        # L4 5000以下自动通过
        chain = self._engine.get_approval_chain("travel", 3000, "L4")
        self.assertEqual(len(chain), 1)
        self.assertTrue(chain[0].is_auto_approved)
        self.assertEqual(chain[0].approver_role, "auto")

    def test_L4_above_threshold_needs_approval(self):
        # L4 ≥5000 走正常审批（但跳过 direct_manager — L4 没有 skip 配置所以不跳）
        chain = self._engine.get_approval_chain("travel", 8000, "L4")
        self.assertTrue(len(chain) >= 1)
        self.assertFalse(any(s.is_auto_approved for s in chain))

    def test_entertainment(self):
        # 招待费 ≤5000 → department_head
        chain = self._engine.get_approval_chain("entertainment", 3000, "L1")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].approver_role, "department_head")

    def test_subtype_resolves_to_parent(self):
        # "accommodation" → "travel"
        chain = self._engine.get_approval_chain("accommodation", 1500, "L1")
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].approver_role, "direct_manager")

    def test_unknown_expense_type(self):
        chain = self._engine.get_approval_chain("unknown_type", 1000, "L1")
        self.assertEqual(chain, [])


# ======================================================================
# PolicyEngine — 发票校验
# ======================================================================

class TestPolicyEngineInvoiceValidation(unittest.TestCase):
    def setUp(self):
        self._engine = PolicyEngine(_make_loader())
        self._employee = Employee(
            name="李四", id="EMP-002", department="研发部",
            city="北京", hire_date=date(2023, 1, 1),
            bank_account="6222-0000-0002-3456", level=EmployeeLevel.L2,
        )

    def _make_invoice(self, **overrides) -> Invoice:
        defaults = dict(
            invoice_code="031001900300",
            invoice_number="99990001",
            invoice_type=InvoiceType.NORMAL,
            amount=200.0,
            tax_amount=0.0,
            date=date(2025, 6, 1),
            vendor="测试商户",
            city="北京",
            items=["测试项"],
        )
        defaults.update(overrides)
        return Invoice(**defaults)

    def test_valid_invoice_all_pass(self):
        inv = self._make_invoice()
        results = self._engine.validate_invoice(inv, self._employee, [])
        failed = [r for r in results if not r.passed]
        self.assertEqual(failed, [])

    def test_zero_amount_fails(self):
        inv = self._make_invoice(amount=0)
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "amount_positive")
        self.assertFalse(r.passed)

    def test_negative_amount_fails(self):
        inv = self._make_invoice(amount=-100)
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "amount_positive")
        self.assertFalse(r.passed)

    def test_future_date_fails(self):
        inv = self._make_invoice(date=date.today() + timedelta(days=30))
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "date_not_future")
        self.assertFalse(r.passed)

    def test_expired_date_fails(self):
        inv = self._make_invoice(date=date.today() - timedelta(days=400))
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "date_not_expired")
        self.assertFalse(r.passed)

    def test_duplicate_invoice_fails(self):
        inv = self._make_invoice()
        history = [self._make_invoice()]  # 同一张发票
        results = self._engine.validate_invoice(inv, self._employee, history)
        r = next(r for r in results if r.rule_name == "no_duplicate")
        self.assertFalse(r.passed)

    def test_no_duplicate_different_number(self):
        inv = self._make_invoice()
        history = [self._make_invoice(invoice_number="88880001")]
        results = self._engine.validate_invoice(inv, self._employee, history)
        r = next(r for r in results if r.rule_name == "no_duplicate")
        self.assertTrue(r.passed)

    def test_vat_special_valid(self):
        inv = self._make_invoice(
            invoice_type=InvoiceType.SPECIAL, amount=500, tax_amount=30,
        )
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "vat_tax_valid")
        self.assertTrue(r.passed)

    def test_vat_special_zero_tax_fails(self):
        inv = self._make_invoice(
            invoice_type=InvoiceType.SPECIAL, amount=500, tax_amount=0,
        )
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "vat_tax_valid")
        self.assertFalse(r.passed)

    def test_unknown_city_warning(self):
        inv = self._make_invoice(city="SomeRandomPlace")
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "city_recognized")
        self.assertFalse(r.passed)
        self.assertEqual(r.severity, "warning")

    def test_known_city_alias_passes(self):
        inv = self._make_invoice(city="Shanghai")
        results = self._engine.validate_invoice(inv, self._employee, [])
        r = next(r for r in results if r.rule_name == "city_recognized")
        self.assertTrue(r.passed)


# ======================================================================
# Skill 01: 发票收据验证
# ======================================================================

class TestSkill01ReceiptBase(unittest.TestCase):
    """skill_01_receipt 测试的公共基类。"""

    def setUp(self):
        _make_loader()  # 确保 ConfigLoader 单例已初始化
        self._employee = Employee(
            name="张三", id="EMP-001", department="销售部",
            city="上海", hire_date=date(2022, 3, 15),
            bank_account="6222-0000-0001-2345", level=EmployeeLevel.L1,
        )

    def _make_invoice(self, **overrides) -> Invoice:
        defaults = dict(
            invoice_code="031001900211",
            invoice_number="12345678",
            invoice_type=InvoiceType.NORMAL,
            amount=200.0,
            tax_amount=0.0,
            date=date(2025, 6, 1),
            vendor="测试商户",
            city="Shanghai",
            items=["测试项"],
            buyer_name="示例科技有限公司",
        )
        defaults.update(overrides)
        return Invoice(**defaults)


class TestSkill01PassingInvoice(TestSkill01ReceiptBase):
    """Mock 发票 1: 完全合规，全部通过。"""

    def test_all_checks_pass(self):
        inv = self._make_invoice()
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        self.assertIsInstance(result, ReceiptResult)
        self.assertTrue(result.passed)
        failed = [c for c in result.checks if not c.passed and c.severity == "error"]
        self.assertEqual(failed, [])

    def test_normalized_city(self):
        inv = self._make_invoice(city="Shanghai")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        self.assertEqual(result.normalized_city, "上海")

    def test_normalized_city_abbreviation(self):
        inv = self._make_invoice(city="SH")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        self.assertEqual(result.normalized_city, "上海")

    def test_format_code_11_digits(self):
        inv = self._make_invoice(invoice_code="03100190021")  # 11位
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        code_check = next(c for c in result.checks if c.rule_name == "format_code")
        self.assertTrue(code_check.passed)

    def test_format_code_12_digits(self):
        inv = self._make_invoice(invoice_code="031001900211")  # 12位
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        code_check = next(c for c in result.checks if c.rule_name == "format_code")
        self.assertTrue(code_check.passed)

    def test_buyer_name_match(self):
        inv = self._make_invoice(buyer_name="示例科技有限公司")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        buyer_check = next(c for c in result.checks if c.rule_name == "buyer_name_match")
        self.assertTrue(buyer_check.passed)


class TestSkill01DuplicateInvoice(TestSkill01ReceiptBase):
    """Mock 发票 2: 重复发票，查重失败。"""

    def test_duplicate_fails(self):
        inv = self._make_invoice()
        history = [self._make_invoice()]  # 同一张发票已在历史库中
        result = receipt_process(inv, self._employee, history, submit_date=date(2025, 7, 1))
        self.assertFalse(result.passed)
        dup_check = next(c for c in result.checks if c.rule_name == "no_duplicate")
        self.assertFalse(dup_check.passed)
        self.assertEqual(dup_check.severity, "error")

    def test_duplicate_different_number_passes(self):
        inv = self._make_invoice()
        history = [self._make_invoice(invoice_number="99990001")]
        result = receipt_process(inv, self._employee, history, submit_date=date(2025, 7, 1))
        dup_check = next(c for c in result.checks if c.rule_name == "no_duplicate")
        self.assertTrue(dup_check.passed)

    def test_duplicate_different_code_passes(self):
        inv = self._make_invoice()
        history = [self._make_invoice(invoice_code="099999900000")]
        result = receipt_process(inv, self._employee, history, submit_date=date(2025, 7, 1))
        dup_check = next(c for c in result.checks if c.rule_name == "no_duplicate")
        self.assertTrue(dup_check.passed)


class TestSkill01DateAnomaly(TestSkill01ReceiptBase):
    """Mock 发票 3: 日期异常。"""

    def test_date_before_hire_fails(self):
        # 员工 2022-03-15 入职，发票日期 2021-01-01
        inv = self._make_invoice(date=date(2021, 1, 1))
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        self.assertFalse(result.passed)
        hire_check = next(c for c in result.checks if c.rule_name == "date_after_hire")
        self.assertFalse(hire_check.passed)
        self.assertEqual(hire_check.severity, "error")

    def test_date_after_submit_fails(self):
        # 提交日期 2025-07-01，发票日期 2025-08-01
        inv = self._make_invoice(date=date(2025, 8, 1))
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        self.assertFalse(result.passed)
        submit_check = next(c for c in result.checks if c.rule_name == "date_before_submit")
        self.assertFalse(submit_check.passed)
        self.assertEqual(submit_check.severity, "error")

    def test_date_on_hire_day_passes(self):
        inv = self._make_invoice(date=date(2022, 3, 15))  # 恰好入职当天
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        hire_check = next(c for c in result.checks if c.rule_name == "date_after_hire")
        self.assertTrue(hire_check.passed)

    def test_date_on_submit_day_passes(self):
        inv = self._make_invoice(date=date(2025, 7, 1))  # 恰好提交当天
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        submit_check = next(c for c in result.checks if c.rule_name == "date_before_submit")
        self.assertTrue(submit_check.passed)


class TestSkill01FormatValidation(TestSkill01ReceiptBase):
    """格式校验边界测试。"""

    def test_code_too_short(self):
        inv = self._make_invoice(invoice_code="12345")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        code_check = next(c for c in result.checks if c.rule_name == "format_code")
        self.assertFalse(code_check.passed)

    def test_code_non_numeric(self):
        inv = self._make_invoice(invoice_code="0310019AB11")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        code_check = next(c for c in result.checks if c.rule_name == "format_code")
        self.assertFalse(code_check.passed)

    def test_number_too_short(self):
        inv = self._make_invoice(invoice_number="1234")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        num_check = next(c for c in result.checks if c.rule_name == "format_number")
        self.assertFalse(num_check.passed)

    def test_number_too_long(self):
        inv = self._make_invoice(invoice_number="123456789")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        num_check = next(c for c in result.checks if c.rule_name == "format_number")
        self.assertFalse(num_check.passed)

    def test_buyer_name_mismatch(self):
        inv = self._make_invoice(buyer_name="其他公司名称")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        buyer_check = next(c for c in result.checks if c.rule_name == "buyer_name_match")
        self.assertFalse(buyer_check.passed)

    def test_buyer_name_empty_warning(self):
        inv = self._make_invoice(buyer_name="")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        buyer_check = next(c for c in result.checks if c.rule_name == "buyer_name_match")
        self.assertFalse(buyer_check.passed)
        self.assertEqual(buyer_check.severity, "warning")
        # warning 不影响整体 passed（其他全通过的情况下）
        error_failures = [c for c in result.checks if not c.passed and c.severity == "error"]
        self.assertEqual(error_failures, [])
        self.assertTrue(result.passed)

    def test_unknown_city_warning_still_passes(self):
        inv = self._make_invoice(city="SomeRandomPlace")
        result = receipt_process(inv, self._employee, [], submit_date=date(2025, 7, 1))
        city_check = next(c for c in result.checks if c.rule_name == "city_recognized")
        self.assertFalse(city_check.passed)
        self.assertEqual(city_check.severity, "warning")
        # warning 不影响整体 passed
        self.assertTrue(result.passed)


# ======================================================================
# 端到端流程
# ======================================================================

class TestFullFlow(unittest.TestCase):
    def setUp(self):
        self._loader = _make_loader()

    def test_normal_report_flow(self):
        report = create_sample_report()
        controller = AgentController(
            self._loader.get("workflow"),
            self._loader.get_all(),
        )
        result = controller.run(report)
        self.assertTrue(result["success"])
        self.assertEqual(result["final_status"], ReportStatus.PAID.value)
        self.assertEqual(len(result["results"]), 5)

    def test_report_has_processing_log(self):
        report = create_sample_report()
        controller = AgentController(
            self._loader.get("workflow"),
            self._loader.get_all(),
        )
        controller.run(report)
        self.assertEqual(len(report.processing_log), 5)


if __name__ == "__main__":
    unittest.main()
