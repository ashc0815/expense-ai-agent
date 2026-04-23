"""迁移脚本：把主库中的 llm_traces / eval_runs 数据拷贝到 Eval 专用库。

场景：
  D1 隔离前，LLMTrace / EvalRun 表和业务数据都在 concurshield.db 里。
  升级到 D1 之后，新数据写入 concurshield_eval.db，但历史数据还躺在主库。
  本脚本把历史数据一次性搬到 Eval 库。

用法：
  # 默认：保留源表（安全，推荐）
  python scripts/migrate_eval_data.py

  # 迁移完成并验证后，删除主库中的旧表
  python scripts/migrate_eval_data.py --drop-source

  # 预演（不写入，只打印将要做什么）
  python scripts/migrate_eval_data.py --dry-run

环境变量：
  DATABASE_URL          源库（默认 sqlite+aiosqlite:///./concurshield.db）
  EVAL_DATABASE_URL     目标库（默认 sqlite+aiosqlite:///./concurshield_eval.db）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is importable when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402

from backend.config import DATABASE_URL, EVAL_DATABASE_URL  # noqa: E402
from backend.db.store import EvalBase, EvalRun, LLMTrace  # noqa: E402


async def _count(session, model) -> int:
    from sqlalchemy import func
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _table_exists(engine, table_name: str) -> bool:
    """Cross-dialect check for table existence."""
    async with engine.connect() as conn:
        dialect = engine.dialect.name
        if dialect == "sqlite":
            q = text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name=:n"
            )
        elif dialect == "postgresql":
            q = text(
                "SELECT tablename FROM pg_tables "
                "WHERE tablename=:n"
            )
        else:
            return True  # best effort — attempt and let caller catch
        row = (await conn.execute(q, {"n": table_name})).first()
        return row is not None


async def migrate(dry_run: bool, drop_source: bool) -> int:
    print(f"Source DB:  {DATABASE_URL}")
    print(f"Target DB:  {EVAL_DATABASE_URL}")
    print(f"Dry-run:    {dry_run}")
    print(f"Drop-source: {drop_source}")
    print()

    if DATABASE_URL == EVAL_DATABASE_URL:
        print("ERROR: DATABASE_URL 和 EVAL_DATABASE_URL 相同，无需迁移。")
        return 1

    src_engine = create_async_engine(DATABASE_URL, echo=False)
    dst_engine = create_async_engine(EVAL_DATABASE_URL, echo=False)

    has_src_traces = await _table_exists(src_engine, "llm_traces")
    has_src_runs = await _table_exists(src_engine, "eval_runs")

    if not has_src_traces and not has_src_runs:
        print("源库中不存在 llm_traces / eval_runs 表 — 无需迁移。")
        await src_engine.dispose()
        await dst_engine.dispose()
        return 0

    # Ensure target tables exist
    async with dst_engine.begin() as conn:
        await conn.run_sync(EvalBase.metadata.create_all)

    SrcSession = async_sessionmaker(src_engine, expire_on_commit=False)
    DstSession = async_sessionmaker(dst_engine, expire_on_commit=False)

    traces_migrated = 0
    runs_migrated = 0

    async with SrcSession() as src:
        # ── LLM traces ──
        if has_src_traces:
            src_rows = (await src.execute(select(LLMTrace))).scalars().all()
            src_count = len(src_rows)
            print(f"源库 llm_traces: {src_count} 行")

            async with DstSession() as dst:
                existing_ids = set(
                    (await dst.execute(select(LLMTrace.id))).scalars().all()
                )
                to_insert = [r for r in src_rows if r.id not in existing_ids]
                print(f"  去重后待迁移: {len(to_insert)} 行（跳过 {src_count - len(to_insert)} 条已存在）")

                if not dry_run:
                    for row in to_insert:
                        dst.add(LLMTrace(
                            id=row.id,
                            component=row.component,
                            submission_id=row.submission_id,
                            model=row.model,
                            prompt=row.prompt,
                            response=row.response,
                            parsed_output=row.parsed_output,
                            latency_ms=row.latency_ms,
                            token_usage=row.token_usage,
                            error=row.error,
                            created_at=row.created_at,
                        ))
                    await dst.commit()
                traces_migrated = len(to_insert)

        # ── Eval runs ──
        if has_src_runs:
            src_rows = (await src.execute(select(EvalRun))).scalars().all()
            src_count = len(src_rows)
            print(f"源库 eval_runs: {src_count} 行")

            async with DstSession() as dst:
                existing_ids = set(
                    (await dst.execute(select(EvalRun.id))).scalars().all()
                )
                to_insert = [r for r in src_rows if r.id not in existing_ids]
                print(f"  去重后待迁移: {len(to_insert)} 行（跳过 {src_count - len(to_insert)} 条已存在）")

                if not dry_run:
                    for row in to_insert:
                        dst.add(EvalRun(
                            id=row.id,
                            started_at=row.started_at,
                            finished_at=row.finished_at,
                            total_cases=row.total_cases,
                            passed_cases=row.passed_cases,
                            pass_rate=row.pass_rate,
                            results=row.results,
                            trigger=row.trigger,
                            run_metadata=row.run_metadata,
                            component_metrics=row.component_metrics,
                            created_at=row.created_at,
                        ))
                    await dst.commit()
                runs_migrated = len(to_insert)

    print()
    if dry_run:
        print(f"[DRY RUN] 将迁移 {traces_migrated} 条 traces + {runs_migrated} 条 runs（未写入）")
    else:
        print(f"✔ 已迁移 {traces_migrated} 条 traces + {runs_migrated} 条 runs 到 Eval 库")

    # Drop source tables only if asked and not a dry run
    if drop_source and not dry_run:
        print()
        print("删除源库旧表（llm_traces / eval_runs）...")
        async with src_engine.begin() as conn:
            if has_src_traces:
                await conn.execute(text("DROP TABLE llm_traces"))
            if has_src_runs:
                await conn.execute(text("DROP TABLE eval_runs"))
        print("✔ 源库旧表已删除")

    await src_engine.dispose()
    await dst_engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="预演，不写入也不删除")
    parser.add_argument(
        "--drop-source",
        action="store_true",
        help="迁移完成后删除源库中的 llm_traces / eval_runs 表（默认保留）",
    )
    args = parser.parse_args()
    return asyncio.run(migrate(dry_run=args.dry_run, drop_source=args.drop_source))


if __name__ == "__main__":
    sys.exit(main())
