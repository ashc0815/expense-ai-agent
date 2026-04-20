"""Task 3 — 认证中间件测试。"""
from __future__ import annotations

import os
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

# 强制 mock 模式
os.environ.setdefault("AUTH_MODE", "mock")

from backend.api.middleware.auth import require_auth, require_role, UserContext

# ── 测试 App ──────────────────────────────────────────────────────

app = FastAPI()

@app.get("/me")
async def me(ctx: UserContext = Depends(require_auth)):
    return {"user_id": ctx.user_id, "roles": ctx.roles}

@app.get("/manager-only")
async def manager_only(ctx: UserContext = Depends(require_role("manager", "finance_admin"))):
    return {"ok": True, "roles": ctx.roles}

client = TestClient(app)


# ── Mock 模式测试 ─────────────────────────────────────────────────

def test_mock_default_headers():
    """无 header → 默认 dev-user / employee。"""
    r = client.get("/me")
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == "dev-user"
    assert data["roles"] == ["employee"]


def test_mock_custom_headers():
    """自定义 header 正确注入。"""
    r = client.get("/me", headers={"X-User-Id": "u-123", "X-User-Role": "manager"})
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == "u-123"
    assert data["roles"] == ["manager"]


def test_mock_multi_role_headers():
    """逗号分隔的多角色 header。"""
    r = client.get("/me", headers={"X-User-Id": "u-mgr", "X-User-Role": "employee,manager"})
    assert r.status_code == 200
    data = r.json()
    assert data["roles"] == ["employee", "manager"]


def test_mock_role_allowed():
    """manager 访问 manager-only 端点 → 200。"""
    r = client.get("/manager-only", headers={"X-User-Role": "manager"})
    assert r.status_code == 200


def test_mock_role_denied():
    """employee 访问 manager-only 端点 → 403。"""
    r = client.get("/manager-only", headers={"X-User-Role": "employee"})
    assert r.status_code == 403


def test_mock_finance_admin_allowed():
    """finance_admin 也可访问 manager-only。"""
    r = client.get("/manager-only", headers={"X-User-Role": "finance_admin"})
    assert r.status_code == 200


def test_mock_multi_role_access():
    """employee,manager 多角色可访问 manager-only 端点。"""
    r = client.get("/manager-only", headers={"X-User-Role": "employee,manager"})
    assert r.status_code == 200
