"""Tests for the trace-review workflow (B2).

Verifies that the new LLMTrace columns + endpoints support the Hamel
"always be looking at data" loop:

  - PATCH /api/eval/traces/{id}/review marks a trace reviewed
  - GET   /api/eval/traces?reviewed=false filters to inbox
  - GET   /api/eval/traces?failure_mode_tag=foo filters to failure mode
  - GET   /api/eval/saturation?component=X gives the saturation snapshot
    (total / reviewed / unreviewed / correct + by_failure_mode)
"""
from __future__ import annotations

import asyncio
import os
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("EVAL_DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_trace_review_test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.db.store import (
    Base, EvalBase, LLMTrace, get_db, get_eval_db,
    mark_trace_reviewed, saturation_summary,
)
from backend.main import app


_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


async def _override_get_db():
    async with _Session() as session:
        yield session


def setup_module(_):
    import backend.config as _cfg
    _cfg.DATABASE_URL = _DB_URL
    _cfg.EVAL_DATABASE_URL = _DB_URL

    async def _init():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(EvalBase.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())
    # Both get_db and get_eval_db point at the same in-process DB so the
    # endpoint can write/read traces in tests without spinning up two engines.
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_eval_db] = _override_get_db


def teardown_module(_):
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_eval_db, None)
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass


client = TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────


async def _seed_trace(component: str, model: str = "gpt-4o-mini", **extra) -> str:
    async with _Session() as db:
        t = LLMTrace(
            component=component,
            model=model,
            prompt={"messages": [{"role": "user", "content": "hi"}]},
            response="ok",
            **extra,
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        return t.id


# ── CRUD-level tests (function under test, no HTTP) ──────────────────


@pytest.mark.asyncio
async def test_mark_trace_reviewed_sets_all_fields():
    tid = await _seed_trace("ambiguity_detector")
    async with _Session() as db:
        updated = await mark_trace_reviewed(
            db, tid,
            reviewed_by="alice",
            failure_mode_tag="wrong_attribution",
            notes="AI flagged amount but the real issue was vendor",
        )
        assert updated is not None
        assert updated.reviewed_by == "alice"
        assert updated.failure_mode_tag == "wrong_attribution"
        assert updated.review_notes.startswith("AI flagged")
        assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_mark_trace_reviewed_correct_uses_empty_string_tag():
    """Hamel: 'reviewed and correct' is a distinct state from 'unreviewed'.
    We use empty-string tag to mean 'reviewed, no failure mode'."""
    tid = await _seed_trace("fraud_rule_11")
    async with _Session() as db:
        updated = await mark_trace_reviewed(db, tid, reviewed_by="bob")
        assert updated.failure_mode_tag == ""    # not None
        assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_mark_trace_reviewed_unknown_id_returns_none():
    async with _Session() as db:
        result = await mark_trace_reviewed(
            db, "nonexistent-id-xxx", reviewed_by="alice",
        )
        assert result is None


# ── Saturation summary ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_saturation_counts_total_reviewed_and_unreviewed():
    component = "saturation_test_component_a"
    # 5 unreviewed
    for _ in range(5):
        await _seed_trace(component)
    # 3 reviewed correct
    for _ in range(3):
        tid = await _seed_trace(component)
        async with _Session() as db:
            await mark_trace_reviewed(db, tid, reviewed_by="alice")
    # 2 reviewed with failure_mode_tag
    for tag in ["wrong_attribution", "wrong_attribution"]:
        tid = await _seed_trace(component)
        async with _Session() as db:
            await mark_trace_reviewed(
                db, tid, reviewed_by="alice", failure_mode_tag=tag,
            )
    # 1 reviewed with a different tag
    tid = await _seed_trace(component)
    async with _Session() as db:
        await mark_trace_reviewed(
            db, tid, reviewed_by="alice", failure_mode_tag="style_only",
        )

    async with _Session() as db:
        out = await saturation_summary(db, component=component)

    assert out["component"] == component
    assert out["total"] == 11
    assert out["reviewed"] == 6
    assert out["unreviewed"] == 5
    assert out["correct"] == 3
    assert out["by_failure_mode"] == {
        "wrong_attribution": 2,
        "style_only": 1,
    }


@pytest.mark.asyncio
async def test_saturation_isolated_per_component():
    """Counts must NOT leak across components."""
    await _seed_trace("isolation_a")
    await _seed_trace("isolation_b")
    await _seed_trace("isolation_b")
    async with _Session() as db:
        a = await saturation_summary(db, component="isolation_a")
        b = await saturation_summary(db, component="isolation_b")
    assert a["total"] == 1
    assert b["total"] == 2


# ── HTTP-level tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_review_endpoint_marks_trace():
    tid = await _seed_trace("api_test_component")
    r = client.patch(
        f"/api/eval/traces/{tid}/review",
        json={
            "reviewed_by": "alice",
            "failure_mode_tag": "false_positive",
            "notes": "policy actually allows this expense",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reviewed_by"] == "alice"
    assert body["failure_mode_tag"] == "false_positive"
    assert body["reviewed_at"] is not None


def test_patch_review_endpoint_404_on_unknown_trace():
    r = client.patch(
        "/api/eval/traces/nonexistent/review",
        json={"reviewed_by": "alice"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_traces_filter_by_reviewed_state():
    component = "filter_test_component"
    # 3 unreviewed + 2 reviewed-correct + 1 reviewed-with-tag
    ids_unreviewed = [await _seed_trace(component) for _ in range(3)]
    ids_correct = [await _seed_trace(component) for _ in range(2)]
    id_tagged = await _seed_trace(component)
    async with _Session() as db:
        for tid in ids_correct:
            await mark_trace_reviewed(db, tid, reviewed_by="alice")
        await mark_trace_reviewed(
            db, id_tagged, reviewed_by="alice", failure_mode_tag="missed_factor",
        )

    # ?reviewed=false → 3
    r = client.get(f"/api/eval/traces?component={component}&reviewed=false")
    assert r.status_code == 200
    assert r.json()["total"] == 3

    # ?reviewed=true → 3
    r = client.get(f"/api/eval/traces?component={component}&reviewed=true")
    assert r.json()["total"] == 3

    # ?failure_mode_tag=missed_factor → 1
    r = client.get(
        f"/api/eval/traces?component={component}&failure_mode_tag=missed_factor"
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == id_tagged

    # ?failure_mode_tag= (empty) → 2 (reviewed and correct)
    r = client.get(f"/api/eval/traces?component={component}&failure_mode_tag=")
    assert r.json()["total"] == 2


@pytest.mark.asyncio
async def test_saturation_endpoint_returns_breakdown():
    component = "endpoint_saturation_test"
    for _ in range(2):
        await _seed_trace(component)
    tid = await _seed_trace(component)
    async with _Session() as db:
        await mark_trace_reviewed(
            db, tid, reviewed_by="alice", failure_mode_tag="wrong_attribution",
        )

    r = client.get(f"/api/eval/saturation?component={component}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert body["reviewed"] == 1
    assert body["unreviewed"] == 2
    assert body["correct"] == 0
    assert body["by_failure_mode"] == {"wrong_attribution": 1}


def test_saturation_endpoint_requires_component_param():
    """Saturation is per-component by definition; no component = 422."""
    r = client.get("/api/eval/saturation")
    assert r.status_code == 422
