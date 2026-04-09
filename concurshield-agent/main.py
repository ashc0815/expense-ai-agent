"""ConcurShield Agent 入口——费用报销智能审核系统。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ConfigLoader
from agent.controller import AgentController
from mock_data.sample_reports import create_sample_report, create_over_limit_report
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

    # 4. 演示：费用限额查询（注意：改YAML就能改限额，不碰代码）
    print("\n--- 费用限额查询演示 ---")
    for level in ["L1", "L2", "L3", "L4"]:
        limit = engine.get_limit("accommodation_per_night", "上海", level)
        display = f"{limit:.0f}" if limit is not None else "不限"
        print(f"  住宿(上海) {level}: {display} 元/晚")

    # 5. 演示：合规判定
    print("\n--- 合规判定演示 ---")
    for amount in [400, 530, 600]:
        level = engine.check_tolerance(amount, 500)
        print(f"  金额 {amount} vs 限额 500 → {level.value}级")

    # 6. 演示：审批链计算
    print("\n--- 审批链计算演示 ---")
    for level, amount in [("L1", 1500), ("L1", 5000), ("L3", 8000), ("L4", 3000)]:
        chain = engine.get_approval_chain("travel", amount, level)
        roles = " → ".join(
            f"{s.approver_role}({'自动' if s.is_auto_approved else f'{s.time_limit_hours}h'})"
            for s in chain
        )
        print(f"  差旅 ¥{amount} {level}: {roles}")

    # 7. 演示：发票校验
    print("\n--- 发票校验演示 ---")
    report = create_sample_report()
    invoice = report.line_items[0].invoice
    results = engine.validate_invoice(invoice, report.employee, [])
    for r in results:
        mark = "✓" if r.passed else "✗"
        print(f"  {mark} [{r.severity:7s}] {r.rule_name}: {r.message}")

    # 8. 执行标准报销单流程
    print("\n--- 标准报销单流程 ---")
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

    # 9. 执行超标报销单流程
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
