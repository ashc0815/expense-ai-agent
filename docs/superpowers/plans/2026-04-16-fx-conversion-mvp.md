# FX Currency Conversion MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mock exchange rate conversion so multi-currency reports show totals in the employee's home currency, with editable exchange rates per line item.

**Architecture:** A YAML mock rate table loaded by `fx_service.py` provides fixed rates. Conversion happens in the Report API response layer. Two new DB columns on Submission (`exchange_rate`, `converted_amount`) and one on Employee (`home_currency`) support per-line overrides.

**Tech Stack:** Python 3.9+, FastAPI, SQLAlchemy async (aiosqlite), PyYAML, vanilla JS frontend

---

## File Structure

| File | Responsibility |
|------|---------------|
| `config/fx_rates.yaml` | NEW — 6 hardcoded exchange rates (base: CNY) |
| `backend/services/__init__.py` | NEW — empty, makes `services` a package |
| `backend/services/fx_service.py` | NEW — load rates, convert, get_rate, get_supported_currencies |
| `backend/db/store.py` | MODIFY — add `home_currency` to Employee, `exchange_rate`+`converted_amount` to Submission |
| `backend/api/routes/reports.py` | MODIFY — return FX fields in response, support PATCH exchange_rate |
| `backend/api/routes/fx.py` | NEW — `GET /api/fx/rates` endpoint |
| `backend/main.py` | MODIFY — register fx router |
| `backend/quick/finalize.py` | MODIFY — copy exchange_rate from draft fields to submission |
| `frontend/employee/report.html` | MODIFY — home currency total, editable exchange rate in detail panel |
| `frontend/employee/quick.html` | MODIFY — currency dropdown, exchange rate display |
| `frontend/employee/my-reports.html` | MODIFY — report card total in home currency |
| `backend/tests/test_fx_service.py` | NEW — unit tests for fx_service |

---

### Task 1: Create mock rate table and FX service

**Files:**
- Create: `config/fx_rates.yaml`
- Create: `backend/services/__init__.py`
- Create: `backend/services/fx_service.py`
- Create: `backend/tests/test_fx_service.py`

- [ ] **Step 1: Create the YAML rate table**

Create `config/fx_rates.yaml`:

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

- [ ] **Step 2: Create the services package**

Create `backend/services/__init__.py` as an empty file.

- [ ] **Step 3: Write failing tests for fx_service**

Create `backend/tests/test_fx_service.py`:

```python
"""FX service unit tests."""
from __future__ import annotations

import pytest


def test_same_currency_returns_unchanged():
    from backend.services.fx_service import convert
    assert convert(100.0, "CNY", "CNY") == 100.0


def test_convert_usd_to_cny():
    from backend.services.fx_service import convert
    # 1 USD = 7.25 CNY, so 100 USD = 725 CNY
    assert convert(100.0, "USD", "CNY") == 725.0


def test_convert_usd_to_aud():
    from backend.services.fx_service import convert
    # 1 USD = 7.25 CNY, 1 AUD = 4.70 CNY
    # 100 USD = 725 CNY = 725 / 4.70 AUD = 154.255319...
    result = convert(100.0, "USD", "AUD")
    assert result == 154.26  # rounded to 2 dp


def test_convert_jpy_to_cny():
    from backend.services.fx_service import convert
    # 1 JPY = 0.048 CNY, so 10000 JPY = 480 CNY
    assert convert(10000.0, "JPY", "CNY") == 480.0


def test_get_rate_same_currency():
    from backend.services.fx_service import get_rate
    assert get_rate("CNY", "CNY") == 1.0


def test_get_rate_usd_to_aud():
    from backend.services.fx_service import get_rate
    # 1 USD = 7.25 CNY, 1 AUD = 4.70 CNY → 1 USD = 7.25/4.70 AUD
    rate = get_rate("USD", "AUD")
    assert rate == round(7.25 / 4.70, 6)


def test_get_rate_jpy_to_gbp():
    from backend.services.fx_service import get_rate
    # 1 JPY = 0.048 CNY, 1 GBP = 9.20 CNY → 1 JPY = 0.048/9.20 GBP
    rate = get_rate("JPY", "GBP")
    assert rate == round(0.048 / 9.20, 6)


def test_get_supported_currencies():
    from backend.services.fx_service import get_supported_currencies
    result = get_supported_currencies()
    assert result == ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]


def test_convert_zero_amount():
    from backend.services.fx_service import convert
    assert convert(0.0, "USD", "CNY") == 0.0


def test_unsupported_currency_raises():
    from backend.services.fx_service import convert
    with pytest.raises(KeyError):
        convert(100.0, "BRL", "CNY")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /Users/ashleychen/expense-ai-agent && python -m pytest backend/tests/test_fx_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.fx_service'`

- [ ] **Step 5: Implement fx_service.py**

Create `backend/services/fx_service.py`:

```python
"""FX conversion service — reads mock rates from config/fx_rates.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "fx_rates.yaml"

with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _data = yaml.safe_load(_f)

_RATES: dict[str, float] = _data["rates"]

SUPPORTED_CURRENCIES = ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]


def get_rate(from_currency: str, to_currency: str) -> float:
    if from_currency == to_currency:
        return 1.0
    return round(_RATES[from_currency] / _RATES[to_currency], 6)


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    if from_currency == to_currency:
        return amount
    amount_cny = amount * _RATES[from_currency]
    result = amount_cny / _RATES[to_currency]
    return round(result, 2)


def get_supported_currencies() -> list[str]:
    return list(SUPPORTED_CURRENCIES)


def get_all_rates() -> dict:
    return {"base": "CNY", "rates": dict(_RATES), "supported": SUPPORTED_CURRENCIES}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/ashleychen/expense-ai-agent && python -m pytest backend/tests/test_fx_service.py -v`
Expected: All 10 tests PASS

- [ ] **Step 7: Commit**

```bash
git add config/fx_rates.yaml backend/services/__init__.py backend/services/fx_service.py backend/tests/test_fx_service.py
git commit -m "feat: add FX service with mock rate table and unit tests"
```

---

### Task 2: Add DB columns (Employee.home_currency, Submission.exchange_rate + converted_amount)

**Files:**
- Modify: `backend/db/store.py:46-63` (Employee model)
- Modify: `backend/db/store.py:65-128` (Submission model)
- Modify: `backend/db/store.py:1134-1147` (seed data)

- [ ] **Step 1: Add home_currency to Employee model**

In `backend/db/store.py`, inside the `Employee` class, add after the `city` column (line 59):

```python
    home_currency = Column(String(3), nullable=False, default="CNY")
```

- [ ] **Step 2: Add exchange_rate and converted_amount to Submission model**

In `backend/db/store.py`, inside the `Submission` class, add after the `report_id` column (line 123) and before the timestamps:

```python
    # ── 汇率 ────────────────────────────────────────────────────────
    exchange_rate    = Column(Numeric(10, 6), nullable=True)   # 1 invoice_currency = X home_currency
    converted_amount = Column(Numeric(12, 2), nullable=True)   # amount * exchange_rate
```

- [ ] **Step 3: Update seed data to set home_currency for demo employees**

In `backend/db/store.py`, in the `seed_budget_demo` function, update the employee seed list (around line 1135) from:

```python
    for emp_id, name, cc in [
        ("E001", "Zhang Wei", "ENG-TRAVEL"),
        ("E002", "Li Mei",   "MKT-EVENTS"),
        ("E003", "Wang Fang","ENG-TRAVEL"),
    ]:
```

to:

```python
    for emp_id, name, cc, hc in [
        ("E001", "Zhang Wei", "ENG-TRAVEL", "CNY"),
        ("E002", "Li Mei",   "MKT-EVENTS", "AUD"),
        ("E003", "Wang Fang","ENG-TRAVEL", "CNY"),
    ]:
```

And update the `Employee()` constructor (around line 1143) from:

```python
        db.add(Employee(
            id=emp_id, name=name,
            department="Engineering" if cc == "ENG-TRAVEL" else "Marketing",
            cost_center=cc,
        ))
```

to:

```python
        db.add(Employee(
            id=emp_id, name=name,
            department="Engineering" if cc == "ENG-TRAVEL" else "Marketing",
            cost_center=cc,
            home_currency=hc,
        ))
```

- [ ] **Step 4: Verify the app starts without errors**

Run: `cd /Users/ashleychen/expense-ai-agent && python -c "from backend.db.store import Employee, Submission; print('OK: Employee.home_currency =', Employee.home_currency.key); print('OK: Submission.exchange_rate =', Submission.exchange_rate.key)"`
Expected: Prints `OK: Employee.home_currency = home_currency` and `OK: Submission.exchange_rate = exchange_rate`

- [ ] **Step 5: Delete the old SQLite database and restart to pick up schema changes**

Run: `cd /Users/ashleychen/expense-ai-agent && rm -f concurshield.db expenseflow.db`

Note: Since this project uses SQLite with `create_all` (which doesn't alter existing tables), the old DB file must be deleted to pick up the new columns. The seed data will be re-created on next startup.

- [ ] **Step 6: Commit**

```bash
git add backend/db/store.py
git commit -m "feat: add Employee.home_currency and Submission.exchange_rate columns"
```

---

### Task 3: Update Report API to return FX fields

**Files:**
- Modify: `backend/api/routes/reports.py:48-100` (_line_dict, _draft_line_dict, _report_payload)

- [ ] **Step 1: Update _line_dict to include exchange_rate and converted_amount**

In `backend/api/routes/reports.py`, in the `_line_dict` function (line 48), add two fields after `"currency": s.currency,` (line 54):

```python
        "exchange_rate": float(s.exchange_rate) if s.exchange_rate is not None else None,
        "converted_amount": float(s.converted_amount) if s.converted_amount is not None else None,
```

- [ ] **Step 2: Update _report_payload to compute FX totals**

In `backend/api/routes/reports.py`, replace the `_report_payload` function (lines 90-100) with:

```python
async def _report_payload(db: AsyncSession, report) -> dict:
    from backend.services.fx_service import get_rate, convert as fx_convert
    subs = await list_report_submissions(db, report.id)
    drafts = await list_report_drafts(db, report.id)
    total = sum(float(s.amount) for s in subs)

    emp = await get_employee(db, report.employee_id)
    home_currency = emp.home_currency if emp and emp.home_currency else "CNY"

    lines = []
    total_converted = 0.0
    for s in subs:
        line = _line_dict(s)
        currency = s.currency or "CNY"
        amt = float(s.amount)
        if s.exchange_rate is not None:
            rate = float(s.exchange_rate)
            conv = round(amt * rate, 2)
        else:
            rate = get_rate(currency, home_currency)
            conv = fx_convert(amt, currency, home_currency)
        line["exchange_rate"] = rate
        line["converted_amount"] = conv
        total_converted += conv
        lines.append(line)

    draft_lines = []
    for d in drafts:
        dl = _draft_line_dict(d)
        currency = dl.get("currency") or "CNY"
        amt = dl.get("amount")
        if amt is not None:
            rate = get_rate(currency, home_currency)
            conv = fx_convert(float(amt), currency, home_currency)
            dl["exchange_rate"] = rate
            dl["converted_amount"] = conv
            total_converted += conv
        else:
            dl["exchange_rate"] = None
            dl["converted_amount"] = None
        draft_lines.append(dl)

    return {
        **_report_dict(report),
        "lines": lines,
        "pending_drafts": draft_lines,
        "total_amount": total,
        "total_converted": round(total_converted, 2),
        "home_currency": home_currency,
        "line_count": len(subs),
    }
```

- [ ] **Step 3: Add exchange_rate to EDITABLE_FIELDS**

In `backend/api/routes/reports.py`, update the `EDITABLE_FIELDS` set (line 395) to include `"exchange_rate"`:

```python
EDITABLE_FIELDS = {
    "merchant", "amount", "currency", "category", "date",
    "tax_amount", "project_code", "description",
    "invoice_number", "invoice_code", "seller_tax_id", "buyer_tax_id",
    "department", "cost_center", "gl_account",
    "exchange_rate",
}
```

- [ ] **Step 4: Add converted_amount recalculation when exchange_rate is patched**

In `backend/api/routes/reports.py`, in the `patch_report_line` function, after `setattr(sub, body.field, body.value)` (line 441), add logic to recalculate `converted_amount` when either `exchange_rate` or `amount` is changed:

```python
    setattr(sub, body.field, body.value)

    if body.field == "exchange_rate" and body.value is not None:
        sub.converted_amount = round(float(sub.amount) * float(body.value), 2)
    elif body.field == "amount" and sub.exchange_rate is not None:
        sub.converted_amount = round(float(body.value) * float(sub.exchange_rate), 2)

    sub.updated_at = datetime.now(timezone.utc)
```

- [ ] **Step 5: Verify the app starts and the report detail endpoint returns new fields**

Run: `cd /Users/ashleychen/expense-ai-agent && python -c "from backend.api.routes.reports import _line_dict; print('Module loads OK')"`
Expected: `Module loads OK`

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/reports.py
git commit -m "feat: report API returns FX conversion fields (exchange_rate, converted_amount, total_converted, home_currency)"
```

---

### Task 4: Add FX rates API endpoint

**Files:**
- Create: `backend/api/routes/fx.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create the FX rates endpoint**

Create `backend/api/routes/fx.py`:

```python
"""FX rates endpoint — returns mock rate table for frontend currency dropdowns."""
from __future__ import annotations

from fastapi import APIRouter

from backend.services.fx_service import get_all_rates

router = APIRouter()


@router.get("/rates")
async def get_fx_rates():
    return get_all_rates()
```

- [ ] **Step 2: Register the FX router in main.py**

In `backend/main.py`, find where other routers are included (look for `app.include_router`). Add:

```python
from backend.api.routes.fx import router as fx_router
app.include_router(fx_router, prefix="/api/fx", tags=["fx"])
```

- [ ] **Step 3: Verify the endpoint works**

Start the server and test:

Run: `curl -s http://localhost:8000/api/fx/rates | python -m json.tool`
Expected:
```json
{
    "base": "CNY",
    "rates": {
        "CNY": 1.0,
        "AUD": 4.7,
        "USD": 7.25,
        "EUR": 7.85,
        "JPY": 0.048,
        "GBP": 9.2
    },
    "supported": ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"]
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/api/routes/fx.py backend/main.py
git commit -m "feat: add GET /api/fx/rates endpoint"
```

---

### Task 5: Copy exchange_rate from draft to submission on attest

**Files:**
- Modify: `backend/quick/finalize.py:76-94` (save_draft_as_report_line)

- [ ] **Step 1: Update save_draft_as_report_line to include exchange_rate**

In `backend/quick/finalize.py`, in the `save_draft_as_report_line` function, update the `create_submission` call (lines 76-94). After the `"gl_account"` line, add the exchange_rate and converted_amount fields:

Replace the `sub = await create_submission(db, {...})` block with:

```python
    from backend.services.fx_service import get_rate

    emp_home = emp.home_currency if emp and hasattr(emp, 'home_currency') and emp.home_currency else "CNY"
    invoice_currency = fields.get("currency", "CNY")
    user_rate = fields.get("exchange_rate")

    if user_rate is not None:
        fx_rate = float(user_rate)
    elif invoice_currency != emp_home:
        fx_rate = get_rate(invoice_currency, emp_home)
    else:
        fx_rate = 1.0

    amount_val = float(fields["amount"])
    converted = round(amount_val * fx_rate, 2)

    sub = await create_submission(db, {
        "employee_id":    ctx.user_id,
        "status":         "in_report",
        "amount":         amount_val,
        "currency":       invoice_currency,
        "category":       fields["category"],
        "date":           fields["date"],
        "merchant":       fields["merchant"],
        "tax_amount":     float(fields.get("tax_amount") or 0) or None,
        "project_code":   fields.get("project_code"),
        "description":    fields.get("description"),
        "receipt_url":    draft.receipt_url,
        "invoice_number": inv,
        "invoice_code":   fields.get("invoice_code"),
        "department":     department,
        "cost_center":    cost_center,
        "gl_account":     gl_account,
        "report_id":      report.id,
        "exchange_rate":  fx_rate,
        "converted_amount": converted,
    })
```

- [ ] **Step 2: Verify the module loads without error**

Run: `cd /Users/ashleychen/expense-ai-agent && python -c "from backend.quick.finalize import save_draft_as_report_line; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/quick/finalize.py
git commit -m "feat: copy exchange_rate from draft fields to submission on attest"
```

---

### Task 6: Update report.html — home currency total and exchange rate in detail panel

**Files:**
- Modify: `frontend/employee/report.html`

- [ ] **Step 1: Update the total row to use home currency**

In `frontend/employee/report.html`, find the total row rendering in the `render()` function. Replace the existing total row:

```javascript
      <div class="total-row">合计 ${currencies.length <= 1
        ? `${currencies[0] || "CNY"} ${total.toFixed(2)}`
        : Object.entries(totalsByCurrency).map(([c, a]) => `${c} ${a.toFixed(2)}`).join(" + ")
      }</div>
```

with:

```javascript
      <div class="total-row">合计 ≈ ${rp.home_currency || "CNY"} ${(rp.total_converted || total).toFixed(2)}</div>
```

- [ ] **Step 2: Remove the now-unused currencies/totalsByCurrency computation**

In the `render()` function, remove these lines that were added previously:

```javascript
  const currencies = [...new Set(allItems.map(i => i.currency || "CNY"))];
  const totalsByCurrency = {};
  for (const item of allItems) {
    const c = item.currency || "CNY";
    totalsByCurrency[c] = (totalsByCurrency[c] || 0) + (Number(item.amount) || 0);
  }
```

- [ ] **Step 3: Add exchange rate row in the detail panel**

In the `showDetail()` function, find where the editable form fields are rendered for the detail panel. After the amount field, add an exchange rate row. Find the section that builds `html` for the detail panel form fields. After the amount/currency related fields, add:

```javascript
      const itemCurrency = item.currency || "CNY";
      const homeCurrency = reportData.home_currency || "CNY";
      const showFx = itemCurrency !== homeCurrency;
```

Then, after the amount form field in the detail panel HTML, add:

```html
      ${showFx ? `
        <div class="dp-field">
          <div class="dp-label">汇率</div>
          <div class="dp-val">
            ${canEdit ? `
              1 ${itemCurrency} =
              <input class="dp-input" style="width:100px;display:inline" 
                     id="fx-rate-input" type="number" step="0.000001"
                     value="${item.exchange_rate || ''}"
                     oninput="updateConvertedPreview()">
              ${homeCurrency}
            ` : `1 ${itemCurrency} = ${item.exchange_rate || '—'} ${homeCurrency}`}
          </div>
        </div>
        <div class="dp-field">
          <div class="dp-label">折算金额</div>
          <div class="dp-val" id="converted-preview">
            ≈ ${homeCurrency} ${item.converted_amount != null ? Number(item.converted_amount).toFixed(2) : '—'}
          </div>
        </div>
      ` : ''}
```

- [ ] **Step 4: Add the updateConvertedPreview() function**

Add this function in the `<script>` section:

```javascript
function updateConvertedPreview() {
  const rateInput = document.getElementById("fx-rate-input");
  const preview = document.getElementById("converted-preview");
  if (!rateInput || !preview) return;
  const rate = parseFloat(rateInput.value);
  const amountInput = document.querySelector('.dp-input[data-field="amount"]');
  const amount = amountInput ? parseFloat(amountInput.value) : null;
  const homeCurrency = reportData.home_currency || "CNY";
  if (!isNaN(rate) && amount != null && !isNaN(amount)) {
    preview.textContent = `≈ ${homeCurrency} ${(amount * rate).toFixed(2)}`;
  }
}
```

- [ ] **Step 5: Update saveLineEdits() to include exchange_rate**

In the `saveLineEdits()` function, find where it iterates over editable fields and PATCHes each one. Add exchange_rate to the list of fields to save. After the existing field save loop, add:

```javascript
  const fxInput = document.getElementById("fx-rate-input");
  if (fxInput) {
    const newRate = parseFloat(fxInput.value);
    if (!isNaN(newRate)) {
      await fetch(`/api/reports/${REPORT_ID}/lines/${currentDetailId}`, {
        method: "PATCH",
        headers: {...authHeaders, "Content-Type": "application/json"},
        body: JSON.stringify({field: "exchange_rate", value: newRate})
      });
    }
  }
```

- [ ] **Step 6: Test in browser**

1. Start the server: `cd /Users/ashleychen/expense-ai-agent && python -m backend.main`
2. Open `http://localhost:8000/employee/report.html?report_id=<id>`
3. Verify: total shows `合计 ≈ CNY xxx.xx` (or AUD if logged in as E002)
4. Click a line item with foreign currency — verify exchange rate and converted amount appear
5. Edit the exchange rate — verify the converted preview updates in real time
6. Save — verify the rate is persisted

- [ ] **Step 7: Commit**

```bash
git add frontend/employee/report.html
git commit -m "feat: report.html shows home currency total and editable exchange rate"
```

---

### Task 7: Update quick.html — currency dropdown and exchange rate display

**Files:**
- Modify: `frontend/employee/quick.html`

- [ ] **Step 1: Fetch FX rates and employee home currency on page load**

In the `<script>` section of `quick.html`, add near the top (after variable declarations):

```javascript
let fxRates = {};
let homeCurrency = "CNY";

async function loadFxData() {
  try {
    const authHeaders = window.auth ? await window.auth.getHeaders() : {};
    const [ratesRes] = await Promise.all([
      fetch("/api/fx/rates")
    ]);
    if (ratesRes.ok) {
      const data = await ratesRes.json();
      fxRates = data.rates || {};
    }
  } catch(e) { console.warn("FX rates load failed", e); }
}
```

Call `loadFxData()` during page initialization (alongside other init calls).

- [ ] **Step 2: Replace hardcoded currency text input with a dropdown**

In the `showForm()` function, find where the currency field is rendered. Replace the currency input with a `<select>`:

```javascript
      const currencyOrder = ["CNY", "AUD", "USD", "EUR", "JPY", "GBP"];
      const currentCurrency = fields.currency || "CNY";
      const currencyOptions = currencyOrder.map(c =>
        `<option value="${c}" ${c === currentCurrency ? 'selected' : ''}>${c}</option>`
      ).join("");
```

Then use this in the form HTML where currency is rendered:

```html
<select class="form-input" name="currency" onchange="onCurrencyChange(this.value)">${currencyOptions}</select>
```

- [ ] **Step 3: Add exchange rate display below amount when foreign currency**

After the currency dropdown and amount field in the form, add a container for the exchange rate row:

```html
<div id="fx-row" style="display:none; margin-top:.5rem; padding:.5rem .8rem; background:#f0fdf9; border-radius:8px; font-size:.85rem; color:#0f172a;">
  <span id="fx-label"></span>
  <input id="fx-rate" type="number" step="0.000001" style="width:90px; margin:0 .3rem; padding:.2rem .4rem; border:1px solid #e2e8f0; border-radius:4px; font-size:.85rem;" oninput="updateQuickConverted()">
  <span id="fx-converted" style="margin-left:.5rem; color:#64748b;"></span>
</div>
```

- [ ] **Step 4: Add onCurrencyChange and updateQuickConverted functions**

```javascript
function onCurrencyChange(newCurrency) {
  const fxRow = document.getElementById("fx-row");
  const amountInput = document.querySelector('input[name="amount"]');
  if (!fxRow) return;

  if (newCurrency === homeCurrency) {
    fxRow.style.display = "none";
    return;
  }

  fxRow.style.display = "block";
  const fromRate = fxRates[newCurrency] || 1;
  const toRate = fxRates[homeCurrency] || 1;
  const rate = (fromRate / toRate).toFixed(6);

  document.getElementById("fx-label").textContent = `1 ${newCurrency} =`;
  document.getElementById("fx-rate").value = rate;
  document.getElementById("fx-converted").textContent = "";

  if (amountInput && amountInput.value) {
    updateQuickConverted();
  }
}

function updateQuickConverted() {
  const rateInput = document.getElementById("fx-rate");
  const amountInput = document.querySelector('input[name="amount"]');
  const converted = document.getElementById("fx-converted");
  if (!rateInput || !amountInput || !converted) return;

  const rate = parseFloat(rateInput.value);
  const amount = parseFloat(amountInput.value);
  if (!isNaN(rate) && !isNaN(amount)) {
    converted.textContent = `≈ ${homeCurrency} ${(amount * rate).toFixed(2)}`;
  }
}
```

- [ ] **Step 5: Include exchange_rate in draft fields when saving**

In the `saveToReport()` function (or wherever draft fields are synced back before attest), add:

```javascript
  const fxRateInput = document.getElementById("fx-rate");
  const currencySelect = document.querySelector('select[name="currency"]');
  if (fxRateInput && currencySelect && currencySelect.value !== homeCurrency) {
    fieldsToSync["exchange_rate"] = parseFloat(fxRateInput.value);
  }
```

- [ ] **Step 6: Trigger exchange rate display on form load if currency is foreign**

At the end of `showForm()`, after the form is rendered, add:

```javascript
  const currencyVal = fields.currency || "CNY";
  if (currencyVal !== homeCurrency) {
    onCurrencyChange(currencyVal);
  }
```

- [ ] **Step 7: Test in browser**

1. Open `http://localhost:8000/employee/quick.html`
2. Upload a receipt
3. Change currency dropdown from CNY to USD
4. Verify exchange rate row appears: `1 USD = 1.542553` (auto-calculated)
5. Enter an amount — verify converted amount updates
6. Edit the exchange rate manually — verify converted amount updates
7. Save to report — verify the exchange_rate is stored

- [ ] **Step 8: Commit**

```bash
git add frontend/employee/quick.html
git commit -m "feat: quick.html currency dropdown with exchange rate display"
```

---

### Task 8: Update my-reports.html — report card total in home currency

**Files:**
- Modify: `frontend/employee/my-reports.html`

- [ ] **Step 1: Update the report card total display**

In `frontend/employee/my-reports.html`, find where the total is rendered in each report card. The current code shows `合计 CNY ${total}` or similar. Replace it with:

```javascript
合计 ≈ ${r.home_currency || "CNY"} ${(r.total_converted || r.total_amount || 0).toFixed(2)}
```

This uses `total_converted` and `home_currency` from the API response (which now includes these fields from Task 3's `_report_payload` changes).

- [ ] **Step 2: Test in browser**

1. Open `http://localhost:8000/employee/my-reports.html`
2. Verify report cards show `合计 ≈ CNY xxx.xx` (or AUD for employee E002)
3. If a report has mixed currency items, verify the total is properly converted

- [ ] **Step 3: Commit**

```bash
git add frontend/employee/my-reports.html
git commit -m "feat: my-reports.html shows report totals in home currency"
```

---

### Task 9: End-to-end verification

**Files:** None (testing only)

- [ ] **Step 1: Delete old database and restart server**

```bash
cd /Users/ashleychen/expense-ai-agent
rm -f concurshield.db expenseflow.db
python -m backend.main
```

- [ ] **Step 2: Run all existing tests to check for regressions**

Run: `cd /Users/ashleychen/expense-ai-agent && python -m pytest backend/tests/ -v --tb=short`
Expected: All tests pass (some may need minor fixes for new columns)

- [ ] **Step 3: Manual E2E test flow**

1. Login as employee (default mock user)
2. Go to quick.html → upload a receipt
3. Change currency to USD → verify exchange rate appears
4. Fill amount → verify converted amount shows
5. Save to report
6. Go to report.html → verify:
   - Line item shows `USD xx.xx` in amount column
   - Total shows `合计 ≈ CNY xx.xx` (converted)
   - Click line item → detail panel shows editable exchange rate
   - Edit exchange rate → converted amount updates
   - Save → total updates
7. Go to my-reports.html → verify card shows home currency total

- [ ] **Step 4: Fix any issues found and commit**

```bash
git add -A
git commit -m "fix: address E2E test findings for FX conversion"
```
