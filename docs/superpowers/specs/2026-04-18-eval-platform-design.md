# ExpenseFlow Eval Platform — Design Spec

**Date:** 2026-04-18
**Status:** Approved

## Problem

ExpenseFlow has 6 AI/LLM components (OCR, fraud rules 11-14, fraud rules 15-20, ambiguity detector, chat agents, category classifier) but:

1. **No trace capture**: LLM calls in `llm_fraud_analyzer.py` and `ambiguity_detector.py` only store results, not prompts/responses. Debugging is impossible.
2. **Limited eval coverage**: Only 12 agent-level test cases exist (`eval_cases.yaml`). No eval for OCR, fraud rules, or ambiguity detector.
3. **No failure discovery**: No way to sort by accuracy, filter by component, or identify worst-performing cases.

## Design Principles

| Source | Principle | Application |
|--------|-----------|-------------|
| Hamel Husain | Error analysis before infrastructure | Phase 0 = trace capture, Phase 1 = manual review |
| Anthropic | Code grader > model grader; pass^k for reliability | Code graders for score ranges; 3-run trials for LLM rules |
| Langfuse | Trace-level observability | `llm_traces` table with full prompt/response |
| Scale Nucleus | Sort by confidence, filter failures | Observatory UI sorts cases low→high |

## Architecture

```
Layer 1: Trace Capture (llm_traces table)
  ↓
Layer 2: Eval Dataset + Graders (YAML cases + pytest harness)
  ↓
Layer 3: Observatory UI (React /eval page + FastAPI endpoints)
```

## Layer 1: Trace Capture

### LLMTrace Model

New SQLAlchemy model in `backend/db/store.py`:

```python
class LLMTrace(Base):
    __tablename__ = "llm_traces"

    id              = Column(String(36), primary_key=True)
    component       = Column(String(50), nullable=False)    # fraud_rule_11, ambiguity_detector, ocr, etc.
    submission_id   = Column(String(36), nullable=True)      # FK to submissions
    model           = Column(String(50), nullable=False)     # gpt-4o, claude-sonnet-4-20250514, MiniMax-M2
    prompt          = Column(JSON, nullable=False)            # full messages array
    response        = Column(Text, nullable=True)             # raw LLM response
    parsed_output   = Column(JSON, nullable=True)             # structured parsed result
    latency_ms      = Column(Integer, nullable=True)
    token_usage     = Column(JSON, nullable=True)             # {input: N, output: N}
    error           = Column(Text, nullable=True)
    created_at      = Column(DateTime(timezone=True))
```

### Integration Points

| File | Change | What gets traced |
|------|--------|-----------------|
| `backend/services/llm_fraud_analyzer.py` | Wrap `_call_llm()` | Rules 11-14 GPT-4o calls |
| `agent/ambiguity_detector.py` | Wrap `_call_minimax()`, `_call_claude()` | Ambiguity deep review calls |

### Trace Helper

New module `backend/services/trace.py`:

```python
async def record_trace(
    component: str,
    model: str,
    prompt: list[dict],
    response: str | None,
    parsed_output: dict | None,
    latency_ms: int | None,
    token_usage: dict | None = None,
    error: str | None = None,
    submission_id: str | None = None,
) -> str:
    """Record an LLM trace. Returns trace_id."""
```

Non-blocking: trace failures are logged but never break the main flow.

## Layer 2: Eval Dataset + Graders

### Directory Structure

```
backend/tests/
  eval_datasets/
    fraud_llm_rules.yaml      # Rules 11-14 cases
    ambiguity_detector.yaml    # 6-factor scoring cases
    ocr_extraction.yaml        # OCR field accuracy cases
    agent_chat.yaml            # Existing 12 cases (migrated from eval_cases.yaml)
  eval_fixtures/
    receipts/                  # Test receipt images for OCR eval
  test_eval_harness.py         # Unified eval runner
  graders/
    __init__.py
    code_graders.py            # Score range, field match, enum match
    model_graders.py           # LLM judge for response quality (Phase 3)
```

### Case Format

```yaml
- id: fraud_11_template_positive
  component: fraud_rule_11
  description: "模板化描述应被检测到"
  input:
    description: "团队午餐 海底捞 5人"
    recent_descriptions: ["团队午餐 海底捞 5人", "团队午餐 海底捞 5人"]
  expected:
    template_score_range: [70, 100]
    verdict: triggered
  grader: code
  trials: 3          # pass^3: all 3 must pass
```

### Grader Types

1. **score_range**: `expected.score_range = [lo, hi]` → assert lo <= actual <= hi
2. **field_match**: Exact match on specific fields
3. **enum_in**: Value must be in allowed set
4. **substring**: Response text contains expected phrase
5. **pass^k**: Run `trials` times, all must pass (for LLM non-determinism)

### EvalRun Model

```python
class EvalRun(Base):
    __tablename__ = "eval_runs"

    id            = Column(String(36), primary_key=True)
    started_at    = Column(DateTime(timezone=True))
    finished_at   = Column(DateTime(timezone=True))
    total_cases   = Column(Integer)
    passed_cases  = Column(Integer)
    pass_rate     = Column(Float)
    results       = Column(JSON)   # [{case_id, component, passed, score, latency_ms, error}]
    trigger       = Column(String(50))  # "manual" | "ci" | "pytest"
```

## Layer 3: Observatory UI

### API Endpoints (`backend/api/routes/eval.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/eval/runs` | List eval runs (paginated) |
| GET | `/api/eval/runs/{id}` | Single run detail + all case results |
| GET | `/api/eval/traces` | List traces with filters (component, submission_id, date range) |
| GET | `/api/eval/traces/{id}` | Single trace detail (full prompt/response) |
| POST | `/api/eval/runs` | Trigger a new eval run |

### Query Parameters

- `?component=fraud_rule_11` — filter by component
- `?passed=false` — show only failures
- `?sort=score&order=asc` — sort by score ascending (worst first)
- `?page=1&page_size=20` — pagination

### Frontend (`frontend/src/pages/EvalDashboard.tsx`)

Three views:

1. **Run List**: Table of eval runs with pass rate, timestamp, sparkline trend
2. **Case Detail**: Sortable table (case_id, component, pass/fail, score, latency). Default sort: score ascending. Filters: component dropdown, pass/fail toggle
3. **Trace Viewer**: Click a case → expand full LLM trace (prompt, response, parsed output, grader verdict)

## Implementation Phases

| Phase | Scope | Files |
|-------|-------|-------|
| 0 | LLMTrace model + trace helper + instrument 2 files | `store.py`, `trace.py`, `llm_fraud_analyzer.py`, `ambiguity_detector.py` |
| 2 | Eval datasets YAML + code graders + harness | `eval_datasets/*.yaml`, `graders/`, `test_eval_harness.py` |
| 4 | EvalRun model + API + React UI | `eval.py` (routes), `EvalDashboard.tsx` |

Phase 1 (manual error analysis) and Phase 3 (model graders) are manual/future work.

## Key Decisions

1. **Binary pass/fail** — No 1-5 scales. Tolerance expressed via score ranges.
2. **pass^3 for LLM rules** — 3 trials, all must pass. Tests reliability, not just capability.
3. **SQLite, no external deps** — Traces stored in same DB. No Langfuse SaaS.
4. **Eval data in YAML** — Version-controlled with code, not in database.
5. **Code grader preferred** — Only use LLM judge where exact matching is impossible.
