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
        await update_draft_receipt(db, draft.id, "/uploads/stub.jpg")

        ctx = UserContext(user_id="emp_pipe_1", roles=["employee"])
        events = []
        async for ev in run_quick_pipeline(draft.id, ctx, db):
            events.append(ev)

        types = [e["type"] for e in events]
        # Pipeline may short-circuit with ocr_failed/card_ready on hard fail,
        # or emit the full 5-event sequence on success.
        if "ocr_failed" in types:
            assert types[-1] == "card_ready"
        else:
            assert types == [
                "ocr_done", "classify_done", "dedupe_done",
                "budget_done", "card_ready",
            ]
        ready = events[-1]
        assert ready["layer"] in ("1", "2", "3_soft", "3_hard")


@pytest.mark.asyncio
async def test_pipeline_persists_layer_to_draft():
    async with _Session() as db:
        draft = await create_draft(db, "emp_pipe_2")
        await update_draft_receipt(db, draft.id, "/uploads/stub.jpg")
        ctx = UserContext(user_id="emp_pipe_2", roles=["employee"])

        async for _ in run_quick_pipeline(draft.id, ctx, db):
            pass

        reloaded = await get_draft(db, draft.id)
        assert reloaded.layer is not None
        assert reloaded.entry == "quick"
