"""LLM Trace Recorder — captures full prompt/response context for eval & debug.

Usage:
    from backend.services.trace import record_trace

    trace_id = await record_trace(
        component="fraud_rule_11",
        model="gpt-4o",
        prompt=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
        response="...",
        parsed_output={...},
        latency_ms=1200,
        submission_id="sub-xxx",
    )

Trace recording is fire-and-forget: failures are logged but never break the main flow.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def record_trace(
    *,
    component: str,
    model: str,
    prompt: list[dict] | str,
    response: Optional[str] = None,
    parsed_output: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    token_usage: Optional[dict] = None,
    error: Optional[str] = None,
    submission_id: Optional[str] = None,
) -> str:
    """Record an LLM trace to the database. Returns trace_id.

    This function imports DB dependencies lazily to avoid circular imports
    and to keep the trace module lightweight.
    """
    trace_id = str(uuid.uuid4())

    # Normalize prompt to JSON-serializable list
    if isinstance(prompt, str):
        prompt_data = [{"role": "user", "content": prompt}]
    else:
        prompt_data = prompt

    try:
        from backend.db.store import AsyncSessionLocal, LLMTrace

        async with AsyncSessionLocal() as session:
            trace = LLMTrace(
                id=trace_id,
                component=component,
                submission_id=submission_id,
                model=model,
                prompt=prompt_data,
                response=response,
                parsed_output=parsed_output,
                latency_ms=latency_ms,
                token_usage=token_usage,
                error=error,
                created_at=datetime.now(timezone.utc),
            )
            session.add(trace)
            await session.commit()
            logger.debug("Trace %s recorded for component=%s", trace_id, component)
    except Exception:
        logger.warning("Failed to record trace %s", trace_id, exc_info=True)

    return trace_id


class TraceTimer:
    """Context manager to measure LLM call latency in milliseconds."""

    def __init__(self) -> None:
        self._start: float = 0
        self.elapsed_ms: int = 0

    def __enter__(self) -> "TraceTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = int((time.monotonic() - self._start) * 1000)
