"""Pattern Miner — extracts recurring tool-use sequences from replay data.

Scans all replay JSON files in benchmark/replay/, extracts tool sequences,
and computes frequency/success statistics.

Usage:
    from app.agent.mining.miner import PatternMiner
    miner = PatternMiner()
    stats = await miner.mine_all()
    for s in stats[:5]:
        print(s.format())
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path

from app.agent.mining.patterns import PatternStats, ToolSequence

logger = logging.getLogger(__name__)

REPLAY_DIR = Path("benchmark/replay")
CASES_DIR = Path("benchmark/cases")


class PatternMiner:
    """Extracts tool-use patterns from replay files.

    Core algorithm:
        1. Load all replay JSONs from benchmark/replay/
        2. For each replay, extract the ordered tool sequence
        3. Group identical sequences
        4. Compute frequency, success rate, coverage, timing
        5. Rank by (count × success_rate)
    """

    def __init__(self, replay_dir: Path | str = REPLAY_DIR):
        self.replay_dir = Path(replay_dir)

    # ── Core Mining ──

    def load_sequences(self) -> list[ToolSequence]:
        """Load all replay files and extract tool sequences.

        Returns a list of ToolSequence objects, one per replay file.
        """
        if not self.replay_dir.exists():
            logger.warning("Replay directory not found: %s", self.replay_dir)
            return []

        sequences: list[ToolSequence] = []
        for f in sorted(self.replay_dir.glob("*.json")):
            try:
                seq = self._extract_sequence(f)
                if seq and len(seq) >= 1:  # at least 1 tool
                    sequences.append(seq)
            except Exception as e:
                logger.debug("Failed to extract sequence from %s: %s", f.name, e)

        logger.info("Loaded %d tool sequences from %d replay files",
                     len(sequences), len(list(self.replay_dir.glob("*.json"))))
        return sequences

    def _extract_sequence(self, path: Path) -> ToolSequence | None:
        """Extract a ToolSequence from a single replay JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        steps = data.get("steps", [])

        # Extract tool names in order, skipping nulls
        tools = []
        for step in steps:
            tool = step.get("tool_used")
            if tool and tool != "None":
                tools.append(tool)

        if not tools:
            return None

        task_id = data.get("task_id", path.stem)

        # Try to match with a benchmark case for coverage/success data
        coverage, success = self._lookup_case_result(task_id)

        # Fallback: derive success from replay data itself
        # (no failures AND has completed steps)
        if not success and coverage == 0.0:
            failures = data.get("failures", [])
            completed = sum(1 for s in steps if s.get("status") == "completed")
            success = len(failures) == 0 and completed >= 1

        return ToolSequence(
            tools=tools,
            task_id=task_id,
            success=success,
            coverage=coverage,
            duration_ms=data.get("duration_ms", 0),
            query=data.get("query", "")[:100],
        )

    def _lookup_case_result(self, task_id: str) -> tuple[float, bool]:
        """Look up coverage and success from benchmark case files.

        Returns (coverage, success) where success = coverage >= 0.8.
        """
        # Search both failure and success case directories
        for grade_dir in ["success", "partial", "failure"]:
            cases_path = CASES_DIR / grade_dir
            if not cases_path.exists():
                continue
            for f in cases_path.glob("*.json"):
                try:
                    case = json.loads(f.read_text(encoding="utf-8"))
                    # Match by question_id or trace_id
                    if (case.get("trace_id") == task_id or
                        case.get("question_id", "") in task_id):
                        cov = case.get("keyword_coverage", 0.0)
                        return cov, cov >= 0.8
                except Exception:
                    continue
        return 0.0, False

    def compute_patterns(self, sequences: list[ToolSequence]) -> list[PatternStats]:
        """Group sequences by tool tuple and compute statistics.

        Returns PatternStats sorted by (count × success_rate) descending.
        """
        # Group by tool sequence
        groups: dict[tuple[str, ...], list[ToolSequence]] = defaultdict(list)
        for seq in sequences:
            key = seq.to_tuple()
            groups[key].append(seq)

        stats: list[PatternStats] = []
        for tool_tuple, group in groups.items():
            count = len(group)
            success_count = sum(1 for s in group if s.success)
            failure_count = count - success_count
            avg_coverage = sum(s.coverage for s in group) / count if count else 0
            avg_duration = sum(s.duration_ms for s in group) / count if count else 0

            # Generate pattern ID
            pattern_id = hashlib.md5(
                "|".join(tool_tuple).encode(), usedforsecurity=False
            ).hexdigest()[:12]

            # Extract trigger keywords from successful queries
            trigger_keywords = self._extract_triggers(
                [s.query for s in group if s.success]
            )

            # Auto-name the pattern
            suggested_name = self._auto_name(tool_tuple)

            stats.append(PatternStats(
                pattern_id=pattern_id,
                tool_sequence=list(tool_tuple),
                count=count,
                success_count=success_count,
                failure_count=failure_count,
                avg_coverage=round(avg_coverage, 3),
                avg_duration_ms=round(avg_duration, 1),
                is_candidate=(count >= 2 and success_count / count >= 0.7 and len(tool_tuple) >= 2),
                suggested_name=suggested_name,
                suggested_triggers=trigger_keywords,
                first_seen=group[0].query[:20] if group else "",
                last_seen=group[-1].query[:20] if group else "",
                source_task_ids=[s.task_id[:12] for s in group[:10]],
            ))

        # Rank by composite score: frequency × success_rate × coverage
        stats.sort(key=lambda s: s.count * s.success_rate * max(s.avg_coverage, 0.1), reverse=True)
        return stats

    @staticmethod
    def _extract_triggers(queries: list[str]) -> list[str]:
        """Extract common keywords from successful queries as trigger patterns."""
        keyword_freq: dict[str, int] = defaultdict(int)

        for query in queries:
            q = query.lower()
            # Chinese bigrams
            for i in range(len(q) - 1):
                bg = q[i:i+2]
                if all('一' <= c <= '鿿' for c in bg):
                    keyword_freq[bg] += 1
            # English words (length > 2)
            for word in q.split():
                if len(word) > 2 and word.isascii():
                    keyword_freq[word] += 1

        # Return keywords that appear in >= 50% of queries
        threshold = max(1, len(queries) // 2)
        triggers = [kw for kw, freq in keyword_freq.items() if freq >= threshold]
        return sorted(triggers, key=lambda kw: -keyword_freq[kw])[:10]

    @staticmethod
    def _auto_name(tool_sequence: tuple[str, ...]) -> str:
        """Generate a human-readable name from a tool sequence.

        Maps common tool sequences to descriptive names.
        """
        # Canonical name mappings
        name_map: dict[tuple[str, ...], str] = {
            ("search_knowledge_base", "extract_insights"): "knowledge_extraction",
            ("search_knowledge_base", "cross_document_analysis"): "document_comparison",
            ("search_knowledge_base", "analyze_document"): "document_analysis",
            ("search_knowledge_base", "summarize_document"): "document_summarization",
            ("search_knowledge_base", "list_documents"): "document_discovery",
            ("web_search", "search_knowledge_base"): "web_knowledge_merge",
            ("search_knowledge_base", "deep_analysis"): "deep_research",
        }

        # Check exact match
        if tool_sequence in name_map:
            return name_map[tool_sequence]

        # Check prefix match (first 2 tools)
        prefix = tool_sequence[:2]
        if prefix in name_map:
            return name_map[prefix] + "_extended"

        # Fallback: join shortened tool names
        short_names = []
        for t in tool_sequence:
            # Shorten: "search_knowledge_base" → "search"
            short = t.split("_")[0][:15]
            short_names.append(short)
        return "_".join(short_names) + "_workflow"

    # ── High-Level API ──

    async def mine_all(self) -> list[PatternStats]:
        """Full pipeline: load → extract → compute → rank.

        Returns PatternStats sorted by composite score.
        """
        sequences = self.load_sequences()
        if not sequences:
            logger.info("No sequences found — run a benchmark first to collect replay data")
            return []

        patterns = self.compute_patterns(sequences)
        logger.info("Mined %d unique patterns from %d sequences",
                     len(patterns), len(sequences))
        return patterns

    def save_patterns(self, patterns: list[PatternStats], path: str = "benchmark/patterns.json") -> str:
        """Save discovered patterns to a local JSON file."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        data = [p.to_dict() for p in patterns]
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Patterns saved: %d → %s", len(patterns), output)
        return str(output)

    def load_patterns(self, path: str = "benchmark/patterns.json") -> list[PatternStats]:
        """Load previously saved patterns."""
        p = Path(path)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [PatternStats.from_dict(d) for d in data]
