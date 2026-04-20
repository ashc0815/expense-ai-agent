# Simplify Employee Submission Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant submission paths, make Quick Flow the sole entry, add return-for-revision workflow, replace AI chat with static FAQ.

**Architecture:** Frontend-first changes (quick.html cleanup, nav redirect, submit.html redirect), then backend additions (needs_revision status, return/resubmit endpoints), then frontend wiring for the new status.

**Tech Stack:** FastAPI, SQLAlchemy, vanilla HTML/JS (no framework)

---

### Task 1: Remove AI Chat from quick.html

**Files:**
- Modify: `frontend/employee/quick.html`

- [ ] **Step 1: Delete AI chat HTML elements (lines 166-181)**

Remove the floating button and entire chat drawer:

```html
<!-- DELETE everything from line 166 to line 181 -->
<!-- AI Assistant (uses shared chat-drawer styles from styles.css) -->
<button class="chat-toggle-btn pulse" id="ai-fab" onclick="toggleAI()">💬</button>

<aside class="chat-drawer" id="ai-drawer">
  <div class="chat-header">
    <div class="chat-title">AI 报销助手
      <span class="ai-needs-badge" id="ai-needs-badge" style="display:none">需要协助</span>
    </div>
    <button class="btn btn-ghost btn-sm" onclick="toggleAI()">✕</button>
  </div>
  <div class="chat-messages" id="ai-messages"></div>
  <div class="chat-input-wrap">
    <input id="ai-input" placeholder="问 AI 助手..." onkeydown="if(event.key==='Enter')sendAI()">
    <button id="ai-send" onclick="sendAI()">➤</button>
  </div>
</aside>
```

- [ ] **Step 2: Delete the submit.html link (lines 161-163)**

Remove:
```html
  <p style="margin-top:2rem;text-align:center;font-size:.8rem">
    <a href="/employee/submit.html" class="muted">或者直接手动填表 →</a>
  </p>
```

- [ ] **Step 3: Delete chat-related CSS (lines 120-130)**

Remove these CSS rules from the `<style>` block:
```css
    /* system message style (extends shared chat styles) */
    .chat-msg.system {
      background: var(--brand-light, #e6f7f3); color: var(--brand-dark, #129e7b);
      font-size: .78rem; border: 1px solid #bbf7d0;
      align-self: flex-start; padding: .5rem .75rem;
      border-radius: 8px; max-width: 85%;
    }
    .ai-needs-badge {
      background: #f59e0b; color: white; font-size: .65rem; font-weight: 700;
      padding: .15rem .4rem; border-radius: 10px; margin-left: .4rem;
    }
```

- [ ] **Step 4: Delete all AI chat JavaScript functions (lines 640-819)**

Delete everything from `// ── AI Assistant` to the end of the script, which includes:
- `currentDraftIdForAI`, `aiStreaming` variables
- `toggleAI()`, `appendAIMsg()`, `showAIAssistant()`, `autoFillMissing()`, `sendAI()`, `runAIChat()`, `syncFieldsToForm()`, `syncSingleField()`, `checkMissingFields()`

- [ ] **Step 5: Remove `showAIAssistant(draftId)` call from `showForm()`**

In the `showForm()` function (around line 532), delete the last line:
```javascript
  showAIAssistant(draftId);
```

- [ ] **Step 6: Remove AI drawer reference from `showSuccess()` and `resetAndUploadNew()`**

In `showSuccess()` (around line 605), delete:
```javascript
  document.getElementById("ai-drawer").classList.remove("open");
```

In `resetAndUploadNew()` (around line 626), delete:
```javascript
  document.getElementById("ai-drawer").classList.remove("open");
```

- [ ] **Step 7: Verify quick.html loads without errors**

Run: Open `http://localhost:8000/employee/quick.html` in a browser and check the console for any JavaScript errors about missing elements (ai-drawer, ai-fab, etc.).

- [ ] **Step 8: Commit**

```bash
git add frontend/employee/quick.html
git commit -m "refactor: remove AI chat drawer from quick.html"
```

---

### Task 2: Add inline layer banners and FAQ to quick.html

**Files:**
- Modify: `frontend/employee/quick.html`

- [ ] **Step 1: Add banner CSS to the `<style>` block**

Add after the `.alert-dup` CSS rule (around line 118):

```css
    .layer-banner {
      padding: .6rem .8rem; border-radius: 8px; font-size: .85rem;
      margin-bottom: .8rem; display: flex; align-items: center; gap: .5rem;
    }
    .layer-banner.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
    .layer-banner.warning { background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }
    .layer-banner.orange  { background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa; }
    .layer-banner.error   { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
    .error-card {
      background: white; border: 1px solid #fecaca; border-radius: 12px;
      padding: 2rem; margin-top: 1rem; text-align: center;
    }
    .error-card .error-icon { font-size: 2.5rem; margin-bottom: .5rem; }
    .error-card .error-title { font-weight: 700; color: #991b1b; font-size: 1rem; margin-bottom: .3rem; }
    .error-card .error-sub { color: #b91c1c; font-size: .85rem; margin-bottom: 1rem; }
    .faq-section { margin-top: 1.5rem; }
    .faq-item { border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: .4rem; overflow: hidden; }
    .faq-q {
      padding: .6rem .8rem; font-size: .85rem; font-weight: 600; color: #0f172a;
      cursor: pointer; display: flex; justify-content: space-between; align-items: center;
      background: #f8fafc;
    }
    .faq-q:hover { background: #f1f5f9; }
    .faq-a { padding: 0 .8rem; max-height: 0; overflow: hidden; transition: all .2s; font-size: .82rem; color: #475569; }
    .faq-item.open .faq-a { max-height: 200px; padding: .5rem .8rem; }
    .faq-item.open .faq-arrow { transform: rotate(180deg); }
    .faq-arrow { transition: transform .2s; font-size: .7rem; color: #94a3b8; }
```

- [ ] **Step 2: Modify `showForm()` to handle layer 3_hard separately**

Replace the beginning of `showForm()` with layer-aware logic. Change the function to:

```javascript
function showForm(draftId) {
  document.getElementById("scan-section").style.display = "none";
  document.getElementById("upload-section").style.display = "none";
  document.getElementById("success-section").style.display = "none";

  const d = draftData[draftId];
  const layer = d.layer || "2";
  const el = document.getElementById("form-section");
  el.style.display = "block";

  // Layer 3 hard: show error card instead of form
  if (layer === "3_hard") {
    el.innerHTML = `
      <div class="error-card">
        <div class="error-icon">❌</div>
        <div class="error-title">无法识别此图片</div>
        <div class="error-sub">可能原因：图片模糊、角度偏斜、或不是发票/收据</div>
        <div class="form-actions" style="justify-content:center">
          <button class="btn-secondary" onclick="resetAndUploadNew()">重新拍照</button>
          <button class="btn-save" onclick="forceManualFill('${draftId}')">手动填写</button>
        </div>
      </div>`;
    return;
  }

  const f = d.fields;
  const s = d.sources;
```

Keep the rest of `showForm()` unchanged (from `function fieldClass(key)` onward), BUT insert a layer banner right after the `<h3>` inside the form card HTML. Replace the current `el.innerHTML` assignment to add the banner:

Find the line:
```javascript
      <h3>确认发票信息 ${d.isDuplicate ? '<span style="color:#dc2626;font-size:.8rem">⚠ 可能重复</span>' : ''}</h3>
```

Add the banner right after it:
```javascript
      ${layer === "1"
        ? '<div class="layer-banner success">✅ 所有字段已自动识别，请确认后保存</div>'
        : layer === "2"
        ? '<div class="layer-banner warning">⚠️ 部分字段需要手动填写（标红处）</div>'
        : '<div class="layer-banner orange">📝 识别效果不佳，请手动填写关键信息</div>'
      }
```

- [ ] **Step 3: Add `forceManualFill()` function**

Add this function after `resetAndUploadNew()`:

```javascript
function forceManualFill(draftId) {
  draftData[draftId].layer = "3_soft";
  draftData[draftId].fields = draftData[draftId].fields || {};
  showForm(draftId);
}
```

- [ ] **Step 4: Add FAQ accordion HTML**

Add this right before the closing `</main>` tag (where the submit.html link used to be):

```html
  <div class="faq-section" id="faq-section">
    <div style="font-size:.8rem;font-weight:600;color:#64748b;margin-bottom:.4rem">常见问题</div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q">这笔费用该选什么分类？<span class="faq-arrow">▼</span></div>
      <div class="faq-a">餐饮 = 工作餐、商务宴请；交通 = 打车、机票、火车票；住宿 = 酒店；招待 = 客户相关的餐饮或活动。如不确定，选"其他"并在备注中说明。</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q">金额超标了怎么办？<span class="faq-arrow">▼</span></div>
      <div class="faq-a">超出限额的报销会进入人工审核流程，由直属经理判断是否批准。合理的超支（如出差目的地物价较高）通常可以通过。</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q">为什么提示"可能重复"？<span class="faq-arrow">▼</span></div>
      <div class="faq-a">系统检测到相同发票号码已经存在。请确认这张发票是否已经在其他报销单中提交过，避免重复报销。</div>
    </div>
    <div class="faq-item" onclick="this.classList.toggle('open')">
      <div class="faq-q">预算不够了还能报销吗？<span class="faq-arrow">▼</span></div>
      <div class="faq-a">当部门预算接近上限时，系统会显示提示。超限的报销会被暂挂，需要财务管理员审批解锁后才能继续。</div>
    </div>
  </div>
```

- [ ] **Step 5: Verify all 4 layers display correctly**

Test in browser:
- Upload a good receipt → should see green banner (Layer 1)
- Upload a blurry image → should see error card (Layer 3 hard) with "重新拍照" and "手动填写" buttons
- Click "手动填写" → should transition to orange banner form (Layer 3 soft)
- FAQ accordion should expand/collapse on click

- [ ] **Step 6: Commit**

```bash
git add frontend/employee/quick.html
git commit -m "feat: add inline layer banners and FAQ panel to quick.html"
```

---

### Task 3: Make Quick Flow the only entry point

**Files:**
- Modify: `frontend/shared/nav.js:9`
- Modify: `frontend/employee/report.html:264`
- Modify: `frontend/employee/submit.html` (full rewrite)

- [ ] **Step 1: Update nav.js**

In `frontend/shared/nav.js`, line 9, change:
```javascript
    { key: "nav.submit",     href: "/employee/submit.html",     roles: ["employee"]      },
```
to:
```javascript
    { key: "nav.submit",     href: "/employee/quick.html",      roles: ["employee"]      },
```

- [ ] **Step 2: Update report.html "手动创建" link**

In `frontend/employee/report.html`, line 264, change:
```javascript
            <a href="/employee/submit.html?report_id=${REPORT_ID}">
              <div class="dd-label">✏️ 手动创建</div>
              <div class="dd-sub">手动填写费用类别和金额</div>
            </a>
```
to:
```javascript
            <a href="/employee/quick.html?report_id=${REPORT_ID}">
              <div class="dd-label">✏️ 手动创建</div>
              <div class="dd-sub">上传或手动填写费用信息</div>
            </a>
```

- [ ] **Step 3: Replace submit.html with redirect**

Replace the entire content of `frontend/employee/submit.html` with:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>跳转中…</title>
  <script>location.replace('/employee/quick.html' + location.search);</script>
</head>
<body>
  <p style="text-align:center;padding:2rem;color:#94a3b8">正在跳转到快速报销…</p>
</body>
</html>
```

- [ ] **Step 4: Verify navigation works**

Test in browser:
- Click nav "提交报销" → should go to quick.html
- Visit `/employee/submit.html` directly → should redirect to quick.html
- Visit `/employee/submit.html?report_id=xxx` → should redirect to `quick.html?report_id=xxx`
- In report detail, click "手动创建" → should go to quick.html

- [ ] **Step 5: Commit**

```bash
git add frontend/shared/nav.js frontend/employee/report.html frontend/employee/submit.html
git commit -m "refactor: make quick.html the only employee submission entry point"
```

---

### Task 4: Add `needs_revision` status to backend

**Files:**
- Modify: `backend/db/store.py:72-73` (status comment), `backend/db/store.py:230-234` (Report status comment), add `revision_reason` column

- [ ] **Step 1: Update Submission status comment**

In `backend/db/store.py`, around line 72-73, change:
```python
    status           = Column(String(50),  nullable=False, default="processing")
    # processing | reviewed | manager_approved | finance_approved | exported | rejected | review_failed
```
to:
```python
    status           = Column(String(50),  nullable=False, default="processing")
    # processing | reviewed | manager_approved | finance_approved | exported | rejected | review_failed | needs_revision
```

- [ ] **Step 2: Update Report model**

In `backend/db/store.py`, around lines 230-242, change the Report class status comment and add `revision_reason`:

```python
class Report(Base):
    """
    报销单 — 包含多个 Submission line。
    状态流转:
      open      — 草稿,可添加发票
      pending   — 已提交,等待经理审批
      approved  — 经理已批准
      rejected  — 经理已拒绝
      needs_revision — 经理退回修改
      withdrawn — 已撤回 (从 pending/approved 回到 open)
    """
    __tablename__ = "reports"

    id            = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id   = Column(String(64),  nullable=False, index=True)
    title         = Column(String(255), nullable=False, default="新建报销单")
    status        = Column(String(20),  nullable=False, default="open")
    revision_reason = Column(String(500), nullable=True)
    submitted_at  = Column(DateTime(timezone=True), nullable=True)
    withdrawn_at  = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True),
                            default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))
```

Note: `status` column width changed from `String(16)` to `String(20)` to fit `"needs_revision"`.

- [ ] **Step 3: Run database migration**

Since the project uses SQLite in dev with `create_all`, just delete the DB and re-create:

```bash
cd /Users/ashleychen/ExpenseFlow
rm -f concurshield.db
python -c "
import asyncio
from backend.db.store import engine, Base
async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
asyncio.run(init())
"
```

- [ ] **Step 4: Verify the schema has the new column**

```bash
cd /Users/ashleychen/ExpenseFlow
python -c "
import sqlite3
conn = sqlite3.connect('concurshield.db')
cursor = conn.execute('PRAGMA table_info(reports)')
for row in cursor:
    print(row)
conn.close()
"
```

Expected: should include a row for `revision_reason`.

- [ ] **Step 5: Commit**

```bash
git add backend/db/store.py
git commit -m "feat: add needs_revision status and revision_reason to Report model"
```

---

### Task 5: Add return-for-revision endpoint

**Files:**
- Modify: `backend/api/routes/reports.py`

- [ ] **Step 1: Add ReturnReportBody schema**

After the existing `ApproveReportBody` class (around line 415), add:

```python
class ReturnReportBody(BaseModel):
    reason: str
```

- [ ] **Step 2: Add the return endpoint**

After the `reject_report` endpoint (after line 501), add:

```python
@router.post("/{report_id}/return")
async def return_report_for_revision(
    report_id: str,
    body: ReturnReportBody,
    ctx: UserContext = Depends(require_role("manager", "finance_admin")),
    db: AsyncSession = Depends(get_db),
):
    report = await get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if report.employee_id == ctx.user_id:
        raise HTTPException(status_code=403, detail="不能退回自己的报销单")
    if report.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {report.status}，不可退回",
        )

    subs = await list_report_submissions(db, report_id)
    actionable = ("processing", "reviewed", "review_failed")
    now = datetime.now(timezone.utc)
    for s in subs:
        if s.status in actionable:
            s.status = "needs_revision"
            s.approver_id = ctx.user_id
            s.approver_comment = body.reason
            s.updated_at = now

    report.status = "needs_revision"
    report.revision_reason = body.reason
    report.updated_at = now
    await db.commit()

    emp = await get_employee(db, report.employee_id)
    await create_notification(
        db,
        recipient_id=report.employee_id,
        kind="report_returned",
        title=f"报销单「{report.title}」被退回修改",
        body=f"退回原因：{body.reason}",
        link=f"/employee/report.html?report_id={report_id}",
    )

    await create_audit_log(
        db, actor_id=ctx.user_id, action="report_returned",
        resource_type="report", resource_id=report_id,
        detail={"reason": body.reason, "line_count": len(subs)},
    )
    return await _report_payload(db, report)
```

- [ ] **Step 3: Add the resubmit endpoint**

After the return endpoint, add:

```python
@router.post("/{report_id}/resubmit")
async def resubmit_report(
    report_id: str,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    report = await get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报销单不存在")
    if report.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")
    if report.status != "needs_revision":
        raise HTTPException(
            status_code=409,
            detail=f"当前状态 {report.status}，不可重新提交",
        )

    subs = await list_report_submissions(db, report_id)
    lines = [s for s in subs if s.status == "needs_revision"]
    if not lines:
        raise HTTPException(status_code=422, detail="没有需要重新提交的发票")

    emp = await get_employee(db, ctx.user_id)

    now = datetime.now(timezone.utc)
    for s in lines:
        s.status = "processing"
        s.approver_id = None
        s.approver_comment = None
        s.approved_at = None
        s.audit_report = None
        s.risk_score = None
        s.tier = None
        s.updated_at = now

    report.status = "pending"
    report.revision_reason = None
    report.submitted_at = now
    report.updated_at = now
    await db.commit()

    from backend.api.routes.submissions import _run_pipeline
    for s in lines:
        background_tasks.add_task(_run_pipeline, s.id, {
            "employee_id":    ctx.user_id,
            "employee_name":  emp.name if emp else None,
            "department":     s.department,
            "city":           emp.city if emp else None,
            "level":          emp.level if emp else None,
            "amount":         float(s.amount),
            "currency":       s.currency,
            "category":       s.category,
            "date":           s.date,
            "merchant":       s.merchant,
            "tax_amount":     float(s.tax_amount) if s.tax_amount is not None else None,
            "description":    s.description,
            "invoice_number": s.invoice_number,
            "invoice_code":   s.invoice_code,
        })

    await create_audit_log(
        db, actor_id=ctx.user_id, action="report_resubmitted",
        resource_type="report", resource_id=report_id,
        detail={"line_count": len(lines)},
    )
    return {"report_id": report_id, "status": "pending", "lines": len(lines)}
```

- [ ] **Step 4: Update `_report_dict` to include `revision_reason`**

In the `_report_dict` helper (around line 36), add `revision_reason`:

```python
def _report_dict(r) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "status": r.status,
        "revision_reason": r.revision_reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "withdrawn_at": r.withdrawn_at.isoformat() if r.withdrawn_at else None,
    }
```

- [ ] **Step 5: Verify server starts**

```bash
cd /Users/ashleychen/ExpenseFlow
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
sleep 2
curl -s http://localhost:8000/docs | head -5
kill %1
```

Expected: FastAPI should start without import errors.

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/reports.py
git commit -m "feat: add return-for-revision and resubmit endpoints"
```

---

### Task 6: Add "退回" button to manager queue

**Files:**
- Modify: `frontend/manager/queue.html`

- [ ] **Step 1: Add the "退回" button to the detail header**

In `frontend/manager/queue.html`, in the `renderDetail()` function, find the `detail-actions` div (around line 202-212). Change it from:

```javascript
        <div class="detail-actions">
          <button class="btn btn-danger btn-sm" onclick="openModal('reject','${r.id}')"
            ${canApprove ? "" : "disabled"}>
            拒绝 <span class="kbd" style="border-color:rgba(255,255,255,.3);
              color:rgba(255,255,255,.7);background:rgba(0,0,0,.15)">R</span>
          </button>
          <button class="btn btn-success btn-sm" onclick="openModal('approve','${r.id}')"
            ${canApprove ? "" : "disabled"}>
            批准 <span class="kbd" style="border-color:rgba(255,255,255,.3);
              color:rgba(255,255,255,.7);background:rgba(0,0,0,.15)">A</span>
          </button>
        </div>
```

to:

```javascript
        <div class="detail-actions">
          <button class="btn btn-danger btn-sm" onclick="openModal('reject','${r.id}')"
            ${canApprove ? "" : "disabled"}>
            拒绝 <span class="kbd" style="border-color:rgba(255,255,255,.3);
              color:rgba(255,255,255,.7);background:rgba(0,0,0,.15)">R</span>
          </button>
          <button class="btn btn-sm" style="background:#f97316;color:white;border:none"
            onclick="openModal('return','${r.id}')"
            ${canApprove ? "" : "disabled"}>
            退回 <span class="kbd" style="border-color:rgba(255,255,255,.3);
              color:rgba(255,255,255,.7);background:rgba(0,0,0,.15)">T</span>
          </button>
          <button class="btn btn-success btn-sm" onclick="openModal('approve','${r.id}')"
            ${canApprove ? "" : "disabled"}>
            批准 <span class="kbd" style="border-color:rgba(255,255,255,.3);
              color:rgba(255,255,255,.7);background:rgba(0,0,0,.15)">A</span>
          </button>
        </div>
```

- [ ] **Step 2: Update the modal to handle "return" action**

In `openModal()` (around line 411-421), change:

```javascript
  window.openModal = function (action, id) {
    modalAction = action;
    modalReportId = id;
    const titles = { approve: "批准整张报销单", reject: "拒绝整张报销单", return: "退回修改" };
    const labels = { approve: "确认批准", reject: "确认拒绝", return: "确认退回" };
    const classes = { approve: "btn btn-success", reject: "btn btn-danger", return: "btn" };
    document.getElementById("modal-title").textContent = titles[action] || action;
    const btn = document.getElementById("modal-confirm");
    btn.textContent = labels[action] || action;
    btn.className = classes[action] || "btn";
    if (action === "return") btn.style.cssText = "background:#f97316;color:white;border:none";
    else btn.style.cssText = "";
    document.getElementById("modal-comment").value = "";
    document.getElementById("modal-comment").placeholder =
      action === "return" ? "请填写退回原因（必填）…" : "填写审批意见（可选）";
    document.getElementById("modal").classList.add("open");
  };
```

- [ ] **Step 3: Update `confirmModal()` to handle "return" action**

In `confirmModal()` (around line 425-455), change the endpoint selection:

```javascript
  window.confirmModal = async function () {
    const comment = document.getElementById("modal-comment").value;
    if (modalAction === "return" && !comment.trim()) {
      alert("退回时必须填写原因");
      return;
    }
    const btn = document.getElementById("modal-confirm");
    btn.disabled = true;
    try {
      let endpoint, bodyData;
      if (modalAction === "return") {
        endpoint = `/api/reports/${modalReportId}/return`;
        bodyData = { reason: comment };
      } else {
        endpoint = modalAction === "approve"
          ? `/api/reports/${modalReportId}/approve`
          : `/api/reports/${modalReportId}/reject`;
        bodyData = { comment };
      }
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await authH()) },
        body: JSON.stringify(bodyData),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || r.status);
      }
      closeModal();
      await loadList();
      const actionLabel = { approve: "批准", reject: "拒绝", return: "退回" };
      document.getElementById("inbox-detail").innerHTML = `
        <div class="inbox-empty">
          <div class="inbox-empty-icon">✅</div>
          <div style="font-size:.9rem;font-weight:500">已${actionLabel[modalAction]}</div>
        </div>`;
      selectedId = null;
    } catch (err) {
      alert("操作失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  };
```

- [ ] **Step 4: Add keyboard shortcut for "T" (return)**

In the keyboard shortcut handler (around line 458-465), add:

```javascript
    if (e.key === "t" || e.key === "T") openModal("return", selectedId);
```

Update the hint text in the empty state (around line 92-94):

```html
        <span class="kbd">A</span> 批准 &nbsp; <span class="kbd">T</span> 退回 &nbsp; <span class="kbd">R</span> 拒绝
```

- [ ] **Step 5: Verify in browser**

Test: Select a pending report → should see three buttons: 拒绝 (red), 退回 (orange), 批准 (green). Click 退回 → modal should require reason text. Press T key → should open return modal.

- [ ] **Step 6: Commit**

```bash
git add frontend/manager/queue.html
git commit -m "feat: add return-for-revision button to manager approval queue"
```

---

### Task 7: Add `needs_revision` UI to my-reports.html

**Files:**
- Modify: `frontend/employee/my-reports.html`

- [ ] **Step 1: Add CSS for needs_revision status**

In `frontend/employee/my-reports.html`, after the `.rstatus.withdrawn` CSS rule (around line 36), add:

```css
    .rstatus.needs_revision { background: #fff7ed; color: #c2410c; }
    .revision-banner {
      background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px;
      padding: .6rem .8rem; margin: 0 1.2rem .5rem; font-size: .82rem; color: #c2410c;
    }
```

- [ ] **Step 2: Add needs_revision to STATUS_LABEL**

In the `STATUS_LABEL` object (around line 101), add:

```javascript
const STATUS_LABEL = {
  open: "开放中", pending: "审批中",
  approved: "已批准", manager_approved: "经理已批", finance_approved: "财务已批",
  rejected: "已拒绝", needs_revision: "需修改", withdrawn: "已撤回", exported: "已入账",
};
```

- [ ] **Step 3: Update sort order**

In the `render()` function sort (around line 124), add `needs_revision`:

```javascript
    const order = { open: 0, needs_revision: 0.5, pending: 1, approved: 2, manager_approved: 2,
                    finance_approved: 2, rejected: 3, withdrawn: 4, exported: 5 };
```

- [ ] **Step 4: Add revision banner and resubmit button to renderCard**

In `renderCard()`, after the `linesHTML` variable (around line 143), add a revision banner:

```javascript
  const revisionBanner = r.status === "needs_revision" && r.revision_reason
    ? `<div class="revision-banner">退回原因：${r.revision_reason}</div>`
    : "";
```

In the actions section (around line 147-158), add a new condition for `needs_revision`:

```javascript
  const isNeedsRevision = r.status === "needs_revision";
```

And add this to the actions block, before the `if (!isOpen && !isPendingOrApproved)` block:

```javascript
  if (isNeedsRevision) {
    actions.push(`<a href="${detailUrl}" class="btn-ghost" onclick="event.stopPropagation()">修改</a>`);
    actions.push(`<button class="btn-primary" onclick="event.stopPropagation();resubmitReport('${r.id}')">重新提交</button>`);
  }
```

Update the fallback condition to also exclude `needs_revision`:

```javascript
  if (!isOpen && !isPendingOrApproved && !isNeedsRevision) {
```

In the card HTML template (around line 169), add the revision banner after `linesHTML`:

```javascript
      ${linesHTML}
      ${revisionBanner}
```

- [ ] **Step 5: Add resubmitReport function**

After the `withdrawReport` function (around line 216), add:

```javascript
async function resubmitReport(reportId) {
  if (!confirm("确认重新提交？所有发票将重新进入审核流程。")) return;
  const r = await fetch(`/api/reports/${reportId}/resubmit`, {
    method: "POST", headers: await authH(),
  });
  if (!r.ok) {
    let msg = "重新提交失败: " + r.status;
    try {
      const body = await r.json();
      if (body.detail) msg = body.detail;
    } catch {}
    alert(msg);
    return;
  }
  await load();
}
```

- [ ] **Step 6: Verify in browser**

Test flow:
1. Submit a report as employee
2. Switch to manager role, find the report, click "退回" with a reason
3. Switch back to employee, check my-reports → should see orange "需修改" badge, revision reason banner, and "重新提交" button
4. Click "修改" → goes to report detail for editing
5. Click "重新提交" → report re-enters pipeline

- [ ] **Step 7: Commit**

```bash
git add frontend/employee/my-reports.html
git commit -m "feat: add needs_revision status display and resubmit flow to my-reports"
```

---

### Task 8: Final integration test

**Files:** None (testing only)

- [ ] **Step 1: Start the server**

```bash
cd /Users/ashleychen/ExpenseFlow
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Test the full happy path**

1. Open `http://localhost:8000/employee/quick.html`
2. Upload a receipt → verify OCR runs, form appears with layer banner
3. Fill missing fields, save to report
4. Go to "我的报销单", submit the report
5. Switch to manager role, find in queue
6. Click "退回" → enter reason → confirm
7. Switch back to employee role
8. Verify "需修改" badge + reason banner + "重新提交" button appear
9. Click "重新提交" → verify status changes to "审批中"

- [ ] **Step 3: Test edge cases**

1. Visit `/employee/submit.html` → should redirect to `quick.html`
2. Visit `/employee/submit.html?report_id=xxx` → should redirect with query string preserved
3. Nav bar "提交报销" → should go to `quick.html`
4. In report detail, "手动创建" → should go to `quick.html`
5. FAQ accordion → should expand/collapse without errors

- [ ] **Step 4: Test return API validation**

```bash
# Return without reason should fail (422)
curl -s -X POST http://localhost:8000/api/reports/xxx/return \
  -H "Content-Type: application/json" -H "X-User-Id: mgr-1" -H "X-User-Role: manager" \
  -d '{}' | python -m json.tool

# Return non-pending report should fail (409)
curl -s -X POST http://localhost:8000/api/reports/xxx/return \
  -H "Content-Type: application/json" -H "X-User-Id: mgr-1" -H "X-User-Role: manager" \
  -d '{"reason":"test"}' | python -m json.tool

# Resubmit non-needs_revision report should fail (409)
curl -s -X POST http://localhost:8000/api/reports/xxx/resubmit \
  -H "X-User-Id: emp-1" -H "X-User-Role: employee" | python -m json.tool
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "test: verify simplified submission flow integration"
```
