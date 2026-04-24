"""Route-level tests for /api/quick/*."""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_quick_api_test")
os.makedirs("/tmp/concurshield_quick_api_test", exist_ok=True)

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
_test_storage = LocalStorage(base_dir=Path("/tmp/concurshield_quick_api_test"))


async def _override_get_db():
    async with _Session() as s:
        yield s


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = lambda: _test_storage


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_storage, None)


client = TestClient(app)
HEADERS = {"X-User-Id": "emp_quick_1", "X-User-Role": "employee"}


def _fake_jpeg() -> io.BytesIO:
    return io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 100)


# ── Tests ─────────────────────────────────────────────────────────

def test_upload_returns_draft_id():
    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r = client.post("/api/quick/upload", files=files, headers=HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "draft_id" in body


def test_stream_emits_card_ready():
    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]

    r2 = client.get(f"/api/quick/stream/{draft_id}", headers=HEADERS)
    assert r2.status_code == 200
    events = [
        json.loads(line[6:])
        for line in r2.text.splitlines()
        if line.startswith("data: ")
    ]
    types = [e["type"] for e in events]
    assert "card_ready" in types
    ready = [e for e in events if e["type"] == "card_ready"][-1]
    assert ready["layer"] in ("1", "2", "3_soft", "3_hard")


def test_attest_rejects_layer_3():
    files = {"file": ("r.jpg", _fake_jpeg(), "image/jpeg")}
    r1 = client.post("/api/quick/upload", files=files, headers=HEADERS)
    draft_id = r1.json()["draft_id"]

    async def _force():
        async with _Session() as db:
            from backend.db.store import get_draft
            d = await get_draft(db, draft_id)
            d.layer = "3_hard"
            await db.commit()

    asyncio.new_event_loop().run_until_complete(_force())

    r3 = client.post(f"/api/quick/attest/{draft_id}", headers=HEADERS)
    assert r3.status_code == 422
