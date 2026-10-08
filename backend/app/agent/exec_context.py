"""ExecutionContext — per-request state that flows through the PER pipeline.

Created at the start of each Agent request, destroyed when the request ends.
All phases (Planner, Executor, Reflector) write into it; nothing persists.

This is NOT Conversation Memory.  Conversation memory crosses sessions.
ExecutionContext lives and dies with a single user query.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.log_safety import log_safe

logger = logging.getLogger(__name__)

_REPLAY_DIR = Path("benchmark/replay")

# task_id 进文件名前的白名单（CodeQL py/path-injection 修复）：
# uuid hex（12/32 位）天然满足；'/'、'\\'、':'、'..' 等一律无法通过。
_SAFE_FILE_STEM = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def _replay_file_for(task_id: str | None) -> Path | None:
    """由 task_id 构造本地 replay 文件落点；不满足约束 → None（拒绝拼接）。

    task_id 来自请求/快照（不可信）。三重防线保证落点被约束在 benchmark/replay/
    之内：① 白名单字符集（见 _SAFE_FILE_STEM）；② os.path.abspath 规范化
    （CodeQL 识别为 PathNormalization）；③ 规范化后用 startswith 校验前缀仍在
    基准目录（CodeQL 识别为 SafeAccessCheck）。不做改写式"消毒"——非法即拒绝，
    绝不把原始值拼进路径。
    """
    raw = "" if task_id is None else str(task_id)
    if not _SAFE_FILE_STEM.fullmatch(raw):
        return None
    base = os.path.abspath(_REPLAY_DIR)
    target = os.path.abspath(os.path.join(base, f"{raw}.json"))
    if not target.startswith(base + os.sep):
        return None
    return Path(target)


@dataclass
class StepRecord:
    """Record of a single plan step execution attempt."""
    step_id: str
    description: str
    tool_used: str | None
    status: str                      # pending / running / completed / failed / skipped
    result_summary: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    tokens_used: int = 0

    @property
    def is_success(self) -> bool:
        return self.status == "completed"


@dataclass
class Finding:
    """A piece of information discovered during execution."""
    source_step: str
    content: str
    source_tool: str | None = None
    confidence: str = "medium"       # high / medium / low


@dataclass
class Decision:
    """A key decision made during any phase."""
    phase: str                       # plan / execute / reflect
    action: str                      # choose_tool / retry_step / skip_step / skill_match / finalize
    reasoning: str
    triggered_by: str | None = None


@dataclass
class ExecutionContext:
    """Single-request execution state.  Created per query, destroyed after response."""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: str = ""

    # ── Planning ──
    goal: str = ""
    plan_summary: str = ""

    # ── Execution — step tracking ──
    current_step_id: str | None = None
    completed_steps: list[StepRecord] = field(default_factory=list)

    # ── Findings & decisions ──
    findings: list[Finding] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)

    # ── Intermediate artifacts (avoid temp vars scattered through code) ──
    artifacts: dict[str, Any] = field(default_factory=dict)

    # ── Progress ──
    progress: float = 0.0
    total_tokens: int = 0
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    duration_ms: float = 0.0

    # ── Convenience methods ──

    def add_tokens(self, n: int) -> None:
        """累加 LLM 用量（issue #202）。

        统一由 `app.agent.usage.record_llm_usage` 调用；调用点不要各自直改
        `total_tokens`——单一入口才能保证"新增调用点漏记"时一眼可见。
        """
        if n and n > 0:
            self.total_tokens += int(n)

    def add_finding(
        self,
        step_id: str,
        content: str,
        tool: str | None = None,
        confidence: str = "medium",
    ) -> None:
        self.findings.append(Finding(
            source_step=step_id,
            content=content[:500],
            source_tool=tool,
            confidence=confidence,
        ))

    def record_decision(self, phase: str, action: str, reasoning: str) -> None:
        self.decisions.append(Decision(
            phase=phase, action=action, reasoning=reasoning,
        ))

    def complete_step(
        self,
        step_id: str,
        description: str,
        tool: str | None,
        status: str,
        result: str | None = None,
        error: str | None = None,
        duration: float = 0.0,
        tokens: int = 0,
    ) -> None:
        record = StepRecord(
            step_id=step_id,
            description=description,
            tool_used=tool,
            status=status,
            result_summary=(result or "")[:300],
            error=error,
            duration_ms=duration,
            tokens_used=tokens,
        )
        self.completed_steps.append(record)
        if status == "failed":
            self.failures.append(f"[{step_id}] {(error or '')[:200]}")

    def mark_done(self) -> None:
        self.duration_ms = (datetime.now().timestamp() - self.start_time) * 1000
        self.progress = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Full serialisation — preserves ALL data for replay."""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "goal": self.goal,
            "plan_summary": self.plan_summary,
            "steps_completed": len(self.completed_steps),
            "steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "tool_used": s.tool_used,
                    "status": s.status,
                    "result_summary": s.result_summary,
                    "error": s.error,
                    "duration_ms": round(s.duration_ms, 1),
                    "tokens_used": s.tokens_used,
                }
                for s in self.completed_steps
            ],
            "findings": [
                {"source_step": f.source_step, "content": f.content[:300],
                 "source_tool": f.source_tool, "confidence": f.confidence}
                for f in self.findings
            ],
            "decisions": [
                {"phase": d.phase, "action": d.action,
                 "reasoning": d.reasoning[:200], "triggered_by": d.triggered_by}
                for d in self.decisions
            ],
            "failures": self.failures[:10],
            "duration_ms": round(self.duration_ms, 1),
            "total_tokens": self.total_tokens,
            "progress": self.progress,
            "start_time": self.start_time,
        }

    # ── Checkpoint（issue #201 最小版）────────────────────────────────────
    # to_dict() 是 replay / SSE execution_context 事件的既有契约（键集不许动）；
    # checkpoint 快照是它的**超集**：补上 to_dict 缺失的 current_step_id 与
    # artifacts（iterations_used 迭代预算、quality_gate / review 结论都在
    # artifacts 里——不序列化它们，resume 后预算口径断裂、迭代计数归零）。

    def to_checkpoint_dict(self) -> dict[str, Any]:
        """快照形状（v1）。与 to_dict() 的差异只增不改。"""
        data = self.to_dict()
        data.update({
            "v": 1,
            "current_step_id": self.current_step_id,
            "artifacts": self.artifacts,
        })
        return data

    @classmethod
    def from_checkpoint_dict(cls, data: dict[str, Any]) -> ExecutionContext:
        """从 to_checkpoint_dict() 的输出重建 ctx（resume 用）。

        逐字段容错（缺失取默认）；整体结构是否可用由更外层的
        checkpoint.parse_snapshot 统一判定（损坏 → 安全降级为"无快照"）。
        不经过 complete_step()：那会按 failed 状态二次追加 failures，与恢复的
        failures 列表重复。
        """
        ctx = cls(query=data.get("query") or "")
        ctx.task_id = data.get("task_id") or ctx.task_id
        ctx.goal = data.get("goal") or ""
        ctx.plan_summary = data.get("plan_summary") or ""
        ctx.current_step_id = data.get("current_step_id")
        ctx.artifacts = dict(data.get("artifacts") or {})
        ctx.total_tokens = int(data.get("total_tokens") or 0)
        ctx.progress = float(data.get("progress") or 0.0)
        ctx.duration_ms = float(data.get("duration_ms") or 0.0)
        if data.get("start_time"):
            ctx.start_time = float(data["start_time"])
        ctx.failures = [str(f) for f in (data.get("failures") or [])]
        for s in data.get("steps") or []:
            ctx.completed_steps.append(StepRecord(
                step_id=s.get("step_id", ""),
                description=s.get("description", ""),
                tool_used=s.get("tool_used"),
                status=s.get("status", ""),
                result_summary=s.get("result_summary"),
                error=s.get("error"),
                duration_ms=float(s.get("duration_ms") or 0.0),
                tokens_used=int(s.get("tokens_used") or 0),
            ))
        for f in data.get("findings") or []:
            ctx.findings.append(Finding(
                source_step=f.get("source_step", ""),
                content=f.get("content", ""),
                source_tool=f.get("source_tool"),
                confidence=f.get("confidence", "medium"),
            ))
        for d in data.get("decisions") or []:
            ctx.decisions.append(Decision(
                phase=d.get("phase", ""),
                action=d.get("action", ""),
                reasoning=d.get("reasoning", ""),
                triggered_by=d.get("triggered_by"),
            ))
        return ctx

    def to_summary_dict(self) -> dict[str, Any]:
        """Lightweight summary for logging / observability."""
        return {
            "task_id": self.task_id,
            "query": self.query[:100],
            "goal": self.goal[:100] if self.goal else "",
            "steps_completed": len(self.completed_steps),
            "findings": len(self.findings),
            "failures": len(self.failures),
            "decisions": len(self.decisions),
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens,
            "artifacts_keys": list(self.artifacts.keys()),
        }

    # ── Persistence for Replay ──

    async def save(self, ttl: int = 86400 * 7) -> bool:
        """Persist full execution context for replay.

        Saves to Redis first; falls back to local JSON file.

        Args:
            ttl: Time-to-live in seconds (default 7 days).

        Returns:
            True if saved successfully (either backend).
        """
        data = self.to_dict()
        # Try Redis
        try:
            from app.core.redis import redis_client
            if redis_client:
                key = f"agent:replay:{self.task_id}"
                await redis_client.setex(key, ttl, json.dumps(data, ensure_ascii=False))
                logger.debug("ExecutionContext saved to Redis: %s", log_safe(self.task_id))
                return True
        except Exception as e:
            logger.debug("Failed to save ExecutionContext to Redis: %s", log_safe(e))

        # Fallback: local file（task_id 先经 _replay_file_for 约束；非法即拒绝落盘）
        try:
            p = _replay_file_for(self.task_id)
            if p is None:
                logger.warning(
                    "跳过本地 replay 落盘：task_id 含不安全字符，已拒绝拼接路径（task_id=%s）",
                    log_safe(self.task_id),
                )
                return False
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("ExecutionContext saved locally: %s", log_safe(p))
            return True
        except Exception as e:
            logger.debug("Failed to save ExecutionContext locally: %s", log_safe(e))
        return False

    @classmethod
    async def load(cls, task_id: str) -> dict[str, Any] | None:
        """Load a saved execution context for replay.

        Returns:
            The full context dict, or None if not found.
        """
        try:
            from app.core.redis import redis_client
            if redis_client:
                raw = await redis_client.get(f"agent:replay:{task_id}")
                if raw:
                    return json.loads(raw)
        except Exception as e:
            logger.debug("Failed to load ExecutionContext from Redis: %s", log_safe(e))
        return None

    @classmethod
    async def load_local(cls, path: str) -> dict[str, Any] | None:
        """Load a saved execution context from a local JSON file."""
        try:
            from pathlib import Path
            p = Path(path)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Failed to load ExecutionContext from %s: %s", log_safe(path), log_safe(e))
        return None

    async def save_local(self, path: str | None = None) -> str:
        """Save execution context to a local JSON file for benchmark analysis.

        Args:
            path: Optional file path. Default: benchmark/replay/{task_id}.json
                （task_id 先经 _replay_file_for 约束；非法 → ValueError）

        Returns:
            The file path saved to.
        """
        if path is None:
            target = _replay_file_for(self.task_id)
            if target is None:
                raise ValueError("task_id 含不安全字符，拒绝构造本地 replay 路径")
            p = target
        else:
            p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("ExecutionContext saved locally: %s", log_safe(p))
        return str(p)
