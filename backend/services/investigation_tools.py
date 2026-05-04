"""Investigation tools — read-only lookup functions for the fraud
investigator agent (Layer 2 OODA loop).

Hamel "Building Effective Agents" emphasizes: an agent is only as good
as the tool registry you give it. These 8 functions are the "eyes" the
fraud investigator gets when Layer 1 (deterministic rules) trips and
something needs deeper digging.

Security boundary (Concur Joule pattern): every function here is
read-only. The investigator agent CANNOT change submission state, can
only QUERY data. Even with prompt injection, an attacker can do at
most "make the agent call read tools more times" — never approve,
reject, modify, or pay.

Each function returns a JSON-friendly dict so the agent can drop the
result straight into the next prompt.

Tool inventory:
  1. get_employee_profile         — employee record (level, dept, etc.)
  2. get_recent_expenses          — submitter's last N expenses
  3. get_approval_history         — all approvals by/for this employee
  4. get_merchant_usage           — who else has used this merchant?
  5. get_peer_comparison          — same-cost-center peers' spending
  6. get_amount_distribution      — submitter's own distribution
  7. check_geo_feasibility        — same-day-different-cities heuristic
  8. check_math_consistency       — amount/attendees vs description claim
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.store import (
    Employee, Submission, SubmissionAttendee,
    get_employee, list_approvals_by_approver, list_submissions,
    list_submissions_by_merchant,
)


# ── Tool 1: get_employee_profile ─────────────────────────────────────

async def get_employee_profile(
    db: AsyncSession, *, employee_id: str,
) -> dict[str, Any]:
    """Read-only employee record — what role, level, cost center, when
    hired. The agent uses this to baseline expectations
    (e.g. an L1 doesn't expense ¥3000 dinners)."""
    emp = await get_employee(db, employee_id)
    if emp is None:
        return {"employee_id": employee_id, "found": False}
    return {
        "employee_id": emp.id,
        "found": True,
        "name": emp.name,
        "department": emp.department,
        "cost_center": emp.cost_center,
        "level": emp.level,
        "city": emp.city,
        "manager_id": emp.manager_id,
        "hire_date": emp.hire_date.isoformat() if emp.hire_date else None,
        "resignation_date": (
            emp.resignation_date.isoformat() if emp.resignation_date else None
        ),
        "home_currency": emp.home_currency,
    }


# ── Tool 2: get_recent_expenses ──────────────────────────────────────

async def get_recent_expenses(
    db: AsyncSession,
    *,
    employee_id: str,
    days: int = 90,
    limit: int = 20,
) -> dict[str, Any]:
    """Submitter's recent expense submissions — used to find pattern
    anomalies (sudden jump in size, new category, etc.)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    page = await list_submissions(
        db, employee_id=employee_id, page=1, page_size=limit,
    )
    items = [s for s in page["items"] if s.date >= cutoff]
    return {
        "employee_id": employee_id,
        "window_days": days,
        "count": len(items),
        "expenses": [
            {
                "id": s.id,
                "date": s.date,
                "category": s.category,
                "amount": float(s.amount),
                "currency": s.currency,
                "merchant": s.merchant,
                "status": s.status,
                "description": s.description,
            }
            for s in items
        ],
    }


# ── Tool 3: get_approval_history ─────────────────────────────────────

async def get_approval_history(
    db: AsyncSession,
    *,
    approver_id: str,
    days: int = 90,
) -> dict[str, Any]:
    """All submissions a given approver has approved. Used to detect
    rubber-stamping (median approval latency tiny, no comments) or
    collusion (same approver always greenlighting same submitter)."""
    rows = await list_approvals_by_approver(db, approver_id, days=days)
    return {
        "approver_id": approver_id,
        "window_days": days,
        "count": len(rows),
        "approvals": [
            {
                "submission_id": s.id,
                "submitter_id": s.employee_id,
                "amount": float(s.amount),
                "category": s.category,
                "approved_at": s.approved_at.isoformat() if s.approved_at else None,
                "comment": s.approver_comment,
            }
            for s in rows
        ],
    }


# ── Tool 4: get_merchant_usage ───────────────────────────────────────

async def get_merchant_usage(
    db: AsyncSession,
    *,
    merchant: str,
    days: int = 90,
) -> dict[str, Any]:
    """Has anyone else expensed at this merchant? Used to spot
    one-off / shell-company merchants (the 'never seen this name
    before' signal)."""
    rows = await list_submissions_by_merchant(db, merchant, days=days, limit=200)
    submitters = sorted({s.employee_id for s in rows})
    return {
        "merchant": merchant,
        "window_days": days,
        "total_count": len(rows),
        "unique_submitters": len(submitters),
        "submitters": submitters[:20],
        "amounts": [float(s.amount) for s in rows],
        "first_seen": min((s.date for s in rows), default=None),
        "last_seen": max((s.date for s in rows), default=None),
    }


# ── Tool 5: get_peer_comparison ──────────────────────────────────────

async def get_peer_comparison(
    db: AsyncSession,
    *,
    employee_id: str,
    category: str,
    days: int = 90,
) -> dict[str, Any]:
    """Same-cost-center peers' spending pattern in this category.
    Returns the submitter's per-submission average + percentile rank
    among peers. The agent uses this to ask 'is this person obviously
    spending more than coworkers?'."""
    emp = await get_employee(db, employee_id)
    if emp is None:
        return {"employee_id": employee_id, "found": False}
    cost_center = emp.cost_center
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    result = await db.execute(
        select(
            Submission.employee_id,
            func.avg(Submission.amount).label("avg_amount"),
            func.count().label("n"),
        )
        .where(
            Submission.cost_center == cost_center,
            Submission.category == category,
            Submission.date >= cutoff,
            Submission.status.notin_(["rejected", "review_failed"]),
        )
        .group_by(Submission.employee_id)
    )
    rows = result.all()
    peer_averages = [(r.employee_id, float(r.avg_amount or 0), int(r.n or 0)) for r in rows]
    self_row = next((p for p in peer_averages if p[0] == employee_id), None)
    others = [p for p in peer_averages if p[0] != employee_id]
    self_avg = self_row[1] if self_row else None
    self_n = self_row[2] if self_row else 0

    # Percentile rank: what fraction of peers spend less than self_avg?
    if self_avg is not None and others:
        below = sum(1 for _, avg, _ in others if avg < self_avg)
        percentile = round(below / len(others), 2)
    else:
        percentile = None

    other_avgs = [a for _, a, _ in others]
    return {
        "employee_id": employee_id,
        "category": category,
        "cost_center": cost_center,
        "window_days": days,
        "peer_count": len(others),
        "self_avg": self_avg,
        "self_n": self_n,
        "peer_avg_mean": round(statistics.mean(other_avgs), 2) if other_avgs else None,
        "peer_avg_median": round(statistics.median(other_avgs), 2) if other_avgs else None,
        "self_percentile": percentile,
    }


# ── Tool 6: get_amount_distribution ──────────────────────────────────

async def get_amount_distribution(
    db: AsyncSession,
    *,
    employee_id: str,
    category: str,
    days: int = 180,
) -> dict[str, Any]:
    """Submitter's OWN amount distribution in this category. Used to
    detect outlier-from-self (e.g. usually ¥80 lunches, suddenly ¥800).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    result = await db.execute(
        select(Submission.amount).where(
            Submission.employee_id == employee_id,
            Submission.category == category,
            Submission.date >= cutoff,
            Submission.status.notin_(["rejected", "review_failed"]),
        )
    )
    amounts = [float(a) for (a,) in result.all()]

    if not amounts:
        return {
            "employee_id": employee_id,
            "category": category,
            "n": 0,
            "min": None, "p25": None, "median": None,
            "p75": None, "max": None, "mean": None,
        }

    s = sorted(amounts)
    def _q(pct: float) -> float:
        i = max(0, min(len(s) - 1, int(pct * (len(s) - 1))))
        return round(s[i], 2)

    return {
        "employee_id": employee_id,
        "category": category,
        "n": len(amounts),
        "min": round(s[0], 2),
        "p25": _q(0.25),
        "median": _q(0.50),
        "p75": _q(0.75),
        "max": round(s[-1], 2),
        "mean": round(statistics.mean(amounts), 2),
    }


# ── Tool 7: check_geo_feasibility ────────────────────────────────────

# Rough crow-fly distances in km between major Chinese cities.
# Conservative: any pair > 200km on the same day flagged as infeasible
# (assumes an employee can't be physically present in both within the
# same business day window without a flight/HSR record).
_CITY_DISTANCES_KM: dict[tuple[str, str], int] = {
    ("上海", "北京"): 1100,
    ("上海", "广州"): 1300,
    ("上海", "深圳"): 1300,
    ("上海", "成都"): 1700,
    ("上海", "杭州"): 170,
    ("上海", "苏州"): 80,
    ("上海", "南京"): 270,
    ("北京", "广州"): 1900,
    ("北京", "深圳"): 1950,
    ("北京", "成都"): 1500,
    ("北京", "上海"): 1100,
    ("北京", "天津"): 120,
    ("广州", "深圳"): 100,
    ("广州", "上海"): 1300,
    ("广州", "北京"): 1900,
    ("成都", "重庆"): 270,
    ("杭州", "苏州"): 130,
    ("武汉", "长沙"): 290,
}


def _city_distance_km(a: str, b: str) -> Optional[int]:
    if a == b:
        return 0
    pair = (a, b) if (a, b) in _CITY_DISTANCES_KM else (b, a)
    return _CITY_DISTANCES_KM.get(pair)


def check_geo_feasibility(
    *,
    date_a: str,
    city_a: str,
    date_b: str,
    city_b: str,
    same_day_max_km: int = 200,
) -> dict[str, Any]:
    """Heuristic: can the same employee plausibly be in both city_a on
    date_a AND city_b on date_b? Returns feasibility flag plus
    reasoning. Doesn't call any external API — just a small lookup
    table for major Chinese cities.

    Used to flag e.g. 'expensed dinner in Shanghai 19:30 AND breakfast
    in Beijing next morning — possible but suspicious given travel
    time' or 'same date, two different cities 1000km apart'.
    """
    if city_a == city_b:
        return {
            "feasible": True,
            "reason": "same city",
            "distance_km": 0,
            "date_diff_days": 0,
        }

    dist = _city_distance_km(city_a, city_b)
    if dist is None:
        return {
            "feasible": True,
            "reason": "city pair not in distance table — cannot judge",
            "distance_km": None,
            "date_diff_days": None,
            "cities_known": False,
        }

    try:
        d_a = date.fromisoformat(date_a)
        d_b = date.fromisoformat(date_b)
    except (ValueError, TypeError):
        return {
            "feasible": True,
            "reason": "invalid date format",
            "distance_km": dist,
        }

    diff_days = abs((d_a - d_b).days)

    if diff_days == 0 and dist > same_day_max_km:
        return {
            "feasible": False,
            "reason": (
                f"same day, but {city_a} → {city_b} ≈ {dist}km "
                f"(threshold {same_day_max_km}km). Needs a travel record."
            ),
            "distance_km": dist,
            "date_diff_days": 0,
        }

    return {
        "feasible": True,
        "reason": f"{diff_days} day(s) apart, {dist}km — plausible",
        "distance_km": dist,
        "date_diff_days": diff_days,
    }


# ── Tool 8: check_math_consistency ───────────────────────────────────

# Match a price-per-person claim like "人均 80", "每人 ¥120", "80元/人"
_PER_PERSON_PATTERNS = [
    re.compile(r"人均\s*[¥￥]?\s*(\d+(?:\.\d+)?)", re.UNICODE),
    re.compile(r"每人\s*[¥￥]?\s*(\d+(?:\.\d+)?)", re.UNICODE),
    re.compile(r"(\d+(?:\.\d+)?)\s*元?\s*/\s*人", re.UNICODE),
]
# Match a head count claim like "5人", "三个人", "10位"
_HEADCOUNT_PATTERNS = [
    re.compile(r"(\d+)\s*[人位]"),
]


def _extract_per_person_claim(description: str) -> Optional[float]:
    if not description:
        return None
    for pat in _PER_PERSON_PATTERNS:
        m = pat.search(description)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _extract_headcount_claim(description: str) -> Optional[int]:
    if not description:
        return None
    for pat in _HEADCOUNT_PATTERNS:
        m = pat.search(description)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def check_math_consistency(
    *,
    amount: float,
    description: str,
    attendees_count: Optional[int] = None,
    tolerance_pct: float = 0.15,
) -> dict[str, Any]:
    """Check that the math implied by the description is consistent
    with the submitted amount.

    Two checks:
      A. If description mentions "人均 80" + headcount N is known:
         expected = 80 * N. Compare to amount within tolerance_pct.
      B. If description mentions a headcount but no per-person figure,
         compute amount / N and report it (no judgment — let the agent
         decide whether ¥600/person for a meal is reasonable).

    Returns a verdict dict the agent can read.
    """
    claimed_per_person = _extract_per_person_claim(description)
    claimed_headcount = _extract_headcount_claim(description)
    effective_n = attendees_count if attendees_count is not None else claimed_headcount

    out: dict[str, Any] = {
        "amount": amount,
        "description_per_person_claim": claimed_per_person,
        "description_headcount_claim": claimed_headcount,
        "effective_attendees_count": effective_n,
    }

    if claimed_per_person is not None and effective_n and effective_n > 0:
        expected = claimed_per_person * effective_n
        diff_pct = abs(amount - expected) / expected if expected > 0 else None
        out["expected_total"] = round(expected, 2)
        out["diff_pct"] = round(diff_pct, 3) if diff_pct is not None else None
        out["consistent"] = (diff_pct is not None and diff_pct <= tolerance_pct)
        if not out["consistent"]:
            out["reason"] = (
                f"Description says {claimed_per_person}/person × {effective_n} ≈ "
                f"{expected:.0f}, but amount is {amount:.0f} "
                f"({(diff_pct * 100):.0f}% off)."
            )
        return out

    if effective_n and effective_n > 0:
        out["amount_per_person_derived"] = round(amount / effective_n, 2)
        out["consistent"] = None  # not enough info to judge
        return out

    out["consistent"] = None
    out["reason"] = "no headcount or per-person info in description"
    return out


# ── Bonus accessor: attendees on a specific submission ───────────────
# Used by the agent when a Layer-1 rule cites a specific other
# submission and the agent wants to see WHO was in that meal.

async def get_submission_attendees(
    db: AsyncSession, *, submission_id: str,
) -> dict[str, Any]:
    rows = (await db.execute(
        select(SubmissionAttendee).where(
            SubmissionAttendee.submission_id == submission_id
        )
    )).scalars().all()
    return {
        "submission_id": submission_id,
        "count": len(rows),
        "attendees": [
            {
                "name": r.name,
                "employee_id": r.employee_id,
                "role": r.role,
            }
            for r in rows
        ],
    }


# ── Tool registry — used by the OODA agent in PR-B ───────────────────
# Stable name → callable. PR-B will read this map to figure out which
# tools the LLM is allowed to call. Keeping it inside this module so
# adding a tool is one diff: define function + add entry here.
INVESTIGATION_TOOLS: dict[str, Any] = {
    "get_employee_profile":      get_employee_profile,
    "get_recent_expenses":       get_recent_expenses,
    "get_approval_history":      get_approval_history,
    "get_merchant_usage":        get_merchant_usage,
    "get_peer_comparison":       get_peer_comparison,
    "get_amount_distribution":   get_amount_distribution,
    "check_geo_feasibility":     check_geo_feasibility,
    "check_math_consistency":    check_math_consistency,
    "get_submission_attendees":  get_submission_attendees,
}
