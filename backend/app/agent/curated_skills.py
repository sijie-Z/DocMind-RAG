"""Curated Skill system — SKILL.md file-based skills with YAML frontmatter + references.

Inspired by PaiAgent's SkillLoader/SkillRegistry (Java). Replaces the old procedural
skill_manager.py approach while keeping backward compatibility.

Architecture:
    SkillSet           — a named collection of skills (directory)
    CuratedSkill         — one SKILL.md parsed into (name, description, content, references[])
    CuratedSkillRegistry — loads from filesystem on startup, caches in Redis

Skill directory layout:
    skills/
    ├── ai-podcast/
    │   ├── SKILL.md          # YAML frontmatter + Markdown body
    │   └── reference/
    │       └── script-template.md
    ├── code-review/
    │   ├── SKILL.md
    │   └── reference/
    │       └── checklist.md
    └── ...

SKILL.md format:
    ---
    name: ai-podcast
    description: 生成专业播客脚本
    ---
    # actual markdown content here...
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$")
SKILLS_DIR_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "skills")


@dataclass
class CuratedSkill:
    """One curated skill loaded from a SKILL.md file."""

    name: str
    description: str = ""
    content: str = ""  # Markdown body after YAML frontmatter
    references: list[str] = field(default_factory=list)  # reference filenames (without .md)
    skill_path: str = ""  # absolute path to the skill directory

    def get_summary(self) -> str:
        """Full prompt-ready summary including description and body."""
        parts = [f"# 技能: {self.name}\n", f"{self.description}\n", "---\n", f"## 技能指南\n\n{self.content}\n"]
        if self.references:
            parts.append("## 可用参考文档\n")
            parts.append("调用 load_skill_reference 函数获取：\n")
            for ref in self.references:
                parts.append(f"- {ref}\n")
        return "\n".join(parts)

    def get_full_prompt(self, reference_contents: dict[str, str] | None = None) -> str:
        """Full execution prompt with all references inlined.

        Args:
            reference_contents: {ref_name: content} mapping. If empty/None,
                               references are listed but not embedded.
        """
        parts = [f"# 技能: {self.name}\n", f"{self.description}\n", "---\n", f"## 技能指南\n\n{self.content}\n"]
        if reference_contents:
            parts.append("## 参考文档\n")
            for ref_name, ref_body in reference_contents.items():
                parts.append(f"### {ref_name}\n\n{ref_body}\n")
        elif self.references:
            parts.append("## 可用参考文档\n")
            for ref in self.references:
                parts.append(f"- {ref}\n")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "content": self.content[:500],
            "references": self.references,
        }


class CuratedSkillRegistry:
    """Global registry of curated skills loaded from the filesystem.

    On startup, scans `skills/` directory for SKILL.md files.
    Reference contents are lazy-loaded on first access and cached.
    """

    def __init__(self, skills_dir: str = SKILLS_DIR_DEFAULT):
        self._skills_dir = Path(skills_dir)
        self._skills: dict[str, CuratedSkill] = {}
        self._reference_cache: dict[str, dict[str, str]] = {}  # {skill_name: {ref_name: content}}
        self._loaded = False

    # ── Loading ──────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Scan skills directory and load all SKILL.md files. Returns count loaded."""
        if self._loaded:
            return len(self._skills)

        if not self._skills_dir.exists():
            logger.warning("Skills directory not found: %s", self._skills_dir)
            self._loaded = True
            return 0

        count = 0
        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                skill = self._load_one(skill_dir, skill_file)
                self._skills[skill.name] = skill
                count += 1
                logger.info("Loaded curated skill: %s (%d references)", skill.name, len(skill.references))
            except Exception as e:
                logger.warning("Failed to load skill from %s: %s", skill_dir, e)

        self._loaded = True
        logger.info("CuratedSkillRegistry: loaded %d skills from %s", count, self._skills_dir)
        return count

    def reload(self) -> int:
        """Force rescan of skills directory."""
        self._skills.clear()
        self._reference_cache.clear()
        self._loaded = False
        return self.load_all()

    def _load_one(self, skill_dir: Path, skill_file: Path) -> CuratedSkill:
        raw = skill_file.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(raw)
        if not m:
            raise ValueError(f"Invalid SKILL.md format: missing YAML frontmatter in {skill_file}")

        frontmatter = m.group(1)
        content = m.group(2).strip()
        metadata = self._parse_frontmatter(frontmatter)

        name = metadata.get("name", "")
        if not name:
            raise ValueError(f"Skill name is required in frontmatter: {skill_file}")
        description = metadata.get("description", "")

        # Scan reference/ directory
        references: list[str] = []
        ref_dir = skill_dir / "reference"
        if ref_dir.exists() and ref_dir.is_dir():
            for ref_file in sorted(ref_dir.iterdir()):
                if ref_file.suffix == ".md":
                    references.append(ref_file.stem)

        return CuratedSkill(
            name=name,
            description=description,
            content=content,
            references=references,
            skill_path=str(skill_dir),
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str]:
        """Simple YAML frontmatter parser (key: value pairs only)."""
        result: dict[str, str] = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                # Strip quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                result[key] = value
        return result

    # ── Access ───────────────────────────────────────────────────────────

    @property
    def skill_names(self) -> list[str]:
        return sorted(self._skills.keys())

    def get(self, name: str) -> CuratedSkill | None:
        return self._skills.get(name)

    def list_summaries(self) -> list[dict[str, Any]]:
        """List all skills with name + description (for API)."""
        return [
            {"name": s.name, "description": s.description, "reference_count": len(s.references)}
            for s in sorted(self._skills.values(), key=lambda x: x.name)
        ]

    def get_detail(self, name: str) -> dict[str, Any] | None:
        """Get full skill detail (for API)."""
        skill = self._skills.get(name)
        if not skill:
            return None
        return {
            "name": skill.name,
            "description": skill.description,
            "content": skill.content,
            "references": skill.references,
        }

    def load_reference(self, skill_name: str, ref_name: str) -> str | None:
        """Load a reference document for a skill. Cached after first load."""
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        if ref_name not in skill.references:
            return None

        # Check cache
        if skill_name in self._reference_cache and ref_name in self._reference_cache[skill_name]:
            return self._reference_cache[skill_name][ref_name]

        # Load from disk
        ref_path = Path(skill.skill_path) / "reference" / f"{ref_name}.md"
        if not ref_path.exists():
            return None

        content = ref_path.read_text(encoding="utf-8")
        self._reference_cache.setdefault(skill_name, {})[ref_name] = content
        return content

    def load_all_references(self, skill_name: str) -> dict[str, str]:
        """Load all reference documents for a skill."""
        skill = self._skills.get(skill_name)
        if not skill:
            return {}

        result: dict[str, str] = {}
        for ref_name in skill.references:
            content = self.load_reference(skill_name, ref_name)
            if content:
                result[ref_name] = content
        return result

    def get_full_prompt(self, skill_name: str, load_refs: bool = True) -> str:
        """Get the full execution prompt for a skill, optionally with all references."""
        skill = self._skills.get(skill_name)
        if not skill:
            return ""
        refs = self.load_all_references(skill_name) if load_refs else None
        return skill.get_full_prompt(refs)


# ── Global singleton ────────────────────────────────────────────────────

curated_skill_registry = CuratedSkillRegistry()
