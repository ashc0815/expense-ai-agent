# Ramp MVP — Proactive Budget Intelligence Design

**Date:** 2026-04-14  
**Project:** ConcurShield expense-ai-agent  
**Status:** Approved

---

## Overview

Iterate ConcurShield's AI agent toward Ramp-style capabilities by adding proactive, budget-aware intelligence. The MVP delivers two user-facing features:

- **B — Proactive budget insight**: AI agent surfaces team budget status unprompted when the employee opens My Reports
- **C-inline — Contextual budget warning**: Submit form shows budget signal inline before the employee commits to submitting

Both features share a common budget service layer. Policy enforcement (soft block at configurable threshold) lives in the API, not the agent. The agent handles early UX only.

---

## Budget Model

Budgets are tracked at the **cost-center level** (per department pool), not per individual employee. Policy rules (e.g., meal limits, pre-approval requirements) apply at the **expense report level** and are separate from budget tracking.

Cost center is **auto-assigned** from the employee's department and is **read-only** for employees. Only finance admins can modify cost center assignments.

---

## Data Model

### New table: `cost_center_budgets`

| Column | Type | Notes |
|--------|------|-------|
| `cost_center` | TEXT | FK to cost centers |
| `period` | TEXT | e.g. `"2026-Q2"` or `"2026"` |
| `total_amount` | DECIMAL | Budget cap set by finance admin |
| `created_by` | TEXT | |
| `updated_at` | DATETIME | |

Spent amount is computed live from `submissions` (sum of approved + pending amounts for that cost center + period). No separate counter to keep in sync.

### New table: `budget_policies`

| Column | Type | Notes |
|--------|------|-------|
| `cost_center` | TEXT | Nullable — `NULL` = global default |
| `info_threshold` | FLOAT | Default `0.75` — show informational banner |
| `block_threshold` | FLOAT | Default `0.95` — soft block, requires admin override |
| `over_budget_action` | ENUM | `"warn_only"` \| `"block"`, default `"warn_only"` |
| `updated_by` | TEXT | |
| `updated_at` | DATETIME | |

Policy resolution: check for a cost-center-specific row first; fall back to the global default (`NULL` cost_center row). This allows per-client customization without touching other cost centers.

### Modified table: `submissions`

Two new columns:

| Column | Type | Notes |
|--------|------|-------|
| `budget_blocked` | BOOL | Set to `true` when API rejects at block threshold |
| `budget_unblocked_by` | TEXT | Finance admin user_id who unblocked |
| `budget_unblocked_at` | DATETIME | |

---

## API Layer

### New: `GET /budget/status/{cost_center}`

Query params: `amount` (float, optional), `period` (str, optional — defaults to current quarter).

Returns:
```json
{
  "cost_center": "ENG-TRAVEL",
  "period": "2026-Q2",
  "total_amount": 10000,
  "spent_amount": 8700,
  "usage_pct": 0.87,
  "info_threshold": 0.75,
  "block_threshold": 0.95,
  "over_budget_action": "warn_only",
  "signal": "info" | "warn" | "blocked" | "over_budget" | "ok",
  "projected_pct": 0.92
}
```

`signal` is computed server-side — neither the agent nor the frontend implements threshold logic. `projected_pct` is only present when `amount` is passed.

Signal levels:
- `ok` — below `info_threshold`
- `info` — at or above `info_threshold`, below `block_threshold`
- `blocked` — at or above `block_threshold`, at or below 100%
- `over_budget` — above 100%

### Modified: `POST /submissions`

Before creating the record, calls budget check internally:
- `signal == "blocked"` → `HTTP 402` with `{"error": "budget_blocked", "budget_status": {...}}`
- `signal == "over_budget"` + `over_budget_action == "block"` → same
- `signal == "over_budget"` + `over_budget_action == "warn_only"` → creates record normally, `budget_blocked = false`
- All other signals → creates record normally

### New: `PATCH /submissions/{id}/unblock`

Finance admin only. Sets `budget_blocked = false`, records `budget_unblocked_by` and `budget_unblocked_at`. Submission re-enters the normal review queue.

### New: `GET /budget/policies/{cost_center}` and `PUT /budget/policies/{cost_center}`

Finance admin only. Read and update threshold config for a cost center. Use `_default` as the sentinel value for the global default row.

### New: `GET /budget/amounts` and `POST /budget/amounts`

Finance admin only. Returns all `cost_center_budgets` rows. `POST` upserts by `(cost_center, period)`.

---

## Agent Changes

### Submit agent (`employee_submit`) — new tool

**Tool:** `check_budget_status(cost_center, amount)`  
Wraps `GET /budget/status/{cost_center}?amount={amount}`. Returns `signal` + human-readable budget context.

**System prompt addition:**
> After the user provides an amount, call `check_budget_status` with their cost center and that amount. If `signal` is `warn`, tell them the budget is running low and show projected usage. If `signal` is `blocked` or `over_budget` (and policy blocks), tell them clearly that submission will be held for finance admin review — don't hide it. If `signal` is `ok` or `info`, no comment needed.

The agent warns before the user hits submit. The API enforces the block as the hard gate. Agent warning ≠ enforcement.

### QA agent (`employee_qa`) — proactive trigger + new tool

**Tool:** `get_budget_summary(period)`  
Calls `GET /budget/status/{cost_center}` for the user's own cost center. Returns signal + usage %.

**Trigger:** My Reports frontend sends a silent message on page load:
```js
api.agentMessage({ trigger: "page_load", page: "my-reports" })
```

**System prompt addition:**
> When you receive a `page_load` trigger for `my-reports`, immediately call `get_budget_summary`. If `signal` is `info`, `blocked`, or `over_budget`, open with a brief proactive budget message before waiting for user input. If `signal` is `ok`, stay silent — do not message the user when everything is fine.

---

## Frontend Changes

### 1. `employee/submit.html` — inline budget signal

When the amount field loses focus, call `GET /budget/status/{cost_center}?amount={value}` (cost center auto-derived from the authenticated user's department).

Show inline card below the amount field:
- `signal: info` — light blue: "Team budget X% used — this submission will bring it to Z%"
- `signal: blocked` or `over_budget` (block) — red: "Budget threshold reached — submission will be held for finance admin review"
- `signal: over_budget` + `over_budget_action: warn_only` — yellow: "Team budget exceeded — submission will proceed but finance admin will be notified"

Cost center field is read-only in the form (displayed as a labeled value, not an input).

On HTTP 402 from `POST /submissions`, show the same red card — in case the user submitted before the inline check ran.

### 2. `employee/my-reports.html` — silent page_load trigger

On page load, fire the silent trigger to the QA agent. The agent's proactive response appears in the chat bubble. No visual change to the expense list itself.

### 3. `finance/review.html` — budget_blocked badge + unblock action

Blocked submissions display a red "⛔ Budget Held" badge alongside their existing status badge.

Finance admin sees an "Unblock" action button on the row. Clicking calls `PATCH /submissions/{id}/unblock`. On success, the badge disappears and the submission moves into the normal review flow.

### 4. New: `admin/budget-policy.html`

New page accessible to `finance_admin` role. Displays a table of all configured cost centers for the current period with:
- Cost center code
- Budget amount (editable)
- Current usage bar + percentage
- Warn threshold, block threshold, over-100% behavior (all editable)
- Global default row always shown at the bottom

Edit opens an inline modal to update `total_amount`, `info_threshold`, `block_threshold`, and `over_budget_action`. Changes call `PUT /budget/policies/{cost_center}` and `POST /budget/amounts`.

---

## Error Handling & Edge Cases

**Fail-open on budget service errors:** If `GET /budget/status` fails, the submit form shows nothing and `POST /submissions` proceeds normally. Budget enforcement never becomes a submission blocker due to infrastructure failures.

**No budget configured for a cost center:** If a cost center has no row in `cost_center_budgets`, `signal` returns `ok` and the agent stays silent. Unconfigured cost centers opt out silently.

**Period calculation:** Period is derived automatically from submission date — `YYYY-Q1/Q2/Q3/Q4`. Finance admin sets budget amounts per period key. Annual budgets can be entered as `YYYY`; the system checks both the quarterly and annual row and uses whichever is more restrictive.

**Employee with no cost center:** If the authenticated user has no department/cost center assigned, skip the budget check entirely. No warning, no block.

---

## Demo Data Seeding

Seed two cost centers for Q2 2026:

| Cost Center | Budget | Seeded Spend | Signal |
|-------------|--------|--------------|--------|
| ENG-TRAVEL | ¥10,000 | ¥8,700 (87%) | `info` |
| MKT-EVENTS | ¥25,000 | ¥9,600 (96%) | `blocked` |

This makes both the proactive banner (B) and the submit-form block (C) demonstrable immediately on first load.

---

## Out of Scope (MVP)

- Anomaly detection at submit time — deferred; needs trip context layer to avoid false positives
- Email/push notifications when budget threshold is crossed
- Budget forecasting or trend charts
- Monthly sub-caps within a quarterly budget
- LLM intent recognition upgrade for the QA agent
- Multi-currency budget tracking
