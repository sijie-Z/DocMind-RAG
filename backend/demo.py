#!/usr/bin/env python3
"""DocMind Agent CLI Demo — trace-level visualization.

Usage:
    python demo.py --query "对比星辰科技和远方创新的财务表现"
    python demo.py --query "对比星辰科技和远方创新的财务表现" --planning-mode coarse

Modes:
    structured  → Structured Planner (multi-step, 004 behavior)
    normal      → LLM Planner (1-2 step, pre-004 behavior, simulated)
    coarse      → Single-step execution (baseline)
"""
import os, sys, json, asyncio, time, argparse, textwrap
from pathlib import Path

# ── Bootstrap ────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("DEEPSEEK_API_KEY", "056beadf58874c58b9b7f121f4f3e7e6.un6ZXWawRWlfH6c3")
os.environ.setdefault("DEEPSEEK_API_URL", "https://open.bigmodel.cn/api/paas/v4/")

import app.agent.service  # noqa: F401 — register tools
import logging
logging.disable(logging.CRITICAL)
from openai import AsyncOpenAI
from app.agent.config import AgentConfig
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.agent.events import AgentEvent

LLM_MODEL = "glm-4-flash"

# ── ANSI helpers ─────────────────────────────────────────────
C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "gray": "\033[90m",
}


def c(name, text):
    return f"{C.get(name, '')}{text}{C['reset']}"


def shorten(text, n=80):
    return text[:n] + "..." if len(text) > n else text


# ── Renderers ────────────────────────────────────────────────

def render_plan_header(plan_type, steps):
    """steps is list of AgentEvent (plan_step events)."""
    label = {"structured": "STRUCTURED", "normal": "LLM PLANNER", "coarse": "SINGLE-STEP"}[plan_type]
    color = {"structured": "green", "normal": "yellow", "coarse": "red"}[plan_type]
    lines = [
        f"  {c('bold', '📋 PLAN')}  [{c(color, label)}]  ({len(steps)} step{'s' if len(steps) > 1 else ''})",
    ]
    for i, ev in enumerate(steps):
        deps = f"  dep=[{','.join(ev.dependencies)}]" if ev.dependencies else "  (parallel OK)"
        lines.append(f"    ├─ {c('cyan', ev.plan_step_id)}: {shorten(ev.content, 70)}")
        lines.append(f"    │  {c('dim', deps)}")
    return "\n".join(lines)


def render_retrieval(step_results):
    lines = [f"  {c('bold', '🔍 RETRIEVAL')}"]
    for sid, info in step_results.items():
        doc_count = len(info.get("docs", []))
        lines.append(f"    ├─ {c('cyan', sid)}: {doc_count} document(s) retrieved")
        for d in info["docs"][:2]:
            lines.append(f"    │  {c('dim', '  └─')} {c('gray', shorten(d.get('filename', '?'), 40))}")
            lines.append(f"    │{c('dim', '      ')} {shorten(d.get('snippet', ''), 80)}")
        if len(info["docs"]) > 2:
            extra = len(info["docs"]) - 2
            lines.append(f"    │  {c('dim', f'  └─ ... and {extra} more')}")
    return "\n".join(lines)


def render_execution(events, step_count, duration):
    lines = [f"  {c('bold', '⚙️  EXECUTION')}  (DAG mode, {step_count} step{'s' if step_count > 1 else ''}, {duration:.1f}s)"]
    for ev in events:
        if ev.type == "plan_step":
            lines.append(f"    ├─ {c('cyan', ev.plan_step_id)}: {shorten(ev.content, 60)}")
        elif ev.type == "tool_call":
            lines.append(f"    │  {c('dim', '  🛠 ')}{c('yellow', ev.tool_name)}({shorten(json.dumps(ev.tool_args), 50)})")
        elif ev.type == "tool_result":
            lines.append(f"    │  {c('dim', '  ✅ result:')} {shorten(ev.content, 80)}")
        elif ev.type == "tool_error":
            lines.append(f"    │  {c('red', '  ❌ error:')} {shorten(ev.content, 80)}")
    return "\n".join(lines)


def render_result(events, coverage=None):
    final = "".join(ev.content for ev in events if ev.type == "chunk")
    cov_str = f" [coverage: {coverage:.0%}]" if coverage is not None else ""
    lines = [
        f"  {c('bold', '✅ RESULT')}{cov_str}",
        f"  {textwrap.shorten(final, width=500, placeholder='...')}" if final else "  (no answer)",
    ]
    return "\n".join(lines)


def score_answer(answer, expected_keywords):
    if not answer or not expected_keywords:
        return 0.0, [], expected_keywords
    al = answer.lower()
    found = [kw for kw in expected_keywords if kw.lower() in al]
    missed = [kw for kw in expected_keywords if kw.lower() not in al]
    return len(found) / len(expected_keywords), found, missed


# ── Agent runners ────────────────────────────────────────────

async def run_structured(query, org_id=3):
    """Full PERAgentLoop → structured planner."""
    client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=os.environ.get("DEEPSEEK_API_URL"))
    config = AgentConfig(model=LLM_MODEL, enable_planning=True, enable_reflection=False,
        enable_memory=False, enable_thinking=False, enable_tools=True, enable_experience=False,
        max_plan_steps=12, max_iterations=10, max_retries_per_step=1)
    agent = PERAgentLoop(client, config, organization_id=org_id, user_id=0, execution_mode="dag")
    collected = []
    start = time.perf_counter()
    async for ev in agent.run(query):
        collected.append(ev)
    dur = time.perf_counter() - start
    return collected, dur


async def run_coarse(query, org_id=3):
    """Single-step (no planner, direct LLM)."""
    client = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=os.environ.get("DEEPSEEK_API_URL"))
    config = AgentConfig(model=LLM_MODEL, enable_planning=False, enable_reflection=False,
        enable_memory=False, enable_thinking=False, enable_tools=True, enable_experience=False,
        max_iterations=5, max_retries_per_step=1)
    agent = PERAgentLoop(client, config, organization_id=org_id, user_id=0, execution_mode="dag")
    collected = []
    start = time.perf_counter()
    async for ev in agent.run(query):
        collected.append(ev)
    dur = time.perf_counter() - start
    return collected, dur


# ── Main ─────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="DocMind Agent CLI Demo")
    parser.add_argument("--query", default="对比星辰科技和远方创新的财务表现",
                        help="Query to run")
    parser.add_argument("--planning-mode", choices=["structured", "coarse"], default="structured",
                        help="structured=multi-step planner (004), coarse=single-step LLM (baseline)")
    parser.add_argument("--expected", nargs="*", default=["毛利率", "净利润率", "资产负债率", "对比"],
                        help="Expected keywords for coverage scoring")
    args = parser.parse_args()

    query = args.query
    mode = args.planning_mode

    print()
    print(f"  {c('bold', '╔══════════════════════════════════════════════════╗')}")
    print(f"  {c('bold', '║')}          {c('cyan', 'DocMind Agent Trace')}          {c('bold', '║')}")
    print(f"  {c('bold', '╚══════════════════════════════════════════════════╝')}")
    print(f"  Query:     {c('yellow', query)}")
    print(f"  Mode:      {c('green' if mode == 'structured' else 'red', mode.upper())}")
    print(f"  Model:     {LLM_MODEL}")
    print()

    # ── Run ───────────────────────────────────────────────────
    if mode == "coarse":
        collected, duration = await run_coarse(query)
        mode_name = "coarse"
    else:
        collected, duration = await run_structured(query)
        mode_name = "structured"

    # ── Extract sections ──────────────────────────────────────
    plan_steps = [ev for ev in collected if ev.type == "plan_step"]
    tool_events = [ev for ev in collected if ev.type in ("tool_call", "tool_result", "tool_error")]
    chunk_events = [ev for ev in collected if ev.type == "chunk"]

    # ── Render ────────────────────────────────────────────────
    print(f"  {c('bold', '─' * 55)}")

    # Plan
    print()
    print(render_plan_header(mode_name, plan_steps))
    print()

    # Execution
    print(f"  {c('bold', '─' * 55)}")
    print()
    print(render_execution(collected, len(plan_steps), duration))
    print()

    # Result
    answer = "".join(ev.content for ev in collected if ev.type == "chunk")
    coverage, found, missed = score_answer(answer, args.expected)
    print(f"  {c('bold', '─' * 55)}")
    print()
    print(render_result(collected, coverage))
    if missed:
        print(f"  {c('dim', f'  Missed keywords: {missed}')}")
    print()

    # ── Summary ───────────────────────────────────────────────
    display_steps = len(plan_steps) if len(plan_steps) > 0 else 1  # coarse = single-step
    print(f"  {c('bold', '─' * 55)}")
    print(f"  {c('bold', '📊 SUMMARY')}")
    print(f"    Steps:     {display_steps}")
    print(f"    Duration:  {duration:.1f}s")
    print(f"    Coverage:  {coverage:.0%}")
    print(f"    Found:     {found}")
    print(f"    Missed:    {missed}")
    print()
    print(f"  {c('bold', '─' * 55)}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
