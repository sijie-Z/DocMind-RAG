#!/usr/bin/env python3
"""Benchmark runner — evaluate the PER agent against a question set.

Usage:
    # Baseline (RAG only)
    python -m benchmark.run --questions benchmark/questions/v1.json --mode baseline

    # PER Agent
    python -m benchmark.run --questions benchmark/questions/v1.json --mode agent

    # Compare two reports
    python -m benchmark.run --compare benchmark/results/baseline.json benchmark/results/agent.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.scorer import (
    BenchmarkReport,
    QuestionResult,
    cases_summary,
    save_case,
)

logger = logging.getLogger(__name__)


# ── Langfuse trace helper ────────────────────────────────────────────

def _get_langfuse():
    """Lazy Langfuse init for trace tracking."""
    try:
        from app.agent.observability import get_langfuse
        return get_langfuse()
    except Exception:
        return None


def _get_org_id() -> int:
    """Get the actual default organization ID from the database."""
    try:
        import asyncio

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.organization import Organization
        async def _fetch():
            async with AsyncSessionLocal() as db:
                org = (await db.execute(select(Organization).where(Organization.name == "Default"))).scalar_one_or_none()
                return org.id if org else 1
        return asyncio.run(_fetch())
    except Exception:
        return 1


def _create_question_trace(lf, question_id: str, mode: str):
    """Create a root trace for a single question, return trace_id + trace_url."""
    if not lf:
        return "", ""

    trace = lf.trace(
        name=f"benchmark_{question_id}",
        input={"question_id": question_id, "mode": mode},
        metadata={"benchmark_type": mode, "question_id": question_id},
    )
    trace_id = getattr(trace, "id", "") or getattr(trace, "trace_id", "")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    trace_url = f"{host}/trace/{trace_id}" if trace_id else ""
    return trace_id, trace_url


# ── Baseline (RAG only) ─────────────────────────────────────────────

async def _run_baseline(question: dict, org_id: int = 3, lf=None) -> QuestionResult:
    """Run a single question in RAG-only mode."""
    from app.core.config import settings
    from app.dependencies import get_rag_pipeline

    qid = question["id"]
    query = question["question"]

    trace_id, trace_url = _create_question_trace(lf, qid, "baseline")

    pipeline = get_rag_pipeline()
    if not pipeline or not pipeline.openai_client:
        return QuestionResult(
            id=qid, category=question.get("subcategory", question.get("category", "unknown")),
            question=query, difficulty=question["difficulty"],
            answer="Error: LLM not configured", duration=0,
            trace_id=trace_id, trace_url=trace_url,
        )

    start = time.perf_counter()

    try:
        # 1. Search knowledge base
        docs = await pipeline.search_knowledge_base(
            query=query, organization_id=org_id, top_k=5,
        )

        # 2. Build context
        context = ""
        if docs:
            for i, d in enumerate(docs[:5], 1):
                snippet = d.get("snippet", d.get("text", ""))[:500]
                filename = d.get("filename", "Unknown")
                context += f"\n[{i}] {filename}: {snippet}"

        # 3. Ask LLM
        if context:
            prompt = f"请根据以下文档内容回答问题。\n\n文档：{context}\n\n问题：{query}"
        else:
            prompt = f"回答以下问题：{query}"

        messages = [
            {"role": "system", "content": "你是一个知识库问答助手。请基于提供的文档内容回答，如果文档中没有相关信息，请如实说明。"},
            {"role": "user", "content": prompt},
        ]
        response = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=1000,
            timeout=30,
        )
        answer = response.choices[0].message.content or ""
        duration = time.perf_counter() - start

    except Exception as e:
        answer = f"Error: {e}"
        duration = time.perf_counter() - start

    result = QuestionResult(
        id=qid, category=question.get("subcategory", question.get("category", "unknown")),
        question=query, difficulty=question["difficulty"],
        answer=answer, duration=duration,
        trace_id=trace_id, trace_url=trace_url,
    )
    result.score(question.get("expected_keywords", []))
    return result


# ── Agent mode (PER) ────────────────────────────────────────────────

async def _run_agent(question: dict, org_id: int = 3, lf=None, enable_experience: bool = True,
                      execution_mode: str = "dag", planning_mode: str = "normal") -> QuestionResult:
    """Run a single question with the full PER agent."""
    from app.agent.config import AgentConfig
    from app.agent.loop import PERAgentLoop
    from app.agent.service import agent_service

    qid = question["id"]
    query = question["question"]

    trace_id, trace_url = _create_question_trace(lf, qid, f"agent_{execution_mode}")

    client = agent_service.client
    if not client:
        return QuestionResult(
            id=qid, category=question.get("subcategory", question.get("category", "unknown")),
            question=query, difficulty=question["difficulty"],
            answer="Error: LLM not configured", duration=0,
            trace_id=trace_id, trace_url=trace_url,
        )

    config = AgentConfig(
        enable_planning=True,
        enable_reflection=True,
        enable_memory=False,
        enable_thinking=False,
        enable_experience=enable_experience,
        planning_mode=planning_mode,
        max_plan_steps=5,
        max_iterations=8,
        max_retries_per_step=3,
        timeout=60.0,
    )

    agent = PERAgentLoop(
        openai_client=client,
        config=config,
        organization_id=org_id,
        user_id=0,
        execution_mode=execution_mode,
    )

    start = time.perf_counter()
    answer_parts: list[str] = []
    step_count = 0
    tool_failures = 0
    replay_task_id = ""

    try:
        async for event in agent.run(query):
            if event.type == "chunk":
                answer_parts.append(event.content)
            elif event.type == "tool_error":
                tool_failures += 1
            elif event.type == "plan_step":
                step_count += 1
            elif event.type == "execution_context":
                # Capture task_id for replay
                replay_task_id = event.content.get("task_id", "") if isinstance(event.content, dict) else ""
    except Exception as e:
        answer_parts.append(f"\n[Error: {e}]")

    duration = time.perf_counter() - start
    answer = "".join(answer_parts) or "(no answer)"

    result = QuestionResult(
        id=qid, category=question.get("subcategory", question.get("category", "unknown")),
        question=query, difficulty=question["difficulty"],
        answer=answer, duration=duration,
        step_count=step_count,
        tool_failures=tool_failures,
        trace_id=trace_id, trace_url=trace_url,
    )
    result.score(question.get("expected_keywords", []))
    # ── Save replay data locally (benchmark persistence) ──
    if replay_task_id:
        try:
            from app.agent.replay.engine import REPLAY_DIR, ReplayEngine
            ctx = await ReplayEngine().load(replay_task_id)
            if ctx:
                path = REPLAY_DIR / f"{replay_task_id}.json"
                path.write_text(
                    json.dumps(ctx, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass
    return result


# ── Runner ──────────────────────────────────────────────────────────

async def _get_org_id() -> int:
    """Get the actual default organization ID from the database."""
    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.organization import Organization
        async with AsyncSessionLocal() as db:
            org = (await db.execute(select(Organization).where(Organization.name == "Default"))).scalar_one_or_none()
            return org.id if org else 1
    except Exception:
        return 1


async def run_benchmark(questions_path: str, mode: str, output: str | None = None,
                        enable_experience: bool = True,
                        execution_mode: str = "dag",
                        planning_mode: str = "normal") -> BenchmarkReport:
    """Load questions, run all, produce report, save cases.

    Args:
        questions_path: Path to questions JSON file.
        mode: 'baseline' or 'agent'.
        output: Path to save result JSON.
        enable_experience: Enable Experience Memory (A/B toggle).
        execution_mode: 'dag' (parallel groups) or 'serial' (step-by-step).
        planning_mode: 'coarse', 'normal', or 'fine' (planner granularity)."""
    org_id = await _get_org_id()
    logger.info(f"Using organization_id={org_id}")
    with open(questions_path, encoding="utf-8") as f:
        raw = json.load(f)

    # Support v1 (list) and v2 ({meta, questions}) formats
    if isinstance(raw, dict) and "questions" in raw:
        questions = raw["questions"]
    elif isinstance(raw, list):
        questions = raw
    else:
        questions = raw

    lf = _get_langfuse()

    print(f"\n{'=' * 50}")
    print(f"  评测开始 | 模式: {mode} | 题目数: {len(questions)}")
    if lf:
        print("  Langfuse trace: 已启用")
    print(f"{'=' * 50}\n")

    results: list[QuestionResult] = []
    for i, q in enumerate(questions, 1):
        qid = q["id"]
        cat = q.get("subcategory", q.get("category", "unknown"))
        print(f"  [{i}/{len(questions)}] {qid} ({cat})", end=" ", flush=True)

        t0 = time.perf_counter()
        if mode == "agent":
            result = await _run_agent(q, org_id=org_id, lf=lf, enable_experience=enable_experience, execution_mode=execution_mode, planning_mode=planning_mode)
        else:
            result = await _run_baseline(q, org_id=org_id, lf=lf)
        elapsed = time.perf_counter() - t0

        # Save individual case file
        save_case(result, q.get("expected_keywords", []))
        results.append(result)

        # Per-question summary
        grade_icon = {"success": "✅", "partial": "🟡", "failure": "❌"}
        icon = grade_icon.get(result.grade, "❓")
        cov = f"{result.keyword_coverage:.0%}"
        missing = f" 漏={result.keywords_missed}" if result.keywords_missed else ""
        print(f"{icon} 覆盖={cov}{missing} 耗时={elapsed:.1f}s 步骤={result.step_count}")

    report = BenchmarkReport.from_results(mode, results)

    print(f"\n{'─' * 50}")
    print(report.summary_text())
    print()
    print(cases_summary())
    print(f"{'─' * 50}\n")

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  报告已保存: {out_path}")

    return report


def compare_reports(baseline_path: str, agent_path: str) -> None:
    """Compare two saved reports."""
    with open(baseline_path) as f:
        baseline = json.load(f)
    with open(agent_path) as f:
        agent_data = json.load(f)

    b = BenchmarkReport(**{k: v for k, v in baseline.items() if k != "results"})
    a = BenchmarkReport(**{k: v for k, v in agent_data.items() if k != "results"})

    print(f"\n{'=' * 50}")
    print("  Baseline vs Agent 对比")
    print(f"{'=' * 50}\n")
    print(b.comparison_text(a))
    print()


def main():
    parser = argparse.ArgumentParser(description="DocMind Benchmark Runner")
    parser.add_argument("--questions", default="benchmark/questions/v2.json",
                        help="题目 JSON 文件路径")
    parser.add_argument("--mode", choices=["baseline", "agent"], default="agent",
                        help="运行模式")
    parser.add_argument("--execution-mode", choices=["dag", "serial"], default="dag",
                        help="执行器模式: dag (依赖感知+并行) / serial (顺序执行)")
    parser.add_argument("--planning-mode", choices=["coarse", "normal", "fine"], default="normal",
                        help="规划粒度: coarse (一步) / normal (适中) / fine (细粒度)")
    parser.add_argument("--output", default=None,
                        help="输出 JSON 路径 (默认: benchmark/results/{mode}_v1.json)")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "AGENT"),
                        help="对比两份报告")
    parser.add_argument("--experience", action="store_true", default=True,
                        help="启用 Experience Memory (默认: true)")
    parser.add_argument("--no-experience", action="store_false", dest="experience",
                        help="禁用 Experience Memory (A/B baseline)")
    args = parser.parse_args()

    if args.compare:
        compare_reports(args.compare[0], args.compare[1])
        return

    exp_label = "with_experience" if args.experience else "no_experience"
    mode_label = f"{args.mode}_{args.execution_mode}_{args.planning_mode}" if args.mode == "agent" else args.mode
    output = args.output or f"benchmark/results/{mode_label}_{exp_label}.json"
    print(f"\n  Execution Mode: {args.execution_mode}")
    print(f"  Planning Mode: {args.planning_mode}")
    print(f"  Experience Memory: {'启用' if args.experience else '禁用'}")
    asyncio.run(run_benchmark(args.questions, args.mode, output,
                              enable_experience=args.experience,
                              execution_mode=args.execution_mode,
                              planning_mode=args.planning_mode))


if __name__ == "__main__":
    main()
