"""认证中间件 — Mock / Clerk 双模式。

通过 AUTH_MODE 环境变量切换：
  mock  — 从 X-User-Id / X-User-Role header 读取（开发默认）
  clerk — 从 Authorization: Bearer <JWT> 验证（生产）

依赖注入：
  require_auth  → 返回 UserContext（任何角色）
  require_role  → 返回 UserContext（指定角色，否则 403）
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import httpx
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from backend.config import AUTH_MODE, CLERK_SECRET_KEY

# ── 数据模型 ──────────────────────────────────────────────────────

@dataclass
class UserContext:
    user_id: str
    roles: list          # e.g. ["employee", "manager"]

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


# ── Mock 模式 ─────────────────────────────────────────────────────

_VALID_ROLES = {"employee", "manager", "finance_admin"}

def _mock_auth(request: Request) -> UserContext:
    """从 header 读取，缺省给 dev-user / employee。

    X-User-Role 支持逗号分隔的多角色，如 "employee,manager"。
    """
    user_id = request.headers.get("X-User-Id") or request.query_params.get("_uid", "dev-user")
    raw_role = request.headers.get("X-User-Role") or request.query_params.get("_role", "employee")
    roles = [r.strip() for r in raw_role.split(",")]
    for r in roles:
        if r not in _VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"无效角色 '{r}'，允许值：{sorted(_VALID_ROLES)}",
            )
    return UserContext(user_id=user_id, roles=roles)


# ── Clerk 模式 ────────────────────────────────────────────────────

_JWKS_CACHE: dict = {}
_JWKS_FETCHED_AT: float = 0.0
_JWKS_TTL = 3600  # 1 小时


async def _get_jwks() -> dict:
    global _JWKS_CACHE, _JWKS_FETCHED_AT
    if time.time() - _JWKS_FETCHED_AT < _JWKS_TTL and _JWKS_CACHE:
        return _JWKS_CACHE
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.clerk.com/v1/jwks")
        resp.raise_for_status()
        _JWKS_CACHE = resp.json()
        _JWKS_FETCHED_AT = time.time()
    return _JWKS_CACHE


async def _clerk_auth(request: Request) -> UserContext:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization header")
    token = auth_header[7:]
    try:
        jwks = await _get_jwks()
        payload = jwt.decode(token, jwks, algorithms=["RS256"])
        user_id: str = payload.get("sub", "")
        meta = payload.get("publicMetadata", {})
        roles = meta.get("roles") or [meta.get("role", "employee")]
        if not user_id:
            raise HTTPException(status_code=401, detail="JWT 中缺少 sub")
        return UserContext(user_id=user_id, roles=roles)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"JWT 验证失败: {e}") from e


# ── 统一入口 ──────────────────────────────────────────────────────

async def require_auth(request: Request) -> UserContext:
    """FastAPI 依赖：返回当前用户，两种模式共用同一接口。"""
    if AUTH_MODE == "clerk":
        return await _clerk_auth(request)
    return _mock_auth(request)


def require_role(*roles: str) -> Callable:
    """工厂函数，返回只允许指定角色的依赖。

    用法：
        Depends(require_role("manager", "finance_admin"))

    用户拥有多个角色时，只要其中一个匹配即可通过。
    """
    async def _checker(ctx: UserContext = Depends(require_auth)) -> UserContext:
        if not ctx.has_any_role(*roles):
            raise HTTPException(
                status_code=403,
                detail=f"权限不足：需要 {list(roles)}，当前角色 {ctx.roles}",
            )
        return ctx
    return _checker
