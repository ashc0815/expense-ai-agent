# Quick Attest Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/employee/quick.html` as the Ramp-style agent submission flow, with a 3-layer degradation (card → inline fix → fallback form). Old `submit.html` becomes the Layer 3 fallback.

**Architecture:** Zero new DB tables (reuse `Draft` + add 2 columns; add one `telemetry_events` table). Zero-LLM determinism pipeline: new `backend/quick/pipeline.py` sequences OCR → classify → dedupe → budget tools directly (no agent loop), emitting SSE events. Frontend `quick.html` is a thin SSE consumer that renders an attestation card and PATCHes fields inline. Multi-page PDFs are split into N drafts via `backend/pdf_splitter.py` (pypdf).

**Tech Stack:** FastAPI + SQLAlchemy async + pytest / Vanilla HTML+JS + EventSource / pypdf.

**Reference spec:** `docs/superpowers/specs/2026-04-15-quick-attest-flow-design.md`

---

## File Structure

**New files**

- `backend/pdf_splitter.py` — pure function `split(file_bytes) -> list[bytes]`
- `backend/quick/__init__.py`
- `backend/quick/layer_decision.py` — pure function `decide_layer(ocr, classify, dedupe, budget, edited_field_count) -> str`
- `backend/quick/pipeline.py` — async generator `run_quick_pipeline(draft_id, ctx, db) -> AsyncIterator[dict]`
- `backend/quick/finalize.py` — extracted helper `finalize_draft_to_submission(draft, ctx, db, background_tasks)` reused by both old `submit_draft` and new `attest_draft`
- `backend/api/routes/quick.py` — 3 routes: `upload`, `stream`, `attest`
- `backend/tests/test_pdf_splitter.py`
- `backend/tests/test_quick_layer_decision.py`
- `backend/tests/test_quick_api.py`
- `backend/tests/test_quick_e2e.py`
- `frontend/employee/quick.html`

**Modified files**

- `backend/db/store.py` — add `layer`, `entry` columns to `Draft`; add `TelemetryEvent` model and `insert_telemetry` helper
- `backend/api/routes/chat.py:1630` — `submit_draft` refactored to call the extracted `finalize_draft_to_submission` helper
- `backend/main.py:16` — register `quick` router
- `frontend/employee/submit.html` — add `← 返回 quick` button at top (Task 14)
- `requirements.txt` — add `pypdf>=4.0.0`

---

## Task 1: Add pypdf dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append pypdf line**

Add at end of `requirements.txt`:

```
pypdf>=4.0.0              # PDF page splitting for multi-receipt uploads
```

- [ ] **Step 2: Install**

Run: `pip install pypdf>=4.0.0`
Expected: successful install.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add pypdf for multi-page PDF splitting"
```

---

## Task 2: PDF splitter module + tests

**Files:**
- Create: `backend/pdf_splitter.py`
- Create: `backend/tests/test_pdf_splitter.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pdf_splitter.py`:

```python
"""pdf_splitter — split multi-page PDFs into per-page byte blobs."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from backend.pdf_splitter import SplitError, split


def _make_pdf(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_single_page_returns_one_item():
    pdf = _make_pdf(1)
    out = split(pdf)
    assert len(out) == 1
    assert PdfReader(io.BytesIO(out[0])).pages.__len__() == 1


def test_three_page_returns_three_items():
    pdf = _make_pdf(3)
    out = split(pdf)
    assert len(out) == 3
    for page_bytes in out:
        assert PdfReader(io.BytesIO(page_bytes)).pages.__len__() == 1


def test_garbage_raises_split_error():
    with pytest.raises(SplitError):
        split(b"this is not a pdf")


def test_non_pdf_bytes_raises_split_error():
    with pytest.raises(SplitError):
        split(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_pdf_splitter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.pdf_splitter'`

- [ ] **Step 3: Write the implementation**

Create `backend/pdf_splitter.py`:

```python
"""Multi-page PDF splitter.

Given PDF bytes, return a list of single-page PDF byte blobs. Single-page
input returns a list of length 1. Non-PDF or corrupt input raises SplitError.
"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class SplitError(ValueError):
    """Raised when input bytes can't be parsed as a PDF."""


def split(file_bytes: bytes) -> list[bytes]:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = list(reader.pages)
    except (PdfReadError, OSError) as exc:
        raise SplitError(f"not a valid PDF: {exc}") from exc

    if not pages:
        raise SplitError("PDF has zero pages")

    out: list[bytes] = []
    for page in pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest backend/tests/test_pdf_splitter.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pdf_splitter.py backend/tests/test_pdf_splitter.py
git commit -m "feat: add pdf_splitter for multi-page PDF uploads"
```

---

## Task 3: Layer decision pure function + tests

**Files:**
- Create: `backend/quick/__init__.py`
- Create: `backend/quick/layer_decision.py`
- Create: `backend/tests/test_quick_layer_decision.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_quick_layer_decision.py`:

```python
"""Layer decision — pure function, exhaustive case coverage."""
from __future__ import annotations

from backend.quick.layer_decision import decide_layer


def _ocr(amount=150.0, merchant="海底捞", date="2026-04-14", confidence=0.95):
    return {
        "amount": amount, "merchant": merchant, "date": date,
        "confidence": confidence,
    }


def _classify(category="meal", confidence=0.9):
    return {"category": category, "confidence": confidence}


def _dedupe(is_duplicate=False):
    return {"is_duplicate": is_duplicate}


def _budget(signal="ok"):
    return {"signal": signal}


# ── Hard errors ──────────────────────────────────────────────────

def test_ocr_all_empty_is_hard():
    ocr = {"amount": None, "merchant": None, "date": None, "confidence": 0.0}
    assert decide_layer(ocr, _classify(), _dedupe(), _budget()) == "3_hard"


def test_not_a_receipt_is_hard():
    ocr = {"amount": None, "merchant": None, "date": None, "confidence": 0.0,
           "not_a_receipt": True}
    assert decide_layer(ocr, _classify(), _dedupe(), _budget()) == "3_hard"


# ── Soft errors ──────────────────────────────────────────────────

def test_three_fields_need_fix_is_soft():
    ocr = _ocr(merchant=None, date=None)  # 2 missing + no project + no category
    classify = _classify(confidence=0.3)  # category also needs fix
    # Missing: merchant, date, project_code, category = 4 — soft
    assert decide_layer(ocr, classify, _dedupe(), _budget()) == "3_soft"


# ── Happy path ───────────────────────────────────────────────────

def test_all_high_confidence_is_layer_1():
    assert decide_layer(_ocr(), _classify(), _dedupe(), _budget()) == "1"


# ── Layer 2 ──────────────────────────────────────────────────────

def test_one_field_missing_is_layer_2():
    # Project code not auto-filled → 1 field
    ocr = _ocr()
    out = decide_layer(ocr, _classify(), _dedupe(), _budget(),
                       missing_optional_fields=["project_code"])
    assert out == "2"


def test_two_fields_missing_is_layer_2():
    ocr = _ocr()
    out = decide_layer(ocr, _classify(), _dedupe(), _budget(),
                       missing_optional_fields=["project_code", "description"])
    assert out == "2"


def test_classify_mid_confidence_is_layer_2():
    assert decide_layer(_ocr(), _classify(confidence=0.65),
                        _dedupe(), _budget()) == "2"


def test_classify_low_confidence_counts_as_needs_fix():
    # conf < 0.5 = category needs fix; that's 1 field → Layer 2
    assert decide_layer(_ocr(), _classify(confidence=0.3),
                        _dedupe(), _budget()) == "2"


def test_budget_warn_is_layer_2():
    assert decide_layer(_ocr(), _classify(),
                        _dedupe(), _budget(signal="warn")) == "2"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest backend/tests/test_quick_layer_decision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.quick'`

- [ ] **Step 3: Write the implementation**

Create `backend/quick/__init__.py` (empty file).

Create `backend/quick/layer_decision.py`:

```python
"""Pure function that classifies a Draft's completeness into a Layer.

Layers:
  "1"       — happy path, all critical fields high-conf, budget ok
  "2"       — 1-2 fields need user fix / classify mid-conf / budget warn
  "3_soft"  — >= 3 fields need user fix; card stays but shows 手填/重拍
  "3_hard"  — OCR all empty or not-a-receipt; frontend auto-redirects

Thresholds (v1, hard-coded; future: telemetry-driven):
  OCR amount/merchant confidence >= 0.8  → high
  classify confidence >= 0.8              → high
  classify confidence 0.5 - 0.8           → mid (Layer 2 inline chip)
  classify confidence < 0.5               → counts as "needs fix"
  Layer 2 capacity                        → max 2 missing/fix fields
"""
from __future__ import annotations

OCR_CONF_HIGH = 0.8
CLASSIFY_CONF_HIGH = 0.8
CLASSIFY_CONF_MID = 0.5
LAYER_2_MAX_FIELDS = 2


def decide_layer(
    ocr: dict,
    classify: dict,
    dedupe: dict,
    budget: dict,
    missing_optional_fields: list[str] | None = None,
) -> str:
    # ── Hard errors ─────────────────────────────────────────────
    if ocr.get("not_a_receipt"):
        return "3_hard"

    critical = [ocr.get("amount"), ocr.get("merchant"), ocr.get("date")]
    if all(v in (None, "", 0) for v in critical):
        return "3_hard"

    # ── Count fields that need user fix ─────────────────────────
    fix_count = 0

    if ocr.get("amount") in (None, "", 0):
        fix_count += 1
    if not ocr.get("merchant"):
        fix_count += 1
    if not ocr.get("date"):
        fix_count += 1
    if (ocr.get("confidence") or 0) < OCR_CONF_HIGH:
        # Even if individual fields present, low overall conf → user confirm
        # but only counts as 1 extra fix
        fix_count += 0  # don't double-count; confidence alone is not a "fix"

    classify_conf = classify.get("confidence") or 0
    if classify_conf < CLASSIFY_CONF_MID:
        fix_count += 1  # category low-conf → user must pick

    fix_count += len(missing_optional_fields or [])

    # ── Soft error: too many fields ─────────────────────────────
    if fix_count >= 3:
        return "3_soft"

    # ── Layer 2 conditions ──────────────────────────────────────
    if fix_count >= 1:
        return "2"
    if CLASSIFY_CONF_MID <= classify_conf < CLASSIFY_CONF_HIGH:
        return "2"
    if budget.get("signal") == "warn":
        return "2"
    if dedupe.get("is_duplicate"):
        return "2"

    # ── Happy path ──────────────────────────────────────────────
    return "1"
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest backend/tests/test_quick_layer_decision.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/quick/__init__.py backend/quick/layer_decision.py backend/tests/test_quick_layer_decision.py
git commit -m "feat: add pure layer decision function for quick flow"
```

---

## Task 4: Draft schema — add layer + entry columns

**Files:**
- Modify: `backend/db/store.py` (class `Draft` around line 128)
- Modify: `backend/tests/test_db.py` (add a new test)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_db.py`:

```python
import pytest

from backend.db.store import (
    Draft, create_draft, get_draft,
)


@pytest.mark.asyncio
async def test_draft_has_layer_and_entry_columns(db_session):
    draft = await create_draft(db_session, employee_id="emp_001")
    assert draft.layer is None
    assert draft.entry is None

    draft.layer = "1"
    draft.entry = "quick"
    await db_session.commit()
    await db_session.refresh(draft)

    reloaded = await get_draft(db_session, draft.id)
    assert reloaded.layer == "1"
    assert reloaded.entry == "quick"
```

> Note: if `db_session` fixture doesn't exist, look at how `test_db.py` currently builds its async session and reuse that pattern — do NOT invent a new fixture.

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest backend/tests/test_db.py::test_draft_has_layer_and_entry_columns -v`
Expected: FAIL — `AttributeError: 'Draft' object has no attribute 'layer'`

- [ ] **Step 3: Add columns to Draft model**

In `backend/db/store.py`, edit class `Draft` (around line 128). After the `submitted_as` line, add:

```python
    layer          = Column(String(16),  nullable=True, default=None)
    # Quick flow only: "1" / "2" / "3_soft" / "3_hard" — telemetry/debugging
    entry          = Column(String(16),  nullable=True, default=None)
    # "quick" or "form" — tracks which entry path created this draft
```

- [ ] **Step 4: Run test, verify pass**

Run: `pytest backend/tests/test_db.py::test_draft_has_layer_and_entry_columns -v`
Expected: PASS

(SQLite auto-creates columns on fresh DB each test run — no migration required. Production has no data in `drafts` worth preserving for v1.)

- [ ] **Step 5: Run full test suite to confirm no regression**

Run: `pytest backend/tests/test_db.py -v`
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add backend/db/store.py backend/tests/test_db.py
git commit -m "feat: add layer and entry columns to Draft model"
```

---

## Task 5: Telemetry table + insert helper + test

**Files:**
- Modify: `backend/db/store.py` (append new model near end of models section)
- Modify: `backend/tests/test_db.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_db.py`:

```python
@pytest.mark.asyncio
async def test_insert_telemetry_event(db_session):
    from backend.db.store import insert_telemetry, TelemetryEvent
    from sqlalchemy import select

    await insert_telemetry(
        db_session,
        draft_id="draft-1",
        entry="quick",
        final_layer="1",
        ocr_confidence_min=0.95,
        classify_confidence=0.9,
        fields_edited_count=0,
        time_to_attest_ms=2800,
        attest_or_abandoned="attest",
    )

    rows = (await db_session.execute(select(TelemetryEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].draft_id == "draft-1"
    assert rows[0].final_layer == "1"
    assert rows[0].attest_or_abandoned == "attest"
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest backend/tests/test_db.py::test_insert_telemetry_event -v`
Expected: FAIL — `ImportError: cannot import name 'insert_telemetry'`

- [ ] **Step 3: Add model + helper**

In `backend/db/store.py`, near the other model definitions add:

```python
class TelemetryEvent(Base):
    """Append-only event stream for quick flow. v1: no reads, only writes."""
    __tablename__ = "telemetry_events"

    id                  = Column(String(36), primary_key=True,
                                 default=lambda: str(uuid.uuid4()))
    draft_id            = Column(String(36), nullable=False, index=True)
    entry               = Column(String(16), nullable=False)
    final_layer         = Column(String(16), nullable=False)
    ocr_confidence_min  = Column(Numeric(4, 3), nullable=True)
    classify_confidence = Column(Numeric(4, 3), nullable=True)
    fields_edited_count = Column(Integer, nullable=False, default=0)
    time_to_attest_ms   = Column(Integer, nullable=True)
    attest_or_abandoned = Column(String(16), nullable=False)
    # "attest" | "abandoned" | "redirected"
    created_at          = Column(DateTime(timezone=True),
                                 default=lambda: datetime.now(timezone.utc))
```

Also add the helper function in the CRUD section:

```python
async def insert_telemetry(
    db: AsyncSession,
    *,
    draft_id: str,
    entry: str,
    final_layer: str,
    ocr_confidence_min: float | None,
    classify_confidence: float | None,
    fields_edited_count: int,
    time_to_attest_ms: int | None,
    attest_or_abandoned: str,
) -> None:
    ev = TelemetryEvent(
        draft_id=draft_id,
        entry=entry,
        final_layer=final_layer,
        ocr_confidence_min=ocr_confidence_min,
        classify_confidence=classify_confidence,
        fields_edited_count=fields_edited_count,
        time_to_attest_ms=time_to_attest_ms,
        attest_or_abandoned=attest_or_abandoned,
    )
    db.add(ev)
    await db.commit()
```

Make sure `Integer` is in the SQLAlchemy import list at top of file; if not, add it.

- [ ] **Step 4: Run test, verify pass**

Run: `pytest backend/tests/test_db.py::test_insert_telemetry_event -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/db/store.py backend/tests/test_db.py
git commit -m "feat: add telemetry_events table and insert helper"
```

---

## Task 6: Extract finalize_draft_to_submission helper

**Files:**
- Create: `backend/quick/finalize.py`
- Modify: `backend/api/routes/chat.py` (refactor `submit_draft` at ~1630 to call helper)

- [ ] **Step 1: Review existing submit_draft**

Read `backend/api/routes/chat.py:1630-1718` carefully. You're extracting the body of `submit_draft` (lines 1637-1716) into a pure helper that the new `attest` route will also call.

- [ ] **Step 2: Write a regression test**

Create `backend/tests/test_quick_finalize.py`:

```python
"""Verify the extracted finalize helper preserves old submit behavior."""
import os, tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP.name}")
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_finalize_test")

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.api.middleware.auth import UserContext
from backend.db.store import (
    Base, create_draft, update_draft_receipt, update_draft_field, get_submission,
)
from backend.quick.finalize import finalize_draft_to_submission

_engine = create_async_engine(f"sqlite+aiosqlite:///{_TMP.name}")
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_finalize_creates_submission_and_marks_draft():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _Session() as db:
        draft = await create_draft(db, "emp_finalize_1")
        await update_draft_receipt(db, draft.id, "/uploads/x.jpg")
        for k, v in [
            ("merchant", "海底捞"),
            ("amount", 358.0),
            ("date", "2026-04-14"),
            ("category", "meal"),
        ]:
            await update_draft_field(db, draft.id, k, v, "ocr")

        ctx = UserContext(user_id="emp_finalize_1", role="employee", email=None, name="t")
        bg = BackgroundTasks()
        sub_id = await finalize_draft_to_submission(draft.id, ctx, db, bg)

        sub = await get_submission(db, sub_id)
        assert sub is not None
        assert sub.merchant == "海底捞"
        assert float(sub.amount) == 358.0
```

- [ ] **Step 3: Run test, verify fail**

Run: `pytest backend/tests/test_quick_finalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.quick.finalize'`

- [ ] **Step 4: Create the helper**

Create `backend/quick/finalize.py`:

```python
"""Extracted finalize helper.

Used by both:
  - backend/api/routes/chat.py:submit_draft    (legacy form flow)
  - backend/api/routes/quick.py:attest_draft   (new quick flow)

Raises HTTPException on validation failure, identical to legacy.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext
from backend.api.routes.admin import _POLICY
from backend.api.routes.submissions import _run_pipeline
from backend.db.store import (
    create_audit_log, create_submission, get_draft, get_employee,
    get_submission_by_invoice, mark_draft_submitted,
)


async def finalize_draft_to_submission(
    draft_id: str,
    ctx: UserContext,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> str:
    """Convert a draft to a submission. Returns the new submission id.

    Raises HTTPException(404/403/409/422) on failure.
    """
    draft = await get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft 不存在")
    if draft.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")
    if draft.submitted_as:
        raise HTTPException(status_code=409,
                            detail=f"该 draft 已提交为 {draft.submitted_as}")
    if not draft.receipt_url:
        raise HTTPException(status_code=422, detail="请先上传发票")

    fields = draft.fields or {}
    for required in ("merchant", "amount", "date", "category"):
        if not fields.get(required):
            raise HTTPException(status_code=422,
                                detail=f"缺少必填字段：{required}")

    inv = fields.get("invoice_number")
    if inv:
        existing = await get_submission_by_invoice(db, inv)
        if existing:
            raise HTTPException(
                status_code=422,
                detail=f"发票号 {inv} 已被报销过（单据 #{existing.id[:8]}）",
            )

    emp = await get_employee(db, ctx.user_id)
    department  = emp.department  if emp else None
    cost_center = emp.cost_center if emp else None
    gl_account  = (_POLICY.get("gl_mapping") or {}).get(fields.get("category"))

    sub = await create_submission(db, {
        "employee_id":    ctx.user_id,
        "status":         "processing",
        "amount":         float(fields["amount"]),
        "currency":       fields.get("currency", "CNY"),
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
    })
    await mark_draft_submitted(db, draft_id, sub.id)
    await create_audit_log(
        db, actor_id=ctx.user_id, action="draft_submitted",
        resource_type="submission", resource_id=sub.id,
        detail={"draft_id": draft_id, "field_sources": draft.field_sources},
    )
    background_tasks.add_task(_run_pipeline, sub.id, {
        "employee_id": ctx.user_id,
        "employee_name": emp.name if emp else None,
        "department": department,
        "city": emp.city if emp else None,
        "level": emp.level if emp else None,
        "amount": float(fields["amount"]),
        "currency": fields.get("currency", "CNY"),
        "category": fields["category"],
        "date": fields["date"],
        "merchant": fields["merchant"],
        "tax_amount": float(fields.get("tax_amount") or 0) or None,
        "description": fields.get("description"),
        "invoice_number": inv,
        "invoice_code": fields.get("invoice_code"),
    })
    return sub.id
```

- [ ] **Step 5: Refactor `submit_draft` in chat.py to use the helper**

In `backend/api/routes/chat.py`, at the top imports add:

```python
from backend.quick.finalize import finalize_draft_to_submission
```

Then replace the entire body of `submit_draft` (lines 1637-1716) with:

```python
    sub_id = await finalize_draft_to_submission(
        draft_id, ctx, db, background_tasks,
    )
    return {
        "id": sub_id,
        "draft_id": draft_id,
        "status": "processing",
        "message": "草稿已转正为正式报销单，AI 审核中…",
    }
```

- [ ] **Step 6: Run the new test + legacy submit tests**

```bash
pytest backend/tests/test_quick_finalize.py -v
pytest backend/tests/test_submissions.py backend/tests/test_e2e.py -v
```

Expected: all pass. If legacy tests fail, the refactor is wrong — inspect the error and align the helper body with the original.

- [ ] **Step 7: Commit**

```bash
git add backend/quick/finalize.py backend/api/routes/chat.py backend/tests/test_quick_finalize.py
git commit -m "refactor: extract finalize_draft_to_submission helper"
```

---

## Task 7: Quick pipeline module (event generator)

**Files:**
- Create: `backend/quick/pipeline.py`
- Create: `backend/tests/test_quick_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_quick_pipeline.py`:

```python
"""Quick pipeline — sequences tools and emits SSE-style events."""
import os, tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP.name}")
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_pipeline_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.api.middleware.auth import UserContext
from backend.db.store import Base, create_draft, update_draft_receipt, get_draft
from backend.quick.pipeline import run_quick_pipeline

_engine = create_async_engine(f"sqlite+aiosqlite:///{_TMP.name}")
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_pipeline_emits_events_in_order():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _Session() as db:
        draft = await create_draft(db, "emp_pipe_1")
        # Write a fake receipt file reference; tool will use mock OCR
        await update_draft_receipt(db, draft.id, "/uploads/stub.jpg")

        ctx = UserContext(user_id="emp_pipe_1", role="employee", email=None, name="t")
        events = []
        async for ev in run_quick_pipeline(draft.id, ctx, db):
            events.append(ev)

        types = [e["type"] for e in events]
        # Expected fixed sequence
        assert types == [
            "ocr_done", "classify_done", "dedupe_done",
            "budget_done", "card_ready",
        ]
        # card_ready carries the decided layer
        ready = events[-1]
        assert ready["layer"] in ("1", "2", "3_soft", "3_hard")


@pytest.mark.asyncio
async def test_pipeline_persists_layer_to_draft():
    async with _Session() as db:
        draft = await create_draft(db, "emp_pipe_2")
        await update_draft_receipt(db, draft.id, "/uploads/stub.jpg")
        ctx = UserContext(user_id="emp_pipe_2", role="employee", email=None, name="t")

        async for _ in run_quick_pipeline(draft.id, ctx, db):
            pass

        reloaded = await get_draft(db, draft.id)
        assert reloaded.layer is not None
        assert reloaded.entry == "quick"
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest backend/tests/test_quick_pipeline.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

Create `backend/quick/pipeline.py`:

```python
"""Deterministic tool pipeline for the quick flow.

Unlike /api/chat/stream (agent-driven, LLM picks tools), this is a fixed
sequence: OCR → classify → dedupe → budget. No LLM. Each tool result is
emitted as an event AND written back to the draft via store helpers.

Event types (in order):
  ocr_done       { amount, merchant, date, confidence, error? }
  classify_done  { category, confidence }
  dedupe_done    { is_duplicate }
  budget_done    { signal }
  card_ready     { layer, actions }
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext
from backend.api.routes.chat import (
    tool_extract_receipt_fields,
    tool_suggest_category,
    tool_check_duplicate_invoice,
    tool_get_budget_summary,
)
from backend.db.store import get_draft, update_draft_field
from backend.quick.layer_decision import decide_layer


async def _set(db: AsyncSession, draft_id: str, field: str, value, source="pipeline"):
    if value is not None and value != "":
        await update_draft_field(db, draft_id, field, value, source)


async def run_quick_pipeline(
    draft_id: str,
    ctx: UserContext,
    db: AsyncSession,
) -> AsyncIterator[dict]:
    """Yield SSE-style event dicts (no data: prefix). Caller formats them."""
    draft = await get_draft(db, draft_id)
    if not draft:
        yield {"type": "error", "message": "draft not found"}
        return

    # Mark entry early so abandoned drafts still show "quick"
    draft.entry = "quick"
    await db.commit()

    # ── 1. OCR ─────────────────────────────────────────────────
    try:
        ocr = await tool_extract_receipt_fields({}, ctx, db, draft_id)
    except Exception as exc:  # noqa: BLE001
        ocr = {"error": str(exc)}

    if ocr.get("error"):
        yield {"type": "ocr_failed", "error": ocr["error"]}
        # Hard error — force layer 3_hard, skip rest
        draft = await get_draft(db, draft_id)
        draft.layer = "3_hard"
        await db.commit()
        yield {"type": "card_ready", "layer": "3_hard", "actions": ["redirect"]}
        return

    await _set(db, draft_id, "amount", ocr.get("amount"), "ocr")
    await _set(db, draft_id, "merchant", ocr.get("merchant"), "ocr")
    await _set(db, draft_id, "date", ocr.get("date"), "ocr")
    await _set(db, draft_id, "invoice_number", ocr.get("invoice_number"), "ocr")
    await _set(db, draft_id, "tax_amount", ocr.get("tax_amount"), "ocr")
    await _set(db, draft_id, "currency", ocr.get("currency"), "ocr")

    yield {
        "type": "ocr_done",
        "amount": ocr.get("amount"),
        "merchant": ocr.get("merchant"),
        "date": ocr.get("date"),
        "confidence": ocr.get("confidence") or (0.95 if ocr.get("amount") else 0.0),
    }

    # ── 2. Classify ────────────────────────────────────────────
    classify = await tool_suggest_category(
        {"merchant": ocr.get("merchant") or ""}, ctx, db, draft_id,
    )
    await _set(db, draft_id, "category", classify.get("category"), "pipeline")
    yield {
        "type": "classify_done",
        "category": classify.get("category"),
        "confidence": classify.get("confidence"),
    }

    # ── 3. Dedupe ──────────────────────────────────────────────
    if ocr.get("invoice_number"):
        dedupe = await tool_check_duplicate_invoice(
            {"invoice_number": ocr["invoice_number"]}, ctx, db, draft_id,
        )
    else:
        dedupe = {"is_duplicate": False}
    yield {"type": "dedupe_done", "is_duplicate": dedupe.get("is_duplicate", False)}

    # ── 4. Budget ──────────────────────────────────────────────
    budget = await tool_get_budget_summary({}, ctx, db, draft_id)
    yield {"type": "budget_done", "signal": budget.get("signal", "ok")}

    # ── 5. Decide layer and persist ────────────────────────────
    fresh = await get_draft(db, draft_id)
    missing_optional: list[str] = []
    if not (fresh.fields or {}).get("project_code"):
        missing_optional.append("project_code")

    layer = decide_layer(
        ocr={**ocr, "confidence": ocr.get("confidence") or 0.95},
        classify=classify,
        dedupe=dedupe,
        budget=budget,
        missing_optional_fields=missing_optional,
    )
    fresh.layer = layer
    await db.commit()

    actions = {"1": ["attest"], "2": ["attest", "edit"],
               "3_soft": ["manual", "retake"], "3_hard": ["redirect"]}[layer]
    yield {"type": "card_ready", "layer": layer, "actions": actions}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest backend/tests/test_quick_pipeline.py -v`
Expected: both tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/quick/pipeline.py backend/tests/test_quick_pipeline.py
git commit -m "feat: add deterministic quick pipeline for OCR/classify/dedupe/budget"
```

---

## Task 8: Quick routes — upload, stream, attest

**Files:**
- Create: `backend/api/routes/quick.py`
- Create: `backend/tests/test_quick_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_quick_api.py`:

```python
"""Route-level tests for /api/quick/*."""
import os, tempfile, io, json

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP.name}")
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_quick_api_test")
os.makedirs("/tmp/concurshield_quick_api_test", exist_ok=True)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import Base, get_db
from backend.main import app

_engine = create_async_engine(f"sqlite+aiosqlite:///{_TMP.name}")
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():
    async with _Session() as s:
        yield s


def setup_module(_):
    import backend.config as cfg
    cfg.DATABASE_URL = f"sqlite+aiosqlite:///{_TMP.name}"
    import asyncio
    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.get_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db


client = TestClient(app)
HEADERS = {"X-Mock-User-Id": "emp_quick_1", "X-Mock-User-Role": "employee"}


def test_upload_returns_draft_id():
    files = {"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xff" + b"\x00"*100), "image/jpeg")}
    r = client.post("/api/quick/upload", files=files, headers=HEADERS)
    assert r.status_code == 201
    body = r.json()
    assert "draft_id" in body


def test_stream_emits_card_ready():
    files = {"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xff" + b"\x00"*100), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]

    r2 = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r2.status_code == 200
    text = r2.text
    # SSE: lines starting with "data: "
    events = [json.loads(line[6:]) for line in text.splitlines()
              if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert "card_ready" in types
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] in ("1", "2", "3_soft", "3_hard")


def test_attest_rejects_layer_3():
    files = {"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xff" + b"\x00"*100), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]
    # Force layer 3_hard by directly setting it
    import asyncio
    async def _force():
        async with _Session() as db:
            from backend.db.store import get_draft
            d = await get_draft(db, draft_id)
            d.layer = "3_hard"
            await db.commit()
    asyncio.get_event_loop().run_until_complete(_force())

    r3 = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r3.status_code == 422
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest backend/tests/test_quick_api.py -v`
Expected: FAIL on all three (routes don't exist, 404).

- [ ] **Step 3: Create the route file**

Create `backend/api/routes/quick.py`:

```python
"""Quick attest flow — primary submission path.

Routes:
  POST /api/quick/upload         → save file, return draft_id
  GET  /api/quick/stream/:id     → SSE pipeline (OCR → classify → dedupe → budget)
  POST /api/quick/attest/:id     → finalize draft to submission (layer 1/2 only)
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth
from backend.db.store import (
    create_draft, get_db, get_draft, insert_telemetry, update_draft_receipt,
)
from backend.quick.finalize import finalize_draft_to_submission
from backend.quick.pipeline import run_quick_pipeline
from backend.storage import get_storage

router = APIRouter()


@router.post("/upload", status_code=201)
async def quick_upload(
    file: UploadFile = File(...),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    draft = await create_draft(db, ctx.user_id)
    draft.entry = "quick"
    await db.commit()

    storage = get_storage()
    receipt_url = await storage.save(file, file.filename or "receipt.jpg")
    await update_draft_receipt(db, draft.id, receipt_url)

    return {"draft_id": draft.id, "receipt_url": receipt_url}


@router.get("/stream/{draft_id}")
async def quick_stream(
    draft_id: str,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    draft = await get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft 不存在")
    if draft.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")

    async def event_stream() -> AsyncIterator[str]:
        async for event in run_quick_pipeline(draft_id, ctx, db):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/attest/{draft_id}")
async def quick_attest(
    draft_id: str,
    background_tasks: BackgroundTasks,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    draft = await get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft 不存在")
    if draft.employee_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")
    if draft.layer not in ("1", "2"):
        raise HTTPException(
            status_code=422,
            detail=f"当前 layer={draft.layer}，无法直接 attest；请走 submit.html",
        )

    sub_id = await finalize_draft_to_submission(draft_id, ctx, db, background_tasks)

    # Telemetry
    try:
        await insert_telemetry(
            db,
            draft_id=draft_id,
            entry=draft.entry or "quick",
            final_layer=draft.layer,
            ocr_confidence_min=None,
            classify_confidence=None,
            fields_edited_count=0,
            time_to_attest_ms=None,
            attest_or_abandoned="attest",
        )
    except Exception:
        pass  # telemetry must not block attest

    return {"id": sub_id, "draft_id": draft_id, "status": "processing"}
```

- [ ] **Step 4: Register router in main.py**

In `backend/main.py`, edit line 16 to add `quick`:

```python
from backend.api.routes import submissions, approvals, ocr, users, admin, employees, finance, chat, budget, quick
```

And add after the other `include_router` lines:

```python
app.include_router(quick.router, prefix="/api/quick", tags=["quick"])
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest backend/tests/test_quick_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes/quick.py backend/main.py backend/tests/test_quick_api.py
git commit -m "feat: add /api/quick/{upload,stream,attest} routes"
```

---

## Task 9: E2E test — happy path + hard fail + soft fail + splitter

**Files:**
- Create: `backend/tests/test_quick_e2e.py`

- [ ] **Step 1: Write the test file**

Create `backend/tests/test_quick_e2e.py`:

```python
"""End-to-end scenarios for /api/quick/*.

Covers §5 spec test list:
  - happy path       (layer 1 → attest success)
  - hard fail        (OCR null → layer 3_hard → attest rejected)
  - soft fail        (too many missing fields → layer 3_soft → attest rejected)
  - pdf splitter     (2-page PDF → split_detected event or 2 drafts)

Uses mock OCR, so "hard fail" / "soft fail" are forced by monkeypatching
the OCR tool to return specific shapes.
"""
import os, tempfile, io, json

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP.name}")
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_quick_e2e")
os.makedirs("/tmp/concurshield_quick_e2e", exist_ok=True)

import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import Base, get_db
from backend.main import app
from backend.quick import pipeline as pipeline_mod

_engine = create_async_engine(f"sqlite+aiosqlite:///{_TMP.name}")
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():
    async with _Session() as s:
        yield s


def setup_module(_):
    import backend.config as cfg
    cfg.DATABASE_URL = f"sqlite+aiosqlite:///{_TMP.name}"
    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.get_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db


client = TestClient(app)
HEADERS = {"X-Mock-User-Id": "emp_e2e_1", "X-Mock-User-Role": "employee"}


def _upload_jpg() -> str:
    files = {"file": ("r.jpg", io.BytesIO(b"\xff\xd8\xff" + b"\x00"*100), "image/jpeg")}
    r = client.post("/api/quick/upload", files=files, headers=HEADERS)
    assert r.status_code == 201
    return r.json()["draft_id"]


def _stream_events(draft_id: str) -> list[dict]:
    r = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r.status_code == 200
    return [json.loads(line[6:]) for line in r.text.splitlines()
            if line.startswith("data: ")]


def test_happy_path_layer_1():
    draft_id = _upload_jpg()
    events = _stream_events(draft_id)
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    # Mock OCR returns high-conf data → should be layer 1 or 2
    assert ready["layer"] in ("1", "2")

    if ready["layer"] in ("1", "2"):
        r = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing"
        assert "id" in body


def test_hard_fail_returns_layer_3_hard(monkeypatch):
    async def _fake_ocr(args, ctx, db, draft_id):
        return {"error": "OCR failed: completely blank image"}
    monkeypatch.setattr(pipeline_mod, "tool_extract_receipt_fields", _fake_ocr)

    draft_id = _upload_jpg()
    events = _stream_events(draft_id)
    types = [e["type"] for e in events]
    assert "ocr_failed" in types
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] == "3_hard"

    r = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r.status_code == 422


def test_soft_fail_returns_layer_3_soft(monkeypatch):
    async def _fake_ocr(args, ctx, db, draft_id):
        # Amount only; merchant/date missing → 2 missing fields.
        # Project_code also missing (default) + low-conf classify → 4 total.
        return {
            "amount": 100.0, "merchant": None, "date": None,
            "confidence": 0.6,
        }
    async def _fake_classify(args, ctx, db, draft_id):
        return {"category": "other", "confidence": 0.3}

    monkeypatch.setattr(pipeline_mod, "tool_extract_receipt_fields", _fake_ocr)
    monkeypatch.setattr(pipeline_mod, "tool_suggest_category", _fake_classify)

    draft_id = _upload_jpg()
    events = _stream_events(draft_id)
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] == "3_soft"

    r = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r.status_code == 422
```

- [ ] **Step 2: Run the tests**

Run: `pytest backend/tests/test_quick_e2e.py -v`
Expected: 3 passed

If any fail because the mock-OCR data in `tool_extract_receipt_fields` doesn't align with the Layer 2/1 bounds, adjust the `_fake_ocr` calls to force the layer — don't weaken the assertions.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_quick_e2e.py
git commit -m "test: add e2e tests for quick flow happy/hard/soft paths"
```

---

## Task 10: PDF splitter integration into upload route

**Files:**
- Modify: `backend/api/routes/quick.py` (quick_upload handler)
- Modify: `backend/tests/test_quick_e2e.py` (add splitter test)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_quick_e2e.py`:

```python
import io as _io
from pypdf import PdfWriter as _PdfWriter


def _make_pdf(pages: int) -> bytes:
    w = _PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = _io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_multi_page_pdf_creates_multiple_drafts():
    pdf = _make_pdf(2)
    files = {"file": ("receipts.pdf", _io.BytesIO(pdf), "application/pdf")}
    r = client.post("/api/quick/upload", files=files, headers=HEADERS)
    assert r.status_code == 201
    body = r.json()
    # Contract: multi-page PDF returns a list of draft_ids
    assert "drafts" in body
    assert len(body["drafts"]) == 2
    for item in body["drafts"]:
        assert "draft_id" in item
```

- [ ] **Step 2: Run test, verify fail**

Run: `pytest backend/tests/test_quick_e2e.py::test_multi_page_pdf_creates_multiple_drafts -v`
Expected: FAIL — body has `draft_id` but no `drafts` list.

- [ ] **Step 3: Update quick_upload**

In `backend/api/routes/quick.py`, replace the `quick_upload` function body with:

```python
@router.post("/upload", status_code=201)
async def quick_upload(
    file: UploadFile = File(...),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    filename = file.filename or "receipt"

    # ── PDF detection and splitting ─────────────────────────────
    is_pdf = (file.content_type == "application/pdf"
              or filename.lower().endswith(".pdf"))
    if is_pdf:
        from backend.pdf_splitter import split, SplitError
        try:
            pages = split(content)
        except SplitError:
            # Corrupt PDF — treat as single hard-error upload
            pages = [content]
    else:
        pages = [content]

    if len(pages) > 1:
        drafts_info = []
        storage = get_storage()
        for idx, page_bytes in enumerate(pages):
            draft = await create_draft(db, ctx.user_id)
            draft.entry = "quick"
            await db.commit()
            # Wrap raw bytes in an UploadFile-like for storage.save
            from fastapi import UploadFile as _UF
            import io as _io
            fake = _UF(
                filename=f"{filename[:-4]}-p{idx+1}.pdf",
                file=_io.BytesIO(page_bytes),
            )
            receipt_url = await storage.save(fake, fake.filename)
            await update_draft_receipt(db, draft.id, receipt_url)
            drafts_info.append({"draft_id": draft.id, "receipt_url": receipt_url,
                                "page": idx + 1})
        return {"drafts": drafts_info}

    # ── Single-page path ────────────────────────────────────────
    draft = await create_draft(db, ctx.user_id)
    draft.entry = "quick"
    await db.commit()

    storage = get_storage()
    from fastapi import UploadFile as _UF
    import io as _io
    fake = _UF(filename=filename, file=_io.BytesIO(content))
    receipt_url = await storage.save(fake, fake.filename)
    await update_draft_receipt(db, draft.id, receipt_url)

    return {"draft_id": draft.id, "receipt_url": receipt_url}
```

Note: if `storage.save` requires a real `UploadFile`, inspect `backend/storage.py` and adapt accordingly — the goal is: each split PDF page becomes its own saved blob and its own draft.

- [ ] **Step 4: Fix earlier tests that read `draft_id` from upload response**

The single-file path still returns `draft_id`, so the earlier `_upload_jpg` helper continues to work. Verify:

Run: `pytest backend/tests/test_quick_api.py backend/tests/test_quick_e2e.py -v`
Expected: all pass including the new multi-page test.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/quick.py backend/tests/test_quick_e2e.py
git commit -m "feat: split multi-page PDFs into one draft per page on upload"
```

---

## Task 11: Frontend quick.html — skeleton + upload + SSE render

**Files:**
- Create: `frontend/employee/quick.html`

- [ ] **Step 1: Create the file**

Create `frontend/employee/quick.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>快速报销 — ExpenseFlow</title>
  <link rel="stylesheet" href="/shared/styles.css">
  <script>window.AUTH_MODE = "mock"; window.API_BASE = "";</script>
  <style>
    .quick-wrap { max-width: 600px; margin: 2rem auto; padding: 0 1rem; }
    .drop-zone {
      border: 2px dashed #cbd5e1; border-radius: 12px; padding: 3rem 1rem;
      text-align: center; color: #64748b; cursor: pointer;
      transition: all 0.2s;
    }
    .drop-zone:hover, .drop-zone.drag { border-color: #0f172a; background: #f8fafc; }
    .card {
      background: white; border: 1px solid #e2e8f0; border-radius: 12px;
      overflow: hidden; margin-top: 1rem;
    }
    .card-row { display: flex; }
    .thumb {
      width: 96px; height: 120px;
      background: linear-gradient(135deg, #ddd, #bbb);
      display: flex; align-items: center; justify-content: center;
      font-size: 2rem;
    }
    .card-meta { flex: 1; padding: 0.9rem 1rem; }
    .amount { font-size: 1.6rem; font-weight: 800; color: #0f172a; }
    .merchant { color: #475569; margin-top: 0.15rem; }
    .meta { color: #94a3b8; font-size: 0.8rem; margin-top: 0.2rem; }
    .badge {
      display: inline-block; padding: 0.15rem 0.4rem; border-radius: 4px;
      font-size: 0.7rem; font-weight: 600; margin-top: 0.4rem;
    }
    .badge-ok { background: #dcfce7; color: #166534; }
    .badge-pending { background: #f1f5f9; color: #64748b; }
    .badge-warn { background: #fef3c7; color: #92400e; }
    .card-footer {
      padding: 0.6rem 1rem; background: #fafafa;
      border-top: 1px solid #f1f5f9;
    }
    .btn-attest {
      width: 100%; background: #0f172a; color: white;
      padding: 0.7rem; border-radius: 8px; border: none;
      font-weight: 600; font-size: 1rem; cursor: pointer;
    }
    .btn-attest:disabled { background: #cbd5e1; cursor: not-allowed; }
    .chip {
      display: inline-block; padding: 0.15rem 0.4rem;
      background: #fef3c7; color: #92400e; border-radius: 4px;
      font-size: 0.75rem; cursor: pointer; margin-right: 0.3rem;
    }
    .chip.done { background: #dcfce7; color: #166534; }
    .muted { color: #94a3b8; font-size: 0.75rem; }
    .ask-row {
      display: flex; gap: 0.4rem; padding: 0.5rem 1rem;
      border-top: 1px dashed #f1f5f9;
    }
    .ask-row input { flex: 1; border: none; outline: none;
                     background: transparent; font-size: 0.8rem; }
  </style>
</head>
<body>
<div id="nav-root"></div>
<script src="/shared/i18n.js"></script>
<script src="/shared/auth.js"></script>
<script src="/shared/nav.js"></script>

<main class="quick-wrap">
  <h1 style="font-size:1.3rem;margin:0 0 1rem">快速报销</h1>
  <p class="muted">拍一张 / 拖一张发票上来,AI 帮你把字段填好,你只需要确认提交。</p>

  <div id="drop-zone" class="drop-zone">
    <div style="font-size:2.5rem">📄</div>
    <div>点击或拖拽发票图 / PDF 到这里</div>
    <input id="file-input" type="file" accept="image/*,application/pdf" style="display:none">
  </div>

  <div id="cards"></div>

  <p style="margin-top:2rem;text-align:center;font-size:.8rem">
    <a href="/employee/submit.html" class="muted">或者直接手动填表 →</a>
  </p>
</main>

<script>
const dz = document.getElementById("drop-zone");
const fi = document.getElementById("file-input");
const cardsEl = document.getElementById("cards");

dz.onclick = () => fi.click();
fi.onchange = () => fi.files[0] && handleUpload(fi.files[0]);
dz.ondragover = e => { e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave = () => dz.classList.remove("drag");
dz.ondrop = e => {
  e.preventDefault(); dz.classList.remove("drag");
  if (e.dataTransfer.files[0]) handleUpload(e.dataTransfer.files[0]);
};

async function handleUpload(file) {
  cardsEl.innerHTML = "";
  const fd = new FormData();
  fd.append("file", file);

  const r = await fetch("/api/quick/upload", {
    method: "POST", body: fd,
    headers: window.__authHeaders ? window.__authHeaders() : {},
  });
  if (!r.ok) { alert("上传失败: " + r.status); return; }
  const body = await r.json();

  // Multi-page PDF path
  if (body.drafts) {
    for (const d of body.drafts) renderCardShell(d.draft_id, d.receipt_url);
    for (const d of body.drafts) streamPipeline(d.draft_id);
  } else {
    renderCardShell(body.draft_id, body.receipt_url);
    streamPipeline(body.draft_id);
  }
}

function renderCardShell(draftId, receiptUrl) {
  const div = document.createElement("div");
  div.className = "card";
  div.id = "card-" + draftId;
  div.innerHTML = `
    <div class="card-row">
      <div class="thumb">🧾</div>
      <div class="card-meta">
        <div class="amount" data-field="amount">— — —</div>
        <div class="merchant" data-field="merchant">识别中...</div>
        <div class="meta" data-field="meta"></div>
        <div class="badge badge-pending" data-field="badge">⏳ 处理中</div>
      </div>
    </div>
    <div class="card-footer">
      <button class="btn-attest" disabled>提交</button>
    </div>
    <div class="ask-row">
      <input placeholder="问 AI（仅限这张票）..." data-field="ask">
    </div>
  `;
  cardsEl.appendChild(div);
}

function streamPipeline(draftId) {
  const card = document.getElementById("card-" + draftId);
  const es = new EventSource(`/api/quick/stream/${draftId}`);
  let layer = null;

  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    handleEvent(card, ev, draftId);
    if (ev.type === "card_ready") {
      layer = ev.layer;
      finalizeCard(card, layer, draftId);
      es.close();
    } else if (ev.type === "ocr_failed") {
      // redirect already triggered by card_ready=3_hard; nothing to do
    }
  };
  es.onerror = () => es.close();
}

function handleEvent(card, ev, draftId) {
  if (ev.type === "ocr_done") {
    card.querySelector('[data-field="amount"]').textContent = "¥" + (ev.amount || "—");
    card.querySelector('[data-field="merchant"]').textContent =
      (ev.merchant || "—") + " · ——";
  } else if (ev.type === "classify_done") {
    const merchant = card.querySelector('[data-field="merchant"]').textContent.split(" · ")[0];
    card.querySelector('[data-field="merchant"]').textContent =
      merchant + " · " + (ev.category || "—");
  } else if (ev.type === "budget_done") {
    const meta = card.querySelector('[data-field="meta"]');
    meta.textContent = ev.signal === "ok" ? "预算 ok" : "⚠ 预算 " + ev.signal;
  }
}

function finalizeCard(card, layer, draftId) {
  const badge = card.querySelector('[data-field="badge"]');
  const btn = card.querySelector('.btn-attest');

  if (layer === "3_hard") {
    window.location.href = `/employee/submit.html?from=quick&draft_id=${draftId}`;
    return;
  }
  if (layer === "3_soft") {
    badge.className = "badge badge-warn";
    badge.textContent = "⚠ 需完整填写";
    card.querySelector('.card-footer').innerHTML = `
      <button class="btn-attest" onclick="goManual('${draftId}')">手动填表</button>
      <button class="btn-attest" style="background:#64748b;margin-top:.4rem"
              onclick="resetUpload()">重拍</button>
    `;
    return;
  }

  badge.className = "badge badge-ok";
  badge.textContent = "✓ AI 已核对";
  btn.disabled = false;
  btn.onclick = () => attest(draftId, btn);

  if (layer === "2") {
    // Task 12 will add inline chips for editable fields
    badge.className = "badge badge-warn";
    badge.textContent = "⚠ 需 1 项确认";
  }
}

function goManual(draftId) {
  window.location.href = `/employee/submit.html?from=quick&draft_id=${draftId}`;
}
function resetUpload() {
  cardsEl.innerHTML = "";
  fi.value = "";
}

async function attest(draftId, btn) {
  btn.disabled = true;
  btn.textContent = "提交中...";
  const r = await fetch(`/api/quick/attest/${draftId}`, {
    method: "POST",
    headers: window.__authHeaders ? window.__authHeaders() : {},
  });
  if (!r.ok) {
    alert("提交失败: " + r.status);
    btn.disabled = false;
    btn.textContent = "提交";
    return;
  }
  btn.textContent = "✓ 已提交";
  setTimeout(() => window.location.href = "/employee/my-reports.html", 1200);
}
</script>
</body>
</html>
```

- [ ] **Step 2: Start the dev server and manually verify happy path**

Run: `uvicorn backend.main:app --reload --port 8000`

Open http://localhost:8000/employee/quick.html in Chrome. Upload a test image (any JPG). Verify:
1. Drop zone accepts the file
2. Card appears
3. Within ~3s amount + merchant + category + budget populate
4. Badge turns green `✓ AI 已核对`
5. Click "提交" → redirects to my-reports after ~1s

If any step fails, check the browser console and the uvicorn log.

- [ ] **Step 3: Commit**

```bash
git add frontend/employee/quick.html
git commit -m "feat: add quick.html frontend with SSE card rendering"
```

---

## Task 12: Frontend — Layer 2 inline editing (chips)

**Files:**
- Modify: `frontend/employee/quick.html`

- [ ] **Step 1: Add chip handling in finalizeCard**

In the `<script>` block of `quick.html`, replace the Layer 2 branch of `finalizeCard` with real chip rendering. The full updated branch:

```javascript
  if (layer === "2") {
    // Render inline chips for missing optional fields.
    // v1: project_code is the only optional field auto-recognized as missing.
    badge.className = "badge badge-warn";
    badge.textContent = "⚠ 需 1 项确认";

    const meta = card.querySelector('[data-field="meta"]');
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = '请选项目 ▾';
    chip.onclick = () => pickProject(draftId, chip, btn);
    meta.appendChild(document.createTextNode(' · '));
    meta.appendChild(chip);

    btn.disabled = true;  // until chip resolves
  } else {
    badge.className = "badge badge-ok";
    badge.textContent = "✓ AI 已核对";
    btn.disabled = false;
  }
  btn.onclick = () => attest(draftId, btn);
```

And add a new helper function in the same `<script>` block:

```javascript
async function pickProject(draftId, chip, btn) {
  // v1 shortcut: prompt for a code. Future: dropdown pulled from /api/projects.
  const code = prompt("请输入项目代码（如 PROJ-042）:");
  if (!code) return;

  const r = await fetch(`/api/chat/drafts/${draftId}/field`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(window.__authHeaders ? window.__authHeaders() : {}),
    },
    body: JSON.stringify({ field: "project_code", value: code, source: "user" }),
  });
  if (!r.ok) { alert("保存失败"); return; }
  chip.textContent = "✓ " + code;
  chip.classList.add("done");
  btn.disabled = false;

  // Update badge
  const card = btn.closest(".card");
  const badge = card.querySelector('[data-field="badge"]');
  badge.className = "badge badge-ok";
  badge.textContent = "✓ AI 已核对";
}
```

> Note: if `/api/chat/drafts/:id/field` doesn't exist, check `backend/api/routes/chat.py` around the draft routes — you may need to add a PATCH field endpoint in this task, or use an existing one. Look for `update_draft_field` references. If neither exists, add this simple endpoint to `chat.py`:

```python
class PatchFieldBody(BaseModel):
    field: str
    value: Any
    source: str = "user"


@router.patch("/drafts/{draft_id}/field")
async def patch_draft_field(
    draft_id: str,
    body: PatchFieldBody,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    draft = await get_draft(db, draft_id)
    if not draft or draft.employee_id != ctx.user_id:
        raise HTTPException(status_code=404)
    await store_update_draft_field(db, draft_id, body.field, body.value, body.source)
    return {"ok": True}
```

- [ ] **Step 2: Manual verification**

Start the dev server. Upload a receipt. If Layer 2 triggers (it will for any upload where project_code is missing — which is most), click the yellow chip, enter `PROJ-042`, verify:
- Chip turns green
- Button activates
- Attest works

- [ ] **Step 3: Commit**

```bash
git add frontend/employee/quick.html backend/api/routes/chat.py
git commit -m "feat: add Layer 2 inline chip editing in quick.html"
```

---

## Task 13: Frontend — Inline chat row (scoped to draft)

**Files:**
- Modify: `frontend/employee/quick.html`

- [ ] **Step 1: Wire the ask input**

In `renderCardShell`, after appending the card, add this at the end of `handleUpload` (or inside a new function called from `renderCardShell`):

Add inside the `<script>` block, after `renderCardShell`:

```javascript
function wireAskRow(draftId) {
  const card = document.getElementById("card-" + draftId);
  const input = card.querySelector('[data-field="ask"]');
  let history = [];

  input.onkeydown = async (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    const q = input.value.trim();
    input.value = "";
    input.disabled = true;

    // Bubble above input
    const bubble = document.createElement("div");
    bubble.style.cssText = "padding:.4rem .8rem;color:#475569;font-size:.75rem;background:#f8fafc";
    bubble.textContent = "你: " + q;
    card.querySelector(".ask-row").insertAdjacentElement("beforebegin", bubble);

    const reply = document.createElement("div");
    reply.style.cssText = bubble.style.cssText + ";color:#0f172a";
    reply.textContent = "AI: 正在思考...";
    card.querySelector(".ask-row").insertAdjacentElement("beforebegin", reply);

    // Call existing chat stream endpoint, scoped by draft_id
    const r = await fetch(`/api/chat/drafts/${draftId}/message`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(window.__authHeaders ? window.__authHeaders() : {}),
      },
      body: JSON.stringify({ message: q }),
    });
    // For v1, simplified: buffer full stream then display the final assistant_text
    const text = await r.text();
    const lines = text.split("\n").filter(l => l.startsWith("data: "));
    const events = lines.map(l => { try { return JSON.parse(l.slice(6)); } catch { return null; } }).filter(Boolean);
    const last = events.filter(e => e.type === "assistant_text").pop();
    reply.textContent = "AI: " + (last ? last.text : "(无响应)");

    input.disabled = false;
    input.focus();
  };
}
```

And call `wireAskRow(draftId)` after `renderCardShell(body.draft_id, ...)` and at the end of the multi-draft branch in `handleUpload`.

- [ ] **Step 2: Manual verification**

Upload a receipt. In the ask row, type `这笔算差旅还是招待?` and press Enter. Verify a reply bubble appears (even if the answer is generic — v1 just needs the loop to work).

- [ ] **Step 3: Commit**

```bash
git add frontend/employee/quick.html
git commit -m "feat: add inline chat row scoped to the current draft"
```

---

## Task 14: submit.html — 「← 返回 quick」 button

**Files:**
- Modify: `frontend/employee/submit.html`

- [ ] **Step 1: Add button at top of `<main>`**

In `frontend/employee/submit.html`, immediately inside `<main class="page">` add:

```html
<div style="margin-bottom:1rem">
  <a href="/employee/quick.html" style="color:#64748b;font-size:.85rem;text-decoration:none">← 返回快速报销</a>
</div>
```

Do not touch anything else in submit.html.

- [ ] **Step 2: Manual verification**

Open `/employee/submit.html`. Verify the link is visible in the top-left of the main area and clicking it navigates to `quick.html`.

- [ ] **Step 3: Commit**

```bash
git add frontend/employee/submit.html
git commit -m "feat: add return-to-quick link at top of submit.html"
```

---

## Task 15: Manual verification + final regression run

**Files:** none modified.

- [ ] **Step 1: Run full backend test suite**

Run: `pytest backend/tests/ -v`
Expected: all tests pass. Address any regressions before proceeding.

- [ ] **Step 2: Manual frontend checklist**

Start the dev server: `uvicorn backend.main:app --reload --port 8000`

Walk through each item from spec §5 "前端手工验证清单":

1. [ ] Clean receipt upload → card ready in ≤ 3s, all fields filled, badge green, attest succeeds
2. [ ] Missing project upload → Layer 2 yellow chip shown, clicking chip + entering code activates button
3. [ ] Force hard fail (upload a plain white image or intercept OCR to return error) → page redirects to `/employee/submit.html?from=quick&draft_id=...` with the return button visible
4. [ ] 2-page PDF → two cards rendered vertically, each with independent pipeline and attest
5. [ ] Inline chat row → typing a question produces a reply bubble; does not reload the page
6. [ ] Attest button is grey and disabled before `card_ready` event arrives

For each failure, fix and re-run the specific scenario. Do NOT claim completion if any item fails.

- [ ] **Step 3: Commit any small fixes, then finish**

If manual verification surfaced small issues, commit them with descriptive messages. Then the implementation is complete.

Run:
```bash
git log --oneline | head -20
```

Expected: 15+ feature/test/refactor commits telling a clean story from "add pypdf" through "return-to-quick link".

---

## Summary

| # | Task | Deliverable |
|---|---|---|
| 1 | pypdf dep | requirements.txt |
| 2 | PDF splitter | backend/pdf_splitter.py + tests |
| 3 | Layer decision | backend/quick/layer_decision.py + tests |
| 4 | Draft schema | layer/entry columns |
| 5 | Telemetry | TelemetryEvent + insert_telemetry |
| 6 | Finalize helper | backend/quick/finalize.py (refactor chat.py) |
| 7 | Quick pipeline | backend/quick/pipeline.py + tests |
| 8 | Quick routes | /api/quick/{upload,stream,attest} |
| 9 | E2E tests | happy / hard / soft |
| 10 | Splitter integration | multi-page PDF → N drafts |
| 11 | quick.html | skeleton + upload + SSE render |
| 12 | quick.html L2 | inline chips |
| 13 | quick.html chat | scoped ask row |
| 14 | submit.html | return-to-quick link |
| 15 | Verification | full suite + manual checklist |
