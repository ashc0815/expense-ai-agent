"""Quick attest flow routes — upload, stream, attest."""
from __future__ import annotations

import io
import json
from typing import AsyncIterator

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile,
)
from fastapi.responses import StreamingResponse
from starlette.datastructures import Headers
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth
from backend.db.store import (
    create_draft, get_db, get_draft, insert_telemetry, update_draft_receipt,
)
from backend.pdf_splitter import SplitError, split
from backend.quick.finalize import save_draft_as_report_line
from backend.quick.pipeline import run_quick_pipeline
from backend.storage import get_storage

router = APIRouter()


def _is_pdf(file: UploadFile) -> bool:
    if file.content_type == "application/pdf":
        return True
    name = (file.filename or "").lower()
    return name.endswith(".pdf")


def _wrap_pdf_bytes(data: bytes, filename: str) -> UploadFile:
    headers = Headers({"content-type": "application/pdf"})
    return UploadFile(file=io.BytesIO(data), filename=filename, headers=headers)


@router.post("/upload", status_code=201)
async def quick_upload(
    file: UploadFile = File(...),
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    storage=Depends(get_storage),
):
    original_name = file.filename or "receipt"

    if _is_pdf(file):
        content = await file.read()
        try:
            pages = split(content)
        except SplitError:
            pages = None

        if pages is not None and len(pages) > 1:
            stem = original_name[:-4] if original_name.lower().endswith(".pdf") else original_name
            drafts: list[dict] = []
            for idx, page_bytes in enumerate(pages, start=1):
                page_name = f"{stem}-p{idx}.pdf"
                draft = await create_draft(db, ctx.user_id)
                draft.entry = "quick"
                await db.commit()

                synthetic = _wrap_pdf_bytes(page_bytes, page_name)
                receipt_url = await storage.save(synthetic, page_name)
                await update_draft_receipt(db, draft.id, receipt_url)
                drafts.append({"draft_id": draft.id, "receipt_url": receipt_url})
            return {"drafts": drafts}

        # single-page PDF or split failure → fall through to single-upload path
        single = _wrap_pdf_bytes(content, original_name if original_name.lower().endswith(".pdf") else f"{original_name}.pdf")
        draft = await create_draft(db, ctx.user_id)
        draft.entry = "quick"
        await db.commit()
        receipt_url = await storage.save(single, single.filename or "receipt.pdf")
        await update_draft_receipt(db, draft.id, receipt_url)
        return {"draft_id": draft.id, "receipt_url": receipt_url}

    draft = await create_draft(db, ctx.user_id)
    draft.entry = "quick"
    await db.commit()

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

    sub_id, _report_id = await save_draft_as_report_line(draft_id, ctx, db)

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
