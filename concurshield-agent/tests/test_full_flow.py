"""端到端流程测试。"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from config import ConfigLoader
from agent.controller import AgentController
from mock_data.sample_reports import create_sample_report, create_over_limit_report
from models.enums import ReportStatus
from rules.city_normalizer import CityNormalizer
from rules.policy_engine import PolicyEngine


class TestConfigLoader(unittest.TestCase):
    def setUp(self):
        ConfigLoader.reset()

    def test_load_all_configs(self):
        loader = ConfigLoader()
        loader.load()
        self.assertIn("employee_levels", loader.get("policy"))
        self.assertIn("mappings", loader.get("city_mapping"))
        self.assertIn("approval_rules", loader.get("approval_flow"))
        self.assertIn("expense_types", loader.get("expense_types"))
        self.assertIn("pipeline", loader.get("workflow"))


class TestCityNormalizer(unittest.TestCase):
    def setUp(self):
        ConfigLoader.reset()
        loader = ConfigLoader()
        loader.load()
        self._normalizer = CityNormalizer(loader.get("city_mapping"))

    def test_normalize_english(self):
        name, matched = self._normalizer.normalize("Shanghai")
        self.assertEqual(name, "上海")
        self.assertTrue(matched)

    def test_normalize_abbreviation(self):
        name, matched = self._normalizer.normalize("SH")
        self.assertEqual(name, "上海")
        self.assertTrue(matched)

    def test_normalize_chinese(self):
        name, matched = self._normalizer.normalize("上海")
        self.assertEqual(name, "上海")
        self.assertTrue(matched)

    def test_normalize_case_insensitive(self):
        name, matched = self._normalizer.normalize("shanghai")
        self.assertEqual(name, "上海")
        self.assertTrue(matched)

    def test_unmapped_city(self):
        name, matched = self._normalizer.normalize("UnknownCity")
        self.assertEqual(name, "UnknownCity")
        self.assertFalse(matched)

    def test_needs_review(self):
        self.assertTrue(self._normalizer.needs_review("UnknownCity"))
        self.assertFalse(self._normalizer.needs_review("Shanghai"))


class TestPolicyEngine(unittest.TestCase):
    def setUp(self):
        ConfigLoader.reset()
        loader = ConfigLoader()
        loader.load()
        normalizer = CityNormalizer(loader.get("city_mapping"))
        self._engine = PolicyEngine(loader.get("policy"), normalizer)

    def test_city_tier(self):
        self.assertEqual(self._engine.get_city_tier("上海"), "tier_1")
        self.assertEqual(self._engine.get_city_tier("Shanghai"), "tier_1")
        self.assertEqual(self._engine.get_city_tier("成都"), "tier_2")
        self.assertEqual(self._engine.get_city_tier("UnknownCity"), "tier_3")

    def test_get_limit(self):
        limit = self._engine.get_limit("accommodation_per_night", "上海", "L1")
        self.assertEqual(limit, 500.0)
        limit = self._engine.get_limit("accommodation_per_night", "上海", "L4")
        self.assertIsNone(limit)  # 不限

    def test_compliance_pass(self):
        from models.enums import ComplianceLevel
        result = self._engine.check_compliance(400, 500)
        self.assertEqual(result, ComplianceLevel.A)

    def test_compliance_warning(self):
        from models.enums import ComplianceLevel
        result = self._engine.check_compliance(530, 500)  # 超标30，在50以内
        self.assertEqual(result, ComplianceLevel.B)

    def test_compliance_reject(self):
        from models.enums import ComplianceLevel
        result = self._engine.check_compliance(600, 500)  # 超标100，超过50
        self.assertEqual(result, ComplianceLevel.C)


class TestFullFlow(unittest.TestCase):
    def setUp(self):
        ConfigLoader.reset()
        self._loader = ConfigLoader()
        self._loader.load()

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
