"""Tests for fraud_rules.py — scenarios 1-20 with mock data."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services.fraud_rules import (
    DEFAULT_CONFIG,
    ApprovalRow,
    EmployeeRow,
    FraudSignal,
    SubmissionRow,
    rule_approver_collusion,
    rule_collusion_pattern,
    rule_consecutive_invoices,
    rule_description_template,
    rule_duplicate_attendee,
    rule_fx_arbitrage,
    rule_geo_conflict,
    rule_ghost_employee,
    rule_merchant_category_mismatch,
    rule_person_amount_mismatch,
    rule_pre_resignation_rush,
    rule_rationalized_personal,
    rule_receipt_contradiction,
    rule_round_amount,
    rule_seasonal_anomaly,
    rule_threshold_proximity,
    rule_timestamp_conflict,
    rule_vague_description,
    rule_vendor_frequency,
    rule_weekend_frequency,
)


def _sub(id="s1", employee_id="emp-1", amount=100.0, currency="CNY",
         category="meal", dt="2026-04-01", merchant="海底捞",
         invoice_number=None, invoice_code=None, description=None,
         exchange_rate=None, city=None, attendees=None) -> SubmissionRow:
    return SubmissionRow(
        id=id, employee_id=employee_id, amount=amount, currency=currency,
        category=category, date=dt, merchant=merchant,
        invoice_number=invoice_number, invoice_code=invoice_code,
        description=description, exchange_rate=exchange_rate,
        city=city, attendees=attendees,
    )


def _emp(id="emp-1", department="工程部", hire_date=None,
         resignation_date=None) -> EmployeeRow:
    return EmployeeRow(id=id, department=department,
                       hire_date=hire_date, resignation_date=resignation_date)


# ── 场景 1: 重复报销 + Attendee 双吃 ──

class TestDuplicateAttendee:
    def test_overlap_detected(self):
        subs = [
            _sub(id="s1", employee_id="A", category="meal",
                 dt="2026-04-01", merchant="海底捞", attendees=["B", "C"]),
            _sub(id="s2", employee_id="B", category="meal",
                 dt="2026-04-01", merchant="海底捞", attendees=["A", "C"]),
        ]
        signals = rule_duplicate_attendee(subs)
        assert len(signals) >= 1
        assert all(s.rule == "duplicate_attendee" for s in signals)
        assert signals[0].score == 80

    def test_no_overlap(self):
        subs = [
            _sub(id="s1", employee_id="A", category="meal",
                 dt="2026-04-01", merchant="海底捞", attendees=["C"]),
            _sub(id="s2", employee_id="B", category="meal",
                 dt="2026-04-01", merchant="海底捞", attendees=["D"]),
        ]
        signals = rule_duplicate_attendee(subs)
        assert len(signals) == 0

    def test_different_date_no_flag(self):
        subs = [
            _sub(id="s1", employee_id="A", category="meal",
                 dt="2026-04-01", merchant="海底捞", attendees=["B"]),
            _sub(id="s2", employee_id="B", category="meal",
                 dt="2026-04-02", merchant="海底捞", attendees=["A"]),
        ]
        signals = rule_duplicate_attendee(subs)
        assert len(signals) == 0

    def test_non_meal_ignored(self):
        subs = [
            _sub(id="s1", employee_id="A", category="transport",
                 dt="2026-04-01", merchant="海底捞", attendees=["B"]),
            _sub(id="s2", employee_id="B", category="transport",
                 dt="2026-04-01", merchant="海底捞", attendees=["A"]),
        ]
        signals = rule_duplicate_attendee(subs)
        assert len(signals) == 0


# ── 场景 2: 地理矛盾 ──

class TestGeoConflict:
    def test_multi_city_no_transport(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="北京", category="meal", dt="2026-04-01"),
        ]
        signals = rule_geo_conflict(subs)
        assert len(signals) == 1
        assert signals[0].rule == "geo_conflict"
        assert signals[0].score == 75

    def test_multi_city_with_transport_ok(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="北京", category="meal", dt="2026-04-01"),
            _sub(id="s3", city="上海", category="transport", dt="2026-04-01"),
        ]
        signals = rule_geo_conflict(subs)
        assert len(signals) == 0

    def test_same_city_ok(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="上海", category="meal", dt="2026-04-01"),
        ]
        signals = rule_geo_conflict(subs)
        assert len(signals) == 0


# ── 场景 3: 卡线报销 ──

class TestThresholdProximity:
    def test_cluster_near_limit(self):
        subs = [_sub(id=f"s{i}", amount=v)
                for i, v in enumerate([299, 298, 295, 297])]
        signals = rule_threshold_proximity(subs)
        assert len(signals) == 1
        assert signals[0].rule == "threshold_proximity"

    def test_below_min_count_no_flag(self):
        subs = [_sub(id="s1", amount=299), _sub(id="s2", amount=298)]
        signals = rule_threshold_proximity(subs)
        assert len(signals) == 0

    def test_amounts_far_from_limit_ok(self):
        subs = [_sub(id=f"s{i}", amount=v)
                for i, v in enumerate([100, 150, 200, 250])]
        signals = rule_threshold_proximity(subs)
        assert len(signals) == 0


# ── 场景 4: 时间戳矛盾 ──

class TestTimestampConflict:
    def test_diff_city_non_transport(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="北京", category="accommodation", dt="2026-04-01"),
        ]
        signals = rule_timestamp_conflict(subs)
        assert len(signals) == 1
        assert signals[0].rule == "timestamp_conflict"

    def test_transport_excluded_from_city_set(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="北京", category="transport", dt="2026-04-01"),
        ]
        signals = rule_timestamp_conflict(subs)
        assert len(signals) == 0

    def test_same_city_ok(self):
        subs = [
            _sub(id="s1", city="上海", category="meal", dt="2026-04-01"),
            _sub(id="s2", city="上海", category="accommodation", dt="2026-04-01"),
        ]
        signals = rule_timestamp_conflict(subs)
        assert len(signals) == 0


# ── 场景 5: 周末/节假日高频报销 ──

class TestWeekendFrequency:
    def test_non_sales_weekend_meals_flagged(self):
        emp = _emp(department="工程部")
        subs = []
        base = date(2026, 3, 7)  # Saturday
        for i in range(5):
            d = base + timedelta(weeks=i)
            subs.append(_sub(id=f"s{i}", category="meal", dt=d.isoformat()))
        signals = rule_weekend_frequency(subs, emp)
        assert len(signals) == 1
        assert signals[0].rule == "weekend_frequency"

    def test_sales_dept_exempt(self):
        emp = _emp(department="销售部")
        subs = []
        base = date(2026, 3, 7)
        for i in range(5):
            d = base + timedelta(weeks=i)
            subs.append(_sub(id=f"s{i}", category="meal", dt=d.isoformat()))
        signals = rule_weekend_frequency(subs, emp)
        assert len(signals) == 0

    def test_below_threshold_ok(self):
        emp = _emp(department="工程部")
        subs = [
            _sub(id="s1", category="meal", dt="2026-03-07"),
            _sub(id="s2", category="meal", dt="2026-03-14"),
        ]
        signals = rule_weekend_frequency(subs, emp)
        assert len(signals) == 0

    def test_weekday_meals_ignored(self):
        emp = _emp(department="工程部")
        subs = [_sub(id=f"s{i}", category="meal",
                     dt=(date(2026, 3, 2) + timedelta(weeks=i)).isoformat())
                for i in range(5)]  # Mondays
        signals = rule_weekend_frequency(subs, emp)
        assert len(signals) == 0


# ── 场景 6: 整数金额聚集 ──

class TestRoundAmount:
    def test_high_round_ratio(self):
        subs = [_sub(id=f"s{i}", amount=a)
                for i, a in enumerate([100, 200, 300, 500, 600, 150.5, 99.9])]
        signals = rule_round_amount(subs)
        assert len(signals) == 1
        assert signals[0].rule == "round_amount"

    def test_low_round_ratio_ok(self):
        subs = [_sub(id=f"s{i}", amount=a)
                for i, a in enumerate([99.5, 123.4, 200, 55.8, 301.2])]
        signals = rule_round_amount(subs)
        assert len(signals) == 0

    def test_too_few_submissions(self):
        subs = [_sub(id="s1", amount=100), _sub(id="s2", amount=200)]
        signals = rule_round_amount(subs)
        assert len(signals) == 0


# ── 场景 7: 发票连号 ──

class TestConsecutiveInvoices:
    def test_sequential_invoices_flagged(self):
        subs = [_sub(id=f"s{i}", merchant="全聚德", invoice_number=str(n))
                for i, n in enumerate([1001, 1002, 1003, 1004])]
        signals = rule_consecutive_invoices(subs)
        assert len(signals) == 1
        assert signals[0].rule == "consecutive_invoices"
        assert signals[0].details["max_seq"] == 4

    def test_non_sequential_ok(self):
        subs = [_sub(id=f"s{i}", merchant="全聚德", invoice_number=str(n))
                for i, n in enumerate([1001, 1005, 1010])]
        signals = rule_consecutive_invoices(subs)
        assert len(signals) == 0

    def test_different_merchants_separate(self):
        subs = [
            _sub(id="s1", merchant="全聚德", invoice_number="1001"),
            _sub(id="s2", merchant="全聚德", invoice_number="1002"),
            _sub(id="s3", merchant="东来顺", invoice_number="1003"),
        ]
        signals = rule_consecutive_invoices(subs)
        assert len(signals) == 0

    def test_cross_employee_detection(self):
        subs = [
            _sub(id="s1", employee_id="A", merchant="全聚德", invoice_number="1001"),
            _sub(id="s2", employee_id="B", merchant="全聚德", invoice_number="1002"),
            _sub(id="s3", employee_id="A", merchant="全聚德", invoice_number="1003"),
        ]
        signals = rule_consecutive_invoices(subs)
        assert len(signals) == 1


# ── 场景 8: 商户类型与费用类别不匹配 ──

class TestMerchantCategoryMismatch:
    def test_foot_spa_as_meal_flagged(self):
        subs = [_sub(id="s1", merchant="天堂足浴中心", category="meal")]
        signals = rule_merchant_category_mismatch(subs)
        assert len(signals) == 1
        assert signals[0].rule == "merchant_category_mismatch"
        assert signals[0].score == 80

    def test_ktv_as_entertainment_ok(self):
        subs = [_sub(id="s1", merchant="欢乐KTV", category="entertainment")]
        signals = rule_merchant_category_mismatch(subs)
        assert len(signals) == 0

    def test_normal_merchant_ok(self):
        subs = [_sub(id="s1", merchant="星巴克", category="meal")]
        signals = rule_merchant_category_mismatch(subs)
        assert len(signals) == 0

    def test_multiple_keywords(self):
        subs = [
            _sub(id="s1", merchant="豪华按摩", category="meal"),
            _sub(id="s2", merchant="烟酒专卖", category="office"),
        ]
        signals = rule_merchant_category_mismatch(subs)
        assert len(signals) == 2


# ── 场景 9: 离职前突击报销 ──

class TestPreResignationRush:
    def test_rush_before_resignation(self):
        resign = date(2026, 4, 30)
        emp = _emp(resignation_date=resign)
        normal = [_sub(id=f"n{i}", amount=500,
                       dt=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat())
                  for i in range(10)]
        rush = [_sub(id=f"r{i}", amount=3000,
                     dt=(resign - timedelta(days=i + 1)).isoformat())
                for i in range(3)]
        signals = rule_pre_resignation_rush(normal + rush, emp)
        assert len(signals) == 1
        assert signals[0].rule == "pre_resignation_rush"
        assert signals[0].score == 85

    def test_no_resignation_no_flag(self):
        emp = _emp(resignation_date=None)
        subs = [_sub(id="s1", amount=5000, dt="2026-04-01")]
        signals = rule_pre_resignation_rush(subs, emp)
        assert len(signals) == 0

    def test_normal_amount_before_resignation_ok(self):
        resign = date(2026, 4, 30)
        emp = _emp(resignation_date=resign)
        normal = [_sub(id=f"n{i}", amount=500,
                       dt=(date(2026, 1, 1) + timedelta(days=i * 10)).isoformat())
                  for i in range(10)]
        rush = [_sub(id="r1", amount=500,
                     dt=(resign - timedelta(days=5)).isoformat())]
        signals = rule_pre_resignation_rush(normal + rush, emp)
        assert len(signals) == 0


# ── 场景 10: 汇率套利 ──

class TestFxArbitrage:
    def _market_rate(self, from_ccy, to_ccy):
        rates = {"USD": 7.25, "EUR": 7.90, "AUD": 4.80}
        if to_ccy == "CNY":
            return rates.get(from_ccy, 0)
        return 0

    def test_high_deviation_flagged(self):
        subs = [_sub(id="s1", currency="USD", exchange_rate=7.50, amount=100)]
        signals = rule_fx_arbitrage(subs, self._market_rate)
        assert len(signals) == 1
        assert signals[0].rule == "fx_arbitrage"
        deviation = abs(7.50 - 7.25) / 7.25
        assert deviation > DEFAULT_CONFIG["fx_deviation_pct"]

    def test_within_tolerance_ok(self):
        subs = [_sub(id="s1", currency="USD", exchange_rate=7.26, amount=100)]
        signals = rule_fx_arbitrage(subs, self._market_rate)
        assert len(signals) == 0

    def test_cny_skipped(self):
        subs = [_sub(id="s1", currency="CNY", exchange_rate=1.0, amount=100)]
        signals = rule_fx_arbitrage(subs, self._market_rate)
        assert len(signals) == 0

    def test_no_exchange_rate_skipped(self):
        subs = [_sub(id="s1", currency="USD", exchange_rate=None, amount=100)]
        signals = rule_fx_arbitrage(subs, self._market_rate)
        assert len(signals) == 0

    def test_low_rate_also_flagged(self):
        subs = [_sub(id="s1", currency="EUR", exchange_rate=7.50, amount=100)]
        signals = rule_fx_arbitrage(subs, self._market_rate)
        assert len(signals) == 1  # 7.50 vs 7.90 = 5% deviation → flagged
        assert "低于" in signals[0].evidence


# ── 场景 11: 备注模板化 (LLM) ──

class TestDescriptionTemplate:
    def test_high_template_score_flags(self):
        sub = _sub(description="与客户张总会面讨论合作事宜")
        llm_analysis = {"template_score": 85, "template_evidence": "3/3 identical pattern"}
        signals = rule_description_template([sub], llm_analysis)
        assert len(signals) == 1
        assert signals[0].rule == "description_template"
        assert signals[0].score == 65

    def test_low_template_score_passes(self):
        sub = _sub(description="与客户张总会面讨论合作事宜")
        llm_analysis = {"template_score": 30, "template_evidence": "descriptions vary"}
        signals = rule_description_template([sub], llm_analysis)
        assert len(signals) == 0

    def test_no_description_passes(self):
        sub = _sub(description=None)
        llm_analysis = {"template_score": 0, "template_evidence": ""}
        signals = rule_description_template([sub], llm_analysis)
        assert len(signals) == 0

    def test_threshold_is_configurable(self):
        sub = _sub(description="test")
        llm_analysis = {"template_score": 60, "template_evidence": "somewhat similar"}
        config = {**DEFAULT_CONFIG, "template_score_threshold": 50}
        signals = rule_description_template([sub], llm_analysis, config)
        assert len(signals) == 1


# ── 场景 12: Receipt 与备注矛盾 (LLM) ──

class TestReceiptContradiction:
    def test_contradiction_detected(self):
        sub = _sub(description="客户办公室附近工作午餐", merchant="购物中心美食广场")
        llm_analysis = {
            "contradiction_found": True,
            "contradiction_evidence": "Receipt shows shopping mall but description says office area",
        }
        signals = rule_receipt_contradiction([sub], llm_analysis)
        assert len(signals) == 1
        assert signals[0].rule == "receipt_contradiction"
        assert signals[0].score == 70

    def test_no_contradiction(self):
        sub = _sub(description="客户办公室附近工作午餐", merchant="写字楼食堂")
        llm_analysis = {
            "contradiction_found": False,
            "contradiction_evidence": "",
        }
        signals = rule_receipt_contradiction([sub], llm_analysis)
        assert len(signals) == 0

    def test_missing_llm_data_passes(self):
        sub = _sub(description="test")
        signals = rule_receipt_contradiction([sub], {})
        assert len(signals) == 0


# ── 场景 13: 人数与金额不匹配 (LLM) ──

class TestPersonAmountMismatch:
    def test_unreasonable_per_person_flags(self):
        sub = _sub(description="两人商务午餐", amount=680.0, category="meal")
        llm_analysis = {
            "extracted_person_count": 2,
            "per_person_amount": 340.0,
            "person_amount_reasonable": False,
            "person_amount_evidence": "AUD 340 per person for lunch is unusually high",
        }
        signals = rule_person_amount_mismatch([sub], llm_analysis)
        assert len(signals) == 1
        assert signals[0].rule == "person_amount_mismatch"
        assert signals[0].score == 60

    def test_reasonable_amount_passes(self):
        sub = _sub(description="两人商务午餐", amount=200.0, category="meal")
        llm_analysis = {
            "extracted_person_count": 2,
            "per_person_amount": 100.0,
            "person_amount_reasonable": True,
            "person_amount_evidence": "100 per person is normal",
        }
        signals = rule_person_amount_mismatch([sub], llm_analysis)
        assert len(signals) == 0

    def test_no_person_count_passes(self):
        sub = _sub(description="商务午餐", amount=500.0, category="meal")
        llm_analysis = {
            "extracted_person_count": None,
            "per_person_amount": None,
            "person_amount_reasonable": True,
            "person_amount_evidence": "",
        }
        signals = rule_person_amount_mismatch([sub], llm_analysis)
        assert len(signals) == 0


# ── 场景 14: 模糊事由掩盖消费性质 (LLM) ──

class TestVagueDescription:
    def test_high_vagueness_with_gift_category_flags(self):
        sub = _sub(description="项目相关支出", category="gift")
        llm_analysis = {"vagueness_score": 80, "vagueness_evidence": "Generic description hides gift nature"}
        signals = rule_vague_description([sub], llm_analysis)
        assert len(signals) == 1
        assert signals[0].rule == "vague_description"
        assert signals[0].score == 60

    def test_high_vagueness_with_meal_passes(self):
        """Meals with vague descriptions are common and less suspicious."""
        sub = _sub(description="项目相关支出", category="meal")
        llm_analysis = {"vagueness_score": 80, "vagueness_evidence": "Generic"}
        signals = rule_vague_description([sub], llm_analysis)
        assert len(signals) == 0

    def test_low_vagueness_passes(self):
        sub = _sub(description="给客户王总的年度合作纪念品，定制笔记本套装", category="gift")
        llm_analysis = {"vagueness_score": 20, "vagueness_evidence": "Specific and detailed"}
        signals = rule_vague_description([sub], llm_analysis)
        assert len(signals) == 0

    def test_threshold_is_configurable(self):
        sub = _sub(description="杂项费用", category="gift")
        llm_analysis = {"vagueness_score": 55, "vagueness_evidence": "somewhat vague"}
        config = {**DEFAULT_CONFIG, "vagueness_threshold": 50}
        signals = rule_vague_description([sub], llm_analysis, config)
        assert len(signals) == 1


# ── 场景 15: Collusion pattern — 轮流报销拆单 ──

class TestCollusionPattern:
    def test_alternating_same_merchant_flags(self):
        """A and B take turns expensing meals at the same merchant."""
        subs = [
            _sub(id="s1", employee_id="A", amount=290, category="meal",
                 dt="2026-04-01", merchant="海底捞"),
            _sub(id="s2", employee_id="B", amount=285, category="meal",
                 dt="2026-04-03", merchant="海底捞"),
            _sub(id="s3", employee_id="A", amount=295, category="meal",
                 dt="2026-04-07", merchant="海底捞"),
            _sub(id="s4", employee_id="B", amount=280, category="meal",
                 dt="2026-04-10", merchant="海底捞"),
        ]
        signals = rule_collusion_pattern(subs)
        assert len(signals) >= 1
        assert signals[0].rule == "collusion_pattern"
        assert signals[0].score == 75

    def test_same_employee_not_flagged(self):
        subs = [
            _sub(id=f"s{i}", employee_id="A", amount=290, category="meal",
                 dt=f"2026-04-{i+1:02d}", merchant="海底捞")
            for i in range(4)
        ]
        signals = rule_collusion_pattern(subs)
        assert len(signals) == 0

    def test_below_threshold_not_flagged(self):
        subs = [
            _sub(id="s1", employee_id="A", amount=290, category="meal",
                 dt="2026-04-01", merchant="海底捞"),
            _sub(id="s2", employee_id="B", amount=285, category="meal",
                 dt="2026-04-03", merchant="海底捞"),
        ]
        # Only 2 submissions — below min_pair_count default of 3
        signals = rule_collusion_pattern(subs)
        assert len(signals) == 0


# ── 场景 16: 合理化的私人消费 ──

class TestRationalizedPersonal:
    def test_weekend_resort_multiple_categories_flags(self):
        subs = [
            _sub(id="s1", employee_id="A", amount=500, category="accommodation",
                 dt="2026-04-12", merchant="三亚亚特兰蒂斯",
                 description="会议室租用费"),  # Saturday
            _sub(id="s2", employee_id="A", amount=200, category="office",
                 dt="2026-04-12", merchant="三亚亚特兰蒂斯",
                 description="商务中心使用费"),
        ]
        signals = rule_rationalized_personal(subs)
        assert len(signals) == 1
        assert signals[0].rule == "rationalized_personal"
        assert signals[0].score == 70

    def test_weekday_same_merchant_not_flagged(self):
        subs = [
            _sub(id="s1", employee_id="A", amount=500, category="accommodation",
                 dt="2026-04-14", merchant="香格里拉酒店",  # Monday
                 description="会议室租用费"),
            _sub(id="s2", employee_id="A", amount=200, category="office",
                 dt="2026-04-14", merchant="香格里拉酒店",
                 description="商务中心使用费"),
        ]
        signals = rule_rationalized_personal(subs)
        assert len(signals) == 0

    def test_single_item_not_flagged(self):
        subs = [
            _sub(id="s1", employee_id="A", amount=500, category="accommodation",
                 dt="2026-04-12", merchant="三亚亚特兰蒂斯",
                 description="住宿"),
        ]
        signals = rule_rationalized_personal(subs)
        assert len(signals) == 0


# ── 场景 17: 供应商频率异常 ──

class TestVendorFrequency:
    def test_high_frequency_single_vendor_flags(self):
        """Same vendor 8 times."""
        subs = [
            _sub(id=f"s{i}", employee_id="A", amount=150, category="meal",
                 dt=f"2026-04-{i+1:02d}", merchant="小李便利店")
            for i in range(8)
        ]
        signals = rule_vendor_frequency(subs)
        assert len(signals) == 1
        assert signals[0].rule == "vendor_frequency"
        assert signals[0].score == 65

    def test_normal_frequency_passes(self):
        subs = [
            _sub(id=f"s{i}", employee_id="A", amount=150, category="meal",
                 dt=f"2026-04-{i*7+1:02d}", merchant="星巴克")
            for i in range(3)
        ]
        signals = rule_vendor_frequency(subs)
        assert len(signals) == 0

    def test_different_merchants_not_flagged(self):
        subs = [
            _sub(id=f"s{i}", employee_id="A", amount=150, category="meal",
                 dt=f"2026-04-{i+1:02d}", merchant=f"餐厅{i}")
            for i in range(8)
        ]
        signals = rule_vendor_frequency(subs)
        assert len(signals) == 0


# ── 场景 18: 季节性异常 ──

class TestSeasonalAnomaly:
    def test_q4_spike_flags(self):
        """Q4 spend is 4x the average of other quarters."""
        quarter_totals = {
            "2025-Q1": 5000, "2025-Q2": 6000,
            "2025-Q3": 5500, "2025-Q4": 25000,
        }
        signals = rule_seasonal_anomaly(quarter_totals, current_quarter="2025-Q4")
        assert len(signals) == 1
        assert signals[0].rule == "seasonal_anomaly"
        assert signals[0].score == 60

    def test_uniform_spending_passes(self):
        quarter_totals = {
            "2025-Q1": 5000, "2025-Q2": 6000,
            "2025-Q3": 5500, "2025-Q4": 6500,
        }
        signals = rule_seasonal_anomaly(quarter_totals, current_quarter="2025-Q4")
        assert len(signals) == 0

    def test_insufficient_history_passes(self):
        quarter_totals = {"2025-Q4": 25000}
        signals = rule_seasonal_anomaly(quarter_totals, current_quarter="2025-Q4")
        assert len(signals) == 0


# ── 场景 19: 审批人与报销人的默契 ──

class TestApproverCollusion:
    def test_fast_approval_never_rejected_flags(self):
        """Approver always approves emp-A in <3 min, but takes 15 min for others."""
        approvals = [
            ApprovalRow(submission_id="s1", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=90),
            ApprovalRow(submission_id="s2", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=120),
            ApprovalRow(submission_id="s3", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=100),
            ApprovalRow(submission_id="s4", employee_id="B", approver_id="mgr-1",
                        approved=True, duration_seconds=900),
            ApprovalRow(submission_id="s5", employee_id="B", approver_id="mgr-1",
                        approved=False, duration_seconds=1200),
        ]
        signals = rule_approver_collusion(approvals, target_employee="A")
        assert len(signals) == 1
        assert signals[0].rule == "approver_collusion"
        assert signals[0].score == 70

    def test_normal_approval_speed_passes(self):
        approvals = [
            ApprovalRow(submission_id="s1", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=900),
            ApprovalRow(submission_id="s2", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=800),
            ApprovalRow(submission_id="s3", employee_id="A", approver_id="mgr-1",
                        approved=True, duration_seconds=850),
            ApprovalRow(submission_id="s4", employee_id="B", approver_id="mgr-1",
                        approved=True, duration_seconds=850),
        ]
        signals = rule_approver_collusion(approvals, target_employee="A")
        assert len(signals) == 0


# ── 场景 20: Ghost Employee — 已离职员工仍在报销 ──

class TestGhostEmployee:
    def test_resigned_employee_submitting_flags(self):
        sub = _sub(employee_id="A", dt="2026-04-10")
        emp = _emp(id="A", resignation_date=date(2026, 3, 15))
        signals = rule_ghost_employee([sub], emp)
        assert len(signals) == 1
        assert signals[0].rule == "ghost_employee"
        assert signals[0].score == 90

    def test_active_employee_passes(self):
        sub = _sub(employee_id="A", dt="2026-04-10")
        emp = _emp(id="A", resignation_date=None)
        signals = rule_ghost_employee([sub], emp)
        assert len(signals) == 0

    def test_submission_before_resignation_passes(self):
        sub = _sub(employee_id="A", dt="2026-03-01")
        emp = _emp(id="A", resignation_date=date(2026, 3, 15))
        signals = rule_ghost_employee([sub], emp)
        assert len(signals) == 0

    def test_no_last_activity_uses_resignation_date(self):
        sub = _sub(employee_id="A", dt="2026-04-10")
        emp = _emp(id="A", resignation_date=date(2026, 3, 15))
        signals = rule_ghost_employee([sub], emp)
        assert len(signals) == 1
