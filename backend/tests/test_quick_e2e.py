"""End-to-end tests for the quick flow (happy / hard-fail / soft-fail)."""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_quick_e2e")
os.makedirs("/tmp/concurshield_quick_e2e", exist_ok=True)

import asyncio
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import Base, get_db
from backend.main import app
from backend.storage import LocalStorage, get_storage

_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)
_test_storage = LocalStorage(base_dir=Path("/tmp/concurshield_quick_e2e"))


async def _override_get_db():
    async with _Session() as s:
        yield s


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: _test_storage


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_storage, None)


client = TestClient(app)
HEADERS = {"X-User-Id": "emp_e2e_1", "X-User-Role": "employee"}


def _fake_jpeg() -> io.BytesIO:
    return io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 100)


def _parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ── Tests ─────────────────────────────────────────────────────────

def test_happy_path_layer_1(monkeypatch):
    async def fake_ocr(args, ctx, db, draft_id):
        return {
            "amount": 150.00,
            "merchant": "海底捞火锅",
            "date": "2026-04-10",
            "currency": "CNY",
            "tax_amount": 9.00,
            "invoice_number": "12345678",
            "confidence": 0.95,
        }

    async def fake_classify(args, ctx, db, draft_id):
        return {"category": "meal", "confidence": 0.92}

    async def fake_dedupe(args, ctx, db, draft_id):
        return {"is_duplicate": False}

    async def fake_budget(args, ctx, db, draft_id):
        return {"signal": "ok"}

    monkeypatch.setattr(
        "backend.quick.pipeline.tool_extract_receipt_fields", fake_ocr,
    )
    monkeypatch.setattr(
        "backend.quick.pipeline.tool_suggest_category", fake_classify,
    )
    monkeypatch.setattr(
        "backend.quick.pipeline.tool_check_duplicate_invoice", fake_dedupe,
    )
    monkeypatch.setattr(
        "backend.quick.pipeline.tool_get_budget_summary", fake_budget,
    )

    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    assert r1.status_code == 201, r1.text
    draft_id = r1.json()["draft_id"]

    r2 = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r2.status_code == 200
    events = _parse_sse(r2.text)
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] in ("1", "2")

    r3 = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["status"] == "processing"
    assert "id" in body


def test_hard_fail_returns_layer_3_hard(monkeypatch):
    async def fake_ocr(args, ctx, db, draft_id):
        return {"error": "OCR failed: completely blank image"}

    monkeypatch.setattr(
        "backend.quick.pipeline.tool_extract_receipt_fields", fake_ocr,
    )

    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]

    r2 = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r2.status_code == 200
    events = _parse_sse(r2.text)
    types = [e["type"] for e in events]
    assert "ocr_failed" in types
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] == "3_hard"

    r3 = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r3.status_code == 422


def test_soft_fail_returns_layer_3_soft(monkeypatch):
    async def fake_ocr(args, ctx, db, draft_id):
        # Only amount present; merchant/date/invoice_number missing.
        return {
            "amount": 42.0,
            "merchant": None,
            "date": None,
            "invoice_number": None,
            "tax_amount": None,
            "currency": "USD",
            "confidence": 0.6,
        }

    async def fake_classify(args, ctx, db, draft_id):
        return {"category": "other", "confidence": 0.3}

    monkeypatch.setattr(
        "backend.quick.pipeline.tool_extract_receipt_fields", fake_ocr,
    )
    monkeypatch.setattr(
        "backend.quick.pipeline.tool_suggest_category", fake_classify,
    )

    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]

    r2 = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r2.status_code == 200
    events = _parse_sse(r2.text)
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] == "3_soft"

    r3 = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r3.status_code == 422
