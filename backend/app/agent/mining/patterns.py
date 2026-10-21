"""Pattern data models — what a discovered tool-use pattern looks like.

A Pattern represents a recurring sequence of tool calls observed
across multiple agent executions. High-frequency, high-success
patterns are candidates for automatic Skill generation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ToolSequence:
    """An ordered sequence of tool names observed in a single execution.

    Example: ["search_knowledge_base", "extract_insights", "cross_document_analysis"]
    """

    tools: list[str]
    """Ordered tool names (excluding None/skipped steps)."""

    task_id: str = ""
    """Source execution ID."""

    success: bool = True
    """Whether this execution succeeded (coverage >= 0.8)."""

    coverage: float = 0.0
    """Keyword coverage from the benchmark case."""

    duration_ms: float = 0.0
    """Total execution time in milliseconds."""

    query: str = ""
    """The original query (for trigger pattern extraction)."""

    def __len__(self) -> int:
        return len(self.tools)

    def to_tuple(self) -> tuple[str, ...]:
        """Convert to tuple for hashing/comparison."""
        return tuple(self.tools)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ToolSequence:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def format(self) -> str:
        """Human-readable: 'search → extract → compare'"""
        return " → ".join(self.tools) if self.tools else "(empty)"


@dataclass
class PatternStats:
    """Aggregated statistics for a recurring tool sequence pattern.

    This is the output of Pattern Mining — one PatternStats per unique
    tool sequence observed across all replay files.
    """

    pattern_id: str = ""
    """Unique identifier (hash of tool tuple)."""

    tool_sequence: list[str] = field(default_factory=list)
    """The canonical tool sequence."""

    count: int = 0
    """How many times this pattern was observed."""

    success_count: int = 0
    """How many times it succeeded (coverage >= 0.8)."""

    failure_count: int = 0
    """How many times it failed."""

    avg_coverage: float = 0.0
    """Average keyword coverage across all observations."""

    avg_duration_ms: float = 0.0
    """Average execution time in milliseconds."""

    # ── Skill recommendation metadata ──
    is_candidate: bool = False
    """Whether this pattern qualifies as a Skill candidate.
       Criteria: count >= 2 AND success_rate >= 0.7 AND length >= 2."""

    suggested_name: str = ""
    """Auto-generated name for the suggested skill."""

    suggested_triggers: list[str] = field(default_factory=list)
    """Keywords extracted from successful queries that used this pattern."""

    first_seen: str = ""
    """ISO timestamp of first observation."""

    last_seen: str = ""
    """ISO timestamp of most recent observation."""

    source_task_ids: list[str] = field(default_factory=list)
    """Task IDs that contributed to this pattern (max 10)."""

    # ── Computed properties ──

    @property
    def success_rate(self) -> float:
        """Fraction of observations that succeeded."""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    @property
    def is_frequent(self) -> bool:
        """Observed at least 2 times."""
        return self.count >= 2

    @property
    def is_reliable(self) -> bool:
        """Success rate >= 70%."""
        return self.success_rate >= 0.7

    @property
    def length(self) -> int:
        return len(self.tool_sequence)

    def format(self) -> str:
        """Human-readable summary."""
        tools = " → ".join(self.tool_sequence)
        return (
            f"{tools}\n"
            f"    count={self.count} success_rate={self.success_rate:.0%} "
            f"avg_coverage={self.avg_coverage:.0%} avg_time={self.avg_duration_ms / 1000:.1f}s"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PatternStats:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SuggestedSkill:
    """A skill recommendation derived from pattern mining.

    This is the bridge between Pattern Mining and the existing SkillManager.
    When a pattern meets the quality bar, it becomes a SuggestedSkill that
    can be either auto-registered or presented to the user for approval.
    """

    name: str = ""
    """Human-readable skill name (e.g. 'document_comparison')."""

    description: str = ""
    """What this skill does, derived from pattern analysis."""

    tool_sequence: list[str] = field(default_factory=list)
    """The tool sequence this skill executes."""

    trigger_keywords: list[str] = field(default_factory=list)
    """Keywords that should activate this skill."""

    confidence: float = 0.0
    """Combined confidence score (0–1) based on frequency and success rate."""

    source_pattern_id: str = ""
    """The PatternStats ID this was derived from."""

    avg_coverage: float = 0.0
    """Average coverage from source pattern."""

    observation_count: int = 0
    """How many times the source pattern was observed."""

    status: str = "suggested"
    """suggested → accepted → registered → active"""

    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SuggestedSkill:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def format(self) -> str:
        tools = " → ".join(self.tool_sequence)
        return (
            f"  {self.name} (confidence={self.confidence:.0%})\n"
            f"    tools: {tools}\n"
            f"    triggers: {', '.join(self.trigger_keywords[:5])}\n"
            f"    based on {self.observation_count} observations, "
            f"avg coverage {self.avg_coverage:.0%}"
        )
