**[EN](README.md)** | **[中文](README_CN.md)**

# ExpenseFlow

AI 驱动的企业报销管理平台 — 从发票提交到付款入账的全流程自动化，内置 5-Skill 合规审核管道、对话式 Agent 助手和 Eval 评估框架。

> 核心设计原则：区分 Workflow 和 Agent、按观众分层、工具白名单防 prompt injection。
> 参照 [Anthropic *Building Effective Agents*](https://www.anthropic.com/research/building-effective-agents)（2024）。

---

## 目录

- [这个项目做了什么](#这个项目做了什么)
- [架构总览](#架构总览)
- [5-Skill 合规审核管道](#5-skill-合规审核管道)
- [对话式 Agent](#对话式-agent)
- [核心设计决策](#核心设计决策)
- [审批与预算流程](#审批与预算流程)
- [角色权限（RBAC）](#角色权限rbac)
- [Eval 评估平台](#eval-评估平台)
- [API 总览](#api-总览)
- [我们故意不做的 5 件事](#我们故意不做的-5-件事)
- [目录结构](#目录结构)
- [快速启动](#快速启动)
- [技术栈](#技术栈)

---

## 这个项目做了什么

ExpenseFlow 模拟了一套完整的企业报销系统：员工上传发票 -> AI 自动审核（OCR、规则引擎、模糊检测）-> 经理审批（附 AI 决策解释）-> 财务复核 -> 凭证生成 -> 付款执行。

**核心差异化：**

1. **5-Skill 合规审核管道** -- 发票验证、审批链、合规检查（含 AmbiguityDetector 五维模糊评分）、凭证生成、付款执行，全部配置驱动
2. **对话式 Agent** -- 员工通过自然语言完成报销（OCR 识别 -> 分类建议 -> 查重 -> 预算检查），经理获得 AI 解释卡辅助审批决策
3. **Eval 评估框架** -- YAML 定义测试用例，覆盖 Agent 路由、风险分级、RBAC 权限、工具白名单安全

---

## 架构总览

```
员工提交发票
  |
  v
+--------------------------- FastAPI Backend ----------------------------+
|                                                                        |
|  +--------------+    +--------------------------------------+          |
|  |  Chat Agent  |    |     5-Skill 合规审核管道              |          |
|  |              |    |                                      |          |
|  | - Submit     |    |  发票验证 -> 审批链 -> 合规检查        |          |
|  | - Q&A        |    |            | AmbiguityDetector       |          |
|  | - Explain    |    |  凭证生成 -> 付款执行                 |          |
|  +------+-------+    +--------------+-----------------------+          |
|         |                           |                                  |
|  +------v---------------------------v--------------------------+       |
|  |                     数据层                                  |       |
|  |  SQLAlchemy Async | Submissions | Drafts | Budgets          |       |
|  |  Employees | AuditLogs | CostCenterBudgets                  |       |
|  +-------------------------------------------------------------+       |
|                          |                                             |
|  +-----------------------v---------------------------------+           |
|  |                  YAML 配置层                             |           |
|  |  policy | approval_flow | expense_types | workflow      |           |
|  |  city_mapping | fx_rates                                |           |
|  +---------------------------------------------------------+           |
+------------------------------------------------------------------------+
       |                                      |
  +----v------+                      +--------v--------+
  | 前端       |                      | Eval 评估框架   |
  | 员工端     |                      | YAML 测试用例   |
  | 经理端     |                      | Agent 行为      |
  | 财务端     |                      | 验证            |
  +-----------+                      +-----------------+
```

---

## 5-Skill 合规审核管道

每笔报销提交后，后台异步执行 5 个 Skill，全部由 `workflow.yaml` 编排：

| Skill | 功能 | 关键能力 |
|-------|------|----------|
| **01 发票验证** | 发票格式、抬头、查重、日期校验 | 发票号全局唯一约束；城市名标准化（"SH"/"沪"/"上海" -> 统一） |
| **02 审批链** | 按费用类型 x 金额 x 员工等级构建审批链 | 超时升级（24h 提醒 -> 48h 升级 -> 72h 自动升级）；等级豁免 |
| **03 合规检查** | 逐行项 A/B/C 合规判定 + AmbiguityDetector | 五维模糊评分；score >50 触发 Claude 深度语义分析 |
| **04 凭证生成** | 会计分录、增值税拆分 | 专票进项税自动拆分；借贷平衡校验 |
| **05 付款执行** | 五重预校验 + 付款模拟 | >=5000 银行转账，<5000 备用金 |

**Shield 机制：** 当 Skill-03 的模糊评分触发人工复核（30-70 分）或建议拒绝（>70 分），管道停止，标记 `PENDING_REVIEW`，等待人工介入。

**配置驱动：** 跳过审批只需 `workflow.yaml: approval.enabled: false`，改费用限额只需改 `policy.yaml` 的数字 -- 零代码适配不同客户。

### AmbiguityDetector -- 五维模糊评分模型

| 因素 | 权重 | 触发条件 |
|------|------|----------|
| 描述模糊度 | 25% | 描述 <10字 或含泛化词（"其他""杂项""费用"） |
| 金额边界 | 20% | 金额在限额的 90%-110% 区间 |
| 模式异常 | 25% | 7天内 >=3笔同类型 +/-15% 金额 |
| 时间异常 | 15% | 周末的餐费/交通费 |
| 城市不匹配 | 15% | 城市名未识别 或 标准化前后不一致 |

评分 -> 决策：`<30` 自动通过 / `30-70` 人工复核 / `>70` 建议拒绝

Score >50 时调用 Claude API 做深度语义分析（未配置 Key 则回退到规则评分模型）。

---

## 核心设计决策

### 1. Workflow vs Agent：诚实申报

> "Agentic AI" 这个词被滥用。这里明确标出哪里是 agent，哪里是 workflow。

| 位置 | 实现方式 | 实质 | 原因 |
|------|---------|------|------|
| 员工 submit：happy path（上传发票 -> 填字段）| OCR -> dup_check -> suggest -> write，线性流水线 | **Workflow** | 步骤预定，LLM 不需要决策 |
| 员工 submit：用户改字段（"把金额改成 380"）| 需要 Real LLM 解析意图 | **真 Agent** | LLM 需解析意图、动态选 tool |
| 员工 my-reports QA drawer | 关键词匹配 -> 单 tool -> 格式化 | **Workflow** | 单 tool 调用，无决策环节 |
| 5-Skill 审核管道 | 5 步顺序执行，policy_engine 硬规则 | **Workflow（故意）** | 合规要求确定性，不允许 LLM 改流程 |
| 经理/财务 AI 解释卡 | 调只读 tool，组装风险评估 + 审批建议 | **Agent（轻量）** | 需自行决定调哪些 tool 收集证据 |

### 2. 工具白名单（Prompt Injection 防线）

`TOOL_REGISTRY` 把每个 `agent_role` 映射到它允许调用的 tool 列表。白名单在两处强制执行：

1. **LLM 只看到白名单内的 tool 定义**（喂给 `tools=` 参数的列表被过滤）
2. **Dispatcher 二次校验**：即使 LLM 幻觉出白名单外的 tool 名，dispatch 前也会被拒绝

```python
TOOL_REGISTRY = {
    "employee_submit": ["extract_receipt_fields", "suggest_category",
                        "check_duplicate_invoice", "get_my_recent_submissions",
                        "update_draft_field", "check_budget_status"],   # 有写权限
    "employee_qa":     ["get_my_recent_submissions", "get_report_detail",
                        "get_spend_summary", "get_budget_summary",
                        "get_policy_rules"],                             # 全只读
    "manager_explain": ["get_submission_for_review",
                        "get_employee_submission_history"],              # 全只读
}
```

> Agent 永远没有 `submit_expense` / `approve` 工具。这不是 limitation，是设计决策。

### 3. 渐进式审计时间线（Phased Timeline）

**问题**：原实现在提交时就把 5-skill 全部结果写入 `audit_report.timeline`，导致"凭证生成 / 付款执行"在经理批准之前就出现在 AI 解释卡里。

**修复**：

```
提交后         timeline = [step0, step1, step2]          phase="submit"
经理批准后     timeline.append(step3: "凭证已生成")      phase="manager_approved"
财务批准后     timeline.append(step4: "付款已执行")      phase="finance_approved"
```

`audit_report.timeline` 永远只反映"已经发生的事"。

### 4. 按观众分层（Audience Layering）

AI 解释卡的信息分两层：

| 信息层 | 谁看 | 展示条件 |
|--------|------|----------|
| 推荐决策、flags、advisory | 所有用户（经理/财务）| 始终显示 |
| tool 调用明细、agent_role | 开发者 / 面试官 | `auth.isDev()` = true |

激活 dev 模式：URL 加 `?dev=1`，或点击导航栏工具按钮。

---

## 审批与预算流程

### 状态机

```
processing -> reviewed -> manager_approved -> finance_approved -> exported
                               |                    |
                           rejected              rejected
```

### 预算管控

每个成本中心按季度设置预算，提交时实时检查：
- **info**（75%-95%）：提醒接近预算
- **blocked**（>=95%）：自动拦截，需财务解锁
- **over_budget**（>100%）：超预算警告

### 风险等级

| 等级 | AI 推荐 | 风险分 | 含义 |
|------|---------|--------|------|
| T1 | approve | <=25 | 发票合规、金额正常、描述具体 |
| T2 | approve | 25-50 | 低风险，有轻微注意项 |
| T3 | review  | 50-75 | 需人工核对，金额偏高或描述模糊 |
| T4 | reject  | >75 | 高风险，金额异常 / 凭证缺失 |

---

## 角色权限（RBAC）

| 角色 | 能力 |
|------|------|
| **employee** | 提交报销、查看自己的报销、对话助手 |
| **manager** | 审批下属报销、查看 AI 解释卡 |
| **finance_admin** | 财务审批、解锁预算拦截、导出凭证、批量操作 |
| **admin** | 员工管理、政策配置、审计日志、预算设置 |

> **Mock 模式**：`AUTH_MODE=mock`（默认），用 `X-User-Id`/`X-User-Role` 请求头模拟身份。浏览器端通过导航栏角色切换下拉框或 URL 参数 `?as=manager` 切换角色。

---

## Eval 评估平台

Eval 系统分三层：**YAML 测试数据集** 定义测什么，**统一评估引擎** 用代码评分器执行测试，**Eval Observatory** 提供可视化仪表盘 + REST API 浏���结果、对比运行、管理 Prompt���

### 架构

```
+-------------------+     pytest      +---------------------+     POST     +-------------------+
|  YAML 数据集       | ------------->  |  统一评估引擎        | ----------> |  Observatory API  |
|                   |                 |  test_eval_harness   |             |  /api/eval/runs   |
|  fraud_llm_rules  |   逐 case      |  .py                 |  运行结果   |                   |
|  fraud_rules_det  |   代码评分      |                      |  + 元数据   |  存入 DB:         |
|  ambiguity_detect | <----------->  |  通过率汇总           |             |  EvalRun, LLMTrace|
|  layer_decision   |  code_graders  |  P/R/F1 按组件统计    |             |                   |
|  category_classif |  .py           |                      |             |  Eval 仪表盘       |
+-------------------+                +---------------------+             +-------------------+
```

### 被评估的 5 个组件

| 组件 | 数据集文件 | 用例数 | 测试内容 |
|------|-----------|--------|----------|
| **确定性欺诈规则** | `fraud_rules_deterministic.yaml` | ~60 | 14 条规则函数（同餐重复、地理冲突、阈值接近、周末频率、整数金额、连号发票、商户类别不匹配、离职前突击、汇率套利、共谋模式、供应商频率、季节异常、幽灵员工、时间戳冲突）。每条提供输入 + 期望信号（触发/不触发） |
| **LLM 欺诈分析** | `fraud_llm_rules.yaml` | ~15 | GPT-4o 语义分析（模板检测、票据矛盾、人均金额合理性、描述模糊度评分）。支持 pass^k 多次试验以应对非确定性输出。需要 `OPENAI_API_KEY` |
| **模糊检测器** | `ambiguity_detector.yaml` | ~12 | 五维评分模型：分数范围验证、触发因素核查、推荐动作检查（auto_pass / human_review / suggest_reject） |
| **分层决策** | `layer_decision.yaml` | ~20 | 快速提交分层路由：给定 OCR/分类/查重/预算信号，验证正确的层级分配（green/yellow/red） |
| **类别分类器** | `category_classifier.yaml` | ~20 | 商户名 -> 费用类别映射（餐费/交通/住宿/娱乐/其他） |

### YAML 测试用例格式

所有用例遵循统一结构：

```yaml
- id: rule1_positive_overlap
  component: fraud_rules_deterministic
  rule: duplicate_attendee
  description: "A和B同日同商户报餐，B的attendee含A -> 应触发"
  input:
    submissions:
      - id: "s1"
        employee_id: "emp-A"
        amount: 200
        category: "meal"
        date: "2026-04-10"
        merchant: "海底捞"
        attendees: ["emp-B"]
      - id: "s2"
        employee_id: "emp-B"
        amount: 180
        category: "meal"
        date: "2026-04-10"
        merchant: "海底捞"
        attendees: ["emp-A"]
  expect:
    has_signal: true
    rule_name: "duplicate_attendee"
```

新增测试场景只需在 YAML 里加一条，不需要改 Python。

### 代码评分器（评分环节无 LLM）

所有评分都是确定性的 -- `backend/tests/graders/code_graders.py`：

| 评分器 | 检查内容 |
|--------|----------|
| `grade_score_range` | 实际分数在 [lo, hi] 范��内 |
| `grade_field_match` | 字段值精确匹配 |
| `grade_enum_in` | 值在允许集合内 |
| `grade_list_contains` | 列表包含所有必需项 |
| `grade_bool` | 布尔匹配（用于 `has_signal`） |
| `classify_detection` | TP/FP/FN/TN 分类，计算 P/R/F1 |

通用 `grade_case(actual_output, expect)` 函数自动分派：`*_range` 键用范围评���器，`has_signal` 用布尔评分器，`layer` 用精确匹配，等等。

### 6 因素可复现性追踪

每次评估运行捕获 6 个因素到 `eval_config.json`，确保可复现：

| 因素 | 追踪内容 | 示例 |
|------|----------|------|
| **Prompt 版本** | 当前激活的 prompt 模板 | `v1` |
| **模型** | LLM 模型 + 快照 | `gpt-4o-2025-03-01` |
| **采样参数** | temperature, top_p, max_tokens | `0.0, 1.0, 1024` |
| **配置阈值** | 规则级别的调参旋钮 | `threshold_proximity_pct: 0.03` |
| **解析版本** | LLM 输出如何解析为结构化数据 | `v1 (JSON + regex fallback)` |
| **数据集哈希** | 所有 YAML 数据集文件的联合 MD5 | 自动计算 |

当通过率下降时，通��� `GET /api/eval/runs/{a}/diff/{b}` 对比两次运行，精确定位哪个因素变了、哪些用例回退了。

### Observatory 仪表盘 & API

**Web 界面** `http://localhost:8000/eval/dashboard.html`：
- KPI 卡片：总用例数、通过率、按组件分解
- 通过率趋势图（最近 10 次运行）
- 逐 case 下钻：输入、期望、实际输出、分类
- 运行对比（diff 视图）：元数据变化 + case 回退/改进
- Prompt 管理：查看/编辑/版本化 prompt 模板，设置激活版本
- 一键触发评估（后台运行 pytest）

**REST API** `/api/eval/`：

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/runs` | 评估运行列表（分页） |
| `GET` | `/runs/{id}` | 单次运行详情 + 所有 case 结果 |
| `POST` | `/runs` | 记录评估运行（由引擎自动调用） |
| `GET` | `/runs/{a}/diff/{b}` | 对比两次运行：元数据差异 + case 回退 |
| `GET` | `/traces` | LLM 调用追踪列表（可按组件/错误状态过滤） |
| `GET` | `/traces/{id}` | 单条追踪 + 完整 prompt & response |
| `GET` | `/stats` | 聚合统计：通过率趋势 + 组件错误率 |
| `GET/PUT` | `/config` | 读取/更新 6 因素配置 |
| `POST` | `/trigger` | 触发评估（后台 pytest 子进程） |
| `GET` | `/trigger/status` | 检查评估是否正在运行 |
| `GET` | `/prompts` | 所有 prompt 模板及版本数 |
| `GET` | `/prompts/{key}` | 完整 prompt + 所有版本 |
| `PUT` | `/prompts/{key}/versions/{v}` | 创建/更新 prompt 版本 |
| `PUT` | `/prompts/{key}/active` | 设置激活的 prompt 版本 |

### 检测质量指标

对确定性欺诈规则，引擎按规则计算 **Precision / Recall / F1**：

```
  -- Detection Quality (P/R) --
  fraud_rule_duplicate_attendee:    P=100% R=100% F1=100%
  fraud_rule_threshold_proximity:   P=100% R=100% F1=100%
  fraud_rule_weekend_frequency:     P=100% R=67%  F1=80%
```

分类矩阵使用业务语言：
- **正确标记** (TP) = 规则正确触发
- **误报** (FP) = 规则不该触发但触发了
- **漏报** (FN) = 规则该触发但没触发
- **正确放行** (TN) = 规则正确地没有触发

### 运行评估

```bash
# 运行全部 eval case（确定性部分无需 API Key）
pytest backend/tests/test_eval_harness.py -v

# 只跑欺诈规则
pytest backend/tests/test_eval_harness.py -v -k deterministic

# 只跑模糊检测器
pytest backend/tests/test_eval_harness.py -v -k ambiguity

# 带 LLM 欺诈分析（需要 OPENAI_API_KEY）
OPENAI_API_KEY=sk-... pytest backend/tests/test_eval_harness.py -v -k llm

# Agent 行为评估（独立引擎）
pytest backend/tests/test_agent_eval.py -v -s

# 通过 Observatory API 触发（从仪表盘一键运行）
curl -X POST http://localhost:8000/api/eval/trigger \
  -H 'Content-Type: application/json' -d '{"component": "all"}'
```

结果自动 POST 到 Observatory API（如果服务在运行）；否则保存到 `backend/tests/eval_last_run.json`，稍后导入

---

## API 总览

**报销单（Reports — 员工把多条行项目打包成一张报销单整体提交）**

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| `POST` | `/api/reports` | employee | 新建报销单 |
| `GET` | `/api/reports` | employee | 我的报销单列表 |
| `GET` | `/api/reports/{id}` | all | 报销单详情（员工只能看自己的） |
| `POST` | `/api/reports/{id}/submit` | employee | 提交报销单进入审批 |
| `POST` | `/api/reports/{id}/withdraw` | employee | 撤回已提交/已批准的报销单 |
| `POST` | `/api/reports/{id}/resubmit` | employee | 重新提交 `needs_revision` 的报销单 |
| `POST` | `/api/reports/{id}/approve` | manager | 经理批准 |
| `POST` | `/api/reports/{id}/reject` | manager | 经理拒绝 |
| `POST` | `/api/reports/{id}/return` | manager | 退回修改（`needs_revision`） |
| `POST` | `/api/reports/{id}/finance-approve` | finance_admin | 财务批准 |
| `POST` | `/api/reports/{id}/finance-reject` | finance_admin | 财务拒绝 |
| `PATCH` | `/api/reports/{id}/title` | employee | 重命名报销单 |
| `PATCH` | `/api/reports/{id}/lines/{sid}` | employee | 编辑某条行项目 |
| `DELETE` | `/api/reports/{id}/lines/{sid}` | employee | 删除某条行项目 |
| `DELETE` | `/api/reports/{id}` | employee | 删除空报销单（仅限 open + 0 条目） |

**行项目 Submissions**

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| `POST` | `/api/submissions` | employee | 提交报销单，返回 202 + 后台 AI 审核 |
| `GET` | `/api/submissions/{id}` | all | 查询单条（员工只能查自己的） |
| `GET` | `/api/submissions` | all | 列表（员工只见自己的） |
| `POST` | `/api/submissions/{id}/approve` | manager | 经理批准 |
| `POST` | `/api/submissions/{id}/reject` | manager | 经理拒绝 |
| `POST` | `/api/finance/submissions/{id}/approve` | finance_admin | 财务批准 + 凭证号 |
| `POST` | `/api/finance/submissions/{id}/reject` | finance_admin | 财务拒绝 |
| `GET` | `/api/finance/export/preview` | finance_admin | 待导出列表 |
| `POST` | `/api/finance/export` | finance_admin | 批量导出 CSV |

**Agent / Chat**

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| `POST` | `/api/chat/drafts` | employee | 新建 draft |
| `POST` | `/api/chat/drafts/{id}/receipt` | employee | 上传发票到 draft |
| `POST` | `/api/chat/drafts/{id}/message` | employee | Agent 1：submit chat（SSE） |
| `POST` | `/api/chat/drafts/{id}/submit` | employee | Draft 转为正式 submission |
| `POST` | `/api/chat/qa/message` | employee | Agent 2：只读 QA（SSE） |
| `POST` | `/api/chat/explain/{id}` | manager / finance_admin | Agent 3：AI 解释卡（JSON） |

**预算**

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| `GET` | `/api/budget/status/{cost_center}` | all | 成本中心预算状态 |
| `GET` | `/api/budget/snapshot/me` | employee | 我的预算概况 |
| `GET/PUT` | `/api/budget/policies/{cc}` | finance_admin | 预算策略配置 |
| `GET/POST` | `/api/budget/amounts` | finance_admin | 预算额度管理 |

**管理**

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| `GET/PUT` | `/api/admin/policy` | admin | 报销政策 |
| `GET` | `/api/admin/audit-log` | admin | 审计日志 |
| `GET` | `/api/admin/stats` | admin | 汇总统计 |
| `GET` | `/api/users/me` | all | 当前用户信息 |

---

## 目录结构

```
backend/
  main.py                          # FastAPI 入口
  config.py                        # DATABASE_URL 等环境配置
  storage.py                       # 文件存储抽象（Local / R2）
  api/
    middleware/auth.py              # RBAC 认证中间件（Mock / Clerk 双模式）
    routes/
      submissions.py               # 报销提交 + 5-Skill 管道触发
      chat.py                      # 对话 Agent（3 种角色）
      reports.py                   # 报销单管理
      approvals.py                 # 经理审批 + 渐进 timeline
      finance.py                   # 财务审批 + 凭证号 + CSV 导出
      budget.py                    # 预算管理
      fx.py                        # 外币汇率转换
      admin.py                     # 政策配置 / 审计日志 / 统计
      employees.py                 # 员工档案 CRUD
      ocr.py                       # OCR 识别接口
      eval.py                      # Eval 仪表盘 API
  db/store.py                      # SQLAlchemy async ORM + CRUD
  quick/
    pipeline.py                    # 快速提交管道编排
    layer_decision.py              # 分层决策引擎
    finalize.py                    # 草稿 -> 正式提交转换
  services/
    fraud_rules.py                 # 确定性欺诈检测规则
    llm_fraud_analyzer.py          # LLM 欺诈分析
    fx_service.py                  # 汇率服务
    config_loader.py               # YAML 配置加载器
    trace.py                       # 调用链追踪
  tests/
    eval_cases.yaml                # Eval harness 用例
    eval_datasets/                 # Eval YAML 测试数据集
    graders/                       # 自定义评分器
    test_*.py                      # 单元 + 集成测试
agent/
  controller.py                    # ExpenseController -- workflow 编排引擎
  ambiguity_detector.py            # 五维模糊评分 + Claude API 深度分析
skills/
  skill_01_receipt.py              # 四重发票校验
  skill_02_approval.py             # 审批链 + 超时升级
  skill_03_compliance.py           # A/B/C 合规判定 + Shield
  skill_04_voucher.py              # 会计凭证 + 增值税拆分
  skill_05_payment.py              # 五重预校验 + 付款模拟
  skill_fraud_check.py             # 欺诈检查 Skill
config/
  policy.yaml                      # 费用标准、城市分级、员工等级限额
  approval_flow.yaml               # 审批矩阵、超时升级机制
  expense_types.yaml               # 费用类型、会计科目、增值税配置
  workflow.yaml                    # 管道编排（启用/禁用/失败策略）
  city_mapping.yaml                # 城市名标准化映射
  fx_rates.yaml                    # 外币汇率
frontend/
  employee/                        # 员工端：提交、草稿、报销单、历史
  manager/                         # 经理端：审批队列
  finance/                         # 财务端：复核、导出
  admin/                           # 管理端：政策、员工、审计日志
  eval/                            # Eval 仪表盘
  shared/                          # 公共 JS/CSS、API 封装、认证
models/                            # 数据模型（Pydantic）
rules/                             # 策略引擎 + 城市标准化
mock_data/                         # 7 个测试场景工厂函数
scripts/seed_demo_data.py          # 演示数据种子脚本
Dockerfile / docker-compose.yml    # 容器化部署
requirements.txt                   # Python 依赖
```

---

## 快速启动

```bash
# 1. 安装依赖
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. （可选）植入演示数据
python scripts/seed_demo_data.py

# 3. 启动服务（默认 MockLLM 模式，全本地，无需 API Key）
uvicorn backend.main:app --reload --port 8000
```

**访问入口**（mock 模式下可通过导航栏下拉框或 URL 参数 `?as=<role>` 切换角色）：

| 角色 | 链接 |
|------|------|
| 员工 | `http://localhost:8000/employee/quick.html` |
| 经理 | `http://localhost:8000/manager/queue.html` |
| 财务 | `http://localhost:8000/finance/review.html` |
| 管理员 | `http://localhost:8000/admin/dashboard.html` |
| Eval Observatory | `http://localhost:8000/eval/dashboard.html` |
| OpenAPI 文档 | `http://localhost:8000/docs` |

**5 分钟端到端演练：**

| 步骤 | 角色 | 操作 |
|------|------|------|
| 1 | employee | `quick.html` → 上传任意发票 → AI 识别字段 → 确认提交 |
| 2 | — | AI 后台审核（1–3 秒）；`my-reports.html` 轮询到 `status=reviewed` |
| 3 | manager | `queue.html` → 点开报销单 → 查看 AI 解释卡 → 点通过 |
| 4 | finance_admin | `review.html` → 通过后生成凭证号 |
| 5 | finance_admin | `export.html` → 批量导出 CSV |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUTH_MODE` | `mock` | `mock`（开发）/ `clerk`（生产）|
| `DATABASE_URL` | SQLite 本地文件 | 生产用 `postgresql+asyncpg://...` |
| `EVAL_DATABASE_URL` | SQLite `concurshield_eval.db` | 与业务库物理隔离，存 LLM traces 和 eval runs，trace 增长不影响主库性能 |
| `STORAGE_BACKEND` | `local` | `local` / `r2`（Cloudflare R2）|
| `ANTHROPIC_API_KEY` | -- | 可选：AmbiguityDetector 深度分析（score >50 时触发） |
| `OPENAI_API_KEY` | -- | 可选：对话 Agent 使用 GPT-4o |
| `OPENAI_MODEL` | `gpt-4o` | 配置 `OPENAI_API_KEY` 时可覆盖默认模型 |
| `AGENT_USE_REAL_LLM` | -- | 设为 `1` + 提供 API Key -> 切换 RealLLM |

### MockLLM vs RealLLM

| 模式 | 条件 | 行为 |
|------|------|------|
| **MockLLM**（默认）| 无需 API Key | 确定性状态机：happy path 线性跑完，keyword routing，deterministic eval |
| **RealLLM（GPT-4o）** | `OPENAI_API_KEY` + `AGENT_USE_REAL_LLM=1` | GPT-4o 真实推理，解锁"用户改字段"Agent 行为 |

---

## 我们故意不做的 5 件事

| # | 没做的事 | 为什么 |
|---|---------|--------|
| 1 | **5-Skill 管道改成 Agent** | 合规要求确定性可审计，把法律责任从规则转移到 LLM 是不可接受的 |
| 2 | **Agent 拥有 submit / approve 工具** | 白名单是防注入最后一道闸，破开等于拆防线 |
| 3 | **审批页加 chat drawer** | 经理一天审 30 单，多 30 秒/单 = 一天多 15 分钟，经理会直接关 AI |
| 4 | **AI 自动提交报销** | 法律责任在员工，Submit 必须由人 confirm（SAP Concur Joule 2026 同样决策） |
| 5 | **一上来就接 Agent SDK** | MVP 原生 tool calling 够用；SDK 价值在 subagents/memory，当前用不上 |

---

## 技术栈

- **Backend**: FastAPI, SQLAlchemy (async), aiosqlite/asyncpg
- **AI**: Claude API (AmbiguityDetector), OpenAI GPT-4o (Chat Agent), MockLLM (默认)
- **Frontend**: 原生 HTML/JS（无框架依赖）
- **Database**: SQLite (开发) / PostgreSQL (生产)
- **Config**: YAML 配置驱动（policy, workflow, approval, expense types, city mapping）
