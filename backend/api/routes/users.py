"""用户 API — 当前用户信息。

GET /api/users/me  → 返回当前已认证用户的基本信息
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.middleware.auth import UserContext, require_auth

router = APIRouter()


@router.get("/me")
async def get_me(ctx: UserContext = Depends(require_auth)):
    return {
        "id": ctx.user_id,
        "roles": ctx.roles,
    }
