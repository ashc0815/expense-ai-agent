"""后端配置 — 从环境变量读取，支持 .env 文件。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ── AI ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── 认证 ──────────────────────────────────────────────────────────
AUTH_MODE: str = os.getenv("AUTH_MODE") or "mock"         # mock | clerk
CLERK_SECRET_KEY: str = os.getenv("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")

# ── 数据库 ────────────────────────────────────────────────────────
_DEFAULT_DB = "sqlite+aiosqlite:///./concurshield.db"
DATABASE_URL: str = os.getenv("DATABASE_URL") or _DEFAULT_DB

# Eval 专用数据库（llm_traces + eval_runs）
# 留空时默认使用同 SQLite 目录下的 concurshield_eval.db
_DEFAULT_EVAL_DB = "sqlite+aiosqlite:///./concurshield_eval.db"
EVAL_DATABASE_URL: str = os.getenv("EVAL_DATABASE_URL") or _DEFAULT_EVAL_DB

# ── 文件存储 ──────────────────────────────────────────────────────
STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")  # local | r2
UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
R2_BUCKET: str = os.getenv("R2_BUCKET", "")
R2_ENDPOINT: str = os.getenv("R2_ENDPOINT", "")
R2_ACCESS_KEY_ID: str = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY: str = os.getenv("R2_SECRET_ACCESS_KEY", "")

# ── Investigator（AI-anti-fraud 后端）────────────────────────────
INVESTIGATOR_URL: str = os.getenv("INVESTIGATOR_URL", "http://localhost:8501")
