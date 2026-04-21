# ExpenseFlow

AI-powered enterprise expense management platform — end-to-end automation from receipt submission to payment, with a built-in 5-Skill compliance pipeline, conversational Agent assistant, and Eval framework.

## What This Project Does

ExpenseFlow simulates a complete enterprise reimbursement system: employee uploads receipt → AI auto-review (OCR, rules engine, ambiguity detection) → manager approval (with AI decision explanation) → finance review → voucher generation → payment execution.

**Core Differentiators:**

1. **5-Skill Compliance Pipeline** — Receipt validation, approval chain, compliance check (with AmbiguityDetector 5-factor scoring), voucher generation, payment execution — all configuration-driven
2. **Conversational Agent** — Employees complete reimbursements via natural language (OCR → category suggestion → dedup check → budget check); managers get AI explanation cards for approval decisions
3. **Eval Framework** — YAML-defined test cases covering Agent routing, risk tiering, RBAC permissions, and tool whitelist security

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

## Conversational Agent

Three Agent roles, each with independent tool whitelists (preventing prompt injection):

| Role | Scenario | Available Tools |
|------|----------|----------------|
| **employee_submit** | Employee filling reimbursement | OCR extraction, category suggestion, invoice dedup, draft editing, budget query |
| **employee_qa** | Employee querying history | View recent expenses, expense details, spend summary, budget status (read-only) |
| **manager_explain** | Manager approval assistance | View pending expenses, employee history → output risk assessment + approval recommendation |

**LLM Abstraction:** Defaults to MockLLM (deterministic state machine, no API Key needed, ideal for demo and testing). Set `OPENAI_API_KEY` + `AGENT_USE_REAL_LLM=1` to switch to GPT-4o.

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

## Role-Based Access Control (RBAC)

| Role | Capabilities |
|------|-------------|
| **employee** | Submit expenses, view own expenses, chat assistant |
| **manager** | Approve team expenses, view AI explanation cards |
| **finance_admin** | Finance approval, unlock budget blocks, export vouchers, bulk operations |
| **admin** | Employee management, policy configuration, audit logs, budget settings |

## Eval Framework

YAML-defined test cases to verify Agent behavior correctness:

```yaml
# Example: verify QA Agent routing
- name: monthly_spend_query
  agent_role: employee_qa
  input: "How much did I spend this month?"
  expect:
    tool_calls_include: ["get_spend_summary"]
    tool_calls_exclude: ["update_draft_field"]
```

Coverage dimensions:
- **Agent Routing**: natural language → correct tool calls
- **Risk Tiering**: T1 → recommend approve, T3 → recommend review, T4 → recommend reject
- **RBAC Enforcement**: employee cannot call explain endpoint (403)
- **Whitelist Security**: QA Agent rejects write tools (even if LLM is prompt-injected)
- **Deterministic Rules**: fraud detection rule input/output validation

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

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start backend (dev mode)
uvicorn backend.main:app --reload --port 8000

# Access points
# Employee:  http://localhost:8000/employee/quick.html
# Manager:   http://localhost:8000/manager/queue.html
# Finance:   http://localhost:8000/finance/review.html
# Admin:     http://localhost:8000/admin/dashboard.html
# Eval:      http://localhost:8000/eval/dashboard.html
# API Docs:  http://localhost:8000/docs
```

### Environment Variables

```bash
# Required
DATABASE_URL=sqlite+aiosqlite:///./concurshield.db  # Default; use PostgreSQL in production

# Optional - AI features
ANTHROPIC_API_KEY=sk-...        # AmbiguityDetector deep analysis (triggered when score > 50)
OPENAI_API_KEY=sk-...           # Chat Agent uses GPT-4o (default MockLLM needs no key)
AGENT_USE_REAL_LLM=1            # Enable real LLM (default off, uses deterministic MockLLM)
```

## Running Tests

```bash
# Unit tests
python -m pytest backend/tests/ -v

# Eval assessment
python -m pytest backend/tests/test_agent_eval.py -v

# Full flow tests (7 scenarios)
python -m pytest tests/test_full_flow.py -v
```

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy (async), aiosqlite/asyncpg
- **AI**: Claude API (AmbiguityDetector), OpenAI GPT-4o (Chat Agent), MockLLM (default)
- **Frontend**: Vanilla HTML/JS (no framework dependencies)
- **Database**: SQLite (dev) / PostgreSQL (production)
- **Config**: YAML configuration-driven (policy, workflow, approval, expense types, city mapping)
