# Budget Spend Trend & Forecast Design

**Date:** 2026-04-14
**Status:** Approved

---

## Goal

Surface quarterly spend trend and budget overrun forecast in two places: (1) the employee AI chat proactively warns when spending pace is high-risk, and (2) the finance admin budget-policy table gains a sparkline column and a projected overrun date column.

---

## Architecture

Extend the existing `get_budget_status()` store function to compute a rolling 3-month trend and append a `trend` field to its return dict. No new API endpoint. The `GET /budget/status/{cost_center}` route automatically includes `trend`. The `GET /budget/snapshot/me` route reads `trend.overrun_risk` from the same call and appends a one-sentence forecast to the chat message when risk is high. The `employee_qa` system prompt gets a single line of guidance. The admin frontend fetches status for each cost center and renders sparkline + overrun badge inline.

**Tech Stack:** Python / SQLAlchemy async (aiosqlite), FastAPI, Vanilla JS + inline SVG

---

## Section 1 — Backend: `store.py` → `get_budget_status()`

### What changes

Append a `trend` key to the dict returned by `get_budget_status()`. Trend is computed from the 3 complete calendar months immediately before today, regardless of quarter boundary.

### New helper: `_rolling_months(n: int) → list[tuple[str, str]]`

Returns a list of `(start_date, end_date)` ISO strings for the last `n` complete calendar months, newest-first.

```python
def _rolling_months(n: int) -> list[tuple[str, str]]:
    """Return (start, end) pairs for the last n complete calendar months, newest first."""
    today = date.today()
    result = []
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        last_day = calendar.monthrange(year, month)[1]
        result.append((f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"))
    return result
```

### Trend computation in `get_budget_status()`

After the existing `signal` computation block, add:

```python
# ── rolling 3-month trend ──────────────────────────────────────
month_ranges = _rolling_months(3)
month_totals: list[float] = []
for m_start, m_end in month_ranges:
    m_result = await db.execute(
        select(func.sum(Submission.amount)).where(
            Submission.cost_center == cost_center,
            Submission.date >= m_start,
            Submission.date <= m_end,
            Submission.status.notin_(["rejected", "review_failed"]),
        )
    )
    month_totals.append(float(m_result.scalar() or 0))

monthly_avg = sum(month_totals) / len(month_totals) if month_totals else 0.0
remaining = float(budget.total_amount) - spent_f

if monthly_avg > 0 and remaining > 0:
    months_until_exhaust = remaining / monthly_avg
    overrun_date = date.today() + timedelta(days=int(months_until_exhaust * 30))
    estimated_overrun_date = overrun_date.isoformat()
elif remaining <= 0:
    estimated_overrun_date = date.today().isoformat()
    months_until_exhaust = 0.0
else:
    estimated_overrun_date = None
    months_until_exhaust = None

if months_until_exhaust is not None and months_until_exhaust < 1.0:
    overrun_risk = "high"
elif months_until_exhaust is not None and months_until_exhaust < 2.0:
    overrun_risk = "moderate"
else:
    overrun_risk = "ok"

out["trend"] = {
    "monthly_avg": round(monthly_avg, 2),
    "months": list(reversed(month_totals)),   # oldest → newest for sparkline
    "overrun_risk": overrun_risk,
    "estimated_overrun_date": estimated_overrun_date,
}
```

`timedelta` is already imported via `from datetime import date, datetime, timezone` — add `timedelta` to that import.

### Result shape

```json
{
  "cost_center": "ENG-TRAVEL",
  "signal": "info",
  "trend": {
    "monthly_avg": 2175.00,
    "months": [1800.0, 2200.0, 2525.0],
    "overrun_risk": "high",
    "estimated_overrun_date": "2026-05-28"
  },
  ...
}
```

When `configured: false` (no budget row), `trend` is omitted from the response entirely.

---

## Section 2 — Backend: `budget.py` → `GET /budget/snapshot/me`

### What changes

After computing `msg`, check `trend.overrun_risk`. If it is `"high"`, append a forecast sentence to `msg` before returning.

```python
trend = status.get("trend")
if trend and trend.get("overrun_risk") == "high" and trend.get("estimated_overrun_date"):
    overrun_date_str = trend["estimated_overrun_date"]
    avg = trend["monthly_avg"]
    msg += (
        f" 按近 3 个月月均 ¥{avg:,.0f} 的消费节奏，"
        f"预计 {overrun_date_str} 前后预算耗尽。"
    )
```

This block only runs for `sig == "info"` or `sig == "blocked"` (the existing `if sig == "ok"` guard already returns `{"message": None}` before this point). No changes to `sig == "over_budget"` branch — if already over, no forecast needed.

---

## Section 3 — Backend: `chat.py` → `employee_qa` system prompt

### What changes

Append one sentence to `_SYSTEM_PROMPTS["employee_qa"]`:

```
当 get_budget_summary 返回的数据中 signal 为 'info' 或 'blocked'，
且 trend.overrun_risk 为 'high' 时，在预算提示后补充趋势预测（一句话，语气自然）。
如果 trend 为 null 或 overrun_risk 为 'ok'/'moderate'，不提趋势。
```

`tool_get_budget_summary` in `chat.py` calls `store.get_budget_status()` (which now auto-includes `trend`), but currently returns only a pre-formatted `result` string — the LLM cannot read `trend` from it. Add `trend` as a top-level key alongside `result`.

```python
return {
    "result": ...,            # existing formatted string
    "signal": _sig,
    "configured": True,
    "trend": _status.get("trend"),   # add this
}
```

The system prompt guidance then references `trend` in the tool result JSON the LLM sees.

---

## Section 4 — Frontend: `frontend/admin/budget-policy.html`

### What changes

Two new columns in the budget amounts table: **月均 / 趋势** and **预计超标**.

#### Table header addition

```html
<th>月均 / 趋势</th>
<th>预计超标</th>
```

#### `loadPolicies()` — fetch trend in parallel

After loading policy rows, fetch `/api/budget/status/{cost_center}` for each configured cost center using `Promise.all`. Store results in a `trendMap` keyed by cost_center.

```javascript
async function loadTrends(costCenters) {
  const results = await Promise.all(
    costCenters.map(cc =>
      fetch(`/api/budget/status/${encodeURIComponent(cc)}`, { credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .catch(() => null)
    )
  );
  const map = {};
  costCenters.forEach((cc, i) => { map[cc] = results[i]; });
  return map;
}
```

#### Row rendering — sparkline cell

```javascript
function renderSparkline(trend) {
  if (!trend || !trend.months || trend.months.length < 2) return '<td>—</td>';
  const pts = trend.months;
  const max = Math.max(...pts, 1);
  const w = 60, h = 20;
  const coords = pts.map((v, i) => {
    const x = (i / (pts.length - 1)) * w;
    const y = h - (v / max) * (h - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const color = trend.overrun_risk === 'high' ? '#ef4444'
              : trend.overrun_risk === 'moderate' ? '#f59e0b'
              : '#22c55e';
  const [lx, ly] = coords.split(' ').at(-1).split(',');
  return `<td>
    <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <polyline points="${coords}" fill="none" stroke="${color}"
        stroke-width="1.5" stroke-linejoin="round"/>
      <circle cx="${lx}" cy="${ly}" r="2" fill="${color}"/>
    </svg>
    <span style="font-size:.72rem;color:#6b7280;margin-left:.3rem">
      ¥${(trend.monthly_avg/1000).toFixed(1)}k/月
    </span>
  </td>`;
}
```

#### Row rendering — overrun badge cell

```javascript
function renderOverrunBadge(trend) {
  if (!trend) return '<td></td>';
  const risk = trend.overrun_risk;
  const dt = trend.estimated_overrun_date;
  if (risk === 'high' && dt) {
    const label = dt.slice(5).replace('-', '月') + '日';
    return `<td><span style="background:#fee2e2;color:#991b1b;font-size:.7rem;
      padding:.15rem .4rem;border-radius:4px;font-weight:600">⚠ ${label}</span></td>`;
  }
  if (risk === 'moderate' && dt) {
    const label = dt.slice(5, 7) + '月中';
    return `<td><span style="background:#fef3c7;color:#92400e;font-size:.7rem;
      padding:.15rem .4rem;border-radius:4px;font-weight:600">~ ${label}</span></td>`;
  }
  return `<td style="font-size:.72rem;color:#9ca3af">季度内安全</td>`;
}
```

#### Null safety

If `trendMap[cc]` is null (fetch failed or budget not configured), both cells render `<td>—</td>` / `<td></td>` without error.

---

## Section 5 — Tests

### `backend/tests/test_budget.py` — trend field

1. Seed 3 months of approved submissions for `ENG-TRAVEL`, call `get_budget_status()`, assert `trend.monthly_avg` ≈ seeded average, `trend.months` has 3 entries, `overrun_risk` matches expectation.
2. Seed zero past-month submissions → `trend.monthly_avg == 0`, `overrun_risk == "ok"`.
3. `configured: False` budget → `trend` key absent from response.

### `backend/tests/test_budget_api.py` — snapshot/me trend narrative

1. Seed employee + high-risk budget status → GET `/budget/snapshot/me` → `message` contains "月均" and a date string.
2. `overrun_risk == "ok"` → `message` does not contain "月均".

### No new eval cases

The `employee_qa` system prompt change is guidance to the LLM; the mock LLM in tests does not read system prompts, so no eval case changes needed.

---

## Out of Scope

- Trip-context layer (required to reduce false positives for legitimate seasonal spikes)
- Per-category trend breakdown
- Historical quarter comparison
- Push notifications / email alerts
- Trend for `_default` global policy row (no cost_center → skip trend)
