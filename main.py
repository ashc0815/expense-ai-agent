"""ConcurShield Agent 入口——跑通全部7个测试场景，输出中文日志。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigLoader
from agent.controller import ExpenseController
from agent.ambiguity_detector import AmbiguityDetector
from models.enums import FinalStatus
from skills.skill_03_compliance import process as compliance_process
from skills.skill_04_voucher import reset_voucher_seq
from mock_data.sample_reports import (
    case1_normal_report,
    case2_duplicate_invoice,
    case3_over_limit_reject,
    case4_warning_pass,
    case5_shield_ambiguity,
    case5_with_history,
    case6_pattern_anomaly,
    case7_level_comparison,
)


def _print_result(report, result, extra_info: str = "") -> None:
    status_emoji = {
        FinalStatus.COMPLETED: "✅",
        FinalStatus.REJECTED: "❌",
        FinalStatus.PENDING_REVIEW: "⚠️",
        FinalStatus.PAYMENT_FAILED: "💳",
    }
    emoji = status_emoji.get(result.final_status, "?")
    print(f"  报销单: {report.report_id}")
    print(f"  员工: {report.employee.name} ({report.employee.level.value})")
    print(f"  金额: ¥{report.total_amount:.2f}")
    print(f"  结果: {emoji} {result.final_status.value}")
    print(f"  耗时: {result.total_processing_time.total_seconds():.3f}s")
    if extra_info:
        print(f"  备注: {extra_info}")
    print(f"  时间线:")
    for step in result.timeline:
        mark = "✓" if step.passed else ("⊘" if step.skipped else "✗")
        print(f"    {mark} {step.message}")
    if result.shield_report:
        for item in result.shield_report.get("flagged_items", []):
            print(f"  Shield: score={item['ambiguity_score']:.0f}, "
                  f"建议={item['recommendation']}, "
                  f"因素={item['triggered_factors']}")
            print(f"          {item['explanation']}")


def main() -> None:
    ConfigLoader.reset()
    loader = ConfigLoader()
    loader.load()

    print("=" * 70)
    print("  ConcurShield Agent — 费用报销智能审核系统")
    print("  配置驱动 · 城市标准化 · 模糊检测")
    print("=" * 70)

    ctrl = ExpenseController(loader)

    # ---- Case 1: 正常报销 ----
    print("\n━━━ Case 1: 正常报销（预期：全A通过）━━━")
    reset_voucher_seq()
    r1 = case1_normal_report()
    _print_result(r1, ctrl.process_report(r1))

    # ---- Case 2: 重复发票 ----
    print("\n━━━ Case 2: 重复发票（预期：Skill-01拦截）━━━")
    r2 = case2_duplicate_invoice()
    _print_result(r2, ctrl.process_report(r2))

    # ---- Case 3: 超标拒绝 ----
    print("\n━━━ Case 3: 超标拒绝（预期：C级，成都住宿420>限额350）━━━")
    r3 = case3_over_limit_reject()
    _print_result(r3, ctrl.process_report(r3))

    # ---- Case 4: 警告通过 ----
    print("\n━━━ Case 4: 警告通过（预期：B级，成都住宿380超标30≤50）━━━")
    reset_voucher_seq()
    r4 = case4_warning_pass()
    _print_result(r4, ctrl.process_report(r4))

    # ---- Case 5: ConcurShield 核心 showcase ----
    print("\n━━━ Case 5: ConcurShield — 城市+描述模糊（核心showcase）━━━")
    print("  场景: city='Shanghai', 描述='商务活动费用', 周六, 无参会人名单")
    r5 = case5_shield_ambiguity()
    res5 = ctrl.process_report(r5)
    _print_result(r5, res5, "CityNormalizer: Shanghai→上海(tier_1)")

    # 注入历史后 score 超过 70
    print("\n  [注入历史数据后]")
    history = case5_with_history()
    detector = AmbiguityDetector(loader)
    item5 = case5_shield_ambiguity().line_items[0]
    emp5 = case5_shield_ambiguity().employee
    amb = detector.evaluate(item5, emp5, [], history)
    print(f"  模糊评分: {amb.score:.1f} → {amb.recommendation}")
    print(f"  触发因素: {amb.triggered_factors}")
    print(f"  解释: {amb.explanation}")

    # ---- Case 6: ConcurShield — 模式异常 ----
    print("\n━━━ Case 6: ConcurShield — 模式异常（5天内3笔相似餐费）━━━")
    r6, history6 = case6_pattern_anomaly()
    # 规则引擎看不出问题
    cmp6 = compliance_process(r6)
    print(f"  规则引擎: {cmp6.overall_level.value}级（金额在限额内）")
    # AmbiguityDetector 检测到模式
    amb6 = detector.evaluate(r6.line_items[0], r6.employee, [], history6)
    print(f"  模糊评分: {amb6.score:.1f} → {amb6.recommendation}")
    print(f"  触发因素: {amb6.triggered_factors}")
    print(f"  解释: {amb6.explanation}")

    # ---- Case 7: 员工等级差异 ----
    print("\n━━━ Case 7: 员工等级差异（同一笔¥500住宿，成都）━━━")
    r7_l1, r7_l2 = case7_level_comparison()
    reset_voucher_seq()
    res7_l1 = ctrl.process_report(r7_l1)
    print(f"  L1(限额350): {res7_l1.final_status.value}")
    reset_voucher_seq()
    res7_l2 = ctrl.process_report(r7_l2)
    print(f"  L2(限额500): {res7_l2.final_status.value}")
    print(f"  → 同样¥500，L1拒绝/L2通过——纯配置驱动，零代码改动")

    print("\n" + "=" * 70)
    print("  全部7个场景执行完毕")
    print("=" * 70)


if __name__ == "__main__":
    main()
