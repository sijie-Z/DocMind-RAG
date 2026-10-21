"""Pattern Analyzer — recommends Skills from mined patterns.

Sits on top of PatternMiner. Takes PatternStats and produces
SuggestedSkill objects that can be fed to the existing SkillManager.

Usage:
    python -m app.agent.mining.analyzer           # analyze and print
    python -m app.agent.mining.analyzer --save    # analyze and persist
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agent.mining.miner import PatternMiner
from app.agent.mining.patterns import PatternStats, SuggestedSkill

logger = logging.getLogger(__name__)


class PatternAnalyzer:
    """Analyzes mined patterns and produces Skill recommendations.

    The pipeline:
        1. PatternMiner.mine_all() → list[PatternStats]
        2. Filter: count >= 2+, success_rate >= 0.7, length >= 2
        3. For each candidate: compute confidence, extract triggers, name it
        4. Output: list[SuggestedSkill]
    """

    # Minimum thresholds for Skill recommendation
    # Lowered for demo/data collection phase; increase for production
    MIN_COUNT = 2
    MIN_SUCCESS_RATE = 0.7
    MIN_LENGTH = 2
    MAX_LENGTH = 8  # skills with > 8 steps are too complex

    def __init__(self, miner: PatternMiner | None = None):
        self.miner = miner or PatternMiner()

    async def analyze(self) -> tuple[list[PatternStats], list[SuggestedSkill]]:
        """Full pipeline: mine → filter → recommend.

        Returns:
            (all_patterns, suggested_skills)
        """
        patterns = await self.miner.mine_all()
        if not patterns:
            return [], []

        suggestions = self._recommend_skills(patterns)
        return patterns, suggestions

    def _recommend_skills(self, patterns: list[PatternStats]) -> list[SuggestedSkill]:
        """Filter patterns and generate Skill recommendations."""
        suggestions: list[SuggestedSkill] = []

        for p in patterns:
            if not self._meets_quality_bar(p):
                continue

            confidence = self._compute_confidence(p)
            suggestion = SuggestedSkill(
                name=p.suggested_name,
                description=self._generate_description(p),
                tool_sequence=p.tool_sequence,
                trigger_keywords=p.suggested_triggers,
                confidence=confidence,
                source_pattern_id=p.pattern_id,
                avg_coverage=p.avg_coverage,
                observation_count=p.count,
            )
            suggestions.append(suggestion)

        # Sort by confidence descending
        suggestions.sort(key=lambda s: -s.confidence)
        return suggestions

    def _meets_quality_bar(self, p: PatternStats) -> bool:
        """Check if a pattern qualifies for Skill recommendation."""
        return (
            p.count >= self.MIN_COUNT
            and p.success_rate >= self.MIN_SUCCESS_RATE
            and self.MIN_LENGTH <= p.length <= self.MAX_LENGTH
        )

    @staticmethod
    def _compute_confidence(p: PatternStats) -> float:
        """Compute confidence score (0–1) for a skill recommendation.

        Factors:
            - Frequency (more observations → higher confidence)
            - Success rate
            - Coverage (higher coverage → more reliable)
        """
        # Frequency factor: log scale, capped at 10 observations
        import math

        freq_factor = min(1.0, math.log2(p.count + 1) / math.log2(11))

        # Success rate factor
        success_factor = p.success_rate

        # Coverage factor
        coverage_factor = min(1.0, p.avg_coverage / 0.8)

        # Composite: weighted average
        confidence = 0.4 * freq_factor + 0.4 * success_factor + 0.2 * coverage_factor
        return round(min(1.0, confidence), 2)

    @staticmethod
    def _generate_description(p: PatternStats) -> str:
        """Generate a human-readable description of the pattern."""
        tools_desc = " → ".join(p.tool_sequence)
        return (
            f"Auto-discovered workflow: {tools_desc}. "
            f"Observed {p.count} times with {p.success_rate:.0%} success rate "
            f"and {p.avg_coverage:.0%} average keyword coverage."
        )


# ── CLI ─────────────────────────────────────────────────────────────


def _print_report(patterns: list[PatternStats], suggestions: list[SuggestedSkill]) -> None:
    """Print the analysis report to stdout."""
    print("=" * 65)
    print("  Pattern Mining Report")
    print("=" * 65)
    print(f"  Patterns found: {len(patterns)}")
    print(f"  Skill candidates: {len([p for p in patterns if p.is_candidate])}")
    print(f"  Suggested skills: {len(suggestions)}")
    print()

    if patterns:
        print("  ── Top Patterns ──")
        for i, p in enumerate(patterns[:10], 1):
            tools = " → ".join(p.tool_sequence)
            print(f"  {i}. {tools}")
            print(
                f"     count={p.count}  success={p.success_rate:.0%}  "
                f"coverage={p.avg_coverage:.0%}  time={p.avg_duration_ms / 1000:.1f}s"
            )
            if p.suggested_triggers:
                print(f"     triggers: {', '.join(p.suggested_triggers[:5])}")
            print()

    if suggestions:
        print("  ── Suggested Skills ──")
        for s in suggestions:
            print(s.format())
            print()

    print("=" * 65)


def _save_suggestions(
    suggestions: list[SuggestedSkill], path: str = "benchmark/suggested_skills.json"
) -> str:
    """Save suggested skills to a local JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [s.to_dict() for s in suggestions]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Pattern Mining & Skill Recommendation")
    parser.add_argument("--save", action="store_true", help="Save results to JSON files")
    parser.add_argument("--replay-dir", default="benchmark/replay", help="Replay directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    miner = PatternMiner(replay_dir=args.replay_dir)
    analyzer = PatternAnalyzer(miner)
    patterns, suggestions = await analyzer.analyze()

    _print_report(patterns, suggestions)

    if args.save:
        miner.save_patterns(patterns)
        path = _save_suggestions(suggestions)
        print(f"  Saved: {path}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
