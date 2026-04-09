"""ConcurShield Agent 完整测试套件。

7 个端到端场景 + 关键模块单元测试。
pytest 运行: python -m pytest tests/test_full_flow.py -v
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from config import ConfigLoader
from agent.controller import ExpenseController
from agent.ambiguity_detector import AmbiguityDetector
from mock_data.sample_reports import (
    case1_normal_report,
    case2_duplicate_invoice,
    case3_over_limit_reject,
    case4_warning_pass,
    case5_shield_ambiguity,
    case5_with_history,
    case6_pattern_anomaly,
    case7_level_comparison,
    make_employee,
)
from models.enums import ComplianceLevel, EmployeeLevel, FinalStatus, InvoiceType, ReportStatus
from models.expense import (
    Employee, ExpenseReport, Invoice, LineItem,
    ProcessingResult, VoucherResult,
)
from rules.city_normalizer import CityNormalizer
from rules.policy_engine import PolicyEngine
from skills.skill_03_compliance import process as compliance_process
from skills.skill_04_voucher import reset_voucher_seq


def _loader() -> ConfigLoader:
    ConfigLoader.reset()
    loader = ConfigLoader()
    loader.load()
    return loader


# =====================================================================
# Case 1: 正常报销 — 全A级通过
# =====================================================================

class TestCase1NormalReport(unittest.TestCase):
    """L1员工上海出差，住宿480+餐费80，全部在限额内。"""

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)
        reset_voucher_seq()

    def test_final_status_completed(self):
        result = self._ctrl.process_report(case1_normal_report())
        self.assertEqual(result.final_status, FinalStatus.COMPLETED)

    def test_all_five_steps_pass(self):
        result = self._ctrl.process_report(case1_normal_report())
        active = [s for s in result.timeline if not s.skipped]
        self.assertEqual(len(active), 5)
        for step in active:
            self.assertTrue(step.passed, f"{step.display_name} 应通过")

    def test_compliance_level_A(self):
        result = self._ctrl.process_report(case1_normal_report())
        cmp_step = next(s for s in result.timeline if s.skill_name == "compliance")
        cmp_result = cmp_step.detail.get("compliance_result")
        self.assertEqual(cmp_result.overall_level, ComplianceLevel.A)

    def test_voucher_balanced(self):
        result = self._ctrl.process_report(case1_normal_report())
        v_step = next(s for s in result.timeline if s.skill_name == "voucher")
        v_result = v_step.detail.get("voucher_result")
        self.assertTrue(v_result.balanced)
        self.assertEqual(v_result.total_debit, 560.0)

    def test_no_shield(self):
        result = self._ctrl.process_report(case1_normal_report())
        self.assertIsNone(result.shield_report)

    def test_config_snapshot_for_audit(self):
        result = self._ctrl.process_report(case1_normal_report())
        self.assertIn("policy", result.config_snapshot)
        self.assertIn("workflow", result.config_snapshot)


# =====================================================================
# Case 2: 重复发票 — Skill-01 拦截
# =====================================================================

class TestCase2DuplicateInvoice(unittest.TestCase):
    """同一张发票(code+number)提交两次，发票验证阶段拦截。"""

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)

    def test_rejected_at_receipt(self):
        result = self._ctrl.process_report(case2_duplicate_invoice())
        self.assertEqual(result.final_status, FinalStatus.REJECTED)

    def test_receipt_step_fails(self):
        result = self._ctrl.process_report(case2_duplicate_invoice())
        receipt = next(s for s in result.timeline if s.skill_name == "receipt_validation")
        self.assertFalse(receipt.passed)

    def test_pipeline_stops_before_approval(self):
        result = self._ctrl.process_report(case2_duplicate_invoice())
        names = [s.skill_name for s in result.timeline]
        self.assertNotIn("approval", names)

    def test_issues_mention_duplicate(self):
        result = self._ctrl.process_report(case2_duplicate_invoice())
        receipt = next(s for s in result.timeline if s.skill_name == "receipt_validation")
        issues = receipt.detail.get("issues", [])
        self.assertTrue(any("重复" in i or "已被提交" in i for i in issues))


# =====================================================================
# Case 3: 超标拒绝 — C 级
# =====================================================================

class TestCase3OverLimitReject(unittest.TestCase):
    """L1成都住宿420(限额350，超70>50) → C级拒绝。"""

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)

    def test_rejected(self):
        result = self._ctrl.process_report(case3_over_limit_reject())
        self.assertEqual(result.final_status, FinalStatus.REJECTED)

    def test_compliance_level_C(self):
        result = self._ctrl.process_report(case3_over_limit_reject())
        cmp = next(s for s in result.timeline if s.skill_name == "compliance")
        self.assertFalse(cmp.passed)
        cmp_result = cmp.detail.get("compliance_result")
        self.assertEqual(cmp_result.overall_level, ComplianceLevel.C)

    def test_limit_is_350_for_tier2(self):
        """验证成都(tier_2) L1 住宿限额确实是350。"""
        engine = PolicyEngine(self._loader)
        limit = engine.get_limit("accommodation_per_night", "成都", "L1")
        self.assertEqual(limit, 350.0)

    def test_no_voucher_or_payment(self):
        result = self._ctrl.process_report(case3_over_limit_reject())
        names = [s.skill_name for s in result.timeline]
        self.assertNotIn("voucher", names)
        self.assertNotIn("payment", names)


# =====================================================================
# Case 4: 警告通过 — B 级
# =====================================================================

class TestCase4WarningPass(unittest.TestCase):
    """L1成都住宿380(限额350，超30≤50) → B级警告但通过。"""

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)
        reset_voucher_seq()

    def test_completed(self):
        result = self._ctrl.process_report(case4_warning_pass())
        self.assertEqual(result.final_status, FinalStatus.COMPLETED)

    def test_compliance_level_B(self):
        result = self._ctrl.process_report(case4_warning_pass())
        cmp = next(s for s in result.timeline if s.skill_name == "compliance")
        self.assertTrue(cmp.passed)
        cmp_result = cmp.detail.get("compliance_result")
        self.assertEqual(cmp_result.overall_level, ComplianceLevel.B)

    def test_tolerance_boundary(self):
        """验证超标30正好在tolerance 50以内。"""
        engine = PolicyEngine(self._loader)
        level = engine.check_tolerance(380, 350)
        self.assertEqual(level, ComplianceLevel.B)

    def test_still_gets_paid(self):
        result = self._ctrl.process_report(case4_warning_pass())
        payment = next(s for s in result.timeline if s.skill_name == "payment")
        self.assertTrue(payment.passed)


# =====================================================================
# Case 5: ConcurShield — 城市+描述模糊（核心 showcase）
# =====================================================================

class TestCase5ShieldAmbiguity(unittest.TestCase):
    """核心showcase：CityNormalizer + AmbiguityDetector 联动。

    "Shanghai" → CityNormalizer → "上海" tier_1 → 找到限额。
    但多个模糊因素叠加 → shield 触发 → PENDING_REVIEW。
    """

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)

    # ---- CityNormalizer 验证 ----

    def test_normalizer_converts_shanghai(self):
        """CityNormalizer 把 "Shanghai" → "上海"。"""
        normalizer = PolicyEngine(self._loader).city_normalizer
        self.assertEqual(normalizer.normalize("Shanghai"), "上海")
        self.assertEqual(normalizer.get_tier("Shanghai"), "tier_1")

    # ---- 端到端流程 ----

    def test_pending_review(self):
        result = self._ctrl.process_report(case5_shield_ambiguity())
        self.assertEqual(result.final_status, FinalStatus.PENDING_REVIEW)

    def test_report_flagged(self):
        report = case5_shield_ambiguity()
        self._ctrl.process_report(report)
        self.assertEqual(report.status, ReportStatus.FLAGGED)

    def test_shield_report_exists(self):
        result = self._ctrl.process_report(case5_shield_ambiguity())
        self.assertIsNotNone(result.shield_report)
        self.assertTrue(result.shield_report["shield_triggered"])
        self.assertGreater(len(result.shield_report["flagged_items"]), 0)

    def test_shield_stops_before_voucher(self):
        result = self._ctrl.process_report(case5_shield_ambiguity())
        names = [s.skill_name for s in result.timeline]
        self.assertNotIn("voucher", names)
        self.assertNotIn("payment", names)

    # ---- AmbiguityDetector 因素验证 ----

    def test_ambiguity_factors(self):
        """验证触发的具体因素：描述模糊 + 金额边界 + 周末 + 城市不匹配。"""
        result = self._ctrl.process_report(case5_shield_ambiguity())
        item = result.shield_report["flagged_items"][0]
        factors = item["triggered_factors"]
        self.assertIn("description_vague", factors)
        self.assertIn("amount_boundary", factors)
        self.assertIn("time_anomaly", factors)
        self.assertIn("city_mismatch", factors)

    def test_ambiguity_score_human_review(self):
        result = self._ctrl.process_report(case5_shield_ambiguity())
        item = result.shield_report["flagged_items"][0]
        self.assertGreater(item["ambiguity_score"], 50)
        self.assertIn(item["recommendation"], ("human_review", "suggest_reject"))

    def test_missing_attendees_flagged(self):
        """招待费缺参会人名单在extra_checks中标记。"""
        report = case5_shield_ambiguity()
        cmp_result = compliance_process(report)
        detail = cmp_result.line_details[0]
        attendee_checks = [c for c in detail.extra_checks
                           if c.rule_name == "attendee_list_required"]
        self.assertEqual(len(attendee_checks), 1)
        self.assertFalse(attendee_checks[0].passed)

    # ---- 注入历史后 score > 70 ----

    def test_with_history_score_above_70(self):
        """注入历史数据触发模式异常后，score > 70 → suggest_reject。"""
        report = case5_shield_ambiguity()
        history = case5_with_history()
        detector = AmbiguityDetector(self._loader)
        item = report.line_items[0]
        result = detector.evaluate(item, report.employee, [], history)
        self.assertGreater(result.score, 70)
        self.assertEqual(result.recommendation, "suggest_reject")
        self.assertIn("pattern_anomaly", result.triggered_factors)


# =====================================================================
# Case 6: ConcurShield — 模式异常
# =====================================================================

class TestCase6PatternAnomaly(unittest.TestCase):
    """5天内3笔相似金额(88/92/85/90)餐费，每笔在限额内。
    规则引擎全A，但 AmbiguityDetector 检测到模式异常。
    """

    def setUp(self):
        self._loader = _loader()

    def test_compliance_all_A(self):
        """规则引擎看不出问题——金额全部在限额内。"""
        report, _ = case6_pattern_anomaly()
        result = compliance_process(report)
        self.assertEqual(result.overall_level, ComplianceLevel.A)

    def test_pattern_anomaly_detected(self):
        """AmbiguityDetector 用历史数据检测到模式异常。"""
        report, history = case6_pattern_anomaly()
        detector = AmbiguityDetector(self._loader)
        item = report.line_items[0]
        result = detector.evaluate(item, report.employee, [], history)
        self.assertIn("pattern_anomaly", result.triggered_factors)

    def test_score_reflects_pattern(self):
        """模式异常权重25%，单因素 score=25。
        实际业务中会与其他因素叠加（如Case5 score>70）。
        """
        report, history = case6_pattern_anomaly()
        detector = AmbiguityDetector(self._loader)
        item = report.line_items[0]
        result = detector.evaluate(item, report.employee, [], history)
        self.assertGreaterEqual(result.score, 25)
        # 单因素不足以触发 human_review，但已标记模式异常
        self.assertIn("pattern_anomaly", result.triggered_factors)

    def test_explanation_mentions_pattern(self):
        report, history = case6_pattern_anomaly()
        detector = AmbiguityDetector(self._loader)
        result = detector.evaluate(report.line_items[0], report.employee, [], history)
        self.assertIn("相似金额", result.explanation)


# =====================================================================
# Case 7: 员工等级差异 — 配置驱动验证
# =====================================================================

class TestCase7LevelComparison(unittest.TestCase):
    """同一笔住宿500/晚在成都(tier_2)。
    L1限额350 → 超标150 → C级拒绝。
    L2限额500 → 刚好等于限额 → A级通过。
    用同一笔费用、不同员工等级跑两次，验证配置驱动生效。
    """

    def setUp(self):
        self._loader = _loader()
        self._ctrl = ExpenseController(self._loader)
        reset_voucher_seq()

    def test_L1_rejected(self):
        report_l1, _ = case7_level_comparison()
        result = self._ctrl.process_report(report_l1)
        self.assertEqual(result.final_status, FinalStatus.REJECTED)

    def test_L2_completed(self):
        _, report_l2 = case7_level_comparison()
        result = self._ctrl.process_report(report_l2)
        self.assertEqual(result.final_status, FinalStatus.COMPLETED)

    def test_L1_compliance_C(self):
        report_l1, _ = case7_level_comparison()
        result = self._ctrl.process_report(report_l1)
        cmp = next(s for s in result.timeline if s.skill_name == "compliance")
        cmp_result = cmp.detail.get("compliance_result")
        self.assertEqual(cmp_result.overall_level, ComplianceLevel.C)

    def test_L2_compliance_A(self):
        _, report_l2 = case7_level_comparison()
        result = self._ctrl.process_report(report_l2)
        cmp = next(s for s in result.timeline if s.skill_name == "compliance")
        cmp_result = cmp.detail.get("compliance_result")
        self.assertEqual(cmp_result.overall_level, ComplianceLevel.A)

    def test_same_amount_different_outcome(self):
        """同样¥500，L1和L2结果完全不同——纯配置驱动。"""
        report_l1, report_l2 = case7_level_comparison()
        self.assertEqual(report_l1.total_amount, report_l2.total_amount)

        r1 = self._ctrl.process_report(report_l1)
        r2 = self._ctrl.process_report(report_l2)
        self.assertEqual(r1.final_status, FinalStatus.REJECTED)
        self.assertEqual(r2.final_status, FinalStatus.COMPLETED)

    def test_limits_from_config(self):
        """验证限额确实来自配置文件。"""
        engine = PolicyEngine(self._loader)
        l1_limit = engine.get_limit("accommodation_per_night", "成都", "L1")
        l2_limit = engine.get_limit("accommodation_per_night", "成都", "L2")
        self.assertEqual(l1_limit, 350.0)
        self.assertEqual(l2_limit, 500.0)


# =====================================================================
# 核心模块单元测试
# =====================================================================

class TestCityNormalizer(unittest.TestCase):
    def setUp(self):
        loader = _loader()
        self._n = CityNormalizer(
            loader.get("city_mapping"),
            loader.get("policy").get("city_tiers", {}),
        )

    def test_english_to_chinese(self):
        self.assertEqual(self._n.normalize("Shanghai"), "上海")

    def test_abbreviation(self):
        self.assertEqual(self._n.normalize("SH"), "上海")

    def test_alias(self):
        self.assertEqual(self._n.normalize("沪"), "上海")

    def test_case_insensitive(self):
        self.assertEqual(self._n.normalize("shanghai"), "上海")

    def test_tier_via_alias(self):
        self.assertEqual(self._n.get_tier("BJ"), "tier_1")
        self.assertEqual(self._n.get_tier("蓉"), "tier_2")
        self.assertEqual(self._n.get_tier("UnknownCity"), "tier_3")

    def test_unknown_needs_review(self):
        self.assertTrue(self._n.needs_review("UnknownCity"))
        self.assertFalse(self._n.needs_review("上海"))


class TestPolicyEngine(unittest.TestCase):
    def setUp(self):
        self._engine = PolicyEngine(_loader())

    def test_limit_matrix(self):
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "上海", "L1"), 500.0)
        self.assertEqual(self._engine.get_limit("accommodation_per_night", "成都", "L1"), 350.0)
        self.assertIsNone(self._engine.get_limit("accommodation_per_night", "上海", "L4"))

    def test_tolerance(self):
        self.assertEqual(self._engine.check_tolerance(400, 500), ComplianceLevel.A)
        self.assertEqual(self._engine.check_tolerance(530, 500), ComplianceLevel.B)
        self.assertEqual(self._engine.check_tolerance(600, 500), ComplianceLevel.C)

    def test_approval_chain(self):
        chain = self._engine.get_approval_chain("travel", 1500, "L1")
        self.assertEqual(chain[0].approver_role, "direct_manager")

    def test_L3_skip(self):
        chain = self._engine.get_approval_chain("travel", 5000, "L3")
        roles = [s.approver_role for s in chain]
        self.assertNotIn("direct_manager", roles)

    def test_L4_auto(self):
        chain = self._engine.get_approval_chain("travel", 3000, "L4")
        self.assertTrue(chain[0].is_auto_approved)


class TestControllerWorkflowDriven(unittest.TestCase):
    """验证 workflow.yaml 配置驱动能力。"""

    def setUp(self):
        self._loader = _loader()

    def test_disable_approval_via_config(self):
        """客户A想跳过审批 → 改 enabled:false，不改代码。"""
        import copy
        loader = _loader()
        wf = copy.deepcopy(loader.get("workflow"))
        for step in wf["pipeline"]:
            if step["skill"] == "approval":
                step["enabled"] = False
        loader._config["workflow"] = wf

        ctrl = ExpenseController(loader)
        report = case1_normal_report()
        result = ctrl.process_report(report)
        self.assertEqual(result.final_status, FinalStatus.COMPLETED)
        approval = next(s for s in result.timeline if s.skill_name == "approval")
        self.assertTrue(approval.skipped)

    def test_change_fail_action_via_config(self):
        """客户B想把凭证失败改为reject → 改 fail_action，不改代码。"""
        import copy
        loader = _loader()
        wf = copy.deepcopy(loader.get("workflow"))
        for step in wf["pipeline"]:
            if step["skill"] == "voucher":
                step["fail_action"] = "reject"
        loader._config["workflow"] = wf
        # 这里不制造凭证失败，只验证配置被读取
        ctrl = ExpenseController(loader)
        result = ctrl.process_report(case1_normal_report())
        v = next(s for s in result.timeline if s.skill_name == "voucher")
        self.assertEqual(v.fail_action, "reject")


if __name__ == "__main__":
    unittest.main()
