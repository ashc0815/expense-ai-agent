# Integration Design — NetSuite + Stripe Issuing + Excel-as-Bridge

> **Status:** Forward-compat design. No integration code in current codebase.
> **Companion to:** [`industrial-readiness-roadmap.md`](industrial-readiness-roadmap.md) (Gap 4 ERP, Gap 5 payment), [`customer-segmentation.md`](customer-segmentation.md).

---

## TL;DR

Three integration paths designed in this doc:

1. **NetSuite — Excel-as-bridge (recommended Path A)** — generate NetSuite-format CSV; finance imports manually. Works day 1 with every customer; no API integration code; zero authentication complexity. **Targets 80% of mid-market.**
2. **NetSuite — SuiteTalk REST API (Path B, enterprise only)** — direct two-way integration; auth via OAuth 2.0; Vendor Bill creation, error handling, reconciliation. **3-month build per ERP.**
3. **Stripe Issuing — for card-issued spend (US-first segment)** — issue corporate cards to employees; webhook on every transaction; map to ExpenseFlow Submission with `funding_source=stripe_card`. **3-month build, payment regulation included.**

For ExpenseFlow's recommended target (Segment C cross-border e-commerce per [`customer-segmentation.md`](customer-segmentation.md)), **Path A (Excel-as-bridge) for NetSuite + 用友 + Xero** is the right v1. Direct API integration only when a specific enterprise customer demands it AND can pay enterprise pricing.

---

## Why integration is the bar

The AI core works. The eval framework works. The UI works. None of this matters if a CFO can't get the data into their accounting system.

Concur's actual moat isn't AI quality — it's the **6,500 supplier connections** they've built over 25 years. Every business school says this; nobody at a startup actually believes it until they try to build their first ERP integration.

Realistic strategy: **don't try to out-Concur Concur on connection breadth**. Pick the 2-3 ERPs your target segment actually uses, do those well, and offer Excel-as-bridge for everything else.

---

## Path A · Excel-as-Bridge (recommended for v1)

### The insight

Most expense systems sold to mid-market end up with this workflow anyway:

```
ExpenseFlow generates a CSV
  → Finance opens it in Excel
  → Adjusts a few cells (override GL, fix a typo)
  → Imports to ERP via the ERP's standard import wizard
```

Concur sells the dream of "no human in the loop"; the reality is that finance always wants to QA before posting. So we just embrace it as the design.

### Schema design

For each ERP we support, we ship a CSV exporter that produces output matching the ERP's standard import format. The export endpoint is `/api/finance/export?erp=netsuite|sap|yongyou|kingdee|xero`.

#### NetSuite "Vendor Bill Import" CSV format

```
external_id,vendor,date,memo,line_account,line_amount,line_class,line_department,line_location,line_taxcode
EFXP-2026-001,Acme Catering,2026-04-15,Client dinner Q2,5210-Entertainment,950.00,CC-MKT-EVENTS,Marketing,Shanghai HQ,VAT-CN-6
EFXP-2026-002,Marriott Shanghai,2026-04-16,Conference accommodation,5310-Travel,1800.00,CC-ENG-TRAVEL,Engineering,Shanghai HQ,VAT-CN-6
```

Mapping from ExpenseFlow's `Submission` row:

| ExpenseFlow field | NetSuite column | Transformation |
|---|---|---|
| `id` | `external_id` | Prefix with `EFXP-` for traceability |
| `merchant` | `vendor` | Direct |
| `date` | `date` | ISO 8601 |
| `description` | `memo` | Truncate to 999 chars (NetSuite limit) |
| `gl_account` | `line_account` | Lookup via `gl_mapping_by_entity` (Multi-entity Gap 3) |
| `amount` | `line_amount` | Direct (in `currency`) |
| `cost_center` | `line_class` | Direct |
| `department` | `line_department` | Direct |
| `entity.name` | `line_location` | From entity record |
| `vat_rate + jurisdiction` | `line_taxcode` | Lookup via tax-code map |

#### 用友 NC "凭证导入" format

```
日期,凭证类型,摘要,科目编码,借方金额,贷方金额,辅助核算-部门,辅助核算-成本中心,附单据数
2026-04-15,记账,客户接待 - 财务报销,6602.02,950.00,,营销部,CC-MKT-EVENTS,1
```

Different shape, different fields, but conceptually the same mapping logic.

#### SAP standard CSV (BAPI-style, simplified)

```
DocDate,PostingDate,DocType,GLAccount,Debit,Credit,CostCenter,Profit Center,Reference,Text
20260415,20260415,KZ,5210000,950.00,0.00,CC-MKT-EVENTS,PC-Marketing,EFXP-2026-001,Client dinner Q2
```

### Per-customer template selection

Admin picks "our ERP" once during onboarding. Stored in `tenants.erp_format`:

```python
class Tenant(Base):
    # ...
    erp_format = Column(String(32), nullable=False, default="generic_csv")
    # netsuite | sap | yongyou | kingdee | xero | quickbooks | generic_csv
    erp_field_overrides = Column(JSON, nullable=True)
    # Per-tenant tweaks: e.g. their NetSuite uses 'CC-' prefix on cost centers
```

### Field mapping UI

Before downloading, finance sees a preview:

```
┌─ Export Preview (NetSuite format · 12 rows) ─────────────────────┐
│ external_id │ vendor    │ date       │ amount │ line_account     │
│ EFXP-...001 │ Acme      │ 2026-04-15 │ 950.00 │ 5210-Entertainment│
│ EFXP-...002 │ Marriott  │ 2026-04-16 │ 1800.0 │ 5310-Travel       │
│ ...                                                                │
│                                                                    │
│ ⚠ 1 warning: row 7 has gl_account "TBD" — please assign            │
│                                                                    │
│ [Edit field mapping] [Download CSV] [Cancel]                       │
└────────────────────────────────────────────────────────────────────┘
```

The "Edit field mapping" button opens a per-column override:

```
gl_account → NetSuite "line_account":
  Default mapping:
    meal              →  5210-Entertainment
    transport         →  6601.03-Travel
    accommodation     →  5310-Travel-Hotels
  
  [Add override]   [Reset to defaults]
```

### Effort

**2-3 weeks** for the first 3 ERP formats (NetSuite + SAP + 用友), plus 1 week for the field-mapping UI.

Each subsequent ERP format: ~3 days (it's just another CSV column mapping, same logic).

### Failure modes

This is the elegant part: there are basically none.

- ERP rejects the import? Finance sees the error in the ERP, opens the CSV in Excel, fixes the row, re-imports.
- Wrong GL code? Finance edits in Excel before import.
- Fiscal period closed? Finance handles per their normal workflow.

ExpenseFlow's responsibility ends at "produce a valid CSV". The ERP's import wizard handles the rest.

### Why this is enough for 80% of customers

Real talk: **most mid-market finance teams want to QA the GL postings before they hit the books anyway**. Even with deep API integration, they'd find a way to insert a manual review step. So Excel-as-bridge isn't a downgrade — it's matching how they actually want to work.

The remaining 20% (large enterprise, high volume, want zero manual touches) → upsell to Path B.

---

## Path B · NetSuite SuiteTalk REST (enterprise only)

### When to build

Only when:
1. A specific enterprise customer demands it AND
2. Pays for it (ARR uplift covers the build cost) AND
3. Their volume justifies (>1000 transactions/month makes manual import painful)

For Segment C cross-border e-commerce target ([`customer-segmentation.md`](customer-segmentation.md)), realistically not in year 1.

### Auth: NetSuite OAuth 2.0 (TBA is being deprecated)

```python
# Per-customer credentials stored in tenant config (encrypted)
class TenantNetsuiteCredentials(Base):
    tenant_id          = Column(String(36), primary_key=True)
    account_id         = Column(String(64), nullable=False)  # NS account
    consumer_key       = Column(String(255), nullable=False)
    consumer_secret    = Column(String(255), nullable=False)  # encrypted
    access_token       = Column(String(255), nullable=False)
    access_token_secret = Column(String(255), nullable=False)  # encrypted
    api_base_url       = Column(String(255), nullable=False)
    # rest endpoint: https://{account_id}.suitetalk.api.netsuite.com/rest/...
```

OAuth 2.0 setup is a documented but tedious workflow (NetSuite admin generates app credentials → ExpenseFlow stores them → exchange for access token).

### Vendor Bill creation flow

```
ExpenseFlow finance approves submission
  ↓
POST {api_base_url}/services/rest/record/v1/vendorBill
  Authorization: OAuth 1.0 (TBA) or 2.0 Bearer
  Body: VendorBill JSON object (entity, items, subsidiary, currency, ...)
  ↓
NetSuite responds:
  201 Created  + Location: /record/v1/vendorBill/{internalId}
  OR
  400 Bad Request + error details (bad GL, period closed, FX rate stale, ...)
```

### Error handling — the real complexity

NetSuite errors are diverse. Rough taxonomy:

| Error class | Example | Our response |
|---|---|---|
| Transient (network, throttling) | 503, 429 | Retry with exponential backoff (max 5 attempts) |
| Auth | 401 expired token | Refresh OAuth token, retry once |
| Configuration | "Account 5210 inactive in this period" | Move to manual review queue; notify finance |
| Data | "Vendor 'Acme' not found" | Move to manual review queue; offer "create vendor" UI |
| Business rule | "Posting period closed" | Move to next period queue; notify finance |
| Bug | 500 internal | Log + alert + manual review |

Each error class needs:
- Logged in `audit_log`
- Surfaced in admin UI ("3 vendor bills pending NetSuite confirmation")
- Eventually retried OR surfaced to a human

### Reconciliation

The hard part. After ExpenseFlow says "we created vendor bill X", we need to confirm NetSuite actually posted it AND a payment cleared.

```python
# Background worker, every 15 min
async def reconcile_pending_bills():
    pending = list_submissions_with_status("posted_to_netsuite")
    for sub in pending:
        ns_bill = await fetch_netsuite_bill(sub.netsuite_internal_id)
        if ns_bill.status == "Paid":
            sub.status = "paid"
            sub.netsuite_payment_id = ns_bill.payment_id
        elif ns_bill.status == "Voided":
            # Someone manually voided in NetSuite — alert
            create_alert("voided_in_netsuite", sub)
        # ... etc
        await db.commit()
```

This is where most "deep ERP integration" projects die — the reconciliation logic is a forever-job. Every edge case gets discovered in production.

### Webhook listener (NetSuite SuiteScript pushes events)

For real-time updates, NetSuite admin can install a SuiteScript that POSTs to ExpenseFlow when bills change. Avoids polling.

```python
@router.post("/webhooks/netsuite/{tenant_id}")
async def netsuite_webhook(
    tenant_id: str,
    event: NetSuiteEvent,
    db: AsyncSession = Depends(get_db),
):
    # Verify HMAC signature (shared secret per tenant)
    # Update local state based on event
    # Kick off downstream notifications
```

### Effort

- **Auth + create + read** (one customer's read-only flow): **3-4 weeks**
- **Production-ready with retry + reconciliation + webhook**: **2-3 months**
- **Per additional NetSuite tenant onboarding**: **1-2 weeks** (their unique vendor list, GL chart, period setup, custom fields)

### Decision points

- **OAuth 1.0 TBA vs 2.0?** OAuth 2.0; TBA is being deprecated.
- **Use NetSuite's "REST web services" or "SuiteTalk SOAP"?** REST. SOAP works but is from another era.
- **Sync write vs async queue?** Async queue (Celery/RQ). Sync writes block the user; failed writes need retry.

### Who to talk to

- A NetSuite implementation consultant — $200-400/hr but 2 hours of their time saves 2 weeks of false starts
- Anyone who's done a Concur → NetSuite integration debug
- NetSuite developer community on Stack Overflow (large + helpful)

---

## Stripe Issuing — for card-issued spend (Segment A only)

### When to build

For the US-SaaS segment (Segment A from [`customer-segmentation.md`](customer-segmentation.md)). Skip for Segment B (China — irrelevant) and Segment C (cross-border — they use Airwallex; we'd integrate read-only with Airwallex's API, similar pattern).

### Architecture

ExpenseFlow becomes a Stripe Connect platform. Each customer is a connected account; their employees get cards issued under their account.

```
Customer signs up
  ↓
Onboard as Stripe Connect Express account (KYC handled by Stripe)
  ↓
Customer admin issues cards to employees via ExpenseFlow UI
  → POST /v1/issuing/cards (Stripe API)
  → Stripe issues virtual card; physical card delivered if requested
  ↓
Employee makes purchase
  → Stripe authorizes / declines based on rules
  → Webhook to ExpenseFlow: issuing_authorization.created
  → ExpenseFlow records as Submission with funding_source=stripe_card
  ↓
Receipt upload (optional) → OCR → match to authorization
  ↓
Reconcile: Stripe sends transactions.balance_transaction nightly
  → ExpenseFlow updates payment_state
```

### Auth flow (Stripe Connect Express)

```python
# Customer onboarding
async def onboard_customer_to_stripe():
    account = stripe.Account.create(
        type="express",
        country="US",
        email=customer.email,
        capabilities={"card_issuing": {"requested": True}},
    )
    # Get account-link for the customer to complete KYC
    link = stripe.AccountLink.create(
        account=account.id,
        return_url="https://expenseflow.app/onboarding/stripe-complete",
        refresh_url="https://expenseflow.app/onboarding/stripe-refresh",
        type="account_onboarding",
    )
    return link.url
```

Stripe handles all KYC. We just need to store the connected account ID.

### Card issuance

```python
async def issue_card_to_employee(employee, customer):
    cardholder = stripe.issuing.Cardholder.create(
        type="individual",
        name=employee.name,
        email=employee.email,
        billing={"address": {...}},
        stripe_account=customer.stripe_account_id,  # Important: per-customer
    )
    card = stripe.issuing.Card.create(
        cardholder=cardholder.id,
        currency="usd",
        type="virtual",  # or "physical"
        spending_controls={
            "spending_limits": [{"amount": 500000, "interval": "monthly"}],  # $5K/mo
            "blocked_categories": ["gambling", "adult_entertainment"],
        },
        stripe_account=customer.stripe_account_id,
    )
    return card.id
```

### Webhook handler (the loop)

```python
@router.post("/webhooks/stripe/{customer_id}")
async def stripe_webhook(customer_id: str, event: StripeEvent, ...):
    # Verify Stripe signature
    if event.type == "issuing_authorization.created":
        # Real-time auth — must respond within 2 seconds
        # Apply ExpenseFlow rules:
        #   - over per-cost-center budget? decline
        #   - merchant blocked by policy? decline
        #   - else: approve
        decision = await apply_realtime_rules(event.data)
        return {"approved": decision.approve, "metadata": {...}}
    
    if event.type == "issuing_transaction.created":
        # Settled transaction — record as Submission
        await create_submission_from_stripe_txn(event.data)
    
    if event.type == "issuing_dispute.created":
        # Fraud / chargeback — flag for finance review
        await escalate_to_finance(event.data)
```

### The 2-second window

Stripe's `issuing_authorization.created` webhook gives you **2 seconds** to approve or decline. If you don't respond, Stripe declines by default (or approves if configured to).

This means:
- Decision logic must be **synchronous** in the webhook
- Pre-compute everything possible (employee budget cache, MCC blocklist, etc.)
- If decision logic takes >1.5s, refactor

This is genuine engineering work — most ExpenseFlow business logic today doesn't have this latency constraint.

### Effort

- **Connect onboarding + card issuance UI**: **3-4 weeks**
- **Real-time webhook decision logic**: **3-4 weeks** (the budget cache, MCC mapping, decline-explanation UI)
- **Reconciliation + dispute handling**: **2-3 weeks**
- **Compliance review** (we're now in payment flow): **2-3 weeks of legal review**

**Total ~3 months** for production-ready.

### Decision points

- **Stripe Connect Express vs Custom?** Express. Custom requires us to handle KYC; Express delegates to Stripe. We do not want to be a money mover.
- **Issue virtual cards by default, physical by request?** Yes; virtual is instant, physical is 3-7 days.
- **Per-employee or per-team cards?** Per-employee for accountability.
- **Card decline with explanation?** Yes — show employee "declined: over your monthly budget" in real-time.

### Who to talk to

- Stripe Issuing PM / sales engineer — eager to help platforms onboard
- Brex / Ramp engineer — won't talk to a competitor; instead, watch their public engineering blogs
- Anyone who's done a "we became a card issuer" project — rare and valuable

### Compliance footnote

Even with Stripe handling KYC, ExpenseFlow takes on:
- **Money Transmitter Act considerations** (state-by-state in US)
- **PCI-DSS scope** if we touch card data (we shouldn't; Stripe wraps it)
- **Customer due diligence** if we're intermediating money flow

This is real legal review territory. Budget for an attorney specializing in fintech (~$500/hr, ~$10-30K for initial review).

---

## Recommended sequencing

```
Phase 1 (~3 weeks) — first customer, any segment
  ✅ Excel-as-bridge for NetSuite + 1 other ERP
  ✅ Field-mapping UI
  ✅ Per-tenant template selection
  ❌ No API integration yet
  ❌ No Stripe Issuing yet

Phase 2 (~3 months) — second/third customer in Segment C (cross-border)
  ✅ Excel-as-bridge for 用友 + Xero (3rd and 4th formats)
  ✅ Read-only Airwallex integration (their cards → our submissions)
  ❌ Still no deep API
  ❌ Still no Stripe Issuing

Phase 3 (~3 months) — first enterprise customer in Segment B
  ✅ NetSuite SuiteTalk REST (Path B) — full two-way
  ✅ Reconciliation worker
  ✅ Webhook listener
  ❌ Still no Stripe Issuing (irrelevant for Segment B)

Phase 4 (~3 months) — IF pivoting to Segment A (US SaaS)
  ✅ Stripe Connect + Issuing
  ✅ Real-time decline logic
  ✅ Dispute handling
  ✅ Legal compliance review
```

For ExpenseFlow's recommended target (Segment C per [`customer-segmentation.md`](customer-segmentation.md)), **Phase 1 + Phase 2** = 4-5 months total to first credible enterprise customer in cross-border e-commerce.

---

## What this doc deliberately doesn't say

- It doesn't cover every ERP. SAP S/4HANA cloud is different from SAP ECC. Workday Financials is its own thing. Each is a project. Approach per-customer demand.
- It doesn't go deep on China bank API integration (网联 / 银联 / 第三方支付 like 合合 / 汇付). Out of scope unless Segment B is a real focus.
- It doesn't cover OCR vendor integration. Currently we use OpenAI Vision; production might want Veryfi / Klippa / Rossum. Different decision.
- It doesn't cover SSO (SAML / OIDC). That's a vendor problem (Auth0 / Clerk / WorkOS) more than an integration problem.

---

## References

- [`industrial-readiness-roadmap.md`](industrial-readiness-roadmap.md) Gap 4 (ERP) and Gap 5 (Payment)
- [`customer-segmentation.md`](customer-segmentation.md) — which segment determines which integrations matter
- [`multi-entity-design.md`](multi-entity-design.md) — `gl_mapping_by_entity` is the source for per-entity GL codes in CSV exports
- [NetSuite SuiteTalk REST docs](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_158799981.html)
- [Stripe Issuing docs](https://stripe.com/docs/issuing)
- [Stripe Connect Express docs](https://stripe.com/docs/connect/express-accounts)

---

*This doc is the contract for what real ERP/payment integration looks like in this product. When the team chooses to ship integration, it executes against this map. Until then, it's the answer to "have you thought about how this would integrate with their accounting system?" — yes, in detail.*
