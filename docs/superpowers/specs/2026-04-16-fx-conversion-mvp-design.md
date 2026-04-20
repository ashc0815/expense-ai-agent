# FX Currency Conversion MVP — Design Spec

## Goal

Add basic multi-currency support to ExpenseFlow: mock exchange rate table, per-employee home currency, report totals converted to home currency, editable exchange rate per line item.

## Architecture

Pure backend conversion. A `fx_service.py` reads a hardcoded YAML rate table at startup, converts amounts on demand. No external API calls. Frontend displays converted totals and lets employees edit the exchange rate per line item.

## Scope

**In scope:**
- 6 currencies: CNY, AUD, USD, EUR, JPY, GBP
- Mock rate table in YAML (fixed rates, no live data)
- Employee `home_currency` field (default CNY)
- Submission `exchange_rate` + `converted_amount` fields
- Report API returns `total_converted` + `home_currency`
- Each line item returns `exchange_rate` + `converted_amount`
- Editable exchange rate in line item detail (report.html + quick.html)
- Currency dropdown on expense forms (default order: CNY, AUD, USD, then rest)
- Report card totals in home currency (my-reports.html)

**Out of scope (Phase 2):**
- Real FX API (OANDA/XE)
- FX fraud detection rules (rate deviation, stale submission, threshold proximity)
- LLM explanation layer for FX anomalies
- Multi-currency budget tracking
- FX gain/loss accounting in vouchers

---

## Data Layer

### New file: `config/fx_rates.yaml`

```yaml
# All rates expressed as: 1 <currency> = X CNY
# To convert between non-CNY pairs: go through CNY as intermediary
base: CNY
rates:
  CNY: 1.0
  AUD: 4.70
  USD: 7.25
  EUR: 7.85
  JPY: 0.048
  GBP: 9.20
```

### DB Changes: `Employee` table

Add column:
```python
home_currency = Column(String(3), default="CNY")
```

### DB Changes: `Submission` table

Add columns:
```python
exchange_rate = Column(Numeric(10, 6), nullable=True)      # 1 invoice_currency = X home_currency
converted_amount = Column(Numeric(12, 2), nullable=True)   # amount * exchange_rate, in home_currency
```

When `exchange_rate` is NULL, the system uses the default rate from the mock table. When the employee manually sets a rate, it is stored and used for all subsequent calculations.

### DB Changes: `Draft` table

No schema change. Draft `fields` JSON already supports arbitrary keys — `exchange_rate` will be stored there during the draft phase, then copied to Submission on attest/submit.

---

## Service Layer

### New file: `backend/services/fx_service.py`

~30 lines. Responsibilities:

1. **`load_rates()`** — Read `config/fx_rates.yaml` at import time, cache in module-level dict.

2. **`convert(amount, from_currency, to_currency) -> float`**
   - If `from_currency == to_currency`: return amount unchanged
   - Convert to CNY: `amount_cny = amount * rates[from_currency]`
   - Convert to target: `result = amount_cny / rates[to_currency]`
   - Round to 2 decimal places

3. **`get_rate(from_currency, to_currency) -> float`**
   - Returns the exchange rate (e.g., 1 USD = ? AUD)
   - `rate = rates[from_currency] / rates[to_currency]`
   - Round to 6 decimal places

4. **`get_supported_currencies() -> list[str]`**
   - Returns `["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]`

---

## API Layer

### `GET /api/reports/{id}` — Response changes

Current response includes `total_amount` (raw sum, meaningless for mixed currencies).

New fields added to response:
```json
{
  "total_amount": 183.01,
  "total_converted": 1231.45,
  "home_currency": "AUD",
  "lines": [
    {
      "id": "xxx",
      "amount": 84.80,
      "currency": "USD",
      "exchange_rate": 1.5426,
      "converted_amount": 130.81
    }
  ]
}
```

**Calculation logic:**
- For each line: if `submission.exchange_rate` is set, use it; otherwise call `fx_service.get_rate(currency, home_currency)`
- `converted_amount = amount * exchange_rate`
- `total_converted = sum of all converted_amounts`
- `home_currency` from Employee record

### `PATCH /api/reports/{report_id}/lines/{submission_id}` — Support `exchange_rate`

When employee edits the exchange rate:
- Store `exchange_rate` on Submission
- Recalculate `converted_amount = amount * exchange_rate`
- Store both fields

### `GET /api/reports` (list) — Response changes

Each report card includes:
```json
{
  "total_converted": 1231.45,
  "home_currency": "AUD"
}
```

### `GET /api/fx/rates` — New endpoint (optional convenience)

Returns the mock rate table for frontend currency dropdowns:
```json
{
  "base": "CNY",
  "rates": {"CNY": 1.0, "AUD": 4.70, "USD": 7.25, "EUR": 7.85, "JPY": 0.048, "GBP": 9.20},
  "supported": ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]
}
```

---

## Frontend Layer

### `report.html`

1. **Expense table** — Amount column stays as original currency (e.g., `USD 84.80`)
2. **Total row** — Changes from `合计 CNY 183.01` to `合计 ≈ AUD 130.81` (uses `total_converted` + `home_currency`)
3. **Line item detail panel** — Add new row below amount:
   - Label: `汇率`
   - Display: `1 USD = 1.5426 AUD` (editable input for the rate number)
   - Below it: `折算金额: ≈ AUD 130.81` (recalculates on rate change)
   - Save button PATCHes `exchange_rate` to backend

### `quick.html`

1. **Currency dropdown** — Replace any hardcoded currency with a `<select>`:
   - Default order: CNY, AUD, USD, EUR, JPY, GBP
   - Pre-selected based on draft `fields.currency` or default CNY
2. **When currency != home_currency** — Show below amount field:
   - `汇率: 1 USD = 1.5426 AUD` (editable)
   - `折算金额: ≈ AUD 130.81`
3. **Save to report** — Include `exchange_rate` in the draft fields sent to backend

### `my-reports.html`

1. **Report card total** — Change from `合计 CNY 183.01` to `合计 ≈ AUD 130.81` (uses `total_converted` + `home_currency` from API)

---

## Employee Home Currency

For MVP, `home_currency` is set per employee in the database. No UI to change it — set via seed data or direct DB update.

Default test employees:
- employee `emp-001`: `home_currency = "CNY"`
- employee `emp-002`: `home_currency = "AUD"` (for demo purposes)

---

## Edge Cases

1. **Same currency** — `exchange_rate` = 1.0, `converted_amount` = `amount`. No conversion UI shown.
2. **Employee has no `home_currency`** — Default to CNY.
3. **Line item has no `exchange_rate` in DB** — Calculate from mock table on the fly.
4. **Employee edits amount after setting custom rate** — `converted_amount` recalculates using the stored rate.
5. **Draft items (not yet submitted)** — Use draft `fields.currency` and calculate rate from mock table. No `exchange_rate` stored until submission.

---

## Files Changed Summary

| Layer | File | Change |
|-------|------|--------|
| Config | `config/fx_rates.yaml` | NEW — 6 fixed exchange rates |
| DB | `backend/db/store.py` | Employee: add `home_currency`; Submission: add `exchange_rate`, `converted_amount` |
| Service | `backend/services/fx_service.py` | NEW — ~30 lines, convert/get_rate/get_supported |
| API | `backend/api/routes/reports.py` | Return `total_converted`, `home_currency`, per-line `exchange_rate`/`converted_amount`; PATCH support for `exchange_rate` |
| API | `backend/api/routes/fx.py` | NEW (optional) — `GET /api/fx/rates` |
| Frontend | `frontend/employee/report.html` | Total in home currency; detail panel adds editable exchange rate |
| Frontend | `frontend/employee/quick.html` | Currency dropdown (CNY/AUD/USD first); exchange rate display when foreign currency |
| Frontend | `frontend/employee/my-reports.html` | Report card total in home currency |
