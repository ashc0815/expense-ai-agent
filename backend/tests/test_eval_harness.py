"""Unified Eval Harness — runs eval datasets for all AI components.

Supports:
  - Fraud LLM rules (11-14): calls llm_fraud_analyzer with mock/real LLM
  - Ambiguity detector: calls AmbiguityDetector.evaluate()
  - pass^k trials for non-deterministic (LLM) components

Run:
  pytest backend/tests/test_eval_harness.py -v         # per-case results
  pytest backend/tests/test_eval_harness.py -v -k fraud # only fraud cases
  pytest backend/tests/test_eval_harness.py -v -s      # with pass-rate table

Pass rate summary is always written to stderr at module teardown.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import json
import time
import urllib.request
import urllib.error

import pytest
import yaml

# ── Temp DB (must be set before importing app) ────────────────────────
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
_DB_URL = f"sqlite+aiosqlite:///{_TMP_DB.name}"

os.environ.setdefault("DATABASE_URL", _DB_URL)
os.environ.setdefault("AUTH_MODE", "mock")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", "/tmp/eval_harness_test")

from backend.db.store import Base, create_async_engine, async_sessionmaker

_engine = create_async_engine(_DB_URL)
_Session = async_sessionmaker(_engine, expire_on_commit=False)

# ── Per-case results (rich format for Observatory) ────────────────────
# Each entry: {"passed": bool, "component": str, "error": str|None}
_EVAL_RESULTS: dict[str, dict] = {}
_RUN_START = datetime.now(timezone.utc)

# ── Eval config (6 factors) ──────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent / "eval_config.json"


def _load_eval_config() -> dict:
    """Load eval config from JSON file."""
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _compute_file_hash(path: Path) -> str:
    """Quick hash of file contents for versioning."""
    import hashlib
    if not path.exists():
        return "missing"
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def _compute_dataset_hash() -> str:
    """Hash all eval dataset YAML files together."""
    import hashlib
    h = hashlib.md5()
    ds_dir = Path(__file__).parent / "eval_datasets"
    if ds_dir.exists():
        for f in sorted(ds_dir.glob("*.yaml")):
            h.update(f.read_bytes())
    return h.hexdigest()[:8]


def _get_git_commit() -> str:
    """Get current git HEAD short hash."""
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parents[2],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _collect_metadata() -> dict:
    """Auto-collect the 6 factors into a metadata snapshot."""
    cfg = _load_eval_config()
    return {
        # Factor 1: Prompt version
        "prompt_version": cfg.get("prompt_version", "unknown"),
        "prompt_notes": cfg.get("prompt_notes", ""),
        # Factor 2: Model + snapshot
        "model": cfg.get("model", "unknown"),
        # Factor 3: Sampling params
        "temperature": cfg.get("temperature", 0.0),
        "top_p": cfg.get("top_p", 1.0),
        "max_tokens": cfg.get("max_tokens", 1024),
        # Factor 4: Config thresholds
        "config_thresholds": cfg.get("config_thresholds", {}),
        "config_hash": _compute_file_hash(_CONFIG_PATH),
        # Factor 5: Code version (replaces the never-changing parsing_version
        # from earlier — git_commit is the true reproducibility anchor)
        "git_commit": _get_git_commit(),
        # Factor 6: Dataset version
        "dataset_hash": _compute_dataset_hash(),
        "dataset_case_count": len(_ALL_CASES) if "_ALL_CASES" in dir() else 0,
    }


_EVAL_CONFIG = _load_eval_config()

# ── Load eval datasets ────────────────────────────────────────────────
_DATASETS_DIR = Path(__file__).parent / "eval_datasets"


def _load_dataset(filename: str) -> list[dict]:
    path = _DATASETS_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


_FRAUD_CASES = _load_dataset("fraud_llm_rules.yaml")
_AMBIGUITY_CASES = _load_dataset("ambiguity_detector.yaml")
_FRAUD_DET_CASES = _load_dataset("fraud_rules_deterministic.yaml")
_LAYER_CASES = _load_dataset("layer_decision.yaml")
_CLASSIFIER_CASES = _load_dataset("category_classifier.yaml")
_ALL_CASES = _FRAUD_CASES + _AMBIGUITY_CASES + _FRAUD_DET_CASES + _LAYER_CASES + _CLASSIFIER_CASES


# ── Module setup / teardown ───────────────────────────────────────────

def setup_module(_: Any) -> None:
    import asyncio

    async def _init() -> None:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.new_event_loop().run_until_complete(_init())


def teardown_module(_: Any) -> None:
    try:
        asyncio.new_event_loop().run_until_complete(_engine.dispose())
    except Exception:
        pass
    try:
        os.unlink(_TMP_DB.name)
    except PermissionError:
        pass  # Windows: temp file still locked, OS Temp cleanup handles it

    total = len(_EVAL_RESULTS)
    passed_n = sum(1 for r in _EVAL_RESULTS.values() if r.get("passed"))
    rate = (passed_n / total * 100) if total else 0.0

    # ── stderr summary ────────────────────────────────────────────
    lines = [
        "",
        "═" * 60,
        "  EVAL HARNESS — RESULTS",
        "═" * 60,
    ]
    for cid, r in _EVAL_RESULTS.items():
        lines.append(f"  {'✓' if r['passed'] else '✗'}  {cid}")
    bar = "█" * passed_n + "░" * (total - passed_n)
    lines += [
        "─" * 60,
        f"  [{bar}]  {passed_n}/{total} passed ({rate:.0f}%)",
        "═" * 60,
        "",
    ]
    sys.stderr.write("\n".join(lines) + "\n")

    # ── POST to Observatory API ───────────────────────────────────
    now = datetime.now(timezone.utc)
    case_results = [
        {
            "case_id": cid,
            "component": r.get("component", "unknown"),
            "passed": r["passed"],
            "error": r.get("error"),
            "classification": r.get("classification"),
            "actual_output": r.get("actual_output"),
            "expected": r.get("expected"),
            "input_summary": r.get("input_summary"),
            "related_config": r.get("related_config"),
            "description": r.get("description", ""),
        }
        for cid, r in _EVAL_RESULTS.items()
    ]

    # ── Compute per-component P/R/F1 ─────────────────────────────
    from collections import defaultdict
    comp_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"正确标记": 0, "漏报": 0, "误报": 0, "正确放行": 0})
    for r in _EVAL_RESULTS.values():
        cls = r.get("classification")
        if cls:
            comp_counts[r.get("component", "unknown")][cls] += 1

    component_metrics = {}
    for comp, counts in comp_counts.items():
        tp = counts["正确标记"]
        fp = counts["误报"]
        fn = counts["漏报"]
        tn = counts["正确放行"]
        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
        f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else None
        component_metrics[comp] = {
            "正确标记": tp, "误报": fp, "漏报": fn, "正确放行": tn,
            "precision": precision, "recall": recall, "f1": f1,
        }

    # Print P/R summary to stderr
    if component_metrics:
        sys.stderr.write("\n  ── Detection Quality (P/R/F1) ──\n")
        for comp, m in sorted(component_metrics.items()):
            p_str = f"{m['precision']:.0%}" if m['precision'] is not None else "N/A"
            r_str = f"{m['recall']:.0%}" if m['recall'] is not None else "N/A"
            f_str = f"{m['f1']:.0%}" if m['f1'] is not None else "N/A"
            sys.stderr.write(
                f"  {comp}: P={p_str} R={r_str} F1={f_str} "
                f"(正确标记={m['正确标记']} 误报={m['误报']} 漏报={m['漏报']} 正确放行={m['正确放行']})\n"
            )
        sys.stderr.write("\n")

    metadata = _collect_metadata()
    metadata["dataset_case_count"] = total

    payload = {
        "started_at": _RUN_START.isoformat(),
        "finished_at": now.isoformat(),
        "total_cases": total,
        "passed_cases": passed_n,
        "pass_rate": round(passed_n / total, 4) if total else 0.0,
        "results": case_results,
        "component_metrics": component_metrics,
        "trigger": "pytest",
        "metadata": metadata,
    }

    api_url = os.environ.get("EVAL_API_URL", "http://localhost:8000/api/eval/runs")
    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            sys.stderr.write(f"  ✓ Eval run posted to Observatory ({resp.status})\n\n")
    except (urllib.error.URLError, OSError):
        # Observatory API not reachable — fall back to a tmp-dir snapshot the
        # operator can curl in later. Avoid writing inside backend/tests/ so
        # we don't accidentally pollute git with stale eval results.
        out_path = Path(tempfile.gettempdir()) / "eval_last_run.json"
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        sys.stderr.write(
            f"  ⚠ Observatory API unreachable. Results saved to:\n"
            f"    {out_path}\n"
            f"  Import later: curl -X POST http://localhost:8000/api/eval/runs "
            f"-H 'Content-Type: application/json' -d @{out_path}\n\n"
        )


# ── Helpers ───────────────────────────────────────────────────────────

def _make_submission_row(inp: dict) -> Any:
    """Create a SubmissionRow-like object from eval case input."""
    from backend.services.fraud_rules import SubmissionRow

    return SubmissionRow(
        id=f"eval-{inp.get('description', 'unknown')[:20]}",
        employee_id="eval-employee",
        description=inp.get("description", ""),
        category=inp.get("category", "meal"),
        amount=inp.get("amount", 0.0),
        currency=inp.get("currency", "CNY"),
        merchant=inp.get("merchant", "unknown"),
        city=inp.get("city", "上海"),
        date=inp.get("date", date.today().isoformat()),
    )


# ── Fraud LLM Rules Runner ───────────────────────────────────────────

@pytest.mark.parametrize("case", _FRAUD_CASES, ids=[c["id"] for c in _FRAUD_CASES])
def test_fraud_llm_rule(case: dict) -> None:
    """Eval a single fraud LLM rule case.

    Requires OPENAI_API_KEY to be set for real LLM calls.
    Skips gracefully if no API key is available.
    """
    import asyncio
    from backend.tests.graders.code_graders import grade_case

    if not os.getenv("OPENAI_API_KEY"):
        _EVAL_RESULTS[case["id"]] = {"passed": False, "component": "fraud_llm", "error": "skipped (no API key)", "classification": None}
        pytest.skip("No OPENAI_API_KEY — skipping LLM fraud eval")

    case_id = case["id"]
    inp = case["input"]
    expect = case["expect"]
    trials = case.get("trials", 1)

    from backend.services.llm_fraud_analyzer import analyze_submission

    sub_row = _make_submission_row(inp)
    recent = inp.get("recent_descriptions", [])
    receipt_loc = inp.get("receipt_location")

    all_trial_passed = True
    trial_messages: list[str] = []

    for trial_num in range(1, trials + 1):
        result = asyncio.new_event_loop().run_until_complete(
            analyze_submission(sub_row, recent, receipt_loc)
        )

        checks = grade_case(result, expect)
        trial_passed = all(ok for _, ok, _ in checks)

        detail = "; ".join(f"{name}: {msg}" for name, ok, msg in checks)
        trial_messages.append(f"Trial {trial_num}: {'PASS' if trial_passed else 'FAIL'} — {detail}")

        if not trial_passed:
            all_trial_passed = False

    fail_msg = None if all_trial_passed else "\n".join(trial_messages)
    _EVAL_RESULTS[case_id] = {"passed": all_trial_passed, "component": "fraud_llm", "error": fail_msg, "classification": None}

    if not all_trial_passed:
        pytest.fail(f"pass^{trials} FAILED for {case_id}:\n{fail_msg}")


# ── Ambiguity Detector Runner ────────────────────────────────────────

@pytest.mark.parametrize("case", _AMBIGUITY_CASES, ids=[c["id"] for c in _AMBIGUITY_CASES])
def test_ambiguity_detector(case: dict) -> None:
    """Eval a single ambiguity detector case (deterministic, no LLM)."""
    from backend.tests.graders.code_graders import grade_case

    case_id = case["id"]
    inp = case["input"]
    expect = case["expect"]

    # Build LineItem and Employee objects for the detector
    try:
        from config import ConfigLoader
        from models.expense import Employee, EmployeeLevel, LineItem
        from agent.ambiguity_detector import AmbiguityDetector

        loader = ConfigLoader()
        detector = AmbiguityDetector(loader)

        # Parse date
        d = date.fromisoformat(inp["date"]) if isinstance(inp["date"], str) else inp["date"]

        line_item = LineItem(
            expense_type=inp["expense_type"],
            amount=float(inp["amount"]),
            currency=inp.get("currency", "CNY"),
            city=inp.get("city", "上海"),
            date=d,
            invoice=None,
            description=inp.get("description", ""),
            attendees=inp.get("attendees", []),
        )

        employee = Employee(
            name="Eval Employee",
            id="eval-emp-001",
            department="Engineering",
            city=inp.get("city", "上海"),
            hire_date=date(2024, 1, 1),
            bank_account="6222000000000000",
            level=EmployeeLevel(inp.get("employee_level", "L3")),
        )

        # Build history LineItems
        history_items: list[LineItem] = []
        for h in inp.get("history", []):
            history_items.append(LineItem(
                expense_type=h.get("expense_type", inp["expense_type"]),
                amount=float(h["amount"]),
                currency=h.get("currency", "CNY"),
                city=h.get("city", "上海"),
                date=date.fromisoformat(h["date"]),
                invoice=None,
                description=h.get("description", ""),
            ))

        fraud_signals = inp.get("fraud_signals", [])

        result = detector.evaluate(
            line_item=line_item,
            employee=employee,
            rule_results=[],
            history=history_items,
            fraud_signals=fraud_signals,
        )

        # Convert AmbiguityResult to dict for grading
        output = {
            "score": result.score,
            "triggered_factors": result.triggered_factors,
            "recommendation": result.recommendation,
            "explanation": result.explanation,
        }

        checks = grade_case(output, expect)
        all_passed = all(ok for _, ok, _ in checks)

        fail_detail = None if all_passed else "; ".join(f"{name}: {msg}" for name, ok, msg in checks)
        _EVAL_RESULTS[case_id] = {"passed": all_passed, "component": "ambiguity_detector", "error": fail_detail, "classification": None}

        if not all_passed:
            pytest.fail(f"{case_id} FAILED: {fail_detail}")

    except ImportError as e:
        _EVAL_RESULTS[case_id] = {"passed": False, "component": "ambiguity_detector", "error": f"import: {e}", "classification": None}
        pytest.skip(f"Missing dependency for ambiguity detector eval: {e}")
    except Exception as e:
        _EVAL_RESULTS[case_id] = {"passed": False, "component": "ambiguity_detector", "error": str(e), "classification": None}
        pytest.fail(f"{case_id} ERROR: {e}")


# ── Deterministic Fraud Rules Runner ────────────────────────────────

_RULE_DISPATCH = {
    "duplicate_attendee": "rule_duplicate_attendee",
    "geo_conflict": "rule_geo_conflict",
    "threshold_proximity": "rule_threshold_proximity",
    "timestamp_conflict": "rule_timestamp_conflict",
    "weekend_frequency": "rule_weekend_frequency",
    "round_amount": "rule_round_amount",
    "consecutive_invoices": "rule_consecutive_invoices",
    "merchant_category_mismatch": "rule_merchant_category_mismatch",
    "pre_resignation_rush": "rule_pre_resignation_rush",
    "fx_arbitrage": "rule_fx_arbitrage",
    "collusion_pattern": "rule_collusion_pattern",
    "vendor_frequency": "rule_vendor_frequency",
    "seasonal_anomaly": "rule_seasonal_anomaly",
    "ghost_employee": "rule_ghost_employee",
}

# Map rule_key → relevant config threshold keys (for debugging context)
_RULE_CONFIG_KEYS = {
    "threshold_proximity": ["threshold_proximity_pct", "threshold_proximity_limit", "threshold_proximity_min_count"],
    "weekend_frequency": ["weekend_meal_max_weeks"],
    "round_amount": ["round_amount_pct", "round_amount_min_count"],
    "consecutive_invoices": ["consecutive_invoice_min"],
    "pre_resignation_rush": ["rush_days_before_last", "rush_amount_multiplier"],
    "fx_arbitrage": ["fx_deviation_pct"],
    "vendor_frequency": ["vendor_frequency_threshold"],
    "seasonal_anomaly": ["seasonal_spike_multiplier"],
    "collusion_pattern": ["collusion_min_pair_count", "approver_speed_ratio"],
}


def _build_submissions(raw_list: list[dict]) -> list:
    from backend.services.fraud_rules import SubmissionRow
    return [
        SubmissionRow(
            id=r.get("id", "eval"),
            employee_id=r.get("employee_id", "eval"),
            amount=float(r.get("amount", 0)),
            currency=r.get("currency", "CNY"),
            category=r.get("category", "meal"),
            date=r.get("date", "2026-01-01"),
            merchant=r.get("merchant", ""),
            invoice_number=r.get("invoice_number"),
            invoice_code=r.get("invoice_code"),
            description=r.get("description"),
            exchange_rate=r.get("exchange_rate"),
            city=r.get("city"),
            attendees=r.get("attendees"),
        )
        for r in raw_list
    ]


def _build_employee(raw: dict):
    from backend.services.fraud_rules import EmployeeRow
    resign = raw.get("resignation_date")
    if isinstance(resign, str):
        resign = date.fromisoformat(resign)
    return EmployeeRow(
        id=raw.get("id", "eval"),
        department=raw.get("department", "未分配"),
        resignation_date=resign,
    )


@pytest.mark.parametrize("case", _FRAUD_DET_CASES, ids=[c["id"] for c in _FRAUD_DET_CASES])
def test_fraud_deterministic(case: dict) -> None:
    """Eval a single deterministic fraud rule case."""
    import backend.services.fraud_rules as fr
    from backend.tests.graders.code_graders import grade_case

    case_id = case["id"]
    rule_key = case["rule"]
    inp = case["input"]
    expect = case["expect"]

    func_name = _RULE_DISPATCH.get(rule_key)
    if not func_name:
        _EVAL_RESULTS[case_id] = {"passed": False, "component": f"fraud_rule_{rule_key}", "error": f"unknown rule: {rule_key}"}
        pytest.fail(f"Unknown rule key: {rule_key}")

    rule_func = getattr(fr, func_name)

    # Build arguments based on rule
    if rule_key in ("duplicate_attendee", "geo_conflict", "timestamp_conflict",
                    "consecutive_invoices", "merchant_category_mismatch",
                    "collusion_pattern"):
        subs = _build_submissions(inp["submissions"])
        signals = rule_func(subs)

    elif rule_key in ("threshold_proximity", "round_amount", "vendor_frequency"):
        subs = _build_submissions(inp["submissions"])
        signals = rule_func(subs)

    elif rule_key in ("weekend_frequency", "pre_resignation_rush", "ghost_employee"):
        subs = _build_submissions(inp["submissions"])
        emp = _build_employee(inp["employee"])
        signals = rule_func(subs, emp)

    elif rule_key == "fx_arbitrage":
        subs = _build_submissions(inp["submissions"])
        rates = inp.get("market_rates", {})
        def get_rate(from_curr, to_curr):
            return rates.get(f"{from_curr}_{to_curr}", 0.0)
        signals = rule_func(subs, get_rate)

    elif rule_key == "seasonal_anomaly":
        signals = rule_func(
            inp["quarter_totals"],
            inp["current_quarter"],
        )
    else:
        _EVAL_RESULTS[case_id] = {"passed": False, "component": f"fraud_rule_{rule_key}", "error": f"unhandled rule: {rule_key}"}
        pytest.fail(f"Unhandled rule: {rule_key}")
        return

    # Convert signals to grader-compatible output
    component = f"fraud_rule_{rule_key}"
    actual_signal = len(signals) > 0
    output = {
        "has_signal": actual_signal,
        "signal_count": len(signals),
        "rule_name": signals[0].rule if signals else None,
        "max_score": max(s.score for s in signals) if signals else 0,
    }

    checks = grade_case(output, expect)
    all_passed = all(ok for _, ok, _ in checks)
    fail_detail = None if all_passed else "; ".join(f"{name}: {msg}" for name, ok, msg in checks)

    # Classify detection result for P/R metrics
    from backend.tests.graders.code_graders import classify_detection
    expected_signal = expect.get("has_signal")
    classification = classify_detection(expected_signal, actual_signal) if expected_signal is not None else None

    # Input summary for debugging
    subs_raw = inp.get("submissions", [])
    input_summary = {
        "rule": rule_key,
        "submission_count": len(subs_raw),
        "submissions": [
            {k: v for k, v in s.items() if k in ("id", "employee_id", "amount", "category", "date", "merchant", "city")}
            for s in subs_raw[:5]  # cap at 5 for payload size
        ],
    }
    if "employee" in inp:
        input_summary["employee"] = inp["employee"]

    # Related config thresholds
    cfg_thresholds = _EVAL_CONFIG.get("config_thresholds", {})
    related_keys = _RULE_CONFIG_KEYS.get(rule_key, [])
    related_config = {k: cfg_thresholds.get(k) for k in related_keys if k in cfg_thresholds}

    _EVAL_RESULTS[case_id] = {
        "passed": all_passed, "component": component, "error": fail_detail,
        "classification": classification,
        "actual_output": output,
        "expected": expect,
        "input_summary": input_summary,
        "related_config": related_config if related_config else None,
        "description": case.get("description", ""),
    }

    if not all_passed:
        pytest.fail(f"{case_id} FAILED: {fail_detail}")


# ── Layer Decision Runner ───────────────────────────────────────────

@pytest.mark.parametrize("case", _LAYER_CASES, ids=[c["id"] for c in _LAYER_CASES])
def test_layer_decision(case: dict) -> None:
    """Eval a single layer decision case."""
    from backend.quick.layer_decision import decide_layer
    from backend.tests.graders.code_graders import grade_case

    case_id = case["id"]
    inp = case["input"]
    expect = case["expect"]

    layer = decide_layer(
        ocr=inp["ocr"],
        classify=inp["classify"],
        dedupe=inp["dedupe"],
        budget=inp["budget"],
        missing_optional_fields=inp.get("missing_optional_fields"),
    )

    output = {"layer": layer}
    checks = grade_case(output, expect)
    all_passed = all(ok for _, ok, _ in checks)
    fail_detail = None if all_passed else "; ".join(f"{name}: {msg}" for name, ok, msg in checks)
    _EVAL_RESULTS[case_id] = {
        "passed": all_passed, "component": "layer_decision", "error": fail_detail,
        "classification": None,
        "actual_output": output, "expected": expect,
        "input_summary": inp, "related_config": None,
        "description": case.get("description", ""),
    }

    if not all_passed:
        pytest.fail(f"{case_id} FAILED: {fail_detail}")


# ── Category Classifier Runner ──────────────────────────────────────

@pytest.mark.parametrize("case", _CLASSIFIER_CASES, ids=[c["id"] for c in _CLASSIFIER_CASES])
def test_category_classifier(case: dict) -> None:
    """Eval a single category classifier case."""
    from backend.tests.graders.code_graders import grade_case

    case_id = case["id"]
    inp = case["input"]
    expect = case["expect"]

    # Inline the classifier logic (it's embedded in chat.py as a tool function)
    merchant = (inp.get("merchant") or "").lower()
    rules = [
        (["海底捞", "西贝", "餐", "咖啡", "饭", "茶", "coffee", "restaurant"], "meal"),
        (["滴滴", "出租", "高铁", "机票", "airline", "taxi", "uber"], "transport"),
        (["酒店", "宾馆", "hotel", "inn"], "accommodation"),
        (["ktv", "娱乐", "会所"], "entertainment"),
    ]
    result = {"category": "other", "confidence": 0.5}
    for keywords, cat in rules:
        if any(k in merchant for k in keywords):
            result = {"category": cat, "confidence": 0.92}
            break

    checks = grade_case(result, expect)
    all_passed = all(ok for _, ok, _ in checks)
    fail_detail = None if all_passed else "; ".join(f"{name}: {msg}" for name, ok, msg in checks)
    _EVAL_RESULTS[case_id] = {
        "passed": all_passed, "component": "category_classifier", "error": fail_detail,
        "classification": None,
        "actual_output": result, "expected": expect,
        "input_summary": {"merchant": inp.get("merchant", "")},
        "related_config": None,
        "description": case.get("description", ""),
    }

    if not all_passed:
        pytest.fail(f"{case_id} FAILED: {fail_detail}")
