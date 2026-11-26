"""评审门控的组织技能库服务（批次十）。

产品边界（v1）：

- 技能是**结构化**的：1–8 条触发短语 + 1–8 个工具名步骤，不存参数、自由
  指令、URL、路径或命令；``description`` 仅用于 UI，绝不进入 system prompt。
- 提议只写 **pending** 版本；运行时只认 **approved + enabled + 哈希校验通过 +
  工具仍可见** 的版本。pending / rejected / disabled / archived 一律不注入。
- 审核与启用是治理动作：审核要求作者 ≠ 审核者（v1 强制），启用/禁用/回滚/
  归档只允许组织管理员（由 API 层强制）。状态迁移只允许 pending → approved /
  rejected；并发审核用条件 UPDATE 检测，冲突返回 409。
- 不提供物理删除：只归档；组织删除保护由 ``organization_service`` 的
  ``_ATTACHED_DATA_MODELS`` 承接。

安全校验（payload v1）：

- ``trigger_phrases``：1–8 条、单行、≤40 字符、无控制字符；拒绝 URL / 路径 /
  命令 / 常见秘密模式 / 常见提示注入标记。
- ``tool_sequence``：1–8 步；工具必须已注册、非 hidden、非别名、非高风险
  （``execute_python`` / ``execute_sql`` / ``mcp_call`` 等）。
- 每行内容哈希 ``sha256(canonical_json(payload))``；读取时重算，哈希不符即
  fail-closed（启用拒绝、运行时跳过）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.agent import checkpoint
from app.agent.approval import tool_risk_level
from app.agent.registry import canonical_tool_name, tool_registry
from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.models.agent_task import AgentTask, TaskStatus
from app.models.org_skill import (
    ORG_SKILL_REVIEW_STATUSES,
    ORG_SKILL_SOURCES,
    ORG_SKILL_STATUSES,
    OrgSkill,
    OrgSkillVersion,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_TRIGGER_PHRASES = 8
MAX_TRIGGER_CHARS = 40
MAX_TOOL_STEPS = 8
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 300
MAX_REVIEW_NOTE_CHARS = 200
MAX_VERSIONS_PER_SKILL = 100
MAX_TOOL_NAME_CHARS = 64

PAYLOAD_KEYS = frozenset({"schema_version", "trigger_phrases", "tool_sequence"})
TOOL_STEP_KEYS = frozenset({"tool_name"})

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
SOURCE_RUN_PROPOSAL = "run_proposal"
SOURCE_MANUAL = "manual"

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_INJECTION_MARKERS = (
    "<|",
    "|>",
    "{{",
    "}}",
    "```",
    "ignore previous",
    "ignore all previous",
    "忽略以上",
    "忽略之前",
    "system prompt",
    "系统提示词",
)
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp|javascript|data|vbscript|file):|www\.")
# 文件系统路径：盘符、POSIX 常见根、~ 主目录。
_PATH_RE = re.compile(
    r"(?i)(?:^|\s)(?:[a-z]:[\\/]|/(?:etc|bin|usr|var|home|root|tmp|opt)/|~/|\\\\[a-z0-9_])"
)
_COMMAND_RE = re.compile(
    r"(?i)\b(?:curl|wget|powershell|cmd(?:\.exe)?|bash|sh|zsh|python[0-9.]*|node|nc|ssh)\b"
    r"\s+(?:-|/|--|[a-z]:[\\/])"
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def _has_forbidden_char(value: str, *, allow_newlines: bool) -> bool:
    """Unicode 级单行判定：Cc/Cf/Zl/Zp 一律拒绝（覆盖 U+2028/Zl、U+200B/Cf、
    U+0085/Cc、U+202E 双向控制等 ASCII 正则抓不到的形态）。"""
    for ch in value:
        if allow_newlines and ch in ("\n", "\t"):
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            return True
    return False


class SkillPayloadError(ValidationError):
    """结构化 payload 校验失败（机器码 ORG_SKILL_PAYLOAD_INVALID）。"""

    error_code = "ORG_SKILL_PAYLOAD_INVALID"


@dataclass(frozen=True)
class MatchedSkill:
    """运行时命中的已评审技能版本（只含注入所需字段）。"""

    skill_id: str
    version_no: int
    tool_sequence: tuple[dict[str, str], ...]


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_digest(payload: Any) -> str:
    """payload 的规范化 SHA-256（用于落库与读取时完整性校验）。"""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _reject_unsafe_text(value: str, *, field: str, allow_newlines: bool = False) -> str:
    """拒绝控制/格式字符、URL/路径/命令、秘密模式与常见提示注入标记。"""
    if _has_forbidden_char(value, allow_newlines=allow_newlines):
        raise SkillPayloadError(f"{field} 不得包含控制/格式字符", field=field)
    if _URL_RE.search(value):
        raise SkillPayloadError(f"{field} 不得包含 URL", field=field)
    if _PATH_RE.search(value):
        raise SkillPayloadError(f"{field} 不得包含文件路径", field=field)
    if _COMMAND_RE.search(value):
        raise SkillPayloadError(f"{field} 不得包含命令", field=field)
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        raise SkillPayloadError(f"{field} 疑似包含秘密凭据，拒绝保存", field=field)
    lowered = value.casefold()
    if any(marker.casefold() in lowered for marker in _INJECTION_MARKERS):
        raise SkillPayloadError(f"{field} 含可疑提示注入标记", field=field)
    return value


def _normalize_trigger(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise SkillPayloadError(f"第 {index + 1} 条触发短语必须是字符串", field="trigger_phrases")
    stripped = value.strip()
    if not stripped:
        raise SkillPayloadError("触发短语不能为空", field="trigger_phrases")
    if len(stripped) > MAX_TRIGGER_CHARS:
        raise SkillPayloadError(f"触发短语最长 {MAX_TRIGGER_CHARS} 个字符", field="trigger_phrases")
    _reject_unsafe_text(stripped, field="trigger_phrases")
    return stripped


def _normalize_tool_step(value: Any, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise SkillPayloadError(f"第 {index + 1} 个工具步骤必须是对象", field="tool_sequence")
    if set(value) != TOOL_STEP_KEYS:
        raise SkillPayloadError(
            "工具步骤只允许 tool_name（不存参数/描述/任何自由字段）",
            field="tool_sequence",
        )
    raw_name = value.get("tool_name")
    if not isinstance(raw_name, str):
        raise SkillPayloadError("tool_name 必须是字符串", field="tool_sequence")
    name = raw_name.strip()
    if not name or len(name) > MAX_TOOL_NAME_CHARS or not _TOOL_NAME_RE.match(name):
        raise SkillPayloadError("tool_name 格式非法", field="tool_sequence")
    # 别名一律拒绝：别名的默认参数（例如 mode=vector）本身就是未声明的“参数”，
    # 且历史上旧名可能被隐藏/合并——只接受当前注册的规范名。
    canonical = canonical_tool_name(name)
    if canonical != name:
        raise SkillPayloadError(
            f"不允许使用工具别名 {name}（请使用规范名 {canonical}）", field="tool_sequence"
        )
    entry = tool_registry.get(name)
    if entry is None:
        raise SkillPayloadError(f"工具 {name} 未注册", field="tool_sequence")
    if entry.hidden:
        raise SkillPayloadError(f"工具 {name} 不可用（已隐藏）", field="tool_sequence")
    if tool_risk_level(name) == "high":
        raise SkillPayloadError(f"高风险工具 {name} 不允许进入技能", field="tool_sequence")
    return {"tool_name": name}


def normalize_payload(raw: Any) -> dict[str, Any]:
    """校验并规范化结构化 payload；任何多余键/未知形状一律拒绝。"""
    if not isinstance(raw, dict):
        raise SkillPayloadError("payload 必须是对象", field="payload")
    if set(raw) != PAYLOAD_KEYS:
        extra = sorted(set(raw) - PAYLOAD_KEYS)
        missing = sorted(PAYLOAD_KEYS - set(raw))
        raise SkillPayloadError(
            f"payload 键集非法（多余: {extra or '无'}；缺失: {missing or '无'}）",
            field="payload",
        )
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        raise SkillPayloadError(f"schema_version 必须为 {SCHEMA_VERSION}", field="schema_version")

    triggers_raw = raw.get("trigger_phrases")
    if not isinstance(triggers_raw, list):
        raise SkillPayloadError("trigger_phrases 必须是数组", field="trigger_phrases")
    if not 1 <= len(triggers_raw) <= MAX_TRIGGER_PHRASES:
        raise SkillPayloadError(
            f"触发短语必须为 1–{MAX_TRIGGER_PHRASES} 条", field="trigger_phrases"
        )
    triggers = [_normalize_trigger(item, i) for i, item in enumerate(triggers_raw)]
    deduped: list[str] = []
    seen: set[str] = set()
    for phrase in triggers:
        key = phrase.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(phrase)
    if not deduped:
        raise SkillPayloadError("触发短语去重后为空", field="trigger_phrases")

    tools_raw = raw.get("tool_sequence")
    if not isinstance(tools_raw, list):
        raise SkillPayloadError("tool_sequence 必须是数组", field="tool_sequence")
    if not 1 <= len(tools_raw) <= MAX_TOOL_STEPS:
        raise SkillPayloadError(f"工具步骤必须为 1–{MAX_TOOL_STEPS} 个", field="tool_sequence")
    tools = [_normalize_tool_step(item, i) for i, item in enumerate(tools_raw)]

    return {
        "schema_version": SCHEMA_VERSION,
        "trigger_phrases": deduped,
        "tool_sequence": tools,
    }


def _check_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValidationError("技能名称必须是字符串", field="name")
    name = raw.strip()
    if not name or len(name) > MAX_NAME_CHARS:
        raise ValidationError(f"技能名称必须为 1–{MAX_NAME_CHARS} 个字符", field="name")
    if _has_forbidden_char(name, allow_newlines=False):
        raise ValidationError("技能名称不得包含控制字符", field="name")
    return name


def _check_description(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationError("技能说明必须是字符串", field="description")
    description = raw.strip()
    if not description:
        return None
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValidationError(f"技能说明最长 {MAX_DESCRIPTION_CHARS} 个字符", field="description")
    _reject_unsafe_text(description, field="description", allow_newlines=True)
    return description


def skill_to_dict(skill: OrgSkill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "organization_id": skill.organization_id,
        "name": skill.name,
        "description": skill.description,
        "status": skill.status,
        "latest_version": skill.latest_version,
        "enabled_version_no": skill.enabled_version_no,
        "created_by": skill.created_by,
        "updated_by": skill.updated_by,
        "archived_at": skill.archived_at.isoformat() if skill.archived_at else None,
        "archived_by": skill.archived_by,
        "lock_version": skill.lock_version,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def version_to_dict(version: OrgSkillVersion, *, include_payload: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": version.id,
        "skill_id": version.skill_id,
        "version_no": version.version_no,
        "payload_sha256": version.payload_sha256,
        "source": version.source,
        "provenance": version.provenance_json,
        "review_status": version.review_status,
        "reviewed_by": version.reviewed_by,
        "reviewed_at": version.reviewed_at.isoformat() if version.reviewed_at else None,
        "review_note": version.review_note,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if include_payload:
        data["payload"] = version.payload_json
    return data


async def _get_skill(session: AsyncSession, *, organization_id: int, skill_id: str) -> OrgSkill:
    stmt = select(OrgSkill).where(
        OrgSkill.id == skill_id, OrgSkill.organization_id == organization_id
    )
    skill = (await session.execute(stmt)).scalar_one_or_none()
    if skill is None:
        raise NotFoundError("技能不存在", resource="org_skill", resource_id=skill_id)
    return skill


async def _get_version(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    version_no: int,
) -> OrgSkillVersion:
    stmt = select(OrgSkillVersion).where(
        OrgSkillVersion.skill_id == skill_id,
        OrgSkillVersion.organization_id == organization_id,
        OrgSkillVersion.version_no == version_no,
    )
    try:
        version = (await session.execute(stmt)).scalar_one_or_none()
    except (TypeError, ValueError) as exc:
        # 损坏 payload_json 在任意读取路径都收敛为可解释冲突（review/enable/
        # rollback/diff 都经过这里），绝不升级成 500。
        logger.warning(
            "Org skill version payload deserialization failed (skill=%s v%s)",
            skill_id,
            version_no,
        )
        raise ConflictError(
            "技能版本数据损坏，请联系管理员", error_code="ORG_SKILL_VERSION_CORRUPT"
        ) from exc
    if version is None:
        raise NotFoundError(
            "技能版本不存在", resource="org_skill_version", resource_id=f"{skill_id}:{version_no}"
        )
    return version


def _verify_version_integrity(version: OrgSkillVersion) -> None:
    """哈希与归属 fail-closed：内容被篡改的版本拒绝启用/注入。"""
    try:
        digest = payload_digest(version.payload_json)
    except (TypeError, ValueError) as exc:
        raise ConflictError(
            "技能版本内容损坏（无法计算哈希）", error_code="ORG_SKILL_VERSION_CORRUPT"
        ) from exc
    if digest != version.payload_sha256:
        raise ConflictError(
            "技能版本内容校验失败（哈希不符）", error_code="ORG_SKILL_VERSION_HASH_MISMATCH"
        )


async def list_skills(
    session: AsyncSession,
    *,
    organization_id: int,
    status: str = STATUS_ACTIVE,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """列出本组织技能（默认只看 active）；带待审数与启用版本摘要。"""
    if status not in (*ORG_SKILL_STATUSES, "all"):
        raise ValidationError("status 必须是 active/archived/all", field="status")
    conditions = [OrgSkill.organization_id == organization_id]
    if status != "all":
        conditions.append(OrgSkill.status == status)
    if q:
        conditions.append(OrgSkill.name.like(f"%{q.strip()}%"))

    total = (
        await session.execute(select(func.count()).select_from(OrgSkill).where(*conditions))
    ).scalar() or 0
    stmt = (
        select(OrgSkill)
        .where(*conditions)
        # MySQL 不支持 NULLS LAST；用 COALESCE 同时兼容 MySQL/SQLite。
        .order_by(func.coalesce(OrgSkill.updated_at, OrgSkill.created_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    skill_ids = [row.id for row in rows]
    pending_counts: dict[str, int] = {}
    pending_versions: dict[str, list[dict[str, Any]]] = {}
    if skill_ids:
        count_rows = (
            await session.execute(
                select(OrgSkillVersion.skill_id, func.count())
                .where(
                    OrgSkillVersion.organization_id == organization_id,
                    OrgSkillVersion.skill_id.in_(skill_ids),
                    OrgSkillVersion.review_status == REVIEW_PENDING,
                )
                .group_by(OrgSkillVersion.skill_id)
            )
        ).all()
        pending_counts = {skill_id: count for skill_id, count in count_rows}
        # 待审队列一次取回（当前页技能，避免前端按技能详情 N+1）。
        try:
            pending_rows = (
                (
                    await session.execute(
                        select(OrgSkillVersion)
                        .where(
                            OrgSkillVersion.organization_id == organization_id,
                            OrgSkillVersion.skill_id.in_(skill_ids),
                            OrgSkillVersion.review_status == REVIEW_PENDING,
                        )
                        .order_by(OrgSkillVersion.version_no.desc())
                    )
                )
                .scalars()
                .all()
            )
        except (TypeError, ValueError) as exc:
            # 损坏 payload_json 只影响待审摘要；主列表仍可读。
            logger.warning("Org skill pending list payload deserialization failed: %s", exc)
            pending_rows = []
        for version in pending_rows:
            pending_versions.setdefault(version.skill_id, []).append(version_to_dict(version))
    data = []
    for row in rows:
        item = skill_to_dict(row)
        item["pending_count"] = pending_counts.get(row.id, 0)
        item["pending_versions"] = pending_versions.get(row.id, [])
        data.append(item)
    return {"items": data, "total": total, "page": page, "page_size": page_size}


async def get_skill_detail(
    session: AsyncSession, *, organization_id: int, skill_id: str
) -> dict[str, Any]:
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    try:
        versions = (
            (
                await session.execute(
                    select(OrgSkillVersion)
                    .where(
                        OrgSkillVersion.skill_id == skill.id,
                        OrgSkillVersion.organization_id == organization_id,
                    )
                    .order_by(OrgSkillVersion.version_no.desc())
                )
            )
            .scalars()
            .all()
        )
    except (TypeError, ValueError) as exc:
        # 半写/截断的 payload_json 在 SELECT 反序列化时失败：如实返回冲突，
        # 不把整页详情炸成 500。
        logger.warning("Org skill detail payload deserialization failed (skill=%s)", skill.id)
        raise ConflictError(
            "技能版本数据损坏，请联系管理员", error_code="ORG_SKILL_VERSION_CORRUPT"
        ) from exc
    item = skill_to_dict(skill)
    item["versions"] = [version_to_dict(row) for row in versions]
    return item


async def get_version_detail(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    version_no: int,
) -> dict[str, Any]:
    await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    try:
        version = await _get_version(
            session,
            organization_id=organization_id,
            skill_id=skill_id,
            version_no=version_no,
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Org skill version payload deserialization failed (skill=%s v%s)",
            skill_id,
            version_no,
        )
        raise ConflictError(
            "技能版本数据损坏，请联系管理员", error_code="ORG_SKILL_VERSION_CORRUPT"
        ) from exc
    return version_to_dict(version)


def diff_versions(base: OrgSkillVersion, target: OrgSkillVersion) -> dict[str, Any]:
    """结构化 diff：触发短语与工具序列的增删（不返回自由文本）。"""
    base_payload = base.payload_json if isinstance(base.payload_json, dict) else {}
    target_payload = target.payload_json if isinstance(target.payload_json, dict) else {}
    base_triggers = list(base_payload.get("trigger_phrases") or [])
    target_triggers = list(target_payload.get("trigger_phrases") or [])
    base_tools = [
        item.get("tool_name")
        for item in (base_payload.get("tool_sequence") or [])
        if isinstance(item, dict) and item.get("tool_name")
    ]
    target_tools = [
        item.get("tool_name")
        for item in (target_payload.get("tool_sequence") or [])
        if isinstance(item, dict) and item.get("tool_name")
    ]
    base_set, target_set = set(base_triggers), set(target_triggers)
    base_tool_set, target_tool_set = set(base_tools), set(target_tools)
    return {
        "skill_id": base.skill_id,
        "base_version_no": base.version_no,
        "target_version_no": target.version_no,
        "base_sha256": base.payload_sha256,
        "target_sha256": target.payload_sha256,
        "trigger_phrases": {
            "base": base_triggers,
            "target": target_triggers,
            "added": [item for item in target_triggers if item not in base_set],
            "removed": [item for item in base_triggers if item not in target_set],
        },
        "tool_sequence": {
            "base": base_tools,
            "target": target_tools,
            "added": [item for item in target_tools if item not in base_tool_set],
            "removed": [item for item in base_tools if item not in target_tool_set],
        },
    }


async def diff_skill_versions(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    base_version_no: int,
    target_version_no: int,
) -> dict[str, Any]:
    await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    base = await _get_version(
        session,
        organization_id=organization_id,
        skill_id=skill_id,
        version_no=base_version_no,
    )
    target = await _get_version(
        session,
        organization_id=organization_id,
        skill_id=skill_id,
        version_no=target_version_no,
    )
    return diff_versions(base, target)


_UNSET = object()


def _derive_payload_from_task(task: AgentTask, snapshot: Any = _UNSET) -> dict[str, Any]:
    """从完成任务的执行快照派生结构化 payload（失败即拒绝，不猜测）。

    生产 ``agent_tasks.context_snapshot`` 存的是 checkpoint **v1 信封**
    （``checkpoint.build_envelope``）：步骤在 ``envelope["ctx"]["steps"]``，
    不是顶层 ``steps``。这里先走 ``checkpoint.parse_snapshot``；同时兼容
    历史上的顶层 ``steps`` 形状（旧测试/带外写入），两种都拿不到才拒绝。
    """
    raw = task.context_snapshot if snapshot is _UNSET else snapshot
    if not isinstance(raw, dict):
        # 显式传入 None（列为 NULL / 行不存在）必须干净拒绝：绝不回退访问
        # deferred 属性（AsyncSession 同步访问会抛 MissingGreenlet → 端点 500）。
        raise SkillPayloadError("来源任务缺少可用的执行快照", field="source_task_id")
    steps: Any = None
    envelope = checkpoint.parse_snapshot(raw)
    if envelope is not None and isinstance(envelope.get("ctx"), dict):
        steps = envelope["ctx"].get("steps")
    if not isinstance(steps, list):
        steps = raw.get("steps")
    if not isinstance(steps, list):
        raise SkillPayloadError("来源任务快照缺少步骤列表", field="source_task_id")
    tool_names: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = step.get("tool_used")
        if not isinstance(name, str) or not name.strip():
            continue
        canonical = canonical_tool_name(name.strip())
        if canonical not in tool_names:
            tool_names.append(canonical)
    if not tool_names:
        raise SkillPayloadError(
            "来源任务没有可沉淀的工具步骤（只接受含工具调用的完成运行）",
            field="source_task_id",
        )
    # 运行可能超过 8 步：只保留前 8 个（顺序敏感，截断而不是拒绝）。
    tool_names = tool_names[:MAX_TOOL_STEPS]
    trigger = " ".join(str(task.query or "").split())
    if not trigger:
        raise SkillPayloadError("来源任务缺少可用的查询文本", field="source_task_id")
    trigger = trigger[:MAX_TRIGGER_CHARS]
    return normalize_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "trigger_phrases": [trigger],
            "tool_sequence": [{"tool_name": name} for name in tool_names],
        }
    )


async def _validate_source_task(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    source_task_id: str,
) -> AgentTask:
    # 快照列单独 defer：半写/截断的 JSON 不能让「读任务行」整体炸成 500（毒丸）。
    stmt = (
        select(AgentTask)
        .where(AgentTask.id == source_task_id)
        .options(defer(AgentTask.context_snapshot))
    )
    task = (await session.execute(stmt)).scalar_one_or_none()
    # 跨组织/跨用户/非 COMPLETED 一律 404：绝不泄露任务是否存在。
    if (
        task is None
        or task.organization_id != organization_id
        or task.user_id != user_id
        or task.status != TaskStatus.COMPLETED
    ):
        raise NotFoundError(
            "来源任务不存在或不可用于技能提议",
            resource="agent_task",
            resource_id=source_task_id,
        )
    return task


async def _read_task_snapshot(session: AsyncSession, task_id: str) -> Any:
    """单独读取快照列；反序列化失败按可解释 400 拒绝（不把毒丸行升级成 500）。"""
    try:
        return (
            await session.execute(select(AgentTask.context_snapshot).where(AgentTask.id == task_id))
        ).scalar_one_or_none()
    except (TypeError, ValueError) as exc:
        # json.JSONDecodeError 是 ValueError 子类（截断/半写 JSON 的实测形态）。
        logger.warning("来源任务快照反序列化失败（task=%s）", task_id)
        raise SkillPayloadError(
            "来源任务快照无法解析，请改用手工 payload 或重新完成任务",
            field="source_task_id",
        ) from exc


async def _find_skill_id_by_name(
    session: AsyncSession, *, organization_id: int, name: str
) -> str | None:
    """同名预检查（并发下仅作快速失败；唯一约束的 flush 才是权威判定）。"""
    return (
        await session.execute(
            select(OrgSkill.id).where(
                OrgSkill.organization_id == organization_id,
                OrgSkill.name == name,
            )
        )
    ).scalar_one_or_none()


async def create_proposal(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
    payload: Any | None = None,
    source_task_id: str | None = None,
    skill_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    commit: bool = True,
) -> tuple[OrgSkill, OrgSkillVersion]:
    """写一个 pending 版本（新技能或已有技能追加）。

    - ``skill_id`` 给出时向该技能追加版本；否则用 ``name`` 新建技能头；
    - ``source_task_id`` 必须是**本人 + 本组织 + COMPLETED** 的任务；payload
      缺省时从任务执行快照（checkpoint v1 信封）派生；
    - ``payload`` 与 ``source_task_id`` 只能二选一（来源语义必须唯一）；
    - 服务端重新校验 payload、计算 sha256，并维护 ``latest_version``。
    """
    if payload is not None and source_task_id is not None:
        raise SkillPayloadError("payload 与 source_task_id 只能二选一", field="payload")
    task: AgentTask | None = None
    if source_task_id is not None:
        task = await _validate_source_task(
            session,
            organization_id=organization_id,
            user_id=user_id,
            source_task_id=source_task_id,
        )
    if payload is None:
        if task is None:
            raise SkillPayloadError("必须提供结构化 payload 或来源任务", field="payload")
        snapshot = await _read_task_snapshot(session, task.id)
        normalized = _derive_payload_from_task(task, snapshot)
    else:
        normalized = normalize_payload(payload)

    if skill_id is not None:
        skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
        if skill.status != STATUS_ACTIVE:
            raise ConflictError("技能已归档，不能追加新版本", error_code="ORG_SKILL_ARCHIVED")
        if name is not None and _check_name(name) != skill.name:
            raise ValidationError("已有技能不能通过提议改名", field="name")
        if description is not None:
            # 说明是技能头字段（不在版本里）；追加 pending 版本不得即时改写它。
            raise ValidationError("已有技能不能通过提议修改说明", field="description")
        skill.updated_by = user_id
    else:
        if not name:
            raise ValidationError("新建技能必须提供名称", field="name")
        checked_name = _check_name(name)
        checked_description = _check_description(description)
        existing = await _find_skill_id_by_name(
            session, organization_id=organization_id, name=checked_name
        )
        if existing is not None:
            raise ConflictError(
                "同名技能已存在；请指定 skill_id 追加版本",
                error_code="ORG_SKILL_NAME_CONFLICT",
            )
        skill = OrgSkill(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            name=checked_name,
            description=checked_description,
            status=STATUS_ACTIVE,
            latest_version=0,
            enabled_version_no=None,
            created_by=user_id,
            updated_by=user_id,
            lock_version=1,
        )
        session.add(skill)
        # flush 必须在 try 内：并发同名时预检查都会 miss，唯一约束在这里才触发；
        # 未包裹的 IntegrityError 会经端点兜底变成 500 而不是 409。
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError(
                "同名技能已存在；请指定 skill_id 追加版本",
                error_code="ORG_SKILL_NAME_CONFLICT",
            ) from exc

    if skill.latest_version >= MAX_VERSIONS_PER_SKILL:
        raise ConflictError(
            f"技能版本数已达上限 {MAX_VERSIONS_PER_SKILL}",
            error_code="ORG_SKILL_VERSION_LIMIT",
        )
    version_no = skill.latest_version + 1
    source = SOURCE_RUN_PROPOSAL if task is not None else SOURCE_MANUAL
    provenance: dict[str, Any] = {"source": source}
    if task is not None:
        provenance["source_task_id"] = task.id
    version = OrgSkillVersion(
        id=str(uuid.uuid4()),
        skill_id=skill.id,
        organization_id=organization_id,
        version_no=version_no,
        payload_json=normalized,
        payload_sha256=payload_digest(normalized),
        source=source,
        provenance_json=provenance,
        review_status=REVIEW_PENDING,
        created_by=user_id,
    )
    session.add(version)
    skill.latest_version = version_no
    try:
        if commit:
            await session.commit()
        else:
            await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "技能版本写入冲突，请重试", error_code="ORG_SKILL_VERSION_CONFLICT"
        ) from exc
    if commit:
        await session.refresh(skill)
        await session.refresh(version)
    return skill, version


async def review_version(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    version_no: int,
    reviewer_id: int,
    decision: str,
    note: str | None = None,
    commit: bool = True,
) -> OrgSkillVersion:
    """审核 pending 版本（仅 pending→approved/rejected；作者不可自审）。"""
    if decision not in (REVIEW_APPROVED, REVIEW_REJECTED):
        raise ValidationError("decision 必须是 approved/rejected", field="decision")
    if note is not None:
        note = note.strip()
        if len(note) > MAX_REVIEW_NOTE_CHARS:
            raise ValidationError(
                f"审核备注最长 {MAX_REVIEW_NOTE_CHARS} 个字符", field="review_note"
            )
        if note:
            _reject_unsafe_text(note, field="review_note", allow_newlines=True)
        note = note or None
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    if skill.status != STATUS_ACTIVE:
        raise ConflictError("技能已归档，不能审核", error_code="ORG_SKILL_ARCHIVED")
    version = await _get_version(
        session,
        organization_id=organization_id,
        skill_id=skill_id,
        version_no=version_no,
    )
    if version.created_by == reviewer_id:
        raise AuthorizationError("作者不能审核自己的技能版本", error_code="ORG_SKILL_SELF_REVIEW")
    if version.review_status != REVIEW_PENDING:
        raise ConflictError("版本已审核，不能重复审核", error_code="ORG_SKILL_ALREADY_REVIEWED")
    # 条件 UPDATE 提供并发保护：两个审核请求只有一个能把 pending 翻走。
    result = await session.execute(
        update(OrgSkillVersion)
        .where(
            OrgSkillVersion.id == version.id,
            OrgSkillVersion.organization_id == organization_id,
            OrgSkillVersion.review_status == REVIEW_PENDING,
        )
        .values(
            review_status=decision,
            reviewed_by=reviewer_id,
            reviewed_at=_now(),
            review_note=note,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise ConflictError(
            "版本已被其他审核者处理，请刷新后重试",
            error_code="ORG_SKILL_REVIEW_CONFLICT",
        )
    if commit:
        await session.commit()
        await session.refresh(version)
    else:
        await session.flush()
        await session.refresh(version)
    return version


def _load_payload_or_conflict(version: OrgSkillVersion) -> dict[str, Any]:
    _verify_version_integrity(version)
    try:
        normalized = normalize_payload(version.payload_json)
    except ValidationError as exc:
        raise ConflictError(
            "技能版本内容不再满足当前校验规则", error_code="ORG_SKILL_PAYLOAD_INVALID"
        ) from exc
    if payload_digest(normalized) != version.payload_sha256:
        raise ConflictError(
            "技能版本内容校验失败（规范化后哈希不符）",
            error_code="ORG_SKILL_VERSION_HASH_MISMATCH",
        )
    return normalized


async def enable_version(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    version_no: int,
    user_id: int,
    commit: bool = True,
) -> OrgSkill:
    """把 approved 版本设为唯一运行时指针（同版本重复启用幂等）。"""
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    if skill.status != STATUS_ACTIVE:
        raise ConflictError("技能已归档，不能启用", error_code="ORG_SKILL_ARCHIVED")
    version = await _get_version(
        session,
        organization_id=organization_id,
        skill_id=skill_id,
        version_no=version_no,
    )
    if version.review_status != REVIEW_APPROVED:
        raise ConflictError("只能启用已批准版本", error_code="ORG_SKILL_VERSION_NOT_APPROVED")
    # 哈希与规范化校验：指针指向的版本必须是完整、未被篡改的结构化 payload。
    _load_payload_or_conflict(version)
    if skill.enabled_version_no == version_no:
        return skill
    skill.enabled_version_no = version_no
    skill.updated_by = user_id
    skill.lock_version += 1
    if commit:
        await session.commit()
        await session.refresh(skill)
    else:
        await session.flush()
    return skill


async def disable_skill(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    user_id: int,
    commit: bool = True,
) -> OrgSkill:
    """关闭运行时注入（幂等；技能仍保留，可再次启用）。"""
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    if skill.enabled_version_no is None:
        return skill
    skill.enabled_version_no = None
    skill.updated_by = user_id
    skill.lock_version += 1
    if commit:
        await session.commit()
        await session.refresh(skill)
    else:
        await session.flush()
    return skill


async def rollback_skill(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    user_id: int,
    target_version_no: int | None = None,
    commit: bool = True,
) -> OrgSkill:
    """回滚到更早的 approved 版本（缺省 = 当前启用版本之前最近的 approved）。"""
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    if skill.status != STATUS_ACTIVE:
        raise ConflictError("技能已归档，不能回滚", error_code="ORG_SKILL_ARCHIVED")
    current = skill.enabled_version_no
    if current is None:
        raise ConflictError("技能未启用，无法回滚", error_code="ORG_SKILL_NOT_ENABLED")
    if target_version_no is None:
        stmt = (
            select(OrgSkillVersion)
            .where(
                OrgSkillVersion.skill_id == skill.id,
                OrgSkillVersion.organization_id == organization_id,
                OrgSkillVersion.review_status == REVIEW_APPROVED,
                OrgSkillVersion.version_no < current,
            )
            .order_by(OrgSkillVersion.version_no.desc())
            .limit(1)
        )
        target = (await session.execute(stmt)).scalar_one_or_none()
        if target is None:
            raise ConflictError(
                "没有可回滚的历史批准版本", error_code="ORG_SKILL_NO_ROLLBACK_TARGET"
            )
    else:
        if target_version_no == current:
            raise ValidationError("回滚目标不能是当前启用版本", field="version_no")
        if target_version_no > current:
            raise ValidationError("回滚目标必须早于当前启用版本", field="version_no")
        target = await _get_version(
            session,
            organization_id=organization_id,
            skill_id=skill_id,
            version_no=target_version_no,
        )
        if target.review_status != REVIEW_APPROVED:
            raise ConflictError("只能回滚到已批准版本", error_code="ORG_SKILL_VERSION_NOT_APPROVED")
    _load_payload_or_conflict(target)
    skill.enabled_version_no = target.version_no
    skill.updated_by = user_id
    skill.lock_version += 1
    if commit:
        await session.commit()
        await session.refresh(skill)
    else:
        await session.flush()
    return skill


async def archive_skill(
    session: AsyncSession,
    *,
    organization_id: int,
    skill_id: str,
    user_id: int,
    commit: bool = True,
) -> OrgSkill:
    """归档技能：只读终态，同时关闭运行时指针（不删除任何行）。"""
    skill = await _get_skill(session, organization_id=organization_id, skill_id=skill_id)
    if skill.status == STATUS_ARCHIVED:
        raise ConflictError("技能已归档", error_code="ORG_SKILL_ALREADY_ARCHIVED")
    skill.status = STATUS_ARCHIVED
    skill.enabled_version_no = None
    skill.archived_at = _now()
    skill.archived_by = user_id
    skill.updated_by = user_id
    skill.lock_version += 1
    if commit:
        await session.commit()
        await session.refresh(skill)
    else:
        await session.flush()
    return skill


async def match_skill(
    session: AsyncSession,
    *,
    organization_id: int,
    query: str,
    visible_tool_names: set[str] | None = None,
) -> MatchedSkill | None:
    """运行时匹配：只返回 approved + enabled + 哈希通过 + 工具仍可见的版本。

    ``visible_tool_names`` 由调用方传入当前 run 的可见工具集（loop 的
    ``_get_tools`` 同源）；None 表示不额外限制可见性（仍要求工具注册、非
    hidden、非高风险）。任何坏行只跳过并记 warning——匹配失败绝不多注入。
    """
    if not query or not query.strip():
        return None
    stmt = (
        select(OrgSkill, OrgSkillVersion)
        .join(
            OrgSkillVersion,
            (OrgSkillVersion.skill_id == OrgSkill.id)
            & (OrgSkillVersion.version_no == OrgSkill.enabled_version_no)
            & (OrgSkillVersion.organization_id == OrgSkill.organization_id),
        )
        .where(
            OrgSkill.organization_id == organization_id,
            OrgSkill.status == STATUS_ACTIVE,
            OrgSkill.enabled_version_no.is_not(None),
            OrgSkillVersion.review_status == REVIEW_APPROVED,
        )
    )
    try:
        rows = (await session.execute(stmt)).all()
    except (TypeError, ValueError) as exc:
        # 半写/截断的 payload_json 在 SELECT 反序列化时即失败；运行时必须
        # fail-closed（不注入），绝不能把整轮 run 拖成 500。
        logger.warning("Org skill match query failed and was skipped: %s", type(exc).__name__)
        return None
    query_folded = query.casefold()
    best: tuple[tuple[int, int, str], MatchedSkill] | None = None
    for skill, version in rows:
        try:
            payload = _load_payload_or_conflict(version)
            trigger_hits = sum(
                1
                for phrase in payload.get("trigger_phrases", [])
                if isinstance(phrase, str) and phrase.casefold() in query_folded
            )
            if trigger_hits <= 0:
                continue
            tool_sequence = payload.get("tool_sequence") or []
            names = [item["tool_name"] for item in tool_sequence]
            if visible_tool_names is not None and any(
                name not in visible_tool_names for name in names
            ):
                logger.info(
                    "Org skill %s v%s skipped: tool not visible in current run",
                    skill.id,
                    version.version_no,
                )
                continue
            candidate = MatchedSkill(
                skill_id=skill.id,
                version_no=version.version_no,
                tool_sequence=tuple({"tool_name": item["tool_name"]} for item in tool_sequence),
            )
            score_key = (trigger_hits, version.version_no, skill.name)
            if best is None or score_key > best[0]:
                best = (score_key, candidate)
        except ConflictError as exc:
            logger.warning(
                "Org skill %s v%s failed integrity check and was skipped: %s",
                skill.id,
                version.version_no,
                exc.message,
            )
        except Exception as exc:  # noqa: BLE001 — 单行坏数据不得拖垮运行时
            logger.warning(
                "Org skill %s v%s matching failed and was skipped: %s",
                skill.id,
                version.version_no,
                type(exc).__name__,
            )
    return best[1] if best else None


async def list_runtime_skills(
    session: AsyncSession, *, organization_id: int
) -> list[dict[str, Any]]:
    """旧 ``GET /agent/skills`` 兼容面：只返回可注入的已批准+已启用版本。

    不读 Redis；pending/rejected/disabled/archived 不出现。返回字段沿用旧
    ``/agent/skills`` 契约（id/name/description/trigger_patterns/tool_sequence/
    success_rate），``success_rate`` 固定 0.0（v1 不追踪使用成功率）。
    """
    stmt = (
        select(OrgSkill, OrgSkillVersion)
        .join(
            OrgSkillVersion,
            (OrgSkillVersion.skill_id == OrgSkill.id)
            & (OrgSkillVersion.version_no == OrgSkill.enabled_version_no)
            & (OrgSkillVersion.organization_id == OrgSkill.organization_id),
        )
        .where(
            OrgSkill.organization_id == organization_id,
            OrgSkill.status == STATUS_ACTIVE,
            OrgSkill.enabled_version_no.is_not(None),
            OrgSkillVersion.review_status == REVIEW_APPROVED,
        )
        .order_by(OrgSkill.name)
    )
    try:
        rows = (await session.execute(stmt)).all()
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Org skill runtime listing query failed and was skipped: %s", type(exc).__name__
        )
        return []
    data: list[dict[str, Any]] = []
    for skill, version in rows:
        try:
            payload = _load_payload_or_conflict(version)
        except ConflictError as exc:
            logger.warning(
                "Org skill %s v%s skipped from legacy listing: %s",
                skill.id,
                version.version_no,
                exc.message,
            )
            continue
        data.append(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "success_rate": 0.0,
                "trigger_patterns": list(payload.get("trigger_phrases") or []),
                "tool_sequence": [
                    {"tool_name": item["tool_name"]}
                    for item in (payload.get("tool_sequence") or [])
                ],
                "version_no": version.version_no,
            }
        )
    return data


__all__ = [
    "MatchedSkill",
    "SkillPayloadError",
    "ORG_SKILL_REVIEW_STATUSES",
    "ORG_SKILL_SOURCES",
    "ORG_SKILL_STATUSES",
    "archive_skill",
    "create_proposal",
    "diff_skill_versions",
    "diff_versions",
    "disable_skill",
    "enable_version",
    "get_skill_detail",
    "get_version_detail",
    "list_runtime_skills",
    "list_skills",
    "match_skill",
    "normalize_payload",
    "payload_digest",
    "review_version",
    "rollback_skill",
    "skill_to_dict",
    "version_to_dict",
]
