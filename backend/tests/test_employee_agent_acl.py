"""Data-level ACL tests for the unified `employee` chat agent.

The Concur-style design has two security layers:

  (A) Dispatcher whitelist — if the LLM hallucinates a tool outside the
      `employee` role's TOOL_REGISTRY entry, dispatch is blocked at the
      routing layer.
  (B) Tool-internal ACL — every WRITE tool re-checks ownership + state
      + field against ctx.user_id and the target object BEFORE any DB
      mutation. A prompt-injected LLM that passes another user's
      line_id or an already-submitted report's line_id still gets
      rejected inside the tool.

This file covers both layers. For each test we use an InjectedLLM that
deterministically makes one specific tool call (simulating a successful
prompt injection on the LLM), then assert the resulting tool_result
contains the expected rejection.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import date

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_acl_test")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi.testclient import TestClient

from backend.db.store import (
    Base, create_report, create_submission, get_db, set_report_status,
)
from backend.main import app


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():
    async with _Session() as session:
        yield session


# Test fixture IDs — set inside setup_module so assertions can reference
_FIXTURES: dict[str, str] = {}


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _seed():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _Session() as s:
            # Owner (the user making chat requests) — has an OPEN report
            owner_open = await create_report(s, "emp-owner", title="Owner's Open Report")
            owner_open_sub = await create_submission(s, {
                "employee_id": "emp-owner",
                "status": "in_report",
                "amount": 100.0, "currency": "CNY",
                "category": "meal", "date": date.today().isoformat(),
                "merchant": "Owner Merchant A",
                "receipt_url": "/tmp/a.jpg",
                "invoice_number": "ACL00000001",
                "report_id": owner_open.id,
            })
            _FIXTURES["owner_open_report_id"] = owner_open.id
            _FIXTURES["owner_open_sub_id"] = owner_open_sub.id

            # Owner has a PENDING report (submitted, awaiting manager)
            owner_pending = await create_report(s, "emp-owner", title="Owner's Pending Report")
            owner_pending_sub = await create_submission(s, {
                "employee_id": "emp-owner",
                "status": "pending",
                "amount": 200.0, "currency": "CNY",
                "category": "transport", "date": date.today().isoformat(),
                "merchant": "Owner Merchant B",
                "receipt_url": "/tmp/b.jpg",
                "invoice_number": "ACL00000002",
                "report_id": owner_pending.id,
            })
            await set_report_status(s, owner_pending.id, "pending")
            _FIXTURES["owner_pending_sub_id"] = owner_pending_sub.id

            # A DIFFERENT employee's open report — owner must not be able to edit this
            stranger_open = await create_report(s, "emp-stranger", title="Stranger's Report")
            stranger_sub = await create_submission(s, {
                "employee_id": "emp-stranger",
                "status": "in_report",
                "amount": 300.0, "currency": "CNY",
                "category": "meal", "date": date.today().isoformat(),
                "merchant": "Stranger Merchant",
                "receipt_url": "/tmp/c.jpg",
                "invoice_number": "ACL00000003",
                "report_id": stranger_open.id,
            })
            _FIXTURES["stranger_sub_id"] = stranger_sub.id

    asyncio.new_event_loop().run_until_complete(_seed())
    app.dependency_overrides[get_db] = _override_get_db


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass  # Windows: temp file still locked, OS Temp cleanup handles it


client = TestClient(app)
HEADERS = {"X-User-Id": "emp-owner", "X-User-Role": "employee"}


def _parse_sse(raw: str) -> list[dict]:
    events = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _injected_llm(tool_name: str, tool_input: dict):
    """Return an InjectedLLM class that fires one specific tool_call,
    then ends the turn. Simulates a successfully prompt-injected LLM."""
    from backend.api.routes import chat as chat_mod

    class _InjectedLLM(chat_mod.BaseLLM):
        _called = False
        async def next_turn(self, messages, tools, agent_role="employee"):
            if not _InjectedLLM._called:
                _InjectedLLM._called = True
                return chat_mod.LLMResponse(
                    text="",
                    tool_calls=[{
                        "id": "tool_acl_test",
                        "name": tool_name,
                        "input": tool_input,
                    }],
                    stop_reason="tool_use",
                )
            return chat_mod.LLMResponse(text="done", stop_reason="end_turn")

    return _InjectedLLM


def _run_with_injection(tool_name: str, tool_input: dict) -> dict:
    """Post a dummy message through /api/chat/message with a monkey-
    patched LLM that injects the given tool call. Return the single
    tool_result dict."""
    from backend.api.routes import chat as chat_mod

    orig = chat_mod.get_llm
    chat_mod.get_llm = lambda: _injected_llm(tool_name, tool_input)()
    try:
        resp = client.post(
            "/api/chat/message",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "probe"}]},
        )
    finally:
        chat_mod.get_llm = orig

    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == 1, f"expected 1 tool_result, got {len(tool_results)}"
    return tool_results[0]


# ═══════════════════════════════════════════════════════════════════════
# Layer A: dispatcher whitelist
# ═══════════════════════════════════════════════════════════════════════

def test_employee_role_cannot_dispatch_update_draft_field():
    """`update_draft_field` is submit-flow only — the unified `employee`
    role's whitelist does not include it. A hallucinated call is blocked
    at dispatch, before the tool handler runs."""
    tr = _run_with_injection(
        "update_draft_field",
        {"field": "amount", "value": "999999", "source": "ocr"},
    )
    assert tr["name"] == "update_draft_field"
    assert "error" in tr["result"]
    assert "not allowed" in tr["result"]["error"]
    assert tr["result"]["error"].endswith("'employee'")


def test_employee_role_cannot_dispatch_extract_receipt_fields():
    """`extract_receipt_fields` is also submit-flow only."""
    tr = _run_with_injection("extract_receipt_fields", {})
    assert "error" in tr["result"]
    assert "not allowed" in tr["result"]["error"]


# ═══════════════════════════════════════════════════════════════════════
# Layer B: tool-internal ACL on update_report_line_field
# ═══════════════════════════════════════════════════════════════════════

def test_cannot_edit_another_users_line():
    """Even with a valid line_id, editing someone else's report is
    rejected by the owner check inside the tool."""
    tr = _run_with_injection(
        "update_report_line_field",
        {
            "line_id": _FIXTURES["stranger_sub_id"],
            "field": "amount",
            "value": "1",
        },
    )
    assert tr["name"] == "update_report_line_field"
    assert "error" in tr["result"]
    assert "非本人" in tr["result"]["error"] or "无权" in tr["result"]["error"]


def test_cannot_edit_line_in_pending_report():
    """Owner is correct, but report is `pending` (in the manager's
    queue) — state check inside the tool rejects the edit."""
    tr = _run_with_injection(
        "update_report_line_field",
        {
            "line_id": _FIXTURES["owner_pending_sub_id"],
            "field": "amount",
            "value": "1",
        },
    )
    assert tr["name"] == "update_report_line_field"
    assert "error" in tr["result"]
    assert "pending" in tr["result"]["error"] or "不可编辑" in tr["result"]["error"]


def test_cannot_edit_non_whitelisted_field():
    """Field-level ACL: even on an editable report, `employee_id` is
    not in EDITABLE_FIELDS, so the edit is rejected."""
    tr = _run_with_injection(
        "update_report_line_field",
        {
            "line_id": _FIXTURES["owner_open_sub_id"],
            "field": "employee_id",  # not in EDITABLE_FIELDS
            "value": "emp-attacker",
        },
    )
    assert tr["name"] == "update_report_line_field"
    assert "error" in tr["result"]
    assert "不可编辑" in tr["result"]["error"] or "not editable" in tr["result"]["error"].lower()


def test_cannot_edit_nonexistent_line():
    """Bogus line_id returns the standard 'not found' error, not a
    server crash."""
    tr = _run_with_injection(
        "update_report_line_field",
        {
            "line_id": "00000000-0000-0000-0000-000000000000",
            "field": "amount",
            "value": "1",
        },
    )
    assert "error" in tr["result"]
    assert "不存在" in tr["result"]["error"]


def test_legitimate_edit_on_own_open_report_succeeds():
    """Positive control: owner, open report, whitelisted field → works."""
    tr = _run_with_injection(
        "update_report_line_field",
        {
            "line_id": _FIXTURES["owner_open_sub_id"],
            "field": "category",
            "value": "meal",
        },
    )
    assert tr["name"] == "update_report_line_field"
    assert tr["result"].get("ok") is True
    assert tr["result"]["field"] == "category"
    assert tr["result"]["value"] == "meal"
