# Multi-Entity Architecture — Design Document

> **Status:** Draft v0.1 · Design only, no implementation in current codebase
> **Scope:** Architecture for serving customers with multiple legal entities under one ExpenseFlow tenant
> **Last updated:** 2026-04 (drafted alongside the agentic-compliance reasoner shipped on `main`)

---

## TL;DR

ExpenseFlow today is a **single-entity** system: one set of expense types, one GL chart, one VAT regime, one approval matrix per client. Customers like multinational groups (e.g. a parent HK holding company with operating entities in CN / SG / AU) need different GL accounts, different VAT rules, and different approval limits **per legal entity** — but their employees need to feel like they're using one product.

This doc lays out the **4-layer decoupling** (Entity / Category / Mapping / Policy) that solves it without forking the product per customer, and explains why we are deliberately **not building it yet**.

---

## 1. Problem statement

A representative customer:

```
Acme Group Holdings (HK) ── parent
├── Acme HK Ltd            VAT: none           currency: HKD   FY: Jan-Dec
├── 艾克米中国有限公司       VAT: 6% (special)   currency: CNY   FY: Jan-Dec
└── Acme SG Pte Ltd        GST: 9%             currency: SGD   FY: Apr-Mar
```

A meal expense submitted by an HK employee should book to:
- HK GL account `5210-HK-Entertainment`, no VAT
- Approved by HK manager, paid in HKD

The same employee transferring to the SG entity for a project should later book meals to:
- SG GL account `5310-SG-Meals`, GST 9% reclaimable
- Approved by SG manager, paid in SGD

**Today**, ExpenseFlow has **one global** `gl_mapping`, **one global** city/employee-tier limit table, and **one global** VAT scheme. To serve Acme we'd have to either:

1. Stand up three separate tenant deployments — three databases, three URLs, three sets of credentials, no consolidated reporting → operationally awful
2. Fork the codebase per customer → exactly what `configuration-driven` was supposed to avoid

Both options miss the point. The system needs **one tenant, multiple entities under it**.

---

## 2. Honest assessment of the current implementation

| Concern | Current state | Multi-entity gap |
|---|---|---|
| GL chart of accounts | One global mapping in `backend/api/routes/admin.py::_POLICY` (in-memory) + a different one in `config/expense_types.yaml` (per subtype `accounting_debit`). [^1] | Per-entity mapping doesn't exist. |
| VAT / tax rules | Single `vat_special_invoice` block in `expense_types.yaml`, China-shaped. | No notion of "this entity is in HK and has no VAT". |
| Approval limits | `policy.yaml::limits` is `(city × level)`-keyed only. | Different entities may want different limits even at same city/level. |
| Currency | Per-employee `home_currency` exists; FX rates exist. | No "entity functional currency" — currency is treated as employee-level. |
| Approval matrix | `approval_flow.yaml` is one global matrix. | Different entities may need different chains (e.g. SG requires CFO sign-off above SGD 10K, HK doesn't). |
| Reports / Submissions ORM | No `entity_id` column. | Nothing to scope by. |
| Employee profile | `employees.cost_center` exists (organizational scope), no `entity_id`. | Cost center is not the same as legal entity. |
| Frontend | Submit / approval / finance pages have no entity selector. | Employees + finance need to see / set entity. |

[^1]: This is **conflict #1** documented in the architecture audit — two parallel policy truth sources (the YAML config layer used by skills/rules, and the in-memory `_POLICY` dict used by FastAPI routes). Multi-entity work cannot start until those are merged. See `docs/spec-conflicts.md` (planned).

So the code is **honest single-entity**: not "single-entity by accident with extension points", but "single-entity by design with assumptions baked in".

---

## 3. Target architecture: 4-layer decoupling

```
                     ┌─────────────────────────────────────┐
   Layer 1   Entity  │  entities.yaml                       │  legal entity, country,
                     │    - id, country, currency, tax_id   │  fiscal year, VAT scheme
                     │    - parent_entity, fiscal_year      │
                     └────────────────┬────────────────────┘
                                      │
                                      ↓
                     ┌─────────────────────────────────────┐
   Layer 2  Category │  expense_types.yaml (existing)       │  what KIND of expense
                     │    - meal / transport / accom / ...  │  this is, conceptually
                     │    - subtypes (transport_local etc.) │
                     └────────────────┬────────────────────┘
                                      │
                                      ↓
                     ┌─────────────────────────────────────┐
   Layer 3   Mapping │  gl_mapping_by_entity.yaml (NEW)     │  per-entity per-category
                     │    (entity × category) →             │  ledger + tax treatment
                     │      { gl_account, vat_rate,         │
                     │        vat_deductible,               │
                     │        vat_invoice_required }        │
                     └────────────────┬────────────────────┘
                                      │
                                      ↓
                     ┌─────────────────────────────────────┐
   Layer 4    Policy │  policy.yaml (extended)              │  per-entity OR
                     │    - per-entity limits override      │  cross-entity rules
                     │    - per-entity approval chains      │
                     │    - cross-entity rules (e.g.        │
                     │      "no inter-entity meal hosting") │
                     └─────────────────────────────────────┘
```

The discipline: **each layer answers exactly one question**, and changes to one layer should not require changes in the layers above or below.

| Layer | Answers | Owned by |
|---|---|---|
| Entity | "Who is paying?" | CFO + group finance |
| Category | "What was bought?" | Product / accounting |
| Mapping | "Where does it post in our books?" | Per-entity controllers |
| Policy | "Is this allowed?" | Per-entity controllers + group finance |

---

## 4. Schema design

### 4.1 `entities.yaml`

```yaml
entities:
  - id: ENT_HK
    name: "Acme HK Ltd"
    country: HK
    currency: HKD
    tax_id: "1234567-X"
    fiscal_year_start: "01-01"
    parent_entity: null
    vat_scheme: none           # HK has no VAT

  - id: ENT_CN
    name: "艾克米中国有限公司"
    country: CN
    currency: CNY
    tax_id: "91310000XXXXXXXX"
    fiscal_year_start: "01-01"
    parent_entity: ENT_HK
    vat_scheme: china_vat       # 增值税专用发票流程

  - id: ENT_SG
    name: "Acme SG Pte Ltd"
    country: SG
    currency: SGD
    tax_id: "201912345A"
    fiscal_year_start: "04-01"  # offset fiscal year
    parent_entity: ENT_HK
    vat_scheme: gst
```

### 4.2 `gl_mapping_by_entity.yaml`

Same `(category)` resolves to **different** GL accounts and VAT rules per entity:

```yaml
mappings:
  - entity: ENT_HK
    category: meal
    gl_account: "5210-HK-Entertainment"
    vat_rate: 0.0
    vat_deductible: false
    vat_invoice_required: false

  - entity: ENT_CN
    category: meal
    gl_account: "660103-餐饮费"
    vat_rate: 0.06
    vat_deductible: true
    vat_invoice_required: true       # 必须有专票才能抵扣

  - entity: ENT_SG
    category: meal
    gl_account: "5310-SG-Meals"
    vat_rate: 0.09                   # GST
    vat_deductible: true
    vat_invoice_required: false
```

### 4.3 `policy.yaml` extension

```yaml
limits:
  meals_per_person:
    # Default: applies to all entities unless overridden
    tier_1: { L1: 100, L2: 150, L3: 200, L4: 不限 }
    tier_2: { L1: 60,  L2: 100, L3: 150, L4: 不限 }

  meals_per_person_overrides_by_entity:
    ENT_SG:        # SG entities have higher limits in absolute SGD
      tier_1: { L1: 50, L2: 80, L3: 120, L4: 不限 }   # in SGD

approval_chains:
  default:
    - { trigger: amount_above, value: 5000, role: manager }
    - { trigger: amount_above, value: 50000, role: cfo }
  by_entity:
    ENT_SG:        # SG requires CFO sign-off at lower threshold
      - { trigger: amount_above, value: 5000, role: manager }
      - { trigger: amount_above, value: 10000, role: cfo }

# Cross-entity rules — a new class enabled by entity awareness
cross_entity_rules:
  - rule: no_meal_hosting_across_entities
    description: "An ENT_HK employee cannot expense meals for ENT_CN attendees
      to ENT_HK's books — must be charged to the entity benefiting."
```

### 4.4 ORM schema

```python
class Submission(Base):
    # ... existing columns ...
    entity_id = Column(String(32), nullable=False,
                       default="DEFAULT_ENTITY", index=True)
    # GL/VAT fields keep existing names but the DERIVATION changes:
    #   old: from policy.gl_mapping[category]
    #   new: from gl_mapping_by_entity[entity_id, category]

class Report(Base):
    # ... existing columns ...
    entity_id = Column(String(32), nullable=False,
                       default="DEFAULT_ENTITY", index=True)
    # Note: All submissions in one report MUST share the same entity_id.
    # Inter-entity expenses go on separate reports (different functional
    # currency, different VAT treatment, different approver chain).

class Employee(Base):
    # ... existing columns ...
    primary_entity_id = Column(String(32), nullable=True, index=True)
    # NULL for now, becomes required after Phase 2 backfill. Allows
    # employees to "transfer" between entities by editing this one field.
```

### 4.5 Backward compatibility: `DEFAULT_ENTITY`

All existing rows backfill to `entity_id = "DEFAULT_ENTITY"`. Single-entity customers stay on `DEFAULT_ENTITY` indefinitely — the field is filled in but `gl_mapping_by_entity` for that entity is just the existing flat mapping promoted to one row. **Zero behavioral change** for existing deployments.

---

## 5. What changes per user role

| Role | Page | New behavior |
|---|---|---|
| **Employee** | `/employee/quick.html`, `/employee/submit.html` | **No visible change.** `entity_id` is auto-derived from `employees.primary_entity_id` and stamped onto the submission. Cross-entity transfers (rare) require an admin-issued override. |
| **Employee** | `/employee/my-reports.html` | Each report card shows a small entity badge (e.g. `HK · HKD`). |
| **Manager** | `/manager/queue.html` | Queue is filtered to the manager's entity by default; toggle `[ ] Include cross-entity` to see hosted expenses they're approving on behalf of another entity. |
| **Finance** | `/finance/review.html` | Finance can see all entities they're authorized for. The AI explanation card surfaces `entity: ENT_HK` and `gl_account: 5210-HK-Entertainment` so the auditor can trace the booking decision. |
| **Finance** | `/finance/export.html` | **Export is grouped by entity** — one CSV / voucher batch per entity, never mixed. ERP push uses `entities.yaml::tax_id` as the legal-entity identifier. |
| **Admin** | `/admin/entities.html` (NEW) | CRUD for legal entities. Initially a small table view: id, name, country, currency, tax_id, parent_entity. |
| **Admin** | `/admin/employees.html` | New column `primary_entity_id` with dropdown sourced from `entities.yaml`. |
| **Admin** | `/admin/policy.html` | Per-entity overrides shown as nested sections; defaults at the top, overrides below. |

---

## 6. Migration plan (3 sub-PRs)

### PR-2A · Schema + read-side (1 week)

- Add `entities.yaml` and `gl_mapping_by_entity.yaml` to `config/`
- Schema: `entities` table, `entity_id` columns on `Submission`, `Report`, `Employee.primary_entity_id`
- Migration script: backfill all existing rows → `entity_id = "DEFAULT_ENTITY"`
- `config_validator.py` rules:
  - V_ENT_1: every entity referenced exists in `entities.yaml`
  - V_ENT_2: every (entity, category) pair has a mapping (or fallback to default)
  - V_ENT_3: a report's submissions all share entity_id (DB-level constraint)
- **No UI changes yet.** Backfill leaves system behaviorally identical.

### PR-2B · Write-side + per-role surfacing (1 week)

- `submit_expense` / quick flow derives `entity_id` from employee profile
- AI explanation card displays entity badge
- `/admin/entities.html` minimal CRUD
- `/admin/employees.html` adds `primary_entity_id` editor
- Finance export groups by entity (one file per entity)

### PR-2C · Cross-entity rules + demo data (3-5 days)

- Extend reasoner with a `_check_cross_entity_meal()` finding (e.g. ENT_HK employee hosting ENT_CN attendee — flag for accounting clarification)
- `seed_compliance_demo` adds 3 entities + 3 employees split across them + a cross-entity meal scenario
- README screenshot + Eval dashboard run with multi-entity profile

**Total: ~2-3 weeks** (matches the original spec estimate, after pre-work in conflict #1 is done).

---

## 7. Why we're not building this yet

This is the deliberate part. We have **no real customer** asking for multi-entity support today. Building it now means:

1. **Speculative complexity.** Every decision is a guess at what a customer might want. We'd ship 2 weeks of code to defend against a customer who may never appear, while shipping zero value to current users.
2. **Two-stage migration coupling.** Multi-entity must follow conflict #1's resolution (unify `_POLICY` ↔ YAML) — otherwise we'd build entity-aware code on top of a foundation that's about to change. This makes the order **cleanup → unification → multi-entity**, total ~6 weeks of foundation work for a feature with no customer.
3. **Abstraction debt.** A 4-layer decoupling that's never exercised by real diversity (e.g. only ever 1 entity in production) accumulates abstraction without purpose. The schema rusts.

The right move is to **ship the design doc as a portfolio artifact and a forward-compatibility contract**, defer implementation until a real customer hits the gap. When that customer appears, the design is on the shelf and execution is straightforward.

This isn't a hedge — it's the cheapest way to demonstrate that the team **knows what's missing and why it's missing**, which is more valuable than half-built abstractions.

---

## 8. Forward compatibility — what we already do right

The agentic compliance reasoner shipped in PR `agent-compliance-tools-EyRKj` (`agent/compliance_reasoner.py` + `backend/services/compliance_lookups.py`) is **already entity-ready in shape**:

```python
# Existing pattern in compliance_lookups.py
async def get_employee_allowances(
    db, *, employee_id: str, on_date: str,
) -> dict[str, Any]:
    ...

# When entities arrive, the same module gets:
async def get_employee_allowances(
    db, *, employee_id: str, on_date: str,
    entity_id: str | None = None,            # ← additive
) -> dict[str, Any]:
    ...

# And reasoner gains a check:
async def _check_cross_entity_meal_hosting(
    db, *, submission_id, employee_id, attendees, entity_id,
) -> Optional[dict]:
    """If meal attendees include employees of a different entity than
    the submitter, the booking entity is ambiguous — flag for
    accounting decision."""
```

The `evidence_chain` schema on agent.* violations already supports arbitrary `kind` values, so a new `kind: "different_entity_attendee"` is a non-breaking addition.

This is the test of whether the architecture is genuinely extensible: **adding a new compliance dimension shouldn't require changing any existing data shape**. So far, it doesn't.

---

## 9. Risks & open questions

| Risk | Severity | Mitigation |
|---|---|---|
| `EmployeeLevel` enum (L1-L4) is hardcoded; entities may want different level structures | medium | Phase 2C extends the enum to a YAML-driven list; default keeps L1-L4 |
| `models/expense.py` (concurshield-agent layer) and `backend/db/store.py::Employee` are two separate models — `entity_id` must be added in both and kept in sync | medium | Single `_employee_to_dataclass` adapter, tested in PR-2A |
| Cross-entity reports — should they be allowed at all? | high | Default no (DB constraint at the report level). Cross-entity hosting becomes a separate flow with explicit accounting transfer |
| FX between functional currencies of different entities | medium | Existing FX service handles per-amount conversion; needs additional metadata for entity-of-record |

### Open questions

1. **Entity hierarchy in reports** — when a parent entity (`ENT_HK`) consolidates monthly, do we surface entity-level subtotals on the existing finance/export page or build a new consolidation page?
2. **Per-entity custom fields** — does `forms.yaml` (Phase 3 in the architecture spec) live at entity level or at tenant level? Probably tenant, with per-entity overrides.
3. **Approval chain scoping** — when a CN employee submits a meal, who's the manager? The CN entity manager always, or the employee's `manager_id` regardless of entity? Likely the latter for usability, but accounting may want the former.
4. **Audit log scoping** — should `audit_logs.entity_id` be required? Probably yes for entities with separate auditors.

---

## 10. References

- **Architecture spec (full)**: `docs/architecture-spec.md` (planned, Phase 0/1/2/3 roadmap)
- **Conflict #1**: Two parallel policy systems (`_POLICY` in admin.py vs. `config/*.yaml`) — must be unified before this design is implementable
- **Agentic compliance reasoner**: `agent/compliance_reasoner.py` (shipped) — pattern that this design extends to entity-awareness
- **PR history**:
  - `feat(compliance): cross-record lookup tools + demo data` (PR-A)
  - `feat(compliance): agent reasoner emits agent.* violations` (PR-B)
  - `feat(compliance): explanation card renders agent evidence chain` (PR-C)

---

*This design doc is intentionally written before any code change. It's the contract: when a real customer hits this gap, the team executes against this doc, not against assumptions improvised at the time.*
