"""Agent system — PER (Planning-Execution-Reflection) architecture.

Architecture:
    config.py       — Persistable agent configuration
    events.py       — Enhanced event system for streaming
    registry.py     — Self-registering tool system with JSON schemas
    tools.py        — Built-in tools (search, analyze, knowledge management)
    tools/          — Extended tools (web_search, code_exec, data_analysis, etc.)
    context.py      — Context window management with LLM-based compression
    planner.py      — Structured plan generation with streaming thinking
    executor.py     — Step-by-step plan execution with retry logic
    reflector.py    — Post-execution reflection and self-correction
    loop.py         — PER agent loop orchestrator
    memory_bridge.py — Integration layer between AgentMemorySystem and agent loop
    skills.py       — Procedural memory: learned tool-use patterns
    subagent.py     — Delegation: spawn child agents for complex subtasks
    service.py      — High-level AgentService interface

Execution flow:
    User query → AgentService.chat() → PERAgentLoop.run()
        → Phase 0: Memory recall (MemoryBridge)
        → Phase 1: Planning (Planner — streaming thinking)
        → Phase 2: Execution (Executor — step-by-step with retry)
        → Phase 3: Reflection (Reflector — pass/retry/replan)
        → Phase 4: Store memories & return final answer
"""
