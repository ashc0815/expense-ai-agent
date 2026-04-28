"""Compliance reasoning lookups — read-only tools the agent can call.

These exist because hard-coded rules (rules/policy_engine.py) only see
ONE submission at a time. Compliance violations that span multiple
records — collusion, allowance/reimbursement conflicts, leave overlap —
need cross-row queries. The agent compliance reasoner (PR-B) calls
these to gather evidence, then the result lands in audit_report.violations
with rule_id="agent.*".

Security: every function here is read-only. The Concur-style ACL is
preserved — these tools never mutate state, never approve / reject /
pay. They just READ data the agent uses to reason about a submission.

Each function returns a plain dict / list of dicts (JSON-friendly) so
they can be passed straight into the LLM tool-result loop.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.store import (
    Submission, find_attendee_appearances, list_active_allowances,
    list_employee_leaves, list_submission_attendees,
)


def _iso(d: date | datetime | None) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def _parse_date(s: str | date | None) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    return date.fromisoformat(s)


# ── Tool 1 — Leave conflict ──────────────────────────────────────────

async def get_employee_leave_in_range(
    db: AsyncSession,
    *,
    employee_id: str,
    start_date: str,
    end_date: str,
    status: Optional[str] = "approved",
) -> dict[str, Any]:
    """All leave records overlapping [start_date, end_date]. By default
    only returns approved leave (the only kind that should block a
    travel claim). Pass status=None to get every record.
    """
    rng_start = _parse_date(start_date)
    rng_end = _parse_date(end_date)
    rows = await list_employee_leaves(
        db, employee_id,
        overlaps_start=rng_start, overlaps_end=rng_end,
        status=status,
    )
    return {
        "employee_id": employee_id,
        "queried_range": {"start": _iso(rng_start), "end": _iso(rng_end)},
        "leaves": [
            {
                "id": r.id,
                "start_date": _iso(r.start_date),
                "end_date": _iso(r.end_date),
                "kind": r.kind,
                "status": r.status,
                "approved_by": r.approved_by,
                "notes": r.notes,
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── Tool 2 — Allowance vs reimbursement ──────────────────────────────

async def get_employee_allowances(
    db: AsyncSession,
    *,
    employee_id: str,
    on_date: Optional[str] = None,
) -> dict[str, Any]:
    """Active allowances for the employee on the given date (defaults
    to today). The reasoner uses this to detect "claiming a category
    already covered by a recurring allowance".
    """
    target = _parse_date(on_date) or date.today()
    rows = await list_active_allowances(db, employee_id, target)
    return {
        "employee_id": employee_id,
        "on_date": _iso(target),
        "allowances": [
            {
                "id": r.id,
                "kind": r.kind,
                "monthly_amount": float(r.monthly_amount),
                "effective_from": _iso(r.effective_from),
                "effective_to": _iso(r.effective_to),
                "notes": r.notes,
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── Tool 3 — Cross-employee meal double-dip ──────────────────────────

async def list_meals_with_attendees(
    db: AsyncSession,
    *,
    employee_id: str,
    on_date: str,
    window_days: int = 1,
) -> dict[str, Any]:
    """Return both sides of the meal-collision picture for `on_date`:

      self_submissions  — meals the employee submitted themselves
      attendee_appearances — submissions where the employee appears as
                             a guest on someone ELSE's meal/entertainment

    The reasoner flags overlap when both lists are non-empty in the
    same window and the categories are meal/entertainment.
    """
    target = _parse_date(on_date)
    if target is None:
        return {"error": "on_date is required (YYYY-MM-DD)"}
    window_start = (target - timedelta(days=max(0, window_days - 1))).isoformat()
    window_end = (target + timedelta(days=max(0, window_days - 1))).isoformat()

    # Self-submitted meal/entertainment
    own = (await db.execute(
        select(Submission).where(
            Submission.employee_id == employee_id,
            Submission.date >= window_start,
            Submission.date <= window_end,
            Submission.category.in_(["meal", "entertainment"]),
        )
    )).scalars().all()

    # Appearances on other people's submissions
    appearances = await find_attendee_appearances(
        db, employee_id,
        start_date=window_start, end_date=window_end,
    )

    self_payload = [
        {
            "submission_id": s.id,
            "date": s.date,
            "merchant": s.merchant,
            "amount": float(s.amount),
            "currency": s.currency,
            "category": s.category,
        }
        for s in own
    ]
    appearance_payload = [
        {
            "submission_id": sub.id,
            "submitter_employee_id": sub.employee_id,
            "date": sub.date,
            "merchant": sub.merchant,
            "amount": float(sub.amount),
            "category": sub.category,
            "attendee_name": att.name,
            "attendee_role": att.role,
        }
        for att, sub in appearances
    ]
    return {
        "employee_id": employee_id,
        "window": {"start": window_start, "end": window_end},
        "self_submissions": self_payload,
        "attendee_appearances": appearance_payload,
        "self_count": len(self_payload),
        "appearance_count": len(appearance_payload),
    }


# ── Tool 4 — Generic overlapping-claim scan ──────────────────────────

async def find_overlapping_claims(
    db: AsyncSession,
    *,
    employee_id: str,
    category: str,
    start_date: str,
    end_date: str,
    exclude_submission_id: Optional[str] = None,
) -> dict[str, Any]:
    """Other approved/pending claims by the same employee in the same
    category and date window. Used as a backstop for any duplicate-claim
    pattern the more specific tools don't cover.
    """
    q = select(Submission).where(
        Submission.employee_id == employee_id,
        Submission.category == category,
        Submission.date >= start_date,
        Submission.date <= end_date,
        Submission.status.notin_(["rejected", "review_failed"]),
    )
    if exclude_submission_id:
        q = q.where(Submission.id != exclude_submission_id)
    rows = (await db.execute(q.order_by(Submission.date.asc()))).scalars().all()
    return {
        "employee_id": employee_id,
        "category": category,
        "window": {"start": start_date, "end": end_date},
        "claims": [
            {
                "submission_id": s.id,
                "date": s.date,
                "merchant": s.merchant,
                "amount": float(s.amount),
                "currency": s.currency,
                "status": s.status,
            }
            for s in rows
        ],
        "count": len(rows),
    }


# ── Bonus accessor used by the reasoner UI ───────────────────────────

async def get_submission_attendees(
    db: AsyncSession, submission_id: str,
) -> list[dict[str, Any]]:
    rows = await list_submission_attendees(db, submission_id)
    return [
        {
            "name": r.name,
            "employee_id": r.employee_id,
            "role": r.role,
        }
        for r in rows
    ]
