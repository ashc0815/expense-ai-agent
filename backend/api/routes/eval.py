"""Eval Observatory API — browse eval runs, traces, and case results.

Endpoints:
  GET  /api/eval/runs              List eval runs (paginated)
  GET  /api/eval/runs/{id}         Single run detail
  POST /api/eval/runs              Record a new eval run
  GET  /api/eval/traces            List LLM traces (filterable)
  GET  /api/eval/traces/{id}       Single trace detail
  GET  /api/eval/stats             Aggregate stats (pass rate trend, component breakdown)
  GET  /api/eval/config            Read current eval config (6 factors)
  PUT  /api/eval/config            Update eval config
  GET  /api/eval/runs/{id1}/diff/{id2}  Compare metadata between two runs
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.store import EvalRun, LLMTrace, get_eval_db

router = APIRouter()

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval_config.json"
_PROMPTS_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval_prompts.json"
_HUMAN_FRAUD_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval_human_fraud_latest.json"
_HUMAN_AMBIG_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval_human_ambiguity_latest.json"


# ── Eval Runs ────────────────────────────────────────────────────────

@router.get("/runs")
async def list_eval_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_eval_db),
) -> dict:
    q = select(EvalRun).order_by(desc(EvalRun.started_at))
    total = (await db.execute(select(func.count()).select_from(EvalRun))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "items": [_run_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str, db: AsyncSession = Depends(get_eval_db)) -> dict:
    result = await db.execute(select(EvalRun).where(EvalRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        return {"error": "not found"}
    return _run_to_dict(run)


@router.post("/runs")
async def create_eval_run(body: dict, db: AsyncSession = Depends(get_eval_db)) -> dict:
    """Record an eval run (called by the harness after completion)."""
    import uuid
    run = EvalRun(
        id=str(uuid.uuid4()),
        started_at=datetime.fromisoformat(body["started_at"]) if "started_at" in body else datetime.now(timezone.utc),
        finished_at=datetime.fromisoformat(body["finished_at"]) if "finished_at" in body else datetime.now(timezone.utc),
        total_cases=body.get("total_cases", 0),
        passed_cases=body.get("passed_cases", 0),
        pass_rate=body.get("pass_rate", 0.0),
        results=body.get("results"),
        trigger=body.get("trigger", "manual"),
        run_metadata=body.get("metadata"),
        component_metrics=body.get("component_metrics"),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return _run_to_dict(run)


# ── LLM Traces ───────────────────────────────────────────────────────

@router.get("/traces")
async def list_traces(
    component: Optional[str] = None,
    submission_id: Optional[str] = None,
    has_error: Optional[bool] = None,
    sort: str = Query("created_at", pattern="^(created_at|latency_ms|component)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_eval_db),
) -> dict:
    q = select(LLMTrace)

    if component:
        # Accept comma-separated list so a single UI filter (e.g. "Chat Agent")
        # can match multiple concrete component values (chat_employee_submit /
        # chat_employee_qa / chat_manager_explain).
        values = [v.strip() for v in component.split(",") if v.strip()]
        if len(values) == 1:
            q = q.where(LLMTrace.component == values[0])
        elif len(values) > 1:
            q = q.where(LLMTrace.component.in_(values))
    if submission_id:
        q = q.where(LLMTrace.submission_id == submission_id)
    if has_error is True:
        q = q.where(LLMTrace.error.isnot(None))
    elif has_error is False:
        q = q.where(LLMTrace.error.is_(None))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    sort_col = getattr(LLMTrace, sort)
    q = q.order_by(desc(sort_col) if order == "desc" else sort_col)
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "items": [_trace_to_dict(t) for t in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, db: AsyncSession = Depends(get_eval_db)) -> dict:
    result = await db.execute(select(LLMTrace).where(LLMTrace.id == trace_id))
    trace = result.scalar_one_or_none()
    if not trace:
        return {"error": "not found"}
    return _trace_to_dict(trace, include_prompt=True)


# ── Stats ─────────────────────────────────────────────────────────────

@router.get("/stats")
async def eval_stats(db: AsyncSession = Depends(get_eval_db)) -> dict:
    """Aggregate stats: recent pass rates + component breakdown of traces."""
    # Recent 10 eval runs for trend
    runs = (await db.execute(
        select(EvalRun).order_by(desc(EvalRun.started_at)).limit(10)
    )).scalars().all()

    trend = [
        {"date": r.started_at.isoformat() if r.started_at else None, "pass_rate": r.pass_rate}
        for r in reversed(list(runs))
    ]

    # Trace count by component
    comp_counts = (await db.execute(
        select(LLMTrace.component, func.count(LLMTrace.id))
        .group_by(LLMTrace.component)
    )).all()

    # Error rate by component
    error_counts = (await db.execute(
        select(LLMTrace.component, func.count(LLMTrace.id))
        .where(LLMTrace.error.isnot(None))
        .group_by(LLMTrace.component)
    )).all()
    error_map = dict(error_counts)

    components = []
    for comp, count in comp_counts:
        errors = error_map.get(comp, 0)
        components.append({
            "component": comp,
            "total_traces": count,
            "error_count": errors,
            "error_rate": round(errors / count, 4) if count > 0 else 0,
        })

    return {"trend": trend, "components": components}


# ── Serializers ───────────────────────────────────────────────────────

def _run_to_dict(r: EvalRun) -> dict:
    return {
        "id": r.id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "total_cases": r.total_cases,
        "passed_cases": r.passed_cases,
        "pass_rate": r.pass_rate,
        "results": r.results,
        "trigger": r.trigger,
        "metadata": r.run_metadata,
        "component_metrics": r.component_metrics,
    }


# ── Eval Config ──────────────────────────────────────────────────────

@router.get("/config")
async def get_eval_config() -> dict:
    """Read current eval config (the 6 tunable factors)."""
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


@router.put("/config")
async def update_eval_config(body: dict) -> dict:
    """Update eval config. Merges with existing config."""
    existing = {}
    if _CONFIG_PATH.exists():
        existing = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    existing.update(body)
    _CONFIG_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return existing


# ── Run Eval Trigger ────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_eval_running = False


@router.post("/trigger")
async def trigger_eval(body: dict = {}) -> dict:
    """Trigger an eval run via pytest subprocess.

    Body (optional):
      component: "fraud" | "ambiguity" | "deterministic" | "all"

    Returns immediately with status; results appear in /runs after completion.
    """
    global _eval_running
    if _eval_running:
        return {"status": "already_running"}

    component = body.get("component", "deterministic")
    # Map component to pytest -k filter
    k_filter = {
        "fraud": "deterministic or layer or classifier",
        "fraud_llm": "llm",
        "ambiguity": "ambiguity",
        "deterministic": "deterministic or layer or classifier",
        "all": "",
    }.get(component, "deterministic or layer or classifier")

    cmd = [
        sys.executable, "-m", "pytest",
        "backend/tests/test_eval_harness.py",
        "-q", "--tb=short",
    ]
    if k_filter:
        cmd += ["-k", k_filter]

    _eval_running = True

    async def _run():
        global _eval_running
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(_PROJECT_ROOT),
                env={**__import__("os").environ, "PYTHONPATH": str(_PROJECT_ROOT)},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return {
                "returncode": proc.returncode,
                "stdout": stdout.decode(errors="replace")[-2000:],
                "stderr": stderr.decode(errors="replace")[-2000:],
            }
        finally:
            _eval_running = False

    # Run in background — results post to /runs via harness teardown
    asyncio.create_task(_run())
    return {"status": "started", "component": component, "k_filter": k_filter}


@router.get("/trigger/status")
async def trigger_status() -> dict:
    """Check if an eval run is currently in progress."""
    return {"running": _eval_running}


# ── Run Diff ─────────────────────────────────────────────────────────

@router.get("/runs/{run_id_a}/diff/{run_id_b}")
async def diff_runs(run_id_a: str, run_id_b: str, db: AsyncSession = Depends(get_eval_db)) -> dict:
    """Compare metadata and results between two eval runs."""
    ra = (await db.execute(select(EvalRun).where(EvalRun.id == run_id_a))).scalar_one_or_none()
    rb = (await db.execute(select(EvalRun).where(EvalRun.id == run_id_b))).scalar_one_or_none()
    if not ra or not rb:
        return {"error": "one or both runs not found"}

    meta_a = ra.run_metadata or {}
    meta_b = rb.run_metadata or {}

    # Find changed metadata fields
    all_keys = set(list(meta_a.keys()) + list(meta_b.keys()))
    meta_diff = {}
    for k in sorted(all_keys):
        va, vb = meta_a.get(k), meta_b.get(k)
        if va != vb:
            meta_diff[k] = {"a": va, "b": vb}

    # Find case result changes (PASS↔FAIL)
    cases_a = {c["case_id"]: c["passed"] for c in (ra.results or [])}
    cases_b = {c["case_id"]: c["passed"] for c in (rb.results or [])}
    all_cases = set(list(cases_a.keys()) + list(cases_b.keys()))
    case_diff = []
    for cid in sorted(all_cases):
        pa, pb = cases_a.get(cid), cases_b.get(cid)
        if pa != pb:
            case_diff.append({"case_id": cid, "a": pa, "b": pb})

    return {
        "run_a": {"id": ra.id, "pass_rate": ra.pass_rate, "total": ra.total_cases, "started_at": ra.started_at.isoformat() if ra.started_at else None},
        "run_b": {"id": rb.id, "pass_rate": rb.pass_rate, "total": rb.total_cases, "started_at": rb.started_at.isoformat() if rb.started_at else None},
        "metadata_diff": meta_diff,
        "case_diff": case_diff,
        "summary": {
            "metadata_changes": len(meta_diff),
            "regressions": sum(1 for c in case_diff if c.get("a") is True and c.get("b") is False),
            "improvements": sum(1 for c in case_diff if c.get("a") is False and c.get("b") is True),
        },
    }


# ── Prompt Management ────────────────────────────────────────────────

def _load_prompts() -> dict:
    if _PROMPTS_PATH.exists():
        return json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
    return {"prompts": {}}


def _save_prompts(data: dict) -> None:
    _PROMPTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@router.get("/prompts")
async def list_prompts() -> dict:
    """List all prompt templates with their versions."""
    data = _load_prompts()
    # Return summary (without full content for list view)
    summary = {}
    for key, p in data.get("prompts", {}).items():
        versions = p.get("versions", {})
        summary[key] = {
            "name": p.get("name"),
            "component": p.get("component"),
            "description": p.get("description"),
            "active_version": p.get("active_version"),
            "version_count": len(versions),
            "version_list": sorted(versions.keys()),
        }
    return {"prompts": summary}


@router.get("/prompts/{prompt_key}")
async def get_prompt(prompt_key: str) -> dict:
    """Get a prompt template with all its versions (full content)."""
    data = _load_prompts()
    prompt = data.get("prompts", {}).get(prompt_key)
    if not prompt:
        return {"error": "prompt not found"}
    return {"key": prompt_key, **prompt}


@router.get("/prompts/{prompt_key}/versions/{version}")
async def get_prompt_version(prompt_key: str, version: str) -> dict:
    """Get a specific prompt version's content."""
    data = _load_prompts()
    prompt = data.get("prompts", {}).get(prompt_key)
    if not prompt:
        return {"error": "prompt not found"}
    ver = prompt.get("versions", {}).get(version)
    if not ver:
        return {"error": "version not found"}
    return {"key": prompt_key, "version": version, **ver}


@router.put("/prompts/{prompt_key}/versions/{version}")
async def save_prompt_version(prompt_key: str, version: str, body: dict) -> dict:
    """Create or update a prompt version. Body: {content, notes}."""
    data = _load_prompts()
    prompts = data.setdefault("prompts", {})

    if prompt_key not in prompts:
        return {"error": "prompt key not found — use an existing key"}

    versions = prompts[prompt_key].setdefault("versions", {})
    versions[version] = {
        "content": body.get("content", ""),
        "notes": body.get("notes", ""),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    _save_prompts(data)
    return {"key": prompt_key, "version": version, "saved": True}


@router.put("/prompts/{prompt_key}/active")
async def set_active_version(prompt_key: str, body: dict) -> dict:
    """Set the active prompt version. Body: {version: "v2"}."""
    data = _load_prompts()
    prompt = data.get("prompts", {}).get(prompt_key)
    if not prompt:
        return {"error": "prompt not found"}
    version = body.get("version")
    if version not in prompt.get("versions", {}):
        return {"error": f"version '{version}' does not exist"}
    prompt["active_version"] = version
    _save_prompts(data)
    return {"key": prompt_key, "active_version": version}


# ── Human-Labeled Evals (fraud subfield matrix + ambiguity confusion) ──

@router.get("/human/fraud")
async def get_human_fraud_eval() -> dict:
    """Return the latest fraud analyzer human-labeled eval results.

    File is written by pytest backend/tests/test_human_eval.py::test_fraud_human_eval.
    Returns {empty: true} if the file does not exist yet (before first run).
    """
    if not _HUMAN_FRAUD_PATH.exists():
        return {"empty": True, "message": "No fraud human-eval run yet. Run: pytest backend/tests/test_human_eval.py"}
    try:
        return json.loads(_HUMAN_FRAUD_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"empty": True, "error": str(exc)}


@router.get("/human/ambiguity")
async def get_human_ambiguity_eval() -> dict:
    """Return the latest ambiguity detector human-labeled eval results.

    File is written by pytest backend/tests/test_human_eval.py::test_ambiguity_human_eval.
    """
    if not _HUMAN_AMBIG_PATH.exists():
        return {"empty": True, "message": "No ambiguity human-eval run yet. Run: pytest backend/tests/test_human_eval.py"}
    try:
        return json.loads(_HUMAN_AMBIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"empty": True, "error": str(exc)}


# ── Serializers ───────────────────────────────────────────────────────

def _trace_to_dict(t: LLMTrace, include_prompt: bool = False) -> dict:
    d: dict = {
        "id": t.id,
        "component": t.component,
        "submission_id": t.submission_id,
        "model": t.model,
        "latency_ms": t.latency_ms,
        "token_usage": t.token_usage,
        "error": t.error,
        "parsed_output": t.parsed_output,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if include_prompt:
        d["prompt"] = t.prompt
        d["response"] = t.response
    return d
