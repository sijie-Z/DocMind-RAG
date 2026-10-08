"""Pattern Mining — discovers recurring tool-use patterns from execution history.

接线状态（#242 核实）：**dev-only 离线 CLI** —— 读 benchmark/replay/ 的落盘回放；
运行时无导入者。

Pipeline:
    Execution Replay (benchmark/replay/*.json)
        ↓
    PatternMiner (miner.py) — extract tool sequences, compute frequency
        ↓
    PatternAnalyzer (analyzer.py) — filter, score, recommend Skills
        ↓
    SuggestedSkill → SkillManager (skills.py) — register new skills

Usage:
    # Analyze all replays and print report
    python -m app.agent.mining.analyzer

    # Analyze and persist results
    python -m app.agent.mining.analyzer --save
"""

from app.agent.mining.analyzer import PatternAnalyzer
from app.agent.mining.miner import PatternMiner
from app.agent.mining.patterns import PatternStats, SuggestedSkill, ToolSequence

__all__ = [
    "ToolSequence",
    "PatternStats",
    "SuggestedSkill",
    "PatternMiner",
    "PatternAnalyzer",
]
