"""Groundwork Opik-Compatible Local Evaluation & Telemetry Tracer (Telemetry Layer).

Inspired by comet-ml/opik:
Provides zero-cost in-process LLM tracing, latency tracking, token usage budgeting,
rubric scoring, and dual persistence (.cache/opik_traces.jsonl + Supabase pipeline_runs).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
TRACE_FILE = CACHE_DIR / "opik_traces.jsonl"


@dataclass
class OpikSpan:
    trace_id: str
    span_id: str
    name: str
    agent_name: str
    model_name: str
    provider: str
    start_time: float
    end_time: float = 0.0
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    rubric_score: int = 0
    passed_evaluation: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def finish(self, rubric_score: int = 0, error: str | None = None) -> None:
        self.end_time = time.monotonic()
        self.latency_ms = round((self.end_time - self.start_time) * 1000, 1)
        self.rubric_score = rubric_score
        self.passed_evaluation = rubric_score >= 85 if rubric_score > 0 else (error is None)
        self.error = error


class OpikTracer:
    """Manages recording and querying LLM traces and evaluation telemetry."""

    def __init__(self, trace_file: Path = TRACE_FILE) -> None:
        self.trace_file = trace_file
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    def start_span(
        self,
        name: str,
        agent_name: str = "scribe",
        model_name: str = "unknown",
        provider: str = "openrouter",
        metadata: dict[str, Any] | None = None,
    ) -> OpikSpan:
        """Start a new telemetry span."""
        return OpikSpan(
            trace_id=f"trc_{uuid.uuid4().hex[:12]}",
            span_id=f"spn_{uuid.uuid4().hex[:8]}",
            name=name,
            agent_name=agent_name,
            model_name=model_name,
            provider=provider,
            start_time=time.monotonic(),
            metadata=metadata or {},
        )

    def log_span(self, span: OpikSpan, supabase: Any = None) -> None:
        """Persist span to local JSONL and optionally sync aggregate to Supabase pipeline_runs."""
        data = asdict(span)
        # 1. Local JSONL write
        try:
            with open(self.trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append local Opik trace: {e}")

        # 2. Sync to Supabase pipeline_runs if available
        if supabase:
            try:
                supabase.table("pipeline_runs").insert({
                    "agent": span.agent_name,
                    "status": "success" if span.passed_evaluation and not span.error else "error",
                    "items_processed": 1,
                    "items_published": 1 if span.passed_evaluation and not span.error else 0,
                    "error_log": span.error or f"Score: {span.rubric_score}, Latency: {span.latency_ms}ms, Model: {span.model_name}",
                }).execute()
            except Exception as e:
                logger.debug(f"Non-fatal Supabase pipeline_runs log notice: {e}")

    def get_recent_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read recent traces from JSONL file."""
        if not self.trace_file.exists():
            return []

        traces: list[dict[str, Any]] = []
        try:
            with open(self.trace_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        traces.append(json.loads(line))
        except Exception as e:
            logger.warning(f"Error reading trace file: {e}")

        return traces[-limit:]

    def get_summary_stats(self) -> dict[str, Any]:
        """Aggregate performance and rubric score analytics."""
        traces = self.get_recent_traces(limit=200)
        if not traces:
            return {
                "total_spans": 0,
                "avg_latency_ms": 0.0,
                "avg_rubric_score": 0.0,
                "success_rate_pct": 0.0,
                "models_used": {},
            }

        total = len(traces)
        latencies = [t.get("latency_ms", 0.0) for t in traces]
        scores = [t.get("rubric_score", 0) for t in traces if t.get("rubric_score", 0) > 0]
        passed = sum(1 for t in traces if t.get("passed_evaluation"))

        models_count: dict[str, int] = {}
        for t in traces:
            m = t.get("model_name", "unknown")
            models_count[m] = models_count.get(m, 0) + 1

        return {
            "total_spans": total,
            "avg_latency_ms": round(sum(latencies) / max(1, total), 1),
            "avg_rubric_score": round(sum(scores) / max(1, len(scores)), 1) if scores else 0.0,
            "success_rate_pct": round((passed / max(1, total)) * 100.0, 1),
            "models_used": models_count,
        }
