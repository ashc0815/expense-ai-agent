# Quick Attest Flow — 把 Ramp-style agent 流做成一等公民

**Status:** Draft
**Date:** 2026-04-15
**Owner:** @ashley

## 1. 动机

现在 `frontend/employee/submit.html` 是一个 3 步 wizard(Upload → OCR Confirm → Details),即使 OCR 成功用户也要逐字段确认。对比 Ramp 的 agent 流——agent 完成全部决策,用户只需要背书(attest)——我们的主路径还停留在"有 AI 的 form"。

本次改动把这个顺序倒过来:**Ramp 流做成主入口,老 submit.html 降级成 fallback 表单**。新入口的核心是一张"attestation card":金额最大、缩略图可见、所有字段折叠成一行元数据、一个绿色 "✓ AI 已核对" 徽章、一个提交按钮。用户的全部动作压缩到"glance + tap"。

但 fallback 不是二元的——是 3 层降级:card(happy path)→ card with inline fix → full form。大多数小问题应该在第 2 层解决,永远不掉到第 3 层。

## 2. 总体架构 & 文件拓扑

### 新增文件

- `frontend/employee/quick.html`——Ramp-style agent 流的一等公民入口。只做三件事:接收上传、展示 attestation card(含渐进式填充和 inline 编辑)、调用后端 attest 接口。
- `backend/api/routes/quick.py`——新接口 `POST /api/quick/upload`、`GET /api/quick/stream/:id`、`POST /api/quick/attest/:id`。
- `backend/pdf_splitter.py`——多页 PDF 切页工具。

### 保留并降级

- `frontend/employee/submit.html`——沦为 **Layer 3 fallback 表单**。入口只来自三个来源:
  1. quick.html 硬错自动 redirect(带 `?from=quick&draft_id=...`)
  2. quick.html 软错用户点「手填」
  3. 首页菜单里的「手动填表」链接(老用户保底路径)
- 顶部加一个「← 返回 quick」纯 UI 按钮。其余 716 行本期不动。

### 不动

- `/api/drafts` + `/api/chat/stream` + 所有现有 tool(`tool_get_budget_summary`、`tool_extract_receipt_fields`、`tool_classify_category`、`tool_check_duplicate`)全部复用,不重写。
- `quick.html` 的 card 状态本质上就是一张 `Draft` 的视图,提交走现有 `finalize_draft` 路径,所以 manager 审批、查重记录、预算扣减等下游逻辑都不受影响。

### 关键判断

这是**加法不是替换**。Draft model、agent tool、finalize 路径全部复用;只是多了一个"薄外壳"把它们包装成 Ramp 体感。

## 3. Card 状态机 & 事件流

### Card 的 4 个状态

```
EMPTY → PROCESSING → READY → SUBMITTED
           ↓ (硬错)
        REDIRECTED (跳 submit.html)
```

### PROCESSING 期间的 SSE 事件(v1 实际版本)

v1 不做"字段逐个点亮"的分步伪动画,而是 OCR 一次返回(现有 tool 能力)。card 整体进入,各模块逐步变绿:

| 后端事件 | 前端 card 变化 |
|---|---|
| `upload_received` | 缩略图立刻显示(客户端 FileReader) |
| `ocr_done` {amount, merchant, date, line_items} | 金额 + 商户 + 日期整行点亮 |
| `classify_done` {category, confidence} | 分类 chip 点亮 + **Layer 判定** |
| `dedupe_done` {status} | 查重结果融入 badge |
| `budget_done` {signal} | 预算徽章点亮 |
| `card_ready` {layer, actions} | 提交按钮从灰转黑,可点 |

**Layer 判定** 发生在 `classify_done` 之后,由后端根据以下规则返回 `layer` 字段:

```
if ocr 所有 critical 字段 empty OR not-a-receipt:
    layer = "3_hard"  → 前端立刻 redirect 到 submit.html
elif 需要 inline fix 的字段数 >= 3:
    layer = "3_soft"  → card 里展示 "这张票 AI 只认出了 N 个字段,建议 [手填] 或 [重拍]"
elif 1-2 字段需改 OR 分类 confidence 0.5-0.8 OR budget warn:
    layer = "2"  → card ready,inline chip 可编辑
else:
    layer = "1"  → card ready,happy path
```

**阈值(v1 初版,写死在代码)**

- OCR 金额/商户 confidence ≥ 0.8 → 算"高",直接采用
- 分类 confidence ≥ 0.8 → 高,直接采用
- 分类 confidence 0.5–0.8 → 中,进 Layer 2 inline chip
- 分类 confidence < 0.5 → 按"需改字段"处理
- 需改字段数 ≥ 3 → 进 Layer 3_soft

阈值不开 admin 可调;上线后靠 telemetry 收集分布再调整。

### Inline 编辑(Layer 2)

- 需要改的字段显示成黄色 chip(例:`请选项目 ▾`)
- 点 chip 弹出 dropdown / number input,就地编辑
- 编辑完成立刻 `PATCH /api/drafts/:id`,不等提交
- 所有黄色 chip 都变绿后,提交按钮激活
- **约束**:card 最多承载 2 个 inline 字段;≥3 就降级到 3_soft

### 内联问答行

- 位置:提交按钮下面,placeholder `问 AI(仅限这张票)...`
- 用户输入 → `POST /api/chat/stream`,context 绑定 `draft_id`
- 回答以气泡展开在 input 上方,card 整体撑高但不跳页
- **作用域限制**:只接受和当前这张票相关的问题。跨 receipt 的全局问题(例:"我这个月总共花了多少")用户继续走首页的全局 agent 入口

### Splitter(多页 PDF)

- `upload_received` 之后后端先判断是否多页
- 是 → 返回 `split_detected` {pages: N} 事件
- 前端把单张 card 替换成 **N 张 card 的竖向列表**,每张独立跑自己的 stream
- 每张 card 的提交状态独立,全部 attest 完才算本次上传完成

### Layer 转移规则

| 触发 | 行为 |
|---|---|
| 硬错(OCR 空 / 不是票 / 已检测多页但 splitter 失败) | 自动 redirect 到 `submit.html?from=quick&draft_id=...` |
| 软错(≥3 字段需改) | 留在 card,显示 `[手填] [重拍]` 两个按钮 |
| 用户点「手填」 | 跳 `submit.html?from=quick&draft_id=...` |
| 用户点「重拍」 | 清空 card 回到 EMPTY,重新走 upload |
| 从 submit.html 返回 quick | submit.html 顶部的「← 返回 quick」按钮(纯 UI,不是状态回流) |

**Layer 3 是终局**:进了 submit.html 之后没有回到 card 的状态机,最多通过 UI 按钮返回新开一次 quick 流。

## 4. 数据模型 & 后端 API

### 数据模型

**零新增表**。复用现有的 `Draft` model,新增 2 个字段:

- `layer: str`——`"1" | "2" | "3_soft" | "3_hard"`,记录这张 draft 走到了哪一层,telemetry 用
- `entry: str`——`"quick" | "form"`,区分入口,后续分析两条路径的转化率

这两个字段不参与任何业务逻辑判断,只为后续看数据调阈值准备。

### 新接口

```
POST /api/quick/upload         → 接收文件,返回 draft_id(立刻,不等任何 tool)
GET  /api/quick/stream/:id     → SSE 流,推送 §3 表格里的 6 个事件
POST /api/quick/attest/:id     → 把 draft 标记为 user-attested,走 finalize_draft
```

**设计意图**:
- `upload` 立刻返回 `draft_id`,让前端进入 PROCESSING 态。所有耗时工作推给 `stream`。
- `attest` 不是 `submit`——名字刻意区分,提醒后端这是"人工背书"而不是"表单校验"。attest 跳过字段级重复校验,只检查必填字段齐全 + `layer in ("1", "2")`。

### 内部编排

`stream` 内部顺序调:

```
tool_extract_receipt_fields  → emit ocr_done
  ↓
tool_classify_category       → emit classify_done + 判定 layer
  ↓
tool_check_duplicate         → emit dedupe_done
  ↓
tool_get_budget_summary      → emit budget_done
  ↓
emit card_ready {layer, actions}
```

每个 tool 完成就 emit 一个 SSE 事件并 `PATCH draft`。

**不复用 `/api/chat/stream`**——那个接口是给 LLM agent 用的,这里是确定性 pipeline。LLM 不应该参与 card 字段填充的决策,只在"内联问答行"里被召唤。

### PDF Splitter

```python
# backend/pdf_splitter.py
def split(file_bytes: bytes) -> list[bytes]:
    """按页切 PDF。单页返回 [原文件];多页返回 N 个单页 PDF bytes。
    非 PDF 或损坏抛 SplitError。"""
```

用 `pypdf`。多页触发后,每页当作独立 receipt,各自创建一张 draft,前端收到 `split_detected` 后把单 card 替换成 card list。

### 接口失败行为

- OCR 超时(>10s) → emit `ocr_failed`,`layer = "3_hard"`,前端自动 redirect
- 分类 / 查重 / 预算任意一个 tool 失败 → emit `tool_failed` + 降级默认值(分类默认"其他"、查重默认 pass、预算默认 ok),**不阻断 card ready**,但在 card 上显示一个灰色 `⚠ 部分检查未完成` 提示,保持用户可以继续 attest

## 5. 测试策略

### 单元测试(pytest)

- `test_pdf_splitter.py`——单页 PDF 返回 1 项 / 多页 PDF 返回 N 项 / 损坏 PDF 抛 `SplitError`
- `test_quick_layer_decision.py`——layer 判定纯函数,覆盖 9 种输入组合:
  - OCR 全空 → `3_hard`
  - 非发票图 → `3_hard`
  - 多页 PDF(splitter 失败) → `3_hard`
  - OCR ok + 分类 conf ≥ 0.8 + 查重 pass + 预算 ok → `1`
  - 1 字段需改 → `2`
  - 2 字段需改 → `2`
  - 3 字段需改 → `3_soft`
  - 分类 conf 0.5–0.8 → `2`
  - 分类 conf < 0.5 → `2`
- `test_quick_api.py`——`upload` / `stream` / `attest` 三个接口各自的 happy path + 失败分支
- `test_attest_vs_submit.py`——attest 和老的 form submit 都最终调 `finalize_draft`,落库结果完全一致(关键回归测试)

### 集成测试(pytest + TestClient)

- `test_quick_e2e_happy.py`——上传一张清晰发票图,SSE 流按顺序收到 6 个事件,最后 `draft.layer == "1"`,attest 成功
- `test_quick_e2e_hardfail.py`——上传一张纯白图,收到 `ocr_failed`,`draft.layer == "3_hard"`,无法 attest
- `test_quick_e2e_softfail.py`——上传一张只能识别出金额的图,`draft.layer == "3_soft"`,attest 接口返回 422
- `test_quick_e2e_splitter.py`——上传 2 页 PDF,收到 `split_detected`,创建 2 张独立 draft

### 前端手工验证清单(implementation 阶段逐条跑)

1. 清晰发票 → card 2.8 秒内 ready,字段整行点亮 → 各模块变绿的顺序符合 §3 表格
2. 模糊但可识别的票 → card ready,黄 chip 显示需补字段,inline 编辑后按钮激活
3. 纯白图 → 2 秒内页面跳转到 submit.html,顶部显示「← 返回 quick」
4. 2 页 PDF → 展示 2 张 card 列表,每张独立 attest
5. 内联问答"这笔算差旅还是招待?"→ 气泡展开,不跳页
6. 提交按钮在 `card_ready` 前是灰的、不可点
7. ≥3 字段错的票 → card 里显示「手填 / 重拍」,点「手填」跳 submit.html,点「重拍」回到空 card

### 不测

- LLM 答复的质量(模型行为不稳定,靠人工抽检)
- SSE 事件的绝对时序(只测事件顺序和内容)
- 多浏览器兼容(沿用现有基线:只保证 Chrome 现代版)

## 6. Scope

### In scope(v1 本次要做)

1. `frontend/employee/quick.html` 作为 Ramp 流主入口
2. `backend/api/routes/quick.py`(upload / stream / attest)
3. `backend/pdf_splitter.py`
4. `Draft` model 新增 `layer` / `entry` 字段
5. 复用现有 4 个 tool,不重写
6. `submit.html` 顶部加「← 返回 quick」按钮
7. 硬触发自动 redirect,软触发留在 card 给「手填 / 重拍」
8. Layer 2 inline 编辑(最多 2 个字段 chip 可点改)
9. 内联问答行(card 底部,绑定 draft_id 的受限 chat)
10. 多页 PDF splitter(切页后每页一张 card)
11. Telemetry 埋点(§7)

### Non-goals(v1 明确不做)

- **原 submit.html 的重构**——只加一个返回按钮,其余 716 行不动,等 v1 上线看 submit 路径使用率再决定要不要精简
- **首页入口改造**——菜单里「提交报销」同时挂两个链接(「快速」quick / 「手动填表」submit),不做智能路由
- **批量上传队列 UI**——v1 坚持单张 happy path + 多页 PDF 的 N 张列表,不做"收件箱"模型
- **移动端适配**——card 在移动端可用但不做专门优化
- **自定义阈值后台**——阈值写死在代码,不开 admin 可调
- **对话多轮问答的会话持久化**——内联问答行每次对话不保存,刷新即丢

### Known gaps(已知,记账,本期不解决)

- **相同商户的历史偏好学习**("这人过去 3 次在海底捞都报餐饮")——等有数据后做 `tool_get_merchant_history`
- **预算 blocked 的特批流程**——预算硬超阈值时 card 降级到 3_soft 让用户走 submit.html 的「申请特批」路径;v1 不在 card 里做特批 UI
- **查重撞单的详情对比**——card 里只显示 `⚠ 疑似重复`,详情对比必须进 submit.html
- **字段逐个点亮的伪动画**——v1 OCR 一次返回所有字段,"金额先点亮、商户后点亮"的体感退到 v2

## 7. Telemetry

每次 card 流程结束往 `telemetry_events` 表写一条:

```
draft_id, entry, final_layer, ocr_confidence_min, classify_confidence,
fields_edited_count, time_to_attest_ms, attest_or_abandoned
```

只写不读,v1 不做 dashboard。目的是后续跑 SQL 看真实分布,调阈值用。

## 8. Open Questions

无。所有关键决策已在 brainstorm 阶段闭环。
