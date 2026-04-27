"""One-shot migration: voucher_number from per-submission to per-report.

Background:
  Original schema stored voucher_number on Submission only. This violated
  double-entry accounting (one business event = one voucher), and also
  caused stale data to drift when finance approval retried — different
  submissions in the same report could end up with different vouchers.

  PR-1 added Report.voucher_number. This script consolidates existing
  data so each report has exactly one voucher and all its submissions
  share that one.

Strategy:
  For each finance-approved report:
    1. Find the MIN voucher_number among its submissions (if any).
    2. Copy that to Report.voucher_number.
    3. Force-overwrite all submissions in that report with the same
       voucher (so 0001 and 0002 in the same report collapse to 0001).
    4. The orphaned higher number (e.g. 0002) is now unreferenced — that
       creates a gap in the sequence, which is fine for a fresh dev DB.

Usage:
  python scripts/migrate_voucher_to_report.py
  python scripts/migrate_voucher_to_report.py --dry-run

Idempotent: running twice does nothing the second time.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make `backend` importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.config import DATABASE_URL
from backend.db.store import Base, Report, Submission


async def migrate(dry_run: bool = False) -> dict:
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    stats = {"reports_updated": 0, "submissions_normalized": 0, "skipped_already_set": 0}

    async with Session() as db:
        # Find all reports whose submissions have a voucher number.
        rows = await db.execute(
            select(Report.id, Report.voucher_number, func.min(Submission.voucher_number))
            .join(Submission, Submission.report_id == Report.id)
            .where(Submission.voucher_number.isnot(None))
            .group_by(Report.id)
        )
        targets = rows.all()

        for report_id, current_vn, sub_min_vn in targets:
            if current_vn:
                stats["skipped_already_set"] += 1
                continue
            if not sub_min_vn:
                continue

            print(f"  report {report_id[:8]}…  voucher = {sub_min_vn}")
            stats["reports_updated"] += 1

            if dry_run:
                continue

            # Set the canonical voucher on Report
            await db.execute(
                update(Report)
                .where(Report.id == report_id)
                .values(voucher_number=sub_min_vn)
            )
            # Force every submission in this report to share that voucher
            res = await db.execute(
                update(Submission)
                .where(Submission.report_id == report_id)
                .where(Submission.voucher_number.isnot(None))
                .where(Submission.voucher_number != sub_min_vn)
                .values(voucher_number=sub_min_vn)
            )
            stats["submissions_normalized"] += res.rowcount or 0

        if not dry_run:
            await db.commit()

    await engine.dispose()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    print(f"DB URL: {DATABASE_URL}")
    print(f"Dry run: {args.dry_run}")
    print()
    print("Scanning…")
    stats = asyncio.run(migrate(dry_run=args.dry_run))
    print()
    print("Done.")
    print(f"  reports updated:           {stats['reports_updated']}")
    print(f"  submissions normalized:    {stats['submissions_normalized']}")
    print(f"  skipped (already set):     {stats['skipped_already_set']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
