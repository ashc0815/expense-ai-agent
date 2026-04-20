# Budget Spend Trend & Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rolling 3-month spend trend and overrun forecast to budget status, surfaced in the employee AI chat (snapshot/me + QA agent) and the finance admin budget table.

**Architecture:** Extend `store.get_budget_status()` to compute trend from past 3 complete calendar months and append it to the return dict. `budget.py` and `chat.py` routes read the trend from the same dict with no extra DB calls. The admin frontend uses the already-fetched status data (which now includes trend) to render sparklines inline — no new API calls.

**Tech Stack:** Python / SQLAlchemy async (aiosqlite), FastAPI, Vanilla JS + inline SVG

---

## File Map

| File | Change |
|------|--------|
| `backend/db/store.py` | Add `_rolling_months()` helper; add `timedelta` to import; add trend block in `get_budget_status()` |
| `backend/api/routes/budget.py` | Append trend narrative in `get_my_budget_snapshot()` when `overrun_risk == "high"` |
| `backend/api/routes/chat.py` | Add `"trend"` key to `tool_get_budget_summary()` return; update `employee_qa` system prompt |
| `frontend/admin/budget-policy.html` | Add `renderSparkline()` + `renderOverrunBadge()`; add 2 `<th>` and 2 `<td>` per row in `render()` |
| `backend/tests/test_budget.py` | Add 6 new test functions covering trend field, snapshot narrative, and tool return |

---

## Task 1: `store.py` — Rolling Trend Computation

**Files:**
- Modify: `backend/db/store.py:13` (import)
- Modify: `backend/db/store.py:205` (add helper after `_period_date_range`)
- Modify: `backend/db/store.py:784` (add trend block after signal computation)
- Test: `backend/tests/test_budget.py`

- [ ] **Step 1: Write 3 failing tests**

Append to `backend/tests/test_budget.py`:

```python
# ── Trend field tests ──────────────────────────────────────────────────────────

def test_budget_status_trend_high_risk():
    """When past-month avg is high relative to remaining budget → overrun_risk=high, monthly_avg correct."""
    import calendar as _cal
    from datetime import date as _date

    cc = "CC-TREND"
    asyncio.get_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))

    # Seed Q2 spend (April 2026): 87% = 8700 used, 1300 remaining
    async def _seed_q2():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "trend-q2-spend")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="trend-q2-spend", employee_id="emp-trend", status="reviewed",
                    amount=Decimal("8700"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="TrendTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()
    asyncio.get_event_loop().run_until_complete(_seed_q2())

    # Seed past 3 complete months (Jan/Feb/Mar 2026 relative to today ≥ 2026-04-01)
    # 1800 + 2200 + 2525 = 6525, avg = 2175
    past_submissions = [
        ("trend-m1", "2026-01-15", Decimal("1800")),
        ("trend-m2", "2026-02-15", Decimal("2200")),
        ("trend-m3", "2026-03-15", Decimal("2525")),
    ]
    async def _seed_past():
        from backend.db.store import Submission
        async with _TestSession() as db:
            for sid, dt, amt in past_submissions:
                existing = await db.execute(
                    __import__('sqlalchemy', fromlist=['select']).select(Submission)
                    .where(Submission.id == sid)
                )
                if existing.scalar_one_or_none() is None:
                    db.add(Submission(
                        id=sid, employee_id="emp-trend", status="reviewed",
                        amount=amt, currency="CNY", category="travel",
                        date=dt, merchant="TrendPast", receipt_url="http://x.com/r.png",
                        cost_center=cc,
                    ))
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_seed_past())

    r = client.get(f"/api/budget/status/{cc}?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "trend" in body
    trend = body["trend"]
    assert abs(trend["monthly_avg"] - 2175.0) < 1.0        # avg of 1800+2200+2525
    assert trend["overrun_risk"] == "high"                  # 1300 remaining / 2175 avg ≈ 0.6 months
    assert trend["estimated_overrun_date"] is not None
    assert len(trend["months"]) == 3                        # oldest → newest


def test_budget_status_trend_zero_history():
    """No past-month submissions → monthly_avg=0, overrun_risk=ok, no overrun date."""
    cc = "CC-TREND-ZERO"
    asyncio.get_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))

    r = client.get(f"/api/budget/status/{cc}?period=2026-Q2", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "trend" in body
    trend = body["trend"]
    assert trend["monthly_avg"] == 0.0
    assert trend["overrun_risk"] == "ok"
    assert trend["estimated_overrun_date"] is None


def test_budget_status_no_budget_has_no_trend():
    """Unconfigured cost center → configured=False, no trend key."""
    r = client.get("/api/budget/status/CC-NO-BUDGET-EVER", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert "trend" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_budget_status_trend_high_risk \
  backend/tests/test_budget.py::test_budget_status_trend_zero_history \
  backend/tests/test_budget.py::test_budget_status_no_budget_has_no_trend -v
```

Expected: FAIL — `assert "trend" in body` fails (key not yet in response)

- [ ] **Step 3: Add `timedelta` to import in `store.py`**

In `backend/db/store.py` line 13, change:

```python
from datetime import date, datetime, timezone
```

to:

```python
from datetime import date, datetime, timedelta, timezone
```

- [ ] **Step 4: Add `_rolling_months()` helper in `store.py`**

In `backend/db/store.py`, insert after the `_period_date_range()` function (after line ~219, before the `# ── 初始化` comment):

```python
def _rolling_months(n: int) -> list[tuple[str, str]]:
    """Return (start_date, end_date) ISO string pairs for the last n complete calendar months.

    Returned newest-first: index 0 is last month, index n-1 is n months ago.
    Example on 2026-04-14 with n=3:
      [('2026-03-01', '2026-03-31'), ('2026-02-01', '2026-02-28'), ('2026-01-01', '2026-01-31')]
    """
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

- [ ] **Step 5: Add trend computation in `get_budget_status()`**

In `backend/db/store.py`, at the end of `get_budget_status()`, after the line `if projected_pct is not None: out["projected_pct"] = projected_pct` (around line 798), add the trend block before the final `return out`:

```python
    # ── rolling 3-month trend ──────────────────────────────────────────────
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
        estimated_overrun_date: Optional[str] = overrun_date.isoformat()
    elif remaining <= 0:
        months_until_exhaust = 0.0
        estimated_overrun_date = date.today().isoformat()
    else:
        months_until_exhaust = None
        estimated_overrun_date = None

    if months_until_exhaust is not None and months_until_exhaust < 1.0:
        overrun_risk = "high"
    elif months_until_exhaust is not None and months_until_exhaust < 2.0:
        overrun_risk = "moderate"
    else:
        overrun_risk = "ok"

    out["trend"] = {
        "monthly_avg": round(monthly_avg, 2),
        "months": list(reversed(month_totals)),  # oldest → newest for sparkline
        "overrun_risk": overrun_risk,
        "estimated_overrun_date": estimated_overrun_date,
    }
    return out
```

Remove the existing `return out` line that was at the end of the function (it was the last line before this addition).

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_budget_status_trend_high_risk \
  backend/tests/test_budget.py::test_budget_status_trend_zero_history \
  backend/tests/test_budget.py::test_budget_status_no_budget_has_no_trend -v
```

Expected: 3 PASSED

- [ ] **Step 7: Run full budget test suite to check regressions**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py -v
```

Expected: All existing tests still pass (trend is additive — no existing assertions broken)

- [ ] **Step 8: Commit**

```bash
cd /Users/ashleychen/expense-ai-agent
git add backend/db/store.py backend/tests/test_budget.py
git commit -m "feat: add rolling 3-month spend trend to get_budget_status"
```

---

## Task 2: `budget.py` — Trend Narrative in `snapshot/me`

**Files:**
- Modify: `backend/api/routes/budget.py:66` (append trend sentence in `get_my_budget_snapshot`)
- Test: `backend/tests/test_budget.py`

- [ ] **Step 1: Write 2 failing tests**

Append to `backend/tests/test_budget.py`:

```python
# ── snapshot/me trend narrative tests ─────────────────────────────────────────

def test_snapshot_me_appends_trend_narrative_when_high_risk():
    """snapshot/me: when signal=info/blocked and overrun_risk=high → message contains 月均."""
    cc = "CC-SNAP-HIGH"
    emp_id = "emp-snap-high"
    asyncio.get_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.get_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    # Seed Q2 spend at 80% (info signal)
    async def _seed_q2_snap():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "snap-q2-high")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="snap-q2-high", employee_id=emp_id, status="reviewed",
                    amount=Decimal("8000"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="SnapTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()

    # Seed past months: avg 3000/month → 2000 remaining / 3000 avg = 0.67 months → high
    async def _seed_past_snap():
        from backend.db.store import Submission
        async with _TestSession() as db:
            for sid, dt, amt in [
                ("snap-m1", "2026-01-15", Decimal("3000")),
                ("snap-m2", "2026-02-15", Decimal("3000")),
                ("snap-m3", "2026-03-15", Decimal("3000")),
            ]:
                existing = await db.execute(
                    __import__('sqlalchemy', fromlist=['select']).select(Submission)
                    .where(Submission.id == sid)
                )
                if existing.scalar_one_or_none() is None:
                    db.add(Submission(
                        id=sid, employee_id=emp_id, status="reviewed",
                        amount=amt, currency="CNY", category="travel",
                        date=dt, merchant="SnapPast", receipt_url="http://x.com/r.png",
                        cost_center=cc,
                    ))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed_q2_snap())
    asyncio.get_event_loop().run_until_complete(_seed_past_snap())

    r = client.get(
        "/api/budget/snapshot/me",
        headers={"X-User-Id": emp_id, "X-User-Role": "employee"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["message"] is not None
    assert "月均" in body["message"]


def test_snapshot_me_no_trend_when_ok_risk():
    """snapshot/me: when overrun_risk=ok (low avg), message does not mention 月均."""
    cc = "CC-SNAP-OK"
    emp_id = "emp-snap-ok"
    asyncio.get_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.get_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    # Seed Q2 spend at 80% (info signal — so we get a message)
    async def _seed_q2_ok():
        from backend.db.store import Submission
        async with _TestSession() as db:
            existing = await db.execute(
                __import__('sqlalchemy', fromlist=['select']).select(Submission)
                .where(Submission.id == "snap-q2-ok")
            )
            if existing.scalar_one_or_none() is None:
                db.add(Submission(
                    id="snap-q2-ok", employee_id=emp_id, status="reviewed",
                    amount=Decimal("8000"), currency="CNY", category="travel",
                    date="2026-04-10", merchant="OkTest", receipt_url="http://x.com/r.png",
                    cost_center=cc,
                ))
                await db.commit()

    # No past-month submissions → monthly_avg = 0 → overrun_risk = ok
    asyncio.get_event_loop().run_until_complete(_seed_q2_ok())

    r = client.get(
        "/api/budget/snapshot/me",
        headers={"X-User-Id": emp_id, "X-User-Role": "employee"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["message"] is not None       # signal=info → message exists
    assert "月均" not in body["message"]     # but no trend narrative
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_snapshot_me_appends_trend_narrative_when_high_risk \
  backend/tests/test_budget.py::test_snapshot_me_no_trend_when_ok_risk -v
```

Expected: FAIL — `assert "月均" in body["message"]` fails

- [ ] **Step 3: Add trend narrative in `budget.py`**

In `backend/api/routes/budget.py`, inside `get_my_budget_snapshot()`, after the `else:  # info` block that builds `msg` (currently ending around line 78), add the trend block **before** the `return` statement:

```python
    # ── trend narrative (append when overrun_risk is high) ────────────────
    trend = status.get("trend")
    if trend and trend.get("overrun_risk") == "high" and trend.get("estimated_overrun_date"):
        avg = trend["monthly_avg"]
        overrun_date_str = trend["estimated_overrun_date"]
        msg += (
            f" 按近 3 个月月均 ¥{avg:,.0f} 的消费节奏，"
            f"预计 {overrun_date_str} 前后预算耗尽。"
        )

    return {"message": msg, "signal": sig, "usage_pct": status["usage_pct"]}
```

Remove the existing `return` line (currently the last line of the function) — the new code above includes the return.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_snapshot_me_appends_trend_narrative_when_high_risk \
  backend/tests/test_budget.py::test_snapshot_me_no_trend_when_ok_risk -v
```

Expected: 2 PASSED

- [ ] **Step 5: Run full budget test suite**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py -v
```

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
cd /Users/ashleychen/expense-ai-agent
git add backend/api/routes/budget.py backend/tests/test_budget.py
git commit -m "feat: append trend narrative to snapshot/me when overrun_risk=high"
```

---

## Task 3: `chat.py` — Tool Return + System Prompt

**Files:**
- Modify: `backend/api/routes/chat.py:651` (`tool_get_budget_summary` return dict)
- Modify: `backend/api/routes/chat.py:1017` (`_SYSTEM_PROMPTS["employee_qa"]`)
- Test: `backend/tests/test_budget.py`

- [ ] **Step 1: Write 1 failing test**

Append to `backend/tests/test_budget.py`:

```python
# ── chat tool trend key test ───────────────────────────────────────────────────

def test_tool_get_budget_summary_includes_trend():
    """tool_get_budget_summary must return a 'trend' key so the LLM can read overrun_risk."""
    cc = "CC-CHAT-TREND"
    emp_id = "emp-chat-trend"
    asyncio.get_event_loop().run_until_complete(_seed_budget(cc, "2026-Q2", 10000.0))
    asyncio.get_event_loop().run_until_complete(_seed_employee_with_cc(emp_id, cc))

    async def _call_tool():
        from backend.api.routes.chat import tool_get_budget_summary
        from backend.api.middleware.auth import UserContext
        async with _TestSession() as db:
            ctx = UserContext(user_id=emp_id, role="employee")
            return await tool_get_budget_summary({}, ctx, db, "")

    result = asyncio.get_event_loop().run_until_complete(_call_tool())
    assert "trend" in result
    assert result["trend"] is not None or result.get("configured") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_tool_get_budget_summary_includes_trend -v
```

Expected: FAIL — `assert "trend" in result`

- [ ] **Step 3: Add `trend` key to `tool_get_budget_summary()` return**

In `backend/api/routes/chat.py`, inside `tool_get_budget_summary()`, find the `return` dict (around line 651–661) and add `"trend": _status.get("trend")`:

The current return is:
```python
        return {
            "result": (
                f"你所在成本中心 {_emp.cost_center} 本季度预算状态："
                f"已用 {_pct:.1f}%（¥{_status['spent_amount']:,.0f} / ¥{_status['total_amount']:,.0f}），"
                f"剩余 ¥{_remaining:,.0f}。状态：{_sig}。"
            ),
            "signal": _sig,
            "configured": True,
        }
```

Change to:
```python
        return {
            "result": (
                f"你所在成本中心 {_emp.cost_center} 本季度预算状态："
                f"已用 {_pct:.1f}%（¥{_status['spent_amount']:,.0f} / ¥{_status['total_amount']:,.0f}），"
                f"剩余 ¥{_remaining:,.0f}。状态：{_sig}。"
            ),
            "signal": _sig,
            "configured": True,
            "trend": _status.get("trend"),
        }
```

- [ ] **Step 4: Update `employee_qa` system prompt**

In `backend/api/routes/chat.py`, find `_SYSTEM_PROMPTS["employee_qa"]` (around line 1014). The current value is:

```python
    "employee_qa": (
        "你是企业报销查询助手，帮助员工查询自己的报销记录。"
        "你只能读取数据，不能修改任何内容。请用中文回复，信息简洁清晰。"
        "\n\n页面加载规则：收到 trigger=page_load 且 page=my-reports 时，立即调用 get_budget_summary。如果 signal 为 'info'、'blocked' 或 'over_budget'，在等待用户输入之前主动发送一条预算状态提示。如果 signal 为 'ok'，保持静默——一切正常时不要打扰用户。"
    ),
```

Change to:

```python
    "employee_qa": (
        "你是企业报销查询助手，帮助员工查询自己的报销记录。"
        "你只能读取数据，不能修改任何内容。请用中文回复，信息简洁清晰。"
        "\n\n页面加载规则：收到 trigger=page_load 且 page=my-reports 时，立即调用 get_budget_summary。如果 signal 为 'info'、'blocked' 或 'over_budget'，在等待用户输入之前主动发送一条预算状态提示。如果 signal 为 'ok'，保持静默——一切正常时不要打扰用户。"
        "\n\n趋势提示规则：当 get_budget_summary 返回的 trend.overrun_risk 为 'high' 时，在预算状态提示后用自然语气补充一句趋势预测（例如：按近 3 个月的节奏，预计 X 日前后预算耗尽）。如果 trend 为 null 或 overrun_risk 为 'ok'/'moderate'，不提趋势。"
    ),
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py::test_tool_get_budget_summary_includes_trend -v
```

Expected: PASSED

- [ ] **Step 6: Run full test suite**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/test_budget.py backend/tests/test_chat_qa.py -v
```

Expected: All tests pass (system prompt change is not tested by MockLLM — no regressions)

- [ ] **Step 7: Commit**

```bash
cd /Users/ashleychen/expense-ai-agent
git add backend/api/routes/chat.py backend/tests/test_budget.py
git commit -m "feat: expose trend in tool_get_budget_summary and update employee_qa prompt"
```

---

## Task 4: `budget-policy.html` — Admin Sparkline Columns

**Files:**
- Modify: `frontend/admin/budget-policy.html`

Note: No automated tests for frontend. Verify manually by running the dev server.

The existing `load()` function already fetches `statuses` via `api.getBudgetStatus()` in a `Promise.all`. After Task 1, these status objects will already contain a `trend` field. No additional fetches needed — just use `st.trend` in `render()`.

- [ ] **Step 1: Add `renderSparkline()` and `renderOverrunBadge()` helper functions**

In `budget-policy.html`, add these two functions inside the `<script>` block, before the `load()` function (before line ~98):

```javascript
  function renderSparkline(trend) {
    if (!trend || !trend.months || trend.months.length < 2) {
      return '<td style="padding:.5rem .6rem">—</td>';
    }
    var pts = trend.months;
    var max = Math.max.apply(null, pts.concat([1]));
    var w = 60, h = 20;
    var coords = pts.map(function(v, i) {
      var x = (i / (pts.length - 1)) * w;
      var y = h - (v / max) * (h - 2) - 1;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    var color = trend.overrun_risk === 'high' ? '#ef4444'
              : trend.overrun_risk === 'moderate' ? '#f59e0b'
              : '#22c55e';
    var lastPair = coords.split(' ').slice(-1)[0].split(',');
    var lx = lastPair[0], ly = lastPair[1];
    var avgK = (trend.monthly_avg / 1000).toFixed(1);
    return '<td style="padding:.5rem .6rem;white-space:nowrap">' +
      '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" style="vertical-align:middle">' +
        '<polyline points="' + coords + '" fill="none" stroke="' + color + '" stroke-width="1.5" stroke-linejoin="round"/>' +
        '<circle cx="' + lx + '" cy="' + ly + '" r="2" fill="' + color + '"/>' +
      '</svg>' +
      '<span style="font-size:.72rem;color:#6b7280;margin-left:.3rem">¥' + avgK + 'k/月</span>' +
    '</td>';
  }

  function renderOverrunBadge(trend) {
    if (!trend) return '<td style="padding:.5rem .6rem"></td>';
    var risk = trend.overrun_risk;
    var dt = trend.estimated_overrun_date;
    if (risk === 'high' && dt) {
      var label = dt.slice(5, 7) + '月' + dt.slice(8, 10) + '日';
      return '<td style="padding:.5rem .6rem">' +
        '<span style="background:#fee2e2;color:#991b1b;font-size:.7rem;padding:.15rem .4rem;border-radius:4px;font-weight:600">⚠ ' + label + '</span>' +
      '</td>';
    }
    if (risk === 'moderate' && dt) {
      var mLabel = dt.slice(5, 7) + '月中';
      return '<td style="padding:.5rem .6rem">' +
        '<span style="background:#fef3c7;color:#92400e;font-size:.7rem;padding:.15rem .4rem;border-radius:4px;font-weight:600">~ ' + mLabel + '</span>' +
      '</td>';
    }
    return '<td style="padding:.5rem .6rem;font-size:.72rem;color:#9ca3af">季度内安全</td>';
  }
```

- [ ] **Step 2: Add 2 new `<th>` headers in `render()`**

In `frontend/admin/budget-policy.html`, inside `render()`, find the `<thead>` row (around line 178–186). The current headers end with:

```javascript
            '<th style="padding:.5rem .6rem">' + t('budget.col-used') + '</th>' +
            '<th style="padding:.5rem .6rem">' + t('budget.col-warn') + '</th>' +
```

Change to insert 2 new headers after `col-used`:

```javascript
            '<th style="padding:.5rem .6rem">' + t('budget.col-used') + '</th>' +
            '<th style="padding:.5rem .6rem">月均 / 趋势</th>' +
            '<th style="padding:.5rem .6rem">预计超标</th>' +
            '<th style="padding:.5rem .6rem">' + t('budget.col-warn') + '</th>' +
```

- [ ] **Step 3: Add 2 new `<td>` cells in each data row**

In `render()`, inside `amounts.map(...)`, find the row string that currently has:

```javascript
        '</td>' +
        '<td style="padding:.5rem .6rem;font-size:.82rem">' + infoPct + '</td>' +
```

(This is the closing `</td>` of the "已用" progress bar cell, followed by the first info threshold cell.)

Change to insert sparkline + overrun cells between them:

```javascript
        '</td>' +
        renderSparkline(st.trend) +
        renderOverrunBadge(st.trend) +
        '<td style="padding:.5rem .6rem;font-size:.82rem">' + infoPct + '</td>' +
```

- [ ] **Step 4: Add 2 empty `<td>` cells to the global default row**

In `render()`, find the global default row (the `rows.push(...)` block). It currently has `<td>` cells matching the header count. The default row has no trend data. Find the `<td>—</td>` cell after the "已用" column (currently the 3rd `<td>` in the default row, showing `—` for used %):

Current default row (around line 163–172):
```javascript
      '<td style="padding:.5rem .6rem;font-size:.85rem">' + t('budget.global-default') + '</td>' +
      '<td style="padding:.5rem .6rem">—</td>' +
      '<td style="padding:.5rem .6rem">—</td>' +
      '<td style="padding:.5rem .6rem;font-size:.82rem">' + defInfoPct + '</td>' +
```

The third `<td>` (showing `—`) is the "已用" column. Insert 2 empty cells after it:

```javascript
      '<td style="padding:.5rem .6rem;font-size:.85rem">' + t('budget.global-default') + '</td>' +
      '<td style="padding:.5rem .6rem">—</td>' +
      '<td style="padding:.5rem .6rem">—</td>' +
      '<td style="padding:.5rem .6rem"></td>' +
      '<td style="padding:.5rem .6rem"></td>' +
      '<td style="padding:.5rem .6rem;font-size:.82rem">' + defInfoPct + '</td>' +
```

- [ ] **Step 5: Verify manually**

Start the dev server and open the admin budget page:

```bash
cd /Users/ashleychen/expense-ai-agent
python -m uvicorn backend.main:app --reload --port 8000
```

Open `http://localhost:8000/admin/budget-policy.html` (or the frontend dev server URL) and verify:
- Budget rows show a sparkline SVG in the new "月均 / 趋势" column
- The "预计超标" column shows a colored badge for high/moderate risk, "季度内安全" for ok
- The global default row has empty cells in the two new columns
- No JS errors in browser console

- [ ] **Step 6: Commit**

```bash
cd /Users/ashleychen/expense-ai-agent
git add frontend/admin/budget-policy.html
git commit -m "feat: add sparkline and overrun forecast columns to admin budget table"
```

---

## Final Verification

- [ ] **Run full backend test suite**

```bash
cd /Users/ashleychen/expense-ai-agent
python -m pytest backend/tests/ -v --ignore=backend/tests/test_agent_eval.py 2>&1 | tail -20
```

Expected: All tests pass (3 pre-existing failures in OCR/auth tests are pre-existing and unrelated to this feature — confirm they were failing before your changes with `git stash && pytest && git stash pop`)
