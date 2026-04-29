"""ExpenseFlow API — FastAPI 入口。

启动：
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from backend.api.routes import submissions, approvals, ocr, users, admin, employees, finance, chat, budget, quick, reports, notifications, fx, eval, auto_rules
from backend.db.store import init_db

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
_UPLOAD_DIR   = Path(__file__).resolve().parents[1] / "uploads"


@asynccontextmanager
async def lifespan(_: "FastAPI"):
    await init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="ExpenseFlow API",
    version="0.1.0",
    description="AI 驱动的报销审核平台 — 员工提交 → 5-Skill 管道处理 → 经理审批",
)

# ── CORS（开发阶段允许所有 localhost）────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ──────────────────────────────────────────────────────
app.include_router(submissions.router, prefix="/api/submissions", tags=["submissions"])
app.include_router(approvals.router, prefix="/api/submissions", tags=["approvals"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["ocr"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(employees.router, prefix="/api/employees", tags=["employees"])
app.include_router(finance.router, prefix="/api/finance", tags=["finance"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(budget.router, prefix="/api/budget", tags=["budget"])
app.include_router(quick.router, prefix="/api/quick", tags=["quick"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(fx.router, prefix="/api/fx", tags=["fx"])
app.include_router(eval.router, prefix="/api/eval", tags=["eval"])
app.include_router(auto_rules.router, prefix="/api/auto-rules", tags=["auto-rules"])


# ── 前端静态文件 ──────────────────────────────────────────────────
if _FRONTEND_DIR.exists():
    app.mount("/shared",   StaticFiles(directory=str(_FRONTEND_DIR / "shared")),   name="shared")
    app.mount("/employee", StaticFiles(directory=str(_FRONTEND_DIR / "employee")), name="employee")
    app.mount("/manager",  StaticFiles(directory=str(_FRONTEND_DIR / "manager")),  name="manager")
    app.mount("/finance",  StaticFiles(directory=str(_FRONTEND_DIR / "finance")),  name="finance")
    app.mount("/admin",    StaticFiles(directory=str(_FRONTEND_DIR / "admin")),    name="admin")
    app.mount("/eval",     StaticFiles(directory=str(_FRONTEND_DIR / "eval")),     name="eval")

_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_UPLOAD_DIR)), name="uploads")


# ── 健康检查 ──────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}
