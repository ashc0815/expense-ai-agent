"""Notifications routes — list / mark-read for the current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.middleware.auth import UserContext, require_auth
from backend.db.store import (
    get_db, list_notifications, mark_notification_read,
)

router = APIRouter()


def _n_dict(n) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "link": n.link,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
async def list_my_notifications(
    unread_only: bool = False,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    items = await list_notifications(db, ctx.user_id, unread_only=unread_only)
    unread = sum(1 for n in items if n.read_at is None)
    return {
        "items": [_n_dict(n) for n in items],
        "unread_count": unread,
        "total": len(items),
    }


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    ctx: UserContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    n = await mark_notification_read(db, notification_id)
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    if n.recipient_id != ctx.user_id:
        raise HTTPException(status_code=403, detail="权限不足")
    return _n_dict(n)
