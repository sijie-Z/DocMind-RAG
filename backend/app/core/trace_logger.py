"""Observability Layer — structured trace collection and export.

Usage:
    from app.core.trace_logger import AgentTracer, TraceExporter

    tracer = AgentTracer(request_id="abc", query="...", mode="structured")
    tracer.log_step("s1", "retrieval", latency=2.3, status="success")
    tracer.set_timing(planner=1.2, retrieval=8.1, execution=13.2)

    # Export to JSONL
    exporter = TraceExporter("traces/")
    exporter.save(tracer.to_dict())

Schema:
    {
        "request_id": str,
        "query": str,
        "mode": str,
        "timing": {"total": float, "planner": float, "retrieval": float, "execution": float},
        "steps": [{"step_id": str, "type": str, "description": str,
                   "latency": float, "retry_count": int, "status": str}],
        "dag_edges": [[str, str], ...],
        "errors": [{"step_id": str, "type": str, "message": str, "recoverable": bool}],
        "answer_preview": str,
        "coverage": float,
        "timestamp": str,
    }
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Step-level trace ─────────────────────────────────────────

@dataclass
class StepTrace:
    """Single step execution record."""
    step_id: str
    type: str = "unknown"             # planner, retrieval, execution, tool
    description: str = ""
    latency: float = 0.0
    retry_count: int = 0
    status: str = "pending"            # pending, running, success, error, skipped
    tool_name: str = ""
    input_size: int = 0
    output_size: int = 0
    error: str | None = None
    error_type: str = ""


# ── Full agent trace ─────────────────────────────────────────

@dataclass
class AgentTrace:
    """Complete execution trace for one agent run."""
    request_id: str
    query: str
    mode: str
    timing: dict[str, float] = field(default_factory=lambda: {
        "total": 0.0, "planner": 0.0, "retrieval": 0.0, "execution": 0.0,
    })
    steps: list[dict] = field(default_factory=list)
    dag_edges: list[list[str]] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    answer_preview: str = ""
    coverage: float = 0.0
    tool_call_count: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["timestamp"]:
            d["timestamp"] = datetime.now(UTC).isoformat()
        return d


# ── Tracer ───────────────────────────────────────────────────

class AgentTracer:
    """Collects execution trace data for one agent run."""

    def __init__(self, request_id: str, query: str, mode: str = "structured"):
        self.request_id = request_id
        self.query = query
        self.mode = mode
        self._steps: list[StepTrace] = []
        self._errors: list[dict] = []
        self._dag_edges: list[list[str]] = []
        self._timing: dict[str, float] = {"total": 0.0, "planner": 0.0, "retrieval": 0.0, "execution": 0.0}
        self._tool_call_count: int = 0
        self._start_time: float = time.perf_counter()
        self._phase_starts: dict[str, float] = {}
        self._current_step: str = ""

    # ── Phase timing ──────────────────────────────────────

    def start_phase(self, phase: str):
        self._phase_starts[phase] = time.perf_counter()

    def end_phase(self, phase: str):
        if phase in self._phase_starts:
            self._timing[phase] = time.perf_counter() - self._phase_starts[phase]

    # ── Step recording ────────────────────────────────────

    def log_step(
        self,
        step_id: str,
        step_type: str = "unknown",
        description: str = "",
        latency: float = 0.0,
        retry_count: int = 0,
        status: str = "success",
        tool_name: str = "",
        error: str | None = None,
        error_type: str = "",
    ):
        self._steps.append(StepTrace(
            step_id=step_id,
            type=step_type,
            description=description,
            latency=latency,
            retry_count=retry_count,
            status=status,
            tool_name=tool_name,
            error=error,
            error_type=error_type,
        ))

    def log_tool_call(self, step_id: str, tool_name: str):
        self._tool_call_count += 1

    def log_error(self, step_id: str, error_type: str, message: str, recoverable: bool = True):
        self._errors.append({
            "step_id": step_id,
            "type": error_type,
            "message": message[:300],
            "recoverable": recoverable,
        })

    def add_dag_edge(self, source: str, target: str):
        self._dag_edges.append([source, target])

    # ── Finalize ──────────────────────────────────────────

    def finalize(self, answer: str = "", coverage: float = 0.0):
        self._timing["total"] = time.perf_counter() - self._start_time
        return AgentTrace(
            request_id=self.request_id,
            query=self.query,
            mode=self.mode,
            timing=self._timing,
            steps=[asdict(s) for s in self._steps],
            dag_edges=self._dag_edges,
            errors=self._errors,
            answer_preview=answer[:300],
            coverage=coverage,
            tool_call_count=self._tool_call_count,
            timestamp=datetime.now(UTC).isoformat(),
        )


# ── Exporter ────────────────────────────────────────────────

class TraceExporter:
    """Persists traces to disk as JSONL."""

    def __init__(self, output_dir: str = "traces"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, trace: AgentTrace | dict) -> str:
        """Save a single trace as JSONL. Returns file path."""
        data = trace.to_dict() if isinstance(trace, AgentTrace) else trace
        date_str = datetime.now().strftime("%Y%m%d")
        path = self.output_dir / f"traces_{date_str}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        return str(path)

    def load(self, date_str: str | None = None) -> list[dict]:
        """Load traces from a date file."""
        if date_str:
            path = self.output_dir / f"traces_{date_str}.jsonl"
        else:
            files = sorted(self.output_dir.glob("traces_*.jsonl"))
            path = files[-1] if files else None

        if not path or not path.exists():
            return []

        traces = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    traces.append(json.loads(line))
        return traces
