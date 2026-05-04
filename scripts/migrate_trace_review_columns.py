"""One-shot migration: add trace-review columns to llm_traces.

Adds 4 nullable columns introduced in PR B2 (eval-trace-review):

  reviewed_at       DATETIME       — when a human reviewed this trace
  reviewed_by       VARCHAR(64)    — who reviewed it
  failure_mode_tag  VARCHAR(64)    — '' = correct, non-empty = error mode
  review_notes      TEXT           — free-form notes from the reviewer

SQLAlchemy's create_all() only adds *new tables*; it does NOT add columns
to existing ones. Without this script, every query that touches the
llm_traces table after pulling B2 will fail with
"no such column: llm_traces.reviewed_at".

The columns target the EVAL database (concurshield_eval.db by default;
configurable via EVAL_DATABASE_URL env var).

Usage:
  python scripts/migrate_trace_review_columns.py
  python scripts/migrate_trace_review_columns.py --dry-run

Idempotent: safe to run multiple times (skips columns that already exist).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make `backend` importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import EVAL_DATABASE_URL


_REQUIRED_COLUMNS: list[tuple[str, str]] = [
    ("reviewed_at",       "DATETIME"),
    ("reviewed_by",       "VARCHAR(64)"),
    ("failure_mode_tag",  "VARCHAR(64)"),
    ("review_notes",      "TEXT"),
]


async def _ensure_columns(engine, dry_run: bool = False) -> list[str]:
    """Add any missing review columns to the llm_traces table. Returns
    list of column names that were actually (or would be) added."""
    added: list[str] = []
    async with engine.begin() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(llm_traces)"))).all()
        existing = {row[1] for row in rows}  # row[1] is column name
        for col, sql_type in _REQUIRED_COLUMNS:
            if col in existing:
                continue
            if dry_run:
                added.append(col)
                continue
            await conn.execute(
                text(f"ALTER TABLE llm_traces ADD COLUMN {col} {sql_type}")
            )
            added.append(col)
    return added


async def migrate(dry_run: bool = False) -> dict:
    engine = create_async_engine(EVAL_DATABASE_URL)
    try:
        added = await _ensure_columns(engine, dry_run=dry_run)
    finally:
        await engine.dispose()
    return {"columns_added": added}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    print(f"Eval DB URL: {EVAL_DATABASE_URL}")
    print(f"Dry run: {args.dry_run}")
    print()
    print("Scanning…")
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    action = "would add" if args.dry_run else "added"
    if stats["columns_added"]:
        print(f"  {action} columns to llm_traces: {stats['columns_added']}")
    else:
        print("  no columns to add (already up to date)")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
