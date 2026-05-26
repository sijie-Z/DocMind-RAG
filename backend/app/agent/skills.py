"""Skill system — procedural memory for learned tool-use patterns.

Skills are reusable sequences of tool calls that the agent has learned
from past interactions. When a new query matches a known skill pattern,
the agent can skip the planning step and execute the skill directly.

Inspired by hermes-agent's Curator and skill management system.

Storage: Redis hash with TTL for auto-expiry of stale skills.
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A learned tool-use pattern."""
    id: str
    name: str
    description: str
    trigger_patterns: list[str]  # Keywords/patterns that activate this skill
    tool_sequence: list[dict[str, Any]]  # Ordered list of tool calls
    success_count: int = 0
    failure_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    @property
    def is_stale(self) -> bool:
        """Skills unused for 7 days are considered stale."""
        return time.time() - self.last_used_at > 7 * 24 * 3600

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "trigger_patterns": self.trigger_patterns,
            "tool_sequence": self.tool_sequence,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        return cls(**data)


class SkillManager:
    """Manages agent skills — creation, matching, and lifecycle."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    async def load(self) -> None:
        """Load skills from Redis."""
        if self._loaded:
            return
        try:
            from app.core.redis import redis_client
            if redis_client is None:
                self._loaded = True
                return
            raw = await redis_client.get("agent:skills")
            if raw:
                data = json.loads(raw)
                for skill_data in data:
                    skill = Skill.from_dict(skill_data)
                    self._skills[skill.id] = skill
            self._loaded = True
        except Exception as e:
            logger.warning(f"Failed to load skills: {e}")
            self._loaded = True

    async def save(self) -> None:
        """Persist skills to Redis."""
        try:
            from app.core.redis import redis_client
            if redis_client is None:
                return
            data = [s.to_dict() for s in self._skills.values()]
            await redis_client.setex(
                "agent:skills",
                30 * 24 * 3600,  # 30 day TTL
                json.dumps(data, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(f"Failed to save skills: {e}")

    def match(self, query: str) -> Skill | None:
        """Find the best matching skill for a query."""
        if not self._skills:
            return None

        query_lower = query.lower()
        best_skill = None
        best_score = 0.0

        for skill in self._skills.values():
            if skill.is_stale:
                continue
            score = 0.0
            for pattern in skill.trigger_patterns:
                if pattern.lower() in query_lower:
                    score += 1.0
            # Weight by success rate
            score *= (0.5 + 0.5 * skill.success_rate)
            if score > best_score:
                best_score = score
                best_skill = skill

        return best_skill if best_score >= 1.0 else None

    async def create_skill(
        self,
        name: str,
        description: str,
        trigger_patterns: list[str],
        tool_sequence: list[dict[str, Any]],
    ) -> Skill:
        """Create a new skill from a successful tool sequence."""
        skill_id = hashlib.md5(f"{name}:{time.time()}".encode()).hexdigest()[:12]
        skill = Skill(
            id=skill_id,
            name=name,
            description=description,
            trigger_patterns=trigger_patterns,
            tool_sequence=tool_sequence,
            last_used_at=time.time(),
        )
        self._skills[skill_id] = skill
        await self.save()
        logger.info(f"Created skill: {name} ({skill_id})")
        return skill

    async def record_success(self, skill_id: str) -> None:
        """Record a successful use of a skill."""
        skill = self._skills.get(skill_id)
        if skill:
            skill.success_count += 1
            skill.last_used_at = time.time()
            await self.save()

    async def record_failure(self, skill_id: str) -> None:
        """Record a failed use of a skill."""
        skill = self._skills.get(skill_id)
        if skill:
            skill.failure_count += 1
            skill.last_used_at = time.time()
            await self.save()

    async def prune_stale(self) -> int:
        """Remove skills that haven't been used recently."""
        stale_ids = [sid for sid, s in self._skills.items() if s.is_stale]
        for sid in stale_ids:
            del self._skills[sid]
        if stale_ids:
            await self.save()
        return len(stale_ids)

    def list_skills(self) -> list[Skill]:
        """List all active skills."""
        return [s for s in self._skills.values() if not s.is_stale]

    def get_tool_hints_for_query(self, query: str) -> list[dict[str, Any]]:
        """Get tool sequence hints from a matching skill for the Planner.

        Returns the tool_sequence from the best matching skill,
        which can be used to suggest tool hints for plan steps.
        """
        skill = self.match(query)
        if skill and skill.tool_sequence:
            return skill.tool_sequence
        return []

    async def learn_from_plan_success(
        self,
        query: str,
        plan_steps: list[dict[str, Any]],
    ) -> Skill | None:
        """Learn a new skill from a successfully executed plan.

        Automatically extracts trigger patterns from the query
        and tool sequence from the plan steps.
        """
        # Only learn if we have multiple tool calls
        tool_steps = [s for s in plan_steps if s.get("tool_hint")]
        if len(tool_steps) < 2:
            return None

        # Extract keywords as trigger patterns
        import re
        keywords = re.findall(r'[一-鿿]{2,}', query)  # Chinese bigrams
        keywords += [w.lower() for w in query.split() if len(w) > 3 and w.isalpha()]

        return await self.create_skill(
            name=f"Auto: {query[:50]}",
            description=f"Learned from: {query[:80]}",
            trigger_patterns=keywords[:5],
            tool_sequence=[{
                "tool_name": s.get("tool_hint", ""),
                "description": s.get("description", ""),
            } for s in tool_steps],
        )


# Global singleton
skill_manager = SkillManager()
