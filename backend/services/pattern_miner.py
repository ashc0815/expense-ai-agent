"""Pattern miner — discover repetitive employee behavior and suggest auto-rules.

Inspired by Airwallex's "auto-categorize transactions and apply automation
rules based on patterns we detect in your behavior." The miner scans an
employee's recent submissions and looks for cases where the same merchant
is consistently logged with the same category / project_code / cost_center.
When the consistency clears a threshold, the miner emits a suggested rule.

The user is the judge. Suggestions are written to the auto_rules table with
status='suggested'. The employee accepts (→ active) or dismisses on the UI.
We never auto-activate.

Usage:
    from backend.services.pattern_miner import mine_for_employee
    new_rules = await mine_for_employee(db, employee_id="E001")
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.store import (
    AutoRule, Submission, upsert_auto_rule,
)


# Knobs. Tuned for a small dataset where 5 consistent occurrences is a
# strong-enough signal to surface; lower MIN_EVIDENCE produces noise.
MIN_EVIDENCE = 5
THRESHOLD = 0.8
LOOKBACK_DAYS = 180
MAX_SAMPLES = 5

# Fields the miner is allowed to suggest. Keeping the list narrow avoids
# accidentally suggesting rules for sensitive fields like amount or tax_id.
MINEABLE_FIELDS: tuple[str, ...] = ("category", "project_code", "cost_center")


def _normalize_merchant(name: str) -> str:
    """Trim and casefold so 'Starbucks' and 'STARBUCKS ' collapse together.
    We keep this simple — fancier normalization (chain detection, fuzzy
    match) is a future enhancement.
    """
    return (name or "").strip().casefold()


async def _recent_submissions(
    db: AsyncSession, employee_id: str, days: int
) -> list[Submission]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()[:10]
    result = await db.execute(
        select(Submission)
        .where(
            Submission.employee_id == employee_id,
            Submission.date >= cutoff,
        )
        .order_by(Submission.created_at.desc())
    )
    return list(result.scalars().all())


def _candidate_rules(
    submissions: list[Submission],
) -> list[dict[str, Any]]:
    """Pure function — given a list of submissions, return rule candidates.

    Returned shape: [{trigger_value, field, value, confidence, evidence_count,
    sample_ids}]. Caller is responsible for persisting.
    """
    # bucket: merchant_normalized -> field -> Counter of values
    buckets: dict[str, dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    samples: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for sub in submissions:
        merchant_key = _normalize_merchant(sub.merchant)
        if not merchant_key:
            continue
        for field in MINEABLE_FIELDS:
            value = getattr(sub, field, None)
            if value is None or value == "":
                continue
            buckets[merchant_key][field][value] += 1
            if len(samples[merchant_key][field][value]) < MAX_SAMPLES:
                samples[merchant_key][field][value].append(sub.id)

    candidates: list[dict[str, Any]] = []
    for merchant_key, fields in buckets.items():
        for field, counts in fields.items():
            total = sum(counts.values())
            if total < MIN_EVIDENCE:
                continue
            top_value, top_count = counts.most_common(1)[0]
            confidence = top_count / total
            if confidence < THRESHOLD:
                continue
            candidates.append({
                "trigger_value": merchant_key,
                "field": field,
                "value": top_value,
                "confidence": round(confidence, 3),
                "evidence_count": top_count,
                "sample_ids": samples[merchant_key][field][top_value],
            })

    # Strongest candidates first — the UI shows them top-down.
    candidates.sort(key=lambda c: (-c["confidence"], -c["evidence_count"]))
    return candidates


async def mine_for_employee(
    db: AsyncSession,
    employee_id: str,
    *,
    days: int = LOOKBACK_DAYS,
) -> list[AutoRule]:
    """Run the miner once for an employee, persist any new suggestions, and
    return the resulting AutoRule rows (both freshly suggested and refreshed).
    """
    submissions = await _recent_submissions(db, employee_id, days)
    candidates = _candidate_rules(submissions)
    persisted: list[AutoRule] = []
    for c in candidates:
        rule = await upsert_auto_rule(
            db,
            employee_id=employee_id,
            trigger_type="merchant_exact",
            trigger_value=c["trigger_value"],
            field=c["field"],
            value=c["value"],
            confidence=c["confidence"],
            evidence_count=c["evidence_count"],
            sample_ids=c["sample_ids"],
        )
        persisted.append(rule)
    return persisted


async def find_matching_active_rules(
    db: AsyncSession,
    *,
    employee_id: str,
    merchant: Optional[str],
) -> list[AutoRule]:
    """Return active rules whose trigger matches the given merchant (used by
    the auto-applier). Only matches employee-scoped rules for now.
    """
    if not merchant:
        return []
    key = _normalize_merchant(merchant)
    result = await db.execute(
        select(AutoRule).where(
            AutoRule.employee_id == employee_id,
            AutoRule.status == "active",
            AutoRule.trigger_type == "merchant_exact",
            AutoRule.trigger_value == key,
        )
    )
    return list(result.scalars().all())


async def apply_rules_to_draft(
    db: AsyncSession,
    *,
    employee_id: str,
    draft_id: str,
    merchant: Optional[str],
    overwrite: bool = True,
) -> list[dict]:
    """Apply every active rule for this merchant to the draft. Returns a list
    of {rule_id, field, value} for each rule that fired so the caller can
    surface them to the user and write an audit log.

    `overwrite=True` means rule values replace whatever the classifier wrote
    (the user already certified this mapping, so it outranks heuristic
    suggestions). When False, only empty fields are filled.
    """
    from backend.db.store import (
        get_draft, increment_rule_applied, update_draft_field,
    )

    rules = await find_matching_active_rules(
        db, employee_id=employee_id, merchant=merchant,
    )
    if not rules:
        return []

    draft = await get_draft(db, draft_id)
    if draft is None:
        return []
    fields = dict(draft.fields or {})

    applied: list[dict] = []
    for rule in rules:
        existing = fields.get(rule.field)
        if not overwrite and existing:
            continue
        if existing == rule.value:
            # Already correct; no need to write or count.
            continue
        await update_draft_field(
            db, draft_id, rule.field, rule.value, source="auto_rule",
        )
        await increment_rule_applied(db, rule.id)
        applied.append({
            "rule_id": rule.id,
            "field": rule.field,
            "value": rule.value,
        })
    return applied
