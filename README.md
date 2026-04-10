# ConcurShield Agent

**智能费用报销审核系统** — 配置驱动 + Agent Only for Ambiguity

## 项目定位

ConcurShield 是一个完全配置驱动的费用报销审核 Agent。它不是替代 Concur 系统，而是在 Concur 之上增加一层智能审核：

- **规则层**：所有费用标准、审批流程、会计科目全部定义在 YAML 配置文件中，零代码适配不同客户
- **智能层**：AmbiguityDetector 在规则层之上，用五维评分模型捕获规则无法覆盖的模糊情况
- **Phase 2 预留**：当模糊评分 > 50 时触发 LLM 深度语义分析接口（当前版本为规则评分）

## 核心差异化

### 1. 所有规则配置化，零代码适配不同客户

```
客户A想跳过审批     → workflow.yaml: approval.enabled: false
客户B的住宿限额不同  → policy.yaml: limits.accommodation_per_night 改数字
客户C多了个费用类型   → expense_types.yaml 加一段配置
```

**从不需要改一行代码。**

### 2. 城市标准化模块 — 修复 Concur 已知缺陷

Concur 系统的已知问题：同一城市在不同报销单中出现 "Shanghai"、"SH"、"沪"、"上海" 四种写法，导致费用标准匹配错误。

```
CityNormalizer:
  "Shanghai" → "上海" → tier_1 → 住宿限额500
  "SH"       → "上海" → tier_1 → 住宿限额500
  "沪"       → "上海" → tier_1 → 住宿限额500
  "蓉"       → "成都" → tier_2 → 住宿限额350
```

### 3. AmbiguityDetector — 规则层之上捕获规则漏判

五维加权评分模型（0-100分）：

| 因素 | 权重 | 触发条件 |
|------|------|----------|
| 描述模糊度 | 25% | 描述 <10字 或含泛化词（"其他""杂项""费用"） |
| 金额边界 | 20% | 金额在限额的 90%-110% 区间 |
| 模式异常 | 25% | 7天内 ≥3笔同类型 ±15% 金额 |
| 时间异常 | 15% | 周末的餐费/交通费 |
| 城市不匹配 | 15% | 城市名未识别 或 标准化前后不一致 |

评分 → 决策：`<30` 自动通过 / `30-70` 人工复核 / `>70` 建议拒绝

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    main.py (入口)                        │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              ExpenseController (总控)                     │
│         读取 workflow.yaml 编排以下 5 个 skill            │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Skill-01 │→│ Skill-02 │→│ Skill-03 │→ ...           │
│  │ 发票验证 │  │ 审批流程 │  │ 合规审查 │               │
│  └──────────┘  └──────────┘  └─────┬────┘               │
│                                     │                    │
│                          ┌──────────▼─────────┐          │
│                          │ AmbiguityDetector   │          │
│                          │ 五维模糊评分模型    │          │
│                          │ score > 30 → Shield │          │
│                          └────────────────────┘          │
│                                                          │
│  ┌──────────┐  ┌──────────┐                              │
│  │ Skill-04 │→│ Skill-05 │                              │
│  │ 凭证生成 │  │ 付款执行 │                              │
│  └──────────┘  └──────────┘                              │
└──────────────────────────────────────────────────────────┘
         │                    │                  │
┌────────▼────────┐ ┌────────▼────────┐ ┌───────▼────────┐
│  PolicyEngine   │ │ CityNormalizer  │ │  ConfigLoader  │
│  (规则引擎)     │ │  (城市标准化)   │ │  (配置单例)    │
└─────────────────┘ └─────────────────┘ └────────────────┘
         │                    │                  │
┌────────▼────────────────────▼──────────────────▼────────┐
│                    YAML 配置层                           │
│  policy.yaml · approval_flow.yaml · expense_types.yaml  │
│  city_mapping.yaml · workflow.yaml                       │
└─────────────────────────────────────────────────────────┘
```

## 项目结构

```
concurshield-agent/
├── main.py                       # 入口：跑通7个测试场景
├── config/
│   ├── __init__.py               # ConfigLoader（全局单例）
│   ├── policy.yaml               # 费用标准、城市分级、员工等级限额
│   ├── approval_flow.yaml        # 审批矩阵、超时升级机制
│   ├── expense_types.yaml        # 费用类型、会计科目、增值税配置
│   ├── city_mapping.yaml         # 城市名标准化映射
│   └── workflow.yaml             # 流程编排（启用/禁用/失败策略）
├── models/
│   ├── expense.py                # 全部数据模型
│   └── enums.py                  # 枚举（含 FinalStatus）
├── skills/
│   ├── skill_01_receipt.py       # 四重发票校验
│   ├── skill_02_approval.py      # 审批链+超时模拟
│   ├── skill_03_compliance.py    # A/B/C 合规判定 + AmbiguityDetector
│   ├── skill_04_voucher.py       # 记账凭证+增值税拆分
│   └── skill_05_payment.py       # 五重预校验+付款模拟
├── agent/
│   ├── controller.py             # ExpenseController（workflow 编排引擎）
│   └── ambiguity_detector.py     # 五维模糊评分模型
├── rules/
│   ├── policy_engine.py          # 策略引擎（配置驱动，零硬编码）
│   └── city_normalizer.py        # 城市名标准化
├── mock_data/
│   └── sample_reports.py         # 7个测试场景工厂函数
└── tests/
    └── test_full_flow.py         # 7场景端到端 + 单元测试
```

## 配置示例

### 改费用限额（不碰代码）

```yaml
# policy.yaml — 只改数字
limits:
  accommodation_per_night:
    tier_1: { L1: 500, L2: 700, L3: 1000, L4: 不限 }  # ← 改这里
```

### 跳过审批（不碰代码）

```yaml
# workflow.yaml — 改 enabled
pipeline:
  - skill: approval
    enabled: false   # ← 改这里
```

### 新增费用类型（不碰代码）

```yaml
# expense_types.yaml — 加配置
expense_types:
  training:
    name_zh: 培训费
    subtypes:
      - id: external_training
        name_zh: 外部培训
        requires_invoice: true
        accounting_debit: 管理费用-培训费
```

## 运行方式

```bash
# 安装基础依赖
pip install pyyaml

# 运行全部7个场景
python main.py

# 运行测试
python -m unittest tests.test_full_flow -v
```

### 可选：启用 LLM 深度语义分析

当 `ambiguity_score > 50` 时，AmbiguityDetector 会调用 LLM 做深度合规审计。
支持多个提供商（优先级 MiniMax → Claude → fallback 规则模型）。

**使用 MiniMax M2**（OpenAI 兼容接口）：

```bash
pip install openai

# Linux/macOS
export MINIMAX_API_KEY=你的key
# 可选：自定义 base_url 和 model
export MINIMAX_BASE_URL=https://api.minimaxi.com/v1   # 默认（国际站）
export MINIMAX_MODEL=MiniMax-M2                       # 默认

# Windows CMD
set MINIMAX_API_KEY=你的key
set MINIMAX_BASE_URL=https://api.minimaxi.com/v1
set MINIMAX_MODEL=MiniMax-M2

# Windows PowerShell
$env:MINIMAX_API_KEY="你的key"
```

> 如果你用的是 MiniMax 国内站，把 `MINIMAX_BASE_URL` 改成 `https://api.minimax.chat/v1`。
> 模型 ID 以 MiniMax 控制台显示为准。

**使用 Claude**：

```bash
pip install anthropic
export ANTHROPIC_API_KEY=你的key
```

**都不配置** → 自动 fallback 到规则评分模型（零依赖可用）。

## 测试场景

| Case | 场景 | 预期结果 | 验证点 |
|------|------|----------|--------|
| 1 | 正常报销 L1上海 480+80 | COMPLETED | 全A，5步全过 |
| 2 | 重复发票 | REJECTED | Skill-01拦截，不进审批 |
| 3 | 超标拒绝 L1成都住宿420(限350) | REJECTED | 超70>50，C级 |
| 4 | 警告通过 L1成都住宿380(限350) | COMPLETED | 超30≤50，B级 |
| 5 | Shield showcase | PENDING_REVIEW | 城市标准化+4因素触发 |
| 6 | 模式异常 3笔相似餐费 | human_review | 规则A级但模式异常 |
| 7 | 等级差异 同¥500 L1/L2 | L1拒绝/L2通过 | 配置驱动验证 |
