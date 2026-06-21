# I Thought DAG Execution Would Win. Five Experiments Later, I Learned Planning Matters More.

> *How a benchmarking rabbit hole revealed that the bottleneck in Agent systems isn't where you think it is.*

---

## The Setup

I built an Agent system. Like many others, it followed the "Plan → Execute → Reflect" pattern. The planner decomposes a user request into steps, the executor runs them (with DAG-based parallel scheduling when steps are independent), and the reflector checks the result.

The architecture looked reasonable on paper. DAG execution should outperform serial execution on multi-branch tasks — parallel steps run concurrently, reducing wall-clock time. It's basic computer science.

So I set up a benchmark — 30 enterprise knowledge tasks spanning document analysis, cross-document comparison, framework analysis (SWOT/PEST), and multi-step reasoning. Then I ran the comparison.

The result was... underwhelming.

| Metric | DAG | Serial |
|--------|:---:|:------:|
| Coverage | 65.8% | 69.4% |
| Success Rate | 50% | 63% |
| Average Steps | **1.2** | 1.0 |
| Avg Duration | 32.2s | 27.7s |

DAG was *worse* than serial in both coverage and speed. A textbook case of an elegant architectural idea failing in practice.

But why?

---

## 001: The Non-Result That Told Me More Than A Result Would Have

The first clue was in the step count: **83% of tasks were executed in a single step.** When every task is a single step, DAG and serial are the same thing. The parallel scheduling engine was never activated.

| Steps | Frequency |
|:-----:|:---------:|
| 1 | 25/30 (83%) |
| 2 | 4/30 (13%) |
| 3 | 1/30 (3%) |

The Planner — an LLM tasked with breaking down complex requests — was collapsing everything into one step. "Analyze these three companies' strategies" became a single step, not "Research A → Research B → Research C → Compare."

The bottleneck wasn't the executor. It was the planner.

I had a hypothesis: maybe the task decomposition was already optimal for the benchmark's difficulty. Or maybe the planner just needed better prompting. Two more experiments would answer that.

---

## 002 → 003: You Can't Prompt Your Way Out Of This

I designed a granularity experiment. Three modes:

- **Coarse**: "Complete in one step, do not decompose"
- **Normal**: Default behavior
- **Fine**: "Decompose as finely as possible. Every sub-goal is a separate step."

I predicted average steps would be ~1.5, ~4, and ~10 respectively — a clean inverted-U curve of performance vs. granularity.

The actual result was a slap in the face:

| Mode | Prompt Instruction | Expected Steps | Actual Steps |
|:----:|-------------------|:--------------:|:------------:|
| coarse | "Do not decompose" | ~1.5 | **1.0** |
| normal | (none) | ~4 | **1.1** |
| fine | "Decompose as finely as possible" | ~10 | **1.2** |

The planner didn't budge. Coarse, normal, and fine all produced the same ~1 step. **The independent variable wasn't manipulated.** This wasn't a failed hypothesis — it was a failed experiment design. I'd nearly run 90 benchmark executions to test a non-existent effect.

This was the most important negative result of the entire project: **modern LLM-powered planners cannot be reliably controlled through prompt engineering alone.** From the model's perspective, solving a complex query in one call is *optimal* — more efficient in token cost, well-aligned with training distribution. Telling it to "decompose finely" is like telling a senior engineer to over-engineer a simple task. They'll push back.

The planner had become a Query Rewriter, not a Task Decomposer.

---

## 004: If The LLM Won't Decompose, Don't Let It Decide

At this point I had a clear diagnosis and two options:

1. Keep trying to make the LLM planner decompose (more prompting, few-shot examples, output format constraints)
2. **Replace the decomposition decision with rules.**

Option 1 had already failed twice. Option 2 was the uncomfortable choice — it meant admitting that the "pure LLM planner" everyone in Agent architecture was selling wasn't going to work for my use case.

The design was simple:

```
User Query
    ↓
Task Classifier (rule-based: ~20 keyword patterns)
    ↓
┌─ comparison ──→ Research A → Research B → Compare (3-5 steps)
├─ analysis   ──→ Collect Data → Apply Framework → Conclusions (3 steps)
├─ research   ──→ Gather Sources → Extract Facts → Synthesize (3-4 steps)
└─ (fallback) ──→ LLM Planner (for simple/ambiguous queries)
```

Three templates, about 110 lines of code total. No ML, no fine-tuning, no agentic loop.

Before building it, I ran a **minimum viable experiment**: 5 carefully chosen questions, each run twice — once with the current planner, once with a hand-crafted multi-step plan. Same model, same executor, same everything except the plan.

| Condition | Coverage | Steps | Duration |
|-----------|:--------:|:-----:|:--------:|
| Current Planner | 76% | 2.0 | 58.3s |
| **Manual Multi-Step** | **92%** | **4.6** | **58.9s** |

The duration delta was 0.6 seconds. **Steps more than doubled with virtually no latency increase.** The DAG executor was finally doing what it was designed to do — running independent steps in parallel.

The full 30-question benchmark confirmed it:

| Layer | Coverage | PASS/PART/FAIL | Avg Steps |
|:-----:|:--------:|:--------------:|:---------:|
| L1 (capability, 20 tasks) | **93.1%** | **18/2/0** | 3.4 |
| L2 (system, 10 tasks) | 50% | 5/0/5 | 2.7 |
| **All 30** | **78.7%** | **23/2/5** | 3.2 |

From 69% to 93% on L1 tasks. Zero benchmark failures. The 5 L2 failures were all deliberate edge cases — ambiguous queries ("analyze apple"), missing context, graceful degradation tests.

This was the first time in the project that I had **Evidence of Impact**. Not a hypothesis, not a theory, not something someone on Twitter said — my own data showing a measurable improvement.

---

## 005: Closing The Loop

The original question was still open: **with proper multi-step planning, does DAG finally outperform serial?**

I ran the comparison again with the structured planner. Both conditions used the same model and the same templates — only the execution mode differed.

| Metric | DAG | Serial | Δ |
|--------|:---:|:------:|:-:|
| Coverage | 94.0% | 95.6% | +1.6% |
| Steps | 3.5 | 3.5 | 0 |
| Duration | 55.4s | 57.6s | +2.2s |

Still equivalent. Even with 3.5× more steps, DAG's parallelism didn't provide a significant advantage on this task distribution.

Why? Because the benchmark's tasks are mostly serial chains: search → extract → synthesize. True parallel branches only appear in comparison queries (Research A || Research B → Compare), and even there, the KB search dominates latency, not the LLM call.

This was the final piece of the puzzle:

> **Planning quality, not execution architecture, is the dominant factor in Agent performance — at least for this class of tasks.**

---

## The Complete Story

Here's what five experiments added up to:

```
001: DAG ≈ Serial when avg_steps = 1.2
  ↓
003: Planner granularity cannot be controlled through prompting
  ↓
004: Structured planning: 69% → 93% coverage
  ↓
005: DAG ≈ Serial (revisited, confirmed)
  ↓
∴ Planning quality >> Execution architecture
```

Each experiment eliminated one hypothesis. The first eliminated "DAG will help." The second eliminated "we can tune granularity." The third eliminated "the planner will listen." The fourth confirmed "structured decomposition works." The fifth confirmed "the execution mode doesn't matter."

---

## What I'd Do Differently

**Experiment 002 should never have been designed that way.** I spent time planning a 90-run granularity study without first verifying that the granularity control actually changed the planner's output. A simple 3-query manipulation check would have saved a full day.

This is now a hard rule for any control experiment I design: **verify the independent variable is actually being manipulated before collecting data at scale.**

**I should have run the 5-question manual validation before experiment 001.** If I'd spent 2 hours hand-crafting multi-step plans and comparing them to the LLM planner, I would have had evidence of the planning bottleneck before spending weeks on DAG optimization.

---

## What This Means For Agent Architecture

There's an uncomfortable implication here for the "agents are the future" narrative.

If your Agent's planner is an LLM, and that LLM can solve most queries in one shot, then your DAG executor, parallel scheduling, reflection loop, and tool orchestration are **decoration**, not value. The LLM is doing the work; the Agent architecture is along for the ride.

This starts to explain why many "Agent frameworks" show impressive demos but struggle with rigorous benchmark improvements. The flashy architecture (agents delegating to sub-agents delegating to sub-agents) is often an emergent behavior of the LLM, not a meaningful architectural benefit.

This also explains the industry shift I've been observing in 2025-2026: from "pure LLM Planner" (ReAct, Plan-and-Execute) toward "Workflow First" (template-based orchestration with LLM in the leaves). Companies building production Agent systems are increasingly adopting hybrid approaches — rules for what's predictable, LLMs for what's not.

The question isn't "can we make the LLM plan better?" The question is "**which decisions should the LLM make, and which should be engineered?**"

---

*Built with DocMind — an experiment-driven Agent research platform. Full benchmark data and experimental reports available at [github.com/sijie-Z/DocMind-RAG](https://github.com/sijie-Z/DocMind-RAG).*

*Experiments: [001](docs/research/001_dag_vs_serial.md), [002](docs/research/002_planner_granularity.md), [003](docs/research/003_planner_compliance.md), [004](docs/research/004_structured_planning.md)*
