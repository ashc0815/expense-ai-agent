**[EN](README.md)** | **[中文](README_CN.md)**

# ExpenseFlow

AI-powered enterprise expense management platform — end-to-end automation from receipt submission to payment, with a built-in 5-Skill compliance pipeline, conversational Agent assistant, and Eval framework.

> Core design principles: distinguish Workflow from Agent, audience-layered presentation, tool whitelist as prompt-injection defense.
> Reference: [Anthropic *Building Effective Agents*](https://www.anthropic.com/research/building-effective-agents) (2024).

---

## Table of Contents

- [What This Project Does](#what-this-project-does)
- [System Architecture](#system-architecture)
- [5-Skill Compliance Pipeline](#5-skill-compliance-pipeline)
- [Conversational Agent](#conversational-agent)
- [Design Decisions](#design-decisions)
- [Approval & Budget Workflow](#approval--budget-workflow)
- [Role-Based Access Control (RBAC)](#role-based-access-control-rbac)
- [Eval Platform](#eval-platform)
- [API Overview](#api-overview)
- [5 Things We Deliberately Don't Do](#5-things-we-deliberately-dont-do)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Running Tests](#running-tests)
- [Tech Stack](#tech-stack)

---

## What This Project Does

ExpenseFlow simulates a complete enterprise reimbursement system: employee uploads receipt → AI auto-review (OCR, rules engine, ambiguity detection) → manager approval (with AI decision explanation) → finance review → voucher generation → payment execution.

**Core Differentiators:**

1. **5-Skill Compliance Pipeline** — Receipt validation, approval chain, compliance check (with AmbiguityDetector 5-factor scoring), voucher generation, payment execution — all configuration-driven
2. **Conversational Agent** — Employees complete reimbursements via natural language (OCR → category suggestion → dedup check → budget check); managers get AI explanation cards for approval decisions
3. **Eval Framework** — YAML-defined test cases covering Agent routing, risk tiering, RBAC permissions, and tool whitelist security

---

## System Architecture

```
Employee submits receipt
  |
  v
+--------------------------- FastAPI Backend ----------------------------+
|                                                                        |
|  +--------------+    +--------------------------------------+          |
|  |  Chat Agent  |    |     5-Skill Compliance Pipeline      |          |
|  |              |    |                                      |          |
|  | - Submit     |    |  Receipt -> Approval -> Compliance   |          |
|  | - Q&A        |    |            | AmbiguityDetector       |          |
|  | - Explain    |    |  Voucher -> Payment                  |          |
|  +------+-------+    +--------------+-----------------------+          |
|         |                           |                                  |
|  +------v---------------------------v--------------------------+       |
|  |                     Data Layer                              |       |
|  |  SQLAlchemy Async | Submissions | Drafts | Budgets          |       |
|  |  Employees | AuditLogs | CostCenterBudgets                  |       |
|  +-------------------------------------------------------------+       |
|                          |                                             |
|  +-----------------------v---------------------------------+           |
|  |                  YAML Config Layer                      |           |
|  |  policy | approval_flow | expense_types | workflow      |           |
|  |  city_mapping | fx_rates                                |           |
|  +---------------------------------------------------------+           |
+------------------------------------------------------------------------+
       |                                      |
  +----v------+                      +--------v--------+
  | Frontend  |                      | Eval Framework  |
  | Employee  |                      | YAML test cases |
  | Manager   |                      | Agent behavior  |
  | Finance   |                      | verification    |
  +-----------+                      +-----------------+
```

---

## 5-Skill Compliance Pipeline

After each expense submission, the backend asynchronously executes 5 Skills, all orchestrated by `workflow.yaml`:

| Skill | Function | Key Capabilities |
|-------|----------|-----------------|
| **01 Receipt Validation** | Invoice format, header, dedup, date checks | Global unique invoice number constraint; city name normalization ("SH"/"Shanghai" → unified) |
| **02 Approval Chain** | Build approval chain by expense type x amount x employee level | Timeout escalation (24h remind → 48h escalate → 72h auto-escalate); level exemptions |
| **03 Compliance Check** | Per-line-item A/B/C compliance + AmbiguityDetector | 5-factor ambiguity scoring (description vagueness / amount boundary / pattern anomaly / time anomaly / city mismatch); score >50 triggers Claude deep analysis |
| **04 Voucher Generation** | Accounting entries, VAT splitting | Auto-split input tax for special VAT invoices; debit-credit balance validation |
| **05 Payment Execution** | 5-point pre-check + payment simulation | >=5000 bank transfer, <5000 petty cash |

**Shield Mechanism:** When Skill-03's ambiguity score triggers human review (30-70) or suggests rejection (>70), the pipeline stops and marks `PENDING_REVIEW`, awaiting manual intervention.

**Configuration-Driven:** Skip approval by setting `workflow.yaml: approval.enabled: false`; change expense limits by editing numbers in `policy.yaml` — zero code changes to adapt for different clients.

### AmbiguityDetector — 5-Factor Scoring Model

| Factor | Weight | Trigger Condition |
|--------|--------|-------------------|
| Description vagueness | 25% | Description <10 chars or contains generic words ("other", "misc", "expense") |
| Amount boundary | 20% | Amount within 90%-110% of policy limit |
| Pattern anomaly | 25% | >=3 same-category submissions within 7 days, amounts within +/-15% |
| Time anomaly | 15% | Meal/transport expenses on weekends |
| City mismatch | 15% | City name unrecognized or inconsistent before/after normalization |

Score → Decision: `<30` auto-pass / `30-70` human review / `>70` suggest reject

When score >50, calls Claude API for deep semantic analysis (falls back to rule-based scoring if no API key configured).

---

## Conversational Agent

Three Agent roles, each with independent tool whitelists (preventing prompt injection):

| Role | Scenario | Available Tools |
|------|----------|----------------|
| **employee_submit** | Employee filling reimbursement | OCR extraction, category suggestion, invoice dedup, draft editing, budget query |
| **employee_qa** | Employee querying history | View recent expenses, expense details, spend summary, budget status (read-only) |
| **manager_explain** | Manager approval assistance | View pending expenses, employee history → output risk assessment + approval recommendation |

**LLM Abstraction:** Defaults to MockLLM (deterministic state machine, no API Key needed, ideal for demo and testing). Set `OPENAI_API_KEY` + `AGENT_USE_REAL_LLM=1` to switch to GPT-4o.

---

## Design Decisions

### 1. Workflow vs Agent: Honest Labeling

> "Agentic AI" is overused. Here we explicitly label what's an agent and what's a workflow.

| Location | Implementation | Reality | Rationale |
|----------|---------------|---------|-----------|
| Employee submit: happy path (upload receipt → fill fields) | OCR → dup_check → suggest → write, linear pipeline | **Workflow** | Steps are predetermined, LLM doesn't need to decide |
| Employee submit: user edits fields ("change amount to 380") | Requires Real LLM to parse intent | **True Agent** | LLM must parse intent, dynamically select tools |
| Employee my-reports QA drawer | Keyword match → single tool → format | **Workflow** | Single tool call, no decision-making |
| 5-Skill compliance pipeline | 5 sequential steps, policy_engine hard rules | **Workflow (intentional)** | Compliance requires determinism; LLM must not alter the flow |
| Manager/Finance AI explanation card | Calls read-only tools, assembles risk assessment + recommendation | **Agent (lightweight)** | Must independently decide which tools to call for evidence gathering |

### 2. Tool Whitelist (Prompt Injection Defense)

`TOOL_REGISTRY` maps each `agent_role` to its allowed tool list. The whitelist is enforced at two layers:

1. **LLM only sees whitelisted tool definitions** (the list fed to `tools=` parameter is pre-filtered)
2. **Dispatcher double-checks**: even if LLM hallucinates a tool name outside the whitelist, dispatch is rejected

```python
TOOL_REGISTRY = {
    "employee_submit": ["extract_receipt_fields", "suggest_category",
                        "check_duplicate_invoice", "get_my_recent_submissions",
                        "update_draft_field", "check_budget_status"],   # has write access
    "employee_qa":     ["get_my_recent_submissions", "get_report_detail",
                        "get_spend_summary", "get_budget_summary",
                        "get_policy_rules"],                             # read-only
    "manager_explain": ["get_submission_for_review",
                        "get_employee_submission_history"],              # read-only
}
```

> The Agent never has `submit_expense` / `approve` tools. This is not a limitation — it's a design decision.

### 3. Phased Audit Timeline

**Problem**: The original implementation wrote all 5-skill results into `audit_report.timeline` at submission time, causing "voucher generated / payment executed" to appear in the AI explanation card before manager approval.

**Fix**:

```
After submission     timeline = [step0, step1, step2]          phase="submit"
After manager approval   timeline.append(step3: "voucher generated")   phase="manager_approved"
After finance approval   timeline.append(step4: "payment executed")    phase="finance_approved"
```

`audit_report.timeline` only ever reflects "what has already happened."

### 4. Audience Layering

The AI explanation card presents information in two layers:

| Layer | Who Sees It | Display Condition |
|-------|-------------|-------------------|
| Recommendation, flags, advisory | All users (manager/finance) | Always shown |
| Tool call details, agent_role | Developers / interviewers | `auth.isDev()` = true |

Activate dev mode: add `?dev=1` to the URL, or click the toolbar button in the navbar.

---

## Approval & Budget Workflow

### State Machine

```
processing → reviewed → manager_approved → finance_approved → exported
                              |                    |
                          rejected              rejected
```

### Budget Control

Each cost center has quarterly budgets, checked in real-time on submission:
- **info** (75%-95%): approaching budget warning
- **blocked** (>=95%): auto-blocked, requires finance unlock
- **over_budget** (>100%): over-budget alert

### Risk Tiers

| Tier | AI Recommendation | Risk Score | Meaning |
|------|-------------------|------------|---------|
| T1 | approve | <=25 | Invoice compliant, amount normal, description specific |
| T2 | approve | 25-50 | Low risk, minor attention items |
| T3 | review  | 50-75 | Needs manual check — high amount or vague description |
| T4 | reject  | >75 | High risk — abnormal amount / missing documentation |

---

## Role-Based Access Control (RBAC)

| Role | Capabilities |
|------|-------------|
| **employee** | Submit expenses, view own expenses, chat assistant |
| **manager** | Approve team expenses, view AI explanation cards |
| **finance_admin** | Finance approval, unlock budget blocks, export vouchers, bulk operations |
| **admin** | Employee management, policy configuration, audit logs, budget settings |

---

## Eval Platform

The eval system has three layers: **YAML test datasets** define what to test, the **Unified Eval Harness** runs them with code-based graders, and the **Eval Observatory** provides a web dashboard + REST API to browse results, compare runs, and manage prompts.

### Architecture

```
+-------------------+     pytest      +---------------------+     POST     +-------------------+
|  YAML Datasets    | ------------->  |  Unified Eval       | ----------> |  Observatory API  |
|                   |                 |  Harness             |             |  /api/eval/runs   |
|  fraud_llm_rules  |   per-case     |  test_eval_harness   |  run results|                   |
|  fraud_rules_det  |   graders      |  .py                 |  + metadata |  Stores to DB:    |
|  ambiguity_detect |  <-----------> |                      |             |  EvalRun, LLMTrace|
|  layer_decision   |   code_graders |  Pass rate summary   |             |                   |
|  category_classif |   .py          |  P/R/F1 per component|             |  Eval Dashboard   |
+-------------------+                +---------------------+             +-------------------+
```

### What Gets Evaluated (5 Components)

| Component | Dataset File | Cases | What It Tests |
|-----------|-------------|-------|---------------|
| **Deterministic Fraud Rules** | `fraud_rules_deterministic.yaml` | ~60 | 14 rule functions (duplicate_attendee, geo_conflict, threshold_proximity, weekend_frequency, round_amount, consecutive_invoices, merchant_category_mismatch, pre_resignation_rush, fx_arbitrage, collusion_pattern, vendor_frequency, seasonal_anomaly, ghost_employee, timestamp_conflict). Each case provides input submissions + expected signal (true/false) |
| **LLM Fraud Analyzer** | `fraud_llm_rules.yaml` | ~15 | GPT-4o-based semantic analysis (template detection, receipt contradiction, per-person amount reasonableness, vagueness scoring). Supports pass^k trials for non-deterministic outputs. Requires `OPENAI_API_KEY` |
| **Ambiguity Detector** | `ambiguity_detector.yaml` | ~12 | 5-factor scoring model: score range validation, triggered factor verification, recommendation check (auto_pass / human_review / suggest_reject) |
| **Layer Decision** | `layer_decision.yaml` | ~20 | Quick-submit layered routing: given OCR/classify/dedupe/budget signals, verify correct layer assignment (green/yellow/red) |
| **Category Classifier** | `category_classifier.yaml` | ~20 | Merchant name -> expense category mapping (meal/transport/accommodation/entertainment/other) |

### YAML Test Case Format

Every case follows the same structure, regardless of component:

```yaml
- id: rule1_positive_overlap
  component: fraud_rules_deterministic
  rule: duplicate_attendee                    # which rule function to call
  description: "A and B same day same merchant, B's attendees include A"
  input:
    submissions:
      - id: "s1"
        employee_id: "emp-A"
        amount: 200
        category: "meal"
        date: "2026-04-10"
        merchant: "Haidilao"
        attendees: ["emp-B"]
      - id: "s2"
        employee_id: "emp-B"
        amount: 180
        category: "meal"
        date: "2026-04-10"
        merchant: "Haidilao"
        attendees: ["emp-A"]
  expect:
    has_signal: true                           # should this rule fire?
    rule_name: "duplicate_attendee"            # expected signal name
```

### Code-Based Graders (No LLM in the Loop)

All grading is deterministic — `backend/tests/graders/code_graders.py`:

| Grader | What It Checks |
|--------|---------------|
| `grade_score_range` | Actual score within [lo, hi] range |
| `grade_field_match` | Exact field value match |
| `grade_enum_in` | Value in allowed set |
| `grade_list_contains` | All required items present in list |
| `grade_bool` | Boolean match (for `has_signal`) |
| `classify_detection` | TP/FP/FN/TN classification for P/R/F1 metrics |

The universal `grade_case(actual_output, expect)` function auto-dispatches: `*_range` keys use range grader, `has_signal` uses bool grader, `layer` uses field match, etc.

### 6-Factor Reproducibility Tracking

Every eval run captures 6 factors in `eval_config.json` to ensure reproducibility:

| Factor | What It Tracks | Example |
|--------|---------------|---------|
| **Prompt version** | Which prompt template is active | `v1` |
| **Model** | LLM model + snapshot | `gpt-4o-2025-03-01` |
| **Sampling params** | temperature, top_p, max_tokens | `0.0, 1.0, 1024` |
| **Config thresholds** | Rule-specific tuning knobs | `threshold_proximity_pct: 0.03` |
| **Parsing version** | How LLM output is parsed into structured data | `v1 (JSON + regex fallback)` |
| **Dataset hash** | MD5 of all YAML dataset files combined | `auto-computed` |

When pass rate drops, diff two runs via `GET /api/eval/runs/{a}/diff/{b}` to see exactly which factor changed and which cases regressed.

### Observatory Dashboard & API

**Web UI** at `/eval/dashboard.html`:
- KPI cards: total cases, pass rate, component breakdown
- Pass rate trend chart (last 10 runs)
- Per-case drill-down: input, expected, actual output, classification
- Run comparison (diff view): metadata changes + case regressions/improvements
- Prompt management: view/edit/version prompt templates, set active version
- One-click eval trigger (runs pytest in background)

**REST API** at `/api/eval/`:

| Method | Path | Function |
|--------|------|----------|
| `GET` | `/runs` | List eval runs (paginated) |
| `GET` | `/runs/{id}` | Single run detail with all case results |
| `POST` | `/runs` | Record a new eval run (called by harness) |
| `GET` | `/runs/{a}/diff/{b}` | Compare two runs: metadata diff + case regressions |
| `GET` | `/traces` | List LLM traces (filterable by component, error status) |
| `GET` | `/traces/{id}` | Single trace with full prompt + response |
| `GET` | `/stats` | Aggregate: pass rate trend + component error rates |
| `GET/PUT` | `/config` | Read/update the 6-factor eval config |
| `POST` | `/trigger` | Trigger eval run via background pytest subprocess |
| `GET` | `/trigger/status` | Check if eval is currently running |
| `GET` | `/prompts` | List all prompt templates with version counts |
| `GET` | `/prompts/{key}` | Full prompt with all versions |
| `PUT` | `/prompts/{key}/versions/{v}` | Create/update a prompt version |
| `PUT` | `/prompts/{key}/active` | Set active prompt version |

### Detection Quality Metrics

For deterministic fraud rules, the harness computes **Precision / Recall / F1** per rule:

```
  -- Detection Quality (P/R) --
  fraud_rule_duplicate_attendee:    P=100% R=100% F1=100%
  fraud_rule_threshold_proximity:   P=100% R=100% F1=100%
  fraud_rule_weekend_frequency:     P=100% R=67%  F1=80%
```

Classification matrix uses business-friendly labels:
- **TP** = correct detection (rule fires when it should)
- **FP** = false alarm (rule fires when it shouldn't)
- **FN** = missed fraud (rule doesn't fire when it should)
- **TN** = correct pass (rule correctly doesn't fire)

### Running Eval

```bash
# Run all eval cases (deterministic — no API key needed)
pytest backend/tests/test_eval_harness.py -v

# Only fraud rules
pytest backend/tests/test_eval_harness.py -v -k deterministic

# Only ambiguity detector
pytest backend/tests/test_eval_harness.py -v -k ambiguity

# With LLM fraud analysis (requires OPENAI_API_KEY)
OPENAI_API_KEY=sk-... pytest backend/tests/test_eval_harness.py -v -k llm

# Agent behavior eval (separate harness)
pytest backend/tests/test_agent_eval.py -v -s

# Via Observatory API (trigger from dashboard)
curl -X POST http://localhost:8000/api/eval/trigger -H 'Content-Type: application/json' -d '{"component": "all"}'
```

Results are automatically posted to the Observatory API if the server is running; otherwise saved to `backend/tests/eval_last_run.json` for later import.

---

## API Overview

**Reports** (expense report — employees bundle line items into a report and submit as a unit)

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `POST` | `/api/reports` | employee | Create a new report |
| `GET` | `/api/reports` | employee | List my reports |
| `GET` | `/api/reports/{id}` | all | Report detail (employees see own only) |
| `POST` | `/api/reports/{id}/submit` | employee | Submit report for approval |
| `POST` | `/api/reports/{id}/withdraw` | employee | Withdraw a submitted/approved report |
| `POST` | `/api/reports/{id}/resubmit` | employee | Resubmit a `needs_revision` report |
| `POST` | `/api/reports/{id}/approve` | manager | Manager approves the report |
| `POST` | `/api/reports/{id}/reject` | manager | Manager rejects |
| `POST` | `/api/reports/{id}/return` | manager | Return for revision (`needs_revision`) |
| `POST` | `/api/reports/{id}/finance-approve` | finance_admin | Finance approval |
| `POST` | `/api/reports/{id}/finance-reject` | finance_admin | Finance rejection |
| `PATCH` | `/api/reports/{id}/title` | employee | Rename report |
| `PATCH` | `/api/reports/{id}/lines/{sid}` | employee | Edit a line item |
| `DELETE` | `/api/reports/{id}/lines/{sid}` | employee | Delete a line item |
| `DELETE` | `/api/reports/{id}` | employee | Delete an empty open report |

**Submissions**

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `POST` | `/api/submissions` | employee | Submit expense, returns 202 + background AI review |
| `GET` | `/api/submissions/{id}` | all | View single submission (employees see own only) |
| `GET` | `/api/submissions` | all | List submissions (employees see own only) |
| `POST` | `/api/submissions/{id}/approve` | manager | Manager approval |
| `POST` | `/api/submissions/{id}/reject` | manager | Manager rejection |
| `POST` | `/api/finance/submissions/{id}/approve` | finance_admin | Finance approval + voucher number |
| `POST` | `/api/finance/submissions/{id}/reject` | finance_admin | Finance rejection |
| `GET` | `/api/finance/export/preview` | finance_admin | Export preview list |
| `POST` | `/api/finance/export` | finance_admin | Bulk export CSV |

**Agent / Chat**

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `POST` | `/api/chat/drafts` | employee | Create new draft |
| `POST` | `/api/chat/drafts/{id}/receipt` | employee | Upload receipt to draft |
| `POST` | `/api/chat/drafts/{id}/message` | employee | Agent 1: submit chat (SSE) |
| `POST` | `/api/chat/drafts/{id}/submit` | employee | Convert draft to formal submission |
| `POST` | `/api/chat/qa/message` | employee | Agent 2: read-only QA (SSE) |
| `POST` | `/api/chat/explain/{id}` | manager / finance_admin | Agent 3: AI explanation card (JSON) |

**Budget**

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `GET` | `/api/budget/status/{cost_center}` | all | Cost center budget status |
| `GET` | `/api/budget/snapshot/me` | employee | My budget overview |
| `GET/PUT` | `/api/budget/policies/{cc}` | finance_admin | Budget policy config |
| `GET/POST` | `/api/budget/amounts` | finance_admin | Budget amount management |

**Admin**

| Method | Path | Role | Description |
|--------|------|------|-------------|
| `GET/PUT` | `/api/admin/policy` | admin | Expense policy |
| `GET` | `/api/admin/audit-log` | admin | Audit log |
| `GET` | `/api/admin/stats` | admin | Summary statistics |
| `GET` | `/api/users/me` | all | Current user info |

---

## 5 Things We Deliberately Don't Do

| # | What We Don't Do | Why |
|---|-----------------|-----|
| 1 | **Turn the 5-Skill pipeline into an Agent** | Compliance requires deterministic auditability; shifting legal liability from rules to LLM is unacceptable |
| 2 | **Give the Agent submit/approve tools** | The whitelist is the last line of injection defense — breaching it dismantles the entire security model |
| 3 | **Add a chat drawer to the approval page** | Managers review ~30 items/day; +30 sec/item = +15 min/day — they'll just turn off AI |
| 4 | **Let AI auto-submit expenses** | Legal liability is on the employee; Submit must be human-confirmed (SAP Concur Joule 2026 made the same decision) |
| 5 | **Adopt an Agent SDK from day one** | MVP native tool calling is sufficient; SDK value is in subagents/memory, which this project doesn't need yet |

---

## Project Structure

```
backend/
  main.py                          # FastAPI entry point
  config.py                        # DATABASE_URL and env config
  storage.py                       # File storage abstraction
  api/
    middleware/auth.py              # RBAC auth middleware
    routes/
      submissions.py               # Expense submission + 5-Skill pipeline trigger
      chat.py                      # Chat Agent (3 roles)
      reports.py                   # Expense report management
      approvals.py                 # Manager approval
      finance.py                   # Finance approval + voucher export
      budget.py                    # Budget management
      fx.py                        # Foreign currency conversion
      admin.py                     # Admin policy configuration
      employees.py                 # Employee CRUD
      ocr.py                       # OCR recognition API
      eval.py                      # Eval dashboard API
  db/store.py                      # SQLAlchemy async ORM + CRUD
  quick/
    pipeline.py                    # Quick pipeline orchestration
    layer_decision.py              # Layered decision engine
    finalize.py                    # Draft -> formal submission conversion
  services/
    fraud_rules.py                 # Deterministic fraud detection rules
    llm_fraud_analyzer.py          # LLM fraud analysis
    fx_service.py                  # Exchange rate service
    config_loader.py               # YAML config loader
    trace.py                       # Call chain tracing
  tests/
    eval_datasets/                 # Eval YAML test datasets
    graders/                       # Custom graders
    test_*.py                      # Unit + integration tests
agent/
  controller.py                    # ExpenseController - workflow orchestration
  ambiguity_detector.py            # 5-factor scoring + Claude API deep analysis
skills/
  skill_01_receipt.py              # 4-layer receipt validation
  skill_02_approval.py             # Approval chain + timeout escalation
  skill_03_compliance.py           # A/B/C compliance judgment + Shield
  skill_04_voucher.py              # Accounting voucher + VAT splitting
  skill_05_payment.py              # 5-point pre-check + payment simulation
config/
  policy.yaml                      # Expense limits, city tiers, employee level caps
  approval_flow.yaml               # Approval matrix, timeout escalation rules
  expense_types.yaml               # Expense types, accounting codes, VAT config
  workflow.yaml                    # Pipeline orchestration (enable/disable/fail)
  city_mapping.yaml                # City name normalization mapping
  fx_rates.yaml                    # Foreign exchange rates
frontend/
  employee/                        # Employee: submit, drafts, reports, history
  manager/                         # Manager: approval queue
  finance/                         # Finance: review, export
  admin/                           # Admin: policy, employees, audit logs
  eval/                            # Eval dashboard
  shared/                          # Common JS/CSS, API wrapper, auth
models/                            # Data models (Pydantic)
rules/                             # Policy engine + city normalization
mock_data/                         # 7 test scenario factory functions
scripts/seed_demo_data.py          # Demo data seed script
Dockerfile                         # Container deployment
docker-compose.yml                 # Docker Compose orchestration
requirements.txt                   # Python dependencies
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) seed demo data
python scripts/seed_demo_data.py

# 3. Start backend in dev mode
uvicorn backend.main:app --reload --port 8000
```

**Access points** (role is switched via the navbar dropdown or `?as=<role>` URL param in mock auth mode):

| Who | URL |
|-----|-----|
| Employee | `http://localhost:8000/employee/quick.html` |
| Manager | `http://localhost:8000/manager/queue.html` |
| Finance | `http://localhost:8000/finance/review.html` |
| Admin | `http://localhost:8000/admin/dashboard.html` |
| Eval Observatory | `http://localhost:8000/eval/dashboard.html` |
| OpenAPI Docs | `http://localhost:8000/docs` |

**5-minute end-to-end demo:**

| Step | Role | Action |
|------|------|--------|
| 1 | employee | `quick.html` → upload any receipt → AI recognizes fields → confirm submit |
| 2 | — | AI review runs in background (1–3 s); `my-reports.html` polls until `status=reviewed` |
| 3 | manager | `queue.html` → open submission → view AI explanation card → approve |
| 4 | finance_admin | `review.html` → approve → voucher number generated |
| 5 | finance_admin | `export.html` → bulk export CSV |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_MODE` | `mock` | `mock` (dev) / `clerk` (production) |
| `DATABASE_URL` | SQLite local file | Production: `postgresql+asyncpg://...` |
| `EVAL_DATABASE_URL` | SQLite `concurshield_eval.db` | Physically isolated from business DB — stores LLM traces + eval runs so trace volume doesn't affect main DB performance |
| `STORAGE_BACKEND` | `local` | `local` / `r2` (Cloudflare R2) |
| `ANTHROPIC_API_KEY` | -- | Optional: AmbiguityDetector deep analysis (triggered when score >50) |
| `OPENAI_API_KEY` | -- | Optional: Chat Agent uses GPT-4o |
| `OPENAI_MODEL` | `gpt-4o` | Override the model when `OPENAI_API_KEY` is set |
| `AGENT_USE_REAL_LLM` | -- | Set to `1` + provide API Key → switch to RealLLM |

### MockLLM vs RealLLM

| Mode | Condition | Behavior |
|------|-----------|----------|
| **MockLLM** (default) | No API Key needed | Deterministic state machine: happy path runs linearly, keyword routing, deterministic eval |
| **RealLLM (GPT-4o)** | `OPENAI_API_KEY` + `AGENT_USE_REAL_LLM=1` | GPT-4o real reasoning, unlocks "user edits fields" Agent behavior |

---

## Running Tests

```bash
# Unit tests
python -m pytest backend/tests/ -v

# Eval assessment
python -m pytest backend/tests/test_agent_eval.py -v

# Full flow tests (7 scenarios)
python -m pytest tests/test_full_flow.py -v
```

---

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy (async), aiosqlite/asyncpg
- **AI**: Claude API (AmbiguityDetector), OpenAI GPT-4o (Chat Agent), MockLLM (default)
- **Frontend**: Vanilla HTML/JS (no framework dependencies)
- **Database**: SQLite (dev) / PostgreSQL (production)
- **Config**: YAML configuration-driven (policy, workflow, approval, expense types, city mapping)
