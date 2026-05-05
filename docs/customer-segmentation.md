# Customer Segmentation — Who Would Actually Buy This?

> **Status:** Strategic design doc. No customers acquired; this is the "if we were going to sell, who to" analysis.
> **Companion to:** [`industrial-readiness-roadmap.md`](industrial-readiness-roadmap.md), [`integration-design.md`](integration-design.md).

---

## TL;DR

Three segments worth analyzing seriously. Each has fundamentally different product needs:

| Segment | Sweet spot | Key needs | What they DON'T need |
|---|---|---|---|
| **A · 50-person SaaS startup** (US/EU) | $0-5M ARR, all-remote / hybrid | Fast onboarding, modern UX, Stripe Issuing for cards | Multi-entity, complex approval chains, jurisdictional tax |
| **B · 5,000-person manufacturer** (China) | Established, multi-site, traditional ERP | Multi-entity, 增值税专用发票, 用友/金蝶 integration, complex approval matrix | Modern UX, mobile-first, Stripe |
| **C · 500-person cross-border e-commerce** | Multi-currency, China + US/EU presence | Multi-entity, FX handling, Airwallex-like spend cards, fast onboarding | Deep ERP, full SOX |

**Recommendation: Start with Segment C** (cross-border e-commerce). It's the segment whose pain ExpenseFlow's existing AI core actually addresses, and it's the segment that will pay for the multi-entity work that's already designed.

---

## Why segment first?

The 8 industrial gaps in [`industrial-readiness-roadmap.md`](industrial-readiness-roadmap.md) all need investment. **Different segments need them in completely different orders.** Picking a segment = picking which gaps to close first.

If you try to be "for everyone", you become for nobody. Concur is "for everyone" because they had 25 years and $1.5B. We have 12 months and one team.

---

## Segment A · 50-person SaaS startup (US/EU)

### Profile

- 50 employees, $0-5M ARR, mostly engineering + sales
- Single legal entity in Delaware or UK
- All-remote or hybrid; people travel for client visits / offsites
- Founders care about speed; finance is 1 person (often part-time)
- Comparable companies: Linear, Vercel, Posthog at their early stage

### What they use today

- **Brex** or **Ramp** corp cards (~80% of US SaaS startups)
- Receipts uploaded via mobile app, OCR'd, auto-categorized
- Expense reports filed monthly, approved by founder/manager in Slack
- QuickBooks Online for accounting

### What they're frustrated by

- Brex/Ramp are great for cards but the **rules are limited** ("no per-person cap on team dinners")
- **Approval is too lax** — people just self-approve via card, audit later
- Hard to enforce per-cost-center budget caps
- AI categorization is OK but "wrong on edge cases" (which they don't have eval methodology to fix)

### What we'd offer them

- AI explanation card on top of card transactions ("this dinner = ¥800/person, well above team norm of ¥300")
- Per-cost-center budget enforcement with auto-alerts
- Auditable rule violations (cite-the-rule pattern)
- Eval framework so finance can tune rules without engineering

### What we'd NOT offer them (initially)

- Multi-entity (they have one entity)
- Multi-jurisdiction tax (single jurisdiction)
- 增值税专用发票 (irrelevant)
- Deep SAP / NetSuite integration (they use QBO; CSV import is fine)

### Effort to win first customer in this segment

- **Multi-tenancy** (Gap 6) — required
- **QuickBooks Online integration** — easier than NetSuite, ~2 weeks
- **Stripe Issuing card integration** — for the "we replace your Brex" play, ~1 month
- **Mobile responsive UI** — current desktop UI is fine for finance, but employees want mobile receipt capture
- **Modern auth (Clerk SSO with Google Workspace)** — required day 1

**Total to first customer: ~3-4 months**.

### Pricing willingness

- $5-15 / employee / month
- 50 employees × $10/mo = $6K MRR per customer
- Need ~10 customers to hit $720K ARR (a real seed-able number)

### Sales motion

- Self-serve onboarding (no demo call required for first 3 months)
- Product Hunt / Hacker News launch
- Founder-to-founder sales via warm intros

### Competition

- **Brex / Ramp** — incumbents in the segment, well-funded, can outspend on features
- **Mercury** for banking + simple expense
- **Pleo** in Europe

To win: **be cheaper or AI-superior**. We can't be cheaper; AI-superior is the only play.

### Verdict

⭐⭐⭐ Strong fit for AI core, but **competing against Brex/Ramp is brutal**. Recommend NOT starting here unless you have a specific founder relationship.

---

## Segment B · 5,000-person manufacturer (China)

### Profile

- 5,000 employees, established 10-30 years
- Multi-site (HQ + 3-5 factories + sales offices)
- 用友 NC or 金蝶 EAS for ERP
- Conservative IT culture, on-prem first
- Finance team: 30-50 people across HQ + sites
- Compliance pressure: 增值税专用发票, 国资委 rules if SOE

### What they use today

- **Concur** if they're large/sophisticated (~30%)
- **钉钉** built-in expense or 易快报 (~50%)
- **Manual Excel + email** (~20%)
- ERP integration via batch CSV import nightly

### What they're frustrated by

- Concur is expensive, English-first, doesn't speak 用友 fluently
- 钉钉 expense is free but **rules are weak** — no per-cost-center budget enforcement, no fraud detection
- Manual Excel doesn't scale; they hire more finance staff every year
- **VAT special invoice (增值税专用发票) workflow** is byzantine — Concur handles it badly, 钉钉 handles it OK, manual is best
- Audit pressure is real — SOE auditors look at everything

### What we'd offer them

- **Cite-the-rule explainability** translates well — auditors love structured violation IDs
- **Fraud investigator (Layer 2 OODA agent)** — China manufacturing has real reimbursement fraud (over-reporting attendees, ghost employees). Real value.
- **Multi-entity** (HQ + factories + sales) — once we ship multi-entity per [`multi-entity-design.md`](multi-entity-design.md), this segment is unlocked
- **用友 / 金蝶 CSV export** — Excel-as-bridge per [`integration-design.md`](integration-design.md)
- **Chinese-language UI** (already there)

### What we'd NOT offer them

- Modern UX as a primary value prop (they don't care; their employees fill out paper forms today)
- Stripe Issuing (irrelevant in China)
- Mobile-first (most employees use desktop in office)

### Effort to win first customer in this segment

- **Multi-entity implementation** (Gap 3 of [`multi-entity-design.md`](multi-entity-design.md)) — REQUIRED, ~3 weeks
- **Multi-jurisdiction policy** (Gap 3 of [roadmap](industrial-readiness-roadmap.md)) — only China for v1, but the framework
- **用友 NC export format** — 1 week of finance-ops + dev pairing
- **N-level approval chains** (Gap 2 of roadmap) — REQUIRED, manufacturing has 4-5 level approval, ~3 weeks
- **On-prem deployment option** — SOE customers will require this; 4-6 weeks
- **Chinese sales motion** — relationship-driven, slow

**Total to first customer: 6-9 months + a Chinese-speaking enterprise sales person**.

### Pricing willingness

- ¥30-80 / employee / month (≈ $4-11)
- 5,000 employees × ¥50/mo = ¥250K/mo = ¥3M/year (~$400K)
- Per-customer ACV is large; sales cycle is 3-6 months

### Sales motion

- Direct enterprise sales (relationship-driven, ¥100K+ deals)
- Channel partners (用友 / 金蝶 implementation consultants)
- Conference presence (CFO 2026, 财税年会)
- Reference customers matter a lot — first one is hardest

### Competition

- **Concur (SAP)** — strong incumbent in upper SOE; expensive, slow
- **易快报 (Yikuaibao)** — Chinese SMB-focused, good UX
- **钉钉 expense (Alibaba)** — free, weak features, sticky

To win: **vertical depth on manufacturing** + AI quality + 国资委-friendly architecture (on-prem deploy, audit-ready).

### Verdict

⭐⭐⭐⭐ Best long-term TAM, but **slow and capital-intensive** to enter. Need a Chinese co-founder or sales head. Not the segment to start with unless you already have that relationship.

---

## Segment C · 500-person cross-border e-commerce ⭐ recommended start

### Profile

- 500 employees, $20-100M GMV
- Multiple legal entities (HK holding + China ops + Singapore + EU/US fulfillment)
- Multi-currency reality: collect in USD/EUR, pay vendors in CNY, salaries in 4 currencies
- Tech-forward but not quite Silicon Valley
- Comparable: SHEIN, Anker, Patpat at their growth stage; Shopee Singapore HQ companies

### What they use today

- **Concur** if they tried (often dropped due to Chinese entity pain)
- **Airwallex** for FX + cards (~60% of cross-border e-commerce)
- **钉钉 expense** for the China entity, **Brex** for US entity, **manual** for SG/EU = three systems, no consolidation
- ERP: NetSuite for HQ, 用友 for China entity, Xero for SG = three ledgers

### What they're frustrated by

- **Multi-entity is a real, daily problem** — they actually need 4-layer decoupling (Entity / Category / Mapping / Policy from [`multi-entity-design.md`](multi-entity-design.md))
- Three different expense tools = no consolidation, finance team rebuilds spreadsheets monthly
- Currency conversion at submit vs approve vs pay — different policies in different jurisdictions
- Inter-entity transactions (HQ employee buys equipment for China subsidiary, who books it where?)
- Airwallex Spend AI is great if they're already an Airwallex customer; locked in if not

### What we'd offer them

- **Multi-entity from day 1** — `entities.yaml` + `gl_mapping_by_entity.yaml` + per-entity policy overrides (per [`multi-entity-design.md`](multi-entity-design.md))
- **AI explanation card with cross-record reasoning** — catches things like "expensed dinner in Shanghai but on Singapore entity's books"
- **Multi-currency handled correctly** — FX rate locked at submit, recorded in audit_report
- **Plays nice with Airwallex** — read-only integration with their card data; we add the rules + audit layer
- **CSV export to NetSuite + 用友 + Xero** — Excel-as-bridge for all three
- **Mid-market UX** — better than Concur, fast enough for 500-person org

### What we'd NOT offer them

- Self-serve onboarding (they need an implementation call)
- Stripe Issuing as primary (they use Airwallex)
- Real payment execution (Airwallex / banks already do this; we focus on the rules + audit layer)

### Effort to win first customer in this segment

- **Multi-entity implementation** — REQUIRED, ~3 weeks (already designed in `multi-entity-design.md`)
- **Multi-tenancy** (Gap 6) — REQUIRED, ~4 weeks
- **CSV exports for 3 ERPs** (NetSuite + 用友 + Xero) — ~3 weeks
- **Airwallex card-data ingestion** (read-only API integration) — ~2 weeks
- **Chinese + English bilingual UI** — already there ✅
- **Pilot deployment + bilingual implementation team** — 1 dedicated person

**Total to first customer: 4-5 months**, IF we can find a friendly first customer.

### Pricing willingness

- $8-20 / employee / month
- 500 employees × $12/mo = $6K MRR per customer
- Per-customer ACV: $72K/year — solid mid-market

### Sales motion

- Warm intros via cross-border e-commerce community (Slack groups, conferences)
- Founder-to-CFO direct sales
- Reference customers from cross-border e-commerce community matter
- 6-month sales cycle, 3-month implementation

### Competition

- **Airwallex Spend AI** — strongest fit for this segment; product-market fit; building out
- **Concur** — too heavy, pretty bad at China entity
- **No one else, really** — this segment is somewhat under-served because it requires multi-entity + multi-currency from day 1

To win: **be Airwallex-but-without-the-card-lock-in**, and be **deeper on AI/eval than Airwallex's current Spend AI**.

### Verdict

⭐⭐⭐⭐⭐ **Best fit for ExpenseFlow's existing strengths** — multi-entity is already designed, AI eval framework is differentiated, language coverage matches.

---

## Comparison matrix

| Dimension | A · SaaS startup | B · CN manufacturer | C · X-border e-commerce |
|---|---|---|---|
| Avg customer ACV | $6K/year | $400K/year | $72K/year |
| Sales cycle | 1-3 weeks (self-serve) | 3-6 months | 6 months |
| Implementation effort | None | 3-6 months | 3 months |
| Required engineering before first sale | 3-4 months | 6-9 months | 4-5 months |
| Competition density | High (Brex, Ramp, Mercury) | Medium (Concur, 易快报) | Low (Airwallex Spend AI) |
| Where ExpenseFlow's existing AI core helps | Some | Lots | Most |
| Where multi-entity helps | Not at all | Lots | Required |
| Capital required | $$ | $$$$$ | $$$ |
| Time to $1M ARR | 18-24 months (~150 customers) | 12-18 months (~3 customers) | 18-24 months (~15 customers) |

---

## Recommendation: start with Segment C

### Why C, not A

A (US SaaS) has the easiest sales motion but the hardest competition. Brex and Ramp will outspend us 100:1 on growth marketing. Even a perfect product loses to "free with our card program" + "every other startup uses us".

### Why C, not B

B (CN manufacturer) has the largest TAM but requires China sales DNA, on-prem deployment, and a 6-9 month build before the first dollar of revenue. Capital intensity is too high without external funding or a Chinese co-founder.

### Why C is the cleanest fit

1. **The multi-entity work that's already designed** ([`multi-entity-design.md`](multi-entity-design.md)) is REQUIRED for them but optional for A/B
2. **The AI eval framework** matters more for them — auditing across 4 entities means catches matter
3. **Mid-market ACV** ($72K) is large enough to justify implementation cost + small enough that founder-led sales works
4. **Competition is sparse** — only Airwallex really plays here, and they're focused on cards more than the rules-and-audit layer
5. **Language + cultural fit** — bilingual zh/en + understanding of CN business culture is a moat over US-only competitors

### What Segment C requires us to do FIRST

1. Multi-entity implementation (Gap 3 from [`multi-entity-design.md`](multi-entity-design.md)) — already designed, ~3 weeks
2. Multi-tenancy (Gap 6 from [roadmap](industrial-readiness-roadmap.md)) — ~4 weeks
3. NetSuite + 用友 + Xero CSV exports per [`integration-design.md`](integration-design.md) — ~3 weeks
4. Find 5 cross-border e-commerce CFOs willing to take a 30-min call

The 5 CFO calls come FIRST. Not after building. Talking to 5 of them in 2 weeks gives us:
- Whether the pain is real (yes, almost certainly)
- Which ERPs to prioritize (NetSuite vs 用友 vs Xero share)
- Whether they'd pay $12/employee/month
- Which one might be a design partner

**The discipline: customer interviews → segment validation → build for that segment, not the reverse.**

---

## What this doc deliberately doesn't say

- We're not going to actually do this. This is the "what we'd do IF" analysis.
- The segment definitions are heuristic, not researched market sizes.
- ACV / pricing / cycle estimates are based on industry averages, not deep research.
- Real strategy work would include: TAM / SAM / SOM analysis with sources; financial model; competitive matrix with feature-by-feature; go-to-market plan; org plan; fundraising plan. This doc has none of that. It's the **first-pass strategic frame** that informs whether to invest deeper.

---

## References

- [`industrial-readiness-roadmap.md`](industrial-readiness-roadmap.md) — what each segment requires us to build
- [`multi-entity-design.md`](multi-entity-design.md) — the architecture that unlocks Segment C
- [`integration-design.md`](integration-design.md) — concrete API designs for NetSuite + others
- [Airwallex Spend AI announcement](https://www.airwallex.com/blog/meet-your-finance-ai-agents-a-new-way-to-manage-bills-and-expenses) — Segment C primary competitor

---

*This doc captures the strategic question "who should we sell to?" It exists so that if/when ExpenseFlow ships to a real customer, the segment choice is documented, not improvised.*
