"""ConcurShield Agent 入口——费用报销智能审核系统。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigLoader
from agent.controller import ExpenseController
from mock_data.sample_reports import create_sample_report, create_over_limit_report
from models.enums import FinalStatus
from rules.policy_engine import PolicyEngine


def main() -> None:
    # 1. 加载全局配置
    ConfigLoader.reset()
    loader = ConfigLoader()
    loader.load()

    print("=" * 60)
    print("ConcurShield Agent — 费用报销智能审核系统")
    print("=" * 60)

    # 2. 初始化策略引擎（内部自动创建 CityNormalizer）
    engine = PolicyEngine(loader)
    normalizer = engine.city_normalizer

    # 3. 演示：城市名标准化
    print("\n--- 城市名标准化演示 ---")
    test_cities = ["Shanghai", "SH", "沪", "beijing", "BJ", "蓉", "UnknownCity"]
    for city in test_cities:
        normalized = normalizer.normalize(city)
        tier = normalizer.get_tier(city)
        known = normalizer.is_known(city)
        status = f"✓ {tier}" if known else "✗ 需人工复核"
        print(f"  {city:15s} → {normalized:4s} {status}")

    # 4. 演示：费用限额查询
    print("\n--- 费用限额查询演示 ---")
    for level in ["L1", "L2", "L3", "L4"]:
        limit = engine.get_limit("accommodation_per_night", "上海", level)
        display = f"{limit:.0f}" if limit is not None else "不限"
        print(f"  住宿(上海) {level}: {display} 元/晚")

    # 5. 演示：审批链计算
    print("\n--- 审批链计算演示 ---")
    for level, amount in [("L1", 1500), ("L1", 5000), ("L3", 8000), ("L4", 3000)]:
        chain = engine.get_approval_chain("travel", amount, level)
        roles = " → ".join(
            f"{s.approver_role}({'自动' if s.is_auto_approved else f'{s.time_limit_hours}h'})"
            for s in chain
        )
        print(f"  差旅 ¥{amount} {level}: {roles}")

    # 6. 新版 ExpenseController 流程编排
    controller = ExpenseController(loader)

    # ---- 标准报销单 ----
    print("\n--- 标准报销单流程（ExpenseController） ---")
    report1 = create_sample_report()
    result1 = controller.process_report(report1)

    print(f"  报销单: {report1.report_id}")
    print(f"  员工: {report1.employee.name} ({report1.employee.level.value})")
    print(f"  金额: ¥{report1.total_amount:.2f}")
    print(f"  最终状态: {result1.final_status.value}")
    print(f"  总耗时: {result1.total_processing_time.total_seconds():.3f}s")
    print(f"  时间线:")
    for step in result1.timeline:
        mark = "✓" if step.passed else ("⊘" if step.skipped else "✗")
        print(f"    {mark} {step.message}")

    # ---- 超标报销单 ----
    print("\n--- 超标报销单流程（ExpenseController） ---")
    report2 = create_over_limit_report()
    result2 = controller.process_report(report2)

    print(f"  报销单: {report2.report_id}")
    print(f"  金额: ¥{report2.total_amount:.2f}")
    print(f"  最终状态: {result2.final_status.value}")
    print(f"  时间线:")
    for step in result2.timeline:
        mark = "✓" if step.passed else ("⊘" if step.skipped else "✗")
        print(f"    {mark} {step.message}")
    if result2.shield_report:
        print(f"  Shield 报告: {result2.shield_report}")

    print("\n" + "=" * 60)
    print("流程执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    main()
