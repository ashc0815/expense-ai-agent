# Level 4: Agent Context Reasoning — Rules 15-20 Execution Spec

> **Scope:** 6 rules that require cross-employee DB queries, temporal pattern analysis, and multi-signal aggregation. No LLM call needed — these are deterministic but operate on **wider context** than single-employee data.

## Architecture (Key Difference from Level 2)

```
Level 2: LLM extracts features from TEXT → deterministic scoring
Level 4: DB queries aggregate BEHAVIORAL PATTERNS → deterministic scoring

┌──────────────────────────────────────────────────┐
│  Cross-Employee DB Queries (store.py)             │
│  - list_submissions_by_merchant(merchant, 90d)    │
│  - list_approvals_by_approver(approver_id, 90d)   │
│  - list_employee_submissions_by_quarter(emp, 8q)   │
└────────┬──────────┬──────────┬───────────────────┘
         │          │          │
    ┌────▼────┐ ┌──▼────┐ ┌──▼──────────┐
    │ R15-R17 │ │ R18   │ │ R19         │   R16, R20: no extra queries
    │collusion│ │season │ │ approver    │   (use existing submission data)
    │vendor   │ │       │ │ collusion   │
    └─────────┘ └───────┘ └─────────────┘
```

**Key distinction from Level 2:**
- Level 2 rules = "LLM reads **text**, extracts semantics" — detects lying/vagueness in descriptions
- Level 4 rules = "Agent reasons over **behavioral data** across employees/time" — detects collusion, anomalies, ghosts

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `backend/db/store.py` | MODIFY | Add 3 cross-employee query helpers |
| `backend/services/fraud_rules.py` | MODIFY | Add rules 15-20 + `ApprovalRow` dataclass + config keys |
| `skills/skill_fraud_check.py` | MODIFY | Wire Level 4 rules into `process_report_async()` |
| `backend/tests/test_fraud_rules.py` | MODIFY | Add test classes per rule |
| `backend/tests/test_fraud_integration.py` | MODIFY | Add 2 integration tests |

---

## DB Query Helpers (store.py)

### `list_submissions_by_merchant(db, merchant, days=90, limit=100) → list`
All submissions to a given merchant **across all employees**. Used by Rule 15 (collusion) and Rule 17 (vendor frequency).

### `list_approvals_by_approver(db, approver_id, days=90) → list`
All submissions approved by a given approver. Used by Rule 19 (approver collusion). Filters on `approved_at IS NOT NULL`.

### `list_employee_submissions_by_quarter(db, employee_id, quarters=8) → dict[str, float]`
Returns `{"2025-Q1": 5000.0, "2025-Q2": 6000.0, ...}`. Used by Rule 18 (seasonal anomaly). Parses date → quarter label.

---

## New Dataclass

```python
@dataclass
class ApprovalRow:
    submission_id: str
    employee_id: str
    approver_id: str
    approved: bool
    duration_seconds: float  # time from submission to approval action
```

---

## Rule Specs (Pure Functions)

### Rule 15 — Collusion Pattern / Split Billing (`rule_collusion_pattern`)

| Aspect | Value |
|--------|-------|
| **Signal** | A 和 B 轮流在同一商户报销，每次在各自限额内 |
| **Input** | `all_submissions` (cross-employee, same merchant) |
| **Detection logic** | Group by `merchant\|category` → ≥2 distinct employees → ≥N submissions → count alternations (employee switches between consecutive dated submissions) → if alternations ≥ min_count-1, flag |
| **Config key** | `collusion_min_pair_count` (default: **3**) |
| **Score** | **75** |
| **Needs** | `defaultdict` import, sort by date |

**Test cases (3):**
1. 4 submissions, A/B alternating at 海底捞 → flags (alternations=3 ≥ 2)
2. Same employee 4x → no flag (only 1 employee)
3. Only 2 submissions (below min_count=3) → no flag

**Why this is Level 4, not Level 2:** Requires cross-employee data aggregation. A single employee's data looks normal. Only the *pattern across employees* reveals collusion.

---

### Rule 16 — Rationalized Personal Spending (`rule_rationalized_personal`)

| Aspect | Value |
|--------|-------|
| **Signal** | 周末 + 度假型商户 + 多笔不同类别商务支出 → 私人消费包装 |
| **Input** | `submissions` (single employee batch) |
| **Detection logic** | Group by `employee_id\|date\|merchant` → ≥2 items with ≥2 distinct categories → date is weekend (weekday ≥ 5) → merchant matches resort keywords → flag |
| **Config key** | none (keyword list is hardcoded: `_RESORT_KEYWORDS`) |
| **Score** | **70** |
| **Resort keywords** | 度假, resort, 温泉, spa, 亚特兰蒂斯, club med, 悦榕庄, 安缦, 丽思卡尔顿, 万豪, 希尔顿 |

**Test cases (3):**
1. Saturday + 三亚亚特兰蒂斯 + accommodation + office → flags
2. Monday + same hotel → passes (weekday)
3. Single item on weekend → passes (need ≥2 categories)

**Why Level 4:** Requires reasoning about *combination* of signals (day-of-week + merchant type + category diversity). No single field is suspicious alone.

---

### Rule 17 — Vendor Frequency Anomaly (`rule_vendor_frequency`)

| Aspect | Value |
|--------|-------|
| **Signal** | 同一小商户频繁出现 → 可能存在关联交易 |
| **Input** | `submissions` (single employee history) |
| **Detection logic** | Group by merchant → if count ≥ threshold → flag |
| **Config key** | `vendor_frequency_threshold` (default: **6**) |
| **Score** | **65** |

**Test cases (3):**
1. 8 submissions to 小李便利店 → flags
2. 3 submissions to 星巴克 → passes
3. 8 submissions to 8 different restaurants → passes

**Why Level 4:** Requires temporal pattern aggregation across an employee's full history. Individual submissions are normal; only the *frequency* across time reveals the anomaly.

---

### Rule 18 — Seasonal Anomaly (`rule_seasonal_anomaly`)

| Aspect | Value |
|--------|-------|
| **Signal** | 某季度报销金额是其他季度均值的 N 倍 |
| **Input** | `quarter_totals: dict[str, float]` (from DB helper) + `current_quarter: str` |
| **Detection logic** | Current quarter amount / avg of other quarters ≥ multiplier → flag |
| **Config key** | `seasonal_spike_multiplier` (default: **2.5**) |
| **Score** | **60** |
| **Guard** | Need ≥2 other quarters with data. Insufficient history → skip. |

**Test cases (3):**
1. Q4=25000 vs others avg=5500 → 4.5x → flags
2. Uniform spending → passes
3. Only 1 quarter of data → passes (insufficient history)

**Why Level 4:** Requires historical quarterly aggregation — a cross-temporal behavioral baseline that a single submission or even single-quarter view cannot provide.

---

### Rule 19 — Approver-Submitter Collusion (`rule_approver_collusion`)

| Aspect | Value |
|--------|-------|
| **Signal** | 审批人对特定下属审批极快且从不驳回 |
| **Input** | `approvals: Sequence[ApprovalRow]` + `target_employee: str` |
| **Detection logic** | Group by approver → compare avg duration for target vs others → if `other_avg / target_avg ≥ speed_ratio` AND `all(approved)` for target → flag |
| **Config keys** | `approver_speed_ratio` (default: **3.0**), `approver_min_samples` (default: **3**) |
| **Score** | **70** |
| **Guard** | Need ≥ min_samples for target + at least 1 other employee's records |

**Test cases (2):**
1. Target: 90-120s avg, others: 900-1200s, never rejected → flags (ratio ~8x)
2. Everyone ~850-900s → passes

**Why Level 4:** Cross-employee behavioral comparison. Target employee's approval speed alone means nothing without the *relative baseline* of how the same approver treats others.

---

### Rule 20 — Ghost Employee (`rule_ghost_employee`)

| Aspect | Value |
|--------|-------|
| **Signal** | 已离职员工仍有报销提交 |
| **Input** | `submissions` + `employee: EmployeeRow` + optional `last_active_date` |
| **Detection logic** | If `employee.resignation_date` is set → check each submission date > cutoff (last_active or resignation) → flag |
| **Config key** | none |
| **Score** | **90** (highest — most clear-cut fraud) |
| **Guard** | No resignation_date → skip |

**Test cases (4):**
1. Resigned 3/15, submission 4/10 → flags (26 days after)
2. Active employee (no resignation) → passes
3. Submission before resignation → passes
4. No last_active → falls back to resignation_date check → flags

**Why Level 4:** Requires cross-referencing HR data (resignation status) with expense submission timeline. The submission itself is perfectly normal; only the *organizational context* makes it fraudulent.

---

## Config Additions to `DEFAULT_CONFIG`

```python
"collusion_min_pair_count": 3,       # Rule 15
"vendor_frequency_threshold": 6,     # Rule 17
"seasonal_spike_multiplier": 2.5,    # Rule 18
"approver_speed_ratio": 3.0,         # Rule 19
"approver_min_samples": 3,           # Rule 19
```

---

## Wiring into Pipeline

In `skills/skill_fraud_check.py` → `process_report_async()`, after Level 2 block:

```python
# ── Level 4: cross-employee + temporal rules 15-20 ──
all_signals.extend(rule_collusion_pattern(company_all, fraud_config))    # R15
all_signals.extend(rule_rationalized_personal(submissions))               # R16
all_signals.extend(rule_vendor_frequency(employee_all, fraud_config))     # R17

if db:  # R18 — needs quarterly history from DB
    quarter_totals = await list_employee_submissions_by_quarter(db, employee_id)
    current_q = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    all_signals.extend(rule_seasonal_anomaly(quarter_totals, current_q, fraud_config))

# R19 — wired when approval timing data is available (needs ApprovalRow)
# R20
all_signals.extend(rule_ghost_employee(submissions, emp_row))
```

---

## Execution Order

1. Add 3 DB query helpers to `store.py`
2. Add `ApprovalRow` dataclass to `fraud_rules.py`
3. Rules 15-20 in `fraud_rules.py` + tests (18 tests total)
4. Wire into `skill_fraud_check.py` + integration tests (2 tests)
5. Full regression suite

---

## Level 2 vs Level 4 — Decision Matrix

When adding future rules, use this to classify:

| Question | Level 2 (LLM Extraction) | Level 4 (Agent Reasoning) |
|----------|--------------------------|---------------------------|
| Does it need to understand **natural language** in descriptions/receipts? | Yes | No |
| Does it need **cross-employee** data? | No | Yes |
| Does it need **temporal baselines** (quarter-over-quarter, frequency)? | No | Yes |
| Does it need **organizational context** (HR, approvals, reporting lines)? | No | Yes |
| Can a single submission be evaluated in isolation? | Yes (with LLM help) | No (needs wider context) |
| LLM call needed? | Yes (shared single call) | No (pure DB + deterministic) |
| Failure mode when data unavailable? | Return neutral (from `_NEUTRAL`) | Skip rule (guard clauses) |
