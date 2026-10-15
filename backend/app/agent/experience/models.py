"""Experience data model — structured lessons the Agent learns from failures.

Each Experience captures a recurring failure pattern and prescribes an
actionable lesson. Experiences are:
    - Auto-extracted from benchmark failures (extractor.py)
    - Stored in Redis + local JSON (store.py)
    - Injected into Planner prompts (planner.py)
    - Confidence-weighted and verified over time
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Experience:
    """A single experience/lesson learned from a benchmark failure or tool error.

    The lifecycle of an Experience:

        extraction (from failure) → storage → retrieval (at planning time)
            → injection → verification (via re-benchmark) → confidence update

    Examples:
        Experience(
            scenario="cross_document_analysis",
            symptom="only_one_document_used",
            lesson="跨文档对比必须从每个目标文档逐一收集证据，再综合分析。",
            confidence=0.9,
            source_benchmark_case="L1-CROSS-01",
        )
        Experience(
            scenario="framework_analysis",
            symptom="framework_not_executed",
            lesson="识别到用户要求使用分析框架时，直接执行框架分析，不询问用户是否继续。",
            confidence=0.85,
        )
    """

    # ── Identification ──
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ── What went wrong ──
    scenario: str = ""
    """High-level category: cross_document, framework, web_search, ..."""

    symptom: str = ""
    """Specific failure symptom: only_one_document_used, keywords_missing, ..."""

    lesson: str = ""
    """Actionable instruction for the Planner. MUST be specific and executable."""

    # ── Confidence & verification ──
    confidence: float = 0.5
    """0.0–1.0. Auto-adjusted: +0.1 on verified success, -0.2 on re-failure."""

    verified_count: int = 0
    """How many times this lesson has been verified as correct."""

    fail_count: int = 0
    """How many times this lesson was applied but still failed."""

    # ── Source traceability ──
    source_trace_id: str = ""
    """Langfuse trace ID for the failure this came from."""

    source_benchmark_case: str = ""
    """Benchmark case ID (e.g. 'L1-FRAME-01') if sourced from benchmark."""

    source_error: str = ""
    """Raw error message if sourced from a runtime exception."""

    # ── Applicability (Negative Transfer protection) ──
    applicable_to: list[str] = field(default_factory=list)
    """Scenario tags this experience SHOULD be applied to.
       e.g. ['tool_recovery', 'search_failure']
       Empty = applicable to all scenarios (use with caution)."""

    avoid_for: list[str] = field(default_factory=list)
    """Scenario tags this experience MUST be avoided for.
       e.g. ['multi_step', 'framework_analysis']
       If the current query matches these, the experience is filtered OUT.
       This is the key mechanism preventing Negative Transfer."""

    # ── Tags & metadata ──
    tags: list[str] = field(default_factory=list)
    """Arbitrary tags for filtering: ['auto_extracted', 'llm_extracted', 'manual']."""

    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    # ── Serialization ──

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Experience:
        valid_fields = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    # ── Lifecycle ──

    def record_verification(self, success: bool) -> None:
        """Update confidence based on whether the lesson helped."""
        if success:
            self.verified_count += 1
            self.confidence = min(1.0, self.confidence + 0.1)
        else:
            self.fail_count += 1
            self.confidence = max(0.1, self.confidence - 0.2)
        self.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        logger.info(
            "Experience %s verified=%s confidence=%.2f (vc=%d fc=%d)",
            self.id[:8], success, self.confidence,
            self.verified_count, self.fail_count,
        )

    def is_reliable(self, threshold: float = 0.6) -> bool:
        """Whether this experience is reliable enough to inject into Planner."""
        return self.confidence >= threshold

    def short_summary(self) -> str:
        """One-line summary for logging."""
        return f"[{self.scenario}] {self.lesson[:60]}... (conf={self.confidence:.0%})"

    def __repr__(self) -> str:
        return f"Experience(id={self.id[:8]}, scenario={self.scenario}, conf={self.confidence:.0%})"
