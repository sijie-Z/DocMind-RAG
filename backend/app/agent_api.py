"""DocMind Agent Service API — stateless FastAPI interface for agent execution.

Endpoints:
    POST /chat     — Run agent, return answer + metrics
    POST /trace    — Run agent, return full execution trace
    POST /plan     — Run planner only, return decomposition

Usage:
    cd backend && uvicorn app.agent_api:app --host 0.0.0.0 --port 8010 --reload

    curl -X POST http://localhost:8010/chat \
      -H "Content-Type: application/json" \
      -d '{"query": "对比星辰科技和远方创新的财务表现", "mode": "structured"}'
"""
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ── Bootstrap ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.disable(logging.CRITICAL)

# API keys must be supplied via environment at startup.
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
_missing = []
for _k in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_URL"):
    if not os.environ.get(_k):
        _missing.append(_k)
if _missing:
    print(
        f"agent_api: FATAL — missing env vars: {', '.join(_missing)}\n"
        f"  Set them before starting, e.g.:\n"
        f"  export DEEPSEEK_API_KEY=sk-...\n"
        f"  export DEEPSEEK_API_URL=https://api.openai.com/v1\n"
        f"  (or use a local Ollama: export DEEPSEEK_API_URL=http://localhost:11434/v1)",
        file=sys.stderr,
    )
    raise SystemExit(1)

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

import app.agent.service  # noqa: F401 — register tools
from app.agent.config import AgentConfig
from app.agent.loop import PERAgentLoop
from app.core.step_runner import StepRunner
from app.core.trace_logger import AgentTracer, TraceExporter

LLM_MODEL = os.environ.get("AGENT_API_MODEL", "deepseek-v4-flash")
AGENT_TIMEOUT = 90.0

# Trace persistence
_trace_exporter = TraceExporter(output_dir="traces")

# ── Structured request log ───────────────────────────────────
_request_log_path = Path("logs/agent_requests.jsonl")
_request_log_path.parent.mkdir(parents=True, exist_ok=True)

def _log_request(
    request_id: str,
    endpoint: str,
    query: str,
    latency: float,
    status: str,
    errors: list | None = None,
    coverage: float = 0.0,
    steps: int = 0,
    tool_calls: int = 0,
):
    """Append a structured request record to the JSONL log."""
    record = {
        "request_id": request_id,
        "endpoint": endpoint,
        "query": query[:80],
        "latency_seconds": round(latency, 2),
        "status": status,
        "error_count": len(errors or []),
        "error_types": list({e.get("type", "unknown") for e in (errors or [])}),
        "coverage": round(coverage, 3),
        "steps": steps,
        "tool_calls": tool_calls,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        with open(_request_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ── Pydantic schemas ─────────────────────────────────────────

class AgentRequest(BaseModel):
    query: str = Field(..., description="User query")
    mode: str = Field(default="structured")
    expected_keywords: list[str] = Field(default_factory=list)


class StepInfo(BaseModel):
    id: str
    description: str
    dependencies: list[str] = []
    tool_hint: str = ""
    status: str = ""


class ToolCallInfo(BaseModel):
    step_id: str
    tool_name: str
    args: dict = {}
    result: str = ""
    duration_ms: float = 0.0
    status: str = ""


class ErrorInfo(BaseModel):
    step_id: str = ""
    type: str = "unknown"
    message: str = ""
    recoverable: bool = False


class TraceResponse(BaseModel):
    request_id: str
    query: str
    mode: str
    plan: list[StepInfo] = []
    execution: list[ToolCallInfo] = []
    answer: str = ""
    coverage: float = 0.0
    latency_seconds: float = 0.0
    timing: dict = {}
    dag_edges: list[list[str]] = []
    steps: int = 0
    tool_calls: int = 0
    errors: list[ErrorInfo] = []


# ── App ──────────────────────────────────────────────────────

app = FastAPI(
    title="DocMind Agent Service",
    description="Agent execution service with trace-level observability",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


# ── API Key auth for port 8010 ─────────────────────────────────
_AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
if _AGENT_API_KEY:
    from fastapi import Header

    async def verify_agent_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
        import secrets

        if not secrets.compare_digest(x_api_key, _AGENT_API_KEY):
            raise HTTPException(status_code=403, detail="Invalid or missing X-API-Key header")
else:
    # Public deployments must configure an API key before exposing port 8010.
    async def verify_agent_api_key() -> None:
        raise HTTPException(status_code=503, detail="AGENT_API_KEY 未配置，拒绝访问")


# ── Orchestrator ─────────────────────────────────────────────

async def run_agent(
    query: str,
    mode: str = "structured",
    expected_keywords: list[str] | None = None,
) -> dict:
    """Execute the agent and return structured result."""
    request_id = uuid.uuid4().hex[:12]
    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_API_URL"),
    )

    config = AgentConfig(
        model=LLM_MODEL,
        enable_planning=(mode == "structured"),
        enable_reflection=False,
        enable_memory=False,
        enable_thinking=False,
        enable_tools=True,
        enable_experience=False,
        max_plan_steps=12,
        max_iterations=10,
        max_retries_per_step=1,
    )

    agent = PERAgentLoop(
        client, config,
        organization_id=int(os.environ.get("AGENT_DEMO_ORG_ID", "0")),
        user_id=int(os.environ.get("AGENT_DEMO_USER_ID", "0")),
        execution_mode="dag",
    )

    plan_steps: list[dict] = []
    tool_calls: list[dict] = []
    chunks: list[str] = []
    errors: list[dict] = []
    step_errors: list[dict] = []
    start = time.perf_counter()

    # Observability: init tracer
    tracer = AgentTracer(request_id=request_id, query=query, mode=mode)
    tracer.start_phase("planner")

    async def run_agent_loop():
        async for event in agent.run(query):
            if event.type == "plan_start":
                pass
            elif event.type == "plan_step":
                plan_steps.append({
                    "id": event.plan_step_id,
                    "description": event.content,
                    "dependencies": event.dependencies or [],
                    "tool_hint": event.tool_hint or "",
                    "status": "ready",
                })
                tracer.log_step(
                    step_id=event.plan_step_id,
                    step_type="planner",
                    description=event.content,
                    status="ready",
                )
            elif event.type == "plan_complete":
                tracer.end_phase("planner")
                tracer.start_phase("execution")
                for step in plan_steps[:-1]:
                    for dep in step.get("dependencies", []):
                        tracer.add_dag_edge(dep, step["id"])
            elif event.type == "tool_call":
                tool_calls.append({
                    "step_id": event.plan_step_id,
                    "tool_name": event.tool_name,
                    "args": event.tool_args,
                    "result": "",
                    "duration_ms": 0.0,
                    "status": "running",
                })
                tracer.log_tool_call(event.plan_step_id, event.tool_name)
                if event.tool_name in ("search_knowledge_base", "vector_search", "web_search"):
                    tracer.start_phase("retrieval")
            elif event.type == "tool_result":
                if tool_calls:
                    tc = tool_calls[-1]
                    tc["result"] = event.content[:300]
                    tc["duration_ms"] = event.tool_duration_ms
                    tc["status"] = "success"
                    tracer.log_step(
                        step_id=tc["step_id"],
                        step_type="retrieval" if tc["tool_name"] in ("search_knowledge_base", "vector_search") else "tool",
                        description=tc["tool_name"],
                        latency=event.tool_duration_ms / 1000,
                        status="success",
                        tool_name=tc["tool_name"],
                    )
                if event.tool_name in ("search_knowledge_base", "vector_search", "web_search"):
                    tracer.end_phase("retrieval")
            elif event.type == "tool_error":
                step_errors.append({
                    "step_id": event.plan_step_id,
                    "type": f"tool_error:{event.tool_name}",
                    "message": event.content[:200],
                    "recoverable": True,
                })
                tracer.log_error(event.plan_step_id, f"tool_error:{event.tool_name}", event.content[:200])
                if tool_calls:
                    tool_calls[-1]["status"] = "error"
                    tool_calls[-1]["result"] = event.content[:200]
            elif event.type == "chunk":
                chunks.append(event.content)
        tracer.end_phase("execution")
        return True

    runner = StepRunner(timeout=AGENT_TIMEOUT, max_retries=0, step_id=request_id)
    result = await runner.run(run_agent_loop)

    duration = time.perf_counter() - start

    if not result.success and not chunks:
        errors.append({
            "step_id": request_id,
            "type": result.error_type or "agent_failure",
            "message": result.error or "Agent execution failed",
            "recoverable": result.recoverable,
        })
        answer = f"[System error: {result.error}]"
    else:
        answer = "".join(chunks) or "(no answer)"
        errors = step_errors

    # Coverage scoring
    coverage = 0.0
    if expected_keywords and answer:
        al = answer.lower()
        found = sum(1 for kw in expected_keywords if kw.lower() in al)
        coverage = found / len(expected_keywords)

    # Finalize and persist trace
    trace = tracer.finalize(answer=answer, coverage=coverage)
    try:
        _trace_exporter.save(trace)
    except Exception:
        pass  # non-critical

    return {
        "request_id": request_id,
        "query": query,
        "mode": mode,
        "plan": plan_steps,
        "execution": tool_calls,
        "answer": answer,
        "coverage": round(coverage, 3),
        "latency_seconds": round(duration, 2),
        "timing": trace.timing,
        "dag_edges": trace.dag_edges,
        "steps": len(plan_steps),
        "tool_calls": len(tool_calls),
        "errors": errors,
    }


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "docmind-agent"}


@app.post("/eval")
async def eval_benchmark(mode: str = "structured", layer: str = "l1", _: None = Depends(verify_agent_api_key)):
    """Run benchmark evaluation, return aggregated metrics.

    Args:
        mode: "structured" or "coarse"
        layer: "l1" (L1 capability, 20 questions) or "all" (30 questions)
    """
    # Load questions
    v3_path = Path(__file__).resolve().parent.parent / "benchmark" / "questions" / "v3.json"
    if not v3_path.exists():
        raise HTTPException(status_code=404, detail="Benchmark questions not found")
    v3 = json.loads(v3_path.read_text(encoding="utf-8"))
    all_questions = v3["questions"]
    if layer == "l1":
        questions = [q for q in all_questions if q["id"].startswith("L1-")]
    else:
        questions = all_questions

    eval_id = uuid.uuid4().hex[:8]
    results: list[dict] = []
    start = time.perf_counter()

    for _i, q in enumerate(questions):
        try:
            r = await run_agent(
                query=q["question"],
                mode=mode,
                expected_keywords=q.get("expected_keywords", []),
            )
            results.append({
                "id": q["id"],
                "coverage": r["coverage"],
                "latency": r["latency_seconds"],
                "steps": r["steps"],
                "errors": len(r["errors"]),
                "error_types": [e.get("type", "") for e in r["errors"]],
            })
        except Exception as e:
            results.append({
                "id": q["id"], "coverage": 0.0,
                "latency": 0, "steps": 0,
                "errors": 1, "error_types": [str(e)[:80]],
            })

    duration = time.perf_counter() - start
    coverages = [r["coverage"] for r in results]
    total_latencies = [r["latency"] for r in results]
    sorted_cov = sorted(coverages)
    sorted_lat = sorted(total_latencies)

    report = {
        "eval_id": eval_id,
        "mode": mode,
        "layer": layer,
        "questions": len(questions),
        "duration_seconds": round(duration, 1),
        "avg_coverage": round(sum(coverages) / len(coverages), 3),
        "coverage_p50": round(sorted_cov[len(sorted_cov) // 2], 3),
        "coverage_p95": round(sorted_cov[int(len(sorted_cov) * 0.95) - 1], 3),
        "avg_latency": round(sum(total_latencies) / len(total_latencies), 1),
        "latency_p50": round(sorted_lat[len(sorted_lat) // 2], 1),
        "latency_p95": round(sorted_lat[int(len(sorted_lat) * 0.95) - 1], 1),
        "pass_count": sum(1 for c in coverages if c >= 0.8),
        "partial_count": sum(1 for c in coverages if 0.4 <= c < 0.8),
        "fail_count": sum(1 for c in coverages if c < 0.4),
        "error_distribution": {},
        "per_question": results,
    }
    # Count error types
    for r in results:
        for et in r["error_types"]:
            report["error_distribution"][et] = report["error_distribution"].get(et, 0) + 1

    _log_request(
        request_id=eval_id, endpoint="/eval",
        query=f"benchmark:{layer}/{mode}", latency=duration,
        status="ok", coverage=report["avg_coverage"],
    )
    return report


@app.post("/chat")
async def chat(req: AgentRequest, _: None = Depends(verify_agent_api_key)):
    """Run agent, return answer + metrics (no full trace)."""
    result = await run_agent(
        query=req.query,
        mode=req.mode,
        expected_keywords=req.expected_keywords,
    )
    _log_request(
        request_id=result["request_id"], endpoint="/chat",
        query=req.query, latency=result["latency_seconds"],
        status="ok" if not result["errors"] else "partial",
        errors=result["errors"], coverage=result.get("coverage", 0),
        steps=result.get("steps", 0), tool_calls=result.get("tool_calls", 0),
    )
    # Strip execution details for /chat (keep summary)
    result.pop("execution", None)
    result["plan"] = [
        {"id": s["id"], "description": s["description"]}
        for s in result["plan"]
    ]
    return result


@app.post("/trace", response_model=TraceResponse)
async def trace(req: AgentRequest, _: None = Depends(verify_agent_api_key)):
    """Run agent, return full execution trace."""
    result = await run_agent(
        query=req.query,
        mode=req.mode,
        expected_keywords=req.expected_keywords,
    )
    _log_request(
        request_id=result["request_id"], endpoint="/trace",
        query=req.query, latency=result["latency_seconds"],
        status="ok" if not result["errors"] else "partial",
        errors=result["errors"], coverage=result.get("coverage", 0),
        steps=result.get("steps", 0), tool_calls=result.get("tool_calls", 0),
    )
    return result


@app.post("/plan")
async def plan(req: AgentRequest, _: None = Depends(verify_agent_api_key)):
    """Run planner only, return decomposition."""
    result = await run_agent(
        query=req.query,
        mode=req.mode,
    )
    return {
        "request_id": result["request_id"],
        "query": result["query"],
        "mode": result["mode"],
        "steps": len(result["plan"]),
        "plan": result["plan"],
        "latency_seconds": result["latency_seconds"],
    }


# ── Run ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
