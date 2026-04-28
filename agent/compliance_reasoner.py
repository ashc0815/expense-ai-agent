"""Cross-record compliance reasoner — the agent layer above hard rules.

Runs AFTER the deterministic 5-Skill pipeline. Calls the read-only
lookup tools in `backend/services/compliance_lookups.py` to look at
state hard rules can't see in a single submission, then emits
`agent.*` violations with a structured evidence_chain pointing to the
other rows that justify the finding.

Three checks, each tied to a real-world failure pattern hard rules
miss:

  1. agent.travel_during_leave
       transport / accommodation claim on a date the employee was on
       approved leave. Hard rule sees the receipt; only the reasoner
       sees the leave record.

  2. agent.claim_vs_allowance
       reimbursement for a category that's already covered by a
       recurring allowance (e.g. car_allowance + transport claim).

  3. agent.cross_person_meal_double_dip
       employee submits a meal AND is listed as an attendee on
       someone else's meal/entertainment claim the same day. Either
       could be legit — the reasoner just surfaces the collision.

This module deliberately stays Python-only (no LLM call): the three
checks above are deterministic given the lookup outputs, so the
reasoner is fast, tests are reliable, and explanations are stable
across runs. An LLM layer can sit on top later for fuzzier patterns.
"""
from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.compliance_lookups import (
    get_employee_allowances, get_employee_leave_in_range,
    list_meals_with_attendees,
)


# ── Category → check applicability ───────────────────────────────────

_TRAVEL_CATEGORIES: frozenset[str] = frozenset({
    "transport", "accommodation", "travel",
})
_MEAL_CATEGORIES: frozenset[str] = frozenset({
    "meal", "entertainment",
})


# Each allowance kind blocks one or more reimbursement categories.
# Keep the mapping conservative — false positives here are user-facing
# and erode trust in the reasoner.
ALLOWANCE_BLOCKS_CATEGORY: dict[str, frozenset[str]] = {
    "car_allowance":   frozenset({"transport"}),
    "meal_per_diem":   frozenset({"meal"}),
    "phone_allowance": frozenset({"telecom", "phone"}),
}


# ── The three checks ─────────────────────────────────────────────────

async def _check_travel_during_leave(
    db: AsyncSession,
    *,
    employee_id: str,
    expense_date: str,
    category: str,
) -> Optional[dict]:
    if category not in _TRAVEL_CATEGORIES:
        return None
    out = await get_employee_leave_in_range(
        db, employee_id=employee_id,
        start_date=expense_date, end_date=expense_date,
        status="approved",
    )
    if out["count"] == 0:
        return None
    leaves = out["leaves"]
    return {
        "kind": "agent.travel_during_leave",
        "context": {
            "expense_date": expense_date,
            "category": category,
            "leave_count": len(leaves),
        },
        "evidence_chain": [
            {
                "kind": "approved_leave",
                "leave_id": leave["id"],
                "leave_kind": leave["kind"],
                "start_date": leave["start_date"],
                "end_date": leave["end_date"],
                "approved_by": leave["approved_by"],
            }
            for leave in leaves
        ],
    }


async def _check_allowance_conflict(
    db: AsyncSession,
    *,
    employee_id: str,
    expense_date: str,
    category: str,
) -> Optional[dict]:
    out = await get_employee_allowances(
        db, employee_id=employee_id, on_date=expense_date,
    )
    if out["count"] == 0:
        return None
    blocking = []
    for a in out["allowances"]:
        if category in ALLOWANCE_BLOCKS_CATEGORY.get(a["kind"], frozenset()):
            blocking.append(a)
    if not blocking:
        return None
    return {
        "kind": "agent.claim_vs_allowance",
        "context": {
            "expense_date": expense_date,
            "category": category,
            "blocking_count": len(blocking),
        },
        "evidence_chain": [
            {
                "kind": "active_allowance",
                "allowance_id": a["id"],
                "allowance_kind": a["kind"],
                "monthly_amount": a["monthly_amount"],
                "effective_from": a["effective_from"],
                "effective_to": a["effective_to"],
            }
            for a in blocking
        ],
    }


async def _check_cross_person_meal(
    db: AsyncSession,
    *,
    submission_id: Optional[str],
    employee_id: str,
    expense_date: str,
    category: str,
) -> Optional[dict]:
    if category not in _MEAL_CATEGORIES:
        return None
    out = await list_meals_with_attendees(
        db, employee_id=employee_id, on_date=expense_date, window_days=1,
    )
    appearances = out.get("attendee_appearances") or []
    if not appearances:
        return None
    # We don't require self_count > 0 because the THIS submission may
    # not have been written yet (when called from background pipeline);
    # the appearance on someone else's submission is the trigger.
    return {
        "kind": "agent.cross_person_meal_double_dip",
        "context": {
            "expense_date": expense_date,
            "category": category,
            "appearance_count": len(appearances),
            "this_submission_id": submission_id,
        },
        "evidence_chain": [
            {
                "kind": "appears_on_other_submission",
                "other_submission_id": ap["submission_id"],
                "other_submitter_id": ap["submitter_employee_id"],
                "other_merchant": ap["merchant"],
                "other_amount": ap["amount"],
                "other_date": ap["date"],
                "other_category": ap["category"],
                "attendee_role": ap["attendee_role"],
            }
            for ap in appearances
        ],
    }


# ── Public API ───────────────────────────────────────────────────────

async def reason_about_submission(
    db: AsyncSession,
    *,
    submission_id: Optional[str],
    employee_id: str,
    expense_date: str,
    category: str,
    amount: Optional[float] = None,
) -> list[dict]:
    """Run all applicable checks for one submission. Returns a list of
    raw findings — caller passes them to violation_registry to render
    into the audit-report violation format.

    The function is intentionally tolerant: any check that throws is
    skipped (the deterministic pipeline must remain trustworthy even
    if a lookup tool fails). Returns [] when nothing fires.
    """
    findings: list[dict] = []
    checks = (
        _check_travel_during_leave(
            db, employee_id=employee_id,
            expense_date=expense_date, category=category,
        ),
        _check_allowance_conflict(
            db, employee_id=employee_id,
            expense_date=expense_date, category=category,
        ),
        _check_cross_person_meal(
            db, submission_id=submission_id, employee_id=employee_id,
            expense_date=expense_date, category=category,
        ),
    )
    for coro in checks:
        try:
            result = await coro
        except Exception:  # noqa: BLE001 — never let one check kill the rest
            continue
        if result:
            findings.append(result)
    return findings
