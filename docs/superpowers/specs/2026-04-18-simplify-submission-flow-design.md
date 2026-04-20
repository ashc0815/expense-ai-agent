# Simplify Employee Submission Flow

**Date:** 2026-04-18
**Status:** Approved

## Problem

The current system has three overlapping submission paths (Quick Flow, Agent Chat, Direct Submit) that do the same thing differently. The Agent Chat path is slower, more expensive (LLM calls), and less reliable than the deterministic Quick pipeline. There is no return-for-revision workflow — rejected submissions are a terminal state with no way to fix and resubmit.

## Changes

### 1. Quick Flow Fallback — Remove AI Chat, Inline Layer Handling

**File:** `frontend/employee/quick.html`

**Delete (~180 lines):**
- `<button id="ai-fab">` floating chat toggle button
- `<aside id="ai-drawer">` entire chat drawer HTML
- Functions: `toggleAI()`, `appendAIMsg()`, `showAIAssistant()`, `autoFillMissing()`, `sendAI()`, `runAIChat()`, `syncFieldsToForm()`, `syncSingleField()`, `checkMissingFields()`
- Bottom link: `<a href="/employee/submit.html">或者直接手动填表 →</a>`
- CSS: `.chat-msg.system`, `.ai-needs-badge`

**Add — inline status banner in `showForm()`:**

Based on `draftData[draftId].layer`:
- `"1"` — green banner: "All fields recognized, please confirm and save"
- `"2"` — yellow banner: "N fields need manual input" (missing fields get red `需填写` badge — already exists)
- `"3_soft"` — orange banner: "Recognition quality low, please fill manually" + empty form with receipt preview
- `"3_hard"` — error card replacing the form: "Cannot recognize this image" + two buttons: [Re-upload] [Fill manually]
  - [Re-upload] calls existing `resetAndUploadNew()`
  - [Fill manually] sets layer to `"3_soft"` and calls `showForm()` with empty fields

**Add — FAQ accordion** replacing the chat drawer:

Static HTML accordion at bottom of form section with 4 predefined Q&A entries:
- Category guidance
- Over-limit handling
- Duplicate warning explanation
- Budget limit explanation

No LLM calls. Pure static content.

### 2. Quick Flow as Only Entry Point

| File | Line | Change |
|------|------|--------|
| `frontend/shared/nav.js` | 9 | `href: "/employee/submit.html"` → `href: "/employee/quick.html"` |
| `frontend/employee/report.html` | 264 | `href="/employee/submit.html?report_id=..."` → `href="/employee/quick.html?report_id=..."` |
| `frontend/employee/quick.html` | 162 | Delete the `<p>` containing submit.html link |
| `frontend/employee/submit.html` | entire file | Replace body with redirect: `<script>location.replace('/employee/quick.html' + location.search)</script>` |

### 3. `needs_revision` Return-for-Revision Flow

**Atomic unit:** Report (not individual submission lines), per project rule in `project_approval_unit.md`.

#### Backend

**`backend/db/store.py`:**
- Submission status comment: add `needs_revision` to the list
- Report status: add `needs_revision` to valid statuses
- Add `revision_reason` column to Report model (nullable String(500))

**`backend/api/routes/approvals.py`:**
- New endpoint: `POST /{submission_id}/return`
  - Accepts `ReturnBody(reason: str)`
  - Looks up the submission's `report_id`
  - Sets Report status → `needs_revision`, stores `revision_reason`
  - Sets ALL submissions under that Report → `needs_revision`
  - Creates audit log entry with reason
  - Returns updated report dict

**`backend/api/routes/reports.py`:**
- New endpoint: `POST /api/reports/{report_id}/resubmit`
  - Validates Report status is `needs_revision`
  - Sets Report status → `pending`, clears `revision_reason`
  - Sets all submissions under Report → `processing`
  - Re-runs the 5-skill pipeline on all lines
  - Creates audit log entry

#### Frontend

**`frontend/employee/my-reports.html`:**
- Add `needs_revision` to `STATUS_LABEL`: `"needs_revision": "需修改"`
- Add CSS: `.rstatus.needs_revision { background: #fff7ed; color: #c2410c; }`
- When report status is `needs_revision`:
  - Show revision reason in an orange banner below report header
  - Show "修改并重新提交" button that navigates to `report.html?report_id=X`
  - In `report.html`, enable line editing (already supported via PATCH)
  - After editing, "重新提交" button calls `POST /api/reports/{id}/resubmit`

**`frontend/manager/queue.html`:**
- Add "退回" button alongside existing "通过" and "拒绝" buttons
- "退回" opens a small modal/prompt for entering the reason
- Calls `POST /api/submissions/{id}/return` with reason
- Visual distinction: 退回 = orange (fixable), 拒绝 = red (terminal)

### 4. Status Machine Update

```
Before:
  processing → reviewed → manager_approved → finance_approved → exported
                   ↓
                rejected (terminal)

After:
  processing → reviewed → manager_approved → finance_approved → exported
                   ↓              ↓
                rejected    needs_revision
                (terminal)       ↓
                            employee edits
                                 ↓
                            resubmit → processing (re-enters pipeline)
```

## Files Modified

| File | Type | Summary |
|------|------|---------|
| `frontend/employee/quick.html` | Major edit | Remove AI chat (~180 lines), add inline banners + FAQ |
| `frontend/employee/submit.html` | Replace | Redirect to quick.html |
| `frontend/shared/nav.js` | 1-line edit | Nav link → quick.html |
| `frontend/employee/report.html` | 1-line edit | "手动创建" link → quick.html |
| `frontend/employee/my-reports.html` | Minor edit | Add needs_revision status + UI |
| `frontend/manager/queue.html` | Minor edit | Add "退回" button |
| `backend/db/store.py` | Minor edit | Add revision_reason column, update status comments |
| `backend/api/routes/approvals.py` | Add endpoint | POST /{id}/return |
| `backend/api/routes/reports.py` | Add endpoint | POST /reports/{id}/resubmit |

## Not In Scope

- Multi-tenant architecture
- Agent Chat for finance investigation
- Budget by expense type
- Eval dataset for AmbiguityDetector
