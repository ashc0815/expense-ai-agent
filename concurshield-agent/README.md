# ConcurShield Agent

费用报销智能审核系统——基于 YAML 配置的可编排报销审核 Agent。

## 项目结构

```
concurshield-agent/
├── main.py                  # 入口
├── config/
│   ├── __init__.py          # ConfigLoader（全局单例）
│   ├── policy.yaml          # 费用标准、城市分级、员工等级对应限额
│   ├── approval_flow.yaml   # 审批矩阵和流程定义
│   ├── expense_types.yaml   # 费用类型定义（可扩展）
│   ├── city_mapping.yaml    # 城市名标准化映射（中英文、别名）
│   └── workflow.yaml        # 流程编排配置
├── models/
│   ├── expense.py           # 数据模型（Employee, Invoice, LineItem, ExpenseReport）
│   └── enums.py             # 枚举定义
├── skills/
│   ├── skill_01_receipt.py  # 发票验证
│   ├── skill_02_approval.py # 审批流程
│   ├── skill_03_compliance.py # 合规检查
│   ├── skill_04_voucher.py  # 记账凭证生成
│   └── skill_05_payment.py  # 付款执行
├── agent/
│   ├── controller.py        # 总控 Agent（根据 workflow.yaml 编排）
│   └── ambiguity_detector.py # 模糊/歧义检测器
├── rules/
│   ├── policy_engine.py     # 策略引擎（从 YAML 加载规则并执行）
│   └── city_normalizer.py   # 城市名标准化
├── mock_data/
│   └── sample_reports.py    # 示例报销单
└── tests/
    └── test_full_flow.py    # 端到端测试
```

## 快速开始

```bash
# 安装依赖
pip install pyyaml

# 运行
python main.py

# 测试
python -m pytest tests/ -v
```

## 核心设计

- **配置驱动**：所有业务规则定义在 YAML 配置文件中，通过 `ConfigLoader` 全局单例统一加载
- **流程可编排**：`workflow.yaml` 控制 skill 的启用/禁用、执行顺序和失败策略
- **城市标准化**：修复 Concur 系统城市名不一致的问题，支持中英文、缩写、别称映射
- **合规分级**：A（合规）/ B（警告通过）/ C（拒绝）三级判定，超标容忍度可配置
