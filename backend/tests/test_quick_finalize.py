"""Verify the quick-add finalize helper creates a submission from a draft."""
import os, tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False); _TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP.name}")
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/concurshield_finalize_test")

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.api.middleware.auth import UserContext
from backend.db.store import (
    Base, create_draft, update_draft_receipt, update_draft_field, get_submission,
)
from backend.quick.finalize import save_draft_as_report_line

_engine = create_async_engine(f"sqlite+aiosqlite:///{_TMP.name}")
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_save_draft_as_report_line_creates_submission():
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

        ctx = UserContext(user_id="emp_finalize_1", role="employee")
        sub_id, report_id = await save_draft_as_report_line(draft.id, ctx, db)

        assert sub_id and report_id
        sub = await get_submission(db, sub_id)
        assert sub is not None
        assert sub.merchant == "海底捞"
        assert float(sub.amount) == 358.0
        assert sub.status == "in_report"
        assert sub.report_id == report_id
