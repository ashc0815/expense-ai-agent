"""ConcurShield Agent 入口——费用报销智能审核系统。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigLoader
from agent.controller import AgentController
from mock_data.sample_reports import create_sample_report, create_over_limit_report
from rules.city_normalizer import CityNormalizer
from rules.policy_engine import PolicyEngine


def main() -> None:
    # 1. 加载全局配置
    loader = ConfigLoader()
    loader.load()

    print("=" * 60)
    print("ConcurShield Agent — 费用报销智能审核系统")
    print("=" * 60)

    # 2. 初始化核心模块
    city_normalizer = CityNormalizer(loader.get("city_mapping"))
    policy_engine = PolicyEngine(loader.get("policy"), city_normalizer)

    # 3. 演示：城市名标准化
    print("\n--- 城市名标准化演示 ---")
    test_cities = ["Shanghai", "SH", "沪", "beijing", "BJ", "蓉", "UnknownCity"]
    for city in test_cities:
        normalized, matched = city_normalizer.normalize(city)
        status = "✓" if matched else "✗ 需人工复核"
        print(f"  {city:15s} → {normalized} {status}")

    # 4. 演示：费用限额查询
    print("\n--- 费用限额查询演示 ---")
    for level in ["L1", "L2", "L3", "L4"]:
        limit = policy_engine.get_limit("accommodation_per_night", "上海", level)
        display = f"{limit:.0f}" if limit else "不限"
        print(f"  住宿(上海) {level}: {display} 元/晚")

    # 5. 执行标准报销单流程
    print("\n--- 标准报销单流程 ---")
    report = create_sample_report()
    controller = AgentController(
        loader.get("workflow"),
        loader.get_all(),
    )
    result = controller.run(report)

    print(f"  报销单: {report.report_id}")
    print(f"  员工: {report.employee.name} ({report.employee.level.value})")
    print(f"  金额: ¥{report.total_amount:.2f}")
    print(f"  结果: {'通过' if result['success'] else '未通过'}")
    print(f"  最终状态: {result['final_status']}")
    print(f"  处理步骤:")
    for step in result["results"]:
        skill = step.get("skill", "unknown")
        passed = step.get("passed", step.get("skipped", False))
        print(f"    - {skill}: {'✓' if passed else '✗'}")

    # 6. 执行超标报销单流程
    print("\n--- 超标报销单流程 ---")
    report2 = create_over_limit_report()
    result2 = controller.run(report2)

    print(f"  报销单: {report2.report_id}")
    print(f"  员工: {report2.employee.name} ({report2.employee.level.value})")
    print(f"  金额: ¥{report2.total_amount:.2f}")
    print(f"  结果: {'通过' if result2['success'] else '未通过'}")
    print(f"  最终状态: {result2['final_status']}")

    print("\n" + "=" * 60)
    print("流程执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    main()
